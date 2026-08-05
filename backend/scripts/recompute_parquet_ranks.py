"""Parquet の T4（relative_ranks）を全期間再計算して新世代を書き出す。

## なぜ必要か

T4 は **SQLite の `indicators` に存在する日付しか計算できない**。SQLite は直近730日
しか持たないため、`update_pipeline.py --rebuild-from T4` を回しても
**直近730日の順位しか作られない**。

2026-08-05 に改称6件を適用した際、この経路で5時間かけて再構築したが
`T3=2096 / T4=500` にしかならなかった。全期間を埋めるには
`run_production_restore.py`（SQLite を全期間復元）→ `--rebuild-from T4` が必要で
**約3.5時間**。本スクリプトなら **Parquet だけを読み書きして約4分**で済む。

## 実装の正しさの担保

SQLite の `relative_ranks` と突合して検証済み（2026-08-05）:

    2026-07-31 / 2026-06-02 / 2025-03-14 / 2024-08-06 の4日付
    × 22ランク列すべてで 最大誤差 0.000e+00

## 副作用（実行前に理解すること）

**現在の `active` フラグで再計算する。** Parquet の時系列テーブルはマージのみで
行削除が伝播しないため、退役・改称した銘柄の古い順位が残っている。
再計算するとそれらは母集団から外れる。

  - バックテスト結果への直接影響はない（`backtest_screener.py` が `active == 1` で除外）
  - **他銘柄のパーセンタイル値はわずかに変わる**（母集団が変動するため）
  - したがって**過去の最適化結果は厳密には再現しなくなる**

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_ranks.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_ranks.py --apply
"""

import argparse
import json
import os
import shutil
import sys
import time
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
from pipeline.parquet_recompute import INDICATORS_TO_RANK, recompute_ranks  # noqa: E402


def run(dry_run: bool):
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
    print(f"Parquet T4 全期間再計算  (dry-run={dry_run})")
    print("=" * 74)
    print(f"現行世代: {os.path.basename(cur['prices'])}")

    t0 = time.time()
    sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    src_cols = [c for c, _ in INDICATORS_TO_RANK]
    ind = pd.read_parquet(cur["indicators"], columns=["symbol_id", "date"] + src_cols)
    old = pd.read_parquet(cur["ranks"])
    print(f"\n[1] 読み込み {time.time() - t0:.1f}s  "
          f"indicators {len(ind):,}行 / 既存 ranks {len(old):,}行")

    t1 = time.time()
    new = recompute_ranks(ind, sym)
    print(f"[2] 再計算 {time.time() - t1:.0f}s  → {len(new):,}行 / {new['date'].nunique():,}日")

    # --- 差分の内訳 ---
    print("\n[3] 差分の内訳")
    old_keys = set(zip(old["symbol_id"], old["date"]))
    new_keys = set(zip(new["symbol_id"], new["date"]))
    removed, added = old_keys - new_keys, new_keys - old_keys
    print(f"    消える行: {len(removed):,}  （退役・改称で active=0 になった銘柄の残骸）")
    print(f"    増える行: {len(added):,}  （T4 が欠けていた銘柄の過去分）")

    id2t = dict(zip(sym["id"], sym["ticker"]))
    for label, keys in (("消える", removed), ("増える", added)):
        if not keys:
            continue
        cnt = pd.Series([id2t.get(s, s) for s, _ in keys]).value_counts()
        print(f"    {label}銘柄 上位: {dict(cnt.head(8))}")

    # --- 既存と一致すべき範囲の確認（実装の健全性チェック） ---
    m = old.merge(new, on=["symbol_id", "date"], suffixes=("_old", "_new"))
    worst, worst_col = 0.0, None
    for _, rc in INDICATORS_TO_RANK:
        d = (m[f"{rc}_old"] - m[f"{rc}_new"]).abs().max()
        if pd.notna(d) and d > worst:
            worst, worst_col = d, rc
    print(f"\n[4] 共通行 {len(m):,} の最大変動: {worst:.3e} ({worst_col})")
    print("    ※ 母集団が変わるため 0 にはならない。1/(n-1)≈3.5e-4 の数倍なら妥当")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    # --- 新世代の書き出し ---
    print("\n[5] 新世代の書き出し...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    # ranks 以外は同世代へコピーする。pointer が複数世代を混ぜて参照すると
    # prune で参照中のファイルが消える。
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "ranks":
            out = new.reset_index(drop=True)
            out.insert(0, "id", range(1, len(out) + 1))
            out.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("recompute")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Parquet の T4 を全期間再計算する")
    p.add_argument("--dry-run", action="store_true", help="差分だけ表示して書き込まない")
    p.add_argument("--apply", action="store_true", help="実際に新世代を書き出す")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    run(dry_run=not a.apply)
