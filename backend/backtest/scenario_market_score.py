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
        use_vxv_vix: bool = True,
        scaling_ratio: Optional[float] = None
    ):
        """
        Initializes the scorer with price data, pre-calculated daily metrics, and weights.
        """
        self.weights = weights if weights is not None else self.DEFAULT_WEIGHTS
        self.daily_metrics = daily_metrics if daily_metrics is not None else {}
        self.use_vxv_vix = use_vxv_vix
        self.scaling_ratio = scaling_ratio
        
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

        # Pre-calculate SPY specific metrics for MTS v3 (Distribution Days, 50SMA/ATR, 200SMA/ATR)
        self._spy_mts_v3_metrics = {}
        if self.spy_id is not None and not prices_df.empty:
            spy_data = prices_df[prices_df['symbol_id'] == self.spy_id].copy()
            spy_data = spy_data.sort_values('date').reset_index(drop=True)
            
            # Use pre-computed T3 indicator values if available (merged into prices_df by scenario_runner).
            # Only fall back to rolling calculation if T3 data is absent (e.g. standalone mode).
            has_t3_atr  = 'atr_14' in spy_data.columns and spy_data['atr_14'].notna().any()
            has_t3_sma50  = 'sma_50' in spy_data.columns and spy_data['sma_50'].notna().any()
            has_t3_sma200 = 'sma_200' in spy_data.columns and spy_data['sma_200'].notna().any()

            if not has_t3_atr:
                if 'high' in spy_data.columns and 'low' in spy_data.columns:
                    high = spy_data['high']
                    low = spy_data['low']
                    close_prev = spy_data['close'].shift(1)
                    tr = pd.concat([
                        high - low,
                        (high - close_prev).abs(),
                        (low - close_prev).abs()
                    ], axis=1).max(axis=1)
                    spy_data['atr_14'] = tr.rolling(14, min_periods=1).mean().ffill().fillna(1.0)
                else:
                    spy_data['atr_14'] = 1.0

            if not has_t3_sma50:
                spy_data['sma_50'] = spy_data['close'].rolling(50, min_periods=1).mean()
            if not has_t3_sma200:
                spy_data['sma_200'] = spy_data['close'].rolling(200, min_periods=1).mean()
            spy_data['atr_pct_14'] = np.where(spy_data['close'] == 0, 0.0, (spy_data['atr_14'] / spy_data['close']) * 100)
            
            # Calculate distribution days
            close = spy_data['close']
            volume = spy_data['volume'].astype(float)
            daily_ret = close.pct_change()
            vol_increase = volume > volume.shift(1)
            is_dist_day = (daily_ret <= -0.002) & vol_increase
            spy_data['distribution_days'] = is_dist_day.rolling(window=25, min_periods=1).sum().astype(int)
            
            # Map by date for quick daily lookup
            for _, row in spy_data.iterrows():
                dt = row['date']
                close_val = row['close']
                sma50 = row['sma_50']
                sma200 = row['sma_200']
                atr_pct = row['atr_pct_14'] if row['atr_pct_14'] > 0 else 1.0
                dist_days = row['distribution_days']
                
                # ATR distances (SMA %-based)
                dist_50sma_atr = ((close_val / sma50 * 100) - 100) / atr_pct if sma50 > 0 else 0.0
                dist_200sma_atr = ((close_val / sma200 * 100) - 100) / atr_pct if sma200 > 0 else 0.0
                
                self._spy_mts_v3_metrics[dt] = {
                    'dist_50sma_atr': dist_50sma_atr,
                    'dist_200sma_atr': dist_200sma_atr,
                    'distribution_days': dist_days
                }

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
        Calculates the VXV/VIX ratio on a specific date with fallback.
        """
        vxv_row = self._vxv_by_date.get(target_date)
        vix_row = self._vix_by_date.get(target_date)
        
        vxv_val = vxv_row.get('close') if vxv_row is not None else None
        vix_val = vix_row.get('close') if vix_row is not None else None
        
        if vxv_val is not None and vix_val is not None and not pd.isna(vxv_val) and not pd.isna(vix_val) and vix_val > 0:
            return float(vxv_val / vix_val)
        elif vix_val is not None and not pd.isna(vix_val):
            # Fallback estimation based on VIX if VXV is missing
            ratio = 1.15 - (vix_val - 12.0) * (0.25 / 23.0)
            return float(max(0.85, min(1.30, ratio)))
        return None

    def evaluate_market_phase(self, target_date: object) -> Tuple[float, MarketPhase]:
        """
        Evaluates the market conditions for a specific date using exact MTS v3 formulas.
        
        Returns:
            Tuple[float, MarketPhase]: The Trend Score [0.0 to 100.0] and the resulting market phase.
        """
        # Component A: VXV/VIX Ratio (0.90 to 1.20)
        ratio = self.get_vxv_vix_ratio(target_date)
        if ratio is None:
            ratio = 1.15
            
        vxv_vix_score = (ratio - 0.90) / (1.20 - 0.90)
        vxv_vix_score = max(0.0, min(1.0, vxv_vix_score))

        # Component B: Market Breadth Component (0.20 to 0.75)
        metrics = self.daily_metrics.get(target_date, {})
        breadth_val = metrics.get('breadth_sma50')
        
        date_str = str(target_date)
        has_breadth = (breadth_val is not None) and (date_str >= '2018-04-01')
        if has_breadth:
            breadth_score = (breadth_val - 0.20) / (0.75 - 0.20)
            breadth_score = max(0.0, min(1.0, breadth_score))
        else:
            breadth_score = 0.0

        spy_mts_data = self._spy_mts_v3_metrics.get(target_date, {})

        # Component C1: SPY 50SMA / ATR Distance Score (-4.0 to +8.0)
        dist_50sma = spy_mts_data.get('dist_50sma_atr', 0.0)
        score_50sma_atr = (dist_50sma - (-4.0)) / (8.0 - (-4.0))
        score_50sma_atr = max(0.0, min(1.0, score_50sma_atr))

        # Component C2: SPY 200SMA / ATR Distance Score (-4.0 to +16.0)
        dist_200sma = spy_mts_data.get('dist_200sma_atr', 0.0)
        score_200sma_atr = (dist_200sma - (-4.0)) / (16.0 - (-4.0))
        score_200sma_atr = max(0.0, min(1.0, score_200sma_atr))

        # Weight and aggregate score (4 components if breadth exists, otherwise 3 components)
        if has_breadth:
            w_vxv = 0.25
            w_brd = 0.25
            w_c50 = 0.25
            w_c200 = 0.25
        else:
            w_vxv = 1.0 / 3.0
            w_brd = 0.0
            w_c50 = 1.0 / 3.0
            w_c200 = 1.0 / 3.0
        
        final_score = (
            vxv_vix_score * w_vxv +
            breadth_score * w_brd +
            score_50sma_atr * w_c50 +
            score_200sma_atr * w_c200
        ) * 100.0
        
        # Apply Scaling Ratio around median 50.0
        if self.scaling_ratio is not None:
            final_score = (final_score - 50.0) * self.scaling_ratio + 50.0
            
        final_score = float(np.clip(final_score, 0.0, 100.0))

        # Determine Market Phase based on 60/40 rule
        phase = MarketPhase.NEUTRAL
        if final_score >= self.BULL_THRESHOLD:
            phase = MarketPhase.BULL
        elif final_score <= self.BEAR_THRESHOLD:
            phase = MarketPhase.BEAR
            
        return final_score, phase
