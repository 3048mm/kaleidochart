import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from db.models import Base
import paths

# DB engine and session factory will be initialized after loading config
# engine/SessionLocal        : API・読み取り用（DEFERRED。SELECT が書き込みロックを取らない）
# write_engine/SessionLocalWrite : パイプライン・書き込み用（BEGIN IMMEDIATE でロック昇格デッドロックを防止）
engine = None
write_engine = None
SessionLocal = None
SessionLocalWrite = None
_active_db_path = None

def get_active_db_path():
    return _active_db_path

def init_db(db_path: str, allow_create: bool = False):
    """
    Initializes the database engine and creates all tables if they don't exist.

    Args:
        db_path: 呼び出し側が想定する DB パス。**ワークツリーでは無視され**、
            プロビジョニング結果（`paths.py`）が優先される。詳細は
            `paths.resolve_db_path_for_init()`。
        allow_create: DB ファイルが存在しないときに新規作成を許可する。
            既定 False。存在しなければ `DataNotProvisionedError` を送出する
            ——「黙って空DBを作り、間違ったデータで結論を出す」事故を防ぐため。
            環境変数 `STOCKTOOL_ALLOW_DB_CREATE=1` でも解除できる（pytest 等）。
    """
    global engine, write_engine, SessionLocal, SessionLocalWrite, _active_db_path

    db_path = paths.resolve_db_path_for_init("stocktool", db_path)
    paths.announce_non_production("DATABASE", db_path)

    # ワークツリーから本番へ書こうとしていないか（FS では防げないのでここで）
    paths.ensure_writable(db_path)
    if not allow_create:
        paths.require_existing(db_path, "システムDB (stocktool.db)")
        paths.require_populated(db_path, "stocktool")

    _active_db_path = db_path

    # Ensure directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    # Example: sqlite:///data/stocktool.db
    database_url = f"sqlite:///{db_path}"
    
    # Read engine (API): DEFERRED transactions so SELECTs never take the write
    # lock. timeout=5s — WAL readers never wait on writers, so a long busy
    # timeout here would only mask configuration bugs as infinite spinners.
    engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 5})

    # Write engine (pipeline): BEGIN IMMEDIATE prevents deadlocks from lock
    # upgrade (DEFERRED → EXCLUSIVE). timeout=3600s tolerates long batch jobs.
    write_engine = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 3600})

    # WAL mode + synchronous=NORMAL for concurrent read/write (architecture.md §10)
    # NOTE: BEGIN IMMEDIATE must NOT be applied to the read engine — API reads
    # would take the write lock and stall behind long pipeline transactions (T4 etc.).
    for eng in (engine, write_engine):
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    @event.listens_for(write_engine, "begin")
    def _do_begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    # Create session factories
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    SessionLocalWrite = sessionmaker(autocommit=False, autoflush=False, bind=write_engine)
    
    # Create all tables according to the models
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"Database initialization error: {e}")

@contextmanager
def get_db_session():
    """
    Context manager to handle database sessions.
    Usage:
        with get_db_session() as db:
            # your database operations
    """
    global SessionLocal
    if SessionLocal is None:
        raise Exception("Database not initialized. Call init_db() first.")
    
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@contextmanager
def get_db():
    """
    Provides a database session as a context manager.
    Can be used with 'with get_db() as db:'
    """
    with get_db_session() as db:
        yield db

def get_read_engine_for(session):
    """pandas read_sql 等の読み取り用に、セッションと同じ DB を指す読み取りエンジンを返す。

    write エンジン (BEGIN IMMEDIATE) 経由で read_sql すると、pandas が開く新規接続が
    自プロセスの write セッションの RESERVED ロックと自己デッドロックするため、
    読み取りには DEFERRED の読み取りエンジンを使う。
    テスト等で読み取りエンジンが未初期化、またはセッションが別 DB を指す場合は
    session.get_bind() にフォールバックする（テストのエンジンには IMMEDIATE リスナーが
    無いためデッドロックしない）。
    """
    session_engine = session.get_bind()
    if engine is not None and str(engine.url) == str(session_engine.url):
        return engine
    return session_engine

@contextmanager
def get_write_db():
    """
    Provides a WRITE database session (BEGIN IMMEDIATE, long busy timeout).
    Use this for the pipeline and any batch job that writes to stocktool.db.
    Usage:
        with get_write_db() as db:
            # your write operations + db.commit()
    """
    global SessionLocalWrite
    if SessionLocalWrite is None:
        raise Exception("Database not initialized. Call init_db() first.")

    db = SessionLocalWrite()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
