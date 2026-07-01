import pandas as pd
import numpy as np
import pytest
from indicators.relative_strength import calc_relative_strength

def test_rs_macd_calculation():
    # Create 50 days of dummy data
    dates = pd.date_range(start='2025-01-01', periods=50)
    # Target close prices
    close = [100.0 + i * 1.5 for i in range(50)]
    # SPY close prices
    spy_close = [200.0 + i * 1.0 for i in range(50)]
    
    df = pd.DataFrame({
        'date': dates,
        'close': close,
        'high': close,
        'low': close,
        'volume': [1000] * 50
    })
    
    df_spy = pd.DataFrame({
        'date': dates,
        'close': spy_close,
        'volume': [5000] * 50
    })
    
    res = calc_relative_strength(df, df_spy)
    
    # 1. Verify columns exist
    assert 'rs_macd_line_21' in res.columns
    assert 'rs_macd_signal_21' in res.columns
    assert 'rs_macd_hist_21' in res.columns
    
    # 2. Verify values match the math
    # line = rs_value_e5 - rs_value_e21
    # hist = line - signal
    latest = res.iloc[-1]
    
    expected_line = latest['rs_value_e5'] - latest['rs_value_e21']
    assert abs(latest['rs_macd_line_21'] - expected_line) < 1e-6
    
    expected_hist = latest['rs_macd_line_21'] - latest['rs_macd_signal_21']
    assert abs(latest['rs_macd_hist_21'] - expected_hist) < 1e-6
