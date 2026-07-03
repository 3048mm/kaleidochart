import os
import sys
import logging

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.database import init_db, get_write_db
from data_collection.txt_sync import sync_symbols_from_txt

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def refresh():
    db_path = "data/stocktool.db"
    init_db(db_path)
    
    with get_write_db() as db:
        logger.info("Starting refresh of symbols and constituents...")
        sync_symbols_from_txt(db)
        logger.info("Done.")

if __name__ == "__main__":
    refresh()
