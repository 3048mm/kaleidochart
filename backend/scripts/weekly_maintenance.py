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
LOCK_FILE = os.path.join(project_root, "update_pipeline.lock")
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
        "stale_symbols": [],
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
            # 5 trading days delta threshold (approx 7 calendar days)
            stale_threshold_date = spy_latest_date - timedelta(days=7)
            
            sub = db.query(
                DailyPrice.symbol_id,
                func.max(DailyPrice.date).label("max_date")
            ).group_by(DailyPrice.symbol_id).subquery()
            
            stale_active = db.query(Symbol.ticker, sub.c.max_date)\
                .join(sub, Symbol.id == sub.c.symbol_id)\
                .filter(Symbol.active == 1, Symbol.ticker != "SPY")\
                .filter(sub.c.max_date < stale_threshold_date).all()
                
            report["stale_symbols"] = [row[0] for row in stale_active]
            
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

def write_maintenance_report(report: dict, dry_run: bool):
    """
    Writes weekly audit reports and Sheets-delisting recommendations to data/maintenance_reports/
    """
    report_dir = os.path.join(project_root, "data", "maintenance_reports")
    os.makedirs(report_dir, exist_ok=True)
    
    # 1. Audit Text Report
    report_file = os.path.join(report_dir, "weekly_maintenance_report.txt")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("==================================================\n")
        f.write(f"WEEKLY DATA AUDIT REPORT (Mode: {'Dry-Run' if dry_run else 'Fix'})\n")
        f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("==================================================\n\n")
        
        f.write("1. Delisted/Stale active symbols (no updates for 5+ days relative to SPY):\n")
        if report["stale_symbols"]:
            for sym in report["stale_symbols"]:
                f.write(f"  - {sym}\n")
        else:
            f.write("  None\n")
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
    
    # 2. Sheets Delisting recommendations CSV
    if report["stale_symbols"]:
        csv_file = os.path.join(report_dir, "sheets_delisting_recommendations.csv")
        with open(csv_file, "w", encoding="utf-8") as f:
            f.write("ticker,reason\n")
            for sym in report["stale_symbols"]:
                f.write(f"{sym},Stale data (Not updated for 5+ days relative to SPY)\n")
        logger.info(f"Sheets delisting recommendation list exported to: {csv_file}")

def main():
    parser = argparse.ArgumentParser(description="Stocktool Weekly Maintenance Script.")
    parser.add_argument("--dry-run", action="store_true", help="Perform checks only without modifying databases.")
    parser.add_argument("--fix", action="store_true", help="Perform actual maintenance operations.")
    parser.add_argument("--db-path", type=str, help="Optional custom database path to target.")
    args = parser.parse_args()
    
    if not args.dry_run and not args.fix:
        logger.error("Error: Either --dry-run or --fix must be specified.")
        parser.print_help()
        sys.exit(1)
        
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
            logger.info(f"  Stale symbols detected: {len(report['stale_symbols'])}")
            logger.info(f"  Empty themes detected: {len(report['empty_themes'])}")
            logger.info(f"  Invalid constituents detected: {len(report['invalid_constituents'])}")
            logger.info(f"  Mismatched indicators count: {report['mismatched_indicators_count']}")
            logger.info(f"  Stock split anomalies: {len(report['split_anomalies'])}")
            if not args.dry_run:
                logger.info(f"  Fixed indicators count: {report['fixed_indicators_count']}")
                
            # Write text/csv audit reports to data/
            write_maintenance_report(report, dry_run=args.dry_run)
            
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
