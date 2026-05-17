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

# Set up project path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from backend.backtest.scenario_market_score import MarketTrendScorer, MarketPhase

class TestScenarioMarketTrendScorer(unittest.TestCase):
    
    def setUp(self):
        # 1. Prepare Mock symbols
        self.symbols_df = pd.DataFrame([
            {'id': 1, 'ticker': 'SPY', 'name': 'SPY ETF', 'category': '個別', 'active': 1},
            {'id': 2, 'ticker': '^VIX', 'name': 'VIX Index', 'category': '個別', 'active': 1}
        ])
        
        # 2. Prepare Mock prices including SMA indicators for SPY
        # We need close, ema_21, sma_50, sma_200 to compute SPY score
        dates = [pd.Timestamp('2026-05-17').date()]
        self.prices_df = pd.DataFrame([
            # SPY Price
            {
                'symbol_id': 1, 
                'date': dates[0], 
                'close': 100.0, 
                'ema_21': 95.0,   # SPY > EMA21 (+8.33)
                'sma_50': 105.0,  # SPY < SMA50 (0.0)
                'sma_200': 90.0,  # SPY > SMA200 (+8.34)
                'volume': 1000000
            },
            # VIX Price (vix_close = 15.0)
            {
                'symbol_id': 2, 
                'date': dates[0], 
                'close': 15.0, 
                'volume': 0
            }
        ])
        
        # spy_score:
        # close(100.0) > ema_21(95.0)  -> 8.33
        # close(100.0) > sma_50(105.0) -> 0.0
        # close(100.0) > sma_200(90.0) -> 8.34
        # Total spy_score = 16.67 (out of 25.0)
        
        # vix_score:
        # vix_val = 15.0
        # vix_score = 25.0 * (35.0 - 15.0) / (35.0 - 12.0) = 25.0 * 20 / 23 = 21.7391
        
        # 3. Prepare Mock Pre-calculated daily metrics (breadth & momentum)
        # breadth_sma50: 0.60 (60% above SMA50) -> breadth_score = 0.60 * 25.0 = 15.0
        # momentum_ratio: 0.80 (80% positive day) -> momentum_score = 0.80 * 25.0 = 20.0
        self.daily_metrics = {
            dates[0]: {
                'breadth_sma50': 0.60,
                'momentum_ratio': 0.80
            }
        }
        
    def test_default_weights_calculation(self):
        """
        Verify that with uniform weights (each 25%), the calculated Trend Score (0-100)
        perfectly matches the dashboard formulation.
        """
        # Equal weights (sum to 1.0)
        weights = {
            'spy_trend': 0.25,
            'breadth': 0.25,
            'momentum': 0.25,
            'vix': 0.25
        }
        
        scorer = MarketTrendScorer(
            self.prices_df, 
            self.symbols_df, 
            daily_metrics=self.daily_metrics,
            weights=weights
        )
        
        target_date = pd.Timestamp('2026-05-17').date()
        score, phase = scorer.evaluate_market_phase(target_date)
        
        # Calculate manually:
        # spy_score: 16.67
        # breadth_score: 15.0
        # momentum_score: 20.0
        # vix_score: 25 * 20 / 23 = 21.7391
        # Weighted sum:
        #   (16.67 * 0.25 * 4) + (15.0 * 0.25 * 4) + (20.0 * 0.25 * 4) + (21.7391 * 0.25 * 4)
        #   = 16.67 + 15.0 + 20.0 + 21.7391 = 73.4091
        
        self.assertAlmostEqual(score, 73.4091, places=3)
        self.assertEqual(phase, MarketPhase.BULL) # Score 73.4 >= 60.0
        
    def test_custom_weights_calculation(self):
        """
        Verify that when weights are skewed, the Trend Score reflects the new weights.
        """
        weights = {
            'spy_trend': 0.50, # 50%
            'breadth': 0.10,   # 10%
            'momentum': 0.10,  # 10%
            'vix': 0.30        # 30%
        }
        
        scorer = MarketTrendScorer(
            self.prices_df, 
            self.symbols_df, 
            daily_metrics=self.daily_metrics,
            weights=weights
        )
        
        target_date = pd.Timestamp('2026-05-17').date()
        score, phase = scorer.evaluate_market_phase(target_date)
        
        # Calculate manually with normalized multipliers:
        # spy_score_contrib: 16.67 * 0.50 * 4 = 33.34
        # breadth_score_contrib: 15.0 * 0.10 * 4 = 6.0
        # momentum_score_contrib: 20.0 * 0.10 * 4 = 8.0
        # vix_score_contrib: 21.7391 * 0.30 * 4 = 26.0869
        # Total expected: 33.34 + 6.0 + 8.0 + 26.0869 = 73.4269
        
        self.assertAlmostEqual(score, 73.4269, places=3)
        
    def test_phase_boundaries(self):
        """
        Verify the boundary values for Market Phase classification:
        - Score >= 60: BULL
        - Score <= 40: BEAR
        - 40 < Score < 60: NEUTRAL
        """
        # We will dynamically inject values to force different scores
        target_date = pd.Timestamp('2026-05-17').date()
        
        # Case A: Force low scores (BEAR)
        # spy_close = 80.0 (below EMA21, SMA50, SMA200 -> spy_score = 0.0)
        # vix_close = 32.0 (vix_score = 25 * 3 / 23 = 3.26)
        # breadth = 0.1 (breadth_score = 2.5)
        # momentum = 0.1 (momentum_score = 2.5)
        # Total expected (equal weights): 0.0 + 3.26 + 2.5 + 2.5 = 8.26 (BEAR)
        prices_bear = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 80.0, 'ema_21': 95.0, 'sma_50': 105.0, 'sma_200': 90.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 32.0, 'volume': 0}
        ])
        metrics_bear = {target_date: {'breadth_sma50': 0.1, 'momentum_ratio': 0.1}}
        
        scorer_bear = MarketTrendScorer(prices_bear, self.symbols_df, daily_metrics=metrics_bear)
        score, phase = scorer_bear.evaluate_market_phase(target_date)
        self.assertEqual(phase, MarketPhase.BEAR)
        
        # Case B: Force Neutral score
        # Let's target score = 50.0 (NEUTRAL)
        # spy_score: 16.67
        # breadth_score: 12.5 (breadth = 0.5)
        # momentum_score: 12.5 (momentum = 0.5)
        # vix_score: 8.33 (vix = 27.33)
        # Total = 16.67 + 12.5 + 12.5 + 8.33 = 50.0
        prices_neutral = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 100.0, 'ema_21': 95.0, 'sma_50': 105.0, 'sma_200': 90.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 27.3333, 'volume': 0}
        ])
        metrics_neutral = {target_date: {'breadth_sma50': 0.5, 'momentum_ratio': 0.5}}
        
        scorer_neutral = MarketTrendScorer(prices_neutral, self.symbols_df, daily_metrics=metrics_neutral)
        score, phase = scorer_neutral.evaluate_market_phase(target_date)
        self.assertEqual(phase, MarketPhase.NEUTRAL)

    def test_use_vxv_vix_calculation(self):
        """
        Verify the VXV/VIX Sentiment Score calculation when use_vxv_vix=True.
        """
        target_date = pd.Timestamp('2026-05-17').date()
        
        symbols_extended = pd.DataFrame([
            {'id': 1, 'ticker': 'SPY', 'name': 'SPY ETF', 'category': '個別', 'active': 1},
            {'id': 2, 'ticker': '^VIX', 'name': 'VIX Index', 'category': '個別', 'active': 1},
            {'id': 3, 'ticker': '^VIX3M', 'name': 'VXV Index', 'category': '個別', 'active': 1}
        ])
        
        prices_extended = pd.DataFrame([
            {'symbol_id': 1, 'date': target_date, 'close': 100.0, 'ema_21': 95.0, 'sma_50': 105.0, 'sma_200': 90.0, 'volume': 1000000},
            {'symbol_id': 2, 'date': target_date, 'close': 15.0, 'volume': 0},
            {'symbol_id': 3, 'date': target_date, 'close': 16.5, 'volume': 0} # VXV/VIX = 1.10
        ])
        
        weights = {
            'spy_trend': 0.25,
            'breadth': 0.25,
            'momentum': 0.25,
            'vix': 0.25
        }
        
        scorer = MarketTrendScorer(
            prices_extended,
            symbols_extended,
            daily_metrics=self.daily_metrics,
            use_vxv_vix=True,
            weights=weights
        )
        
        score, phase = scorer.evaluate_market_phase(target_date)
        
        # Calculate manually with VXV/VIX:
        # spy_score: 16.67
        # breadth_score: 15.0
        # momentum_score: 20.0
        # vxv_vix_ratio = 16.5 / 15.0 = 1.10
        # sentiment_score = 25.0 * (1.10 - 0.90) / (1.20 - 0.90) = 25.0 * 0.20 / 0.30 = 16.6667
        # Total expected score (equal weights): 16.67 + 15.0 + 20.0 + 16.6667 = 68.3367
        
        self.assertAlmostEqual(score, 68.3367, places=3)
        self.assertEqual(phase, MarketPhase.BULL)

if __name__ == '__main__':
    unittest.main()
