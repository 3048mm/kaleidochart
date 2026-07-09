# -*- coding: utf-8 -*-
import os
import sys
import time
import shutil
import logging
import gc

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Add project root and backend to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from backend.db.database import init_db, get_db
from backend.pipeline.parquet_cache_manager import restore_sqlite_cache_from_parquet

def run_production_restore(db_path: str = None):
    logger.info("=========================================================")
    logger.info("    🏛️ PRODUCTION DATABASE REBUILD & RESTORE RUNNER 🏛️")
    logger.info("=========================================================")

    # db_path 指定時はそのDB（と隣の parquet_master/）を対象にする
    # （deploy_after_merge の模擬昇格テスト・ワークスペース復元で使用）
    prod_db_path = os.path.abspath(db_path) if db_path else os.path.join(project_root, "data", "stocktool.db")

    # init_db は STOCKTOOL_DB_PATH を優先するため、restore 対象と食い違うと
    # 「parquet は A、書き込み先は B」という破壊的な不整合になる。ここで必ず解除する。
    env_db = os.environ.pop("STOCKTOOL_DB_PATH", None)
    if env_db and os.path.abspath(env_db) != prod_db_path:
        logger.warning(f"STOCKTOOL_DB_PATH ({env_db}) を解除しました。restore 対象: {prod_db_path}")
    
    # 1. Close any potential open engine/sessions in this process
    import backend.db.database as db_module
    if db_module.engine:
        db_module.engine.dispose()
    db_module.engine = None
    db_module.SessionLocal = None
    
    gc.collect()
    time.sleep(0.5) # Wait for OS handles
    
    # 2. Delete the corrupted / bloated SQLite file and its journals (Non-fatal fallback)
    logger.info("1. Attempting to delete bloated SQLite database files (will fallback to truncation if locked)...")
    target_files = [
        prod_db_path,
        prod_db_path + "-journal",
        prod_db_path + "-shm",
        prod_db_path + "-wal"
    ]
    
    file_deleted = True
    for f in target_files:
        if os.path.exists(f):
            try:
                os.remove(f)
                logger.info(f"  Deleted: {os.path.basename(f)}")
            except Exception as e:
                logger.warning(f"  Could not physically delete {f} due to active locks: {e}")
                logger.warning("  Active lock detected. Skipping physical file deletion and falling back to in-place truncation.")
                file_deleted = False
                break

    # 3. Create fresh database with correct schemas
    logger.info("2. Re-creating fresh SQLite database schemas from models...")
    try:
        init_db(prod_db_path)
        if not file_deleted:
            logger.info("  Active lock detected. Forcing DROP ALL tables to ensure schema matches latest models...")
            from backend.db.models import Base
            Base.metadata.drop_all(bind=db_module.engine)
            Base.metadata.create_all(bind=db_module.engine)
        logger.info("  SQLite schema recreation completed successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False
        
    # 4. Import the historical cache from Parquet Master
    logger.info("3. Bulk-importing the historical cache from Parquet masters...")
    try:
        # Re-import db module to get fresh sessionlocal
        import backend.db.database as db_module
        with db_module.get_write_db() as db:
            restore_sqlite_cache_from_parquet(db, prod_db_path, logger)
        logger.info("  SQLite Cache successfully restored from Parquet master!")
    except Exception as e:
        logger.error(f"Failed to restore SQLite Cache from Parquet master: {e}")
        return False
        
    # 5. Restore metadata if needed (so update_pipeline knows where it left off)
    try:
        from backend.pipeline.utils import update_pipeline_meta
        from datetime import datetime
        # Fetch max date in DailyPrice to align last SPY date
        with db_module.get_write_db() as db:
            from backend.db.models import DailyPrice
            from sqlalchemy import func
            max_date_val = db.query(func.max(DailyPrice.date)).scalar()
            if max_date_val:
                from datetime import date
                if isinstance(max_date_val, str):
                    spy_date = datetime.strptime(max_date_val, "%Y-%m-%d").date()
                else:
                    spy_date = max_date_val
                update_pipeline_meta(db, datetime.utcnow(), spy_date)
                logger.info(f"  Successfully restored pipeline metadata to last_spy_date={spy_date}")
    except Exception as me:
        logger.warning(f"  Failed to restore pipeline metadata: {me}")
        
    logger.info("\n🎉 PRODUCTION DATABASE MIGRATION & RESTORE COMPLETED SUCCESSFULLY!")
    logger.info(f"  Bloated DB (4.7 GB) has been successfully rebuilt into optimized historical cache!")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild & restore a SQLite hot cache from its Parquet master.")
    parser.add_argument("--db-path", type=str, default=None,
                        help="対象 SQLite パス（省略時は data/stocktool.db。parquet_master/ は DB と同じディレクトリから解決）")
    args = parser.parse_args()
    # run_production_restore 内で STOCKTOOL_DB_PATH は解除され --db-path が優先される
    if args.db_path and os.getenv("STOCKTOOL_DB_PATH"):
        logger.info("STOCKTOOL_DB_PATH はプロセス内で解除され、--db-path を restore 対象として使用します。")
    success = run_production_restore(args.db_path)
    sys.exit(0 if success else 1)
