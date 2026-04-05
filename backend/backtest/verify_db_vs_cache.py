import os
import sys
import pandas as pd
import time
from datetime import date

# Set PYTHONPATH
backend_dir = r"d:\My Documents\Programing\stocktool\backend"
sys.path.insert(0, backend_dir)

from backtest.backtest_runner import preload_data, run_single_strategy
from backtest.backtest_simulator import ExitRules
from db.database import engine, init_db

# Initialize DB
db_path = os.path.join(r"d:\My Documents\Programing\stocktool", "data", "stocktool.db")
init_db(db_path)
from db.database import engine # Re-import established engine

def verify_strategy(strategy_name, start_date, end_date):
    print(f"\n[{strategy_name}] Verifying DB vs Cache Consistency...")
    
    # 1. Load from DB
    print("  -> Loading from DB...")
    sym_db, pri_db, ind_db, rnk_db, tc_db, dates_db = preload_data(
        engine, start_date, end_date, refresh_cache=True # Force refresh
    )
    
    # 2. Load from Cache
    print("  -> Loading from Cache...")
    sym_ch, pri_ch, ind_ch, rnk_ch, tc_ch, dates_ch = preload_data(
        engine, start_date, end_date, refresh_cache=False
    )
    
    # Define strategy config (simplified name mapping if needed)
    from backtest.backtest_runner import load_config
    config = load_config(os.path.join(backend_dir, "backtest", "backtest_config.toml"))
    actual_name = strategy_name
    if strategy_name == "B": actual_name = "B_theme_momentum"
    if strategy_name == "A": actual_name = "A_momentum_breakout"
    if strategy_name == "D": actual_name = "D_ema21_pullback"
    if strategy_name == "F": actual_name = "F_elite_momentum97"
    
    strat = next((s for s in config.get("strategy", []) if s.get("name") == actual_name), None)
    if not strat:
        print(f"  FAILED: Strategy {actual_name} not found.")
        return False

    exit_rules = ExitRules(take_profit_pct=15.0, stop_loss_pct=5.0)

    # 3. Run on DB data
    res_db, _ = run_single_strategy(strat, ind_db, pri_db, rnk_db, sym_db, tc_db, dates_db, exit_rules, show_progress=False)
    
    # 4. Run on Cache data
    res_ch, _ = run_single_strategy(strat, ind_ch, pri_ch, rnk_ch, sym_ch, tc_ch, dates_ch, exit_rules, show_progress=False)
    
    # Compare
    db_trades = res_db.get("total_trades", 0)
    ch_trades = res_ch.get("total_trades", 0)
    
    print(f"  Results:")
    print(f"    DB Total Trades:    {db_trades}")
    print(f"    Cache Total Trades: {ch_trades}")
    
    if db_trades == ch_trades:
        print("  SUCCESS: Strategy results are IDENTICAL.")
        return True
    else:
        print("  FAILED: Strategy results MISMATCH.")
        return False

if __name__ == "__main__":
    test_start = "2024-06-01"
    test_end = "2024-06-30" # Shorter period for quick verification
    
    results = []
    for s in ["A", "B", "D", "F"]:
        results.append(verify_strategy(s, test_start, test_end))
    
    print("\n" + "="*40)
    print("FINAL VERIFICATION SUMMARY")
    print("="*40)
    for i, s in enumerate(["A", "B", "D", "F"]):
        status = "PASS" if results[i] else "FAIL"
        print(f"  Strategy {s}: {status}")
    print("="*40)
