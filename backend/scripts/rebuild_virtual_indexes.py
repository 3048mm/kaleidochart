"""仮想テーマ指数を Parquet 全期間から作り直す。

## いつ使うか

仮想テーマ指数は構成銘柄の日次平均リターンを基準値 1000 から連鎖させて作る。
T2 の再合成（`build_all_virtual_indexes_prices`）は **SQLite（直近730日）**から
読んでいたため、再合成が走ると**指数が窓の左端で 1000 に振り直され**、
Parquet に残る古い履歴との継ぎ目に偽の段差ができていた。

2026-08-07 07:49 に構成銘柄の変更をきっかけに全170テーマが再合成され、
当時の最古日 **2024-08-06** で 165本がリセットされた（152本が同日に段差）:

```
_HLTHCB_  2024-08-06  close=1019.65  当日リターン -98.2%   ← 直前は ~55,700
_BLCK3F_  2024-08-06  close=1033.16  当日リターン -92.5%
_BLOK_    2024-08-06  close=1015.56  当日リターン -91.5%
```

T2 側は修正済み（rebuild 時は Parquet 全期間を読む）だが、**既に壊れたデータは
自力では直らない**。本スクリプトで作り直す。

構成銘柄の価格を手当てした後（例: `adjust_symbol_split.py`）にも使う。

## やること

  1. 対象テーマの指数を Parquet 全期間から再合成
  2. 段差が残っていないか検算（残っていれば**その旨を報告して中断**）
  3. 対象テーマの T3 を再計算
  4. T4 を全期間再計算（横断的なので母集団が変わった日付は全銘柄が影響を受ける）
  5. 新世代を書き出してポインタを更新
  6. **SQLite ホットキャッシュにも反映**（省くと翌日のマージで元に戻る）
  7. `virtual_theme_hashes.json` から対象を落として次回 T2 に再合成させる

> [!NOTE]
> **本スクリプトは冪等。** 同じ入力から同じ指数を作り直すだけなので、
> 何度実行しても結果は変わらない（`adjust_symbol_split.py` とはここが違う）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\rebuild_virtual_indexes.py --all --dry-run
    ... --all --apply
    ... --tickers _CNSM0A_,_GRCL29_ --apply
"""

import argparse
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)
from pipeline.parquet_maintenance import (  # noqa: E402
    drop_virtual_theme_hashes,
    replace_sqlite_rows,
    resolve_db_path,
    write_master_generation,
)
from pipeline.parquet_recompute import (  # noqa: E402
    rebuild_virtual_index_prices,
    recompute_indicators,
    recompute_ranks,
)

VIRTUAL_THEME_HASH_FILE = os.path.join("data", "virtual_theme_hashes.json")

# 再合成後に残っていたら異常とみなす日次リターンの幅。
# 指数は構成銘柄（平均で8.6銘柄）のリターン平均なので、単独銘柄より必ず穏やかになる。
# ±40% を超えるのは合成の失敗か、構成銘柄側に未処理の段差が残っているサイン。
SANITY_RETURN_LIMIT = 0.40

# 基準値 1000 の近傍とみなす幅。
BASE_VALUE_BAND = (950.0, 1060.0)

# 基準値へ「跳んで」戻ったとみなす日次リターンの大きさ。
# 近傍にいるだけでは判定できない（立ち上がり直後は自然に 1000 付近にいる）。
BASE_RESTART_JUMP = 0.50


def find_virtual_theme_ids(symbols: pd.DataFrame,
                           tickers: list[str] | None = None) -> list[int]:
    """仮想テーマ（ティッカーが `_` で始まり `_` で終わる）の symbol_id を返す。

    実在 ETF のテーマは合成しないので含めない。
    """
    virt = symbols[
        symbols["ticker"].str.startswith("_") & symbols["ticker"].str.endswith("_")
    ]
    if tickers:
        want = {t.strip() for t in tickers if t.strip()}
        virt = virt[virt["ticker"].isin(want)]
        missing = want - set(virt["ticker"])
        if missing:
            print(f"[ERROR] 仮想テーマとして見つかりません: {sorted(missing)}")
            sys.exit(1)
    return sorted(int(i) for i in virt["id"])


def check_index_sanity(index_rows: pd.DataFrame, symbols: pd.DataFrame,
                       limit: float = SANITY_RETURN_LIMIT) -> pd.DataFrame:
    """再合成した指数に残っている段差を洗い出す。

    ここで出るのは **合成の失敗** か **構成銘柄側の未処理の段差**（未調整の分割など）。
    指数は構成銘柄のリターン平均なので、単独銘柄より必ず穏やかになるはずである。

    Returns:
        `symbol_id` / `ticker` / `date` / `ret` の DataFrame（空なら健全）。
    """
    df = index_rows.sort_values(["symbol_id", "date"]).copy()
    df["ret"] = df.groupby("symbol_id")["close"].pct_change()
    bad = df[df["ret"].abs() > limit].dropna(subset=["ret"]).copy()
    if bad.empty:
        return bad
    name = symbols.set_index("id")["ticker"]
    bad["ticker"] = bad["symbol_id"].map(name)
    return bad[["symbol_id", "ticker", "date", "close", "ret"]]


