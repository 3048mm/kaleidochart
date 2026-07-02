import os
import json
import pytest
import pandas as pd
import shutil

# We will implement run_comparison in backend/backtest/scenario_comparison_runner.py
from backend.backtest.scenario_comparison_runner import run_comparison

def test_run_comparison_generates_outputs():
    output_dir = "output/test_comparison_run"
    
    # Clean up previous outputs if exist
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
        
    start_date = "2025-01-01"
    end_date = "2025-04-01" # Short period for fast test run
    
    # 1. Run comparison
    run_comparison(start_date=start_date, end_date=end_date, output_dir=output_dir)
    
    # 2. Check file presence
    summary_path = os.path.join(output_dir, "comparison_summary.json")
    equity_path = os.path.join(output_dir, "comparison_equity_curve.csv")
    
    assert os.path.exists(summary_path)
    assert os.path.exists(equity_path)
    
    # 3. Verify comparison_summary.json structure
    with open(summary_path, 'r', encoding='utf-8') as f:
        summary_data = json.load(f)
        
    assert "start_date" in summary_data
    assert "end_date" in summary_data
    assert "strategies" in summary_data
    
    strategies = summary_data["strategies"]
    for model in ['mts_raw', 'vxv_vix_ema', 'spy_sma200', 'spy_sma63', 'full_position']:
        assert model in strategies
        metrics = strategies[model]
        assert "final_capital" in metrics
        assert "cagr" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "total_trades" in metrics
        assert "profit_factor" in metrics
        
    # 4. Verify comparison_equity_curve.csv columns
    df_equity = pd.read_csv(equity_path)
    required_cols = ['date', 'spy_equity', 'equity_mts_raw', 'equity_vxv_vix_ema', 'equity_spy_sma200', 'equity_spy_sma63', 'equity_full_position']
    for col in required_cols:
        assert col in df_equity.columns
        
    # Clean up
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
