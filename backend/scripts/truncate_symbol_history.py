"""逆さ合併・再上場などで「別会社の履歴」が繋がっている銘柄を、指定日以降に切り詰める。

## いつ使うか

ティッカーが同じでも、**逆さ合併（reverse merger）を挟むと前後で別の会社**になる。
データ提供側は系列を繋げて配信するため、そのままでは

  - バックテストが「合併前の別会社」を同じ銘柄として売買する
  - 合併日に実在しない単日リターンを計上する

という汚染が起きる。分割の未調整とは**原因も対処も異なる**（分割は調整で直るが、
これは切り離すしかない）。

## 実例（2026-08-06）

`JBIO`（Jade Biosciences）は 2025-04-28 に Aerovate Therapeutics と逆さ合併し、
04-29 から新体制で取引開始。

```
2025-04-28  91.40   ← Aerovate の株価を 1:35 で調整した値（$2.68 × 35）
2025-04-29  10.14   ← Jade Biosciences として取引開始
```

**Yahoo の現在の系列にも同じ段差がある**ため、再取得しても直らない。

## やること

  1. 指定日より前の価格・指標・順位を Parquet から除去
  2. 対象銘柄の T3 を再計算
  3. T4 を全期間再計算（横断的なので母集団が変わった日付は全銘柄が影響を受ける）
  4. **SQLite ホットキャッシュからも同じ行を除去**

> [!IMPORTANT]
> **4 を省くと翌日のデイリー更新で切り詰めが元に戻る。**
> `daily_prices` などは Parquet と SQLite の**マージ**（`drop_duplicates(keep='last')`・
> SQL 側優先）で伝播し、**マージは行を削除しない**。直近730日に合併前の行が残っていれば
> そのまま Parquet に復活する。2026-08-06 に `JBIO` で実際に発生した
> （01:43 時点では消えていたが 06:49 のデイリーで段差が戻った）。

> [!WARNING]
> **全期間再構築（`run/tool/refresh_All.bat`）を実行したら、切り詰めは再適用が必要。**
> 再構築は Yahoo から取り直すため、上流に残っている段差がそのまま戻ってくる。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\truncate_symbol_history.py \\
        --ticker JBIO --from 2025-04-29 --reason "Aerovate との逆さ合併" --dry-run
    ... --apply
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import pandas as pd

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
    update_pointer_with_retry,
)
from pipeline.parquet_recompute import (  # noqa: E402
    recompute_indicators,
    recompute_ranks,
)


# マージで SQLite → Parquet に戻る階層。**ここを消し忘れると切り詰めが翌日で無効化される。**
TRUNCATE_TABLES = ("daily_prices", "indicators", "relative_ranks")


def purge_sqlite_history(db_path: str, symbol_id: int, keep_from: str,
                         dry_run: bool, expect_ticker: str | None = None
                         ) -> dict[str, int | None]:
    """ホットキャッシュ（SQLite）からも指定日より前の行を消す。

    **Parquet だけ消しても翌日のデイリー更新で戻る。** `daily_prices` などは
    Parquet と SQLite の**マージ**（`drop_duplicates(keep='last')`・SQL 側優先）で
    伝播し、**マージは行を削除しない**ため、直近730日に残った合併前の行が
    そのまま Parquet に復活する（2026-08-06 に `JBIO` で実際に発生）。

    Args:
        expect_ticker: `symbol_id` がこのティッカーを指していることを確認してから消す。
            **全期間再構築は `symbols.id` を再採番する**（サンドボックスの空 DB から
            始まるため T1 の id 温存 upsert が働かない。2026-08-06 は 2,378件が変化）。
            Parquet で解決した id を照合せず SQLite に流すと**別銘柄を削る**。

    Raises:
        ValueError: `expect_ticker` と実際のティッカーが食い違う場合（**削除しない**）。

    Returns:
        テーブル名 → 削除件数。テーブルが存在しなければ ``None``。
    """
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")

        if expect_ticker:
            row = con.execute("SELECT ticker FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
            actual = row[0] if row else None
            if actual != expect_ticker:
                raise ValueError(
                    f"symbol_id={symbol_id} は SQLite では {actual!r} を指しており "
                    f"{expect_ticker!r} と一致しません。Parquet と SQLite で id 体系が"
                    f"ずれている可能性があります（全期間再構築の直後など）。"
                    f" 先に run_production_restore.py で SQLite を作り直してください。"
                )

        out: dict[str, int | None] = {}
        con.execute("BEGIN IMMEDIATE")
        for table in TRUNCATE_TABLES:
            # スコープの無い DELETE を撃たないよう、必ず symbol_id と date で絞る
            q = f"FROM {table} WHERE symbol_id = ? AND date < ?"
            try:
                n = con.execute(f"SELECT COUNT(*) {q}", (symbol_id, keep_from)).fetchone()[0]
            except sqlite3.OperationalError:
                out[table] = None      # サンドボックス等でテーブルが無い場合
                continue
            if not dry_run and n:
                con.execute(f"DELETE {q}", (symbol_id, keep_from))
            out[table] = n
        con.commit() if not dry_run else con.rollback()
        return out
    finally:
        con.close()


def run(ticker: str, keep_from: str, reason: str, dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    parquet_dir = get_parquet_master_dir(config["system"]["db_path"])
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"履歴の切り詰め  {ticker}  {keep_from} 以降を残す  (dry-run={dry_run})")
    print(f"理由: {reason}")
    print("=" * 74)

    sym = pd.read_parquet(cur["symbols"])
    hit = sym[sym["ticker"] == ticker]
    if hit.empty:
        print(f"[ERROR] {ticker} が symbols にありません。")
        sys.exit(1)
    sid = int(hit["id"].iloc[0])
    spy_id = int(sym[sym["ticker"] == "SPY"]["id"].iloc[0])

    px = pd.read_parquet(cur["prices"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    target = px[px["symbol_id"] == sid]
    drop = target[target["date"] < keep_from]

    print(f"\n[1] 対象: {ticker} (symbol_id={sid})")
    print(f"    現在: {len(target):,}行  {target['date'].min()} 〜 {target['date'].max()}")
    print(f"    除去: {len(drop):,}行  （{keep_from} より前）")
    print(f"    残り: {len(target) - len(drop):,}行")
    if drop.empty:
        print("\n除去対象がありません。終了します。")
        return

    seam = target[target["date"] >= keep_from].sort_values("date")
    last_old = drop.sort_values("date").iloc[-1]
    print(f"\n[2] 接合部")
    print(f"    {last_old['date']}  {last_old['close']:>10.2f}   ← 除去する最後の行")
    print(f"    {seam.iloc[0]['date']}  {seam.iloc[0]['close']:>10.2f}   ← 残す最初の行")
    print(f"    比率 {seam.iloc[0]['close'] / last_old['close']:.3f}")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    # --- prices / indicators / ranks から除去 ---
    print("\n[3] 除去と再計算...")
    new_px = px[~((px["symbol_id"] == sid) & (px["date"] < keep_from))].copy()

    ind = pd.read_parquet(cur["indicators"])
    ind["date"] = pd.to_datetime(ind["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    ind_cols = [c for c in ind.columns if c not in ("id", "symbol_id", "date")]
    keep_ind = ind[ind["symbol_id"] != sid]

    # 残した価格だけで T3 を計算し直す（除去前の値を引きずらないため）
    new_ind_rows = recompute_indicators([sid], new_px, ind_cols, spy_id)
    next_id = int(pd.to_numeric(ind["id"], errors="coerce").max()) + 1
    new_ind_rows = new_ind_rows.copy()
    new_ind_rows["id"] = range(next_id, next_id + len(new_ind_rows))
    new_ind_rows["symbol_id"] = new_ind_rows["symbol_id"].astype(ind["symbol_id"].dtype)
    merged_ind = pd.concat([keep_ind, new_ind_rows[ind.columns]], ignore_index=True)
    merged_ind = merged_ind.sort_values(["symbol_id", "date"]).reset_index(drop=True)
    print(f"    indicators {len(ind):,} → {len(merged_ind):,}行")

    # T4 は横断的なので全期間を作り直す（母集団が変わった日付は全銘柄が影響を受ける）
    new_ranks = recompute_ranks(merged_ind, sym)
    new_ranks = new_ranks.reset_index(drop=True)
    new_ranks.insert(0, "id", range(1, len(new_ranks) + 1))
    print(f"    ranks {len(pd.read_parquet(cur['ranks'])):,} → {len(new_ranks):,}行")

    # --- 新世代を書き出す ---
    print("\n[4] 新世代の書き出し...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "prices":
            new_px.to_parquet(dst, index=False)
        elif key == "indicators":
            merged_ind.to_parquet(dst, index=False)
        elif key == "ranks":
            new_ranks.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("truncate")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")

    # --- ホットキャッシュからも消す（これを忘れると翌日のマージで戻る） ---
    print("\n[5] SQLite ホットキャッシュの掃除...")
    purged = purge_sqlite_history(config["system"]["db_path"], sid, keep_from,
                                  dry_run=False, expect_ticker=ticker)
    for table, n in purged.items():
        print(f"    {table:<18} {'テーブルなし' if n is None else f'{n:,}行 削除'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="逆さ合併等で別会社の履歴が繋がっている銘柄を切り詰める")
    p.add_argument("--ticker", required=True, help="対象ティッカー")
    p.add_argument("--from", dest="keep_from", required=True,
                   help="この日以降を残す（YYYY-MM-DD）")
    p.add_argument("--reason", default="", help="切り詰めの理由（記録用）")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損）
    with pipeline_lock("truncate_symbol_history"):
        run(a.ticker, a.keep_from, a.reason, dry_run=not a.apply)
