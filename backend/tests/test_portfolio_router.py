"""
TDD tests for portfolio API router endpoints.
Phase 2 - Step 2-6. Tests HTTP-level endpoints using FastAPI TestClient.
Uses an in-memory SQLite DB for isolation.
"""
import pytest
from datetime import date
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator
from db.models_user import BaseUser

# Create shared in-memory DBs
_engine = create_engine(
    "sqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

_user_engine = create_engine(
    "sqlite:///file:memdb2?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
BaseUser.metadata.create_all(_user_engine)
_UserTestSession = sessionmaker(bind=_user_engine, autocommit=False, autoflush=False)


def _get_test_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()

def _get_test_user_db():
    db = _UserTestSession()
    try:
        yield db
    finally:
        db.close()


def _create_app():
    app = FastAPI()
    from api.portfolio_router import router, get_api_db, get_api_user_db
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = _get_test_db
    app.dependency_overrides[get_api_user_db] = _get_test_user_db
    return app


_app = _create_app()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset DB for each test, seed base data."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    BaseUser.metadata.drop_all(_user_engine)
    BaseUser.metadata.create_all(_user_engine)

    db = _TestSession()
    sym1 = Symbol(id=1, ticker="AAPL", name="Apple Inc.", category="個別",
                  asset_class="Equity", active=1)
    sym2 = Symbol(id=2, ticker="MSFT", name="Microsoft", category="個別",
                  asset_class="Equity", active=1)
    db.add_all([sym1, sym2])

    for i in range(5):
        d = date(2026, 5, 1 + i)
        dp = DailyPrice(symbol_id=1, date=d,
                        open=180 + i, high=185 + i, low=178 + i,
                        close=183 + i, volume=50_000_000)
        ind = Indicator(symbol_id=1, date=d, ema_21=181 + i, sma_50=175,
                        atr_14=3.5, atr_pct_14=1.9)
        db.add(dp)
        db.add(ind)

    dp_msft = DailyPrice(symbol_id=2, date=date(2026, 5, 1),
                         open=420, high=425, low=418, close=422, volume=30_000_000)
    db.add(dp_msft)
    db.commit()
    db.close()
    
    # create default total portfolio
    user_db = _UserTestSession()
    from db.models_user import TotalPortfolio
    tp = TotalPortfolio(name="Main Account", currency="JPY")
    user_db.add(tp)
    user_db.commit()
    user_db.close()
    
    yield


@pytest.fixture
def client():
    with TestClient(_app) as c:
        yield c


class TestPortfolioEndpoints:
    """Test HTTP-level portfolio CRUD."""

    def test_create_and_list(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "スイング", "total_capital": 3_000_000
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "スイング"

        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_single(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "Test", "total_capital": 1_000_000
        })
        pf_id = resp.json()["id"]

        resp = client.get(f"/api/portfolio/{pf_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/api/portfolio/9999")
        assert resp.status_code == 404

    def test_update(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "Old", "total_capital": 1_000_000
        })
        pf_id = resp.json()["id"]

        resp = client.put(f"/api/portfolio/{pf_id}", json={
            "name": "New", "total_capital": 5_000_000
        })
        assert resp.status_code == 200

        resp = client.get(f"/api/portfolio/{pf_id}")
        assert resp.json()["name"] == "New"
        assert resp.json()["total_capital"] == 5_000_000

    def test_archive(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "To Delete", "total_capital": 1_000_000
        })
        pf_id = resp.json()["id"]

        resp = client.delete(f"/api/portfolio/{pf_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

        resp = client.get("/api/portfolio")
        assert len(resp.json()) == 0


class TestPositionEndpoints:
    """Test HTTP-level position operations."""

    def _create_pf(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "Test", "total_capital": 10_000_000
        })
        return resp.json()["id"]

    def test_add_and_list_positions(self, client):
        pf_id = self._create_pf(client)
        resp = client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "AAPL", "entry_date": "2026-05-01", "shares": 100
        })
        assert resp.status_code == 201
        assert resp.json()["ticker"] == "AAPL"

        resp = client.get(f"/api/portfolio/{pf_id}/positions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["entry_price"] == 183.0

    def test_add_invalid_ticker_returns_400(self, client):
        pf_id = self._create_pf(client)
        resp = client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "INVALID", "entry_date": "2026-05-01", "shares": 100
        })
        assert resp.status_code == 400

    def test_sell_full(self, client):
        pf_id = self._create_pf(client)
        resp = client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "AAPL", "entry_date": "2026-05-01", "shares": 100
        })
        pos_id = resp.json()["id"]

        resp = client.post(f"/api/portfolio/{pf_id}/positions/{pos_id}/sell", json={
            "exit_date": "2026-05-05", "exit_price": 187.0,
            "exit_shares": 100, "exit_reason": "take_profit_full"
        })
        assert resp.status_code == 200
        assert resp.json()["exit_shares"] == 100
        assert resp.json()["pnl_pct"] > 0

    def test_sell_trim(self, client):
        pf_id = self._create_pf(client)
        resp = client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "AAPL", "entry_date": "2026-05-01", "shares": 100
        })
        pos_id = resp.json()["id"]

        resp = client.post(f"/api/portfolio/{pf_id}/positions/{pos_id}/sell", json={
            "exit_date": "2026-05-03", "exit_price": 185.0,
            "exit_shares": 33, "exit_reason": "take_profit_trim"
        })
        assert resp.status_code == 200
        assert resp.json()["exit_shares"] == 33

        resp = client.get(f"/api/portfolio/{pf_id}/positions")
        assert len(resp.json()) == 1
        assert resp.json()[0]["shares"] == 67


class TestHistoryAndSummaryEndpoints:
    """Test history and summary endpoints."""

    def _create_pf_with_trade(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "Test", "total_capital": 10_000_000
        })
        pf_id = resp.json()["id"]
        resp = client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "AAPL", "entry_date": "2026-05-01", "shares": 100
        })
        pos_id = resp.json()["id"]
        client.post(f"/api/portfolio/{pf_id}/positions/{pos_id}/sell", json={
            "exit_date": "2026-05-05", "exit_price": 187.0,
            "exit_shares": 100, "exit_reason": "take_profit_full"
        })
        return pf_id

    def test_get_history(self, client):
        pf_id = self._create_pf_with_trade(client)
        resp = client.get(f"/api/portfolio/{pf_id}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["ticker"] == "AAPL"
        assert "cumulative_pnl" in data[0]

    def test_get_summary(self, client):
        resp = client.post("/api/portfolio", json={
            "name": "Summary Test", "total_capital": 10_000_000
        })
        pf_id = resp.json()["id"]
        client.post(f"/api/portfolio/{pf_id}/positions", json={
            "ticker": "AAPL", "entry_date": "2026-05-01", "shares": 100
        })

        resp = client.get(f"/api/portfolio/{pf_id}/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_capital"] == 10_000_000
        assert data["risk_amount"] == 100_000
        assert data["open_positions"] == 1
