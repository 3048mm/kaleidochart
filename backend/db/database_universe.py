"""Universe Manager – database connection management (universe.db)

既存の database.py / database_user.py と同じパターンで、
read / write 分離 + WAL + BEGIN IMMEDIATE を採用。
"""

import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from db.models_universe import BaseUniverse
import paths

# Module-level state
engine_universe = None
write_engine_universe = None
SessionLocalUniverse = None
SessionLocalUniverseWrite = None
_active_universe_db_path = None


def get_active_universe_db_path():
    return _active_universe_db_path


def init_universe_db(db_path: str, allow_create: bool = False):
    """Initialize universe.db engine, session factories, and create tables.

    `universe.db` は**ユーザー資産**（手動編集と `ticker_history` を持ち再生成
    不可能）であり、swap・クリア・再構築が禁止されている
    （`doc/agent_execution_rules.md` §10.1 / `doc/universe_db_specification.md`）。
    `init_db()` と同じガードを通す。

    Args:
        db_path: 呼び出し側が想定するパス。ワークツリーでは無視される。
        allow_create: 存在しない場合の新規作成を許可する（既定 False）。
    """
    global engine_universe, write_engine_universe
    global SessionLocalUniverse, SessionLocalUniverseWrite
    global _active_universe_db_path

    db_path = paths.resolve_db_path_for_init("universe", db_path)
    paths.announce_non_production("UNIVERSE DATABASE", db_path)

    paths.ensure_writable(db_path)
    if not allow_create:
        paths.require_existing(db_path, "銘柄定義DB (universe.db)")
        paths.require_populated(db_path, "universe")

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
