import sys
import os
import pandas as pd
import numpy as np
import pytest

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from indicators.calculate import calculate_indicators

def test_change_return_calculations():
    """change_1d_pct, change_1w_pct, change_1m_pct が正しく計算されるか検証"""
    periods = 10
    dates = pd.date_range(start='2025-01-01', periods=periods, freq='B')
    
    # Prices: 100, 110, 121, 133.1, 146.41, 161.051, ... (10% increase every day)
    prices = [100.0 * (1.1 ** i) for i in range(periods)]
    
    df_dummy = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': [p * 0.95 for p in prices],
        'high': [p * 1.05 for p in prices],
        'low': [p * 0.90 for p in prices],
        'volume': [1000000] * periods
    })
    
    df_spy = pd.DataFrame({
        'date': dates,
        'close': [300.0] * periods,
        'volume': [5000000] * periods
    })
    
    res = calculate_indicators(df_dummy, df_spy)
    
    assert 'change_1d_pct' in res.columns
    assert 'change_1w_pct' in res.columns
    assert 'change_1m_pct' in res.columns
    
    # 1-day: should all be ~10% except the first one
    assert pd.isna(res['change_1d_pct'].iloc[0])
    for i in range(1, periods):
        assert abs(res['change_1d_pct'].iloc[i] - 10.0) < 1e-5
        
    # 5-day (1-week):
    expected_5d = (1.1 ** 5 - 1) * 100
    assert pd.isna(res['change_1w_pct'].iloc[4]) # Not enough data until index 5
    assert abs(res['change_1w_pct'].iloc[5] - expected_5d) < 1e-5
