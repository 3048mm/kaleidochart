import pytest
import pandas as pd
from backend.backtest.backtest_simulator import BacktestSimulator

@pytest.fixture
def mock_simulator_data():
    prices = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=5, freq='B'),
        'symbol_id': [1] * 5,
        'close': [100, 105, 90, 110, 85],
        'low': [95, 100, 88, 105, 80],
        'high': [102, 108, 95, 115, 90]
    })
    
    symbols = pd.DataFrame({
        'id': [1],
        'ticker': ['AAPL']
    })
    
    return prices, symbols

def test_evaluate_exit_for_day_no_positions(mock_simulator_data):
    prices, symbols = mock_simulator_data
    simulator = BacktestSimulator(prices, symbols)
    
    exits = simulator.evaluate_exit_for_day(pd.Timestamp('2024-01-02'), {'stop_loss_pct': -0.08})
    assert len(exits) == 0

def test_evaluate_exit_for_day_stop_loss(mock_simulator_data):
    prices, symbols = mock_simulator_data
    simulator = BacktestSimulator(prices, symbols)
    
    # Manually add a position
    simulator.positions = [{
        'symbol_id': 1,
        'entry_date': pd.Timestamp('2024-01-01'),
        'entry_price': 100.0,
        'shares': 10
    }]
    
    # 2024-01-03: close is 90 (-10% from entry), stop loss is -8%
    exits = simulator.evaluate_exit_for_day(pd.Timestamp('2024-01-03'), {'stop_loss_pct': -0.08})
    
    assert len(exits) == 1
    assert exits[0]['symbol_id'] == 1
    assert exits[0]['reason'] == 'stop_loss'
    assert exits[0]['exit_price'] == 90.0

def test_evaluate_exit_for_day_profit_target(mock_simulator_data):
    prices, symbols = mock_simulator_data
    simulator = BacktestSimulator(prices, symbols)
    
    simulator.positions = [{
        'symbol_id': 1,
        'entry_date': pd.Timestamp('2024-01-01'),
        'entry_price': 100.0,
        'shares': 10
    }]
    
    # 2024-01-04: close is 110 (+10% from entry), profit target is +8%
    exits = simulator.evaluate_exit_for_day(pd.Timestamp('2024-01-04'), {'profit_target_pct': 0.08})
    
    assert len(exits) == 1
    assert exits[0]['reason'] == 'partial_take_profit'
    assert exits[0]['exit_price'] == 110.0

def test_evaluate_exit_for_day_removes_position(mock_simulator_data):
    prices, symbols = mock_simulator_data
    simulator = BacktestSimulator(prices, symbols)
    
    simulator.positions = [{
        'symbol_id': 1,
        'entry_date': pd.Timestamp('2024-01-01'),
        'entry_price': 100.0,
        'shares': 10
    }]
    
    # Should trigger stop loss
    exits = simulator.evaluate_exit_for_day(pd.Timestamp('2024-01-03'), {'stop_loss_pct': -0.08})
    
    assert len(exits) == 1
    # Position should be removed from active positions
    assert len(simulator.positions) == 0
    # And added to trades history
    assert len(simulator.trades) == 1
