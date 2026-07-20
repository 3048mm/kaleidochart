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

def test_exit_rules_equivalence():
    """
    Test equivalence between simulate_trade() (one-shot simulation) and
    BacktestSimulator.evaluate_exit_for_day() (stateful step simulation).
    """
    import numpy as np
    from backend.backtest.backtest_simulator import ExitRules, simulate_trade, TradeResult
    
    # 1. Setup mock data spanning 15 business days
    dates = pd.date_range('2024-01-01', periods=15, freq='B')
    
    # Mock prices
    prices = pd.DataFrame({
        'date': dates,
        'symbol_id': [1] * 15,
        # Price action: Entry at 100 on 2024-01-01.
        # Rises to 121 (triggers +20% partial profit target on day 4).
        # Moves breakeven stop loss to 100.
        # Drops to 99 (triggers breakeven stop loss on day 6).
        'close': [100.0, 102.0, 105.0, 121.0, 115.0, 99.0, 102.0, 105.0, 108.0, 110.0, 112.0, 115.0, 118.0, 120.0, 122.0],
        'low':   [98.0,  100.0, 103.0, 118.0, 110.0, 98.0, 100.0, 103.0, 106.0, 108.0, 110.0, 112.0, 115.0, 118.0, 120.0],
        'high':  [102.0, 104.0, 108.0, 123.0, 118.0, 102.0, 104.0, 108.0, 110.0, 112.0, 115.0, 118.0, 120.0, 122.0, 125.0]
    })
    
    # Mock indicators (needed for full exit rules, SMA50/ATR, EMA21, ATR)
    inds = pd.DataFrame({
        'date': dates,
        'symbol_id': [1] * 15,
        'ema_21': [102.0] * 15,
        'sma_50': [100.0] * 15,
        'atr_14': [5.0] * 15,
        'sma50_atr_mult': [2.0] * 15
    })
    
    symbols = pd.DataFrame({
        'id': [1],
        'ticker': ['AAPL']
    })
    
    # Custom mock signal class to match SignalRecord expectations
    class MockSignal:
        def __init__(self, date, entry_price, symbol_id, ticker):
            self.date = date
            self.entry_price = entry_price
            self.symbol_id = symbol_id
            self.ticker = ticker

    signal = MockSignal(dates[0], 100.0, 1, 'AAPL')
    rules = ExitRules(
        stop_loss_pct=-8.0,
        partial_take_profit_pct=20.0,
        partial_ratio=0.333,
        failsafe_max_days=120
    )
    
    # 2. Execute simulate_trade (one-shot path)
    res_one_shot = simulate_trade(signal, prices, inds, rules)
    assert res_one_shot is not None
    
    # 3. Execute BacktestSimulator (stateful path)
    simulator = BacktestSimulator(prices, symbols)
    
    # Manually seed initial position (entry date is 2024-01-01)
    # The evaluation loop starts on the next day (2024-01-02 onwards)
    simulator.positions = [{
        'symbol_id': 1,
        'entry_date': dates[0],
        'entry_price': 100.0,
        'shares': 10
    }]
    
    for d in dates[1:]:
        simulator.evaluate_exit_for_day(d, rules)
        if len(simulator.positions) == 0:
            break
            
    assert len(simulator.trades) == 1
    res_stateful = simulator.trades[0]
    
    # 4. Compare results
    # exits_triggered formats PnL in decimal in BacktestSimulator, but simulate_trade uses percents
    assert res_one_shot.exit_date == res_stateful['exit_date']
    assert pytest.approx(res_one_shot.pnl_pct, 1e-4) == res_stateful['pnl_pct'] * 100.0
    assert res_one_shot.holding_days == res_stateful['holding_days']
    assert res_one_shot.exit_reason == res_stateful['reason']
    assert pytest.approx(res_one_shot.exit_price) == res_stateful['exit_price']
