import os
import sys
from datetime import datetime, date, timedelta
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend and project root to path prioritizing local packages (tests -> api -> backend -> project_root と4段階上に遡る)
test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(test_dir)
project_root = os.path.dirname(backend_dir)

sys.path.insert(0, backend_dir)
sys.path.insert(0, project_root)

from db.models import Base, Symbol, DailyPrice, Indicator
from db.models_user import BaseUser
# We will implement these helper functions in pipeline.utils or similar
from pipeline.utils import get_expired_earnings_date_tickers, update_earnings_dates_sync

_engine = create_engine(
    "sqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

_user_engine = create_engine(
    "sqlite:///file:memdb_wl_user?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
BaseUser.metadata.create_all(_user_engine)
_UserTestSession = sessionmaker(bind=_user_engine, autocommit=False, autoflush=False)

@pytest.fixture
def db_session():
    """Sets up an in-memory SQLite DB for testing."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    session = _TestSession()
    try:
        # Seed mock symbols
        session.add_all([
            Symbol(id=1, ticker="AAPL", name="Apple Inc.", exchange="NASDAQ", category="個別", active=1, next_earnings_date=None),
            Symbol(id=2, ticker="NVDA", name="NVIDIA Corp.", exchange="NASDAQ", category="個別", active=1, next_earnings_date=date(2020, 5, 20)), # Past (needs update)
            Symbol(id=3, ticker="TSLA", name="Tesla Inc.", exchange="NASDAQ", category="個別", active=1, next_earnings_date=date(2030, 6, 30)), # Future (skip)
            Symbol(id=4, ticker="MSFT", name="Microsoft Corp.", exchange="NASDAQ", category="個別", active=0, next_earnings_date=None),             # Inactive (skip)
        ])
        session.commit()
        yield session
    finally:
        session.close()

def test_get_expired_earnings_date_tickers(db_session):
    """
    Verifies that get_expired_earnings_date_tickers returns only active tickers whose
    earnings dates are None or in the past relative to 'today'.
    """
    today_date = date(2026, 5, 24)
    target_tickers = ["AAPL", "NVDA", "TSLA", "MSFT"]
    
    expired_tickers = get_expired_earnings_date_tickers(db_session, target_tickers, today_date)
    
    assert "AAPL" in expired_tickers
    assert "NVDA" in expired_tickers
    assert "TSLA" not in expired_tickers
    assert "MSFT" not in expired_tickers # Inactive symbols should be skipped

@patch('data_collection.fetcher.fetch_next_earnings_date')
def test_update_earnings_dates_sync(mock_fetch, db_session):
    """
    Verifies that update_earnings_dates_sync queries yfinance via mock and updates DB.
    """
    # Mock return values for AAPL and NVDA
    def mock_fetch_side_effect(ticker):
        if ticker == "AAPL":
            return date(2026, 7, 28)
        if ticker == "NVDA":
            return date(2026, 8, 20)
        return None
        
    mock_fetch.side_effect = mock_fetch_side_effect
    
    # AAPL and NVDA are expired
    update_earnings_dates_sync(db_session, ["AAPL", "NVDA"])
    
    # Check DB
    aapl = db_session.query(Symbol).filter(Symbol.ticker == "AAPL").first()
    nvda = db_session.query(Symbol).filter(Symbol.ticker == "NVDA").first()
    
    assert aapl.next_earnings_date == date(2026, 7, 28)
    assert nvda.next_earnings_date == date(2026, 8, 20)


# --- API Tests ---

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.routers import router, get_api_db, get_api_user_db
from api.watchlist_router import router as watchlist_api_router
from db.models_user import Watchlist

def _get_test_user_db():
    db = _UserTestSession()
    try:
        yield db
    finally:
        db.close()

def _create_app(db_session_fixture):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(watchlist_api_router, prefix="/api")
    app.dependency_overrides[get_api_db] = lambda: db_session_fixture
    app.dependency_overrides[get_api_user_db] = _get_test_user_db
    return app

def test_api_get_watchlist_triggers_background_tasks(db_session):
    """
    Verifies that GET /api/watchlist triggers background update tasks for expired/missing earnings dates,
    and returns next_earnings_date in the response items.
    """
    # Set up user watchlist db
    BaseUser.metadata.drop_all(_user_engine)
    BaseUser.metadata.create_all(_user_engine)
    
    user_db = _UserTestSession()
    # Add active watchlist entry for NVDA (expired) and TSLA (future)
    user_db.add_all([
        Watchlist(symbol_id=2, ticker="NVDA", exchange="NASDAQ", entry_date=date(2026, 5, 10), entry_price=100.0, status="active", added_at=datetime.utcnow()),
        Watchlist(symbol_id=3, ticker="TSLA", exchange="NASDAQ", entry_date=date(2026, 5, 10), entry_price=200.0, status="active", added_at=datetime.utcnow()),
    ])
    user_db.commit()
    user_db.close()
    
    # Also add mock prices and indicators for target symbols to avoid errors during metrics calculation
    for sid in [2, 3]:
        dp = DailyPrice(symbol_id=sid, date=date(2026, 5, 24), open=100, high=105, low=95, close=102, volume=1000)
        ind = Indicator(symbol_id=sid, date=date(2026, 5, 24), ema_21=101, adr_pct_21=2.5, sma50_atr_mult=3.0)
        db_session.add_all([dp, ind])
    db_session.commit()
    
    app = _create_app(db_session)
    client = TestClient(app)
    
    # Mock update_earnings_dates_sync to verify it got added to background tasks
    with patch("fastapi.BackgroundTasks.add_task") as mock_add_task:
        response = client.get("/api/watchlist")
        assert response.status_code == 200
        
        # Verify the structure includes next_earnings_date
        data = response.json()
        active_items = data["active"]
        assert len(active_items) == 2
        
        nvda_item = next(item for item in active_items if item["ticker"] == "NVDA")
        tsla_item = next(item for item in active_items if item["ticker"] == "TSLA")
        
        # NVDA has next_earnings_date "2020-05-20" in Symbol seed
        assert nvda_item["next_earnings_date"] == "2020-05-20"
        # TSLA has next_earnings_date "2030-06-30" in Symbol seed
        assert tsla_item["next_earnings_date"] == "2030-06-30"
        
        # Background task should be added because NVDA is expired (past date 2026-05-20 relative to today 2026-05-24)
        mock_add_task.assert_called_once()
        args, kwargs = mock_add_task.call_args
        # The target function should be update_earnings_dates_sync
        assert args[0].__name__ == "update_earnings_dates_sync"
        # It should pass the db session, the expired tickers list (containing NVDA), and sleep_seconds
        assert "NVDA" in args[2]
        assert "TSLA" not in args[2]  # TSLA is in the future, so not expired



def test_api_get_watchlist_with_unresolvable_ticker_returns_200(db_session):
    """symbols に存在しない ticker（上場廃止・ticker変更等）が watchlist にあっても
    API 全体が 500 にならず、該当項目は symbol_id=None で返ること。

    背景: heal_watchlist_ids は解決できない ticker の symbol_id を NULL に更新する。
    schema が int 必須だと watchlist 全体が ValidationError で 500 になる。
    """
    BaseUser.metadata.drop_all(_user_engine)
    BaseUser.metadata.create_all(_user_engine)

    user_db = _UserTestSession()
    user_db.add_all([
        # NVDA は symbols に存在（正常系）
        Watchlist(symbol_id=2, ticker="NVDA", exchange="NASDAQ", entry_date=date(2026, 5, 10), entry_price=100.0, status="active", added_at=datetime.utcnow()),
        # GHOST は symbols に存在しない（heal で symbol_id=None になる）
        Watchlist(symbol_id=999, ticker="GHOST", exchange="NASDAQ", entry_date=date(2026, 5, 10), entry_price=50.0, status="active", added_at=datetime.utcnow()),
    ])
    user_db.commit()
    user_db.close()

    dp = DailyPrice(symbol_id=2, date=date(2026, 5, 24), open=100, high=105, low=95, close=102, volume=1000)
    ind = Indicator(symbol_id=2, date=date(2026, 5, 24), ema_21=101, adr_pct_21=2.5, sma50_atr_mult=3.0)
    db_session.add_all([dp, ind])
    db_session.commit()

    app = _create_app(db_session)
    client = TestClient(app)

    response = client.get("/api/watchlist")
    assert response.status_code == 200, response.text

    active = response.json()["active"]
    tickers = {item["ticker"] for item in active}
    assert tickers == {"NVDA", "GHOST"}

    ghost = next(item for item in active if item["ticker"] == "GHOST")
    assert ghost["symbol_id"] is None
