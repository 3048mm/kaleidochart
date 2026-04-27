import pandas as pd
import numpy as np
from numba import njit

@njit
def _ema_kernel(values, alpha, initial_sma, start_idx):
    """Numba-accelerated recursive EMA loop."""
    res = np.full(values.shape, np.nan)
    res[start_idx] = initial_sma
    curr_ema = initial_sma
    for i in range(start_idx + 1, len(values)):
        val = values[i]
        if not np.isnan(val):
            curr_ema = val * alpha + curr_ema * (1 - alpha)
            res[i] = curr_ema
    return res

def calculate_ema_tv(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average matching TradingView (Pine Script 'ta.ema').
    Starts with SMA(period) as the initial value after skipping leading NaNs.
    """
    valid_series = series.dropna()
    if len(valid_series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)
    
    alpha = 2 / (period + 1)
    
    first_valid_idx = valid_series.index[0]
    start_pos = series.index.get_loc(first_valid_idx)
    
    initial_sma = valid_series.iloc[:period].mean()
    
    seed_idx_in_valid = period - 1
    seed_actual_idx = valid_series.index[seed_idx_in_valid]
    seed_pos = series.index.get_loc(seed_actual_idx)
    
    ema_vals = _ema_kernel(series.values, alpha, initial_sma, seed_pos)
    
    return pd.Series(ema_vals, index=series.index)

def calc_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate Simple and Exponential Moving Averages."""
    close = df['close']
    for period in [5, 21, 50, 63, 150, 200]:
        df[f'sma_{period}'] = close.rolling(window=period, min_periods=1).mean()
        df[f'ema_{period}'] = calculate_ema_tv(close, period)
    return df
