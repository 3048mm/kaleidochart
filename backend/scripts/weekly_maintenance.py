import os
import sys
import argparse
import msvcrt
import logging
import time
import sqlite3
from typing import Optional

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import get_active_db_path, get_write_db, init_db
from db.database_user import get_active_user_db_path, init_user_db

# Lock file path (same as update_pipeline.py to prevent concurrent run)
#
# 本番では update_pipeline.py と同じファイルを共有して排他する（architecture.md §13.2）。
# ただしテストはこのスクリプトをサブプロセス起動するため、本番ロックをそのまま使うと
# **日次パイプラインの実行中はテストが必ず失敗する**（2026-07-31 に実際に発生。
# パイプラインが5時間超ロックを保持し、test_weekly_maintenance.py の
# サブプロセステスト5件が軒並み落ちた）。
# --lock-file で差し替え可能にして、テストを本番の実行状況から独立させる。
DEFAULT_LOCK_FILE = os.path.join(project_root, "update_pipeline.lock")
LOCK_FILE = DEFAULT_LOCK_FILE
lock_fd = None

def setup_logging():
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "weekly_maintenance.log")
    
    logger = logging.getLogger("weekly_maintenance")
    logger.setLevel(logging.INFO)
    
    # Reset existing handlers
    for h in logger.handlers[:]:
        logger.removeHandler(h)
        
    log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(log_format)
    logger.addHandler(console)
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logging()

def acquire_lock() -> bool:
    global lock_fd
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
        except:
            pass

def run_physical_maintenance(db_path: str, label: str, dry_run: bool):
    """
    Executes database integrity checks, vacuuming, and reindexing.
    """
    if not os.path.exists(db_path):
        logger.warning(f"[{label}] Database file not found for physical maintenance: {db_path}. Skipping.")
        return
        
    initial_size = os.path.getsize(db_path)
    logger.info(f"[{label}] Database size before maintenance: {initial_size / 1024 / 1024:.2f} MB")
    
    # 1. Integrity Check
    logger.info(f"[{label}] Running PRAGMA integrity_check...")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout = 60000")
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        if result.lower() != "ok":
            logger.error(f"[{label}] Database integrity check FAILED: {result}")
            raise ValueError(f"[{label}] Database is physically corrupted: {result}")
        logger.info(f"[{label}] Database integrity check: OK")
        
        if dry_run:
            logger.info(f"[{label}] Dry-run mode: skipping REINDEX and VACUUM.")
            return
            
        # 2. Reindex
        logger.info(f"[{label}] Rebuilding indexes (REINDEX)...")
        t_reindex = time.time()
        conn.execute("REINDEX")
        logger.info(f"[{label}] REINDEX completed in {time.time() - t_reindex:.2f}s")
        
        # 3. Vacuum
        logger.info(f"[{label}] Reclaiming database pages (VACUUM)...")
        t_vacuum = time.time()
        conn.execute("VACUUM")
        logger.info(f"[{label}] VACUUM completed in {time.time() - t_vacuum:.2f}s")
        
    finally:
        conn.close()
        
    final_size = os.path.getsize(db_path)
    reclaimed = initial_size - final_size
    logger.info(f"[{label}] Database size after maintenance: {final_size / 1024 / 1024:.2f} MB "
                f"(Reclaimed: {reclaimed / 1024 / 1024:.2f} MB, {reclaimed / initial_size * 100:.1f}% space reclaimed)")

                                                                   # noqa: E305
# ---------------------------------------------------------------------------
# 銘柄の鮮度分類
# ---------------------------------------------------------------------------
#
# 「SPY の最新日より古い」だけで上場廃止候補としていたため、履歴取得が
# そもそも成立していない銘柄と、一時的に1日取りこぼした銘柄を区別できず、
# 現役銘柄を退役候補に混ぜていた（2026-07-29 発見）。保有行数を併せて見る。
#
LOW_HISTORY_ROWS = 20      # これ以下は「履歴がそもそも無い」= 上場廃止・改称の疑い
STALE_CALENDAR_DAYS = 7    # 5営業日相当。これ以上古いと供給停止とみなす

