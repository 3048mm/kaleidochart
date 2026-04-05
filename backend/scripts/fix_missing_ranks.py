import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import logging
from datetime import datetime

# backendディレクトリをパスに追加
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from indicators.calculator import calculate_relative_ranks

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = r'D:\My Documents\Programing\stocktool\data\stocktool.db'
TARGET_DATE = '2026-03-27'

def fix_missing_ranks():
    try:
        if not os.path.exists(DB_PATH):
            logger.error(f"Database not found: {DB_PATH}")
            return

        logger.info(f"Connecting to {DB_PATH}...")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 1. 銘柄カテゴリマップの取得
        logger.info("Loading symbols...")
        symbols_df = pd.read_sql("SELECT id as symbol_id, category FROM symbols WHERE active = 1", conn)
        symbol_map = symbols_df.set_index('symbol_id')['category'].to_dict()

        # 2. 対象日の Indicator (T3) データの取得
        logger.info(f"Fetching indicators for {TARGET_DATE}...")
        df_ind = pd.read_sql("SELECT * FROM indicators WHERE date = ?", conn, params=(TARGET_DATE,))
        
        if df_ind.empty:
            logger.warning(f"No indicators found for {TARGET_DATE}. Nothing to do.")
            return

        # カテゴリ結合
        df_ind['category'] = df_ind['symbol_id'].map(symbol_map)
        df_ind = df_ind[df_ind['category'].notna()]

        # ランク計算対象の指標
        indicators_to_rank = [
            'relative_strength_spy', 
            'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
            'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
            'rs_condition_14', 'rs_condition_21', 'rs_condition_63'
        ]

        # 3. 既存データの削除（この日付分のみ）
        logger.info(f"Clearing existing ranks for {TARGET_DATE}...")
        cursor.execute("DELETE FROM relative_ranks WHERE date = ?", (TARGET_DATE,))
        
        # 4. 指標ごとにランク計算
        all_rank_tuples = []
        for col in indicators_to_rank:
            if col not in df_ind.columns:
                logger.warning(f"Indicator column '{col}' not found in T3 table.")
                continue
            
            logger.info(f"Ranking {col}...")
            ranked = calculate_relative_ranks(df_ind, group_col='category', indicator_col=col)
            valid = ranked[ranked['percent_rank'].notna()]
            
            for _, row in valid.iterrows():
                all_rank_tuples.append((
                    int(row['symbol_id']),
                    str(row['date']),
                    str(row['group_name']),
                    str(row['indicator_name']),
                    float(row['percent_rank'])
                ))

        # 5. バルクインサート
        if all_rank_tuples:
            logger.info(f"Inserting {len(all_rank_tuples)} records into relative_ranks...")
            cursor.executemany(
                "INSERT INTO relative_ranks (symbol_id, date, group_name, indicator_name, percent_rank) VALUES (?,?,?,?,?)",
                all_rank_tuples
            )
            conn.commit()
            logger.info(f"Successfully fixed ranks for {TARGET_DATE}.")
        else:
            logger.warning("No rank data generated.")

    except Exception as e:
        logger.error(f"Error: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    fix_missing_ranks()