def check_no_base_value_restart(index_rows: pd.DataFrame, symbols: pd.DataFrame,
                                jump: float = BASE_RESTART_JUMP) -> pd.DataFrame:
    """途中で**跳んで**基準値 1000 に戻っている行を洗い出す。

    2026-08-07 の事故の指紋がこれ（165/170 本がホットキャッシュ境界日に ~1000）:

        _HLTHCB_  2024-08-06  close=1019.65  当日リターン -98.2%  ← 直前は ~55,700

    > [!IMPORTANT]
    > **「基準値の近く」だけで判定してはいけない。** 指数は初日に 1000 から始まるので
    > 立ち上がり直後は自然にその近傍にいる。実データ（170テーマ・2018-04-03 開始）で
    > 21,235行が該当し、2018-04-04〜04-10 に 146〜160 テーマが集中した — 全部ただの
    > ウォームアップだった。じりじり下げて 1000 を通過するのも正常な値動き。
    >
    > リセットは**不連続**なので、基準値近傍であることに加えて
    > **大きなリターンを伴う**ことを要求する。

    Returns:
        `symbol_id` / `ticker` / `date` / `close` / `ret` の DataFrame（空なら健全）。
    """
    df = index_rows.sort_values(["symbol_id", "date"]).copy()
    df["ret"] = df.groupby("symbol_id")["close"].pct_change()
    lo, hi = BASE_VALUE_BAND
    hit = df[
        (df["close"] > lo) & (df["close"] < hi) & (df["ret"].abs() > jump)
    ].dropna(subset=["ret"]).copy()
    if hit.empty:
        return hit
    name = symbols.set_index("id")["ticker"]
    hit["ticker"] = hit["symbol_id"].map(name)
    return hit[["symbol_id", "ticker", "date", "close", "ret"]]


