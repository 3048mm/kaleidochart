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
from api import screener_router
from api import chart_router
from api import dashboard_router
from api import watchlist_router
from api import portfolio_router
from api import backtest_router
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

# Prevent API caching
@app.middleware("http")
async def add_cache_control_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Include the main API router
app.include_router(routers.router, prefix="/api")
# Include screener API router
app.include_router(screener_router.router, prefix="/api")
# Include chart / dashboard / watchlist API routers
app.include_router(chart_router.router, prefix="/api")
app.include_router(dashboard_router.router, prefix="/api")
app.include_router(watchlist_router.router, prefix="/api")
# Include portfolio API router
app.include_router(portfolio_router.router, prefix="/api")
# Include backtest API router
app.include_router(backtest_router.router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Stock Analyzer API"}
