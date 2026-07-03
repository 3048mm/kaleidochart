# -*- coding: utf-8 -*-
"""
rebuild_backtest_data.py

Downloads historical data (from 2010-04-01) for key backtest assets
(SPY, QQQ, TQQQ, ^VIX, ^VIX3M) and archives them into the Parquet Master cache.
This resolves the data limitation issue where pre-2024 backtests default to 70.0.
"""

import os
import sys
import datetime
import logging
import yfinance as yf
import pandas as pd

# Add project root and backend to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from backend.db.database import init_db, get_db, get_write_db, get_active_db_path
from backend.db.models import Symbol, DailyPrice
from backend.pipeline.parquet_cache_manager import rotate_and_archive_to_parquet, purge_sqlite_cache_older_than_2_years

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TARGET_TICKERS = ["SPY", "QQQ", "TQQQ", "^VIX", "^VIX3M"]
START_DATE = "2010-04-01"

def rebuild_data():
    db_path = get_active_db_path() or os.path.join(project_root, "data", "stocktool.db")
    logger.info(f"Rebuilding backtest historical data in DB: {db_path}")
    init_db(db_path)

    with get_write_db() as db:
        # 1. Resolve / Create symbols in DB
        symbol_ids = {}
        for ticker in TARGET_TICKERS:
            sym = db.query(Symbol).filter(Symbol.ticker == ticker).first()
            if not sym:
                logger.info(f"Creating Symbol record for missing ticker: {ticker}")
                sym = Symbol(
                    ticker=ticker,
                    exchange="US" if not ticker.startswith("^") else "INDEX",
                    name=ticker,
                    category="市場" if not ticker.startswith("^") else "インデックス",
                    asset_class="System",
                    active=1
                )
                db.add(sym)
                db.flush()
            symbol_ids[ticker] = sym.id
        db.commit()

        # 2. Download and insert daily prices
        end_date = datetime.date.today().isoformat()
        for ticker in TARGET_TICKERS:
            logger.info(f"Downloading {ticker} from {START_DATE} to {end_date}...")
            try:
                # yfinance returns timezone-naive daily candles if we fetch daily
                df = yf.download(ticker, start=START_DATE, end=end_date, progress=False)
                if df.empty:
                    logger.warning(f"No data returned for {ticker} via yfinance.")
                    continue

                sym_id = symbol_ids[ticker]
                
                # Delete existing prices for this symbol to prevent unique constraint failures
                db.query(DailyPrice).filter(DailyPrice.symbol_id == sym_id).delete()
                db.commit()

                # Process downloaded data
                prices_to_insert = []
                for idx, row in df.iterrows():
                    date_val = idx.date()
                    
                    # Safe extraction (pandas columns can be MultiIndex or normal)
                    def get_val(col_name):
                        if col_name in row:
                            return float(row[col_name])
                        # MultiIndex fallback
                        for c in row.index:
                            if isinstance(c, tuple) and c[0] == col_name:
                                return float(row[c])
                        return None

                    op = get_val("Open")
                    hp = get_val("High")
                    lp = get_val("Low")
                    cp = get_val("Close")
                    vol = get_val("Volume")
                    
                    if cp is not None:
                        prices_to_insert.append(DailyPrice(
                            symbol_id=sym_id,
                            date=date_val,  # Pass date object directly
                            open=op,
                            high=hp,
                            low=lp,
                            close=cp,
                            volume=vol
                        ))

                logger.info(f"Inserting {len(prices_to_insert)} daily price records for {ticker} into SQLite...")
                db.bulk_save_objects(prices_to_insert)
                db.commit()
                logger.info(f"Inserted successfully for {ticker}.")

            except Exception as e:
                logger.error(f"Failed to process {ticker}: {e}")
                db.rollback()

        # 3. Archive to Parquet Master (this merges SQLite prices into Parquet history)
        logger.info("Archiving loaded historical data to Parquet Master...")
        try:
            rotate_and_archive_to_parquet(db, db_path, logger)
            logger.info("Successfully archived to Parquet Master!")
        except Exception as pe:
            logger.error(f"Failed Parquet archiving: {pe}")

        # 4. Clean up SQLite Cache to keep database size lightweight (< 2 years)
        logger.info("Purging old records from SQLite DB cache to maintain lightweight size...")
        try:
            purge_sqlite_cache_older_than_2_years(db, db_path, logger)
            logger.info("SQLite DB cache successfully shrunk!")
        except Exception as se:
            logger.error(f"Failed to purge SQLite cache: {se}")

    logger.info("Rebuild completed successfully!")

if __name__ == "__main__":
    rebuild_data()
