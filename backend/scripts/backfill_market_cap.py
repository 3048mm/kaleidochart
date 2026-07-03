import os
import sys
import logging
import yfinance as yf
from datetime import datetime
import time

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.database import init_db, get_write_db
from db.models import Symbol, Indicator

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def backfill_market_cap():
    db_path = "data/stocktool.db"
    init_db(db_path)
    
    with get_write_db() as db:
        # Get active real symbols
        symbols = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.theme_type.is_(None)
        ).all()
        
        logger.info(f"Found {len(symbols)} active symbols to check.")
        
        count = 0
        skipped = 0
        updated = 0
        failed = 0
        
        for i, sym in enumerate(symbols):
            # Check if this symbol needs update
            # (If it has ANY row with NULL market_cap, we'll update it)
            has_null = db.query(Indicator).filter(
                Indicator.symbol_id == sym.id,
                Indicator.market_cap.is_(None)
            ).first()
            
            if not has_null:
                skipped += 1
                continue
                
            # Fetch from yfinance
            try:
                logger.info(f"[{i+1}/{len(symbols)}] Fetching marketCap for {sym.ticker}...")
                t = yf.Ticker(sym.ticker)
                # yfinance's info call is slow, use a reasonable timeout if possible 
                # (Ticker doesn't have a direct timeout for info, but handled by requests inside)
                mcap = t.info.get("marketCap")
                
                if mcap:
                    # Update all NULL rows for this symbol at once with the latest value
                    db.query(Indicator).filter(
                        Indicator.symbol_id == sym.id,
                        Indicator.market_cap.is_(None)
                    ).update({"market_cap": float(mcap)}, synchronize_session=False)
                    
                    updated += 1
                    count += 1
                    if count % 10 == 0:
                        db.commit()
                        logger.info(f"Committed {count} symbols (Total updated so far: {updated}).")
                else:
                    logger.warning(f"No marketCap found for {sym.ticker}")
                    # Even if no mcap found, we don't want to retry every time if it's consistently missing?
                    # For now, let's just let it stay NULL to retry next time if needed.
                    failed += 1
                
            except Exception as e:
                logger.error(f"Failed to update {sym.ticker}: {e}")
                failed += 1
            
            # Rate limiting: 0.3s is generally fine for info calls
            time.sleep(0.3)
            
        db.commit()
        logger.info(f"Finished. Updated: {updated}, Skipped: {skipped}, Failed/Missing: {failed}, Total: {len(symbols)}")

if __name__ == "__main__":
    backfill_market_cap()
