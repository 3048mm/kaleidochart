import os
import sys
import logging
import yfinance as yf
import subprocess
import msvcrt
from datetime import datetime

# Windows encoding safety
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

LOCK_FILE = "update_pipeline.lock"
lock_fd = None

def acquire_lock():
    """Attempt to acquire an exclusive lock on the lock file. Returns True if successful."""
    global lock_fd
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        # LK_NBLCK means non-blocking lock. Will raise IOError if already locked by another process.
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False
        
def release_lock():
    """Release the lock."""
    global lock_fd
    if lock_fd is not None:
        try:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")

def check_if_needs_update():
    from db.database import get_db, init_db
    from db.models import Symbol, DailyPrice
    import tomli
    
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]
    init_db(db_path)
    
    # Get latest date from DB for SPY
    db_latest_date = None
    with get_db() as db:
        spy = db.query(Symbol).filter(Symbol.ticker == "SPY").first()
        if spy:
            latest_db_price = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy.id).order_by(DailyPrice.date.desc()).first()
            if latest_db_price:
                db_latest_date = latest_db_price.date
                
    if not db_latest_date:
        logger.info("SPY or DailyPrices not found in DB. Full update needed.")
        return True
        
    # Get latest date from yfinance for SPY
    logger.info("Fetching latest market date from yfinance (SPY)...")
    try:
        df = yf.download("SPY", period="5d", progress=False)
        if df.empty:
            logger.error("Could not fetch data from yfinance. Assuming no update needed to prevent blank DB.")
            return False
            
        # df.index contains timestamps, get the date of the last row
        yf_latest_date = df.index[-1].date()
        
        logger.info(f"DB latest date: {db_latest_date}, Market latest date: {yf_latest_date}")
        
        # Compare dates (convert to string for clean comparison)
        if str(db_latest_date) == str(yf_latest_date):
            logger.info("DB is already up to date with the market. Exiting.")
            return False
            
    except Exception as e:
        logger.error(f"Error fetching validation data from yfinance: {e}")
        return False
        
    logger.info("New market data available. Proceeding with update.")
    return True

def run_update():
    logger.info("Starting update_pipeline.py calculation...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    
    # Run the main pipeline (ensure it runs in the scripts directory or with absolute path)
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_pipeline.py")
    process = subprocess.run([sys.executable, script_path], env=env)
    
    if process.returncode != 0:
        logger.error(f"update_pipeline.py exited with error code {process.returncode}")
    else:
        logger.info("Database update completed successfully.")

if __name__ == "__main__":
    logger.info("=== Daily Update Boot Sequence ===")
    
    # Check concurrent execution
    if not acquire_lock():
        logger.warning("Another instance of the update script is already running. Exiting.")
        sys.exit(0)
        
    try:
        # Check if we actually have new data to download
        if not check_if_needs_update():
            sys.exit(0)
            
        # Block and run the update
        run_update()
    finally:
        # Guarantee lock release even if crashes
        release_lock()
