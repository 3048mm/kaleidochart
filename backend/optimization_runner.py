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
    print("Data preload complete.", flush=True)
    return res

def calculate_custom_score(metrics, total_trading_days, max_allowed_dd: float = 20.0):
    """
    Calculate optimization score. Higher = Better.
    
    Primary metric: expectancy (avg gain per trade).
    
    Penalties:
    - Less than 5 trades (too rare to be statistically meaningful)
    - More than 25 trades per day on average (too noisy / over-fitted)
    - Threshold-based squared penalty for drawdowns exceeding max_allowed_dd
    """
    if not metrics:
        return -1000.0

    trades_count = metrics.get('total_trades', 0)
    if trades_count < 5:
        # Gradient so Optuna knows if it's getting closer
        return -100.0 + (trades_count * 20.0)
    avg_trades_per_day = trades_count / total_trading_days
    
    # Penalty for too many trades (noise / over-fitting)
    if avg_trades_per_day >= 50:
        return -500.0 - (avg_trades_per_day * 10)
    elif avg_trades_per_day >= 25:
        return -100.0 - (avg_trades_per_day * 2)
        
    # --- Primary scoring: expectancy (average %Gain per trade) ---
    expectancy = metrics.get('expectancy', 0.0)
    # The max_drawdown_pct received here is now already Portfolio-Equivalent (Normalized via Little's Law)
    normalized_dd = abs(metrics.get('max_drawdown_pct', 0.0))

    if expectancy <= 0:
        # マイナス期待値ならドローダウンが深いほどさらにマイナス
        return (expectancy * 10) - normalized_dd

    # --- しきい値付き2乗ペナルティ計算 ---
    if normalized_dd <= max_allowed_dd:
        # 閾値内ならマイルドな割引
        penalty = 1.0 + (normalized_dd ** 0.5) * 0.1
    else:
        # 閾値を超えたら、超過分を2乗して急激にペナルティを増大させる
        excess = normalized_dd - max_allowed_dd
        penalty = 1.0 + (max_allowed_dd ** 0.5) * 0.1 + (excess ** 2) * 1.5
        
    score = (expectancy * 100.0) / penalty
    
    # 1日あたりの取引回数が多すぎる場合は期待値をさらに割り引く(10件まではノーペナルティ)
    if avg_trades_per_day > 10.0:
        score = score / ((avg_trades_per_day / 10.0) ** 0.5)

    return score

def calculate_prune_penalty(avg_hits: float, hit_rate_pct: float, bounds: tuple):
    """
    Calculate directional penalty when a trial fails pruning condition.
    bounds = (min_avg, max_avg, min_hit_rate_pct)
    Allows Optuna's TPE to infer whether to tighten or relax conditions.
    """
    min_avg, max_avg, min_hit_rate = bounds
    
    # Base penalty offset inside the forbidden zone
    base_penalty = -100.0
    
    if avg_hits < min_avg:
        # Too few hits. Penalty gets much worse as it reaches 0.
        return base_penalty - ((min_avg - avg_hits) * 2000.0)
        
    if avg_hits > max_avg:
        # Too many hits. Penalty gets linearly worse.
        return base_penalty - ((avg_hits - max_avg) * 50.0)
        
    if hit_rate_pct < min_hit_rate:
        # Hit rate ratio too low.
        return base_penalty - ((min_hit_rate - hit_rate_pct) * 100.0)
        
    return None

# =============================================================
# TOML-driven parameter parsing functions (testable, pure)
# =============================================================

def parse_optimization_params(config, strategy_short):
    """Parse optimization parameter definitions from TOML config.
    
    Args:
        config: Parsed TOML config dict (must contain 'optimization' section).
        strategy_short: Short strategy name (e.g. 'B', 'D', 'F').
    
    Returns:
        List of parameter definition dicts, each containing:
        - name: parameter name
        - type: 'float', 'int', or 'categorical'
        - For float/int: min, max, step
        - For categorical: choices (with 'none' strings converted to Python None)
    
    Raises:
        ValueError: If strategy_short is not defined in [optimization.*] section.
    """
    opt_section = config.get('optimization', {})
    if strategy_short not in opt_section:
        raise ValueError(
            f"No [optimization.{strategy_short}] section found in config. "
            f"Available: {list(opt_section.keys())}"
        )
    
    raw_params = opt_section[strategy_short]
    result = []
    for param_name, param_def in raw_params.items():
        entry = {'name': param_name, 'type': param_def['type']}
        if param_def['type'] in ('float', 'int'):
            entry['min'] = param_def['min']
            entry['max'] = param_def['max']
            entry['step'] = param_def['step']
        elif param_def['type'] == 'categorical':
            # Convert string 'none' to Python None
            entry['choices'] = [
                None if (isinstance(v, str) and v.lower() == 'none') else v
                for v in param_def['choices']
            ]
        result.append(entry)
    return result


