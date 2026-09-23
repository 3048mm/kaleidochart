"""
test_scenario_market_score.py — Unit Tests for Scenario Market Trend Scorer (TDD)
"""
import os
import sys
import unittest
import pandas as pd
import numpy as np

# Force UTF-8 environment
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Set up project path (tests -> backtest -> backend -> project_root と4段階上に遡る)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(project_root)

from backend.backtest.scenario_market_score import MarketTrendScorer, MarketPhase

class TestScenarioMarketTrendScorer(unittest.TestCase):
    
    def setUp(self):
        # 1. Prepare Mock symbols
        self.symbols_df = pd.DataFrame([
            {'id': 1, 'ticker': 'SPY', 'name': 'SPY ETF', 'category': '個別', 'active': 1},
            {'id': 2, 'ticker': '^VIX', 'name': 'VIX Index', 'category': '個別', 'active': 1},
            {'id': 3, 'ticker': '^VIX3M', 'name': 'VXV Index', 'category': '個別', 'active': 1}
        ])
        
        # 2. Prepare Mock prices including SMA indicators for SPY
        # We need close, sma_50 to compute SPY distance score
        dates = [pd.Timestamp('2026-05-17').date()]
        self.prices_df = pd.DataFrame([
            # SPY Price
            {
                'symbol_id': 1, 
                'date': dates[0], 
                'close': 100.0, 
                'sma_50': 100.0,  # SPY is at SMA50 -> distance_score = 0.50 (neutral)
                'volume': 1000000
            },
            # VIX Price (vix_close = 15.0)
            {
                'symbol_id': 2, 
                'date': dates[0], 
                'close': 15.0, 
                'volume': 0
            },
            # VXV Price (vxv_close = 16.5) -> VXV/VIX = 1.10
            {
                'symbol_id': 3, 
                'date': dates[0], 
                'close': 16.5, 
                'volume': 0
            }
        ])
        
        # 3. Prepare Mock Pre-calculated daily metrics (breadth & momentum)
        # breadth_sma50: 0.60 (60% above SMA50)
        self.daily_metrics = {
            dates[0]: {
                'breadth_sma50': 0.60
            }
        }
        
    def test_default_weights_calculation(self):
        """
        Verify that calculated Trend Score (0-100) matches the MTS v3 equal-weighted formula.
        """
        scorer = MarketTrendScorer(
            self.prices_df, 
            self.symbols_df, 
            daily_metrics=self.daily_metrics,
            use_vxv_vix=True,
            scaling_ratio=None
        )
        
        target_date = pd.Timestamp('2026-05-17').date()
        score, phase = scorer.evaluate_market_phase(target_date)
        
        # Calculate manually (MTS v3 equal-weighted score, Component D has weight 0.0):
        # Component A: VXV/VIX Score = (1.10 - 0.90) / (1.20 - 0.90) = 0.20 / 0.30 = 0.666667
        # Component B: Breadth Score = (0.60 - 0.20) / (0.75 - 0.20) = 0.40 / 0.55 = 0.727273
        # Component C1: SPY 50SMA/ATR distance = (0.0 - (-4.0)) / 12.0 = 0.333333
        # Component C2: SPY 200SMA/ATR distance = (0.0 - (-4.0)) / 20.0 = 0.20
        # Expected score: (0.666667 + 0.727273 + 0.333333 + 0.20) * 25.0 = 48.1818
        
        self.assertAlmostEqual(score, 48.1818, places=3)
        self.assertEqual(phase, MarketPhase.NEUTRAL) # Score 48.18 is between 40.0 and 60.0
        
    def test_phase_boundaries(self):
        """
        Verify the boundary values for Market Phase classification:
        - Score >= 60: BULL
        - Score <= 40: BEAR
        - 40 < Score < 60: NEUTRAL
        """
        target_date = pd.Timestamp('2026-05-17').date()
        
        # Case A: Force low scores (BEAR)
        # VIX = 30.0 -> fallback ratio = 1.15 - (30 - 12) * (0.25 / 23) = 0.9543
        # vxv_vix_score = (0.9543 - 0.90) / 0.35 = 0.155
        # breadth = 0.1
        # SPY distance: close = 90.0, sma_50 = 100.0 -> diff = -10% -> clipped to 0.0
        # Distribution days: let's assume 11 (we will inject) -> score = 0.0 (weight 0.0)
        # Equal-weighted: (0.155 + 0.10 + 0.0 + 0.0) * 25.0 = 6.375% (BEAR)
        prices_bear = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 90.0, 'sma_50': 100.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 30.0, 'volume': 0}
        ])
        metrics_bear = {target_date: {'breadth_sma50': 0.1}}
        
        scorer_bear = MarketTrendScorer(prices_bear, self.symbols_df, daily_metrics=metrics_bear, use_vxv_vix=True)
        # Inject distribution days
        scorer_bear._spy_mts_v3_metrics[target_date]['distribution_days'] = 11
        
        score, phase = scorer_bear.evaluate_market_phase(target_date)
        self.assertEqual(phase, MarketPhase.BEAR)
        
        # Case B: Force Neutral score
        # Let's target score around 50.0 (NEUTRAL)
        prices_neutral = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 100.0, 'sma_50': 100.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 16.0, 'volume': 0},
            {'symbol_id': 3, 'date': target_date, 'close': 17.2, 'volume': 0} # 17.2/16 = 1.075 -> score = 0.50
        ])
        metrics_neutral = {target_date: {'breadth_sma50': 0.60}}
        
        scorer_neutral = MarketTrendScorer(prices_neutral, self.symbols_df, daily_metrics=metrics_neutral, use_vxv_vix=True)
        scorer_neutral._spy_mts_v3_metrics[target_date]['distribution_days'] = 8
        
        score, phase = scorer_neutral.evaluate_market_phase(target_date)
        self.assertEqual(phase, MarketPhase.NEUTRAL)

    def test_fallback_calculation_pre_2020(self):
        """
        Verify VIX-based fallback when VXV is missing (pre-2020 simulation).
        """
        target_date = pd.Timestamp('2026-05-17').date()
        
        # Missing VXV symbol ID or price
        prices_missing_vxv = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 100.0, 'sma_50': 100.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 15.0, 'volume': 0}
        ])
        
        scorer = MarketTrendScorer(
            prices_missing_vxv,
            self.symbols_df,
            daily_metrics=self.daily_metrics,
            use_vxv_vix=True,
            scaling_ratio=None
        )
        
        ratio = scorer.get_vxv_vix_ratio(target_date)
        # Fallback ratio = 1.15 - (15 - 12) * (0.25 / 23) = 1.15 - 0.0326 = 1.11739
        self.assertAlmostEqual(ratio, 1.11739, places=4)

    # ------------------------------------------------------------------
    # 5-21: has_breadth を日付ゲートではなく breadth の有無（NaN 判定）で決める
    # ------------------------------------------------------------------
    def _three_comp_expected(self):
        """breadth を使わない3成分スコア（setUp のデータ）。
        A=0.666667, C1=0.333333, C2=0.20 → (A+C1+C2)/3*100 = 40.0"""
        return (0.666667 + 0.333333 + 0.20) / 3.0 * 100.0

    def test_nan_breadth_uses_three_components(self):
        """breadth_sma50 が NaN（判定不能）の日は3成分スコア（breadth を混ぜない）。"""
        target_date = pd.Timestamp('2026-05-17').date()
        scorer = MarketTrendScorer(
            self.prices_df, self.symbols_df,
            daily_metrics={target_date: {'breadth_sma50': float('nan')}},
            use_vxv_vix=True, scaling_ratio=None
        )
        score, _ = scorer.evaluate_market_phase(target_date)
        self.assertAlmostEqual(score, self._three_comp_expected(), places=3)

    def test_valid_breadth_before_2018_04_is_used_no_date_gate(self):
        """T5 と揃えて日付ゲート（2018-04-01）を撤去した。breadth が算出できていれば
        2018-04 より前の日付でも4成分スコアを使う。"""
        target_date = pd.Timestamp('2017-06-01').date()
        prices = self.prices_df.copy()
        prices['date'] = target_date
        scorer = MarketTrendScorer(
            prices, self.symbols_df,
            daily_metrics={target_date: {'breadth_sma50': 0.60}},
            use_vxv_vix=True, scaling_ratio=None
        )
        score, _ = scorer.evaluate_market_phase(target_date)
        self.assertAlmostEqual(score, 48.1818, places=3)


if __name__ == '__main__':
    unittest.main()
