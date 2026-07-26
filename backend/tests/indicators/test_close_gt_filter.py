"""
TDD test for is_close_gt_* / close_gt_* filter in _apply_filter.

Bug: _build_preset_query recognizes `is_close_gt_ema63` as a known key,
but _apply_filter does not implement the SQL conversion for close_gt_* patterns.
The filter is silently ignored.
"""
import pytest
from datetime import date
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator
from api.routers import router, get_api_db
from api.screener_router import router as screener_api_router


# Create shared in-memory SQLite DB
_engine = create_engine(
    "sqlite:///file:test_close_gt?mode=memory&cache=shared&uri=true",
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
    app.include_router(screener_api_router, prefix="/api")
    app.dependency_overrides[get_api_db] = _get_test_db
    return app


_app = _create_app()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset DB for each test and seed base data."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    db = _TestSession()
    d = date(2026, 6, 30)

    # Stock A: close (150) > ema_63 (140) → should PASS the filter
    sym_a = Symbol(id=1, ticker="ABOVE", name="Above EMA63", category="個別", active=1)
    dp_a = DailyPrice(symbol_id=1, date=d, open=145, high=155, low=144, close=150, volume=1000)
    ind_a = Indicator(symbol_id=1, date=d, ema_63=140.0, change_1d_pct=5.0, avg_dollar_volume_21=5e6)

    # Stock B: close (130) < ema_63 (140) → should FAIL the filter
    sym_b = Symbol(id=2, ticker="BELOW", name="Below EMA63", category="個別", active=1)
    dp_b = DailyPrice(symbol_id=2, date=d, open=135, high=138, low=128, close=130, volume=800)
    ind_b = Indicator(symbol_id=2, date=d, ema_63=140.0, change_1d_pct=3.0, avg_dollar_volume_21=5e6)

    db.add_all([sym_a, dp_a, ind_a, sym_b, dp_b, ind_b])
    db.commit()
    db.close()
    yield


@pytest.fixture
def client():
    with TestClient(_app) as c:
        yield c


class TestCloseGtFilter:
    """Tests for is_close_gt_* and close_gt_* filter patterns via /api/screener."""

    def test_is_close_gt_ema63_filters_correctly(self, client):
        """is_close_gt_ema63=true should only return stocks where close > ema_63."""
        resp = client.get("/api/screener", params={"is_close_gt_ema63": "true"})
        assert resp.status_code == 200
        data = resp.json()
        tickers = [item["ticker"] for item in data]
        assert "ABOVE" in tickers, "Stock with close > ema_63 should pass the filter"
        assert "BELOW" not in tickers, "Stock with close < ema_63 should be filtered out"

    def test_close_gt_sma50_filters_correctly(self, client):
        """close_gt_sma50=true should only return stocks where close > sma_50."""
        # Seed additional data with sma_50
        db = _TestSession()
        d = date(2026, 6, 30)
        # Update indicators with sma_50 values
        ind_a = db.query(Indicator).filter_by(symbol_id=1).first()
        ind_a.sma_50 = 145.0  # close (150) > sma_50 (145) → PASS
        ind_b = db.query(Indicator).filter_by(symbol_id=2).first()
        ind_b.sma_50 = 135.0  # close (130) < sma_50 (135) → FAIL
        db.commit()
        db.close()

        resp = client.get("/api/screener", params={"close_gt_sma50": "true"})
        assert resp.status_code == 200
        data = resp.json()
        tickers = [item["ticker"] for item in data]
        assert "ABOVE" in tickers
        assert "BELOW" not in tickers