def run(tickers: list[str] | None, dry_run: bool, db_path: str | None = None,
        sanity_limit: float = SANITY_RETURN_LIMIT):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = resolve_db_path(config, db_path)
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"仮想テーマ指数の再合成  (dry-run={dry_run})")
    print("=" * 74)
    print(f"[環境] SQLite  : {os.path.abspath(db_path)}")
    print(f"[環境] Parquet : {os.path.abspath(parquet_dir)}")

    sym = pd.read_parquet(cur["symbols"])
    tc = pd.read_parquet(cur["tc"])
    theme_ids = find_virtual_theme_ids(sym, tickers)
    if not theme_ids:
        print("\n対象がありません。終了します。")
        return
    spy_id = int(sym[sym["ticker"] == "SPY"]["id"].iloc[0])

    px = pd.read_parquet(cur["prices"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    print(f"\n[1] 対象テーマ {len(theme_ids)}件")
    old = px[px["symbol_id"].isin(theme_ids)]
    print(f"    現在の指数行数: {len(old):,}行")

    # --- [2] 再合成 ---
    print("\n[2] Parquet 全期間から再合成...")
    rebuilt = rebuild_virtual_index_prices(theme_ids, px, tc)
    if rebuilt.empty:
        print("[ERROR] 1行も再合成できませんでした（構成銘柄が空？）。")
        sys.exit(1)
    print(f"    再合成: {len(rebuilt):,}行  "
          f"{rebuilt['date'].min()} 〜 {rebuilt['date'].max()}")

    # --- [3] 検算 ---
    print("\n[3] 検算...")
    bad = check_index_sanity(rebuilt, sym, sanity_limit)
    restart = check_no_base_value_restart(rebuilt, sym)
    print(f"    |日次リターン| > {sanity_limit:.0%} の行: {len(bad)}")
    if not bad.empty:
        print(bad.head(15).to_string(index=False))
    print(f"    跳んで基準値1000に戻っている行: {len(restart)}")
    if not restart.empty:
        by_date = restart.groupby("date")["symbol_id"].nunique().sort_values(ascending=False)
        print(f"    日付別（上位5）:\n{by_date.head(5).to_string()}")
        if int(by_date.iloc[0]) > max(3, len(theme_ids) // 10):
            print("[ERROR] 多数のテーマが同じ日に基準値へ戻っています。"
                  " 窓の左端でリセットされた疑いがあるため中断します。")
            sys.exit(1)

    # 変更前後の比較（何がどれだけ動くかを昇格前に見せる）
    cmp_old = old[["symbol_id", "date", "close"]].rename(columns={"close": "old"})
    cmp = rebuilt[["symbol_id", "date", "close"]].merge(cmp_old, on=["symbol_id", "date"],
                                                        how="inner")
    if not cmp.empty:
        cmp["ratio"] = cmp["close"] / cmp["old"]
        moved = cmp.groupby("symbol_id")["ratio"].agg(["min", "max"])
        changed = moved[(moved["min"] < 0.999) | (moved["max"] > 1.001)]
        print(f"\n    値が変わるテーマ: {len(changed)} / {len(moved)}")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    # --- [4] Parquet の価格を差し替え ---
    print("\n[4] 指標と順位の再計算...")
    keep_px = px[~px["symbol_id"].isin(theme_ids)]
    new_px = pd.concat([keep_px, rebuilt.reindex(columns=px.columns)], ignore_index=True)
    new_px.sort_values(["symbol_id", "date"], inplace=True, ignore_index=True)
    del keep_px

    ind = pd.read_parquet(cur["indicators"])
    ind["date"] = pd.to_datetime(ind["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    ind_cols = [c for c in ind.columns if c not in ("id", "symbol_id", "date")]

    keep_ind = ind[~ind["symbol_id"].isin(theme_ids)]
    new_rows = recompute_indicators(theme_ids, new_px, ind_cols, spy_id)
    next_id = int(pd.to_numeric(ind["id"], errors="coerce").max()) + 1
    new_rows = new_rows.copy()
    new_rows["id"] = range(next_id, next_id + len(new_rows))
    # **concat の前に dtype を既存へ揃える**（1列でも object が混じると600万行が
    # object に巻き上げられ、sort のコピーで OOM する）
    for col in ind.columns:
        want = ind[col].dtype
        if col in new_rows.columns and new_rows[col].dtype != want:
            try:
                new_rows[col] = new_rows[col].astype(want)
            except (TypeError, ValueError):
                new_rows[col] = pd.to_numeric(new_rows[col], errors="coerce")

    merged_ind = pd.concat([keep_ind, new_rows[ind.columns]], ignore_index=True)
    del keep_ind, new_rows
    merged_ind.sort_values(["symbol_id", "date"], inplace=True, ignore_index=True)
    print(f"    indicators {len(ind):,} → {len(merged_ind):,}行")
    del ind

    old_rank_rows = pq.ParquetFile(cur["ranks"]).metadata.num_rows
    new_ranks = recompute_ranks(merged_ind, sym)
    new_ranks.insert(0, "id", range(1, len(new_ranks) + 1))
    print(f"    ranks {old_rank_rows:,} → {len(new_ranks):,}行")

    # --- [5] 新世代 ---
    print("\n[5] 新世代の書き出し...")
    try:
        write_master_generation(
            parquet_dir, pointer_file, cur,
            {"prices": new_px, "indicators": merged_ind, "ranks": new_ranks},
            label="rebuild_virtual_indexes")
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # --- [6] ホットキャッシュ（省くと翌日のマージで戻る） ---
    print("\n[6] SQLite ホットキャッシュの同期...")

    def _n(v, unit):
        return "テーブルなし" if v is None else f"{v:,}行 {unit}"

    n_px = replace_sqlite_rows(db_path, "daily_prices", theme_ids,
                               new_px[new_px["symbol_id"].isin(theme_ids)], dry_run=False)
    print(f"    daily_prices     {_n(n_px, '差し替え')}")
    n_ind = replace_sqlite_rows(db_path, "indicators", theme_ids,
                                merged_ind[merged_ind["symbol_id"].isin(theme_ids)],
                                dry_run=False)
    print(f"    indicators       {_n(n_ind, '差し替え')}")
    n_rk = replace_sqlite_rows(db_path, "relative_ranks", theme_ids,
                               new_ranks[new_ranks["symbol_id"].isin(theme_ids)],
                               dry_run=False)
    print(f"    relative_ranks   {_n(n_rk, '差し替え')}")

    # --- [7] 次回 T2 に再合成させる ---
    n_hash = drop_virtual_theme_hashes(
        os.path.join(_project_root, VIRTUAL_THEME_HASH_FILE), theme_ids, dry_run=False)
    print(f"\n[7] virtual_theme_hashes.json から {n_hash}件を削除（次回 T2 で再合成）")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="仮想テーマ指数を Parquet 全期間から作り直す")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="全ての仮想テーマを対象にする")
    g.add_argument("--tickers", type=str,
                   help="対象を絞る（カンマ区切り。例: _CNSM0A_,_GRCL29_）")
    p.add_argument("--db-path", default=None,
                   help="書き込み先 SQLite を明示指定（省略時は STOCKTOOL_ENV / "
                        "STOCKTOOL_DB_PATH / config.toml の順で解決）")
    p.add_argument("--sanity-limit", type=float, default=SANITY_RETURN_LIMIT,
                   help=f"異常とみなす日次リターンの幅（既定 {SANITY_RETURN_LIMIT}）")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    tickers = a.tickers.split(",") if a.tickers else None
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損）
    with pipeline_lock("rebuild_virtual_indexes"):
        run(tickers, dry_run=not a.apply, db_path=a.db_path,
            sanity_limit=a.sanity_limit)
