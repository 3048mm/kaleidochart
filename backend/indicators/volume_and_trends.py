import pandas as pd
import numpy as np

def calc_volume_and_trends(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate Relative Volume, Distance from Highs, Up/Down Volume Ratio, and Trend Template."""
    close = df['close']
    high = df['high']
    volume = df['volume'].astype(float)

    # --- Relative Volume vs SPY ---
    if 'spy_volume' in df.columns:
        vol_sma_21     = volume.rolling(window=21, min_periods=1).mean()
        spy_vol_sma_21 = df['spy_volume'].rolling(window=21, min_periods=1).mean()

        df['vol_surge_21'] = np.where(
            vol_sma_21 == 0, np.nan, volume / vol_sma_21
        )
        # Relative Vol vs SPY = vol_surge / SPY_vol_surge
        spy_vol_surge = np.where(
            spy_vol_sma_21 == 0, np.nan, df['spy_volume'] / spy_vol_sma_21
        )
        df['vol_surge_rel_spy_21'] = np.where(
            (spy_vol_surge == 0) | (pd.isna(spy_vol_surge)), np.nan,
            df['vol_surge_21'] / spy_vol_surge
        )
    else:
        df['vol_surge_21'] = np.nan
        df['vol_surge_rel_spy_21'] = np.nan

    # 8. % from N-day Highs
    max_63d  = high.rolling(window=63,  min_periods=1).max()
    max_252d = high.rolling(window=252, min_periods=1).max()

    df['dist_63d_high_pct']  = np.where(max_63d  == 0, np.nan, (close - max_63d)  / max_63d  * 100)
    df['dist_52w_high_pct']  = np.where(max_252d == 0, np.nan, (close - max_252d) / max_252d * 100)

    # 9. Up/Down Volume Ratio (50-day)
    close_change = close.diff()
    up_vol = volume.where(close_change > 0, 0.0).rolling(window=50, min_periods=50).sum()
    down_vol = volume.where(close_change < 0, 0.0).rolling(window=50, min_periods=50).sum()
    df['up_down_vol_ratio_50'] = np.where(
        (down_vol == 0) | down_vol.isna(), np.nan, up_vol / down_vol
    )

    # 12. Trend Template Flag
    # Requires SMA calculations to be complete
    if all(col in df.columns for col in ['sma_50', 'sma_150', 'sma_200']):
        sma200_20d_ago = df['sma_200'].shift(20)

        cond1 = close > df['sma_50']
        cond2 = df['sma_50'] > df['sma_150']
        cond3 = df['sma_150'] > df['sma_200']
        cond4 = df['sma_200'] >= sma200_20d_ago
        cond5 = close >= (max_252d * 0.70)  # within 30% of 52w high

        df['is_trend_template'] = np.where(
            sma200_20d_ago.isna(), 
            None,
            np.where(cond1 & cond2 & cond3 & cond4 & cond5, 1, 0)
        )
    else:
        df['is_trend_template'] = None

    # 13. Volume Accumulation Days (5-day)
    # Count of days in the last 5 days where:
    # 1) Close-to-Close change is positive (close.diff() > 0)
    # 2) Volume is > 1.1x of its 21-day average volume
    vol_sma_21 = volume.rolling(window=21, min_periods=1).mean()
    is_accum = (close.diff() > 0) & (volume > vol_sma_21 * 1.1)
    df['vol_accum_days_5'] = is_accum.astype(float).rolling(window=5, min_periods=1).sum().fillna(0).astype(int)

    return df
