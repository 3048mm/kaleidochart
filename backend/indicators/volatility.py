import pandas as pd
import numpy as np
import ta
from numba import njit

@njit
def _td9_kernel(close_values, compare_values):
    """Numba-accelerated TD Sequential (TD9) loop."""
    n = len(close_values)
    res = np.zeros(n)
    for i in range(4, n):
        if close_values[i] > compare_values[i]: # close > close[i-4]
            prev = res[i-1]
            count = prev if (0 < prev < 9) else 0
            res[i] = count + 1
        elif close_values[i] < compare_values[i]: # close < close[i-4]
            prev = res[i-1]
            count = prev if (-9 < prev < 0) else 0
            res[i] = count - 1
    return res

def calc_volatility(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate Volatility Indicators (ATR, ADR, VCR, TD9, SMA50 Distance).
    Requires 'sma_50' column to be present for sma50_atr_mult.
    """
    close = df['close']
    high = df['high']
    low = df['low']
    
    # 3. ATR (14日) — raw & %
    try:
        atr_indicator = ta.volatility.AverageTrueRange(
            high=high, low=low, close=close, window=14, fillna=True
        )
        atr_values = atr_indicator.average_true_range()
        df['atr_14'] = atr_values
        df['atr_pct_14'] = np.where(close == 0, 0, (atr_values / close) * 100)
    except Exception:
        df['atr_14'] = np.nan
        df['atr_pct_14'] = np.nan

    # 4. ADR% (21日) — Average Daily Range as % of Low
    daily_range_pct = np.where(low == 0, np.nan, (high - low) / low * 100)
    df['adr_pct_21'] = pd.Series(daily_range_pct).rolling(window=21, min_periods=1).mean().values

    # 5. Distance from SMA50 in ATR multiples
    if 'sma_50' in df.columns:
        df['sma50_atr_mult'] = np.where(
            df['atr_pct_14'].isna() | (df['atr_pct_14'] == 0) | df['sma_50'].isna() | (df['sma_50'] == 0),
            np.nan,
            ((close / df['sma_50'] * 100) - 100) / df['atr_pct_14']
        )
    else:
        df['sma50_atr_mult'] = np.nan

    # 6. TD Sequential (TD9)
    close_vals = close.values
    compare_vals = close.shift(4).values
    df['td9'] = _td9_kernel(close_vals, compare_vals)

    # 7. Volatility Contraction Ratio (VCR) = Simple ATR(10) / Simple ATR(50)
    tr_vals = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr_10 = tr_vals.rolling(window=10, min_periods=10).mean()
    atr_50 = tr_vals.rolling(window=50, min_periods=50).mean()
    df['vcr'] = np.where(atr_50.isna() | (atr_50 == 0), np.nan, atr_10 / atr_50)

    return df
