"""旧 Parquet 世代から `fx_rates` を復元する（全期間再構築の後始末）。

## なぜ必要か

`fx_rates` は SQLite を正として **Parquet を置換する**テーブル（`daily_prices` のような
マージではない）。全期間再構築はサンドボックスの空 DB から始めるため、FX 同期は
「直近30日」しか取得せず、**その23行が7,717行を置き換えてしまう**。

    再構築前  7,717行  1996-10-30 〜 2026-08-04
    再構築後     23行  2026-07-07 〜 2026-08-05    ← 30年分の履歴が消える

2026-07-30 の再構築でも同じことが起き（7,711行 → 22行）、2026-08-01 に
「fx を Parquet 対象に加える」修正を入れたが、**再構築時に空にならず「少しだけ入る」**
経路は塞げていなかった。既存の安全弁は「空なら旧世代を維持」なので 23 行は通過する。

## やること

旧世代と現行世代の `fx_rates` を **和集合**にして新世代を書き出す
（`(currency_pair, date)` で重複除去し、新しい方を優先）。他のファイルはコピーする。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\restore_fx_from_generation.py \\
        --from data/parquet_master_pre_rebuild_20260806_084108 --dry-run
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
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)

FX_KEYS = ["currency_pair", "date"]


def merge_fx(df_old: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    """旧世代と現行世代の和集合を作る。重複は**現行世代を優先**する。

    現行世代は再構築で取り直した直近の値を持つため、そちらが新しい。
    """
    for df in (df_old, df_new):
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)
    merged = pd.concat([df_old, df_new], ignore_index=True)
    keys = [k for k in FX_KEYS if k in merged.columns]
    merged = merged.drop_duplicates(subset=keys, keep="last")
    return merged.sort_values(keys).reset_index(drop=True)


def run(source_dir: str, dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    parquet_dir = get_parquet_master_dir(config["system"]["db_path"])
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    src_pointer = os.path.join(source_dir, "latest_master.json")
    if not os.path.exists(src_pointer):
        print(f"[ERROR] 復元元に latest_master.json がありません: {source_dir}")
        sys.exit(1)
    src = {k: os.path.join(source_dir, os.path.basename(v))
           for k, v in json.load(open(src_pointer, encoding="utf-8")).items()}

    print("=" * 74)
    print(f"fx_rates の復元  (dry-run={dry_run})")
    print("=" * 74)

    df_old = pd.read_parquet(src["fx"])
    df_new = pd.read_parquet(cur["fx"])
    print(f"  復元元 : {len(df_old):>7,}行  {df_old['date'].min()} 〜 {df_old['date'].max()}")
    print(f"  現行   : {len(df_new):>7,}行  {df_new['date'].min()} 〜 {df_new['date'].max()}")

    merged = merge_fx(df_old, df_new)
    print(f"  マージ : {len(merged):>7,}行  {merged['date'].min()} 〜 {merged['date'].max()}")

    if len(merged) <= len(df_new):
        print("\n復元するものがありません（現行が既に完全）。終了します。")
        return
    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    print("\n新世代の書き出し...")
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "fx":
            merged.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("fx_restore")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="旧 Parquet 世代から fx_rates を復元する")
    p.add_argument("--from", dest="source", required=True, help="復元元の Parquet 世代ディレクトリ")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損）
    with pipeline_lock("restore_fx_from_generation"):
        run(a.source, dry_run=not a.apply)
