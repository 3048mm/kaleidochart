"""
backtest_report.py — Report Generator for Backtest Engine

Aggregates TradeResult lists into performance metrics and
generates comparison tables.
"""
import json
import os
from typing import List, Dict, Any
from datetime import date
from backtest_simulator import TradeResult


def calculate_metrics(trades: List[TradeResult]) -> Dict[str, Any]:
    """
    Calculate performance metrics from a list of trade results.

    Returns:
        Dict with metrics including:
        - trades, wins, win_rate, avg_win, avg_loss
        - profit_factor, expectancy, avg_holding_days
        - avg_gain: average PnL% per trade (key metric for optimization)
        - avg_spy_gain: average SPY return% over the same holding periods (benchmark)
        - alpha: avg_gain - avg_spy_gain (excess return over market)
        - total_return_pct, max_drawdown_pct (additive cumulative PnL)
        - exit_reasons breakdown
    """
    if not trades:
        return {
            'trades': 0, 'wins': 0, 'win_rate': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'expectancy': 0.0,
            'avg_holding_days': 0.0, 'exit_reasons': {},
            'avg_gain': 0.0, 'avg_spy_gain': 0.0, 'alpha': 0.0,
            'total_trades': 0, 'win_trades': 0,
            'total_return_pct': 0.0, 'max_drawdown_pct': 0.0,
        }

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    total = len(trades)
    win_count = len(wins)
    win_rate = win_count / total if total > 0 else 0.0

    avg_win = sum(t.pnl_pct for t in wins) / win_count if win_count > 0 else 0.0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0

    total_profit = sum(t.pnl_pct for t in wins)
    total_loss = abs(sum(t.pnl_pct for t in losses))
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    avg_holding = sum(t.holding_days for t in trades) / total

    # Average gain per trade (key metric for optimization)
    avg_gain = sum(t.pnl_pct for t in trades) / total

    # SPY benchmark: average SPY return over the same holding periods
    spy_gains = [t.spy_pnl_pct for t in trades if t.spy_pnl_pct is not None]
    avg_spy_gain = sum(spy_gains) / len(spy_gains) if spy_gains else 0.0

    # Alpha: excess return over market (positive = outperforming SPY)
    alpha = avg_gain - avg_spy_gain

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    # Calculate Max Drawdown using additive cumulative PnL
    # (not multiplicative compounding, since we evaluate raw signals without portfolio sizing)
    sorted_trades = sorted(trades, key=lambda t: t.exit_date)
    cumulative_pnl = 0.0
    peak_cumulative = 0.0
    max_dd = 0.0
    
    for t in sorted_trades:
        cumulative_pnl += t.pnl_pct
        if cumulative_pnl > peak_cumulative:
            peak_cumulative = cumulative_pnl
        
        # Drawdown is the distance from the peak cumulative PnL
        dd = peak_cumulative - cumulative_pnl
        if dd > max_dd:
            max_dd = dd

    total_return_pct = cumulative_pnl

    return {
        'trades': total,
        'wins': win_count,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'avg_holding_days': avg_holding,
        'exit_reasons': exit_reasons,
        'avg_gain': avg_gain,
        'avg_spy_gain': avg_spy_gain,
        'alpha': alpha,
        
        # Keys expected by optimization_runner.py
        'total_trades': total,
        'win_trades': win_count,
        'total_return_pct': total_return_pct,
        'max_drawdown_pct': -max_dd
    }


def print_comparison_table(results: Dict[str, Dict[str, Any]], start_date: str, end_date: str):
    """
    Print a formatted comparison table to console.

    Args:
        results: Dict mapping strategy name to metrics dict.
        start_date: Backtest start date.
        end_date: Backtest end date.
    """
    print(f"\n{'='*100}")
    print(f"  Backtest Results ({start_date} ~ {end_date})")
    print(f"{'='*100}")

    # Header
    header = (
        f"{'Strategy':<25} {'Trades':>7} {'WinRate':>8} {'AvgWin':>8} {'AvgLoss':>8} "
        f"{'PF':>6} {'Expect':>8} {'AvgGain':>8} {'SPY':>8} {'Alpha':>8} {'AvgDays':>8}"
    )
    print(header)
    print('-' * 120)

    # Sort by expectancy descending
    sorted_strategies = sorted(results.items(), key=lambda x: x[1].get('expectancy', 0), reverse=True)

    for name, metrics in sorted_strategies:
        row = (
            f"{name:<25} "
            f"{metrics['trades']:>7} "
            f"{metrics['win_rate']*100:>7.1f}% "
            f"{metrics['avg_win']:>+7.1f}% "
            f"{metrics['avg_loss']:>+7.1f}% "
            f"{metrics['profit_factor']:>6.2f} "
            f"{metrics['expectancy']:>+7.2f}% "
            f"{metrics.get('avg_gain', 0):>+7.2f}% "
            f"{metrics.get('avg_spy_gain', 0):>+7.2f}% "
            f"{metrics.get('alpha', 0):>+7.2f}% "
            f"{metrics['avg_holding_days']:>7.1f}"
        )
        print(row)

    print(f"{'='*120}")

    # Exit reason breakdown
    print(f"\n  Exit Reason Breakdown")
    print(f"{'-'*80}")

    all_reasons = set()
    for metrics in results.values():
        all_reasons.update(metrics.get('exit_reasons', {}).keys())
    all_reasons = sorted(all_reasons)

    reason_header = f"{'Strategy':<30} " + " ".join(f"{r:>15}" for r in all_reasons)
    print(reason_header)
    print('-' * 80)

    for name, metrics in sorted_strategies:
        reasons = metrics.get('exit_reasons', {})
        row = f"{name:<30} " + " ".join(f"{reasons.get(r, 0):>15}" for r in all_reasons)
        print(row)

    print()


def save_results_json(
    results: Dict[str, Dict[str, Any]],
    all_trades: Dict[str, List[TradeResult]],
    output_dir: str,
    start_date: str,
    end_date: str,
):
    """
    Save detailed results to JSON files.

    Args:
        results: Dict mapping strategy name to metrics.
        all_trades: Dict mapping strategy name to list of TradeResult.
        output_dir: Directory to save output files.
        start_date: Backtest start date.
        end_date: Backtest end date.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Summary JSON
    summary = {
        'period': {'start': start_date, 'end': end_date},
        'strategies': {}
    }
    for name, metrics in results.items():
        summary['strategies'][name] = metrics

    summary_path = os.path.join(output_dir, 'backtest_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"Summary saved to: {summary_path}")

    # Per-strategy trade details
    for name, trades in all_trades.items():
        trades_data = []
        for t in trades:
            trades_data.append({
                'ticker': t.ticker,
                'entry_date': str(t.entry_date),
                'exit_date': str(t.exit_date),
                'entry_price': round(t.entry_price, 2),
                'exit_price': round(t.exit_price, 2),
                'pnl_pct': round(t.pnl_pct, 2),
                'holding_days': t.holding_days,
                'exit_reason': t.exit_reason,
                'partial_exit_pnl_pct': round(t.partial_exit_pnl_pct, 2) if t.partial_exit_pnl_pct is not None else None,
            })

        detail_path = os.path.join(output_dir, f'trades_{name}.json')
        with open(detail_path, 'w', encoding='utf-8') as f:
            json.dump(trades_data, f, indent=2, ensure_ascii=False, default=str)

    print(f"Trade details saved to: {output_dir}/trades_*.json")
