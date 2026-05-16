import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from backend.backtest.scenario_runner import run_scenario_test

@pytest.fixture
def mock_scenario_data():
    dates = pd.date_range('2024-01-01', periods=10, freq='B')
    
    # Mock prices for SPY, VIX, and a stock AAPL
    prices = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1]*10 + [2]*10 + [3]*10,
        'open': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)],
        'high': [101.0 + i for i in range(10)] + [16.0]*10 + [152.0 + i*5 for i in range(10)],
        'low': [99.0 + i for i in range(10)] + [14.0]*10 + [148.0 + i*5 for i in range(10)],
        'close': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)],
        'volume': [1000000]*30,
        'market_cap': [1000000000]*30,
        'sma_20': [90.0]*10 + [15.0]*10 + [140.0]*10,
        'change_1d_pct': [0.01]*10 + [0.0]*10 + [0.05]*10,
        'ftd_signal': [False]*30,
        'dd_signal': [False]*30
    })
    
    symbols = pd.DataFrame({
        'id': [1, 2, 3],
        'ticker': ['SPY', '^VIX', 'AAPL'],
        'category': ['ETF', 'ETF', '個別'],
        'name': ['SPDR S&P 500', 'VIX', 'Apple'],
        'active': [1, 1, 1]
    })
    
    indicators = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1]*10 + [2]*10 + [3]*10,
        'rs_ratio_21': [105.0]*30,
        'rs_momentum_21': [100.0]*30,
        'ema_21': [95.0]*30,
        'sma_50': [90.0]*30,
        'atr_14': [2.0]*30,
        'dist_sma50_atr': [5.0]*30
    })
    
    return prices, symbols, indicators

@patch('backend.backtest.scenario_runner.SessionLocal')
@patch('backend.backtest.scenario_runner.preload_data')
def test_scenario_runner_integration(mock_preload, mock_session, mock_scenario_data, tmp_path):
    prices, symbols, indicators = mock_scenario_data
    mock_preload.return_value = (
        symbols,
        prices,
        indicators,
        pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        pd.DataFrame(columns=['theme_id', 'symbol_id']),
        prices['date'].unique().tolist()
    )
    
    mock_db_instance = MagicMock()
    mock_session.return_value = mock_db_instance
    
    output_dir = tmp_path / "scenario_output"
    output_dir.mkdir()
    
    # Run the integration wrapper
    result = run_scenario_test(
        start_date="2024-01-01",
        end_date="2024-01-12",
        initial_capital=100000.0,
        max_positions=4,
        output_dir=str(output_dir)
    )
    
    assert result is not None
    assert 'summary' in result
    assert 'trade_history' in result
    
    # Check that CSV was created
    assert (output_dir / "scenario_trade_logs.csv").exists()
    
    # With a highly bullish setup and only 1 stock, it might trigger a buy
    # The integration should run without errors, verifying the component wiring.
    assert result['summary']['initial_capital'] == 100000.0