# Market Trend Score の直接入力。退役対象には絶対にならないため、
# 上場廃止候補（stale_symbols / CSV）から外し、専用セクションで報告する。
#
# 2026-07-20〜07-29 に Yahoo が ^VIX3M を 8営業日 null で返した際、
# 2,085行の履歴があるため `no_history` には該当せず、`delisted`（退役候補）に
# 紛れて埋もれていた。MTS の入力という重要性が判定に反映されていなかった。
MTS_INPUT_TICKERS = ("^VIX", "^VIX3M")


def classify_symbol_freshness(
    row_count: int,
    last_date,
    spy_latest_date,
    low_history_rows: int = LOW_HISTORY_ROWS,
    stale_days: int = STALE_CALENDAR_DAYS,
) -> str:
    """銘柄の T2 鮮度を4分類する。

    Returns:
        ``"no_history"`` — 保有行数が極端に少ない。上場廃止・ティッカー改称の疑い。
            取り直しても供給側にデータが無いことが多いため、バックフィルではなく退役が対応。
        ``"delisted"``   — 十分な履歴があるのに供給が止まった。退役候補。
        ``"lagging"``    — 完全な履歴があり直近数日だけ欠けている。次回実行が
            ``current_max + 1`` から取りに行くため**自己回復する**ので放置してよい。
        ``"ok"``         — SPY の最新日に追いついている。
    """
    from datetime import timedelta

    if row_count <= low_history_rows or last_date is None:
        return "no_history"
    if last_date < spy_latest_date - timedelta(days=stale_days):
        return "delisted"
    if last_date < spy_latest_date:
        return "lagging"
    return "ok"


