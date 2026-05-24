import pandas as pd
import numpy as np
from enum import Enum
from typing import Dict, Tuple, Optional

class MarketPhase(Enum):
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"

class MarketTrendScorer:
    """
    Evaluates market conditions based on SPY, ^VIX, and overall market statistics
    to calculate the 0-100 numerical Market Trend Score and classify market phase.
    
    100% parity with the front-end dashboard / backend market_signals.py formulas.
    """
    
    DEFAULT_WEIGHTS = {
        'spy_trend': 0.222,
        'breadth': 0.274,
        'momentum': 0.273,
        'vix': 0.231
    }
    
    # Thresholds for classification matching the dashboard
    BULL_THRESHOLD = 60.0
    BEAR_THRESHOLD = 40.0

    def __init__(
        self, 
        prices_df: pd.DataFrame, 
        symbols_df: pd.DataFrame, 
        daily_metrics: Optional[Dict[object, Dict[str, float]]] = None,
        weights: Optional[Dict[str, float]] = None,
        use_vxv_vix: bool = False,
        scaling_ratio: Optional[float] = 1.7
    ):
        """
        Initializes the scorer with price data, pre-calculated daily metrics, and weights.
        
        Args:
            prices_df: DataFrame containing all ticker prices and indicators (must include SPY and ^VIX).
            symbols_df: DataFrame containing symbol mappings.
            daily_metrics: Dict mapping date -> {'breadth_sma50': float, 'momentum_ratio': float}
            weights: Optional custom dictionary of component weights. Must sum to 1.0.
            use_vxv_vix: If True, uses the VXV/VIX ratio instead of VIX directly.
            scaling_ratio: Optional scaling factor to stretch scores around median 50.0.
        """
        self.weights = weights if weights is not None else self.DEFAULT_WEIGHTS
        self.daily_metrics = daily_metrics if daily_metrics is not None else {}
        self.use_vxv_vix = use_vxv_vix
        self.scaling_ratio = scaling_ratio
        
        # Verify weights sum to 1.0 (approximately)
        assert abs(sum(self.weights.values()) - 1.0) < 1e-6, "Weights must sum to 1.0"
        
        # Extract symbol IDs for quick lookup
        self.spy_id = self._get_symbol_id(symbols_df, 'SPY')
        self.vix_id = self._get_symbol_id(symbols_df, '^VIX')
        self.vxv_id = self._get_symbol_id(symbols_df, '^VIX3M')
        
        # Pre-filter and index for O(1) daily lookup
        self._spy_by_date = {}
        self._vix_by_date = {}
        self._vxv_by_date = {}
        
        if self.spy_id is not None and not prices_df.empty:
            spy_data = prices_df[prices_df['symbol_id'] == self.spy_id]
            self._spy_by_date = {row['date']: row for _, row in spy_data.iterrows()}
            
        if self.vix_id is not None and not prices_df.empty:
            vix_data = prices_df[prices_df['symbol_id'] == self.vix_id]
            self._vix_by_date = {row['date']: row for _, row in vix_data.iterrows()}

        if self.vxv_id is not None and not prices_df.empty:
            vxv_data = prices_df[prices_df['symbol_id'] == self.vxv_id]
            self._vxv_by_date = {row['date']: row for _, row in vxv_data.iterrows()}

    @staticmethod
    def _get_symbol_id(symbols_df: pd.DataFrame, ticker: str) -> Optional[int]:
        if symbols_df.empty:
            return None
        match = symbols_df[symbols_df['ticker'] == ticker]
        if not match.empty:
            return int(match['id'].values[0])
        return None

    def get_vxv_vix_ratio(self, target_date: object) -> Optional[float]:
        """
        Calculates the VXV/VIX ratio on a specific date.
        """
        vxv_row = self._vxv_by_date.get(target_date)
        vix_row = self._vix_by_date.get(target_date)
        
        vxv_val = vxv_row.get('close') if vxv_row is not None else None
        vix_val = vix_row.get('close') if vix_row is not None else None
        
        if vxv_val is not None and vix_val is not None and not pd.isna(vxv_val) and not pd.isna(vix_val) and vix_val > 0:
            return float(vxv_val / vix_val)
        return None

    def evaluate_market_phase(self, target_date: object) -> Tuple[float, MarketPhase]:
        """
        Evaluates the market conditions for a specific date using exact dashboard formulas.
        
        Returns:
            Tuple[float, MarketPhase]: The Trend Score [0.0 to 100.0] and the resulting market phase.
        """
        # 1. SPY Trend Component (25 pts max)
        spy_score = 0.0
        spy_row = self._spy_by_date.get(target_date)
        if spy_row is not None:
            close = spy_row.get('close', 0.0)
            
            # EMA21
            ema21 = spy_row.get('ema_21')
            if ema21 is not None and not pd.isna(ema21) and close > ema21:
                spy_score += 8.33
                
            # SMA50
            sma50 = spy_row.get('sma_50')
            if sma50 is not None and not pd.isna(sma50) and close > sma50:
                spy_score += 8.33
                
            # SMA200
            sma200 = spy_row.get('sma_200')
            if sma200 is not None and not pd.isna(sma200) and close > sma200:
                spy_score += 8.34

        # 2. Market Breadth Component (25 pts max)
        # Ratio of active individual stocks above their SMA50
        metrics = self.daily_metrics.get(target_date, {})
        breadth_val = metrics.get('breadth_sma50', 0.5)
        breadth_score = breadth_val * 25.0

        # 3. Market Momentum Component (25 pts max)
        # Ratio of advancing active individual stocks vs yesterday
        momentum_val = metrics.get('momentum_ratio', 0.5)
        momentum_score = momentum_val * 25.0

        # 4. VIX Component (25 pts max)
        if self.use_vxv_vix:
            # VXV/VIX Ratio logic (Sentiment Score)
            vxv_row = self._vxv_by_date.get(target_date)
            vix_row = self._vix_by_date.get(target_date)
            
            vxv_val = vxv_row.get('close') if vxv_row is not None else None
            vix_val = vix_row.get('close') if vix_row is not None else None
            
            if vxv_val is not None and vix_val is not None and not pd.isna(vxv_val) and not pd.isna(vix_val) and vix_val > 0:
                ratio = vxv_val / vix_val
            else:
                # Default ratio fallback if data is missing or invalid
                ratio = 1.10
                
            # Linear map 0.90 to 1.20 into 0 to 25 pts
            vix_score = 25.0 * (ratio - 0.90) / (1.20 - 0.90)
            vix_score = float(np.clip(vix_score, 0.0, 25.0))
        else:
            # Calmness index of VIX (lower VIX yields higher score)
            vix_row = self._vix_by_date.get(target_date)
            vix_val = vix_row.get('close', 20.0) if vix_row is not None else 20.0
            if pd.isna(vix_val):
                vix_val = 20.0
                
            vix_score = 25.0 * (35.0 - vix_val) / (35.0 - 12.0)
            vix_score = float(np.clip(vix_score, 0.0, 25.0))

        # Calculate final weighted score normalized to [0, 100]
        # Since weights sum to 1.0, multiplying each weight by 4 (since max component is 25)
        # projects the final score perfectly to [0, 100].
        final_score = (
            spy_score * self.weights['spy_trend'] * 4.0 +
            breadth_score * self.weights['breadth'] * 4.0 +
            momentum_score * self.weights['momentum'] * 4.0 +
            vix_score * self.weights['vix'] * 4.0
        )
        
        # Apply Scaling Ratio around median 50.0
        if self.scaling_ratio is not None:
            final_score = (final_score - 50.0) * self.scaling_ratio + 50.0
            
        final_score = float(np.clip(final_score, 0.0, 100.0))

        # Determine Market Phase based on exact dashboard boundaries (60/40)
        phase = MarketPhase.NEUTRAL
        if final_score >= self.BULL_THRESHOLD:
            phase = MarketPhase.BULL
        elif final_score <= self.BEAR_THRESHOLD:
            phase = MarketPhase.BEAR
            
        return final_score, phase
