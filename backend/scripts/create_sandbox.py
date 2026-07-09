import os
import sys
import sqlite3
from datetime import datetime, timedelta

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.database import init_db

def create_sandbox(src_db='data/stocktool.db', dst_db='data/sandbox/stocktool_sandbox.db', days=180):
    # 注意: sandbox は専用ディレクトリ（data/sandbox/ 等）に置くこと。data/ 直下に置くと
    # Parquet 解決（DB と同じディレクトリの parquet_master/）が本番 Parquet を指してしまう。
    # 詳細: .claude/skills/sandbox-workflow/SKILL.md §1
    if not os.path.exists(src_db):
        print(f"Source DB {src_db} not found.")
        return

    os.makedirs(os.path.dirname(dst_db) or ".", exist_ok=True)

    # Delete existing sandbox
    if os.path.exists(dst_db):
        try:
            os.remove(dst_db)
        except PermissionError:
            print(f"Error: {dst_db} is currently in use. Please close any connections to it.")
            return
    
    # Initialize destination DB schema using our standard init_db
    print(f"Initializing schema for {dst_db}...")
    init_db(dst_db)
    
    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    
    print(f"Extracting data from {src_db} to {dst_db}...")
    try:
        src_conn = sqlite3.connect(src_db)
        src_conn.execute("PRAGMA busy_timeout = 30000;")
        src_conn.row_factory = sqlite3.Row
        src_cursor = src_conn.cursor()
        
        dst_conn = sqlite3.connect(dst_db)
        dst_conn.execute("PRAGMA busy_timeout = 30000;")
        dst_cursor = dst_conn.cursor()

        with src_conn, dst_conn:
            # 1. Symbols
            print("  Copying symbols...")
            # Rather than filtering by Japanese category names (which may have encoding issues),
            # we select:
            # - All symbols that act as a 'theme' or 'index' (they are in theme_constituents as theme_id)
            # - A set of major tickers
            src_cursor.execute("""
                SELECT * FROM symbols 
                WHERE (
                    id IN (SELECT DISTINCT theme_id FROM theme_constituents)
                    OR ticker IN ('AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'AVGO', 'SMCI', 'LNG', 'SM', 'SPY', 'QQQ', 'DIA', 'IWM')
                    OR category NOT LIKE '個別%' -- Fallback: non-individual stocks (careful with encoding, but '個別' is prefix)
                )
                AND active = 1
            """)
            symbols = src_cursor.fetchall()
            print(f"  Found {len(symbols)} symbols to copy.")
            
            if not symbols:
                # Fallback to just copying major tickers if the subquery/category fails
                print("  [WARNING] Category/Theme filter yielded nothing. Copying major tickers only...")
                src_cursor.execute("SELECT * FROM symbols WHERE ticker IN ('AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'SPY', 'QQQ') AND active = 1")
                symbols = src_cursor.fetchall()
                if not symbols:
                    print("  [ERROR] No symbols found even with fallback!")
                    return
                
            symbol_ids = [s['id'] for s in symbols]
            symbol_placeholders = ",".join(["?"] * len(symbol_ids))
            
            print(f"  Copying {len(symbols)} symbols...")
            # We use REPLACE to handle any accidental duplicates, but symbols table should be clean
            cols = symbols[0].keys()
            dst_cursor.executemany(f"INSERT OR REPLACE INTO symbols ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(s) for s in symbols])
            
            # 2. Daily Prices (Limit days)
            print(f"  Copying daily_prices (since {cutoff_date})...")
            src_cursor.execute(f"SELECT * FROM daily_prices WHERE symbol_id IN ({symbol_placeholders}) AND date >= ?", (*symbol_ids, cutoff_date))
            rows = src_cursor.fetchall()
            if rows:
                cols = rows[0].keys()
                dst_cursor.executemany(f"INSERT OR REPLACE INTO daily_prices ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

            # 3. Indicators (Limit days) - Skipped due to schema difference during refactoring
            # print(f"  Copying indicators (since {cutoff_date})...")
            # src_cursor.execute(f"SELECT * FROM indicators WHERE symbol_id IN ({symbol_placeholders}) AND date >= ?", (*symbol_ids, cutoff_date))
            # rows = src_cursor.fetchall()
            # if rows:
            #     cols = rows[0].keys()
            #     dst_cursor.executemany(f"INSERT OR REPLACE INTO indicators ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

            # 4. Relative Ranks (Limit days) - Skipped due to schema difference during refactoring
            # print(f"  Copying relative_ranks (since {cutoff_date})...")
            # src_cursor.execute(f"SELECT * FROM relative_ranks WHERE symbol_id IN ({symbol_placeholders}) AND date >= ?", (*symbol_ids, cutoff_date))
            # rows = src_cursor.fetchall()
            # if rows:
            #     cols = rows[0].keys()
            #     dst_cursor.executemany(f"INSERT OR REPLACE INTO relative_ranks ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

            # 5. Market Signals (All for timeline consistency)
            print("  Copying market_signals...")
            src_cursor.execute(f"SELECT * FROM market_signals WHERE date >= ?", (cutoff_date,))
            rows = src_cursor.fetchall()
            if rows:
                cols = rows[0].keys()
                dst_cursor.executemany(f"INSERT OR REPLACE INTO market_signals ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

            # 6. Earnings
            print("  Copying earnings...")
            src_cursor.execute(f"SELECT * FROM earnings WHERE symbol_id IN ({symbol_placeholders})", symbol_ids)
            rows = src_cursor.fetchall()
            if rows:
                cols = rows[0].keys()
                dst_cursor.executemany(f"INSERT OR REPLACE INTO earnings ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

            # 7. Theme Constituents
            print("  Copying theme_constituents...")
            src_cursor.execute(f"SELECT * FROM theme_constituents WHERE theme_id IN ({symbol_placeholders}) OR symbol_id IN ({symbol_placeholders})", (*symbol_ids, *symbol_ids))
            rows = src_cursor.fetchall()
            if rows:
                cols = rows[0].keys()
                dst_cursor.executemany(f"INSERT OR REPLACE INTO theme_constituents ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", [tuple(r) for r in rows])

        print(f"\nDone! Sandbox DB created at: {os.path.abspath(dst_db)}")
        print(f"To use it, set environment variable: STOCKTOOL_DB_PATH={dst_db}")
        
    except sqlite3.Error as e:
        print(f"SQLite Error: {e}")
    finally:
        if 'src_conn' in locals(): src_conn.close()
        if 'dst_conn' in locals(): dst_conn.close()

if __name__ == "__main__":
    # Increased time range to 300 days to ensure we have enough data points and rank coverage
    create_sandbox(days=300)
