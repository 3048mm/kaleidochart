import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models_user import BaseUser

engine_user = None
SessionLocalUser = None
_active_user_db_path = None

def get_active_user_db_path():
    return _active_user_db_path

def init_user_db(db_path: str):
    global engine_user, SessionLocalUser
    
    # Allow override via environment variable (e.g. for testing)
    env_db_path = os.getenv("STOCKTOOL_USER_DB_PATH")
    if env_db_path:
        db_path = env_db_path
        print("\n" + "!" * 60)
        print(f"!!! [WARNING] USER DATABASE OVERRIDDEN BY ENVIRONMENT VARIABLE !!!")
        print(f"!!! Target DB: {db_path} ")
        print("!" * 60 + "\n")
    
    db_path = os.path.abspath(db_path)
    _active_user_db_path = db_path
    
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    database_url = f"sqlite:///{db_path}"
    
    engine_user = create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 3600})
    
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
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@contextmanager
def get_user_db():
    with get_user_db_session() as db:
        yield db