def audit_and_fix_weekly(db, dry_run: bool) -> dict:
    """
    Scans the database for weekly anomalies and applies fixes if dry_run=False.
    1. Delisted active symbols.
    2. Active themes with zero constituents.
    3. Invalid constituents pointing to inactive symbols.
    4. Mismatched indicators (DailyPrice exists but Indicator does not).
    """
    from db.models import Symbol, DailyPrice, Indicator, ThemeConstituent
    from sqlalchemy import func
    from datetime import timedelta, date
    import pandas as pd
    from indicators.calculate import calculate_indicators
    
    report = {
        "stale_symbols": [],       # 退役候補 = delisted + no_history（後方互換のティッカー列）
        "delisted_symbols": [],    # (ticker, last_date, rows)
        "no_history_symbols": [],  # (ticker, last_date, rows)
        "lagging_symbols": [],     # (ticker, last_date, rows) — 自己回復するので放置
        "mts_input_status": [],    # (ticker, last_date, rows, 遅延営業日数, 判定)
        "empty_themes": [],
        "invalid_constituents": [],
        "mismatched_indicators_count": 0,
        "fixed_indicators_count": 0,
        "split_anomalies": [],
    }

    # 1. Delisted / Stale active symbols detection
    spy = db.query(Symbol).filter(Symbol.ticker == "SPY", Symbol.active == 1).first()
    if spy:
        spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy.id).scalar()
        if spy_latest_date:
            sub = db.query(
                DailyPrice.symbol_id,
                func.max(DailyPrice.date).label("max_date"),
                func.count(DailyPrice.id).label("row_count"),
            ).group_by(DailyPrice.symbol_id).subquery()

            # outerjoin にすることで「1行も無い銘柄」も検出対象に含める
            # （旧実装は inner join だったため、最も重症な 0 行の銘柄を取りこぼしていた）
            candidates = db.query(Symbol.ticker, sub.c.max_date, sub.c.row_count)\
                .outerjoin(sub, Symbol.id == sub.c.symbol_id)\
                .filter(Symbol.active == 1, Symbol.ticker != "SPY").all()

            for ticker, max_date, row_count in candidates:
                kind = classify_symbol_freshness(row_count or 0, max_date, spy_latest_date)

                # MTS 入力は退役対象になりえないので専用枠へ回す（ok でも状態を残す）
                if ticker in MTS_INPUT_TICKERS:
                    lag = (spy_latest_date - max_date).days if max_date else None
                    report["mts_input_status"].append(
                        (ticker, str(max_date) if max_date else "", int(row_count or 0),
                         lag, kind)
                    )
                    continue

                if kind == "ok":
                    continue
                entry = (ticker, str(max_date) if max_date else "", int(row_count or 0))
                if kind == "delisted":
                    report["delisted_symbols"].append(entry)
                elif kind == "no_history":
                    report["no_history_symbols"].append(entry)
                else:
                    report["lagging_symbols"].append(entry)

            # ok だった MTS 入力も漏らさず記録する（candidates には ok も含まれる）
            recorded = {e[0] for e in report["mts_input_status"]}
            for ticker in MTS_INPUT_TICKERS:
                if ticker not in recorded:
                    report["mts_input_status"].append((ticker, "", 0, None, "missing"))

            for key in ("delisted_symbols", "no_history_symbols", "lagging_symbols"):
                report[key].sort(key=lambda e: (e[2], e[0]))

            # 退役候補（バックフィルでは解決しないもの）だけを stale_symbols に載せる。
            # lagging は次回実行で自己回復するため含めない。
            report["stale_symbols"] = [
                e[0] for e in report["delisted_symbols"] + report["no_history_symbols"]
            ]

    # 2. Empty active themes
    active_themes = db.query(Symbol.id, Symbol.ticker).filter(Symbol.active == 1, Symbol.category == "テーマ").all()
    for theme_id, ticker in active_themes:
        const_count = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == theme_id).count()
        if const_count == 0:
            report["empty_themes"].append(ticker)
            
    # 3. Invalid constituents (pointing to active=0)
    invalid_consts = db.query(ThemeConstituent, Symbol.ticker, Symbol.name)\
        .join(Symbol, ThemeConstituent.symbol_id == Symbol.id)\
        .filter(Symbol.active == 0).all()
        
    for const, ticker, name in invalid_consts:
        report["invalid_constituents"].append({
            "theme_id": const.theme_id,
            "symbol_id": const.symbol_id,
            "ticker": ticker,
            "name": name
        })
        if not dry_run:
            db.query(ThemeConstituent).filter(
                ThemeConstituent.theme_id == const.theme_id,
                ThemeConstituent.symbol_id == const.symbol_id
            ).delete()
            
    if not dry_run and invalid_consts:
        db.commit()
        
    # 4. Mismatched indicators (T2 DailyPrice exists but T3 Indicator is missing, last 730 days)
    max_date = db.query(func.max(DailyPrice.date)).scalar()
    active_symbol_ids = []
    if max_date:
        lookback_cutoff = max_date - timedelta(days=730)
        active_symbol_ids = [s[0] for s in db.query(Symbol.id).filter(Symbol.active == 1).all()]
        
        if active_symbol_ids:
            mismatches = db.query(DailyPrice.symbol_id, DailyPrice.date, Symbol.ticker)\
                .join(Symbol, DailyPrice.symbol_id == Symbol.id)\
                .filter(DailyPrice.symbol_id.in_(active_symbol_ids))\
                .filter(DailyPrice.date >= lookback_cutoff)\
                .outerjoin(Indicator, (DailyPrice.symbol_id == Indicator.symbol_id) & (DailyPrice.date == Indicator.date))\
                .filter(Indicator.id.is_(None))\
                .order_by(DailyPrice.symbol_id, DailyPrice.date).all()
                
            report["mismatched_indicators_count"] = len(mismatches)
            
            if not dry_run and len(mismatches) > 0:
                # Group by symbol_id to calculate once per symbol
                mismatches_by_symbol = {}
                for sid, d_val, ticker in mismatches:
                    mismatches_by_symbol.setdefault(sid, []).append((d_val, ticker))
                    
                # Fetch SPY prices
                spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
                spy_df = None
                if spy_sym_id:
                    spy_prices = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()
                    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in spy_prices])
                    
                indicator_cols = [c.name for c in Indicator.__table__.columns if c.name not in ('id', 'symbol_id', 'date')]
                
                fixed_count = 0
                for sid, dates_info in mismatches_by_symbol.items():
                    ticker = dates_info[0][1]
                    missing_dates = {d for d, _ in dates_info}
                    
                    prices_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == sid).order_by(DailyPrice.date).all()
                    if not prices_all:
                        continue
                    df_price = pd.DataFrame([{
                        "date": p.date,
                        "open": p.open,
                        "high": p.high,
                        "low": p.low,
                        "close": p.close,
                        "volume": p.volume
                    } for p in prices_all])
                    
                    try:
                        df_ind = calculate_indicators(df_price, spy_df if ticker != "SPY" else None)
                        if df_ind.empty:
                            continue
                            
                        pending_inserts = []
                        for _, row in df_ind.iterrows():
                            row_date = row["date"]
                            if isinstance(row_date, str):
                                row_date = pd.to_datetime(row_date).date()
                            if row_date in missing_dates:
                                kwargs = {'symbol_id': sid, 'date': row_date}
                                for col in indicator_cols:
                                    val = row.get(col)
                                    if col in ('td9', 'trend_template_ok', 'rs_blue_dot', 'rs_red_dot'):
                                        kwargs[col] = int(val) if val is not None and not pd.isna(val) else None
                                    else:
                                        kwargs[col] = float(val) if val is not None and not pd.isna(val) else None
                                pending_inserts.append(Indicator(**kwargs))
                                
                        if pending_inserts:
                            db.bulk_save_objects(pending_inserts)
                            db.commit()
                            fixed_count += len(pending_inserts)
                    except Exception as calc_err:
                        logger.error(f"[{ticker}] Self-healing calculations failed: {calc_err}")
                        
                report["fixed_indicators_count"] = fixed_count
                
    # 5. Stock split / consolidation anomaly detection (last 730 days)
    if active_symbol_ids and max_date:
        lookback_cutoff = max_date - timedelta(days=730)
        for sid in active_symbol_ids:
            ticker = db.query(Symbol.ticker).filter(Symbol.id == sid).scalar()
            prices = db.query(DailyPrice.date, DailyPrice.close)\
                .filter(DailyPrice.symbol_id == sid)\
                .filter(DailyPrice.date >= lookback_cutoff)\
                .order_by(DailyPrice.date).all()
                
            if len(prices) < 2:
                continue
                
            for i in range(1, len(prices)):
                prev_date, prev_close = prices[i-1]
                curr_date, curr_close = prices[i]
                
                if prev_close and prev_close > 0 and curr_close and curr_close > 0:
                    ratio = curr_close / prev_close
                    if ratio <= 0.61 or ratio >= 1.79:
                        report["split_anomalies"].append({
                            "ticker": ticker,
                            "date": curr_date,
                            "prev_close": prev_close,
                            "curr_close": curr_close,
                            "ratio": ratio
                        })
                
    return report

