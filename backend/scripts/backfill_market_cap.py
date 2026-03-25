import os
import sys
import logging
import yfinance as yf
from datetime import datetime

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.database import init_db, get_db
from db.models import Symbol, Indicator

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def backfill_market_cap():
    db_path = "data/stocktool.db"
    init_db(db_path)
    
    with get_db() as db:
        # Get active real symbols
        symbols = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.theme_type.is_(None)
        ).all()
        
        logger.info(f"Found {len(symbols)} active symbols to check.")
        
        count = 0
        for i, sym in enumerate(symbols):
            # Check if latest indicator has market_cap
            latest_ind = db.query(Indicator).filter(
                Indicator.symbol_id == sym.id
            ).order_by(Indicator.date.desc()).first()
            
            if not latest_ind:
                continue
                
            if latest_ind.market_cap is not None:
                # Already populated
                continue
                
            # Fetch from yfinance
            try:
                logger.info(f"[{i+1}/{len(symbols)}] Fetching marketCap for {sym.ticker}...")
                t = yf.Ticker(sym.ticker)
                mcap = t.info.get("marketCap")
                
                if mcap:
                    # Update all indicators for this symbol that have NULL market_cap?
                    # Or just the latest? Let's do all NULL ones with this static value for now 
                    # as a "best effort" backfill.
                    db.query(Indicator).filter(
                        Indicator.symbol_id == sym.id,
                        Indicator.market_cap.is_(None)
                    ).update({"market_cap": mcap}, synchronize_session=False)
                    
                    count += 1
                    if count % 10 == 0:
                        db.commit()
                        logger.info(f"Committed {count} updates.")
                
            except Exception as e:
                logger.error(f"Failed to update {sym.ticker}: {e}")
            
            # Rate limiting
            import time
            time.sleep(0.5)
            
        db.commit()
        logger.info(f"Finished. Total symbols updated: {count}")

if __name__ == "__main__":
    backfill_market_cap()
