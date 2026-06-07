from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional
import datetime
import pandas as pd
from backend.backtest.scenario_market_score import MarketPhase

@dataclass
class PortfolioConfig:
    initial_capital: float = 100000.0
    max_positions: int = 8
    risk_per_trade_pct: float = 0.01  # 1% risk per trade
    stop_loss_pct: float = -0.08      # -8% stop loss
    neutral_cash_ratio: float = 0.3   # 30% cash required in NEUTRAL
    bear_cash_ratio: float = 0.6      # 60% cash required in BEAR
    consider_tax: float = 0.0         # tax rate (e.g. 0.2 for 20%)


class ScenarioPortfolio:
    """
    Manages capital, active positions, and trade history for scenario testing.
    Determines position sizing and enforces cash ratios based on market phase.
    """
    def __init__(self, config: PortfolioConfig):
        self.config = config
        self.initial_capital = config.initial_capital
        self.capital = self.initial_capital
        self.tax = config.consider_tax
        self.active_positions: List[Dict[str, Any]] = []
        self.trade_history: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []

        
        # New: Tracking market score and direction for hysteresis-based dynamic allocation
        self.current_market_score = 50.0
        self.prev_market_score = 50.0
        self.dynamic_max_positions = config.max_positions
        
        # VXV/VIX Ratio hysteresis states
        self.current_vxv_vix_ratio = 1.10
        self.prev_vxv_vix_ratio = 1.10
        self.vxv_vix_hysteresis_type = "trend_follow" # default
        
    def update_vxv_vix_state(self, ratio: Optional[float], h_type: str = "trend_follow"):
        """
        Updates the portfolio's allocation limit based on VXV/VIX Ratio hysteresis.
        Supports 'trend_follow', 'contrarian', and 'vxv_vix_ema' (4-regime model).
        """
        if ratio is None:
            ratio = 1.10
            
        self.prev_vxv_vix_ratio = self.current_vxv_vix_ratio
        self.current_vxv_vix_ratio = ratio
        
        # Track history for EMA calculation
        if not hasattr(self, 'vxv_vix_history'):
            self.vxv_vix_history = []
        self.vxv_vix_history.append(ratio)
        
        if not getattr(self, 'use_hysteresis', True):
            self.dynamic_max_positions = self.config.max_positions
            return
            
        if h_type == "vxv_vix_ema":
            # Warm up period: need 21 days of history
            if len(self.vxv_vix_history) < 21:
                self.dynamic_max_positions = self.config.max_positions
                return
                
            series = pd.Series(self.vxv_vix_history)
            ema5 = series.ewm(span=5, adjust=False).mean().iloc[-1]
            ema21 = series.ewm(span=21, adjust=False).mean().iloc[-1]
            
            if ema5 < 1.00:
                # BOTTOM (Deep Bear Panic - Start scaling in)
                self.dynamic_max_positions = 2
                self.config.neutral_cash_ratio = 0.50
            elif ema5 > 1.20:
                # OVERHEAT (Overbought - Protect profits)
                self.dynamic_max_positions = 4
                self.config.neutral_cash_ratio = 0.30
                self.tighten_all_stop_losses()
            elif ema5 > ema21:
                # BULL (Stable Trend - Full size)
                self.dynamic_max_positions = self.config.max_positions
                self.config.neutral_cash_ratio = 0.00
            else:
                # BEAR (Weak/Correction - Cash out/Stay out)
                self.dynamic_max_positions = 0
                self.config.neutral_cash_ratio = 0.70
            return
            
        is_upward = ratio >= self.prev_vxv_vix_ratio
        
        if h_type == "trend_follow":
            # Interpretation A (Trend Follow): Stable market (high ratio) -> Full Pos, Panic (low ratio) -> Cash out
            if is_upward:
                if ratio >= 1.20:
                    self.dynamic_max_positions = self.config.max_positions
                else:
                    # Keep previous, or if we were starting, default to 0
                    pass
            else: # Downward
                if ratio < 1.00:
                    self.dynamic_max_positions = 0
                else:
                    # Maintain full
                    self.dynamic_max_positions = self.config.max_positions
        elif h_type == "contrarian":
            # Interpretation B (Contrarian / User literal): Panic (low ratio) -> Full Pos, Stable (high ratio) -> Cash out
            if is_upward:
                if ratio >= 1.20:
                    self.dynamic_max_positions = 0
                else:
                    # Maintain full (contrarian buying state)
                    self.dynamic_max_positions = self.config.max_positions
            else: # Downward
                if ratio < 1.00:
                    self.dynamic_max_positions = self.config.max_positions
                else:
                    # Keep previous or remain 0
                    pass

    def update_market_state(self, score: float):
        """
        Updates the daily market score and dynamically adjusts allocation limits
        based on the user's dynamic upward/downward hysteresis rules.
        """
        self.prev_market_score = self.current_market_score
        self.current_market_score = score
        
        # If hysteresis is disabled for the portfolio, maintain maximum allocation
        if not getattr(self, 'use_hysteresis', True):
            self.dynamic_max_positions = self.config.max_positions
            return
            
        # Determine the direction of the score change
        is_upward = score >= self.prev_market_score
        
        # Implements the user's dynamic direction-based allocation rules:
        # - 70% -> 80% (Upward >= 80%): 100% position (8 slots)
        # - 90% -> 80% (Downward >= 80%): 80% position (6 slots)
        # - Downward >= 40% but < 80%: 50% position (4 slots)
        # - 30% -> 20% (Downward < 40%): 25% position (2 slots, testing the waters but defensive)
        # - < 20% (Downward < 20%): 0% position (0 slots, full cash out!)
        # - 0% -> 10% (Upward >= 10% from bottom): 25% position (2 slots, testing the waters)
        
        if is_upward:
            if score >= 80.0:
                self.dynamic_max_positions = self.config.max_positions # 100% (8 slots)
            elif score >= 10.0:
                # Upward recovery from bottom: 25% allocation (2 slots)
                self.dynamic_max_positions = max(2, int(self.config.max_positions * 0.25))
            else:
                self.dynamic_max_positions = 0
        else: # Downward
            if score >= 80.0:
                # Downward from peak but still very high: 80% allocation (6 slots)
                self.dynamic_max_positions = max(1, int(self.config.max_positions * 0.8))
            elif score >= 40.0:
                # Downward neutral: 50% allocation (4 slots)
                self.dynamic_max_positions = max(1, int(self.config.max_positions * 0.5))
            elif score >= 20.0:
                # Downward near bottom: 25% allocation (2 slots)
                self.dynamic_max_positions = max(1, int(self.config.max_positions * 0.25))
            else:
                # Downward extreme bear: 0% allocation (full cash)
                self.dynamic_max_positions = 0
        
    def record_daily_equity(self, date: datetime.date, price_by_date: dict = None):
        """
        Records a daily snapshot of the portfolio's equity state.
        Uses closing prices to calculate floating P&L for active positions.
        """
        invested_value = 0.0
        held_tickers = []
        for pos in self.active_positions:
            held_tickers.append(pos.get('ticker', 'N/A'))
            # Use current day's close for floating valuation if available
            if price_by_date is not None:
                day_prices = price_by_date.get(date)
                if day_prices is not None:
                    sym_row = day_prices[day_prices['symbol_id'] == pos['symbol_id']]
                    if not sym_row.empty:
                        invested_value += pos['shares'] * sym_row.iloc[0]['close']
                        continue
            # Fallback: use entry cost
            invested_value += pos['amount']

        total_equity = self.capital + invested_value
        self.equity_curve.append({
            'date': date,
            'cash': round(self.capital, 2),
            'invested': round(invested_value, 2),
            'total_equity': round(total_equity, 2),
            'positions': len(self.active_positions),
            'held_tickers': ','.join(held_tickers) if held_tickers else '',
        })
        
    @property
    def current_total_value(self) -> float:
        """
        Total value including cash and the initial cost of active positions.
        (For scenario planning, we use entry value, not floating value).
        """
        invested = sum(pos['amount'] for pos in self.active_positions)
        return self.capital + invested

    def _get_target_cash_ratio(self, phase: MarketPhase) -> float:
        if phase == MarketPhase.BULL:
            return 0.0
        elif phase == MarketPhase.NEUTRAL:
            return self.config.neutral_cash_ratio
        elif phase == MarketPhase.BEAR:
            return self.config.bear_cash_ratio
        return 0.0

    def _calculate_position_size(self, price: float) -> Tuple[int, float]:
        """
        Calculates the number of shares to buy and the total cost.
        Uses the Risk/StopLoss calculation, capped by maximum position division.
        """
        total_equity = self.current_total_value
        
        # Risk per trade method
        risk_amount = total_equity * self.config.risk_per_trade_pct
        max_loss_per_share = price * abs(self.config.stop_loss_pct)
        shares_by_risk = int(risk_amount / max_loss_per_share) if max_loss_per_share > 0 else 0
        
        # Max positions method (equal weight target)
        target_allocation = total_equity / self.config.max_positions
        shares_by_weight = int(target_allocation / price)
        
        # Take the smaller size to satisfy both rules
        shares = min(shares_by_risk, shares_by_weight)
        
        return shares, shares * price

    def process_buy_candidate(self, candidate: Dict[str, Any], date: pd.Timestamp, phase: MarketPhase) -> bool:
        """
        Attempts to buy a candidate stock.
        Returns True if successful, False if rejected (e.g., lack of funds or phase restrictions).
        """
        if len(self.active_positions) >= self.dynamic_max_positions:
            return False
            
        # Check if already hold
        if any(pos['symbol_id'] == candidate['symbol_id'] for pos in self.active_positions):
            return False
            
        price = candidate.get('close')
        if not price or price <= 0:
            return False
            
        shares, cost = self._calculate_position_size(price)
        if shares == 0 or cost > self.capital:
            return False
            
        # Enforce Market Phase Cash Ratio
        total_equity = self.current_total_value
        target_cash_ratio = self._get_target_cash_ratio(phase)
        min_required_cash = total_equity * target_cash_ratio
        
        if (self.capital - cost) < (min_required_cash - 0.01):
            return False
            
        # Execute Buy
        self.capital -= cost
        position = {
            'symbol_id': candidate['symbol_id'],
            'ticker': candidate.get('ticker'),
            'entry_date': date,
            'entry_price': price,
            'shares': shares,
            'amount': cost,
            'score': candidate.get('score'),
            'rs21_rank': candidate.get('rs21_rank')
        }
        self.active_positions.append(position)
        return True

    def apply_exits(self, exits: List[Dict[str, Any]]):
        """
        Processes exits triggered by the simulator and updates capital and history.
        Supports both partial exits (1/3 take profit) and full exits.
        """
        partial_ratio = 0.333
        tax_rate = getattr(self, 'tax', 0.0)
        
        for exit_data in exits:
            symbol_id = exit_data['symbol_id']
            # Find the position
            pos = next((p for p in self.active_positions if p['symbol_id'] == symbol_id), None)
            if not pos:
                continue
                
            if exit_data.get('is_partial'):
                # 1/3 Partial Take Profit
                partial_shares = int(pos['shares'] * partial_ratio)
                if partial_shares <= 0:
                    partial_shares = 1 # Ensure at least 1 share
                    
                partial_amount = partial_shares * pos['entry_price']
                exit_amount = partial_shares * exit_data['exit_price']
                
                # PnL for this partial exit
                pnl_amount_partial = exit_amount - partial_amount
                
                # Update capital with cash from 1/3 sale
                self.capital += exit_amount
                
                # Apply tax if profit
                if tax_rate > 0.0 and pnl_amount_partial > 0:
                    tax_pay = pnl_amount_partial * tax_rate
                    self.capital -= tax_pay
                
                # Reduce the active position shares and initial cost amount
                pos['shares'] -= partial_shares
                pos['amount'] -= partial_amount
                
                # Mark as partial taken on the position so simulator knows
                pos['partial_taken'] = True
                pos['partial_exit_pnl_pct'] = exit_data['pnl_pct'] * 100.0
                pos['stop_price'] = pos['entry_price'] # Move stop to breakeven
            else:
                # Full Exit
                exit_amount = pos['shares'] * exit_data['exit_price']
                pnl_amount_full = exit_amount - pos['amount']
                
                if pos.get('partial_taken'):
                    # The raw pnl_pct returned by evaluate_exit_for_day is already the
                    # weighted average (1/3 partial PnL + 2/3 remaining PnL).
                    # So we calculate the real pnl_amount based on actual cash flows:
                    original_cost = (pos['shares'] / (1 - partial_ratio)) * pos['entry_price'] if pos['shares'] > 0 else pos['amount']
                    pnl_amount = original_cost * exit_data['pnl_pct']
                else:
                    pnl_amount = exit_amount - pos['amount']
                
                # Update capital with cash from full sale
                self.capital += exit_amount
                
                # Apply tax if profit on this final leg
                if tax_rate > 0.0 and pnl_amount_full > 0:
                    tax_pay = pnl_amount_full * tax_rate
                    self.capital -= tax_pay
                
                # Record trade
                trade_record = {
                    **pos,
                    'exit_date': exit_data['exit_date'],
                    'exit_price': exit_data['exit_price'],
                    'exit_reason': exit_data['reason'],
                    'pnl_pct': exit_data['pnl_pct'],
                    'pnl_amount': pnl_amount,
                    'capital_after': self.capital
                }
                self.trade_history.append(trade_record)
                
                # Remove from active
                self.active_positions = [p for p in self.active_positions if p['symbol_id'] != symbol_id]


    def tighten_all_stop_losses(self):
        """
        過熱期 (OVERHEAT) に入った際、含み損ポジションは即損切り(買値に引き上げ)とし、
        含み益ポジションは買値（ブレイクイーブン）に引き上げて元本を能動的に防衛する。
        """
        for pos in self.active_positions:
            if 'entry_price' in pos:
                # 既に部分利確等で買値以上に引き上げられていない場合のみ、買値（entry_price）に強制引き上げ
                current_stop = pos.get('stop_price', 0.0)
                if current_stop < pos['entry_price']:
                    pos['stop_price'] = pos['entry_price']
