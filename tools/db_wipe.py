import sqlite3
import os

db_path = 'data/stocktool.db'
if os.path.exists(db_path):
    print("Dropping all tables from database out of safety against schema locks...")
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # We explicitly DROP tables instead of DELETE so that SQLAlchemy
        # will recreate them from scratch using models.py on the next run.
        # This completely avoids schema mismatch errors if models.py changes.
        tables = [
            "market_signals", "relative_ranks", "indicators", 
            "daily_prices", "theme_constituents", "symbols", "alembic_version"
        ]
        
        for tbl in tables:
            try:
                c.execute(f"DROP TABLE {tbl}")
                print(f"Dropped table: {tbl}")
            except Exception as e:
                pass # Table might not exist
                
        conn.commit()
        conn.close()
        print("Done. DB tables dropped completely. Ready for next initialization.")
    except Exception as e:
        print(f"Error dropping tables: {e}")
else:
    print("DB file does not exist, nothing to wipe.")
