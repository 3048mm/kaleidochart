import os
import sys
import tomllib
import argparse
import traceback

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
    file_h = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_h.setFormatter(log_format)
    root_logger.addHandler(file_h)
    return logging.getLogger(__name__)

logger = setup_pipeline_logging()

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

if __name__ == "__main__":
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
    db_path = get_active_db_path() or config["system"]["db_path"]

    from pipeline.orchestrator import run_pipeline

    try:
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
        logger.error(traceback.format_exc()); raise e
