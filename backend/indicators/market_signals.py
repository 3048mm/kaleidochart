import pandas as pd
import numpy as np

# --- Tuning Parameters for MTS v3 ---
# 1. VXV/VIX Ratio bounds (Low = panic/0.0, High = overheat/1.0)
VXV_VIX_MIN = 0.90
VXV_VIX_MAX = 1.25

# 2. EMA/ATR deviation bounds
EMA50_ATR_MIN = -4.0
EMA50_ATR_MAX = 8.0

EMA200_ATR_MIN = -4.0
EMA200_ATR_MAX = 16.0

# 3. Distribution Days bounds (<= 5 days = 1.0, >= 10 days = 0.0, step-down in between)
DIST_DAYS_MIN = 5
DIST_DAYS_MAX = 10

# 4. Component Weights (Must sum to 1.0)
WEIGHT_VXV_VIX    = 0.20
WEIGHT_BREADTH    = 0.20
WEIGHT_EMA50_ATR  = 0.20
WEIGHT_EMA200_ATR = 0.20
WEIGHT_DIST_DAYS  = 0.20


def calculate_market_signals(
    df_spy: pd.DataFrame,
    df_vix: pd.DataFrame = None,
    df_vxv: pd.DataFrame = None,
    df_metrics: pd.DataFrame = None
) -> pd.DataFrame:
    """
    Calculates market phase signals and numerical score (0-100) using MTS v3.
    - Uses VXV/VIX ratio (0.90 to 1.25) [20%]
    - Uses Market Breadth (50 SMA above ratio) [20%]
    - Uses SPY Distance to 50 EMA / ATR (-4.0 to +4.0) [20%]
    - Uses SPY Distance to 200 EMA / ATR (-4.0 to +10.0) [20%]
    - Uses Distribution Days (step-down logic for days > 5) [20%]
    """
    if df_spy is None or df_spy.empty:
        return pd.DataFrame()

    df = df_spy.copy().sort_values('date').reset_index(drop=True)
    close  = df['close']
    volume = df['volume'].astype(float)

    # 1. SPY Trend Components (required for phase classification)
    df['sma_50'] = close.rolling(50, min_periods=1).mean()
    df['sma_200'] = close.rolling(200, min_periods=1).mean()
    
    sma200_20d_ago = df['sma_200'].shift(20)

    df['spy_above_sma200']  = (close > df['sma_200']).astype(int)
    df['spy_sma200_rising'] = np.where(
        sma200_20d_ago.isna(), None,
        (df['sma_200'] >= sma200_20d_ago).astype(int)
    )

    # 2. Distribution Days: SPY drop >= 0.2% on higher volume
    daily_ret    = close.pct_change()
    vol_increase = volume > volume.shift(1)
    is_dist_day  = (daily_ret <= -0.002) & vol_increase
    df['is_distribution_day'] = is_dist_day.astype(int)
    df['distribution_days'] = is_dist_day.rolling(window=25, min_periods=1).sum().astype(int)

    # 3. Follow Through Day (FTD)
    is_ftd = (daily_ret >= 0.017) & vol_increase
    df['follow_through_day'] = is_ftd.astype(int)

    # 4. Market Phase classification
    def phase(row):
        if row['spy_above_sma200'] == 1 and row['distribution_days'] <= 3:
            return 'BULL'
        elif row['spy_above_sma200'] == 1 and row['distribution_days'] >= 5:
            return 'CORRECTION'
        elif row['spy_above_sma200'] == 0 and row['follow_through_day'] == 1:
            return 'RALLY_ATTEMPT'
        elif row['spy_above_sma200'] == 0:
            if row.get('spy_sma200_rising') == 0:
                return 'BEAR'
            return 'RALLY_ATTEMPT'
        else:
            return 'BULL'

    df['market_phase'] = df.apply(phase, axis=1)

    # 5. Integrate VXV/VIX Ratio
    if df_vix is not None and not df_vix.empty:
        vix_ref = df_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
        df = pd.merge(df, vix_ref, on='date', how='left')
        df['vix_close'] = df['vix_close'].ffill()
    else:
        df['vix_close'] = 20.0

    df['vxv_vix_ratio'] = None
    if df_vxv is not None and not df_vxv.empty:
        vxv_ref = df_vxv[['date', 'close']].rename(columns={'close': 'vxv_close'})
        df = pd.merge(df, vxv_ref, on='date', how='left')
        df['vxv_close'] = df['vxv_close'].ffill()
        df['vxv_vix_ratio'] = df['vxv_close'] / df['vix_close']
    else:
        # Fallback ratio estimation based on VIX if VXV is missing
        df['vxv_vix_ratio'] = 1.15 - (df['vix_close'] - 12.0) * (0.25 / 23.0)
        df['vxv_vix_ratio'] = df['vxv_vix_ratio'].clip(0.85, 1.30)

    # 6. Integrate Breadth and Momentum
    if df_metrics is not None and not df_metrics.empty:
        df = pd.merge(df, df_metrics, on='date', how='left')
        df['breadth_sma50'] = df['breadth_sma50'].fillna(0.5)
        df['momentum_ratio'] = df['momentum_ratio'].fillna(0.5)
    else:
        df['breadth_sma50'] = 0.5
        df['momentum_ratio'] = 0.5

    # 7. Calculate individual component scores (0.0 to 1.0)
    
    # Component A: VXV/VIX Score (0.90 to 1.25)
    vxv_vix_score = (df['vxv_vix_ratio'] - VXV_VIX_MIN) / (VXV_VIX_MAX - VXV_VIX_MIN)
    vxv_vix_score = vxv_vix_score.clip(0.0, 1.0)
    
    # Component B: Market Breadth Score
    breadth_score = df['breadth_sma50'].clip(0.0, 1.0)
    
    # Calculate ATR 14
    if 'high' in df.columns and 'low' in df.columns:
        high = df['high']
        low = df['low']
        close_prev = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14, min_periods=1).mean().ffill().fillna(1.0)
    else:
        # Fallback if high/low not provided
        df['atr_14'] = 1.0

    # Avoid zero division
    df['atr_14'] = np.where(df['atr_14'] > 0, df['atr_14'], 1.0)

    # Calculate EMAs
    df['ema_50'] = close.ewm(span=50, adjust=False).mean()
    df['ema_200'] = close.ewm(span=200, adjust=False).mean()

    # Component C1: SPY 50EMA / ATR Distance Score (-4.0 to +4.0)
    dist_50ema = (close - df['ema_50']) / df['atr_14']
    score_50ema_atr = (dist_50ema - EMA50_ATR_MIN) / (EMA50_ATR_MAX - EMA50_ATR_MIN)
    score_50ema_atr = score_50ema_atr.clip(0.0, 1.0)

    # Component C2: SPY 200EMA / ATR Distance Score (-4.0 to +10.0)
    dist_200ema = (close - df['ema_200']) / df['atr_14']
    score_200ema_atr = (dist_200ema - EMA200_ATR_MIN) / (EMA200_ATR_MAX - EMA200_ATR_MIN)
    score_200ema_atr = score_200ema_atr.clip(0.0, 1.0)
    
    # Component D: Distribution Days Score (<=5 days = 1.0, >=10 days = 0.0, step-down in between)
    dist_days = df['distribution_days']
    dist_score = np.where(
        dist_days <= DIST_DAYS_MIN,
        1.0,
        np.where(
            dist_days >= DIST_DAYS_MAX,
            0.0,
            1.0 - (dist_days - DIST_DAYS_MIN) / (DIST_DAYS_MAX - DIST_DAYS_MIN)
        )
    )
    
    # 8. Weight and aggregate score (0 to 100)
    raw_score = (
        vxv_vix_score * WEIGHT_VXV_VIX +
        breadth_score * WEIGHT_BREADTH +
        score_50ema_atr * WEIGHT_EMA50_ATR +
        score_200ema_atr * WEIGHT_EMA200_ATR +
        dist_score * WEIGHT_DIST_DAYS
    ) * 100.0
    
    df['market_trend_score'] = raw_score.clip(0.0, 100.0)

    return df[['date', 'spy_above_sma200', 'spy_sma200_rising',
                'distribution_days', 'is_distribution_day', 'follow_through_day', 'market_phase', 'market_trend_score', 'vxv_vix_ratio']]

