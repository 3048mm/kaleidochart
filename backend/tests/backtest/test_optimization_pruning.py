import pytest
from unittest.mock import MagicMock
import pandas as pd
import optuna
from optimization_runner import objective
from backend.backtest.backtest_simulator import ExitRules

def test_objective_applies_fast_pruning():
    # 1. Mock the Optuna trial
    mock_trial = MagicMock(spec=optuna.Trial)
    mock_trial.number = 0
    mock_trial.user_attrs = {'win_rate': 0.0}
    # mock_trial.params returns parameters that will yield very few trades
    mock_trial.params = {
        'min_change_1d_pct': 9.9,  # Extremely strict
        'min_vol_surge_21': 9.9,
    }
    mock_trial.suggest_float.side_effect = lambda name, *args, **kwargs: mock_trial.params.get(name, 1.0)
    mock_trial.suggest_categorical.side_effect = lambda name, *args, **kwargs: mock_trial.params.get(name, 'none')

    # 2. Config mock setup
    config = {
        'strategy': [
            {
                'name': 'B_theme_momentum',
                'description': 'Test',
                'max_hits_per_day': 10,
                'min_change_1d_pct': 7.0,
                'min_vol_surge_21': 2.8,
                'min_avg_hits_per_day': 1.0,  # Require 1.0 avg trades per day
                'min_hit_rate_pct': 5.0,
            }
        ],
        'optimization': {
            'B': {
                'min_change_1d_pct': {'type': 'float', 'min': 1.0, 'max': 10.0, 'step': 1.0},
                'min_vol_surge_21': {'type': 'float', 'min': 0.5, 'max': 10.0, 'step': 0.1},
            }
        }
    }
    
    config_app = {
        'system': {'db_path': 'dummy'}
    }

    # Exit rules setup
    exit_rules = ExitRules()

    # Stub get_cached_data to return minimal mock dataframes that result in 0 trades
    # 0 trades will trigger fast prune
    import optimization_runner
    original_get_cached_data = optimization_runner.get_cached_data
    
    df_symbols = pd.DataFrame(columns=['id', 'ticker', 'name', 'category', 'active'])
    df_prices = pd.DataFrame(columns=['symbol_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap'])
    df_indicators = pd.DataFrame(columns=['symbol_id', 'date', 'change_1d_pct', 'vol_surge_21'])
    df_ranks = pd.DataFrame(columns=['symbol_id', 'date'])
    df_tc = pd.DataFrame(columns=['theme_id', 'symbol_id'])
    trading_dates = [pd.Timestamp('2022-01-03').date(), pd.Timestamp('2022-01-04').date()]

    optimization_runner.get_cached_data = lambda *args: (df_symbols, df_prices, df_indicators, df_ranks, df_tc, trading_dates)

    try:
        # Run objective function with 1 period
        periods = [("2022-01-01", "2022-12-31")]
        
        # シグナル0件なので fast_prune が発火し、通常スコアではなく重い罰点が返ること。
        score = objective(mock_trial, "B", config, config_app, exit_rules, periods)

        # 2026-08-15: fast_prune の発火点を min_avg_hits_per_day(1.0) から
        # prune_floor_hits_per_day(既定 0.2) へ引き下げた（resolve_prune_floor）。
        # fast_prune は学習時間短縮のための速度の安全弁であって評価の門番ではなく、
        # 1.0 に置かれていたことで「Bull で健全でも Bear が僅かに薄いだけで
        # スコアが確定する」崖になっていたため（計画書 §7 / issue_list 参照）。
        #   旧: base_penalty - (1.0 - 0.0) * 2000.0 = -2100.0
        #   新: base_penalty - (0.2 - 0.0) * 2000.0 =  -500.0
        # 罰点の「大きさ」ではなく「0件なら足切りされる」ことがこのテストの本旨なので、
        # 期待値は式から導出して固定する。
        from optimization_runner import calculate_prune_penalty, resolve_prune_floor
        prune_conf = config.get('optimization_pruning', {})
        floor = resolve_prune_floor({}, prune_conf, min_avg=1.0)
        expected = calculate_prune_penalty(0.0, hit_rate_pct=0.0,
                                           bounds=(floor, 5.0, 5.0))
        assert score == expected
        assert score < 0
    finally:
        optimization_runner.get_cached_data = original_get_cached_data
