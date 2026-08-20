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
    
    assert 'exit_reasons' in summary
    assert 'profit_target' in summary['exit_reasons']
    assert summary['exit_reasons']['profit_target']['count'] == 1
    assert summary['exit_reasons']['profit_target']['avg_pnl_pct'] == 10.0
    assert 'stop_loss' in summary['exit_reasons']
    assert summary['exit_reasons']['stop_loss']['count'] == 1
    assert summary['exit_reasons']['stop_loss']['avg_pnl_pct'] == -5.0
    
    # SPY return from index 0 to -1
    spy_start = mock_spy_data.iloc[0]['close']
    spy_end = mock_spy_data.iloc[-1]['close']
    expected_spy_return = (spy_end - spy_start) / spy_start * 100
    assert summary['spy_benchmark_return_pct'] == pytest.approx(expected_spy_return)

    # Expected CAGR over 30 days (30 / 365.25 = 0.082135 years)
    # (1.005) ** (1 / 0.082135) - 1 = ~6.26%
    assert 'cagr' in summary
    assert summary['cagr'] == pytest.approx(6.26, abs=0.1)

    # 1取引平均リターン（建玉に対する pnl_pct の単純平均）: (0.10 + -0.05) / 2 * 100 = 2.5
    assert 'avg_trade_pnl_pct' in summary
    assert summary['avg_trade_pnl_pct'] == pytest.approx(2.5)


def test_avg_trade_pnl_pct_empty_trade_history(mock_spy_data):
    """トレード履歴が空のとき、avg_trade_pnl_pct は 0.0 になる。"""
    reporter = ScenarioReporter()
    summary = reporter.generate_summary(
        trade_history=[],
        initial_capital=100000.0,
        final_capital=100000.0,
        start_date=pd.Timestamp('2024-01-01'),
        end_date=pd.Timestamp('2024-01-31'),
        spy_prices=mock_spy_data
    )
    assert summary['avg_trade_pnl_pct'] == 0.0


def test_avg_trade_pnl_pct_missing_and_mixed_sign(mock_spy_data):
    """pnl_pct が欠損したトレードはスキップし、正負混在も正しく平均できる。"""
    trade_history = [
        {
            'ticker': 'AAPL', 'entry_date': pd.Timestamp('2024-01-01'),
            'exit_date': pd.Timestamp('2024-01-10'), 'entry_price': 100.0, 'exit_price': 120.0,
            'shares': 100, 'amount': 10000.0, 'exit_reason': 'profit_target',
            'pnl_pct': 0.20, 'pnl_amount': 2000.0, 'capital_after': 102000.0
        },
        {
            'ticker': 'MSFT', 'entry_date': pd.Timestamp('2024-01-05'),
            'exit_date': pd.Timestamp('2024-01-12'), 'entry_price': 200.0, 'exit_price': 160.0,
            'shares': 50, 'amount': 10000.0, 'exit_reason': 'stop_loss',
            'pnl_pct': -0.20, 'pnl_amount': -2000.0, 'capital_after': 100000.0
        },
        {
            # pnl_pct が欠損しているトレードは平均計算から除外される
            'ticker': 'GOOG', 'entry_date': pd.Timestamp('2024-01-08'),
            'exit_date': pd.Timestamp('2024-01-15'), 'entry_price': 300.0, 'exit_price': 300.0,
            'shares': 10, 'amount': 3000.0, 'exit_reason': 'unknown',
            'pnl_pct': None, 'pnl_amount': 0.0, 'capital_after': 100000.0
        }
    ]
    reporter = ScenarioReporter()
    summary = reporter.generate_summary(
        trade_history=trade_history,
        initial_capital=100000.0,
        final_capital=100000.0,
        start_date=pd.Timestamp('2024-01-01'),
        end_date=pd.Timestamp('2024-01-31'),
        spy_prices=mock_spy_data
    )
    # (0.20 + -0.20) / 2 * 100 = 0.0（欠損トレードは除外）
    assert summary['avg_trade_pnl_pct'] == pytest.approx(0.0)

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


# ---------------------------------------------------------------------------
# export_trade_logs — score 列（複数戦略の組み合わせジョブ用、2026-08-21 追加）
# ---------------------------------------------------------------------------
def test_export_trade_logs_without_score_still_works(mock_trade_history, tmp_path):
    """score を持たない従来のトレード履歴（単独戦略ジョブ）でも落ちない。"""
    reporter = ScenarioReporter()
    output_file = tmp_path / "trade_logs_no_score.csv"

    reporter.export_trade_logs(mock_trade_history, str(output_file))

    df = pd.read_csv(output_file)
    assert len(df) == 2
    assert 'score' not in df.columns


def test_export_trade_logs_with_score(tmp_path):
    """score を持つトレード履歴（複数戦略の組み合わせジョブ）で score 列が出力される。"""
    reporter = ScenarioReporter()
    trade_history = [
        {
            'ticker': 'TEM', 'score': 3, 'entry_date': pd.Timestamp('2024-01-01'),
            'exit_date': pd.Timestamp('2024-01-10'), 'entry_price': 100.0, 'exit_price': 110.0,
            'shares': 100, 'amount': 10000.0, 'exit_reason': 'profit_target',
            'pnl_pct': 0.10, 'pnl_amount': 1000.0, 'capital_after': 101000.0
        },
    ]
    output_file = tmp_path / "trade_logs_with_score.csv"

    reporter.export_trade_logs(trade_history, str(output_file))

    df = pd.read_csv(output_file)
    assert 'score' in df.columns
    assert df.iloc[0]['score'] == 3
    # score はティッカーの直後（銘柄と一緒に見たいため）
    assert list(df.columns).index('score') == list(df.columns).index('ticker') + 1


def test_export_trade_logs_empty_history_includes_score_header(tmp_path):
    """トレードが0件でも score 列を含んだ空 CSV のヘッダーが得られる。"""
    reporter = ScenarioReporter()
    output_file = tmp_path / "trade_logs_empty.csv"

    reporter.export_trade_logs([], str(output_file))

    df = pd.read_csv(output_file)
    assert len(df) == 0
    assert 'score' in df.columns
