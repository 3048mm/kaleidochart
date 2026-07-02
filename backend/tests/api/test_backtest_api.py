import os
import json
import pytest
from fastapi.testclient import TestClient

# Import our app from api.server.
from api.server import app

@pytest.fixture
def mock_output_dir(tmp_path, monkeypatch):
    """
    Creates a mock output directory structure with fake backtest results.
    Matches the actual backtest CSV/JSON output formats.
    """
    # Create two scenario directories
    sc1_dir = tmp_path / "scenario_alpha"
    sc1_dir.mkdir()
    
    sc2_dir = tmp_path / "scenario_beta"
    sc2_dir.mkdir()
    
    # 1. Summary JSON (Matches actual stocktool format, lacks cagr directly to test dynamic calculation)
    sc1_summary = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-20",
        "initial_capital": 10000.0,
        "final_capital": 10255.0,
        "profit_factor": 1.25,
        "max_drawdown": {
            "pct": 18.0,
            "amount": 1800.0
        },
        "win_rate": 0.48,
        "total_trades": 80,
        "yearly_performance": {
            "2026": {
                "total_trades": 80,
                "win_rate": 0.48,
                "net_pnl": 255.0,
                "avg_pnl_pct": 0.5,
                "profit_factor": 1.25,
                "spy_return_pct": 3.0
            }
        }
    }
    sc2_summary = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-20",
        "initial_capital": 10000.0,
        "final_capital": 14020.0,
        "profit_factor": 1.43,
        "max_drawdown": {
            "pct": 12.0,
            "amount": 1200.0
        },
        "win_rate": 0.55,
        "total_trades": 120,
        "yearly_performance": {
            "2026": {
                "total_trades": 120,
                "win_rate": 0.55,
                "net_pnl": 4020.0,
                "avg_pnl_pct": 3.35,
                "profit_factor": 1.43,
                "spy_return_pct": 5.2
            }
        }
    }
    
    with open(sc1_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(sc1_summary, f)
    with open(sc2_dir / "scenario_summary.json", "w", encoding="utf-8") as f:
        json.dump(sc2_summary, f)
        
    # 2. Equity Curve CSV (Matches actual stocktool headers 'total_equity', 'cash', 'date')
    sc1_equity = "date,cash,invested,total_equity,positions,held_tickers\n2026-05-20,10000.0,0.0,10000.0,0,\n2026-05-21,9800.0,400.0,10200.0,1,AAPL\n"
    sc2_equity = "date,cash,invested,total_equity,positions,held_tickers\n2026-05-20,10000.0,0.0,10000.0,0,\n2026-05-21,9500.0,1000.0,10500.0,1,MSFT\n"
    
    with open(sc1_dir / "scenario_equity_curve.csv", "w", encoding="utf-8") as f:
        f.write(sc1_equity)
    with open(sc2_dir / "scenario_equity_curve.csv", "w", encoding="utf-8") as f:
        f.write(sc2_equity)
        
    # 3. Trade Logs CSV (Matches actual stocktool headers)
    sc1_trades = "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\nAAPL,2026-05-19,2026-05-20,150.0,150.0,10,1500.0,strategy,0.0\n"
    sc2_trades = "ticker,entry_date,exit_date,entry_price,exit_price,shares,amount,exit_reason,pnl_pct\nMSFT,2026-05-19,2026-05-20,300.0,300.0,5,1500.0,strategy,0.0\nMSFT,2026-05-20,2026-05-21,300.0,320.0,5,1600.0,stop_loss,0.0667\n"
    
    with open(sc1_dir / "scenario_trade_logs.csv", "w", encoding="utf-8") as f:
        f.write(sc1_trades)
    with open(sc2_dir / "scenario_trade_logs.csv", "w", encoding="utf-8") as f:
        f.write(sc2_trades)
        
    # Set different modification times to test 'latest' resolution.
    os.utime(str(sc1_dir), (1700000000, 1700000000))
    os.utime(str(sc2_dir), (1700000050, 1700000050))
    
    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))
        
    return tmp_path

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_get_scenarios(client, mock_output_dir):
    """Test GET /api/backtest/scenarios returns a list of scenario directory names."""
    resp = client.get("/api/backtest/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert "scenario_alpha" in data
    assert "scenario_beta" in data

def test_get_scenario_summary_specific(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/summary returns the JSON summary."""
    resp = client.get("/api/backtest/scenario/scenario_alpha/summary")
    assert resp.status_code == 200
    data = resp.json()
    # CAGR calculated dynamically: (10255/10000)^(365.25/19) - 1. Just check it has some value.
    assert data["cagr"] > 0
    assert data["profit_factor"] == 1.25
    assert data["max_drawdown"] == -0.18  # Convert 18.0 (%) pct to -0.18

def test_get_scenario_summary_latest(client, mock_output_dir):
    """Test GET /api/backtest/scenario/latest/summary dynamically resolves to the latest folder (scenario_beta)."""
    resp = client.get("/api/backtest/scenario/latest/summary")
    assert resp.status_code == 200
    data = resp.json()
    # scenario_beta has higher mtime, so it should be loaded
    assert data["cagr"] > 0
    assert data["profit_factor"] == 1.43
    assert data["max_drawdown"] == -0.12  # Convert 12.0 (%) pct to -0.12
    assert data["yearly_performance"] is not None
    assert data["yearly_performance"]["2026"]["spy_return_pct"] == 5.2

def test_get_scenario_equity(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/equity returns parsed CSV data as JSON."""
    resp = client.get("/api/backtest/scenario/scenario_beta/equity")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["date"] == "2026-05-20"
    assert float(data[0]["equity"]) == 10000.0
    assert float(data[1]["equity"]) == 10500.0

def test_get_scenario_trades(client, mock_output_dir):
    """Test GET /api/backtest/scenario/{name}/trades returns parsed CSV trades."""
    resp = client.get("/api/backtest/scenario/scenario_beta/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["ticker"] == "MSFT"
    assert data[0]["action"] == "SELL"
    assert data[1]["ticker"] == "MSFT"
    assert data[1]["action"] == "SELL"
    # PnL multiplied by 100
    assert float(data[1]["pnl_pct"]) == 6.67

def test_get_scenario_not_found(client, mock_output_dir):
    """Test GET of a non-existent scenario returns 404."""
    resp = client.get("/api/backtest/scenario/scenario_non_existent/summary")
    assert resp.status_code == 404
    assert "detail" in resp.json()
