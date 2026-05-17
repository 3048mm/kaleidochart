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

class BacktestSimulator:
    """
    Stateful simulator that can track active positions and evaluate exits on a daily basis.
    Required for Scenario Testing (daily step simulation).
    """
    def __init__(self, prices_df: pd.DataFrame, symbols_df: pd.DataFrame):
        self.prices_df = prices_df
        self.symbols_df = symbols_df
        self.positions = []
        self.trades = []
        # Pre-group prices by date for O(1) lookup
        self._price_by_date = {d: group for d, group in prices_df.groupby('date')}
        
    def evaluate_exit_for_day(self, target_date: pd.Timestamp, exit_rules: Any) -> List[Dict[str, Any]]:
        """
        Evaluates all current positions against detailed exit rules for the given date.
        Supports both ExitRules class and legacy Dict.
        Returns a list of exits triggered on this date (including partial exits).
        """
        import numpy as np
        from backend.backtest.backtest_simulator import ExitRules

        if isinstance(exit_rules, dict):
            # Dict fallback (if from scenario run with limited options)
            rules = ExitRules()
            if 'stop_loss_pct' in exit_rules:
                # If negative decimal (like -0.08), convert to percent (like -8.0)
                sl = exit_rules['stop_loss_pct']
                rules.stop_loss_pct = sl * 100.0 if abs(sl) < 1.0 else sl
            if 'profit_target_pct' in exit_rules:
                pt = exit_rules['profit_target_pct']
                rules.partial_take_profit_pct = pt * 100.0 if abs(pt) < 1.0 else pt
        else:
            rules = exit_rules

        exits_triggered = []
        remaining_positions = []
        
        day_prices = self._price_by_date.get(target_date)
        if day_prices is None:
            return []
            
        for pos in self.positions:
            symbol_id = pos['symbol_id']
            symbol_data = day_prices[day_prices['symbol_id'] == symbol_id]
            
            # 1. Update holding days
            pos['holding_days'] = pos.get('holding_days', 0) + 1
            
            if symbol_data.empty:
                remaining_positions.append(pos)
                continue
                
            close_price = float(symbol_data.iloc[0]['close'])
            high_price = float(symbol_data.iloc[0]['high'])
            low_price = float(symbol_data.iloc[0]['low'])
            entry_price = float(pos['entry_price'])
            
            ema_21 = symbol_data.iloc[0].get('ema_21')
            dist_sma50_atr = symbol_data.iloc[0].get('dist_sma50_atr')
            atr = symbol_data.iloc[0].get('atr_14')
            
            # Initialize dynamic exit states for the position if not present
            if 'stop_price' not in pos:
                pos['stop_price'] = entry_price * (1 + rules.stop_loss_pct / 100.0)
                pos['partial_taken'] = False
                pos['ema21_below_count'] = 0
                pos['recent_highs'] = []
                pos['recent_lows'] = []
                
            # Track price ranges for time stop
            pos['recent_highs'].append(high_price)
            pos['recent_lows'].append(low_price)
            if len(pos['recent_highs']) > rules.time_stop_days:
                pos['recent_highs'].pop(0)
                pos['recent_lows'].pop(0)
                
            exit_reason = None
            gain_pct = (close_price - entry_price) / entry_price * 100.0
            
            # --- 1. Stop Loss Check ---
            if close_price <= pos['stop_price']:
                exit_pnl = gain_pct
                if pos['partial_taken']:
                    total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
                    reason = 'partial_then_stop'
                else:
                    total_pnl = exit_pnl
                    reason = 'stop_loss'
                
                exit_record = {
                    'symbol_id': symbol_id,
                    'exit_date': target_date,
                    'exit_price': close_price,
                    'reason': reason,
                    'pnl_pct': total_pnl / 100.0, # portfolio expects decimal for pnl_pct
                    'is_partial': False
                }
                exits_triggered.append(exit_record)
                self.trades.append({**pos, **exit_record})
                continue
                
            # --- 2. Partial Take Profit Check ---
            if not pos['partial_taken']:
                take_profit_triggered = gain_pct >= rules.partial_take_profit_pct
                sma50_atr_triggered = (
                    dist_sma50_atr is not None and
                    not np.isnan(dist_sma50_atr) and
                    dist_sma50_atr >= rules.partial_take_profit_sma50_atr
                )
                
                if take_profit_triggered or sma50_atr_triggered:
                    pos['partial_taken'] = True
                    pos['partial_exit_pnl_pct'] = gain_pct
                    pos['stop_price'] = entry_price # Breakeven
                    
                    # Trigger a partial exit event
                    exit_record = {
                        'symbol_id': symbol_id,
                        'exit_date': target_date,
                        'exit_price': close_price,
                        'reason': 'partial_take_profit',
                        'pnl_pct': gain_pct / 100.0,
                        'is_partial': True
                    }
                    exits_triggered.append(exit_record)
                    remaining_positions.append(pos)
                    continue

            # --- 3. Full Exit: SMA50/ATR% >= 11 ---
            if (dist_sma50_atr is not None and
                    not np.isnan(dist_sma50_atr) and
                    dist_sma50_atr >= rules.full_exit_sma50_atr):
                exit_reason = 'sma50_atr_exit'
                
            # --- 4. Full Exit: EMA21 below for N consecutive days ---
            elif ema_21 is not None and not np.isnan(ema_21):
                if close_price < ema_21:
                    pos['ema21_below_count'] = pos.get('ema21_below_count', 0) + 1
                else:
                    pos['ema21_below_count'] = 0
                    
                if pos['ema21_below_count'] >= rules.full_exit_ema21_consecutive_days:
                    exit_reason = 'ema21_exit'
                    
            # --- 5. Time Stop: 7-day range < 1 ATR ---
            if not exit_reason and (len(pos['recent_highs']) >= rules.time_stop_days and
                    atr is not None and not np.isnan(atr)):
                range_7d = max(pos['recent_highs']) - min(pos['recent_lows'])
                if range_7d < atr:
                    exit_reason = 'time_stop'
                    
            # --- 6. Failsafe ---
            if not exit_reason and pos['holding_days'] >= rules.failsafe_max_days:
                exit_reason = 'failsafe'
                
            if exit_reason:
                exit_pnl = gain_pct
                if pos['partial_taken']:
                    total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
                else:
                    total_pnl = exit_pnl
                    
                exit_record = {
                    'symbol_id': symbol_id,
                    'exit_date': target_date,
                    'exit_price': close_price,
                    'reason': exit_reason,
                    'pnl_pct': total_pnl / 100.0,
                    'is_partial': False
                }
                exits_triggered.append(exit_record)
                self.trades.append({**pos, **exit_record})
            else:
                remaining_positions.append(pos)
                
        self.positions = remaining_positions
        return exits_triggered
