"""Parquet の T3（indicators）を全期間再計算して新世代を書き出す。

## なぜ必要か

T3 は **SQLite の `daily_prices` にある全行**を入力にする（`t3_indicators.py` L22-24。
日付の絞り込みが無い）。つまり **SQLite に何が入っているかが計算結果の全て**で、
切り詰められていればそのまま誤差になる。

    2026-08-29  SQLite が730日しか無い状態で --rebuild-from T3 を実行
                → 2024-09〜2025-08 の 772,526行が誤った値で上書きされた
                   sma_200 の 77.7%、dist_52w_high_pct の 37.6% が食い違い、
                   ブルードットの点灯は 4,983件 → 0件

さらに復元は `default_start_date`(2018-04-01) 単独で切っていたため、
`index_start_date`(2010-04-01) 側の 49銘柄（SPY 含む）は復元しても 2010〜2018 が入らず、
**ETF だけ 2018 起点で再計算される**という食い違いもあった。

**Parquet を直接読めばこの問題が構造的に消える。** ETF は 2010、個別は 2018 と、
各銘柄の全履歴がそのまま入力になる。T4 について同じ理由で作られた
`recompute_parquet_ranks.py` と同じ考え方・同じ手順に揃えてある。

## メモリ

`recompute_indicators()` を全銘柄で直接呼ぶと `pd.concat` が落ちる
（2026-09-01 に rotate が同じ規模で "Unable to allocate 1.77 GiB" を出した。
空きメモリ 28.7GB での失敗なので**連続領域の断片化**が原因）。
`parquet_recompute_chunked` 経由で銘柄をチャンクに割り、`ParquetWriter` へ
逐次書き出すことで、一度に確保するメモリを抑える。

## 副作用（実行前に理解すること）

**現在の価格データで再計算する。** 価格に対する手当て（分割調整・履歴の切り詰め等）が
入っていれば、その結果が指標へ反映される。逆に言えば、指標だけが古い前提のまま
残っている状態を解消できる。

## 使い方

    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_indicators.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_indicators.py --apply
"""
import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)
from pipeline.parquet_recompute_chunked import (  # noqa: E402
    DEFAULT_CHUNK_SIZE,
    recompute_indicators_chunked,
)
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402

