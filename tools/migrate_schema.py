import sqlite3
import logging
import os
from sqlalchemy import MetaData

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

def migrate_db(db_path, metadata: MetaData):
    """
    Compares the current database schema with the provided SQLAlchemy MetaData.
    Adds any missing columns using ALTER TABLE.
    Note: SQLite doesn't support dropping columns easily, so this only adds.
    """
    if not os.path.exists(db_path):
        logger.error(f"Migration failed: Database file not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        for table_name, table_obj in metadata.tables.items():
            # Check if table exists
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
            if not cursor.fetchone():
                logger.warning(f"Table '{table_name}' does not exist in DB yet. create_all() should handle this.")
                continue

            # Get actual columns in DB
            cursor.execute(f"PRAGMA table_info('{table_name}');")
            actual_cols = {row[1] for row in cursor.fetchall()}

            # Identify missing columns in the DB
            for col in table_obj.columns:
                if col.name not in actual_cols:
                    col_type = str(col.type).split('(')[0] # Simplify (e.g. FLOAT(24) -> FLOAT)
                    # SQLite types: INTEGER, TEXT, REAL, BLOB, NULL
                    # Float maps to REAL/FLOAT
                    logger.info(f"Adding column '{col.name}' ({col_type}) to table '{table_name}'...")
                    try:
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}")
                    except sqlite3.OperationalError as e:
                        logger.error(f"Failed to add column {col.name}: {e}")

        conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    # If run as a script, migrate the main databases using the project's models
    import sys
    sys.path.append(os.path.join(os.getcwd(), 'backend'))
    from db.models import Base
    
    # Check for CLI arg or use defaults
    target_db = sys.argv[1] if len(sys.argv) > 1 else "data/stocktool_sandbox.db"
    migrate_db(target_db, Base.metadata)
