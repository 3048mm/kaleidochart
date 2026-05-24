import os
import sys
import argparse
from sqlalchemy import create_engine

# Add backend to path
backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.models import Base

def migrate_pipeline_meta(db_path: str):
    """
    Creates the 'pipeline_meta' table if it does not exist.
    Compatible with any SQLite database path (Sandbox or Production).
    """
    if not db_path:
        print("Error: No database path specified.")
        sys.exit(1)
        
    print(f"Starting migration for DB: {db_path}")
    
    # Resolve absolute path for engine
    abs_db_path = os.path.abspath(db_path)
    db_dir = os.path.dirname(abs_db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    engine = create_engine(f"sqlite:///{abs_db_path}")
    
    try:
        # Base.metadata.create_all will only create tables that do not exist yet.
        # It is safe to run repeatedly.
        Base.metadata.create_all(engine)
        print("Migration completed successfully. 'pipeline_meta' table is ready.")
    except Exception as e:
        print(f"Migration failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create pipeline_meta table in target SQLite DB.")
    parser.add_argument("db_path", type=str, nargs="?", default="data/stocktool_sandbox.db",
                        help="Path to the SQLite database file (default: data/stocktool_sandbox.db)")
    args = parser.parse_args()
    
    migrate_pipeline_meta(args.db_path)
