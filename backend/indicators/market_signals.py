import pandas as pd
import numpy as np
from .moving_averages import calculate_ema_tv

def calculate_market_signals(df_spy: pd.DataFrame, df_vix: pd.DataFrame = None, df_metrics: pd.DataFrame = None) -> pd.DataFrame:
    """
    SPYの日足データ、VIXデータ、および市場全体の統計（Breadth, Momentum Ratio）から
    市場フェーズシグナルと数値スコア (0-100) を計算して返す。
    """
    if df_spy is None or df_spy.empty:
        return pd.DataFrame()

    df = df_spy.copy().sort_values('date').reset_index(drop=True)
    close  = df['close']
    volume = df['volume'].astype(float)

    # 1. SPY Trend Components (25 pts)
    df['sma_50'] = close.rolling(50, min_periods=1).mean()
    df['sma_200'] = close.rolling(200, min_periods=1).mean()
    df['ema_21'] = calculate_ema_tv(close, 21)
    
    sma200_20d_ago = df['sma_200'].shift(20)

    df['spy_above_sma200']  = (close > df['sma_200']).astype(int)
    df['spy_sma200_rising'] = np.where(
        sma200_20d_ago.isna(), None,
        (df['sma_200'] >= sma200_20d_ago).astype(int)
    )
    
    spy_score = (
        (close > df['ema_21']).astype(float) * 8.33 +
        (close > df['sma_50']).astype(float) * 8.33 +
        (close > df['sma_200']).astype(float) * 8.34
    )

    # 2. Distribution Days: SPY下落 -0.2% 以上 かつ 出来高が前日比増
    daily_ret    = close.pct_change()
    vol_increase = volume > volume.shift(1)
    is_dist_day  = (daily_ret <= -0.002) & vol_increase
    df['distribution_days'] = is_dist_day.rolling(window=25, min_periods=1).sum().astype(int)

    # 3. Follow Through Day (FTD)
    is_ftd = (daily_ret >= 0.017) & vol_increase
    df['follow_through_day'] = is_ftd.astype(int)

    # 4. Market Phase 判定
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
    
    # 5. Market Trend Score (0-100) aggregation
    if df_vix is not None and not df_vix.empty:
        vix_ref = df_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
        df = pd.merge(df, vix_ref, on='date', how='left')
        df['vix_close'] = df['vix_close'].ffill()
    else:
        df['vix_close'] = 20.0 
        
    if df_metrics is not None and not df_metrics.empty:
        df = pd.merge(df, df_metrics, on='date', how='left')
        df['breadth_sma50'] = df['breadth_sma50'].fillna(0.5)
        df['momentum_ratio'] = df['momentum_ratio'].fillna(0.5)
    else:
        df['breadth_sma50'] = 0.5
        df['momentum_ratio'] = 0.5

    breadth_score = df['breadth_sma50'] * 25.0
    momentum_score = df['momentum_ratio'] * 25.0
    
    vix_val = df['vix_close'].fillna(20.0)
    vix_score = 25.0 * (35.0 - vix_val) / (35.0 - 12.0)
    vix_score = vix_score.clip(lower=0, upper=25.0)
    
    df['market_trend_score'] = spy_score + breadth_score + momentum_score + vix_score

    return df[['date', 'spy_above_sma200', 'spy_sma200_rising',
                'distribution_days', 'follow_through_day', 'market_phase', 'market_trend_score']]
