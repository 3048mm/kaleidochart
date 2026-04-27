import pandas as pd
import numpy as np

# We can import calculate_ema_tv from the newly created moving_averages
from .moving_averages import calculate_ema_tv

def calc_relative_strength(df: pd.DataFrame, df_spy: pd.DataFrame = None) -> pd.DataFrame:
    """Calculate Relative Strength and RRG (Relative Rotation Graph) factors.
    Requires df_spy to be passed. If not passed, RS features will not be calculated.
    """
    close = df['close']
    
    if df_spy is None or df_spy.empty:
        df['rs_blue_dot'] = 0
        df['rs_red_dot'] = 0
        return df

    # --- SPY close / volume の準備 ---
    spy_ref = df_spy[['date', 'close', 'volume']].rename(
        columns={'close': 'spy_close', 'volume': 'spy_volume'}
    )
    df = pd.merge(df, spy_ref, on='date', how='left')
    df['spy_close']  = df['spy_close'].ffill()
    df['spy_volume'] = df['spy_volume'].ffill().astype(float)

    # RS = Close / SPY_Close
    df['relative_strength_spy'] = np.where(
        df['spy_close'].isna() | (df['spy_close'] == 0),
        np.nan,
        close / df['spy_close']
    )
    rs = df['relative_strength_spy']

    # RS Condition (n) = RS / SMA(RS, n)
    for n in [14, 21, 63]:
        rs_sma = rs.rolling(window=n, min_periods=max(1, n//2)).mean()
        df[f'rs_condition_{n}'] = np.where(
            rs_sma.isna() | (rs_sma == 0), np.nan, rs / rs_sma
        )

    # RS-EMA, RS-Ratio and RS-Momentum (Refined JdK methodology)
    for n in [14, 21, 63]:
        # 1. RS-EMA (Smoothing of Relative Strength)
        rs_ema = calculate_ema_tv(rs, n)
        df[f'rs_ema_{n}'] = rs_ema
        
        # 2. RS-Ratio (Z-score of smoothed RS over n days)
        rs_mean = rs_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        rs_std  = rs_ema.rolling(window=n, min_periods=max(1, n//2)).std()
        df[f'rs_ratio_{n}'] = np.where(
            rs_std.isna() | (rs_std == 0), np.nan, (rs_ema - rs_mean) / rs_std
        )

        # 3. RS-Momentum (ROC of Ratio + EMA Smoothing + Z-score)
        ratio_val = df[f'rs_ratio_{n}']
        ratio_offset = ratio_val + 100.0
        # Standard daily RRG uses a 14-day ROC period.
        roc = (ratio_offset / ratio_offset.shift(14)) * 100.0
        
        # Smooth the ROC recursively
        roc_ema = calculate_ema_tv(roc, n)
        df[f'rs_roc_ema_{n}'] = roc_ema
        
        # Standardize the smoothed ROC
        roc_mean = roc_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        roc_std  = roc_ema.rolling(window=n, min_periods=max(1, n//2)).std()
        df[f'rs_momentum_{n}'] = np.where(
            roc_std.isna() | (roc_std == 0), np.nan, (roc_ema - roc_mean) / roc_std
        )

    # RS Leading Signals (Blue Dot / Red Dot)
    # 1. RS Blue Dot (Bullish Leading)
    rs_252_high = rs.rolling(window=252, min_periods=1).max()
    close_252_high = close.rolling(window=252, min_periods=1).max()
    
    df['rs_blue_dot'] = np.where(
        rs.isna() | rs_252_high.isna(),
        0,
        np.where((rs >= rs_252_high) & (close < close_252_high), 1, 0)
    )

    # 2. RS Red Dot (Bearish Leading)
    rs_252_low = rs.rolling(window=252, min_periods=1).min()
    close_252_low = close.rolling(window=252, min_periods=1).min()
    
    df['rs_red_dot'] = np.where(
        rs.isna() | rs_252_low.isna(),
        0,
        np.where((rs <= rs_252_low) & (close > close_252_low), 1, 0)
    )

    return df
