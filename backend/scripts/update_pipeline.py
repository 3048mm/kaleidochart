import os
import sys
import tomllib
import argparse
import traceback
import msvcrt

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import get_active_db_path
import logging
from logging.handlers import RotatingFileHandler

import multiprocessing

def setup_pipeline_logging():
    log_dir = os.path.join(project_root, "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "pipeline.log")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(log_format)
    root_logger.addHandler(console_h)
    
    # Only use RotatingFileHandler in the MainProcess to avoid PermissionError on Windows
    if multiprocessing.current_process().name == 'MainProcess':
        file_h = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_h.setFormatter(log_format)
        root_logger.addHandler(file_h)
    
    return logging.getLogger(__name__)

logger = setup_pipeline_logging()

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

LOCK_FILE = os.path.join(project_root, "update_pipeline.lock")
lock_fd = None

def acquire_lock():
    global lock_fd
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
        except:
            pass

if __name__ == "__main__":
    # Check for concurrent execution
    if not acquire_lock():
        print("Another instance of the update script is already running. Exiting.")
        sys.exit(0)

    try:
        parser = argparse.ArgumentParser(description="Run the Step 3 Data Pipeline.")
        parser.add_argument("--rebuild-from", type=str, help="Rebuild from a specific table (T2, T3, T4, T5).")
        parser.add_argument("--re-calculate", action="store_true", help="Redownload all data and recalculate all indicators.")
        parser.add_argument("--category", type=str, help="Comma-separated categories to process.")
        parser.add_argument("--skip-fetch", action="store_true", help="Skip yfinance price fetching.")
        parser.add_argument("--skip-sync", action="store_true", help="Skip Google Spreadsheet sync.")
        parser.add_argument("--skip-t3", action="store_true", help="Skip T3 indicator calculation.")
        args = parser.parse_args()
        selected_categories = [c.strip() for c in args.category.split(",")] if args.category else None

        # Load configuration
        config = load_config()
        
        # Apply logging level from config.toml dynamically
        log_level_str = config.get("system", {}).get("log_level", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        logging.getLogger().setLevel(log_level)
        
        db_path = get_active_db_path() or config["system"]["db_path"]

        from pipeline.orchestrator import run_pipeline

        run_pipeline(
            config=config,
            db_path=db_path,
            logger=logger,
            rebuild_from=args.rebuild_from,
            categories=selected_categories,
            skip_fetch=args.skip_fetch,
            skip_sync=args.skip_sync,
            skip_t3=args.skip_t3,
            recalculate_all=args.re_calculate
        )
    except Exception as e:
        logger.error(f"Failed to execute pipeline: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        release_lock()
