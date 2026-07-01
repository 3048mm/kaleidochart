import os
import sqlite3
import tomllib

def main():
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    
    db_path = config["system"]["db_path"]
    db_path = os.path.abspath(db_path)
    print(f"Target Database: {db_path}")
    
    # Connect to SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # New columns to append
    # (table_name, column_name, type)
    new_columns = [
        ('indicators', 'rs_macd_line_21', 'FLOAT'),
        ('indicators', 'rs_macd_signal_21', 'FLOAT'),
        ('indicators', 'rs_macd_hist_21', 'FLOAT'),
        ('relative_ranks', 'rs_macd_hist_rank_21', 'FLOAT'),
    ]
    
    for tbl, col, col_type in new_columns:
        # Check if already exists
        cursor.execute(f"PRAGMA table_info({tbl});")
        cols = [c[1] for c in cursor.fetchall()]
        if col in cols:
            print(f"Column '{col}' already exists in table '{tbl}'. Skipping.")
        else:
            try:
                cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type};")
                print(f"Added column '{col}' to table '{tbl}'.")
            except Exception as e:
                print(f"Error adding column '{col}' to '{tbl}': {e}")
                
    conn.commit()
    conn.close()
    print("Migration completed.")

if __name__ == "__main__":
    main()
