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
        df['is_rs_blue_dot'] = 0
        df['is_rs_red_dot'] = 0
        return df

    # --- SPY close / volume の準備 ---
    spy_ref = df_spy[['date', 'close', 'volume']].rename(
        columns={'close': 'spy_close', 'volume': 'spy_volume'}
    )
    df = pd.merge(df, spy_ref, on='date', how='left')
    df['spy_close']  = df['spy_close'].ffill()
    df['spy_volume'] = df['spy_volume'].ffill().astype(float)

    # rs_value = Close / SPY_Close
    df['rs_value'] = np.where(
        df['spy_close'].isna() | (df['spy_close'] == 0),
        np.nan,
        close / df['spy_close']
    )
    rs = df['rs_value']

    # rs_value_e5 (Smoothing for rs_trend)
    rs_ema_5 = calculate_ema_tv(rs, 5)
    df['rs_value_e5'] = rs_ema_5

    # rs_trend_sN = rs_value_e5 / SMA(rs_value, N)
    for n in [5, 14, 21, 63, 200]:
        rs_sma = rs.rolling(window=n, min_periods=max(1, n//2)).mean()
        df[f'rs_trend_s{n}'] = np.where(
            rs_sma.isna() | (rs_sma == 0), np.nan, rs_ema_5 / rs_sma
        )

    # rs_value_eN, rs_ratio_eN, rs_momentum_eN (Refined JdK methodology)
    for n in [5, 14, 21, 63, 200]:
        # 1. rs_value_eN (Smoothing of rs_value)
        if n == 5:
            rs_ema = rs_ema_5
        else:
            rs_ema = calculate_ema_tv(rs, n)
            df[f'rs_value_e{n}'] = rs_ema
        
        # 2. rs_ratio_eN (Z-score of rs_value_eN over n days)
        rs_mean = rs_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        rs_std  = rs_ema.rolling(window=n, min_periods=max(1, n//2)).std()
        df[f'rs_ratio_e{n}'] = np.where(
            rs_std.isna() | (rs_std == 0), np.nan, (rs_ema - rs_mean) / rs_std
        )

        # 3. rs_momentum_eN (ROC of Ratio + EMA Smoothing + Z-score)
        ratio_val = df[f'rs_ratio_e{n}']
        ratio_offset = ratio_val + 100.0
        # Standard daily RRG uses a 14-day ROC period.
        roc = (ratio_offset / ratio_offset.shift(14)) * 100.0
        
        # Smooth the ROC recursively
        roc_ema = calculate_ema_tv(roc, n)
        df[f'rs_roc_ema_{n}'] = roc_ema
        
        # Standardize the smoothed ROC
        roc_mean = roc_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        roc_std  = roc_ema.rolling(window=n, min_periods=max(1, n//2)).std()
        df[f'rs_momentum_e{n}'] = np.where(
            roc_std.isna() | (roc_std == 0), np.nan, (roc_ema - roc_mean) / roc_std
        )

    # RS Leading Signals (Blue Dot / Red Dot)
    # 1. is_rs_blue_dot (Bullish Leading)
    rs_252_high = rs.rolling(window=252, min_periods=1).max()
    close_252_high = close.rolling(window=252, min_periods=1).max()
    
    df['is_rs_blue_dot'] = np.where(
        rs.isna() | rs_252_high.isna(),
        0,
        np.where((rs >= rs_252_high) & (close < close_252_high), 1, 0)
    )

    # 2. is_rs_red_dot (Bearish Leading)
    rs_252_low = rs.rolling(window=252, min_periods=1).min()
    close_252_low = close.rolling(window=252, min_periods=1).min()
    
    df['is_rs_red_dot'] = np.where(
        rs.isna() | rs_252_low.isna(),
        0,
        np.where((rs <= rs_252_low) & (close > close_252_low), 1, 0)
    )

    # --- RS-MACD(5, 21, 5) ---
    df['rs_macd_line_21'] = df['rs_value_e5'] - df['rs_value_e21']
    df['rs_macd_signal_21'] = calculate_ema_tv(df['rs_macd_line_21'], 5)
    df['rs_macd_hist_21'] = df['rs_macd_line_21'] - df['rs_macd_signal_21']

    return df
