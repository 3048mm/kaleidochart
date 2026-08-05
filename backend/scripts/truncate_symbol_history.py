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
import sys
from datetime import datetime

import pandas as pd

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
from pipeline.parquet_recompute import (  # noqa: E402
    recompute_indicators,
    recompute_ranks,
)


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
    print("\n    SQLite 側は次回の restore_sqlite_cache_from_parquet で追随します。")


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
    run(a.ticker, a.keep_from, a.reason, dry_run=not a.apply)
