import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
import optuna
import sys
from optimization_runner import objective
from backend.backtest.backtest_simulator import ExitRules

def test_objective_calculates_cagr_correctly():
    # Setup mock trial
    mock_trial = MagicMock(spec=optuna.Trial)
    mock_trial.number = 0
    mock_trial.user_attrs = {'win_rate': 0.0}
    mock_trial.params = {
        'min_change_1d_pct': 7.0,
    }
    mock_trial.suggest_float.side_effect = lambda name, *args, **kwargs: mock_trial.params.get(name, 1.0)
    
    config = {
        'strategy': [
            {
                'name': 'B_theme_momentum',
                'description': 'Test',
                'max_hits_per_day': 10,
                'min_change_1d_pct': 7.0,
                'min_avg_hits_per_day': 0.0, # Disable pruning
                'min_hit_rate_pct': 0.0,
                'optimization': {
                    'min_change_1d_pct': {'type': 'float', 'min': 1.0, 'max': 10.0, 'step': 1.0},
                }
            }
        ],
    }
    
    config_app = {'system': {'db_path': 'dummy'}}
    exit_rules = ExitRules()

    import optimization_runner
    original_get_cached_data = optimization_runner.get_cached_data
    original_run_strategy = optimization_runner.run_single_strategy

    # Mock cached data to be valid but empty
    df_symbols = pd.DataFrame(columns=['id', 'ticker', 'name', 'category', 'active'])
    df_prices = pd.DataFrame(columns=['symbol_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap'])
    df_indicators = pd.DataFrame(columns=['symbol_id', 'date', 'change_1d_pct'])
    df_ranks = pd.DataFrame(columns=['symbol_id', 'date', 'indicator_name', 'percent_rank'])
    df_tc = pd.DataFrame(columns=['theme_id', 'symbol_id'])
    trading_dates = [pd.Timestamp('2022-01-03').date(), pd.Timestamp('2022-01-04').date()]

    optimization_runner.get_cached_data = lambda *args: (df_symbols, df_prices, df_indicators, df_ranks, df_tc, trading_dates)

    # Mock run_single_strategy to return a mock multiplier
    # We want overall_strat_mult to be 1.6479 over 2.5 years
    # Period 1 (1 year): strat_multiplier = 1.2
    # Period 2 (1.5 years): strat_multiplier = 1.37325
    # Total mult = 1.2 * 1.37325 = 1.6479
    mock_run_results = [
        ({'total_trades': 10, 'win_trades': 5, 'expectancy': 2.0, 'max_drawdown_pct': -10.0, 'strat_multiplier': 1.2, 'spy_multiplier': 1.05}, []),
        ({'total_trades': 10, 'win_trades': 5, 'expectancy': 2.0, 'max_drawdown_pct': -10.0, 'strat_multiplier': 1.37325, 'spy_multiplier': 1.05}, []),
    ]
    call_count = 0
    def mock_run(*args, **kwargs):
        nonlocal call_count
        res = mock_run_results[call_count]
        call_count += 1
        return res

    optimization_runner.run_single_strategy = mock_run

    try:
        # Define periods: Bear 2022 (1 year) and Bull 2024-25 (approx 1.58 years)
        periods = [
            ("2022-01-01", "2022-12-31"), # 364 days / 365.25 = 0.996 years
            ("2024-06-01", "2025-12-31"), # 578 days / 365.25 = 1.582 years
        ]
        # Total years = 0.996 + 1.582 = 2.578 years
        # Expected CAGR = (1.6479) ** (1 / 2.578) - 1 = 21.4%
        
        objective(mock_trial, "B", config, config_app, exit_rules, periods)
        
        # Verify saved user attribute
        # We need to find the call where "port_cagr" was set
        cagr_call = next(c[0] for c in mock_trial.set_user_attr.call_args_list if c[0][0] == "port_cagr")
        cagr_val = cagr_call[1]
        
        # It should be close to 21.4 (years = 2.578)
        assert cagr_val == pytest.approx(21.4, abs=0.5)

        # Check total return attributes
        tr_call = next(c[0] for c in mock_trial.set_user_attr.call_args_list if c[0][0] == "port_total_return")
        assert tr_call[1] == pytest.approx(64.79, abs=0.1)

        spy_tr_call = next(c[0] for c in mock_trial.set_user_attr.call_args_list if c[0][0] == "spy_total_return")
        assert spy_tr_call[1] == pytest.approx(10.25, abs=0.1)

        vs_spy_tr_call = next(c[0] for c in mock_trial.set_user_attr.call_args_list if c[0][0] == "port_vs_spy_total")
        assert vs_spy_tr_call[1] == pytest.approx(54.54, abs=0.1)
    finally:
        optimization_runner.get_cached_data = original_get_cached_data
        optimization_runner.run_single_strategy = original_run_strategy
