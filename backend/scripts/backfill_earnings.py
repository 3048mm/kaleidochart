import os
import sys
import logging
import tomli
import pandas as pd
from datetime import datetime
import time
import yfinance as yf

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path: sys.path.append(backend_dir)
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path: sys.path.append(project_root)

from db.database import init_db, get_db
from db.models import Symbol, Earning
from sqlalchemy import text, or_

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def run_backfill():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        config = tomli.load(f)
        
    db_path = config["system"]["db_path"]
    init_db(db_path)
    
    with get_db() as db:
        # Get all non-virtual symbols that don't already have earnings data
        target_tickers = ['AAPL', 'NKE', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'GOOGL', 'META']
        symbols = db.query(Symbol).filter(Symbol.ticker.in_(target_tickers)).all()
        # Find who already has earnings
        processed_ids = [res[0] for res in db.execute(text("SELECT DISTINCT symbol_id FROM earnings")).fetchall()]
        to_process = [s for s in symbols if s.id not in processed_ids]
        
        logger.info(f"Found {len(to_process)} symbols to backfill earnings...")
        
        for i, sym in enumerate(to_process):
            logger.info(f"[{sym.ticker}] ({i+1}/{len(to_process)}) Fetching income stmt directly...")
            try:
                t = yf.Ticker(sym.ticker)
                income_stmt = t.quarterly_income_stmt
                
                if income_stmt is not None and not income_stmt.empty:
                    db.query(Earning).filter(Earning.symbol_id == sym.id).delete()
                    earning_records = []
                    
                    for period_dt in income_stmt.columns:
                        if pd.isna(period_dt): continue
                        col_data = income_stmt[period_dt]
                        
                        def get_val(key):
                            if key in col_data.index and pd.notna(col_data[key]):
                                return float(col_data[key])
                            return None
                            
                        earning_records.append(Earning(
                            symbol_id=sym.id,
                            period_date=period_dt.date() if hasattr(period_dt, 'date') else period_dt,
                            eps_basic=get_val('Basic EPS'),
                            eps_diluted=get_val('Diluted EPS'),
                            revenue=get_val('Total Revenue') or get_val('Operating Revenue'),
                            net_income=get_val('Net Income')
                        ))
                    
                    if earning_records:
                        db.bulk_save_objects(earning_records)
                        db.commit()
                        logger.info(f"[{sym.ticker}] Saved {len(earning_records)} quarterly earnings records.")
                    else:
                        logger.warning(f"[{sym.ticker}] No valid earning keys found.")
                else:
                    logger.warning(f"[{sym.ticker}] No income statement available.")
            except Exception as e:
                logger.error(f"[{sym.ticker}] Error: {e}")
                
            # Increase sleep to avoid Yahoo Finance 429 Too Many Requests
            time.sleep(2.0)

if __name__ == "__main__":
    run_backfill()
