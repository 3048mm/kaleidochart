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
    partial_exit_date: Optional[object] = None    # 部分利確の約定日（MTM エクイティ計算で確定分を固定するため）


@dataclass
class ExitRules:
    """Fixed exit rules from config."""
    exit_type: str = "fixed"
    vxv_vix_threshold: float = 1.0
    stop_loss_pct: float = -8.0
    partial_take_profit_pct: float = 20.0
    partial_take_profit_sma50_atr: float = 8.0
    partial_ratio: float = 0.333
    full_exit_ema21_consecutive_days: int = 2
    full_exit_sma50_atr: float = 11.0
    time_stop_days: int = 7
    failsafe_max_days: int = 120
    vxv_vix_series: Dict[Any, float] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> 'ExitRules':
        exit_cfg = config.get('exit_rules', {})
        general_cfg = config.get('general', {})
        return cls(
            exit_type=exit_cfg.get('exit_type', 'fixed'),
            vxv_vix_threshold=exit_cfg.get('vxv_vix_threshold', 1.0),
            stop_loss_pct=exit_cfg.get('stop_loss_pct', -8.0),
            partial_take_profit_pct=exit_cfg.get('partial_take_profit_pct', 20.0),
            partial_take_profit_sma50_atr=exit_cfg.get('partial_take_profit_sma50_atr', 8.0),
            partial_ratio=exit_cfg.get('partial_ratio', 0.333),
            full_exit_ema21_consecutive_days=exit_cfg.get('full_exit_ema21_consecutive_days', 2),
            full_exit_sma50_atr=exit_cfg.get('full_exit_sma50_atr', 11.0),
            time_stop_days=exit_cfg.get('time_stop_days', 7),
            failsafe_max_days=general_cfg.get('failsafe_max_days', 120),
        )


