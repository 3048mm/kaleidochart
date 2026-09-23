import pandas as pd
import numpy as np
from numba import njit

from .incremental_merge import compute_recursive_series

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
            作り直さず、series の**末尾から2番目の行**（供給された前日値の
            位置。増分呼び出しの契約では行0..K-1が供給済み・最終行Kが
            新規計算対象）にこの値をそのまま置き、`_ema_kernel` で
            **最終行だけ**を1歩計算する（T3 増分化計画 5-4b）。
            供給された履歴列（行0..K-1）は既に実値なので、それより前を
            歩き直す必要はない（5-4版は先頭行(index 0)をシードに全行を
            歩き直していたが、WINDOW型列の rolling 窓がその範囲でしか
            育たず不正確になる問題があった。詳細: incremental_merge.py）。
            None（既定）なら現行どおり全期間計算。
    """
    if prev_ema is not None:
        # シードを作り直さない経路。_ema_kernel は既にステップ実行のループなので、
        # 開始位置(len-2)と開始値(prev_ema)を差し替えるだけで最終行だけの
        # 1歩計算になる。
        alpha = 2 / (period + 1)
        seed_idx = len(series) - 2
        ema_vals = _ema_kernel(series.values.astype(float), alpha, float(prev_ema), seed_idx)
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

def calc_moving_averages(df: pd.DataFrame, state: bool = None) -> pd.DataFrame:
    """Calculate Simple and Exponential Moving Averages.

    Args:
        state: 増分計算のフラグ（truthy で増分モード。T3 増分化計画 5-4b）。
            `sma_*` は WINDOW 型（生の close のみに依存）なので通常どおり
            計算すれば正しい（`incremental_merge.py` 参照）。`ema_*` は
            RECURSIVE 型なので、前日シードを df 自身の供給済み履歴
            （最終行の1つ前の行）から取り出し、最終行だけを1歩計算した上で
            供給済み履歴とマージする。
            None/False（既定）なら現行どおり全期間計算。
    """
    incremental = bool(state)
    close = df['close']
    for period in [5, 21, 50, 63, 150, 200]:
        df[f'sma_{period}'] = close.rolling(window=period, min_periods=period).mean()
        # シード取得〜マージは compute_recursive_series に集約（5-15d・
        # code-review指摘3）。増分モードでシードが取得できない場合はNaNになり、
        # 「df全体（K+1本の窓）から再シード」という危険な経路には入らない。
        df[f'ema_{period}'] = compute_recursive_series(
            df, f'ema_{period}', incremental,
            lambda prev_ema, period=period: calculate_ema_tv(close, period, prev_ema=prev_ema),
        )
    return df
