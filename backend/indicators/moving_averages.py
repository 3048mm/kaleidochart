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

def calculate_ema_tv(series: pd.Series, period: int, prev_ema: float = None) -> pd.Series:
    """
    Exponential Moving Average matching TradingView (Pine Script 'ta.ema').
    Starts with SMA(period) as the initial value after skipping leading NaNs.

    Args:
        prev_ema: 増分計算用。前日の EMA 値（state）を渡すと、SMA でシードを
            作り直さず、series の先頭行（index 0）にこの値をそのまま置いて
            そこから `_ema_kernel` で1歩ずつ継続する（T3 増分化計画 5-4）。
            None（既定）なら現行どおり全期間計算。
    """
    if prev_ema is not None:
        # シードを作り直さない経路。_ema_kernel は既にステップ実行のループなので、
        # 開始位置(0)と開始値(prev_ema)を差し替えるだけで継続計算になる。
        alpha = 2 / (period + 1)
        ema_vals = _ema_kernel(series.values.astype(float), alpha, float(prev_ema), 0)
        return pd.Series(ema_vals, index=series.index)

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

def calc_moving_averages(df: pd.DataFrame, state: dict = None) -> pd.DataFrame:
    """Calculate Simple and Exponential Moving Averages.

    Args:
        state: 増分計算用の前日値辞書（例: {'ema_200': 123.45, ...}）。
            None（既定）なら現行どおり全期間計算。
    """
    close = df['close']
    for period in [5, 21, 50, 63, 150, 200]:
        df[f'sma_{period}'] = close.rolling(window=period, min_periods=1).mean()
        prev_ema = state.get(f'ema_{period}') if state else None
        df[f'ema_{period}'] = calculate_ema_tv(close, period, prev_ema=prev_ema)
    return df
