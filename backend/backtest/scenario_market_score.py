import pandas as pd
import numpy as np
from enum import Enum
from typing import Dict, Tuple

class MarketPhase(Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"

class MarketTrendScorer:
    """
    Evaluates market conditions based on SPY and ^VIX data to produce a Trend Score
    and classify the market into a phase (BULL, NEUTRAL, BEAR).
    """
    
    DEFAULT_WEIGHTS = {
        'spy_sma20': 0.4,   # Weight for SPY being above/below its 20-day SMA
        'vix_threshold': 0.3, # Weight for VIX being below/above 20
        'ftd_dd': 0.2,      # Weight for Follow-Through Day (+) or Distribution Day (-)
        'spy_1d': 0.1       # Weight for SPY daily percentage change direction
    }
    
    # Internal component scores range from -1.0 to 1.0
    # Final score is a weighted sum, resulting in a value between -1.0 and 1.0
    
    BULL_THRESHOLD = 0.2
    BEAR_THRESHOLD = -0.2

    def __init__(self, prices_df: pd.DataFrame, symbols_df: pd.DataFrame, weights: Dict[str, float] = None):
        """
        Initializes the scorer with price data and weights.
        
        Args:
            prices_df: DataFrame containing all ticker prices and indicators (must include SPY and ^VIX).
            symbols_df: DataFrame containing symbol mappings to identify SPY and ^VIX IDs.
            weights: Optional custom dictionary of component weights. Must sum to 1.0.
        """
        self.weights = weights if weights is not None else self.DEFAULT_WEIGHTS
        
        # Verify weights sum to 1.0 (approximately)
        assert abs(sum(self.weights.values()) - 1.0) < 1e-6, "Weights must sum to 1.0"
        
        # Extract symbol IDs for quick lookup
        self.spy_id = self._get_symbol_id(symbols_df, 'SPY')
        self.vix_id = self._get_symbol_id(symbols_df, '^VIX')
        
        # Pre-filter and index for O(1) daily lookup
        self._spy_by_date = {}
        self._vix_by_date = {}
        if self.spy_id is not None:
            spy_data = prices_df[prices_df['symbol_id'] == self.spy_id]
            self._spy_by_date = {row['date']: row for _, row in spy_data.iterrows()}
        if self.vix_id is not None:
            vix_data = prices_df[prices_df['symbol_id'] == self.vix_id]
            self._vix_by_date = {row['date']: row for _, row in vix_data.iterrows()}

    @staticmethod
    def _get_symbol_id(symbols_df: pd.DataFrame, ticker: str) -> int:
        match = symbols_df[symbols_df['ticker'] == ticker]
        if not match.empty:
            return match['id'].values[0]
        return None

    def evaluate_market_phase(self, target_date: pd.Timestamp) -> Tuple[float, MarketPhase]:
        """
        Evaluates the market conditions for a specific date.
        
        Returns:
            Tuple[float, MarketPhase]: The calculated trend score [-1.0 to 1.0] and the resulting market phase.
        """
        if self.spy_id is None or self.vix_id is None:
            return 0.0, MarketPhase.NEUTRAL
            
        spy_row = self._spy_by_date.get(target_date)
        vix_row = self._vix_by_date.get(target_date)
        
        if spy_row is None or vix_row is None:
            return 0.0, MarketPhase.NEUTRAL
        
        # 1. SPY vs SMA20
        spy_sma20_score = 0.0
        if 'close' in spy_row and 'sma_20' in spy_row and not pd.isna(spy_row['sma_20']):
            spy_sma20_score = 1.0 if spy_row['close'] > spy_row['sma_20'] else -1.0
            
        # 2. VIX Threshold
        vix_score = 0.0
        if 'close' in vix_row and not pd.isna(vix_row['close']):
            vix_score = 1.0 if vix_row['close'] < 20 else -1.0
            
        # 3. FTD / DD Signal
        ftd_dd_score = 0.0
        if 'ftd_signal' in spy_row and spy_row['ftd_signal']:
            ftd_dd_score = 1.0
        elif 'dd_signal' in spy_row and spy_row['dd_signal']:
            ftd_dd_score = -1.0
            
        # 4. SPY 1D% Direction
        spy_1d_score = 0.0
        if 'change_1d_pct' in spy_row and not pd.isna(spy_row['change_1d_pct']):
            spy_1d_score = 1.0 if spy_row['change_1d_pct'] > 0 else -1.0
            
        # Calculate weighted sum
        final_score = (
            spy_sma20_score * self.weights['spy_sma20'] +
            vix_score * self.weights['vix_threshold'] +
            ftd_dd_score * self.weights['ftd_dd'] +
            spy_1d_score * self.weights['spy_1d']
        )
        
        # Determine Phase
        phase = MarketPhase.NEUTRAL
        if final_score >= self.BULL_THRESHOLD:
            phase = MarketPhase.BULL
        elif final_score <= self.BEAR_THRESHOLD:
            phase = MarketPhase.BEAR
            
        return final_score, phase