PRICE_COLS = ["symbol_id", "date", "open", "high", "low", "close", "volume"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# 列の性質で検証基準を分ける。基準を間違えると「正しい実装を落とす」ことになる
# （2026-09-04 の初回検証で実際に誤検知した）。
#
#   EXACT      生の価格に対するローリングのみ。必要な遡りは最長252本で、既存も
#              730日窓で計算されているから足りている。**完全一致すべき**
#              ＝ここが切り詰め起因のバグを捕まえる真の回帰検査。
#   LEVEL      EMA を含む「水準」の列。既存は730日窓ぶんしかウォームアップが無く、
#              初期値の残存は exp(-730*2/201) ≈ 0.07%。**相対%で評価してよい**。
#   CENTERED   z-score（rs_ratio / rs_momentum）や EMA の差分（rs_macd）。
#              **値がゼロ近傍を取るので相対%は発散する**（初回検証で最大12511%を観測）。
#              絶対差で評価する。
#   COUNTER    経路依存カウンタ（ドットの経過日数）。既存は730日窓の先頭252本が
#              ウォームアップガードで抑止されているため、フル履歴で計算し直すと
#              **点灯が復活して値が変わるのが正常**。件数だけ報告する。
EXACT_PREFIXES = ("sma_", "atr_", "adr_", "dist_", "change_", "vol_surge_21",
                  "vol_accum_", "up_down_vol_", "vcr", "td9", "market_cap",
                  "sp_", "is_trend_template", "avg_dollar_volume_")
LEVEL_PREFIXES = ("ema_", "rs_value", "rs_trend_s", "vol_surge_rel_spy_")
CENTERED_PREFIXES = ("rs_ratio_", "rs_momentum_", "rs_macd_", "rs_roc_ema_")
COUNTER_PREFIXES = ("rs_blue_dot_age", "rs_red_dot_age")

# LEVEL 列の許容（実測ベースライン 2026-09-04: ema_200 が中央値 0.10% / p95 0.60%）
LEVEL_MEDIAN_TOLERANCE_PCT = 0.5
LEVEL_P95_TOLERANCE_PCT = 2.0


def _classify(name: str) -> str:
    for kind, prefixes in (("COUNTER", COUNTER_PREFIXES),
                           ("CENTERED", CENTERED_PREFIXES),
                           ("LEVEL", LEVEL_PREFIXES),
                           ("EXACT", EXACT_PREFIXES)):
        if any(name.startswith(pre) or name == pre for pre in prefixes):
            return kind
    return "LEVEL"


def verify(sample: int, chunk_size: int) -> int:
    """再計算結果を既存 Parquet と突合する（書き込みは一切しない）。

    全銘柄を突合すると既存 indicators(2.5GB) を何度も読むことになるため、
    **標本で検証する**。系統的なバグ（窓の切り詰め・SPY の渡し忘れ・列順のズレ）は
    標本で十分に検出できる。
    """
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    parquet_dir = get_parquet_master_dir(config["system"]["db_path"])
    cur = get_latest_master_files(get_pointer_file_path(parquet_dir))
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        return 1

    sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    spy_id = int(sym[sym["ticker"] == "SPY"]["id"].iloc[0])

    # 個別と ETF 系を混ぜて標本にする（ETF は既存値の由来が違う可能性があるため）
    act = sym[sym["active"] == 1]
    etf = act[act["category"].isin(["レバレッジ", "市場", "指標"])]["id"].astype(int).tolist()
    ind_sym = act[act["category"] == "個別"]["id"].astype(int).tolist()
    rng = __import__("random").Random(42)
    picked = sorted(set(rng.sample(ind_sym, min(sample, len(ind_sym)))
                        + rng.sample(etf, min(30, len(etf)))))
    _log(f"標本 {len(picked)} 銘柄（個別 {min(sample, len(ind_sym))} + ETF系 {min(30, len(etf))}）")

    need = set(picked) | {spy_id}
    px = pd.read_parquet(cur["prices"], columns=PRICE_COLS,
                         filters=[("symbol_id", "in", list(need))])
    old = pd.read_parquet(cur["indicators"], filters=[("symbol_id", "in", list(picked))])
    old["date"] = old["date"].astype(str)
    ind_cols = [c for c in pq.ParquetFile(cur["indicators"]).schema_arrow.names
                if c not in ("id", "symbol_id", "date")]
    _log(f"読み込み: prices {len(px):,}行 / 既存 indicators {len(old):,}行")

    new = pd.concat(list(recompute_indicators_chunked(picked, px, ind_cols, spy_id,
                                                     chunk_size=chunk_size)),
                    ignore_index=True)
    new["date"] = new["date"].astype(str)
    _log(f"再計算: {len(new):,}行")

    m = old.merge(new, on=["symbol_id", "date"], suffixes=("_old", "_new"))
    _log(f"突合: {len(m):,}行（既存 {len(old):,} / 再計算 {len(new):,}）")
    if len(m) == 0:
        print("[ERROR] 突合できる行がありません。")
        return 1

    buckets = {"EXACT": [], "LEVEL": [], "CENTERED": [], "COUNTER": []}
    for c in ind_cols:
        a, b = m.get(f"{c}_old"), m.get(f"{c}_new")
        if a is None or b is None or not pd.api.types.is_numeric_dtype(a):
            continue
        both = a.notna() & b.notna()
        if not both.any():
            continue
        av, bv = a[both].astype(float), b[both].astype(float)
        neq = ~np.isclose(av, bv, rtol=1e-9, atol=1e-9)
        n, tot = int(neq.sum()), int(both.sum())
        kind = _classify(c)
        if n == 0:
            buckets[kind].append((c, 0, tot, 0.0, 0.0, ""))
            continue
        diff = (av[neq] - bv[neq]).abs()
        dts = m.loc[both, 'date'].to_numpy()[neq]
        span = f"{dts.min()}〜{dts.max()}" if dts.size else ""
        if kind == "CENTERED":
            # z-score / 差分はゼロ近傍を取るので絶対差で見る
            buckets[kind].append((c, n, tot, float(diff.median()), float(diff.quantile(0.95)), span))
        else:
            rel = (diff / av[neq].abs().replace(0, np.nan) * 100).dropna()
            med = float(rel.median()) if len(rel) else 0.0
            p95 = float(rel.quantile(0.95)) if len(rel) else 0.0
            buckets[kind].append((c, n, tot, med, p95, span))

    def _show(kind, title, unit):
        rows = [r for r in buckets[kind] if r[1] > 0]
        print()
        print("=" * 92)
        print(f"[{kind}] {title}")
        print("=" * 92)
        if not rows:
            print(f"  差分なし（{len(buckets[kind])} 列すべて一致）")
            return
        print(f"  {'列':<24} {'差分行':>9} {'割合':>7} {'中央値'+unit:>11} "
              f"{'p95'+unit:>11}  差分の日付範囲")
        for c, n, tot, med, p95, span in sorted(rows, key=lambda x: -x[1]):
            print(f"  {c:<24} {n:>9,} {n/tot*100:6.2f}% {med:11.5f} {p95:11.5f}  {span}")

    _show("EXACT", "生の価格のローリングのみ。完全一致すべき（真の回帰検査）", "%")
    _show("LEVEL", "EMA を含む水準。既存は730日窓の近似で、再計算が正しい", "%")
    _show("CENTERED", "z-score / EMA差分。ゼロ近傍を取るので絶対差で評価", "")
    _show("COUNTER", "経路依存カウンタ。ウォームアップ復活で変わるのが正常", "%")

    exact_bad = [r for r in buckets["EXACT"] if r[1] > 0]
    print()
    print("=" * 92)
    print("判定")
    print("=" * 92)
    if exact_bad:
        print(f"  ❌ EXACT 系に差分あり（{len(exact_bad)} 列）— 切り詰め起因のバグの疑い")
        print("     生の価格のローリングは既存(730日窓)でも足りているはずなので、"
              "ここが割れるのは実装の問題")
    else:
        print("  ✅ EXACT 系は全列完全一致 — 切り詰め起因のバグなし")

    # LEVEL / CENTERED / COUNTER は合否のゲートにしない。
    # これらの差分は「既存が過去のリビルドで壊れていた箇所を再計算が直している」
    # ことを示す（2026-09-04 の実測で、差分の日付が理論どおりの位置に集中していた）:
    #
    #   rs_trend_s200        2025-01〜2025-06  rolling(200,min_periods=100) の100〜200本目
    #   vol_surge_rel_spy_21 2024-08〜2024-09  ホット期間開始直後の21本以内
    #   ema_200              2026-08〜2026-09  日次T3が730日窓で計算した直近行
    #
    # いずれもホット期間の境界からの相対位置に一致しており、**再計算side が正しい**。
    other = sum(len([r for r in buckets[k] if r[1] > 0])
                for k in ("LEVEL", "CENTERED", "COUNTER"))
    if other:
        print(f"  ℹ  LEVEL/CENTERED/COUNTER に差分 {other} 列 — 日付範囲を確認すること。")
        print("     ホット期間の境界からの相対位置に集中していれば、"
              "既存の破損を再計算が直している（正常）")

    return 0 if not exact_bad else 1


def run(dry_run: bool, chunk_size: int) -> None:
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"Parquet T3 全期間再計算  (dry-run={dry_run} / chunk={chunk_size})")
    print("=" * 74)
    print(f"現行世代: {os.path.basename(cur['indicators'])}")

    t0 = time.time()
    sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    px = pd.read_parquet(cur["prices"], columns=PRICE_COLS)
    old_pf = pq.ParquetFile(cur["indicators"])
    old_schema = old_pf.schema_arrow
    ind_cols = [c for c in old_schema.names if c not in ("id", "symbol_id", "date")]
    _log(f"[1] 読み込み {time.time() - t0:.1f}s  prices {len(px):,}行 / "
         f"既存 indicators {old_pf.metadata.num_rows:,}行 / 指標列 {len(ind_cols)}")

    spy_row = sym[sym["ticker"] == "SPY"]
    if spy_row.empty:
        print("[ERROR] SPY が symbols に見つかりません。RS を計算できないため中断します。")
        sys.exit(1)
    spy_id = int(spy_row["id"].iloc[0])

    target_ids = sorted(px["symbol_id"].unique().tolist())
    first_last = px.groupby("symbol_id")["date"].agg(["min", "max"])
    _log(f"[2] 対象 {len(target_ids):,} 銘柄 / 最古 {first_last['min'].min()} "
         f"〜 最新 {first_last['max'].max()}")

    if dry_run:
        # 全期間が入力になっていることの確認（ETF が 2018 起点に切られていないか）
        etf = sym[sym["category"].isin(["レバレッジ", "市場", "Market", "指標"])]
        etf_ids = set(etf["id"].astype(int))
        pre2018 = first_last[first_last["min"].astype(str) < "2018-04-01"]
        _log(f"[dry-run] pre-2018 の履歴を持つ銘柄: {len(pre2018):,} "
             f"（うち ETF 系 {len(set(pre2018.index) & etf_ids):,}）")
        _log("[dry-run] 書き込んでいません。")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(parquet_dir, f"indicators_{ts}.parquet")

    _log(f"[3] 再計算して逐次書き出し → {os.path.basename(out_path)}")
    writer = None
    written = 0
    t1 = time.time()
    try:
        for n, chunk in enumerate(recompute_indicators_chunked(
                target_ids, px, ind_cols, spy_id, chunk_size=chunk_size), start=1):
            # 既存世代と同じ列順・同じ型に揃える（後段の merge で dtype が壊れないため）
            chunk = chunk.reindex(columns=["symbol_id", "date"] + ind_cols)
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            target_schema = pa.schema([old_schema.field(c) for c in table.schema.names])
            table = table.cast(target_schema)
            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema, compression="snappy")
            writer.write_table(table)
            written += table.num_rows
            if n % 5 == 0 or written >= old_pf.metadata.num_rows:
                _log(f"    chunk {n}: 累計 {written:,} 行 ({time.time() - t1:.0f}s)")
    finally:
        if writer is not None:
            writer.close()
    _log(f"[4] 書き出し完了 {written:,} 行 / {time.time() - t1:.0f}s")

    # --- 防護柵: 行数が旧世代より大幅に減っていたら公開しない ---
    old_rows = old_pf.metadata.num_rows
    if written < old_rows * 0.99:
        print(f"[ERROR] 行数が旧世代より減っています（{old_rows:,} → {written:,}）。"
              " ポインタは更新していません。")
        sys.exit(1)

    _log("[5] 新世代の公開")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    for key, base in name_map.items():
        if key == "indicators":
            files[key] = out_path
        else:
            # indicators 以外は同世代へコピーする。pointer が複数世代を混ぜて参照すると
            # prune で参照中のファイルが消える（recompute_parquet_ranks.py と同じ方針）
            dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
            shutil.copy2(cur[key], dst)
            files[key] = dst
        _log(f"    {base:<20} {os.path.getsize(files[key]) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("recompute")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    _log(f"pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")
    print("    ※ T4（ranks）は T3 に依存します。続けて")
    print("       backend/scripts/recompute_parquet_ranks.py --apply を実行してください")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Parquet の T3 を全期間再計算する")
    p.add_argument("--dry-run", action="store_true", help="対象を表示して書き込まない")
    p.add_argument("--verify", action="store_true",
                   help="標本で既存 Parquet と突合する（書き込まない）")
    p.add_argument("--sample", type=int, default=300, help="--verify で使う個別銘柄数")
    p.add_argument("--apply", action="store_true", help="実際に新世代を書き出す")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                   help=f"1チャンクあたりの銘柄数（既定 {DEFAULT_CHUNK_SIZE}）")
    a = p.parse_args()
    if not a.apply and not a.dry_run and not a.verify:
        p.error("--dry-run / --verify / --apply のいずれかを指定してください")
    if a.verify:
        # 読み取りのみなのでロック不要
        raise SystemExit(verify(a.sample, a.chunk_size))
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損の実績）
    with pipeline_lock("recompute_parquet_indicators"):
        run(a.dry_run, a.chunk_size)
