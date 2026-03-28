import sqlite3
import pandas as pd
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

DB_PATH = r'd:\My Documents\Programing\stocktool\data\stocktool.db'
BACKUP_PATH = r'd:\My Documents\Programing\stocktool\data\market_cap_backup.csv'

def restore_market_cap():
    if not os.path.exists(BACKUP_PATH):
        logger.error(f"Backup file not found: {BACKUP_PATH}")
        return

    logger.info(f"Loading backup from {BACKUP_PATH}...")
    df = pd.read_csv(BACKUP_PATH)
    logger.info(f"Loaded {len(df)} records.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Step-by-step update to avoid SQL parameter limits or performance issues
    # We'll use a temporary table for the join update
    try:
        logger.info("Creating temporary table for restore...")
        cursor.execute("DROP TABLE IF EXISTS market_cap_restore")
        cursor.execute("CREATE TABLE market_cap_restore (symbol_id INTEGER, date TEXT, market_cap REAL)")
        
        # Batch insert into temp table
        logger.info("Inserting indices into temp table...")
        df.to_sql('market_cap_restore', conn, if_exists='replace', index=False)
        
        # Create index on temp table
        cursor.execute("CREATE INDEX idx_restore ON market_cap_restore(symbol_id, date)")
        
        logger.info("Updating indicators table with backed up market_cap...")
        # Since SQLite 3.33+ supports UPDATE FROM, but we want to be safe with subquery if not.
        # SQLite UPDATE FROM syntax:
        # UPDATE indicators SET market_cap = r.market_cap 
        # FROM market_cap_restore r 
        # WHERE indicators.symbol_id = r.symbol_id AND indicators.date = r.date;
        
        sql = """
        UPDATE indicators
        SET market_cap = (
            SELECT r.market_cap 
            FROM market_cap_restore r 
            WHERE r.symbol_id = indicators.symbol_id AND r.date = indicators.date
        )
        WHERE EXISTS (
            SELECT 1 FROM market_cap_restore r 
            WHERE r.symbol_id = indicators.symbol_id AND r.date = indicators.date
        );
        """
        t0 = time.time()
        cursor.execute(sql)
        row_count = cursor.rowcount
        conn.commit()
        
        elapsed = time.time() - t0
        logger.info(f"Successfully restored {row_count} market_cap entries in {elapsed:.1f} seconds.")

    except Exception as e:
        logger.error(f"Failed to restore: {e}")
        conn.rollback()
    finally:
        cursor.execute("DROP TABLE IF EXISTS market_cap_restore")
        conn.close()

if __name__ == "__main__":
    restore_market_cap()