def resolve_report_dir(db_path: str | None) -> str:
    """レポート出力先を「監査対象 DB と同じ階層」に解決する。

    出力先を `data/maintenance_reports/` 固定にしていたため、テストが
    `--db-path <一時DB>` で起動したサブプロセスの監査結果（空 DB なので全項目ゼロ）が
    **本番のレポートと退役候補 CSV を上書き・削除していた**（2026-07-29 発見）。
    対象 DB に紐付けることで、本番実行だけが本番のレポートを書くようにする。
    """
    if db_path:
        return os.path.join(os.path.dirname(os.path.abspath(db_path)), "maintenance_reports")
    return os.path.join(project_root, "data", "maintenance_reports")


def write_maintenance_report(report: dict, dry_run: bool, db_path: str | None = None):
    """
    Writes weekly audit reports and delisting recommendations next to the audited DB.
    """
    report_dir = resolve_report_dir(db_path)
    os.makedirs(report_dir, exist_ok=True)
    
    # 1. Audit Text Report
    report_file = os.path.join(report_dir, "weekly_maintenance_report.txt")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("==================================================\n")
        f.write(f"WEEKLY DATA AUDIT REPORT (Mode: {'Dry-Run' if dry_run else 'Fix'})\n")
        f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("==================================================\n\n")
        
        f.write("1. Symbol freshness audit (classified by history depth + last date):\n\n")

        f.write("  1-a. DELISTED - sufficient history but supply stopped (retire):\n")
        if report.get("delisted_symbols"):
            for ticker, last, rows in report["delisted_symbols"]:
                f.write(f"    - {ticker:<10} rows={rows:<6} last={last}\n")
        else:
            f.write("    None\n")
        f.write("\n")

        f.write("  1-b. NO HISTORY - almost no rows; delisting or ticker rename (retire + investigate):\n")
        if report.get("no_history_symbols"):
            for ticker, last, rows in report["no_history_symbols"]:
                f.write(f"    - {ticker:<10} rows={rows:<6} last={last or '(none)'}\n")
        else:
            f.write("    None\n")
        f.write("\n")

        f.write("  1-c. LAGGING - complete history, just behind by a day or two (self-heals; no action):\n")
        if report.get("lagging_symbols"):
            for ticker, last, rows in report["lagging_symbols"]:
                f.write(f"    - {ticker:<10} rows={rows:<6} last={last}\n")
        else:
            f.write("    None\n")
        f.write("\n")

        # MTS の直接入力は退役対象にならないため専用枠。供給が止まると
        # market_signals 側の ffill が直前値を持ち越し続け、気づきにくい。
        f.write("  1-d. MARKET TREND SCORE INPUTS (never retired; staleness silently ffill'd):\n")
        for ticker, last, rows, lag, kind in report.get("mts_input_status", []):
            if kind == "missing":
                f.write(f"    - {ticker:<10} !! NOT FOUND in symbols table\n")
                continue
            flag = "  <<< STALE" if kind != "ok" else ""
            lag_s = f"{lag}d behind SPY" if lag else "up to date"
            f.write(f"    - {ticker:<10} rows={rows:<6} last={last}  ({lag_s}){flag}\n")
        f.write("\n")

        f.write("2. Active themes with zero constituents:\n")
        if report["empty_themes"]:
            for theme in report["empty_themes"]:
                f.write(f"  - {theme}\n")
        else:
            f.write("  None\n")
        f.write("\n")
        
        f.write("3. Invalid theme constituents pointing to inactive symbols:\n")
        if report["invalid_constituents"]:
            for item in report["invalid_constituents"]:
                action_taken = "Flagged" if dry_run else "Auto-Deleted"
                f.write(f"  - Theme ID {item['theme_id']} -> Inactive Ticker: {item['ticker']} ({item['name']}) [{action_taken}]\n")
        else:
            f.write("  None\n")
        f.write("\n")
        
        f.write("4. Mismatched indicators (T2 price exists but T3 indicator is missing):\n")
        f.write(f"  - Count detected: {report['mismatched_indicators_count']}\n")
        f.write(f"  - Count fixed: {report['fixed_indicators_count']}\n")
        f.write("\n")
        
        f.write("5. Stock split / consolidation anomalies (price shift >= 40% drop or >= 80% spike):\n")
        if report["split_anomalies"]:
            for item in report["split_anomalies"]:
                f.write(f"  - {item['ticker']} on {item['date']}: {item['prev_close']:.2f} -> {item['curr_close']:.2f} (Ratio: {item['ratio']:.2f})\n")
        else:
            f.write("  None\n")
        
    logger.info(f"Audit report saved to: {report_file}")
    
    # 2. Delisting recommendations CSV
    #    T1 のソースが universe.db へ移行したため、除外先は Google スプレッドシートではなく
    #    universe.db の symbols_master.active=0 になる（計画書 W8）。
    #    判定根拠（分類・保有行数・最終日）を含めることで、退役してよいか人間が検証できるようにする。
    csv_file = os.path.join(report_dir, "delisting_recommendations.csv")
    rows_out = [("delisted", t, last, n) for t, last, n in report.get("delisted_symbols", [])]
    rows_out += [("no_history", t, last, n) for t, last, n in report.get("no_history_symbols", [])]

    if rows_out:
        with open(csv_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("ticker,classification,rows,last_date,reason\n")
            for kind, ticker, last, n in rows_out:
                reason = ("Sufficient history but supply stopped"
                          if kind == "delisted"
                          else "Almost no history; delisting or ticker rename")
                f.write(f"{ticker},{kind},{n},{last},{reason}\n")
        logger.info(f"Delisting recommendation list exported to: {csv_file}")
        logger.info(
            f"  delisted={len(report.get('delisted_symbols', []))} / "
            f"no_history={len(report.get('no_history_symbols', []))} / "
            f"lagging={len(report.get('lagging_symbols', []))} (lagging は自己回復するため対象外)"
        )
        logger.info(
            "  → universe.db へ反映するには: "
            "python backend/scripts/retire_stale_symbols.py --from-report"
        )
    elif os.path.exists(csv_file):
        # 前回の候補が解消したのに古い CSV が残っていると誤って退役させる恐れがある
        os.remove(csv_file)
        logger.info("退役候補が解消したため、古い delisting_recommendations.csv を削除しました")

def main():
    parser = argparse.ArgumentParser(description="Stocktool Weekly Maintenance Script.")
    parser.add_argument("--dry-run", action="store_true", help="Perform checks only without modifying databases.")
    parser.add_argument("--fix", action="store_true", help="Perform actual maintenance operations.")
    parser.add_argument("--db-path", type=str, help="Optional custom database path to target.")
    parser.add_argument("--lock-file", type=str, default=None,
                        help="排他ロックのパス（既定は update_pipeline.py と共有）。"
                             "テストや検証実行を本番の実行状況から独立させたいときに指定する。")
    args = parser.parse_args()
    
    if not args.dry_run and not args.fix:
        logger.error("Error: Either --dry-run or --fix must be specified.")
        parser.print_help()
        sys.exit(1)

    if args.lock_file:
        global LOCK_FILE
        LOCK_FILE = os.path.abspath(args.lock_file)
        os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
        logger.info(f"排他ロックを {LOCK_FILE} に切り替えました（本番ロックとは独立）")

    # Check lock
    if not acquire_lock():
        logger.error("Another instance of the update script or maintenance is already running. Exiting.")
        sys.exit(1)
        
    try:
        logger.info("=== Starting Weekly Maintenance Sequence ===")
        
        # Initialize ORM database connection pools (resolves STOCKTOOL_ENV paths)
        init_db(args.db_path or "data/stocktool.db")
        init_user_db("data/user_data.db")
        
        db_path = get_active_db_path()
        user_db_path = get_active_user_db_path()
        
        logger.info(f"Target System DB: {db_path}")
        logger.info(f"Target User DB: {user_db_path}")
            
        # Run physical maintenance for System Database
        if db_path:
            run_physical_maintenance(db_path, "System DB", dry_run=args.dry_run)
            
        # Run physical maintenance for User Database
        if user_db_path:
            run_physical_maintenance(user_db_path, "User DB", dry_run=args.dry_run)
            
        # Run logical integrity audits
        if db_path:
            logger.info("Starting Logical Integrity Audits...")
            with get_write_db() as db:
                report = audit_and_fix_weekly(db, dry_run=args.dry_run)
                
            # Log summary
            logger.info(f"Audit Summary:")
            logger.info(f"  Retirement candidates: {len(report['stale_symbols'])}"
                        f"  (delisted={len(report.get('delisted_symbols', []))},"
                        f" no_history={len(report.get('no_history_symbols', []))})")
            logger.info(f"  Lagging (self-healing, no action): {len(report.get('lagging_symbols', []))}")
            for ticker, last, rows, lag, kind in report.get("mts_input_status", []):
                if kind == "missing":
                    logger.error(f"  MTS input {ticker}: symbols テーブルに存在しません")
                elif kind == "ok":
                    logger.info(f"  MTS input {ticker}: OK (last={last})")
                else:
                    logger.warning(
                        f"  MTS input {ticker}: 供給停止の疑い last={last} "
                        f"({lag}日 SPY より遅延) — market_signals は直前値を持ち越して計算しています"
                    )
            logger.info(f"  Empty themes detected: {len(report['empty_themes'])}")
            logger.info(f"  Invalid constituents detected: {len(report['invalid_constituents'])}")
            logger.info(f"  Mismatched indicators count: {report['mismatched_indicators_count']}")
            logger.info(f"  Stock split anomalies: {len(report['split_anomalies'])}")
            if not args.dry_run:
                logger.info(f"  Fixed indicators count: {report['fixed_indicators_count']}")
                
            # Write text/csv audit reports next to the audited DB
            # （本番以外の DB を監査したときに本番のレポートを壊さないため）
            write_maintenance_report(report, dry_run=args.dry_run, db_path=db_path)
            
        logger.info("=== Weekly Maintenance Sequence Completed Successfully ===")
    except Exception as e:
        logger.error(f"Weekly maintenance failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        release_lock()

if __name__ == "__main__":
    main()
