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

    Primary metric: expectancy_lcb（期待値の下側信頼限界 = expectancy − 2×SE）。
    少数トレード×高分散の「まぐれ」は SE 増大で自動的に沈み、
    件数を伴って安定して勝つパラメータほど高スコアになる。
    （metrics に expectancy_lcb が無い旧呼び出し元では expectancy にフォールバック）

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
        
    # --- Primary scoring: expectancy の下側信頼限界（LCB）---
    expectancy = metrics.get('expectancy_lcb', metrics.get('expectancy', 0.0))
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

def resolve_strategy(config, strategy_name):
    """
    Find the strategy configuration dict and actual name in backtest_config.toml.
    Supports both exact match (full name) and legacy short code/prefix fallback.
    """
    strategies = config.get('strategy', [])
    
    # 1. Exact match
    strat = next((s for s in strategies if s.get('name') == strategy_name), None)
    if strat:
        return strat, strategy_name
        
    # 2. Legacy short code fallback mapping
    full_names = {
        'A': 'A_momentum_breakout',
        'B': 'B_theme_momentum',
        'C1': 'C1_rrg_leading_in',
        'C2': 'C2_rrg_improving_in',
        'D': 'D_ema21_pullback',
        'E': 'E1_vcp',  # Map legacy 'E' to 'E1_vcp'
        'F': 'F_elite_momentum97'
    }
    legacy_name = full_names.get(strategy_name)
    if legacy_name:
        strat = next((s for s in strategies if s.get('name') == legacy_name), None)
        if strat:
            return strat, legacy_name
            
    # 3. Wildcard prefix check (e.g., strategy_name='B4' matches 'B4_rs_trend_with_theme')
    found_name = next((s['name'] for s in strategies if s.get('name', '').startswith(strategy_name + "_")), None)
    if found_name:
        strat = next((s for s in strategies if s.get('name') == found_name), None)
        return strat, found_name
        
    return None, None


