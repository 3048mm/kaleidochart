import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import logging
from datetime import datetime

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from indicators.calculator import calculate_relative_ranks

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = r'D:\My Documents\Programing\stocktool\data\stocktool.db'

def rebuild_ranks_v2():
    try:
        logger.info(f"Connecting to {DB_PATH} using sqlite3...")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 1. Get Symbols Category Map
        logger.info("Loading symbols...")
        symbols_df = pd.read_sql("SELECT id as symbol_id, category FROM symbols WHERE active = 1", conn)
        symbol_map = symbols_df.set_index('symbol_id')['category'].to_dict()

        # 2. Get All Dates
        cursor.execute("SELECT DISTINCT date FROM indicators ORDER BY date")
        dates = [r[0] for r in cursor.fetchall()]
        total_dates = len(dates)
        logger.info(f"Found {total_dates} dates.")

        # 3. Clear existing data
        logger.info("Clearing RelativeRank and MarketSignal tables...")
        cursor.execute("DELETE FROM relative_ranks")
        cursor.execute("DELETE FROM market_signals")
        conn.commit()

        indicators_to_rank = [
            'relative_strength_spy', 
            'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
            'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
            'rs_condition_14', 'rs_condition_21', 'rs_condition_63'
        ]

        # 4. Process Date by Date
        logger.info("Starting Batch Processing...")
        t_start = datetime.now()

        for i, target_date in enumerate(dates):
            # Load T3 for this date
            query = f"SELECT * FROM indicators WHERE date = ?"
            df_ind = pd.read_sql(query, conn, params=(target_date,))
            if df_ind.empty:
                continue
            
            # Merge category
            df_ind['category'] = df_ind['symbol_id'].map(symbol_map)
            df_ind = df_ind[df_ind['category'].notna()]
            
            # Rank Each Indicator
            all_rank_tuples = []
            for col in indicators_to_rank:
                if col not in df_ind.columns:
                    continue
                
                ranked = calculate_relative_ranks(df_ind, group_col='category', indicator_col=col)
                valid = ranked[ranked['percent_rank'].notna()]
                
                # Create tuples for executemany
                for _, row in valid.iterrows():
                    all_rank_tuples.append((
                        int(row['symbol_id']),
                        str(row['date']),
                        str(row['group_name']),
                        str(row['indicator_name']),
                        float(row['percent_rank'])
                    ))
            
            # Bulk Insert
            if all_rank_tuples:
                cursor.executemany(
                    "INSERT INTO relative_ranks (symbol_id, date, group_name, indicator_name, percent_rank) VALUES (?,?,?,?,?)",
                    all_rank_tuples
                )
            
            if (i+1) % 50 == 0 or i == total_dates - 1:
                conn.commit()
                elapsed = (datetime.now() - t_start).total_seconds()
                avg_speed = elapsed / (i + 1)
                remaining_time = avg_speed * (total_dates - (i + 1))
                logger.info(f"[{i+1}/{total_dates}] {target_date} done. Speed: {avg_speed:.2f}s/date. Est. Remaining: {remaining_time/60:.1f}m")

        logger.info("Rebuild T4/T5 Success.")
        
    except Exception as e:
        logger.error(f"Critical error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    rebuild_ranks_v2()
