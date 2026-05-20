import time
import logging
import multiprocessing
from typing import Dict, List, Optional
from datetime import date
from sqlalchemy import func
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd

from pipeline.utils import sanitize_numeric
from db.database import init_db, get_db

def _calculate_t3_worker(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virtual=False):
    """Worker function to calculate T3 for a single ticker in a separate process."""
    try:
        from db.database import init_db, get_db
        from db.models import DailyPrice, Indicator
        import pandas as pd
        from datetime import date
        from indicators.calculate import calculate_indicators
        
        init_db(db_path)
        start_t = time.time()
        with get_db() as db:
            fp_start = time.time()
            prices_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == sid).order_by(DailyPrice.date).all()
            fp_end = time.time()
            if not prices_all:
                return ticker, sid, []
                
            df_price = pd.DataFrame([{'date': p.date, 'open': p.open, 'high': p.high, 'low': p.low, 'close': p.close, 'volume': p.volume} for p in prices_all])
            
            calc_start = time.time()
            df_ind = calculate_indicators(df_price, spy_df if ticker != "SPY" else None)
            calc_end = time.time()
            
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1)))]
            
            if delta_df.empty:
                return ticker, sid, []
            
            return ticker, sid, delta_df.to_dict('records')
            
    except Exception as e:
        return ticker, sid, e

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
    logger.info(f"Phase 3: Spawning {num_workers} parallel workers for {len(tasks)} tickers.")
    
    update_count = 0
    # Run sequentially to avoid Windows multiprocessing hangs
    completed = 0
    indicator_cols = [c.name for c in Indicator.__table__.columns if c.name not in ('id', 'symbol_id', 'date')]
    for sid, ticker, t3_max, is_virt in tasks:
        try:
            res_ticker, res_sid, records = _calculate_t3_worker(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virt)
            if isinstance(records, Exception):
                logger.error(f"[{ticker}] Worker exception: {records}")
                continue
                
            if records:
                t3_recs = []
                for row in records:
                    kwargs = {'symbol_id': sid, 'date': row['date']}
                    for col in indicator_cols:
                        val = row.get(col)
                        if col in ('td9', 'trend_template_ok', 'rs_blue_dot', 'rs_red_dot'):
                            kwargs[col] = int(val) if val is not None else None
                        else:
                            kwargs[col] = val
                    t3_recs.append(Indicator(**kwargs))
                    
                db.bulk_save_objects(t3_recs)
                db.commit()
                update_count += 1
            
            completed += 1
            if completed % 10 == 0:
                logger.info(f"Phase 3 Progress: {completed}/{len(tasks)}")
                
        except Exception as e:
            logger.error(f"[{ticker}] Worker exception: {str(e)}")
            db.rollback()
    logger.info(f"Phase 3 COMPLETE: Updated {update_count} tickers.")
