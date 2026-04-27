import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy import func

from db.models import DailyPrice
from data_collection.fetcher import fetch_daily_data
from pipeline.utils import sanitize_numeric, attach_market_cap

def sync_phase_t2_prices(db, sheet_data: List[Dict], symbol_id_map: Dict, initial_fetch_days: int, skip_fetch: bool, logger: logging.Logger) -> Optional[datetime]:
    """Phase 2: Prices (T2) - Sync prices from yfinance led by SPY."""
    logger.info("--- Phase 2: Price data sync START ---")
    
    spy_item = next((d for d in sheet_data if d['ticker'] == 'SPY'), None)
    if not spy_item:
        logger.error("SPY not found in sheet_data.")
        return None
        
    spy_sym_id = symbol_id_map.get(("SPY", spy_item['exchange']))
    spy_max_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
    
    default_start = (datetime.now() - timedelta(days=initial_fetch_days)).strftime('%Y-%m-%d')
    spy_fetch_start = default_start
    if spy_max_date:
        spy_fetch_start = (datetime.combine(spy_max_date, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
        
    if not skip_fetch:
        logger.info(f"Updating SPY from {spy_fetch_start}...")
        spy_df = fetch_daily_data("SPY", spy_fetch_start)
        if spy_df is not None and not spy_df.empty:
            spy_df = attach_market_cap("SPY", spy_df, logger)
            existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == spy_sym_id).all()}
            new_recs = [
                DailyPrice(
                    symbol_id=spy_sym_id, 
                    date=row['date'], 
                    open=sanitize_numeric(row, 'open'), 
                    high=sanitize_numeric(row, 'high'), 
                    low=sanitize_numeric(row, 'low'), 
                    close=sanitize_numeric(row, 'close'), 
                    volume=int(row['volume']) if sanitize_numeric(row, 'volume') is not None else 0,
                    market_cap=sanitize_numeric(row, 'market_cap')
                ) for _, row in spy_df.iterrows() if row['date'] not in existing_dates
            ]
            if new_recs:
                db.bulk_save_objects(new_recs)
                db.commit()
                logger.info(f"SPY updated: +{len(new_recs)} rows.")
            else:
                logger.info("SPY: No new rows to add.")
                
    spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
    logger.info(f"Target Reference Date (SPY): {spy_latest_date}")
    if not spy_latest_date: return None
    
    real_items = [d for d in sheet_data if d['theme_type'] != 'virtual' and d['ticker'] != 'SPY']
    latest_rows = db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()
    sym_latest_map = {sid: ldt for sid, ldt in latest_rows}
    
    update_needed = []
    for it in real_items:
        sid = symbol_id_map.get((it['ticker'], it['exchange']))
        current_max = sym_latest_map.get(sid)
        if not current_max or current_max < spy_latest_date:
            update_needed.append((it['ticker'], sid, current_max))
            
    logger.info(f"Found {len(update_needed)} tickers needing price update.")
    total_needed = len(update_needed)
    
    if not skip_fetch and update_needed:
        for i, (ticker, sid, current_max) in enumerate(update_needed):
            f_start = default_start
            if current_max: f_start = (datetime.combine(current_max, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
            
            progress_str = f"[{i+1}/{total_needed}]"
            df = fetch_daily_data(ticker, f_start, progress=progress_str)
            if df is not None and not df.empty:
                df = attach_market_cap(ticker, df, logger)
                existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == sid).all()}
                new_recs = [
                    DailyPrice(
                        symbol_id=sid, 
                        date=row['date'], 
                        open=sanitize_numeric(row, 'open'), 
                        high=sanitize_numeric(row, 'high'), 
                        low=sanitize_numeric(row, 'low'), 
                        close=sanitize_numeric(row, 'close'), 
                        volume=int(row['volume']) if sanitize_numeric(row, 'volume') is not None else 0,
                        market_cap=sanitize_numeric(row, 'market_cap')
                    ) for _, row in df.iterrows() if row['date'] not in existing_dates
                ]
                if new_recs:
                    db.bulk_save_objects(new_recs)
                    db.commit()
                    logger.debug(f"[{ticker}] Updated +{len(new_recs)} rows.")
                    
    return spy_latest_date
