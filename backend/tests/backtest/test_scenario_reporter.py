import pytest
import pandas as pd
from backend.backtest.scenario_reporter import ScenarioReporter

@pytest.fixture
def mock_trade_history():
    return [
        {
            'symbol_id': 1, 'ticker': 'AAPL', 'entry_date': pd.Timestamp('2024-01-01'),
            'exit_date': pd.Timestamp('2024-01-10'), 'entry_price': 100.0, 'exit_price': 110.0,
            'shares': 100, 'amount': 10000.0, 'exit_reason': 'profit_target',
            'pnl_pct': 0.10, 'pnl_amount': 1000.0, 'capital_after': 101000.0
        },
        {
            'symbol_id': 2, 'ticker': 'MSFT', 'entry_date': pd.Timestamp('2024-01-05'),
            'exit_date': pd.Timestamp('2024-01-12'), 'entry_price': 200.0, 'exit_price': 190.0,
            'shares': 50, 'amount': 10000.0, 'exit_reason': 'stop_loss',
            'pnl_pct': -0.05, 'pnl_amount': -500.0, 'capital_after': 100500.0
        }
    ]

@pytest.fixture
def mock_spy_data():
    return pd.DataFrame({
        'date': pd.date_range('2024-01-01', '2024-01-31', freq='B'),
        'symbol_id': [100] * 23,
        'close': [400 + i for i in range(23)] # Linear growth
    })

def test_scenario_reporter_generates_summary(mock_trade_history, mock_spy_data):
    reporter = ScenarioReporter()
    summary = reporter.generate_summary(
        trade_history=mock_trade_history,
        initial_capital=100000.0,
        final_capital=100500.0,
        start_date=pd.Timestamp('2024-01-01'),
        end_date=pd.Timestamp('2024-01-31'),
        spy_prices=mock_spy_data
    )
    
    assert summary['total_trades'] == 2
    assert summary['winning_trades'] == 1
    assert summary['losing_trades'] == 1
    assert summary['win_rate'] == 0.5
    assert summary['net_profit'] == 500.0
    assert summary['total_return_pct'] == 0.5 # 500 / 100000 * 100
    
    # SPY return from index 0 to -1
    spy_start = mock_spy_data.iloc[0]['close']
    spy_end = mock_spy_data.iloc[-1]['close']
    expected_spy_return = (spy_end - spy_start) / spy_start * 100
    assert summary['spy_benchmark_return_pct'] == pytest.approx(expected_spy_return)

def test_scenario_reporter_exports_csv(mock_trade_history, tmp_path):
    reporter = ScenarioReporter()
    output_file = tmp_path / "trade_logs.csv"
    
    reporter.export_trade_logs(mock_trade_history, str(output_file))
    
    assert output_file.exists()
    
    df = pd.read_csv(output_file)
    assert len(df) == 2
    assert 'ticker' in df.columns
    assert 'pnl_amount' in df.columns
    assert 'capital_after' in df.columns
    
    assert df.iloc[0]['ticker'] == 'AAPL'
    assert df.iloc[1]['capital_after'] == 100500.0
