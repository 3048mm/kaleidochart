import pandas as pd
import numpy as np
import ta

def calculate_ema_tv(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average matching TradingView (Pine Script 'ta.ema').
    Starts with SMA(period) as the initial value at index period-1.
    """
    if len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)
    
    alpha = 2 / (period + 1)
    ema_values = np.full(len(series), np.nan)
    
    # Simple Moving Average for the first 'period' entries
    initial_sma = series.iloc[:period].mean()
    ema_values[period-1] = initial_sma
    
    # Recursive calculation from index 'period' onwards
    curr_ema = initial_sma
    for i in range(period, len(series)):
        curr_ema = series.iloc[i] * alpha + curr_ema * (1 - alpha)
        ema_values[i] = curr_ema
        
    return pd.Series(ema_values, index=series.index)

def calculate_indicators(df_daily: pd.DataFrame, df_spy: pd.DataFrame = None) -> pd.DataFrame:
    """
    日足データ(T2)を受け取り、テクニカル指標とSPYとの相対評価を算出して返す純粋な関数。
    
    Args:
        df_daily: 対象銘柄の日足DataFrame (date, open, high, low, close, volume)
        df_spy:   SPYの日足DataFrame (date, close, volume) — RS系・Relative Volume計算に使用
    Returns:
        各種インジケーターカラムが追加されたDataFrame
    """
    df = df_daily.copy()
    df = df.sort_values('date').reset_index(drop=True)
    
    if df.empty:
        return df
        
    close  = df['close']
    high   = df['high']
    low    = df['low']
    volume = df['volume'].astype(float)

    # =========================================================
    # 1. Simple Moving Averages (SMA)
    # =========================================================
    for period in [5, 21, 50, 63, 150, 200]:
        df[f'sma_{period}'] = close.rolling(window=period, min_periods=1).mean()
        
    # =========================================================
    # 2. Exponential Moving Averages (EMA) — Match TradingView (SMA Seeding)
    # =========================================================
    for period in [5, 21, 50, 63, 150, 200]:
        df[f'ema_{period}'] = calculate_ema_tv(close, period)
        
    # =========================================================
    # 3. ATR (14日) — raw & %
    # =========================================================
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

    # =========================================================
    # 4. ADR% (21日) — Average Daily Range as % of Low
    # =========================================================
    daily_range_pct = np.where(low == 0, np.nan, (high - low) / low * 100)
    df['adr_pct_21'] = pd.Series(daily_range_pct).rolling(window=21, min_periods=1).mean().values

    # =========================================================
    # 5. Distance from SMA50 in ATR multiples (Formula updated per user request)
    # =========================================================
    df['dist_sma50_atr'] = np.where(
        df['atr_pct_14'].isna() | (df['atr_pct_14'] == 0) | df['sma_50'].isna() | (df['sma_50'] == 0),
        np.nan,
        ((close / df['sma_50'] * 100) - 100) / df['atr_pct_14']
    )

    # =========================================================
    # 6. TD Sequential (TD9)
    # =========================================================
    td9_series = np.zeros(len(close))
    for i in range(len(close)):
        if i >= 4:
            if close.iloc[i] > close.iloc[i-4]:
                prev = td9_series[i-1]
                # Reset after completing a 9-count, or start fresh if previous was bearish/zero
                count = prev if (0 < prev < 9) else 0
                td9_series[i] = count + 1
            elif close.iloc[i] < close.iloc[i-4]:
                prev = td9_series[i-1]
                count = prev if (-9 < prev < 0) else 0
                td9_series[i] = count - 1
    df['td9'] = td9_series

    # =========================================================
    # 7. Relative Strength vs SPY  +  RS Momentum  +  RS Ratio(Z-score)
    # =========================================================
    if df_spy is not None and not df_spy.empty:
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
            df['close'] / df['spy_close']
        )
        rs = df['relative_strength_spy']

        # RS Condition (n) = RS / SMA(RS, n)
        for n in [14, 21, 63]:
            rs_sma = rs.rolling(window=n, min_periods=max(1, n//2)).mean()
            df[f'rs_condition_{n}'] = np.where(
                rs_sma.isna() | (rs_sma == 0), np.nan, rs / rs_sma
            )

        # RS Ratio (Z-score of RS over n days)
        for n in [14, 21, 63]:
            rs_mean = rs.rolling(window=n, min_periods=max(1, n//2)).mean()
            rs_std  = rs.rolling(window=n, min_periods=max(1, n//2)).std()
            df[f'rs_ratio_{n}'] = np.where(
                rs_std.isna() | (rs_std == 0), np.nan, (rs - rs_mean) / rs_std
            )
            
        # RRG RS Momentum (Z-score of RS-Ratio over n days)
        # In standard RRG, momentum is the rate of change of Ratio.
        # Since our Ratio is essentially a normalized relative strength,
        # we apply a rolling normalized difference (Z-score approximation) to the Ratio itself.
        for n in [14, 21, 63]:
            ratio_col = df[f'rs_ratio_{n}']
            ratio_mean = ratio_col.rolling(window=n, min_periods=max(1, n//2)).mean()
            ratio_std  = ratio_col.rolling(window=n, min_periods=max(1, n//2)).std()
            df[f'rs_momentum_{n}'] = np.where(
                ratio_std.isna() | (ratio_std == 0), np.nan, (ratio_col - ratio_mean) / ratio_std
            )

        # --- Relative Volume vs SPY ---
        vol_sma_21     = volume.rolling(window=21, min_periods=1).mean()
        spy_vol_sma_21 = df['spy_volume'].rolling(window=21, min_periods=1).mean()

        df['vol_surge_21'] = np.where(
            vol_sma_21 == 0, np.nan, volume / vol_sma_21
        )
        # Relative Vol vs SPY = vol_surge / SPY_vol_surge
        spy_vol_surge = np.where(
            spy_vol_sma_21 == 0, np.nan, df['spy_volume'] / spy_vol_sma_21
        )
        df['rel_vol_vs_spy_21'] = np.where(
            (spy_vol_surge == 0) | (pd.isna(spy_vol_surge)), np.nan,
            df['vol_surge_21'] / spy_vol_surge
        )

        df = df.drop(columns=['spy_close', 'spy_volume'])
    else:
        # If no SPY benchmark is provided (i.e. this IS SPY), 
        # set relative strength to 1.0 and Z-scores (Ratio/Momentum) to 0.0 to avoid NULLs.
        df['relative_strength_spy'] = 1.0
        for n in [14, 21, 63]:
            df[f'rs_condition_{n}'] = 1.0
            df[f'rs_ratio_{n}']     = 0.0
            df[f'rs_momentum_{n}']  = 0.0
        df['rel_vol_vs_spy_21'] = 1.0
        # vol_surge can still be computed without SPY
        vol_sma_21 = volume.rolling(window=21, min_periods=1).mean()
        df['vol_surge_21'] = np.where(vol_sma_21 == 0, np.nan, volume / vol_sma_21)

    # Volume surge without SPY (covers VIX/DXY for vol_surge_21 column)
    if 'vol_surge_21' not in df.columns:
        vol_sma_21 = volume.rolling(window=21, min_periods=1).mean()
        df['vol_surge_21'] = np.where(vol_sma_21 == 0, np.nan, volume / vol_sma_21)

    # =========================================================
    # 8. % from N-day Highs
    # =========================================================
    max_63d  = high.rolling(window=63,  min_periods=1).max()
    max_252d = high.rolling(window=252, min_periods=1).max()

    df['pct_from_63d_high']  = np.where(max_63d  == 0, np.nan, (close - max_63d)  / max_63d  * 100)
    df['pct_from_52w_high']  = np.where(max_252d == 0, np.nan, (close - max_252d) / max_252d * 100)

    # =========================================================
    # 9. Trend Template フラグ
    # =========================================================
    # Condition 4: SMA200 today >= SMA200 20 days ago (rolling check)
    sma200_20d_ago = df['sma_200'].shift(20)

    cond1 = close > df['sma_50']
    cond2 = df['sma_50'] > df['sma_150']
    cond3 = df['sma_150'] > df['sma_200']
    cond4 = df['sma_200'] >= sma200_20d_ago
    cond5 = close >= (max_252d * 0.70)  # within 30% of 52w high

    df['trend_template_ok'] = np.where(
        sma200_20d_ago.isna(),   # not enough data yet → NULL
        None,
        np.where(cond1 & cond2 & cond3 & cond4 & cond5, 1, 0)
    )

    # =========================================================
    # 10. Sanitize: replace np.nan with None for SQLAlchemy
    # =========================================================
    df = df.replace({np.nan: None})
    
    return df


def calculate_relative_ranks(df_all_indicators: pd.DataFrame, group_col: str, indicator_col: str) -> pd.DataFrame:
    """
    全銘柄の計算済みデータを受け取り、日付ごとのグループ内 Relative Rank (Percentile) を計算。
    対象: rs_ratio_21, rs_ratio_63 など T4 に保存する指標
    """
    if df_all_indicators.empty or indicator_col not in df_all_indicators.columns:
        return pd.DataFrame()
        
    df_result = df_all_indicators.copy()
    df_result['percent_rank'] = df_result.groupby(['date', group_col])[indicator_col].rank(pct=True, ascending=True)
    
    res = df_result[['symbol_id', 'date', group_col, indicator_col, 'percent_rank']].copy()
    res = res.rename(columns={group_col: 'group_name'})
    res['indicator_name'] = indicator_col
    res = res.drop(columns=[indicator_col])
    return res


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
    # Using calculate_ema_tv for EMA21 to be consistent
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
    # Merge VIX and Metrics (Breadth, Momentum)
    if df_vix is not None and not df_vix.empty:
        vix_ref = df_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
        df = pd.merge(df, vix_ref, on='date', how='left')
        df['vix_close'] = df['vix_close'].ffill()
    else:
        df['vix_close'] = 20.0 # Neutral fallback
        
    if df_metrics is not None and not df_metrics.empty:
        df = pd.merge(df, df_metrics, on='date', how='left')
        df['breadth_sma50'] = df['breadth_sma50'].fillna(0.5)
        df['momentum_ratio'] = df['momentum_ratio'].fillna(0.5)
    else:
        df['breadth_sma50'] = 0.5
        df['momentum_ratio'] = 0.5

    # Calculate sub-scores
    breadth_score = df['breadth_sma50'] * 25.0
    momentum_score = df['momentum_ratio'] * 25.0
    
    # VIX Score: 25 if VIX <= 12, 0 if VIX >= 35. Linear interpolation.
    vix_val = df['vix_close'].fillna(20.0)
    vix_score = 25.0 * (35.0 - vix_val) / (35.0 - 12.0)
    vix_score = vix_score.clip(lower=0, upper=25.0)
    
    df['market_trend_score'] = spy_score + breadth_score + momentum_score + vix_score

    return df[['date', 'spy_above_sma200', 'spy_sma200_rising',
                'distribution_days', 'follow_through_day', 'market_phase', 'market_trend_score']]
