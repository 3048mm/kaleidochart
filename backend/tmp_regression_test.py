import os
import sys
import tomli
import time

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

from optimization_runner import get_cached_data, calculate_custom_score

def run_regression_test():
    print("=" * 60)
    print("Regression Test (Target: 2025-01-01 ~ 2025-12-31)")
    print("=" * 60)
    
    config_path = os.path.join(backtest_dir, 'backtest_config.toml')
    with open(config_path, 'rb') as f:
        config = tomli.load(f)

    # Set dates
    start_date = "2024-01-01"
    end_date = "2024-12-31"
    config['general']['start_date'] = start_date
    config['general']['end_date'] = end_date

    baseline_strat_d = next(s for s in config['strategy'] if s['name'] == 'D_ema21_pullback')
    exit_rules = ExitRules.from_config(config)

    # Database
    project_root = os.path.dirname(backend_dir)
    config_path_app = os.path.join(project_root, 'config.toml')
    with open(config_path_app, 'rb') as f:
        app_config = tomli.load(f)
    db_path = os.path.join(project_root, app_config['system']['db_path'])
    init_db(db_path)

    # Load cache (similar to optimization_runner)
    t0 = time.time()
    df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = get_cached_data(app_config, start_date, end_date)
    print(f"Data Cached. ({(time.time() - t0):.1f}s)")

    print("\n--- Test 1: Direct Execution (Simulating Standalone Output) ---")
    metrics_direct, trades_direct = run_single_strategy(
        baseline_strat_d, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, 
        trading_dates, exit_rules, show_progress=False
    )
    direct_return = metrics_direct.get('total_return_pct', 0)
    direct_trades = metrics_direct.get('total_trades', 0)
    print(f"  Total Trades: {direct_trades}")
    print(f"  Total Return: {direct_return}%")

    print("\n--- Test 2: Optuna Evaluation (Baseline config via optimization_runner logic) ---")
    optuna_strat_d = {
        "name": "D_Trial_Test",
        "description": "Baseline verification",
        "min_dist_21ema_pct": -2.0,
        "max_dist_21ema_pct": 2.0,
        "max_dist_sma50_atr": 4.0,
        "min_rs_ratio_21_rank": 0.7,
        "min_market_cap": 1e9,
        "trend_template_ok": 1
    }
    metrics_opt, trades_opt = run_single_strategy(
        optuna_strat_d, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, 
        trading_dates, exit_rules, show_progress=False
    )
    opt_return = metrics_opt.get('total_return_pct', 0)
    opt_trades = metrics_opt.get('total_trades', 0)
    print(f"  Total Trades: {opt_trades}")
    print(f"  Total Return: {opt_return}%")

    print("\n--- Results ---")
    if direct_trades == opt_trades and abs(direct_return - opt_return) < 0.01:
        print(">> REGRESSION TEST PASSED! Same parameters yield exactly identical outputs.")
    else:
        print(">> REGRESSION TEST FAILED!")

if __name__ == "__main__":
    run_regression_test()
