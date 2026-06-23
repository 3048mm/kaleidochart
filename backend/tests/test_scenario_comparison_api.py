import os
import json
import pytest
from fastapi.testclient import TestClient
from api.server import app

@pytest.fixture
def mock_comparison_output_dir(tmp_path, monkeypatch):
    """
    Creates mock directory structure for scenario comparison results.
    """
    # Create the scenario_comparison directory
    comp_dir = tmp_path / "scenario_comparison"
    comp_dir.mkdir()
    
    # 1. comparison_summary.json
    summary_data = {
        "start_date": "2025-01-01",
        "end_date": "2025-04-01",
        "strategies": {
            "mts_raw": {
                "final_capital": 105000.0,
                "cagr": 21.55,
                "max_drawdown": 8.5,
                "win_rate": 0.52,
                "total_trades": 45,
                "profit_factor": 1.45
            },
            "vxv_vix_ema": {
                "final_capital": 102000.0,
                "cagr": 8.24,
                "max_drawdown": 12.3,
                "win_rate": 0.48,
                "total_trades": 50,
                "profit_factor": 1.15
            },
            "spy_sma200": {
                "final_capital": 101000.0,
                "cagr": 4.06,
                "max_drawdown": 14.5,
                "win_rate": 0.44,
                "total_trades": 35,
                "profit_factor": 1.05
            },
            "spy_sma63": {
                "final_capital": 103000.0,
                "cagr": 12.55,
                "max_drawdown": 10.2,
                "win_rate": 0.50,
                "total_trades": 40,
                "profit_factor": 1.28
            }
        }
    }
    
    with open(comp_dir / "comparison_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f)
        
    # 2. comparison_equity_curve.csv
    equity_content = (
        "date,spy_equity,equity_mts_raw,equity_vxv_vix_ema,equity_spy_sma200,equity_spy_sma63\n"
        "2025-01-02,100000.0,100000.0,100000.0,100000.0,100000.0\n"
        "2025-01-03,101000.0,100500.0,100200.0,100100.0,100300.0\n"
    )
    with open(comp_dir / "comparison_equity_curve.csv", "w", encoding="utf-8") as f:
        f.write(equity_content)
        
    # 3. Create mock subdirectories for each model to test progress
    models = ['mts_raw', 'vxv_vix_ema', 'spy_sma200', 'spy_sma63']
    for idx, model in enumerate(models):
        model_dir = comp_dir / model
        model_dir.mkdir()
        # Set progress_pct mock (e.g. 10%, 20%, 30%, 40%)
        progress_data = {
            "status": "running",
            "progress_pct": (idx + 1) * 10.0
        }
        with open(model_dir / "scenario_progress.json", "w", encoding="utf-8") as f:
            json.dump(progress_data, f)

    import api.backtest_router
    monkeypatch.setattr(api.backtest_router, "OUTPUT_DIR", str(tmp_path))
    
    return tmp_path

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def test_get_comparison_summary(client, mock_comparison_output_dir):
    """Test GET /api/backtest/comparison/summary."""
    resp = client.get("/api/api/backtest/comparison/summary" if "/api/api/" in "/api/backtest/" else "/api/backtest/comparison/summary")
    if resp.status_code != 200:
        print("DEBUG SUMMARY:", resp.status_code, resp.json())
    assert resp.status_code == 200
    data = resp.json()
    assert data["start_date"] == "2025-01-01"
    assert "mts_raw" in data["strategies"]
    assert data["strategies"]["mts_raw"]["cagr"] == 21.55

def test_get_comparison_equity(client, mock_comparison_output_dir):
    """Test GET /api/backtest/comparison/equity."""
    resp = client.get("/api/backtest/comparison/equity")
    if resp.status_code != 200:
        print("DEBUG EQUITY:", resp.status_code, resp.json())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["date"] == "2025-01-02"
    assert data[1]["spy_equity"] == 101000.0
    assert data[1]["equity_mts_raw"] == 100500.0

def test_get_comparison_progress(client, mock_comparison_output_dir):
    """Test GET /api/backtest/comparison/progress calculates the average progress."""
    resp = client.get("/api/backtest/comparison/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    # Average of 10, 20, 30, 40 is 25
    assert data["progress_pct"] == 25.0

def test_post_comparison_run(client, monkeypatch):
    """Test POST /api/backtest/comparison/run triggers the task."""
    import api.backtest_router
    monkeypatch.setitem(api.backtest_router._comparison_run_status, "status", "idle")
    
    payload = {
        "start_date": "2025-01-01",
        "end_date": "2025-04-01"
    }
    
    called_args = []
    def mock_bg_run(*args, **kwargs):
        called_args.append(args)
        
    monkeypatch.setattr(api.backtest_router, "_bg_run_comparison", mock_bg_run)
    
    resp = client.post(
        "/api/backtest/comparison/run",
        params=payload
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running" or resp.json()["status"] == "started"
    assert len(called_args) == 1
