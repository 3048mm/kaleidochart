"""FastAPI dependency providers shared by all API routers."""
from db.database import get_db


# Dependency to get a direct DB Session for FastAPI
def get_api_db():
    with get_db() as db:
        yield db

# Dependency for user data DB
def get_api_user_db():
    from db.database_user import get_user_db
    with get_user_db() as user_db:
        yield user_db
