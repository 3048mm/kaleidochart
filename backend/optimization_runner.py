"""
optimization_runner.py — Optuna-based Hyperparameter Optimization for Backtesting
"""
import os
import sys
import argparse
import tomli
from datetime import date as dt_date
import pandas as pd

import optuna
import logging

# Add backend directory to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
backtest_dir = os.path.join(backend_dir, 'backtest')
if backtest_dir not in sys.path:
    sys.path.insert(0, backtest_dir)

from backtest.backtest_runner import preload_data, run_single_strategy
from backtest.backtest_simulator import ExitRules
from db.database import init_db
from db import database

# Configure optuna logging
optuna.logging.set_verbosity(optuna.logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary for cached data per period
_cached_data_dict = {}

def get_cached_data(config_app, start_date, end_date):
    global _cached_data_dict
    period_key = f"{start_date}_{end_date}"
    if period_key in _cached_data_dict:
        return _cached_data_dict[period_key]
        
    print(f"Preloading data from {start_date} to {end_date} for optimization...")
    res = preload_data(database.engine, start_date, end_date, refresh_cache=False)
    _cached_data_dict[period_key] = res
    print("Data preload complete.")
    return res

def calculate_custom_score(metrics, total_trading_days):

    """
    Calculate optimization score. Higher = Better.
    Provides heavy penalties for:
    - Less than 5 trades (too rare)
    - More than 25 trades per day on average
    """
    if not metrics:
        return -1000.0

    trades_count = metrics.get('total_trades', 0)
    if trades_count < 5:
        # Give a gradient so Optuna knows if it's getting closer
        # e.g., 0 trades = -100.0, 1 trade = -80.0, ..., 4 trades = -20.0
        return -100.0 + (trades_count * 20.0)
    avg_trades_per_day = trades_count / total_trading_days
    
    # Penalty for too many trades (e.g. user requested < 50 cases/day, 
    # we use average of > 25/day as a severe penalty threshold as 50 is extremely high)
    if avg_trades_per_day >= 50:
        return -500.0 - (avg_trades_per_day * 10)
    elif avg_trades_per_day >= 25:
        # Penalize moderately if between 25 and 50
        return -100.0 - (avg_trades_per_day * 2)
        
    profit_pct = metrics.get('total_return_pct', 0.0)
    max_dd = abs(metrics.get('max_drawdown_pct', 0.0))
    
    # Hard penalty for severe drawdown
    if max_dd > 35.0:  
        return -max_dd * 10

    if profit_pct <= 0:
        return profit_pct
        
    # Custom Score: Profit / (Drawdown + 1)
    score = (profit_pct * 100) / (max_dd + 1.0)
    
    # Minor penalization for volume of trades to prefer efficiency (higher win rate, fewer trades)
    # If trades are > 1 per day, softly reduce the score
    if avg_trades_per_day > 1.0:
        score = score / (avg_trades_per_day ** 0.5)

    return score

def objective(trial: optuna.Trial, strategy_type: str, config, config_app, exit_rules: ExitRules, periods: list):
    # Mapping for short codes used by Optuna study names
    full_names = {
        'B': 'B_theme_momentum',
        'D': 'D_ema21_pullback',
        'F': 'F_elite_momentum97'
    }
    actual_name = full_names.get(strategy_type, strategy_type)
    
    # Extract the base strategy configuration from the list in config['strategy']
    strat_base = next((s for s in config.get('strategy', []) if s.get('name') == actual_name), None)
    if not strat_base:
        raise ValueError(f"Strategy '{actual_name}' not found in backtest config.")
        
    # Copy strategy config to preserve baseline settings (like market_cap etc)
    strat = strat_base.copy()
    strat['name'] = f"{actual_name}_Trial_{trial.number}"

    if strategy_type == "B":
        strat['min_1d_gain_pct'] = trial.suggest_float("min_1d_gain_pct", 1.0, 5.0, step=0.5)
        strat['min_vol_surge_21'] = trial.suggest_float("min_vol_surge_21", 0.5, 2.0, step=0.1)
        strat['min_adr_pct_21'] = trial.suggest_float("min_adr_pct_21", 2.0, 6.0, step=0.5)
        strat['max_dist_sma50_atr'] = trial.suggest_float("max_dist_sma50_atr", 3.0, 8.0, step=0.5)
        strat['min_market_cap'] = trial.suggest_categorical("min_market_cap", [1e7, 5e7, 1e8, 3e8, 5e8, 1e9])
        strat['theme_rs21_gt_63'] = trial.suggest_categorical("theme_rs21_gt_63", [True, False])
    elif strategy_type == "D":
        strat['description'] = "Optuna D: EMA21 Pullback"
        strat['min_dist_21ema_pct'] = trial.suggest_float("min_dist_21ema_pct", -4.0, -1.0, step=0.5)
        strat['max_dist_21ema_pct'] = trial.suggest_float("max_dist_21ema_pct", 0.5, 4.0, step=0.5)
        strat['max_dist_sma50_atr'] = trial.suggest_float("max_dist_sma50_atr", 2.0, 6.0, step=0.5)
        strat['min_rs_ratio_21_rank'] = trial.suggest_float("min_rs_ratio_21_rank", 0.50, 0.90, step=0.05)
        strat['min_market_cap'] = trial.suggest_categorical("min_market_cap", [1e8, 3e8, 5e8, 1e9])
        strat['trend_template_ok'] = trial.suggest_categorical("trend_template_ok", [1])
    elif strategy_type == "F":
        strat['description'] = "Optuna F: Elite Momentum"
        strat['min_rs_ratio_21_rank'] = trial.suggest_float("min_rs_ratio_21_rank", 0.85, 0.99, step=0.01)
        strat['min_market_cap'] = trial.suggest_categorical("min_market_cap", [1e7, 1e8, 3e8, 5e8, 1e9])
        strat['min_adr_pct_21'] = trial.suggest_float("min_adr_pct_21", 1.0, 4.0, step=0.5)
        strat['trend_template_ok'] = trial.suggest_categorical("trend_template_ok", [1, None])
    else:
        raise ValueError(f"Unknown strategy_type: {strategy_type}")
        
    total_score = 0.0
    total_trades = 0
    total_wins = 0
    total_return_pct_sum = 0.0
    max_dd_overall = 0.0
    
    for start_date, end_date in periods:
        df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = get_cached_data(config_app, start_date, end_date)
        metrics, _ = run_single_strategy(
            strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, 
            trading_dates, exit_rules, show_progress=False
        )
        period_score = calculate_custom_score(metrics, len(trading_dates))
        total_score += period_score
        
        if metrics:
            total_trades += metrics.get('total_trades', 0)
            total_wins += metrics.get('win_trades', 0)
            total_return_pct_sum += metrics.get('total_return_pct', 0.0)
            max_dd_overall = min(max_dd_overall, metrics.get('max_drawdown_pct', 0.0))
            
    # Average score across periods
    avg_score = total_score / len(periods)
    
    # Log aggregated metrics
    trial.set_user_attr("total_trades", total_trades)
    if total_trades > 0:
        trial.set_user_attr("win_rate", (total_wins / total_trades) * 100)
    else:
        trial.set_user_attr("win_rate", 0.0)
    trial.set_user_attr("total_return", total_return_pct_sum)
    trial.set_user_attr("max_drawdown", max_dd_overall)

    return avg_score

def main():
    parser = argparse.ArgumentParser(description="Optimize backtest parameters with Optuna")
    parser.add_argument("--strategy", type=str, choices=["B", "D", "F"], required=True)
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()
    
    # Use config just to load exit rules
    config_path = os.path.join(backend_dir, 'backtest', 'backtest_config.toml')
    with open(config_path, 'rb') as f:
        config = tomli.load(f)
    exit_rules = ExitRules.from_config(config)
    
    # Init DB
    project_root = os.path.dirname(backend_dir)
    config_path_app = os.path.join(project_root, 'config.toml')
    with open(config_path_app, 'rb') as f:
        config_app = tomli.load(f)
    db_path_main = os.path.join(project_root, config_app['system']['db_path'])
    init_db(db_path_main)
    
    # Defined periods: 2022 (Bear), 2025 (Bull)
    periods = [
        ("2022-01-01", "2022-12-31"),
        ("2024-06-01", "2025-12-31")  # Added some 2024 to catch 2025 nicely
    ]
    
    # Setup storage
    db_path = os.path.join(project_root, 'data', 'optimization_trials.db')
    storage = f"sqlite:///{db_path}"
    study_name = f"opt_strategy_{args.strategy}_multi_period"
    
    print("=" * 60)
    print(f"  Optuna Optimization Runner: Strategy {args.strategy}")
    print(f"  Periods: {periods}")
    print("=" * 60)
    
    study = optuna.create_study(
        study_name=study_name, 
        storage=storage, 
        load_if_exists=True,
        direction="maximize"
    )
    
    study.optimize(
        lambda t: objective(t, args.strategy, config, config_app, exit_rules, periods),
        n_trials=args.trials
    )
    
    print("-" * 60)
    print("Optimization finished.")
    print("Best trial:")
    trial = study.best_trial
    print(f"  Value (Score): {trial.value:.2f}")
    print("  Params: ")
    for key, value in trial.params.items():
        print(f"    {key}: {value}")
        
    if "total_return" in trial.user_attrs:
        print(f"  Total Return: {trial.user_attrs['total_return']:.1f}%")
        print(f"  Max Drawdown: {trial.user_attrs['max_drawdown']:.1f}%")
        print(f"  Win Rate:     {trial.user_attrs['win_rate']:.1f}%")
        print(f"  Total Trades: {trial.user_attrs['total_trades']}")

if __name__ == "__main__":
    main()
