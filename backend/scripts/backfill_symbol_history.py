"""履歴が欠落した銘柄の T2 を全期間で取り直す（issue_list P1「T2 の初回取得が…」対応）。

## 背景

`t2_prices.py` の取得起点は「既存データがあれば `current_max + 1日` から」という差分取得。
新規銘柄の初回取得が yfinance のレート制限（HTTP 429。yfinance は
`possibly delisted; no price data found` として握り潰す）で部分的にしか返らず
**1日分でも入ってしまうと、以降は差分取得に切り替わり過去を二度と取りに行かない**。
一度この状態に落ちると自力では回復しないため、既存行を削除して初回扱いに戻す必要がある。

## 処理

    1. Parquet マスターを基準に、保有行数が閾値以下の active 銘柄を抽出（仮想テーマは除外）
    2. 既存行を CSV へ退避
    3. SQLite の daily_prices / indicators / relative_ranks から該当行を削除
    4. yfinance から全期間を取り直して daily_prices へ投入（銘柄間にウェイトを入れる）

T3 以降（指標・ランク・Parquet 反映）は通常のパイプライン実行に任せる。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_symbol_history.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_symbol_history.py
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_symbol_history.py --tickers MASI,BLD
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from data_collection.fetcher import fetch_daily_data
from db.database import init_db, get_write_db
from db.models import DailyPrice, Indicator, RelativeRank, Symbol
from pipeline.parquet_cache_manager import get_latest_master_files, get_parquet_master_dir, get_pointer_file_path
from pipeline.utils import attach_market_cap, sanitize_numeric

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROW_THRESHOLD = 20        # Parquet 保有行数がこれ以下なら履歴欠落とみなす
FETCH_INTERVAL_SEC = 2.0  # 銘柄間のウェイト（レート制限回避）


def find_candidates(db_path: str, threshold: int) -> list[tuple[int, str, int]]:
    """Parquet マスターを基準に履歴欠落銘柄を抽出する。"""
    pointer = get_pointer_file_path(get_parquet_master_dir(db_path))
    latest = get_latest_master_files(pointer)
    if not latest:
        raise FileNotFoundError("latest_master.json を解決できません")

    px = pd.read_parquet(latest["prices"], columns=["symbol_id", "date"])
    counts = px.groupby("symbol_id").size().to_dict()

    with get_write_db() as db:
        rows = db.query(Symbol.id, Symbol.ticker, Symbol.theme_type).filter(Symbol.active == 1).all()

    out = []
    for sid, ticker, theme_type in rows:
        if theme_type == "virtual":
            continue  # 仮想テーマは構成銘柄から合成されるため取得対象外
        n = counts.get(sid, 0)
        if n <= threshold:
            out.append((sid, ticker, n))
    return sorted(out, key=lambda x: (x[2], x[1]))


def run(dry_run: bool, threshold: int, only_tickers: list[str] | None, interval: float):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]
    init_db(db_path)
    start_date = config["data_collection"]["default_start_date"]

    print("=" * 72)
    print(f"履歴欠落銘柄のバックフィル  (dry-run={dry_run})")
    print("=" * 72)

    targets = find_candidates(db_path, threshold)
    if only_tickers:
        want = {t.upper() for t in only_tickers}
        targets = [t for t in targets if t[1].upper() in want]

    print(f"\n[1] 対象: {len(targets)} 銘柄（Parquet 保有行数 <= {threshold}、仮想テーマ除く）")
    print(f"    取得開始日: {start_date}")
    for sid, ticker, n in targets:
        print(f"    {ticker:<9} id={sid:<6} Parquet {n:>3} 行")

    if not targets:
        print("\n対象がありません。")
        return

    # --- 2) 既存行の退避 -------------------------------------------------
    report_dir = os.path.join(_project_root, "data", "maintenance_reports")
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(report_dir, f"backfill_backup_{stamp}.csv")

    ids = [t[0] for t in targets]
    with get_write_db() as db:
        existing = db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(ids)).all()
        n_dp = len(existing)
        n_ind = db.query(Indicator).filter(Indicator.symbol_id.in_(ids)).count()
        n_rr = db.query(RelativeRank).filter(RelativeRank.symbol_id.in_(ids)).count()

        print(f"\n[2] 削除対象の既存行  daily_prices={n_dp} / indicators={n_ind} / relative_ranks={n_rr}")
        if not dry_run:
            with open(backup_path, "w", encoding="utf-8", newline="\n") as f:
                w = csv.writer(f, lineterminator="\n")
                w.writerow(["symbol_id", "date", "open", "high", "low", "close", "volume", "market_cap"])
                for p in existing:
                    w.writerow([p.symbol_id, p.date, p.open, p.high, p.low, p.close, p.volume, p.market_cap])
            print(f"    退避 → {backup_path}")

        # --- 3) 削除（初回扱いに戻す） -----------------------------------
        if not dry_run:
            db.query(RelativeRank).filter(RelativeRank.symbol_id.in_(ids)).delete(synchronize_session=False)
            db.query(Indicator).filter(Indicator.symbol_id.in_(ids)).delete(synchronize_session=False)
            db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            print("    → 削除完了（次回取得が初回扱いになります）")

    if dry_run:
        print("\n[3] [DRY-RUN] 取得は行いません。")
        print(f"    本実行すると {len(targets)} 銘柄を {start_date} から取得します"
              f"（銘柄間 {interval}s ウェイト、推定 {len(targets) * interval / 60:.1f} 分＋取得時間）")
        return

    # --- 4) 全期間の取り直し ---------------------------------------------
    print(f"\n[4] 全期間の取得（銘柄間 {interval}s ウェイト）")
    ok = ng = 0
    failed = []
    for i, (sid, ticker, _n) in enumerate(targets, start=1):
        df = fetch_daily_data(ticker, start_date, progress=f"[{i}/{len(targets)}]")
        if df is None or df.empty:
            logger.warning(f"[{ticker}] 取得できませんでした（上場廃止の可能性）")
            ng += 1
            failed.append(ticker)
            time.sleep(interval)
            continue

        df = attach_market_cap(ticker, df, logger)
        recs = [
            DailyPrice(
                symbol_id=sid,
                date=row["date"],
                open=sanitize_numeric(row, "open"),
                high=sanitize_numeric(row, "high"),
                low=sanitize_numeric(row, "low"),
                close=sanitize_numeric(row, "close"),
                volume=int(row["volume"]) if sanitize_numeric(row, "volume") is not None else 0,
                market_cap=sanitize_numeric(row, "market_cap"),
            )
            for _, row in df.iterrows()
        ]
        with get_write_db() as db:
            db.bulk_save_objects(recs)
            db.commit()
        logger.info(f"[{ticker}] {len(recs)} 行を投入（{df['date'].min()} 〜 {df['date'].max()}）")
        ok += 1
        time.sleep(interval)

    print("\n--- 結果 ---")
    print(f"  成功: {ok} 銘柄 / 失敗: {ng} 銘柄")
    if failed:
        print(f"  取得できなかった銘柄（真の上場廃止候補）: {failed}")
    print("\n次の手順: パイプラインを実行して T3 以降と Parquet 反映を行ってください。")
    print("  run\\run_daily_update.bat")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="履歴欠落銘柄の T2 を全期間で取り直す")
    parser.add_argument("--dry-run", action="store_true", help="変更せず対象だけ表示")
    parser.add_argument("--threshold", type=int, default=ROW_THRESHOLD,
                        help=f"Parquet 保有行数の閾値（既定 {ROW_THRESHOLD}）")
    parser.add_argument("--tickers", type=str, default=None, help="対象を絞る（カンマ区切り）")
    parser.add_argument("--interval", type=float, default=FETCH_INTERVAL_SEC,
                        help=f"銘柄間のウェイト秒（既定 {FETCH_INTERVAL_SEC}）")
    args = parser.parse_args()
    run(dry_run=args.dry_run, threshold=args.threshold,
        only_tickers=args.tickers.split(",") if args.tickers else None,
        interval=args.interval)
