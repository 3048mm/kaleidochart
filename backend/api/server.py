import os
import sys
import tomli
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)

from api import routers
from api import portfolio_router
from db.database import init_db
from db.database_user import init_user_db

app = FastAPI(title="Stock Analyzer API", version="0.1.0")

# Load Configuration for DB Init
def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomli.load(f)

# Initialize Database connection pool
config = load_config()
init_db(config["system"]["db_path"])

# Initialize User Database
user_db_path = config["system"].get("user_db_path", os.path.join(os.path.dirname(config["system"]["db_path"]), "user_data.db"))
init_user_db(user_db_path)

# Configure CORS for frontend access (Vite default dev server is 5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the main API router
app.include_router(routers.router, prefix="/api")
# Include portfolio API router
app.include_router(portfolio_router.router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Stock Analyzer API"}