def apply_trial_params(trial, param_defs, strat):
    """Apply Optuna trial suggestions to strategy dict based on param definitions.
    
    Args:
        trial: Optuna Trial object.
        param_defs: List of parameter definitions from parse_optimization_params().
        strat: Strategy dict to update in-place.
    """
    for p in param_defs:
        name = p['name']
        if p['type'] == 'float':
            strat[name] = trial.suggest_float(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'int':
            strat[name] = trial.suggest_int(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'categorical':
            strat[name] = trial.suggest_categorical(name, p['choices'])


def parse_optimization_periods(config):
    """Parse optimization evaluation periods from TOML config.
    
    Args:
        config: Parsed TOML config dict.
    
    Returns:
        List of (start_date, end_date) tuples.
    
    Raises:
        ValueError: If optimization_periods section is missing.
    """
    if 'optimization_periods' not in config:
        raise ValueError(
            "No [optimization_periods] section found in config. "
            "Please define evaluation periods in backtest_config.toml."
        )
    raw_periods = config['optimization_periods']['periods']
    return [(p['start'], p['end']) for p in raw_periods]


def enqueue_baseline_trial(study, config: dict, strategy_short: str) -> bool:
    """
    Extract baseline default parameters for a strategy from TOML config
    and enqueue them as the first trial in the study.
    
    Args:
        study: The Optuna Study object.
        config: The parsed TOML config dict.
        strategy_short: Short strategy name/code (e.g., 'B1', 'B2', 'B', 'D').
        
    Returns:
        bool: True if trial was successfully enqueued, False otherwise.
    """
    full_names = {
        'A': 'A_momentum_breakout',
        'B': 'B_theme_momentum',
        'C1': 'C1_rrg_leading_in',
        'C2': 'C2_rrg_improving_in',
        'D': 'D_ema21_pullback',
        'E': 'E_vcp',
        'F': 'F_elite_momentum97'
    }
    
    # Try to find the actual name
    if strategy_short in full_names:
        actual_name = full_names[strategy_short]
    else:
        strategies = config.get('strategy', [])
        found = next((s['name'] for s in strategies if s['name'] == strategy_short or s['name'].startswith(strategy_short + "_")), None)
        actual_name = found if found else strategy_short
        
    # Extract the base strategy configuration
    strat_base = next((s for s in config.get('strategy', []) if s.get('name') == actual_name), None)
    if not strat_base:
        raise ValueError(f"Strategy '{actual_name}' not found in backtest config.")
        
    # Parse optimization params from TOML
    try:
        param_defs = parse_optimization_params(config, strategy_short)
    except ValueError:
        return False
        
    # Extract baseline parameters from the default strategy config
    default_params = {}
    for p in param_defs:
        name = p['name']
        if name in strat_base:
            default_params[name] = strat_base[name]
            
    if default_params:
        study.enqueue_trial(default_params)
        return True
    return False


# =============================================================
# Optuna objective function (TOML-driven)
# =============================================================

def objective(trial: optuna.Trial, strategy_type: str, config, config_app, exit_rules: ExitRules, periods: list):
    import traceback
    try:
        # Mapping for short codes (fallback to searching config if not in map)
        full_names = {
            'A': 'A_momentum_breakout',
            'B': 'B_theme_momentum',
            'C1': 'C1_rrg_leading_in',
            'C2': 'C2_rrg_improving_in',
            'D': 'D_ema21_pullback',
            'E': 'E_vcp',
            'F': 'F_elite_momentum97'
        }
        
        # Try to find the actual name from the mapping or searching the strategy list
        if strategy_type in full_names:
            actual_name = full_names[strategy_type]
        else:
            # Search for a strategy that matches exactly or starts with "STRATEGY_"
            strategies = config.get('strategy', [])
            found = next((s['name'] for s in strategies if s['name'] == strategy_type or s['name'].startswith(strategy_type + "_")), None)
            actual_name = found if found else strategy_type
        
        # Extract the base strategy configuration from the list in config['strategy']
        strat_base = next((s for s in config.get('strategy', []) if s.get('name') == actual_name), None)
        if not strat_base:
            raise ValueError(f"Strategy '{actual_name}' not found in backtest config.")
            
        # Copy strategy config to preserve baseline settings (like market_cap etc)
        strat = strat_base.copy()
        strat['name'] = f"{actual_name}_Trial_{trial.number}"

        # Parse optimization params from TOML and apply via Optuna trial
        param_defs = parse_optimization_params(config, strategy_type)
        apply_trial_params(trial, param_defs, strat)
        # Extract prune bounds from config if available (allow strategy-specific overrides)
        prune_conf = config.get('optimization_pruning', {})
        min_avg = strat_base.get('min_avg_hits_per_day', prune_conf.get('min_avg_hits_per_day', 1.0))
        max_avg = strat_base.get('max_avg_hits_per_day', prune_conf.get('max_avg_hits_per_day', 15.0))
        min_hit_rate = strat_base.get('min_hit_rate_pct', prune_conf.get('min_hit_rate_pct', 5.0))
        prune_bounds = (min_avg, max_avg, min_hit_rate)
        
        # Extract tax and drawdown threshold options
        max_allowed_dd = strat_base.get('max_allowed_dd', 20.0)
        consider_tax = float(config.get('general', {}).get('consider_tax', 0.0))
        
        total_score = 0.0
        total_trades = 0
        total_wins = 0
        expectancy_sum = 0.0
        avg_gain_sum = 0.0
        avg_spy_gain_sum = 0.0
        max_dd_overall = 0.0
        periods_with_trades = 0
        
        overall_strat_mult = 1.0
        overall_spy_mult = 1.0
        
        for start_date, end_date in periods:
            df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = get_cached_data(config_app, start_date, end_date)
            metrics, _ = run_single_strategy(
                strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, 
                trading_dates, exit_rules, show_progress=True, 
                fast_prune=False, prune_bounds=prune_bounds, consider_tax=consider_tax
            )
            
            # --- Handle directional penalty branching ---
            if isinstance(metrics, dict) and metrics.get('fast_pruned'):
                penalty = calculate_prune_penalty(
                    metrics['avg_per_day'], 
                    metrics['hit_rate_pct'], 
                    prune_bounds
                )
                if penalty is not None:
                    # We return the heavy penalty immediately (skip remaining periods)
                    # so Optuna TPE learns the gradient.
                    return penalty
                else:
                    # Fallback, theoretically shouldn't reach if bounds logic matched
                    raise optuna.TrialPruned()
                
            period_score = calculate_custom_score(metrics, len(trading_dates), max_allowed_dd)
            total_score += period_score
            
            if metrics:
                total_trades += metrics.get('total_trades', 0)
                total_wins += metrics.get('win_trades', 0)
                max_dd_overall = min(max_dd_overall, metrics.get('max_drawdown_pct', 0.0))
                if metrics.get('total_trades', 0) > 0:
                    expectancy_sum += metrics.get('expectancy', 0.0)
                    avg_gain_sum += metrics.get('avg_gain', 0.0)
                    avg_spy_gain_sum += metrics.get('avg_spy_gain', 0.0)
                    overall_strat_mult *= metrics.get('strat_multiplier', 1.0)
                    overall_spy_mult *= metrics.get('spy_multiplier', 1.0)
                    periods_with_trades += 1
                
        # Average score across periods
        avg_score = total_score / len(periods)
        
        # Log aggregated metrics
        trial.set_user_attr("total_trades", total_trades)
        if total_trades > 0:
            trial.set_user_attr("win_rate", (total_wins / total_trades) * 100)
        else:
            trial.set_user_attr("win_rate", 0.0)
        
        if periods_with_trades > 0:
            avg_expectancy = expectancy_sum / periods_with_trades
            avg_gain = avg_gain_sum / periods_with_trades
            avg_spy = avg_spy_gain_sum / periods_with_trades
            trial.set_user_attr("expectancy", round(avg_expectancy, 3))
            trial.set_user_attr("avg_gain", round(avg_gain, 3))
            trial.set_user_attr("avg_spy_gain", round(avg_spy, 3))
            trial.set_user_attr("alpha", round(avg_gain - avg_spy, 3))
        else:
            trial.set_user_attr("expectancy", 0.0)
            trial.set_user_attr("avg_gain", 0.0)
            trial.set_user_attr("avg_spy_gain", 0.0)
            trial.set_user_attr("alpha", 0.0)
            
        portfolio_cagr = (overall_strat_mult - 1.0) * 100.0
        spy_bh_cagr = (overall_spy_mult - 1.0) * 100.0
        portfolio_vs_spy = portfolio_cagr - spy_bh_cagr

        trial.set_user_attr("max_drawdown", max_dd_overall)
        trial.set_user_attr("port_cagr", round(portfolio_cagr, 2))
        trial.set_user_attr("spy_cagr", round(spy_bh_cagr, 2))
        trial.set_user_attr("port_vs_spy", round(portfolio_vs_spy, 2))

        # Generate TOML format string for parameters (for easy copy-paste)
        toml_params = []
        for key, value in trial.params.items():
            if isinstance(value, bool):
                toml_val = "true" if value else "false"
            elif isinstance(value, str):
                toml_val = f'"{value}"'
            else:
                toml_val = value
            toml_params.append(f"{key} = {toml_val}")
        
        params_toml_str = "\n".join(toml_params)
        # Key renamed to 'z_params_toml' so it sorts to the very end of the list in Optuna Dashboard
        trial.set_user_attr("z_params_toml", params_toml_str)
        # Also set as system attribute 'note' for Optuna Dashboard
        trial.set_system_attr("note", params_toml_str)

        # --- Trial Summary Log ---
        print(f"  [Trial {trial.number}] Score: {avg_score:.2f} | Port vs SPY: {portfolio_vs_spy:+.2f}% | MaxDD: {max_dd_overall:.1f}% | Trades: {total_trades} | WinRate: {trial.user_attrs['win_rate']:.1f}%", flush=True)

        return avg_score
    except Exception as e:
        print(f"\n!!! EXCEPTION IN TRIAL {trial.number} !!!", flush=True)
        traceback.print_exc()
        raise e

def main():
    parser = argparse.ArgumentParser(description="Optimize backtest parameters with Optuna")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy name or short code (A, B, C...)")
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()
    
    # Use config just to load exit rules
    config_path = os.path.join(backend_dir, 'backtest', 'backtest_config.toml')
    with open(config_path, 'rb') as f:
        config = tomli.load(f)
    exit_rules = ExitRules.from_config(config)

    # Validate strategy parameters in configuration
    try:
        from backtest.backtest_runner import validate_strategies_config, print_validation_warnings
        dummy_df = pd.DataFrame()
        warnings = validate_strategies_config(config.get('strategy', []), dummy_df, dummy_df)
        if warnings:
            print_validation_warnings(warnings)
    except Exception as e:
        print(f"Warning during config validation: {e}")
    
    # Init DB
    project_root = os.path.dirname(backend_dir)
    config_path_app = os.path.join(project_root, 'config.toml')
    with open(config_path_app, 'rb') as f:
        config_app = tomli.load(f)
    db_path_main = os.path.join(project_root, config_app['system']['db_path'])
    init_db(db_path_main)
    
    # Load periods from TOML config
    periods = parse_optimization_periods(config)
    
    # Setup storage
    db_path = os.path.join(project_root, 'data', 'optimization_trials.db')
    
    # 1. Ensure directories exist
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
    # 2. Use RDBStorage with high timeout to prevent "database is locked" errors
    from optuna.storages import RDBStorage
    storage = RDBStorage(
        url=f"sqlite:///{db_path}",
        engine_kwargs={
            "connect_args": {"timeout": 60.0} # Extend timeout from default 5s to 60s
        }
    )
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
    
    try:
        enqueued = enqueue_baseline_trial(study, config, args.strategy)
        if enqueued:
            print("  Enqueued baseline trial from default config values.")
    except Exception as e:
        print(f"  Warning: Could not enqueue baseline trial: {e}")
        
    study.optimize(
        lambda t: objective(t, args.strategy, config, config_app, exit_rules, periods),
        n_trials=args.trials
    )
    
    print("-" * 60)
    print("Optimization finished.")
    print("Best trial:")
    trial = study.best_trial
    print(f"  Value (Score): {trial.value:.2f}")
    print("\n[TOML Params for copy-paste]")
    print("-" * 30)
    for key, value in trial.params.items():
        if isinstance(value, bool):
            toml_val = "true" if value else "false"
        elif isinstance(value, str):
            toml_val = f'"{value}"'
        else:
            toml_val = value
        print(f"{key} = {toml_val}")
    print("-" * 30 + "\n")
        
    if "expectancy" in trial.user_attrs:
        print(f"  Expectancy:     {trial.user_attrs['expectancy']:.3f}%")
        print(f"  Avg Gain/Trade: {trial.user_attrs['avg_gain']:.3f}%")
        print(f"  Avg SPY Gain:   {trial.user_attrs['avg_spy_gain']:.3f}%")
        print(f"  Alpha:          {trial.user_attrs.get('alpha', 0):.3f}%")
        print(f"  Max Drawdown:   {trial.user_attrs.get('max_drawdown', 0):.1f}%")
        print(f"  Win Rate:       {trial.user_attrs.get('win_rate', 0):.1f}%")
        print(f"  Total Trades:   {trial.user_attrs.get('total_trades', 0)}")
        print(f"  Port CAGR:      {trial.user_attrs.get('port_cagr', 0):.2f}%")
        print(f"  SPY B&H CAGR:   {trial.user_attrs.get('spy_cagr', 0):.2f}%")
        print(f"  Port vs SPY:    {trial.user_attrs.get('port_vs_spy', 0):.2f}%")

if __name__ == "__main__":
    main()
