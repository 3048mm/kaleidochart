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

def fix_dist_sma50_atr_batch():
    logger.info(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 全シンボルを取得
    cursor.execute("SELECT id, ticker FROM symbols WHERE active = 1")
    symbols = cursor.fetchall()
    total = len(symbols)
    logger.info(f"Found {total} symbols to update.")
    
    t0 = time.time()
    count = 0
    
    for i, (sym_id, ticker) in enumerate(symbols):
        try:
            # 銘柄ごとに T3 と T2 を JOIN して計算・更新
            # ((p.close / indicators.sma_50 * 100) - 100) / indicators.atr_pct_14
            sql = """
            UPDATE indicators
            SET dist_sma50_atr = (
                SELECT ((p.close / indicators.sma_50 * 100) - 100) / indicators.atr_pct_14
                FROM daily_prices p
                WHERE p.symbol_id = ? AND p.date = indicators.date
            )
            WHERE indicators.symbol_id = ? 
              AND indicators.atr_pct_14 IS NOT NULL AND indicators.atr_pct_14 != 0 
              AND indicators.sma_50 IS NOT NULL AND indicators.sma_50 != 0;
            """
            cursor.execute(sql, (sym_id, sym_id))
            
            count += 1
            if count % 100 == 0:
                conn.commit()
                elapsed = time.time() - t0
                logger.info(f"[{count}/{total}] Symbols updated. {elapsed:.1f}s elapsed.")
                
        except Exception as e:
            logger.error(f"Failed to update {ticker} (id:{sym_id}): {e}")
            conn.rollback()
            
    conn.commit()
    total_elapsed = time.time() - t0
    logger.info(f"Finished. Total {count} symbols updated in {total_elapsed:.1f} seconds.")
    conn.close()

if __name__ == "__main__":
    fix_dist_sma50_atr_batch()
