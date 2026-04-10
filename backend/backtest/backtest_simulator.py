"""
backtest_simulator.py — Trade Simulator for Backtest Engine

Takes signal records and simulates trades using fixed exit rules,
walking forward through daily price data.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import pandas as pd


@dataclass
class TradeResult:
    """Result of a completed trade."""
    symbol_id: int
    ticker: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    pnl_pct: float          # Total PnL % (weighted average of partial + remaining)
    holding_days: int
    exit_reason: str         # 'stop_loss', 'partial_then_stop', 'ema21_exit', 'sma50_atr_exit', 'time_stop', 'failsafe'
    partial_exit_pnl_pct: Optional[float] = None  # PnL % on the 1/3 partial exit
    spy_pnl_pct: Optional[float] = None           # SPY return % over the same holding period (benchmark)


@dataclass
class ExitRules:
    """Fixed exit rules from config."""
    stop_loss_pct: float = -8.0
    partial_take_profit_pct: float = 20.0
    partial_take_profit_sma50_atr: float = 8.0
    partial_ratio: float = 0.333
    full_exit_ema21_consecutive_days: int = 2
    full_exit_sma50_atr: float = 11.0
    time_stop_days: int = 7
    failsafe_max_days: int = 120

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'ExitRules':
        exit_cfg = config.get('exit_rules', {})
        general_cfg = config.get('general', {})
        return cls(
            stop_loss_pct=exit_cfg.get('stop_loss_pct', -8.0),
            partial_take_profit_pct=exit_cfg.get('partial_take_profit_pct', 20.0),
            partial_take_profit_sma50_atr=exit_cfg.get('partial_take_profit_sma50_atr', 8.0),
            partial_ratio=exit_cfg.get('partial_ratio', 0.333),
            full_exit_ema21_consecutive_days=exit_cfg.get('full_exit_ema21_consecutive_days', 2),
            full_exit_sma50_atr=exit_cfg.get('full_exit_sma50_atr', 11.0),
            time_stop_days=exit_cfg.get('time_stop_days', 7),
            failsafe_max_days=general_cfg.get('failsafe_max_days', 120),
        )


def simulate_trade(
    signal,
    df_price_sym: pd.DataFrame,
    df_ind_sym: pd.DataFrame,
    exit_rules: ExitRules,
) -> Optional[TradeResult]:
    """
    Simulate a single trade from entry signal through exit.

    Args:
        signal: SignalRecord with entry info.
        df_price_sym: Daily prices for this symbol, sorted by date.
        df_ind_sym: Indicators for this symbol, sorted by date.
        exit_rules: Fixed exit rules.

    Returns:
        TradeResult or None if insufficient data.
    """
    entry_date = signal.date
    entry_price = signal.entry_price

    # Get future data after entry date
    future_prices = df_price_sym[df_price_sym['date'] > entry_date].sort_values('date')
    future_inds = df_ind_sym[df_ind_sym['date'] > entry_date].sort_values('date')

    if future_prices.empty:
        return None

    # Merge future prices with indicators
    future = future_prices.merge(
        future_inds[['date', 'ema_21', 'sma_50', 'atr_14', 'dist_sma50_atr']],
        on='date', how='left'
    )

    # State tracking
    partial_taken = False
    stop_price = entry_price * (1 + exit_rules.stop_loss_pct / 100.0)
    ema21_below_count = 0
    partial_exit_pnl = None

    # For time stop: track 7-day high/low range
    recent_highs = []
    recent_lows = []

    for i, (_, row) in enumerate(future.iterrows()):
        current_close = row['close']
        current_high = row['high']
        current_low = row['low']
        current_date = row['date']
        current_ema21 = row.get('ema_21')
        current_dist_sma50_atr = row.get('dist_sma50_atr')
        current_atr = row.get('atr_14')

        day_count = i + 1  # 1-indexed trading days after entry
        gain_pct = (current_close - entry_price) / entry_price * 100.0

        # Track recent highs/lows for time stop
        recent_highs.append(current_high)
        recent_lows.append(current_low)
        if len(recent_highs) > exit_rules.time_stop_days:
            recent_highs.pop(0)
            recent_lows.pop(0)

        # === 1. Stop Loss Check ===
        if current_close <= stop_price:
            exit_pnl = (current_close - entry_price) / entry_price * 100.0
            if partial_taken:
                # Weighted PnL: 1/3 at partial price + 2/3 at stop
                remaining_pnl = exit_pnl
                total_pnl = partial_exit_pnl * exit_rules.partial_ratio + remaining_pnl * (1 - exit_rules.partial_ratio)
                reason = 'partial_then_stop'
            else:
                total_pnl = exit_pnl
                reason = 'stop_loss'

            return TradeResult(
                symbol_id=signal.symbol_id, ticker=signal.ticker,
                entry_date=entry_date, exit_date=current_date,
                entry_price=entry_price, exit_price=current_close,
                pnl_pct=total_pnl, holding_days=day_count,
                exit_reason=reason, partial_exit_pnl_pct=partial_exit_pnl,
            )

        # === 2. 1/3 Partial Take Profit Check ===
        if not partial_taken:
            take_profit_triggered = gain_pct >= exit_rules.partial_take_profit_pct
            sma50_atr_triggered = (
                current_dist_sma50_atr is not None and
                not np.isnan(current_dist_sma50_atr) and
                current_dist_sma50_atr >= exit_rules.partial_take_profit_sma50_atr
            )

            if take_profit_triggered or sma50_atr_triggered:
                partial_taken = True
                partial_exit_pnl = gain_pct
                # Move stop to entry (breakeven)
                stop_price = entry_price

        # === 3. Full Exit: SMA50/ATR% >= 11 ===
        if (current_dist_sma50_atr is not None and
                not np.isnan(current_dist_sma50_atr) and
                current_dist_sma50_atr >= exit_rules.full_exit_sma50_atr):
            exit_pnl = gain_pct
            if partial_taken:
                total_pnl = partial_exit_pnl * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
            else:
                total_pnl = exit_pnl
            return TradeResult(
                symbol_id=signal.symbol_id, ticker=signal.ticker,
                entry_date=entry_date, exit_date=current_date,
                entry_price=entry_price, exit_price=current_close,
                pnl_pct=total_pnl, holding_days=day_count,
                exit_reason='sma50_atr_exit', partial_exit_pnl_pct=partial_exit_pnl,
            )

        # === 4. Full Exit: EMA21 below for N consecutive days ===
        if current_ema21 is not None and not np.isnan(current_ema21):
            if current_close < current_ema21:
                ema21_below_count += 1
            else:
                ema21_below_count = 0

            if ema21_below_count >= exit_rules.full_exit_ema21_consecutive_days:
                exit_pnl = gain_pct
                if partial_taken:
                    total_pnl = partial_exit_pnl * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
                else:
                    total_pnl = exit_pnl
                return TradeResult(
                    symbol_id=signal.symbol_id, ticker=signal.ticker,
                    entry_date=entry_date, exit_date=current_date,
                    entry_price=entry_price, exit_price=current_close,
                    pnl_pct=total_pnl, holding_days=day_count,
                    exit_reason='ema21_exit', partial_exit_pnl_pct=partial_exit_pnl,
                )

        # === 5. Time Stop: 7-day range < 1 ATR ===
        if (len(recent_highs) >= exit_rules.time_stop_days and
                current_atr is not None and not np.isnan(current_atr)):
            range_7d = max(recent_highs) - min(recent_lows)
            if range_7d < current_atr:
                exit_pnl = gain_pct
                if partial_taken:
                    total_pnl = partial_exit_pnl * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
                else:
                    total_pnl = exit_pnl
                return TradeResult(
                    symbol_id=signal.symbol_id, ticker=signal.ticker,
                    entry_date=entry_date, exit_date=current_date,
                    entry_price=entry_price, exit_price=current_close,
                    pnl_pct=total_pnl, holding_days=day_count,
                    exit_reason='time_stop', partial_exit_pnl_pct=partial_exit_pnl,
                )

        # === 6. Failsafe ===
        if day_count >= exit_rules.failsafe_max_days:
            exit_pnl = gain_pct
            if partial_taken:
                total_pnl = partial_exit_pnl * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
            else:
                total_pnl = exit_pnl
            return TradeResult(
                symbol_id=signal.symbol_id, ticker=signal.ticker,
                entry_date=entry_date, exit_date=current_date,
                entry_price=entry_price, exit_price=current_close,
                pnl_pct=total_pnl, holding_days=day_count,
                exit_reason='failsafe', partial_exit_pnl_pct=partial_exit_pnl,
            )

    # If we exhausted all data without an exit, exit at last available price
    if not future_prices.empty:
        last_row = future_prices.iloc[-1]
        exit_pnl = (last_row['close'] - entry_price) / entry_price * 100.0
        if partial_taken:
            total_pnl = partial_exit_pnl * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
        else:
            total_pnl = exit_pnl
        return TradeResult(
            symbol_id=signal.symbol_id, ticker=signal.ticker,
            entry_date=entry_date, exit_date=last_row['date'],
            entry_price=entry_price, exit_price=float(last_row['close']),
            pnl_pct=total_pnl, holding_days=len(future_prices),
            exit_reason='data_end', partial_exit_pnl_pct=partial_exit_pnl,
        )

    return None
