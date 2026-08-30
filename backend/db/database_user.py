import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from db.models_user import BaseUser
import paths

engine_user = None
SessionLocalUser = None
_active_user_db_path = None

def get_active_user_db_path():
    return _active_user_db_path

def init_user_db(db_path: str, allow_create: bool = False):
    """ユーザー資産DB（watchlist / portfolio）を初期化する。

    `user_data.db` は**ユーザー資産**であり swap・クリア・再構築が禁止されている
    （`doc/agent_execution_rules.md` §10.1）。ワークツリーから本番を掴んで
    `heal_*_ids()` に破壊的 UPDATE をさせた前例があるため、`init_db()` と同じ
    ガード（本番書き込み禁止・存在しなければ即死）を通す。

    Args:
        db_path: 呼び出し側が想定するパス。ワークツリーでは無視される。
        allow_create: 存在しない場合の新規作成を許可する（既定 False）。
    """
    global engine_user, SessionLocalUser, _active_user_db_path

    db_path = paths.resolve_db_path_for_init("user_data", db_path)
    paths.announce_non_production("USER DATABASE", db_path)

    paths.ensure_writable(db_path)
    if not allow_create:
        paths.require_existing(db_path, "ユーザーDB (user_data.db)")
        paths.require_populated(db_path, "user_data")

    _active_user_db_path = db_path

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    database_url = f"sqlite:///{db_path}"
    
    engine_user = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 3600})
    
    # WAL mode + synchronous=NORMAL for concurrent read/write (same policy as stocktool.db)
    @event.listens_for(engine_user, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    
    # BEGIN IMMEDIATE prevents deadlocks from lock upgrade
    @event.listens_for(engine_user, "begin")
    def _do_begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    
    SessionLocalUser = sessionmaker(autocommit=False, autoflush=False, bind=engine_user)
    
    try:
        BaseUser.metadata.create_all(bind=engine_user)
    except Exception as e:
        print(f"User Database initialization error: {e}")

@contextmanager
def get_user_db_session():
    global SessionLocalUser
    if SessionLocalUser is None:
        raise Exception("User Database not initialized. Call init_user_db() first.")
    
    db = SessionLocalUser()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@contextmanager
def get_user_db():
    with get_user_db_session() as db:
        yield db
