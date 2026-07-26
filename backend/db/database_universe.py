"""Universe Manager – database connection management (universe.db)

既存の database.py / database_user.py と同じパターンで、
read / write 分離 + WAL + BEGIN IMMEDIATE を採用。
"""

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from db.models_universe import BaseUniverse

# Module-level state
engine_universe = None
write_engine_universe = None
SessionLocalUniverse = None
SessionLocalUniverseWrite = None
_active_universe_db_path = None


def get_active_universe_db_path():
    return _active_universe_db_path


def init_universe_db(db_path: str):
    """Initialize universe.db engine, session factories, and create tables."""
    global engine_universe, write_engine_universe
    global SessionLocalUniverse, SessionLocalUniverseWrite
    global _active_universe_db_path

    # Environment-based override (same convention as database.py)
    env_name = os.getenv("STOCKTOOL_ENV")
    if env_name == "sandbox":
        db_path = "data/sandbox/universe.db"
        print("\n" + "!" * 60)
        print("!!! [INFO] UNIVERSE DATABASE ENVIRONMENT: SANDBOX !!!")
        print(f"!!! Target DB: {db_path} ")
        print("!" * 60 + "\n")
    elif env_name == "test":
        db_path = "data/test/universe.db"
        print("\n" + "!" * 60)
        print("!!! [INFO] UNIVERSE DATABASE ENVIRONMENT: TEST !!!")
        print(f"!!! Target DB: {db_path} ")
        print("!" * 60 + "\n")
    else:
        env_db_path = os.getenv("STOCKTOOL_UNIVERSE_DB_PATH")
        if env_db_path:
            db_path = env_db_path
            print("\n" + "!" * 60)
            print("!!! [WARNING] UNIVERSE DATABASE OVERRIDDEN BY ENVIRONMENT VARIABLE !!!")
            print(f"!!! Target DB: {db_path} ")
            print("!" * 60 + "\n")

    db_path = os.path.abspath(db_path)
    _active_universe_db_path = db_path

    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    database_url = f"sqlite:///{db_path}"

    # Read engine (API): DEFERRED – SELECTs never take write lock
    engine_universe = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    # Write engine: BEGIN IMMEDIATE – prevents lock-upgrade deadlocks
    write_engine_universe = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 3600},
    )

    # WAL + synchronous=NORMAL for both engines
    for eng in (engine_universe, write_engine_universe):
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    @event.listens_for(write_engine_universe, "begin")
    def _do_begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    # Session factories
    SessionLocalUniverse = sessionmaker(
        autocommit=False, autoflush=False, bind=engine_universe,
    )
    SessionLocalUniverseWrite = sessionmaker(
        autocommit=False, autoflush=False, bind=write_engine_universe,
    )

    # Create tables
    try:
        BaseUniverse.metadata.create_all(bind=engine_universe)
    except Exception as e:
        print(f"Universe Database initialization error: {e}")


@contextmanager
def get_universe_db():
    """Read-only session context manager."""
    global SessionLocalUniverse
    if SessionLocalUniverse is None:
        raise Exception("Universe Database not initialized. Call init_universe_db() first.")

    db = SessionLocalUniverse()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@contextmanager
def get_universe_write_db():
    """Write session context manager (BEGIN IMMEDIATE, long timeout)."""
    global SessionLocalUniverseWrite
    if SessionLocalUniverseWrite is None:
        raise Exception("Universe Database not initialized. Call init_universe_db() first.")

    db = SessionLocalUniverseWrite()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
