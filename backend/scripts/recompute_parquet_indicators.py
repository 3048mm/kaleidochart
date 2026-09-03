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
    p.add_argument("--apply", action="store_true", help="実際に新世代を書き出す")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                   help=f"1チャンクあたりの銘柄数（既定 {DEFAULT_CHUNK_SIZE}）")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損の実績）
    with pipeline_lock("recompute_parquet_indicators"):
        run(a.dry_run, a.chunk_size)
