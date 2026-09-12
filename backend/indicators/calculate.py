import pandas as pd
import numpy as np

from .moving_averages import calc_moving_averages
from .volatility import calc_volatility
from .relative_strength import calc_relative_strength
from .volume_and_trends import calc_volume_and_trends
from .structure_pivot import counter_trend_series, structure_pivot_series
from .zone_break import zone_break_series

# Export calculate_market_signals so that update_pipeline can import it from here if needed
# or it can import it directly. We'll expose it here for backward compatibility.
from .market_signals import calculate_market_signals

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
    
    # Returns (Prev Close Base)
    df['change_1d_pct'] = df['close'].pct_change() * 100
    df['change_1w_pct'] = df['close'].pct_change(5) * 100
    df['change_1m_pct'] = df['close'].pct_change(20) * 100
    
    if df.empty:
        return df

    # 1. Moving Averages
    df = calc_moving_averages(df)
    
    # 2. Volatility (Requires MAs)
    df = calc_volatility(df)
    
    # 3. Relative Strength (Requires SPY and MAs for dots)
    df = calc_relative_strength(df, df_spy)
    
    # 4. Volume and Trends (Requires MAs and SPY)
    df = calc_volume_and_trends(df)

    # 5. Structure Pivot (LL-HL). 価格のみから決まるので SPY 非依存。
    #    確定遅延（ピボットは L 本先まで確定しない）は関数側で保たれている。
    sp_pivot, sp_hl = structure_pivot_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )
    df['sp_pivot'] = sp_pivot
    df['sp_hl'] = sp_hl
    #    カウンタートレンド線は構造が無い期間に引かれる（sp_pivot とは排他）
    df['sp_counter'] = counter_trend_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )

    # 6. Direction via Zone Break。価格のみから決まるので SPY 非依存。
    #    確定遅延は無い（当日の確定closeだけでブレイク判定が決まる。フラクタル自体の
    #    確定は1本遅れるが先読みではない。doc/in_progress/zone_break_plan.md §3.1 参照）
    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )
    df['is_zone_break_bull'] = is_bull
    df['zb_ssl'] = zb_ssl
    df['zb_bsl'] = zb_bsl
    df['is_zone_break_weak'] = is_weak

    # Sanitize: replace np.nan with None for SQLAlchemy
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
