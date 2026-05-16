import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from backend.backtest.scenario_market_score import MarketTrendScorer, MarketPhase

@pytest.fixture
def sample_market_data():
    """
    Creates a mock DataFrame containing necessary columns for MarketTrendScorer.
    Columns needed: date, close, sma_20, change_1d_pct, ftd_signal, dd_signal
    And ^VIX data mapped appropriately.
    """
    dates = pd.date_range(start='2024-01-01', periods=10, freq='B')
    
    # SPY data mock
    spy_df = pd.DataFrame({
        'date': dates,
        'symbol_id': 1, # Assume SPY id is 1
        'close': [100, 102, 101, 105, 104, 106, 108, 107, 110, 112],
        'sma_20': [98, 99, 100, 101, 102, 103, 104, 105, 106, 107],
        'change_1d_pct': [0, 0.02, -0.01, 0.04, -0.01, 0.02, 0.02, -0.01, 0.03, 0.02],
        'ftd_signal': [False, False, False, True, False, False, False, False, False, False],
        'dd_signal': [False, False, True, False, False, False, False, True, False, False]
    })
    
    # VIX data mock
    vix_df = pd.DataFrame({
        'date': dates,
        'symbol_id': 2, # Assume ^VIX id is 2
        'close': [15.0, 14.5, 16.0, 13.5, 14.0, 13.0, 12.5, 15.5, 12.0, 11.5]
    })
    
    return pd.concat([spy_df, vix_df], ignore_index=True)

@pytest.fixture
def mock_symbols():
    return pd.DataFrame({
        'id': [1, 2],
        'ticker': ['SPY', '^VIX']
    })

def test_market_trend_scorer_initialization(sample_market_data, mock_symbols):
    scorer = MarketTrendScorer(sample_market_data, mock_symbols)
    assert scorer is not None
    assert 'SPY' in scorer.tickers
    assert '^VIX' in scorer.tickers

def test_market_trend_scorer_evaluates_correct_phase(sample_market_data, mock_symbols):
    scorer = MarketTrendScorer(sample_market_data, mock_symbols)
    
    # Date 4: SPY > SMA20, VIX < 20, FTD = True => Bull Phase expected
    # Score details based on weights (default expected logic):
    # FTD: +1, SPY > SMA20: +1, VIX < 20: +1, SPY 1D% > 0: +1
    date_to_test = pd.Timestamp('2024-01-04')
    
    score, phase = scorer.evaluate_market_phase(date_to_test)
    
    assert isinstance(score, float) or isinstance(score, int)
    assert isinstance(phase, MarketPhase)
    # With typical positive conditions, it should be BULL
    assert phase == MarketPhase.BULL

def test_market_trend_scorer_handles_bear_conditions(sample_market_data, mock_symbols):
    # Modify data for a clear BEAR scenario
    data = sample_market_data.copy()
    
    # Force SPY < SMA20 and VIX > 20 on a specific date
    target_date = pd.Timestamp('2024-01-03')
    
    # SPY
    data.loc[(data['date'] == target_date) & (data['symbol_id'] == 1), 'close'] = 95
    data.loc[(data['date'] == target_date) & (data['symbol_id'] == 1), 'sma_20'] = 100
    data.loc[(data['date'] == target_date) & (data['symbol_id'] == 1), 'change_1d_pct'] = -0.02
    
    # VIX
    data.loc[(data['date'] == target_date) & (data['symbol_id'] == 2), 'close'] = 25.0
    
    scorer = MarketTrendScorer(data, mock_symbols)
    score, phase = scorer.evaluate_market_phase(target_date)
    
    assert phase == MarketPhase.BEAR

def test_market_trend_scorer_missing_data(sample_market_data, mock_symbols):
    # Test behavior when requesting a date not in the data
    scorer = MarketTrendScorer(sample_market_data, mock_symbols)
    missing_date = pd.Timestamp('2025-01-01')
    
    score, phase = scorer.evaluate_market_phase(missing_date)
    
    # Should fallback to NEUTRAL or handle gracefully
    assert phase == MarketPhase.NEUTRAL
    assert score == 0.0

def test_market_trend_scorer_weights_customization(sample_market_data, mock_symbols):
    # Custom weights
    custom_weights = {
        'spy_sma20': 0.5,
        'vix_threshold': 0.3,
        'ftd_dd': 0.2,
        'spy_1d': 0.0
    }
    scorer = MarketTrendScorer(sample_market_data, mock_symbols, weights=custom_weights)
    
    target_date = pd.Timestamp('2024-01-04')
    score, phase = scorer.evaluate_market_phase(target_date)
    
    # Score should be bound between -1 and 1
    assert -1.0 <= score <= 1.0
