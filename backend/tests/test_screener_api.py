import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator
from api.routers import router, get_api_db

# Create shared in-memory SQLite DB
_engine = create_engine(
    "sqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def _get_test_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


def _create_app():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = _get_test_db
    return app


_app = _create_app()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset DB for each test and seed base data."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    db = _TestSession()
    # Add minimal symbols
    sym1 = Symbol(id=1, ticker="AAPL", name="Apple", category="個別", active=1)
    sym2 = Symbol(id=2, ticker="_CYBR9E_", name="サイバーセキュリティ::クラウド・セキュリティ", category="テーマ", active=1)
    db.add_all([sym1, sym2])

    from datetime import date
    d = date(2026, 5, 20)
    # AAPL
    dp1 = DailyPrice(symbol_id=1, date=d, open=180, high=185, low=178, close=183, volume=1000)
    ind1 = Indicator(symbol_id=1, date=d, change_1d_pct=1.5, dist_sma50_atr=4.0)
    db.add(dp1)
    db.add(ind1)

    # Theme _CYBR9E_
    dp2 = DailyPrice(symbol_id=2, date=d, open=100, high=105, low=98, close=102, volume=500)
    ind2 = Indicator(symbol_id=2, date=d, change_1d_pct=2.0, dist_sma50_atr=1.0)
    db.add(dp2)
    db.add(ind2)

    db.commit()
    db.close()
    yield


@pytest.fixture
def client():
    with TestClient(_app) as c:
        yield c


def test_screener_dashboard_returns_all_presets(client):
    """
    TDD Test: Verify that all presets defined in TOML are returned,
    including those NOT in active_rise_ids (e.g. climax_top).
    """
    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    
    data = resp.json()
    assert "rise" in data
    assert "fall" in data
    
    # "climax_top" is in data/screener_presets.toml (group = "Overhead sign")
    # but NOT in active_rise_ids = ["thema_momentum", "momentum_breakout", "rrg_improving_in", "check_1d_gain"]
    # This test asserts that climax_top is STILL present in the dashboard response.
    rise_ids = [cat["id"] for cat in data["rise"]]
    assert "climax_top" in rise_ids, f"climax_top should be present in rise categories: {rise_ids}"
