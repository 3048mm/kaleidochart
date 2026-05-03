import sqlite3
import os
import sys

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

def migrate(db_path):
    print(f"Migrating {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    columns = [
        ("change_1d_pct", "FLOAT"),
        ("change_1w_pct", "FLOAT"),
        ("change_1m_pct", "FLOAT")
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE indicators ADD COLUMN {col_name} {col_type}")
            print(f"Successfully added {col_name} column.")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column {col_name} already exists.")
            else:
                print(f"Error adding {col_name}: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/stocktool.db")
    args = parser.parse_args()
    
    # Resolve relative path from project root
    project_root = os.path.dirname(backend_dir)
    db_full_path = os.path.join(project_root, args.db)
    
    if os.path.exists(db_full_path):
        migrate(db_full_path)
    else:
        print(f"File not found: {db_full_path}")
