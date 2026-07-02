# -*- coding: utf-8 -*-
import os
import sys
import time
import shutil
import logging
import pandas as pd
from datetime import datetime

# Windows encoding safety
import codecs
sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "backend"))

from backend.db.database import init_db, get_db
from backend.db.models import Symbol, ThemeConstituent, DailyPrice, Indicator, RelativeRank, MarketSignal
from backend.pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files, rotate_and_archive_to_parquet
from backend.pipeline.orchestrator import run_pipeline

def run_local_rebuild(category=None):
    if category:
        # Map English terms to Japanese database categories to avoid multi-byte characters in batch files
        category_map = {
            "theme": "テーマ",
            "market": "市場",
            "individual": "個別",
            "system": "システム"
        }
        category = category_map.get(category.lower(), category)

    logger.info("=========================================================")
    logger.info(f" 🏛️ LOCAL FULL REBUILD RUNNER ({'Category: ' + category if category else 'All Categories'}) 🏛️")
    logger.info("=========================================================")
    
    temp_db_path = os.path.abspath(os.path.join(project_root, "data", "stocktool_restoring.db"))
    prod_db_path = os.path.abspath(os.path.join(project_root, "data", "stocktool.db"))
    
    # Override via environment variable so init_db targeting temp Sandbox
    os.environ["STOCKTOOL_DB_PATH"] = temp_db_path
    
    # 1. Close any potential open engine/sessions in this process
    import backend.db.database as db_module
    if db_module.engine:
        db_module.engine.dispose()
    db_module.engine = None
    db_module.SessionLocal = None
    
    time.sleep(0.5)
    
    # Clean up previous temp files if exist
    for suffix in ["", "-journal", "-wal", "-shm"]:
        f = temp_db_path + suffix
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
                
    logger.info("1. Initializing fresh Sandbox SQLite schema...")
    init_db(temp_db_path)
    
    # 2. Get latest Parquet Master files
    parquet_dir = get_parquet_master_dir(prod_db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    latest_files = get_latest_master_files(pointer_file)
    
    if not latest_files:
        logger.error("❌ Critical: No active Parquet master file pointer found! Run refresh_All.bat first.")
        return False
        
    logger.info("2. Loading full-history Prices & Symbols from Parquet master (No internet download)...")
    try:
        df_symbols = pd.read_parquet(latest_files['symbols'])
        df_tc = pd.read_parquet(latest_files['tc'])
        df_prices = pd.read_parquet(latest_files['prices'])
        
        # Load indicators & ranks if available to preserve other categories' data
        df_indicators = pd.DataFrame()
        if 'indicators' in latest_files and os.path.exists(latest_files['indicators']):
            df_indicators = pd.read_parquet(latest_files['indicators'])
            
        df_ranks = pd.DataFrame()
        if 'ranks' in latest_files and os.path.exists(latest_files['ranks']):
            df_ranks = pd.read_parquet(latest_files['ranks'])
            
    except Exception as le:
        logger.error(f"❌ Failed to load Parquet master files: {le}")
        return False
        
    logger.info(f"   Loaded Symbols: {len(df_symbols)}, Constituents: {len(df_tc)}, Prices: {len(df_prices)}")
    
    # Initialize Engine mapping for the newly created Sandbox
    # Re-import to ensure we get the fresh sandbox SessionLocal
    import backend.db.database as db_module
    engine_temp = db_module.engine
    
    def bulk_insert(df, table_name):
        if df.empty:
            return
        
        # Drop auto-incrementing ID column for tables other than 'symbols' to prevent UNIQUE constraint failures.
        # 'symbols' ID must be preserved because it is referenced as a foreign key by other tables.
        df_to_insert = df.copy()
        if 'id' in df_to_insert.columns and table_name != 'symbols':
            df_to_insert = df_to_insert.drop(columns=['id'])
            
        cols = df_to_insert.columns.tolist()
        col_str = ", ".join([f'"{c}"' for c in cols])
        placeholders = ", ".join(["?"] * len(cols))
        query = f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholders})'
        df_clean = df_to_insert.where(pd.notnull(df_to_insert), None)
        records = [tuple(x) for x in df_clean.to_numpy()]
        
        connection = engine_temp.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("PRAGMA synchronous = OFF")
            cursor.execute("PRAGMA journal_mode = MEMORY")
            cursor.executemany(query, records)
            connection.commit()
            
            # Restore WAL mode after bulk insert (match production conditions)
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            logger.info(f"   ✓ Bulk-inserted {len(records)} rows into {table_name}")
        except Exception as e:
            connection.rollback()
            logger.error(f"❌ Failed bulk insert into {table_name}: {e}")
            raise e
        finally:
            connection.close()
            
    logger.info("3. Bulk-importing full-history dimension tables into Sandbox...")
    try:
        bulk_insert(df_symbols, "symbols")
        bulk_insert(df_tc, "theme_constituents")
        
        # Load only non-virtual prices (T2 price synthesis will recalculate virtual theme prices from scratch)
        individual_ids = df_symbols[df_symbols['category'].isin(['個別', '市場', '指標', 'セクタ'])]['id'].tolist()
        df_prices_ind = df_prices[df_prices['symbol_id'].isin(individual_ids)]
        logger.info(f"   Filtering only individual/index prices to import ({len(df_prices_ind)} out of {len(df_prices)})...")
        bulk_insert(df_prices_ind, "daily_prices")
        
        # Preserve existing indicators & ranks in the sandbox so other categories are not cleared in final Parquet merge
        if not df_indicators.empty:
            logger.info(f"   Importing existing indicators cache ({len(df_indicators)} rows)...")
            bulk_insert(df_indicators, "indicators")
        if not df_ranks.empty:
            logger.info(f"   Importing existing relative ranks cache ({len(df_ranks)} rows)...")
            bulk_insert(df_ranks, "relative_ranks")
            
    except Exception as e:
        logger.error(f"❌ Failed importing baseline data into sandbox: {e}")
        return False
        
    # 4. Trigger Pipeline in Sandbox mode from T2 (Synthesis) to T5 with --skip-fetch
    logger.info("4. Launching Pipeline to rebuild T2-T5 full-history indicators with --skip-fetch...")
    try:
        import tomllib
        config_path = os.path.join(project_root, "config.toml")
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
            
        # Re-import to get fresh sandbox session
        import backend.db.database as db_module
        
        selected_categories = [category] if category else None
        
        # Run T2 (virtual synthesis) cascaded rebuild
        run_pipeline(
            config=config,
            db_path=temp_db_path,
            logger=logger,
            rebuild_from="T2", # prices cascade rebuild
            categories=selected_categories, # Filter by category
            skip_fetch=True,   # skip internet download completely
            skip_sync=False,   # sync Google sheet symbols (to pick up new tags)
            recalculate_all=True
        )
    except Exception as pe:
        logger.error(f"❌ Pipeline calculation in Sandbox failed: {pe}")
        return False
        
    # 5. Clean up temporary database files
    logger.info("5. Cleaning up temporary Sandbox DB files...")
    import backend.db.database as db_module
    if db_module.engine:
        db_module.engine.dispose()
    db_module.engine = None
    db_module.SessionLocal = None
    
    time.sleep(0.5)
    for suffix in ["", "-journal", "-wal", "-shm"]:
        f = temp_db_path + suffix
        if os.path.exists(f):
            try:
                os.remove(f)
                logger.info(f"   Deleted: {os.path.basename(f)}")
            except:
                pass
                
    # Restore environmental DB path
    os.environ.pop("STOCKTOOL_DB_PATH", None)
    
    # 6. Re-sync Production SQL from newly-updated Parquet Master (2-year lookback)
    logger.info("6. Syncing production stocktool.db cache (latest 2-years) from updated Parquet...")
    try:
        from backend.scripts.run_production_restore import run_production_restore
        run_production_restore()
    except Exception as re:
        logger.error(f"❌ Failed to restore production cache: {re}")
        return False
        
    logger.info(f"\n🎉 LOCAL FULL REBUILD ({category if category else 'All'}) SUCCESSFULLY COMPLETED!")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Local Full Rebuild Runner')
    parser.add_argument('--category', type=str, default=None, help='Category to rebuild (e.g. テーマ)')
    args = parser.parse_args()
    
    success = run_local_rebuild(category=args.category)
    sys.exit(0 if success else 1)
