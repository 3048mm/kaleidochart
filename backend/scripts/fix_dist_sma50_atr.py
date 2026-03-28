import sqlite3
import os
import sys
import time
import logging

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(backend_dir), "data", "stocktool.db")

def fix_dist_sma50_atr():
    logger.info(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    t0 = time.time()
    
    try:
        logger.info("Updating dist_sma50_atr using join-update strategy...")
        
        # Use a subquery with join to indicators and daily_prices
        # For SQLite, the most performant way without UPDATE FROM (for compatibility) 
        # is often a subquery if indices are good.
        
        sql = """
        UPDATE indicators
        SET dist_sma50_atr = (
            SELECT ((p.close / indicators.sma_50 * 100) - 100) / indicators.atr_pct_14
            FROM daily_prices p
            WHERE p.symbol_id = indicators.symbol_id AND p.date = indicators.date
        )
        WHERE indicators.atr_pct_14 IS NOT NULL AND indicators.atr_pct_14 != 0 
          AND indicators.sma_50 IS NOT NULL AND indicators.sma_50 != 0;
        """
        
        logger.info("Executing UPDATE SQL...")
        cursor.execute(sql)
        row_count = cursor.rowcount
        conn.commit()
        
        elapsed = time.time() - t0
        logger.info(f"Successfully updated {row_count} rows in {elapsed:.1f} seconds.")
        
    except Exception as e:
        logger.error(f"Failed to update: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_dist_sma50_atr()
