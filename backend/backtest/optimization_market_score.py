"""
optimization_market_score.py — Optuna Optimizer for Market Trend Score Weights

Finds the optimal combination of weights for the 4 market trend components
(SPY Trend, Breadth, Momentum, VIX) by maximizing the Calmar Ratio (Return / MDD)
of the portfolio scenario simulation.
"""
import os
import sys
import argparse
import logging
import optuna

# Force UTF-8 environment
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Set up project path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from backend.backtest.scenario_runner import run_scenario_test
from backend.backtest.common_constraints import load_tax_rate

# Suppress Optuna verbose logging to keep terminal clean
optuna.logging.set_verbosity(optuna.logging.WARNING)

def objective(trial, consider_tax: float = 0.0):
    # 1. Suggest raw weight components (0.0 to 1.0)
    w_spy_trend = trial.suggest_float('spy_trend', 0.0, 1.0)
    w_breadth = trial.suggest_float('breadth', 0.0, 1.0)
    w_momentum = trial.suggest_float('momentum', 0.0, 1.0)
    w_vix = trial.suggest_float('vix', 0.0, 1.0)
    
    # 2. Normalize weights so they sum exactly to 1.0
    total_w = w_spy_trend + w_breadth + w_momentum + w_vix
    if total_w < 1e-5:
        return -9999.0
        
    weights = {
        'spy_trend': w_spy_trend / total_w,
        'breadth': w_breadth / total_w,
        'momentum': w_momentum / total_w,
        'vix': w_vix / total_w
    }
    
    # Suggest scaling ratio (1.0 to 3.0)
    scaling_ratio = trial.suggest_float('scaling_ratio', 1.0, 3.0, step=0.1)
    
    # 3. Run scenario test with normalized weights
    try:
        result = run_scenario_test(
            start_date="2021-03-26",
            end_date="2026-03-26",
            initial_capital=100000.0,
            max_positions=8,
            min_score=1,
            config_path="data/screener_presets_B_only.toml",
            market_weights=weights,
            scaling_ratio=scaling_ratio,
            output_dir="output/scenario_opt_temp",
            consider_tax=consider_tax
        )
        
        summary = result['summary']
        total_trades = summary.get('total_trades', 0)
        total_return_pct = summary.get('total_return_pct', 0.0)
        max_dd_dict = summary.get('max_drawdown', {})
        max_dd_pct = max_dd_dict.get('pct', 100.0) if isinstance(max_dd_dict, dict) else 100.0
        
        # Prevent division by zero and force positive DD percentage
        max_dd_pct = max(abs(max_dd_pct), 1.0)
        
        # 4. Enforce strict penalty for overly conservative/inactive parameters (under 50 trades)
        if total_trades < 50:
            return -9999.0
            
        # Calculate Calmar Ratio: Return % / Max Drawdown %
        score = total_return_pct / max_dd_pct
        
        # Real-time trial logging for user transparency
        print(f"Trial {trial.number:02d} | Score: {score:6.2f} (Ret: {total_return_pct:7.2f}%, MDD: {max_dd_pct:5.2f}%) | "
              f"Trades: {total_trades:3d} | "
              f"Ratio: {scaling_ratio:.1f} | "
              f"Weights -> SPY: {weights['spy_trend']:.2f}, Breadth: {weights['breadth']:.2f}, "
              f"Momentum: {weights['momentum']:.2f}, VIX: {weights['vix']:.2f}", flush=True)
              
        return score
        
    except Exception as e:
        print(f"Trial {trial.number:02d} failed due to: {e}", flush=True)
        return -9999.0

def main():
    parser = argparse.ArgumentParser(description='Optuna Optimizer for Market Trend Weights')
    parser.add_argument('--n-trials', type=int, default=20, help='Number of optimization trials')
    args = parser.parse_args()
    
    tax_rate = load_tax_rate()

    print("=" * 70)
    print("   Starting Market Trend Score Weights Optimization (Optuna)")
    print("   Goal: Optimize Dashboard Weights for Max Calmar Ratio")
    print(f"   Target Period: 2021-03-26 to 2026-03-26 | Trials: {args.n_trials}")
    if tax_rate > 0.0:
        print(f"   [Tax] 適用税率: {tax_rate * 100:.1f}% (consider_tax={tax_rate})")
    else:
        print("   [Tax] 税なし (consider_tax=0.0)")
    print("=" * 70, flush=True)

    study = optuna.create_study(direction='maximize')
    
    # Enforce default baseline weights as trial 0 (25% each, ratio=1.0)
    baseline_weights = {
        'spy_trend': 0.25,
        'breadth': 0.25,
        'momentum': 0.25,
        'vix': 0.25,
        'scaling_ratio': 1.0
    }
    study.enqueue_trial(baseline_weights)
    
    study.optimize(lambda t: objective(t, consider_tax=tax_rate), n_trials=args.n_trials)
    
    # Normalize the best weights
    best_params = study.best_params
    best_ratio = best_params.get('scaling_ratio', 1.0)
    
    # Extract only weight params
    raw_weights = {k: v for k, v in best_params.items() if k != 'scaling_ratio'}
    total_w = sum(raw_weights.values())
    best_weights = {k: v / total_w for k, v in raw_weights.items()}
    
    print("\n" + "=" * 70)
    print("   Optimization Complete!")
    print(f"   Best Calmar Score: {study.best_value:.4f}")
    print(f"   Best Scaling Ratio (ratio): {best_ratio:.2f}")
    print("   Best Market Weights Configuration (100% compatible with dashboard):")
    for k, v in best_weights.items():
        print(f"     -> {k:15s}: {v:.4f} ({v*100:.1f}%)")
    print("=" * 70, flush=True)

if __name__ == '__main__':
    main()
