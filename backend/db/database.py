import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base

# DB engine and session factory will be initialized after loading config
engine = None
SessionLocal = None
_active_db_path = None

def get_active_db_path():
    return _active_db_path

def init_db(db_path: str):
    """
    Initializes the database engine and creates all tables if they don't exist.
    """
    global engine, SessionLocal
    
    # Allow override via environment variable
    env_db_path = os.getenv("STOCKTOOL_DB_PATH")
    if env_db_path:
        db_path = env_db_path
        print("\n" + "!" * 60)
        print(f"!!! [WARNING] DATABASE OVERRIDDEN BY ENVIRONMENT VARIABLE !!!")
        print(f"!!! Target DB: {db_path} ")
        print("!" * 60 + "\n")
    
    _active_db_path = db_path
    
    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    # Example: sqlite:///data/stocktool.db
    database_url = f"sqlite:///{db_path}"
    
    # Creates engine. connect_args check_same_thread is for SQLite
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 3600})
    
    # Enable WAL mode for SQLite
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
    
    # Create session factory
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create all tables according to the models
    # Wrapped in try/except to handle race conditions when parallel workers
    # all call init_db() simultaneously (SQLite table-already-exists errors).
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        if "already exists" in str(e):
            pass  # Safe to ignore: table was created by another worker
        else:
            raise

@contextmanager
def get_db():
    """
    Dependency generator for DB sessions, to be used with context managers wrapper.
    Ensures safe commit/rollback and closing of sessions.
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
        
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
