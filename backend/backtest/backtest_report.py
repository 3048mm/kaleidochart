"""
backtest_report.py — Report Generator for Backtest Engine

Aggregates TradeResult lists into performance metrics and
generates comparison tables.
"""
import json
import math
import os
from typing import Any, Dict, List, Optional
from datetime import date
from backend.backtest.backtest_simulator import TradeResult


def _build_mtm_max_drawdown(trades: List[TradeResult], price_by_sym: Dict[int, Any],
                            partial_ratio: float) -> float:
    """日次 mark-to-market エクイティカーブから最大ドローダウン（正規化前・%ポイント）を計算する。

    - 保有中（entry_date < d < exit_date）は日次終値で含み損益を評価
    - 部分利確後は確定分（partial_ratio）を固定し、残りのみ時価評価
    - exit 日以降は確定損益（pnl_pct）が累積エクイティに恒久加算される

    旧 additive 方式（exit 日ソートの確定損益累積）では見えなかった
    「保有中の含み損の谷」「シグナル集中期の同時被弾」を DD に反映する。
    """
    realized_by_date: Dict[Any, float] = {}
    open_by_date: Dict[Any, float] = {}

    for t in trades:
        realized_by_date[t.exit_date] = realized_by_date.get(t.exit_date, 0.0) + t.pnl_pct

        df_p = price_by_sym.get(t.symbol_id)
        if df_p is None or len(df_p) == 0 or t.entry_price <= 0:
            continue
        window = df_p[(df_p['date'] > t.entry_date) & (df_p['date'] < t.exit_date)]
        if window.empty:
            continue
        partial_date = getattr(t, 'partial_exit_date', None)
        partial_pnl = t.partial_exit_pnl_pct
        for d, close in zip(window['date'].values, window['close'].values):
            unrealized = (close - t.entry_price) / t.entry_price * 100.0
            if partial_date is not None and partial_pnl is not None and d >= partial_date:
                contrib = partial_pnl * partial_ratio + unrealized * (1.0 - partial_ratio)
            else:
                contrib = unrealized
            open_by_date[d] = open_by_date.get(d, 0.0) + contrib

    all_dates = sorted(set(realized_by_date) | set(open_by_date))
    cum_realized = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in all_dates:
        cum_realized += realized_by_date.get(d, 0.0)
        equity = cum_realized + open_by_date.get(d, 0.0)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def calculate_metrics(trades: List[TradeResult], spy_period_return: float = 0.0,
                      consider_tax: float = 0.0,
                      price_by_sym: Optional[Dict[int, Any]] = None,
                      trading_dates: Optional[list] = None,
                      partial_ratio: float = 0.333) -> Dict[str, Any]:
    """
    Calculate performance metrics from a list of trade results.

    Args:
        price_by_sym: {symbol_id: 日次価格 DataFrame}。指定時は max_drawdown_pct を
            mark-to-market 方式で計算する（未指定時は旧 additive 方式にフォールバック）。
        partial_ratio: 部分利確の比率（MTM 評価で確定分を固定するために使用）。

    Returns:
        Dict with metrics including:
        - trades, wins, win_rate, avg_win, avg_loss
        - profit_factor, expectancy, avg_holding_days
        - pnl_std / expectancy_se / expectancy_lcb: トレードpnl の標本標準偏差・標準誤差・
          下側信頼限界（expectancy − 2×SE）。少数トレードのまぐれを罰する最適化用指標
        - avg_gain: average PnL% per trade
        - avg_spy_gain: average SPY return% over the same holding periods (benchmark)
        - alpha: avg_gain - avg_spy_gain (excess return over market)
        - total_return_pct
        - max_drawdown_pct: MTM ドローダウン（price_by_sym 指定時）
        - max_drawdown_legacy_pct: 旧 additive 方式の DD（比較用に常時併記)
        - exit_reasons breakdown
    """
    if not trades:
        return {
            'trades': 0, 'wins': 0, 'win_rate': 0.0,
            'avg_win': 0.0, 'avg_loss': 0.0,
            'profit_factor': 0.0, 'expectancy': 0.0,
            'pnl_std': 0.0, 'expectancy_se': 0.0, 'expectancy_lcb': 0.0,
            'avg_holding_days': 0.0, 'exit_reasons': {},
            'avg_gain': 0.0, 'avg_spy_gain': 0.0, 'alpha': 0.0,
            'total_trades': 0, 'win_trades': 0,
            'total_return_pct': 0.0, 'max_drawdown_pct': 0.0,
            'max_drawdown_legacy_pct': 0.0,
            'strat_multiplier': 1.0, 'spy_multiplier': 1.0,
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

    # 期待値の下側信頼限界（LCB = expectancy − 2×SE）。
    # 少数トレード・高分散の「まぐれ」は SE が大きくなり自動的に沈む。
    # n < 2 では SE が定義できないため LCB = expectancy（<5件はスコア側のゲートが罰する）
    if total >= 2:
        variance = sum((t.pnl_pct - avg_gain) ** 2 for t in trades) / (total - 1)
        pnl_std = math.sqrt(variance)
        expectancy_se = pnl_std / math.sqrt(total)
    else:
        pnl_std = 0.0
        expectancy_se = 0.0
    expectancy_lcb = avg_gain - 2.0 * expectancy_se

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

    # Normalize additive return and drawdown to Portfolio-Equivalent scale using Little's Law
    # L (Average Concurrent Positions) = Arrival Rate (Trades / Day) * Average Holding Days
    if len(trades) > 1:
        start_dt = min(t.entry_date for t in trades)
        end_dt = max(t.exit_date for t in trades)
        calendar_days = (end_dt - start_dt).days
        trading_days = max(1.0, calendar_days * 252.0 / 365.0)
        arrival_rate = len(trades) / trading_days
        avg_slots = max(1.0, arrival_rate * avg_holding)
    else:
        avg_slots = 1.0

    total_return_pct = cumulative_pnl / avg_slots
    max_dd_legacy_norm = max_dd / avg_slots

    # MTM ドローダウン（price_by_sym 指定時のみ。未指定は旧 additive にフォールバック）
    if price_by_sym is not None:
        mtm_dd = _build_mtm_max_drawdown(trades, price_by_sym, partial_ratio)
        max_dd_norm = mtm_dd / avg_slots
    else:
        max_dd_norm = max_dd_legacy_norm

    # Calculate Portfolio Compound Multiplier for this period
    strat_mult = 1.0
    for t in sorted_trades:
        pnl = t.pnl_pct
        if consider_tax > 0.0 and pnl > 0:
            pnl = pnl * (1.0 - consider_tax)
        strat_mult *= (1.0 + (pnl / 100.0) / avg_slots)

    return {
        'trades': total,
        'wins': win_count,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'expectancy': expectancy,
        'pnl_std': pnl_std,
        'expectancy_se': expectancy_se,
        'expectancy_lcb': expectancy_lcb,
        'avg_holding_days': avg_holding,
        'exit_reasons': exit_reasons,
        'avg_gain': avg_gain,
        'avg_spy_gain': avg_spy_gain,
        'alpha': alpha,
        
        # Keys expected by optimization_runner.py
        'total_trades': total,
        'win_trades': win_count,
        'total_return_pct': total_return_pct,
        'max_drawdown_pct': -max_dd_norm,
        'max_drawdown_legacy_pct': -max_dd_legacy_norm,
        
        # Multipliers for multi-period compounding
        'strat_multiplier': strat_mult,
        'spy_multiplier': 1.0 + spy_period_return
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
