import time
import logging
import multiprocessing
from typing import Dict, List, Optional
from datetime import date
from sqlalchemy import func
import pandas as pd

from pipeline.utils import sanitize_numeric
from db.database import init_db, get_db

def _calculate_t3_worker(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virtual=False, spy_latest_date: Optional[date] = None):
    """Worker function to calculate T3 for a single ticker in a separate process using direct sqlite3 connection (fast, no ORM)."""
    try:
        import sqlite3
        import pandas as pd
        from datetime import date
        from indicators.calculate import calculate_indicators
        
        conn = sqlite3.connect(db_path, timeout=60.0)
        try:
            query = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE symbol_id = ? ORDER BY date"
            df_price = pd.read_sql_query(query, conn, params=(sid,))
        finally:
            conn.close()
            
        if df_price.empty:
            return ticker, sid, []
            
        # sqlite3 returns date as string, parse to date object
        df_price['date'] = pd.to_datetime(df_price['date']).dt.date
        
        df_ind = calculate_indicators(df_price, spy_df if ticker != "SPY" else None)
        
        if spy_latest_date:
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1))) & (df_ind['date'] <= spy_latest_date)]
        else:
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1)))]
            
        if delta_df.empty:
            return ticker, sid, []
            
        return ticker, sid, delta_df.to_dict('records')
        
    except Exception as e:
        return ticker, sid, e

def _calculate_t3_worker_wrapper(args):
    """Wrapper function to unpack arguments for multiprocessing Pool."""
    return _calculate_t3_worker(*args)

def sync_phase_t3_indicators(db, sheet_data: List[Dict], symbol_id_map: Dict, spy_latest_date: Optional[date], skip_fetch: bool, db_path: str, logger: logging.Logger):
    """Phase 3: Indicators (T3) - Per-ticker catch-up using T2 price data with Parallel Processing."""
    logger.info("--- Phase 3: Indicator calculation START (Parallel) ---")
    if not spy_latest_date: return
    
    from db.models import Symbol, DailyPrice, Indicator

    spy_sym_id = symbol_id_map.get(("SPY", "NYSE" if ("SPY", "NYSE") in symbol_id_map else "AMEX")) or db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    spy_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()
    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in spy_all])
    
    p_max_map = {sid: mdt for sid, mdt in db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()}
    i_max_map = {sid: mdt for sid, mdt in db.query(Indicator.symbol_id, func.max(Indicator.date)).group_by(Indicator.symbol_id).all()}
    
    tasks_map = {}
    for item in sheet_data:
        ticker, sid = item['ticker'], symbol_id_map.get((item['ticker'], item['exchange']))
        if not sid: continue
        if sid in tasks_map: continue
        
        t2_max, t3_max = p_max_map.get(sid), i_max_map.get(sid)
        if t2_max and (not t3_max or t3_max < t2_max):
            is_virt = (item.get('exchange') == 'VIRTUAL')
            tasks_map[sid] = (sid, ticker, t3_max, is_virt)
    
    tasks = list(tasks_map.values())
    
    if not tasks:
        logger.info("Phase 3: No tickers need indicator update.")
        return

    num_workers = min(4, multiprocessing.cpu_count() // 2)
    if num_workers < 1: num_workers = 1
    logger.info(f"Phase 3: Spawning {num_workers} parallel workers for {len(tasks)} tickers.")
    
    # Prepare arguments for multiprocessing
    pool_args = [(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virt, spy_latest_date) for sid, ticker, t3_max, is_virt in tasks]
    
    update_count = 0
    completed = 0
    indicator_cols = [c.name for c in Indicator.__table__.columns if c.name not in ('id', 'symbol_id', 'date')]
    
    # Use multiprocessing Pool to run in parallel
    pending_recs = []
    chunk_size = 50  # Write to DB every 50 tickers to minimize commit/fsync overhead
    
    with multiprocessing.Pool(processes=num_workers) as pool:
        results = pool.imap_unordered(_calculate_t3_worker_wrapper, pool_args)
        
        for res_ticker, res_sid, records in results:
            try:
                if isinstance(records, Exception):
                    logger.error(f"[{res_ticker}] Worker exception: {records}")
                    continue
                    
                if records:
                    for row in records:
                        kwargs = {'symbol_id': res_sid, 'date': row['date']}
                        for col in indicator_cols:
                            val = row.get(col)
                            if col in ('td9', 'trend_template_ok', 'rs_blue_dot_age', 'rs_red_dot_age'):
                                kwargs[col] = int(val) if val is not None else None
                            else:
                                kwargs[col] = val
                        pending_recs.append(Indicator(**kwargs))
                    update_count += 1
                
                completed += 1
                if completed % chunk_size == 0:
                    if pending_recs:
                        db.bulk_save_objects(pending_recs)
                        db.commit()
                        db.expunge_all()  # Clear SQLAlchemy identity map to free memory
                        pending_recs.clear()
                    logger.info(f"Phase 3 Progress: {completed}/{len(tasks)}")
                    
            except Exception as e:
                logger.error(f"[{res_ticker}] Parent db insert exception: {str(e)}")
                db.rollback()
                pending_recs.clear()
                
        # Commit any remaining records
        if pending_recs:
            try:
                db.bulk_save_objects(pending_recs)
                db.commit()
                db.expunge_all()
                pending_recs.clear()
            except Exception as e:
                logger.error(f"Failed to commit final batch: {e}")
                db.rollback()
                
    logger.info(f"Phase 3 COMPLETE: Updated {update_count} tickers.")
