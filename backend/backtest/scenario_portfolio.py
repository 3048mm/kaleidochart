from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple
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

class ScenarioPortfolio:
    """
    Manages capital, active positions, and trade history for scenario testing.
    Determines position sizing and enforces cash ratios based on market phase.
    """
    def __init__(self, config: PortfolioConfig):
        self.config = config
        self.initial_capital = config.initial_capital
        self.capital = self.initial_capital
        self.active_positions: List[Dict[str, Any]] = []
        self.trade_history: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
        
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
        if len(self.active_positions) >= self.config.max_positions:
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
        """
        for exit_data in exits:
            symbol_id = exit_data['symbol_id']
            # Find the position
            pos = next((p for p in self.active_positions if p['symbol_id'] == symbol_id), None)
            if not pos:
                continue
                
            exit_amount = pos['shares'] * exit_data['exit_price']
            pnl_amount = exit_amount - pos['amount']
            
            # Update capital
            self.capital += exit_amount
            
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