def parse_optimization_params(config, strategy_name):
    """Parse optimization parameter definitions from TOML config.
    
    Args:
        config: Parsed TOML config dict.
        strategy_name: Strategy name (e.g. 'B4_rs_trend_with_theme').
    
    Returns:
        List of parameter definition dicts.
    
    Raises:
        ValueError: If strategy is not found in config.
    """
    strat_base, actual_name = resolve_strategy(config, strategy_name)
    if not strat_base:
        raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
    raw_params = strat_base.get('optimization', {})
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
    """Apply Optuna trial suggestions to strategy dict based on param definitions."""
    for p in param_defs:
        name = p['name']
        if p['type'] == 'float':
            strat[name] = trial.suggest_float(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'int':
            strat[name] = trial.suggest_int(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'categorical':
            strat[name] = trial.suggest_categorical(name, p['choices'])


def parse_optimization_periods(config):
    """Parse optimization evaluation periods from TOML config."""
    if 'optimization_periods' not in config:
        raise ValueError(
            "No [optimization_periods] section found in config. "
            "Please define evaluation periods in backtest_config.toml."
        )
    raw_periods = config['optimization_periods']['periods']
    return [(p['start'], p['end']) for p in raw_periods]


def parse_validation_sets(config) -> list:
    """[[optimization_validation.sets]] をパースする。

    学習期間（optimization_periods）とは別の、スコア計算に一切使わない
    ホールドアウト検証セット。無ければ空リスト（検証スキップ）。
    Returns: [{'label': str, 'windows': [(start, end), ...]}, ...]
    """
    section = config.get('optimization_validation', {})
    result = []
    for s in section.get('sets', []):
        result.append({
            'label': s['label'],
            'windows': [(w['start'], w['end']) for w in s['windows']],
        })
    return result


def evaluate_holdout_for_params(strat: dict, config_app, exit_rules, validation_sets: list,
                                entry_mode: str = "close", consider_tax: float = 0.0) -> dict:
    """ホールドアウト検証セットでパラメータを評価する（スコア計算には一切使わない）。

    セット内の全窓のトレードを**プールしてから**指標計算する。3ヶ月窓単体に
    活動量ゲート等を当てると静かなスクリーンが不当に沈むため。窓別内訳も併記する。

    注意: プール後の指標（pooled）は窓をまたぐため DD は additive（legacy）値。
    窓単体の MTM DD は windows[i]['metrics'] 側を参照する。
    """
    from backtest.backtest_report import calculate_metrics as _calc_metrics

    results = {}
    for vset in validation_sets:
        pooled_trades = []
        window_metrics = []
        for start_date, end_date in vset['windows']:
            df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = \
                get_cached_data(config_app, start_date, end_date)
            metrics, trades = run_single_strategy(
                strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents,
                trading_dates, exit_rules, show_progress=False,
                consider_tax=consider_tax, entry_mode=entry_mode,
            )
            window_metrics.append({'start': start_date, 'end': end_date, 'metrics': metrics})
            if trades:
                pooled_trades.extend(trades)
        results[vset['label']] = {
            'pooled': _calc_metrics(pooled_trades, consider_tax=consider_tax),
            'windows': window_metrics,
        }
    return results


def run_holdout_validation(study, strat_base: dict, actual_name: str, config, config_app,
                           exit_rules, validation_sets: list, top_n: int = 5):
    """study 完了後に best-N trial をホールドアウト検証セットで評価し、表と JSON を出力する。

    in-sample スコア（study の value）と並記することで、勝者の呪い（選択バイアス）
    による劣化率を可視化する。結果は backend/backtest/results/holdout_{strategy}.json に保存。
    """
    completed = [t for t in study.trials
                 if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None]
    top_trials = sorted(completed, key=lambda t: t.value, reverse=True)[:top_n]
    if not top_trials:
        print("  Holdout: 評価可能な trial がありません。")
        return

    entry_mode = config.get('general', {}).get('entry_mode', 'close')
    consider_tax = float(config.get('general', {}).get('consider_tax', 0.0))
    min_dollar_vol = config.get('general', {}).get('min_avg_dollar_volume_21')

    print("=" * 70)
    print(f"  Holdout Validation: best {len(top_trials)} trials / "
          f"sets: {', '.join(v['label'] for v in validation_sets)}")
    print("  （この期間はスコア最大化に一切使用していない未学習期間）")
    print("=" * 70)

    report = {
        'strategy': actual_name,
        'validation_sets': [{'label': v['label'], 'windows': v['windows']} for v in validation_sets],
        'trials': [],
    }
    for t in top_trials:
        strat = strat_base.copy()
        strat.update(t.params)
        strat['name'] = f"{actual_name}_holdout_t{t.number}"
        if min_dollar_vol is not None and 'min_avg_dollar_volume_21' not in strat:
            strat['min_avg_dollar_volume_21'] = float(min_dollar_vol)

        results = evaluate_holdout_for_params(
            strat, config_app, exit_rules, validation_sets,
            entry_mode=entry_mode, consider_tax=consider_tax)

        print(f"\n  [Trial {t.number}] in-sample score = {t.value:.2f}")
        entry = {'trial': t.number, 'in_sample_score': t.value, 'params': t.params, 'sets': {}}
        for label, res in results.items():
            p = res['pooled']
            print(f"    {label:<14} trades={p['total_trades']:>4} "
                  f"win={p['win_rate'] * 100:5.1f}% exp={p['expectancy']:+6.2f}% "
                  f"lcb={p['expectancy_lcb']:+6.2f}% dd={p['max_drawdown_pct']:6.1f}%")
            entry['sets'][label] = {
                'pooled': {k: p.get(k) for k in (
                    'total_trades', 'win_rate', 'expectancy', 'expectancy_lcb',
                    'avg_gain', 'profit_factor', 'max_drawdown_pct')},
                'windows': [{'start': w['start'], 'end': w['end'],
                             'trades': (w['metrics'] or {}).get('total_trades', 0),
                             'win_rate': (w['metrics'] or {}).get('win_rate', 0.0),
                             'expectancy': (w['metrics'] or {}).get('expectancy', 0.0)}
                            for w in res['windows']],
            }
        report['trials'].append(entry)

    import json as _json
    out_dir = os.path.join(backend_dir, 'backtest', 'results')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'holdout_{actual_name}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        _json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Holdout report saved: {out_path}")


def enqueue_baseline_trial(study, config: dict, strategy_name: str) -> bool:
    """
    Extract baseline default parameters for a strategy from TOML config
    and enqueue them as the first trial in the study.
    """
    strat_base, actual_name = resolve_strategy(config, strategy_name)
    if not strat_base:
        raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
    # Parse optimization params from TOML
    try:
        param_defs = parse_optimization_params(config, actual_name)
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

def objective(trial: optuna.Trial, strategy_name: str, config, config_app, exit_rules: ExitRules, periods: list):
    import traceback
    try:
        # Resolve strategy config
        strat_base, actual_name = resolve_strategy(config, strategy_name)
        if not strat_base:
            raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
        # Copy strategy config to preserve baseline settings
        strat = strat_base.copy()
        strat['name'] = f"{actual_name}_Trial_{trial.number}"

        # 流動性ハード制約（最適化対象外・全戦略共通。戦略側の明示指定があればそちらを優先）
        min_dollar_vol = config.get('general', {}).get('min_avg_dollar_volume_21')
        if min_dollar_vol is not None and 'min_avg_dollar_volume_21' not in strat:
            strat['min_avg_dollar_volume_21'] = float(min_dollar_vol)

        # Parse optimization params from TOML and apply via Optuna trial
        param_defs = parse_optimization_params(config, actual_name)
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
        entry_mode = config.get('general', {}).get('entry_mode', 'close')
        
        total_score = 0.0
        total_trades = 0
        total_wins = 0
        expectancy_sum = 0.0
        expectancy_lcb_sum = 0.0
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
                fast_prune=True, prune_bounds=prune_bounds, consider_tax=consider_tax,
                entry_mode=entry_mode
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
                    expectancy_lcb_sum += metrics.get('expectancy_lcb', 0.0)
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
            trial.set_user_attr("expectancy_lcb", round(expectancy_lcb_sum / periods_with_trades, 3))
            trial.set_user_attr("avg_gain", round(avg_gain, 3))
            trial.set_user_attr("avg_spy_gain", round(avg_spy, 3))
            trial.set_user_attr("alpha", round(avg_gain - avg_spy, 3))
        else:
            trial.set_user_attr("expectancy", 0.0)
            trial.set_user_attr("expectancy_lcb", 0.0)
            trial.set_user_attr("avg_gain", 0.0)
            trial.set_user_attr("avg_spy_gain", 0.0)
            trial.set_user_attr("alpha", 0.0)
            
        # Calculate total years across all periods to compute true annualized CAGR
        total_years = 0.0
        for start_date, end_date in periods:
            from datetime import datetime, date as dt_date
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").date() if isinstance(start_date, str) else start_date
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").date() if isinstance(end_date, str) else end_date
            total_years += (e_dt - s_dt).days / 365.25

        port_total_return = (overall_strat_mult - 1.0) * 100.0
        spy_total_return = (overall_spy_mult - 1.0) * 100.0

        if total_years > 0:
            portfolio_cagr = (overall_strat_mult ** (1.0 / total_years) - 1.0) * 100.0
            spy_bh_cagr = (overall_spy_mult ** (1.0 / total_years) - 1.0) * 100.0
        else:
            portfolio_cagr = 0.0
            spy_bh_cagr = 0.0
            
        portfolio_vs_spy = portfolio_cagr - spy_bh_cagr
        port_vs_spy_total = port_total_return - spy_total_return

        trial.set_user_attr("max_drawdown", max_dd_overall)
        trial.set_user_attr("port_cagr", round(portfolio_cagr, 2))
        trial.set_user_attr("spy_cagr", round(spy_bh_cagr, 2))
        trial.set_user_attr("port_vs_spy", round(portfolio_vs_spy, 2))
        trial.set_user_attr("port_total_return", round(port_total_return, 2))
        trial.set_user_attr("spy_total_return", round(spy_total_return, 2))
        trial.set_user_attr("port_vs_spy_total", round(port_vs_spy_total, 2))

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
        print(f"  [Trial {trial.number}] Score: {avg_score:.2f} | Port vs SPY: {portfolio_vs_spy:+.2f}% (Total: {port_vs_spy_total:+.1f}%) | MaxDD: {max_dd_overall:.1f}% | Trades: {total_trades} | WinRate: {trial.user_attrs['win_rate']:.1f}%", flush=True)

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
    # Resolve actual strategy name from configuration
    strat_base, actual_name = resolve_strategy(config, args.strategy)
    if not strat_base:
        print(f"Error: Strategy '{args.strategy}' not found in backtest config.")
        return

    study_name = actual_name
    
    print("=" * 60)
    print(f"  Optuna Optimization Runner: Strategy {actual_name} (from: {args.strategy})")
    print(f"  Periods: {periods}")
    print("=" * 60)
    
    study = optuna.create_study(
        study_name=study_name, 
        storage=storage, 
        load_if_exists=True,
        direction="maximize"
    )
    
    try:
        enqueued = enqueue_baseline_trial(study, config, actual_name)
        if enqueued:
            print("  Enqueued baseline trial from default config values.")
    except Exception as e:
        print(f"  Warning: Could not enqueue baseline trial: {e}")
        
    study.optimize(
        lambda t: objective(t, actual_name, config, config_app, exit_rules, periods),
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
        print(f"  Port CAGR:      {trial.user_attrs.get('port_cagr', 0):.2f}% (Total: {trial.user_attrs.get('port_total_return', 0):.2f}%)")
        print(f"  SPY B&H CAGR:   {trial.user_attrs.get('spy_cagr', 0):.2f}% (Total: {trial.user_attrs.get('spy_total_return', 0):.2f}%)")
        print(f"  Port vs SPY:    {trial.user_attrs.get('port_vs_spy', 0):.2f}% (Total: {trial.user_attrs.get('port_vs_spy_total', 0):.2f}%)")

    # --- ホールドアウト検証（未学習期間で best-N を評価。スコア最大化には不使用） ---
    validation_sets = parse_validation_sets(config)
    if validation_sets:
        try:
            run_holdout_validation(study, strat_base, actual_name, config, config_app,
                                   exit_rules, validation_sets)
        except Exception as e:
            import traceback
            print(f"\n  Warning: ホールドアウト検証でエラーが発生しました: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()
