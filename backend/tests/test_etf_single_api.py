"""
test_etf_single_api.py — TDD Tests for ETF Single Backtest API Endpoints

Tests the GET endpoints that read results from output files.
Uses mock output directory with pre-generated JSON/CSV files.
"""
import os
import json
import pytest
from fastapi.testclient import TestClient

from api.server import app


@pytest.fixture
def mock_etf_output(tmp_path, monkeypatch):
    """
    Creates a mock etf_single output directory with fake results for SPY.
    """
    etf_dir = tmp_path / "etf_single" / "SPY"
    etf_dir.mkdir(parents=True)

    # 1. Summary JSON
    summary = {
        "ticker": "SPY",
        "start_date": "2010-01-01",
        "end_date": "2025-12-31",
        "initial_capital": 100000.0,
        "consider_tax": 0.20,
        "trading_days": 4000,
        "strategies": {
            "vxv_vix_ema": {
                "final_capital": 285000.0,
                "total_return_pct": 185.0,
                "cagr": 6.78,
                "max_drawdown_pct": -22.5,
                "max_drawdown_date": "2020-03-23",
                "sharpe_ratio": 0.85,
                "yearly_returns": {"2010": 12.5, "2011": -3.2},
                "regime_changes": 45,
                "rebalance_count": 45,
                "time_in_market_pct": 68.5,
            },
            "buy_and_hold": {
                "final_capital": 310000.0,
                "total_return_pct": 210.0,
                "cagr": 7.35,
                "max_drawdown_pct": -33.9,
                "max_drawdown_date": "2020-03-23",
                "sharpe_ratio": 0.72,
                "yearly_returns": {"2010": 15.1, "2011": 2.1},
            },
            "dca": {
                "final_capital": 250000.0,
                "total_return_pct": 150.0,
                "cagr": 5.92,
                "max_drawdown_pct": -28.1,
                "max_drawdown_date": "2020-03-23",
                "sharpe_ratio": 0.65,
                "yearly_returns": {"2010": 10.2, "2011": 0.5},
            },
        },
    }
    with open(etf_dir / "etf_single_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f)

    # 2. Equity CSV
    equity_csv = (
        "date,vxv_equity,buyhold_equity,dca_equity,vxv_position_pct,regime\n"
        "2010-01-04,100000.0,100000.0,100000.0,100.0,BULL\n"
        "2010-01-05,100500.0,100400.0,100200.0,100.0,BULL\n"
        "2010-01-06,99800.0,99900.0,100100.0,0.0,BEAR\n"
    )
    with open(etf_dir / "etf_single_equity.csv", "w", encoding="utf-8") as f:
        f.write(equity_csv)

    # 3. Regimes CSV
    regimes_csv = (
        "date,regime,vxv_vix_ratio,ema5,ema21,position_pct\n"
        "2010-01-04,BULL,1.12,1.12,1.10,1.0\n"
        "2010-01-05,BULL,1.11,1.12,1.10,1.0\n"
        "2010-01-06,BEAR,1.01,1.05,1.09,0.0\n"
    )
    with open(etf_dir / "etf_single_regimes.csv", "w", encoding="utf-8") as f:
        f.write(regimes_csv)

    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))

    return tmp_path


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestEtfSingleSummaryEndpoint:
    """Tests for GET /backtest/etf-single/{ticker}/summary"""

    def test_returns_summary(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/SPY/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "SPY"
        assert "strategies" in data
        assert "vxv_vix_ema" in data["strategies"]
        assert "buy_and_hold" in data["strategies"]
        assert "dca" in data["strategies"]

    def test_strategy_fields(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/SPY/summary")
        data = resp.json()
        vxv = data["strategies"]["vxv_vix_ema"]
        assert vxv["cagr"] == 6.78
        assert vxv["max_drawdown_pct"] == -22.5
        assert vxv["sharpe_ratio"] == 0.85
        assert vxv["regime_changes"] == 45

    def test_not_found_ticker(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/UNKNOWN/summary")
        assert resp.status_code == 404


class TestEtfSingleEquityEndpoint:
    """Tests for GET /backtest/etf-single/{ticker}/equity"""

    def test_returns_equity_points(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/SPY/equity")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["date"] == "2010-01-04"
        assert data[0]["vxv_equity"] == 100000.0
        assert data[0]["regime"] == "BULL"

    def test_equity_all_strategies(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/SPY/equity")
        data = resp.json()
        point = data[1]
        assert point["buyhold_equity"] == 100400.0
        assert point["dca_equity"] == 100200.0


class TestEtfSingleRegimesEndpoint:
    """Tests for GET /backtest/etf-single/{ticker}/regimes"""

    def test_returns_regime_history(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/SPY/regimes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[2]["regime"] == "BEAR"
        assert float(data[2]["vxv_vix_ratio"]) == 1.01

    def test_not_found(self, client, mock_etf_output):
        resp = client.get("/api/backtest/etf-single/INVALID/regimes")
        assert resp.status_code == 404
