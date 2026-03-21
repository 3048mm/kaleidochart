import sqlite3
import os

db_path = 'data/stocktool.db'
if os.path.exists(db_path):
    print("Dropping all tables from database...")
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        tables = [
            "market_signals", "relative_ranks", "indicators", 
            "daily_prices", "theme_constituents", "symbols", "alembic_version"
        ]
        for tbl in tables:
            try:
                c.execute(f"DROP TABLE {tbl}")
            except Exception as e:
                print(f"Skipping {tbl}: {e}")
                
        conn.commit()
        conn.close()
        print("Done. DB tables dropped.")
    except Exception as e:
        print(f"Error dropping tables: {e}")
else:
    print("DB file does not exist.")