def evaluate_position_exit_for_day(
    pos: Dict[str, Any],
    close_price: float,
    high_price: float,
    low_price: float,
    ema_21: Optional[float],
    dist_sma50_atr: Optional[float],
    atr: Optional[float],
    target_date: Any,
    rules: ExitRules,
) -> List[Dict[str, Any]]:
    """
    Applies exit rules to a single position for one trading day.
    Updates pos dictionary state destructively.
    Returns a list of exit events triggered on this day.
    """
    entry_price = float(pos['entry_price'])
    
    # Initialize state
    if 'stop_price' not in pos:
        pos['stop_price'] = entry_price * (1 + rules.stop_loss_pct / 100.0)
        pos['partial_taken'] = False
        pos['ema21_below_count'] = 0
        pos['recent_highs'] = []
        pos['recent_lows'] = []
        pos['partial_exit_pnl_pct'] = None
        pos['partial_exit_date'] = None

    # Track price range for time stop
    pos['recent_highs'].append(high_price)
    pos['recent_lows'].append(low_price)
    if len(pos['recent_highs']) > rules.time_stop_days:
        pos['recent_highs'].pop(0)
        pos['recent_lows'].pop(0)

    day_count = pos.get('holding_days', 0)
    gain_pct = (close_price - entry_price) / entry_price * 100.0
    exits = []

    # 1. Hold Type
    if rules.exit_type == "hold":
        if day_count >= rules.failsafe_max_days:
            exit_pnl = gain_pct
            if pos['partial_taken']:
                total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
            else:
                total_pnl = exit_pnl
            exits.append({
                'reason': 'failsafe',
                'pnl_pct': total_pnl,
                'is_partial': False,
                'exit_price': close_price,
            })
        return exits

    # 2. VXV/VIX Ratio Type
    if rules.exit_type == "vxv_vix_ratio":
        curr_d = target_date.date() if hasattr(target_date, 'date') else target_date
        ratio = rules.vxv_vix_series.get(curr_d)
        if ratio is not None and ratio < rules.vxv_vix_threshold:
            exit_pnl = gain_pct
            if pos['partial_taken']:
                total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
            else:
                total_pnl = exit_pnl
            exits.append({
                'reason': 'vxv_vix_exit',
                'pnl_pct': total_pnl,
                'is_partial': False,
                'exit_price': close_price,
            })
            return exits

        if close_price <= pos['stop_price']:
            exit_pnl = gain_pct
            if pos['partial_taken']:
                total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
                reason = 'partial_then_stop'
            else:
                total_pnl = exit_pnl
                reason = 'stop_loss'
            exits.append({
                'reason': reason,
                'pnl_pct': total_pnl,
                'is_partial': False,
                'exit_price': close_price,
            })
            return exits

        if day_count >= rules.failsafe_max_days:
            exit_pnl = gain_pct
            if pos['partial_taken']:
                total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
            else:
                total_pnl = exit_pnl
            exits.append({
                'reason': 'failsafe',
                'pnl_pct': total_pnl,
                'is_partial': False,
                'exit_price': close_price,
            })
            return exits
        return exits

    # 3. Standard Exit Rules (fixed)
    # Stop Loss Check
    if close_price <= pos['stop_price']:
        exit_pnl = gain_pct
        if pos['partial_taken']:
            total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
            reason = 'partial_then_stop'
        else:
            total_pnl = exit_pnl
            reason = 'stop_loss'
        exits.append({
            'reason': reason,
            'pnl_pct': total_pnl,
            'is_partial': False,
            'exit_price': close_price,
        })
        return exits

    # Partial Take Profit Check
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
            pos['partial_exit_date'] = target_date
            pos['stop_price'] = entry_price # Move stop to breakeven
            exits.append({
                'reason': 'partial_take_profit',
                'pnl_pct': gain_pct,
                'is_partial': True,
                'exit_price': close_price,
            })

    # Full Exit Check (SMA50/ATR, EMA21, Time Stop, Failsafe)
    exit_reason = None
    if (dist_sma50_atr is not None and
            not np.isnan(dist_sma50_atr) and
            dist_sma50_atr >= rules.full_exit_sma50_atr):
        exit_reason = 'sma50_atr_exit'
    elif ema_21 is not None and not np.isnan(ema_21):
        if close_price < ema_21:
            pos['ema21_below_count'] += 1
        else:
            pos['ema21_below_count'] = 0
            
        if pos['ema21_below_count'] >= rules.full_exit_ema21_consecutive_days:
            exit_reason = 'ema21_exit'

    if not exit_reason and (len(pos['recent_highs']) >= rules.time_stop_days and
            atr is not None and not np.isnan(atr)):
        range_7d = max(pos['recent_highs']) - min(pos['recent_lows'])
        if range_7d < atr:
            exit_reason = 'time_stop'

    if not exit_reason and day_count >= rules.failsafe_max_days:
        exit_reason = 'failsafe'

    if exit_reason:
        exit_pnl = gain_pct
        if pos['partial_taken']:
            total_pnl = pos['partial_exit_pnl_pct'] * rules.partial_ratio + exit_pnl * (1 - rules.partial_ratio)
        else:
            total_pnl = exit_pnl
        exits.append({
            'reason': exit_reason,
            'pnl_pct': total_pnl,
            'is_partial': False,
            'exit_price': close_price,
        })

    return exits


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
        future_inds[['date', 'ema_21', 'sma_50', 'atr_14', 'sma50_atr_mult']],
        on='date', how='left'
    )

    pos = {
        'symbol_id': signal.symbol_id,
        'entry_price': entry_price,
        'holding_days': 0,
    }

    for i, (_, row) in enumerate(future.iterrows()):
        pos['holding_days'] += 1
        
        day_exits = evaluate_position_exit_for_day(
            pos=pos,
            close_price=float(row['close']),
            high_price=float(row['high']),
            low_price=float(row['low']),
            ema_21=row.get('ema_21'),
            dist_sma50_atr=row.get('sma50_atr_mult'),
            atr=row.get('atr_14'),
            target_date=row['date'],
            rules=exit_rules,
        )
        
        full_exit = None
        for ex in day_exits:
            if not ex['is_partial']:
                full_exit = ex
                break
                
        if full_exit:
            return TradeResult(
                symbol_id=signal.symbol_id,
                ticker=signal.ticker,
                entry_date=entry_date,
                exit_date=row['date'],
                entry_price=entry_price,
                exit_price=full_exit['exit_price'],
                pnl_pct=full_exit['pnl_pct'],
                holding_days=pos['holding_days'],
                exit_reason=full_exit['reason'],
                partial_exit_pnl_pct=pos.get('partial_exit_pnl_pct'),
                partial_exit_date=pos.get('partial_exit_date'),
            )

    # If we exhausted all data without an exit, exit at last available price
    if not future_prices.empty:
        last_row = future_prices.iloc[-1]
        exit_pnl = (float(last_row['close']) - entry_price) / entry_price * 100.0
        if pos.get('partial_taken'):
            total_pnl = pos['partial_exit_pnl_pct'] * exit_rules.partial_ratio + exit_pnl * (1 - exit_rules.partial_ratio)
        else:
            total_pnl = exit_pnl
        return TradeResult(
            symbol_id=signal.symbol_id, ticker=signal.ticker,
            entry_date=entry_date, exit_date=last_row['date'],
            entry_price=entry_price, exit_price=float(last_row['close']),
            pnl_pct=total_pnl, holding_days=len(future_prices),
            exit_reason='data_end', partial_exit_pnl_pct=pos.get('partial_exit_pnl_pct'), partial_exit_date=pos.get('partial_exit_date'),
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
            
            ema_21 = symbol_data.iloc[0].get('ema_21')
            dist_sma50_atr = symbol_data.iloc[0].get('sma50_atr_mult')
            atr = symbol_data.iloc[0].get('atr_14')

            day_exits = evaluate_position_exit_for_day(
                pos=pos,
                close_price=close_price,
                high_price=high_price,
                low_price=low_price,
                ema_21=ema_21,
                dist_sma50_atr=dist_sma50_atr,
                atr=atr,
                target_date=target_date,
                rules=rules,
            )
            
            has_full_exit = False
            for ex in day_exits:
                exit_record = {
                    'symbol_id': symbol_id,
                    'exit_date': target_date,
                    'exit_price': ex['exit_price'],
                    'reason': ex['reason'],
                    'pnl_pct': ex['pnl_pct'] / 100.0,  # portfolio expects decimal for pnl_pct
                    'is_partial': ex['is_partial']
                }
                exits_triggered.append(exit_record)
                
                if not ex['is_partial']:
                    self.trades.append({**pos, **exit_record})
                    has_full_exit = True
                    break
                    
            if not has_full_exit:
                remaining_positions.append(pos)
                
        self.positions = remaining_positions
        return exits_triggered
