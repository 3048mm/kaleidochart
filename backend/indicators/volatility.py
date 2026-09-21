import pandas as pd
import numpy as np
from numba import njit

@njit
def _td9_kernel(close_values, compare_values, seed_value=0.0):
    """Numba-accelerated TD Sequential (TD9) loop。

    seed_value: 増分計算用。前日の td9 値（state）。0.0（既定）なら
        従来どおり「履歴の先頭」を意味する。compare_values（close.shift(4)）が
        NaN の間は比較が成立しないため no-op になり、実質的に seed_value は
        i=0 の1点でしか参照されない（state=None のときは常に 0.0 なので
        従来の `range(4, n)` 版とビット単位で一致する）。
    """
    n = len(close_values)
    res = np.zeros(n)
    for i in range(n):
        prev = seed_value if i == 0 else res[i-1]
        if close_values[i] > compare_values[i]: # close > close[i-4]
            count = prev if (0 < prev < 9) else 0
            res[i] = count + 1
        elif close_values[i] < compare_values[i]: # close < close[i-4]
            count = prev if (-9 < prev < 0) else 0
            res[i] = count - 1
    return res


@njit
def _atr_wilder_kernel(true_range, window, seed_idx, seed_value):
    """Wilder の再帰平滑化による ATR。`ta.volatility.AverageTrueRange`
    （fillna=True）とビット単位で一致するように自前実装したもの
    （T3 増分化計画 5-4。ta ライブラリには前日値を注入する経路が無いため）。

    seed_idx/seed_value: res[seed_idx] = seed_value をそのまま置き（平滑化なし）、
        seed_idx+1 以降を `atr[i] = (atr[i-1]*(window-1) + tr[i]) / window` で
        1歩ずつ進める。
        - state=None（全期間計算）: seed_idx=window-1, seed_value=true_range[0:window]の平均
          （ta 実装と同じ。それより前の行は 0 のまま＝ta の np.zeros 初期化と同じ）
        - state あり（増分計算）: seed_idx=0, seed_value=前日の atr_14
    """
    n = true_range.shape[0]
    atr = np.zeros(n)
    if seed_idx >= n:
        return atr
    atr[seed_idx] = seed_value
    for i in range(seed_idx + 1, n):
        atr[i] = (atr[i-1] * (window - 1) + true_range[i]) / window
    return atr


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """ta.volatility.IndicatorMixin._true_range と同一の定義。"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def calc_volatility(df: pd.DataFrame, state: dict = None) -> pd.DataFrame:
    """Calculate Volatility Indicators (ATR, ADR, VCR, TD9, SMA50 Distance).
    Requires 'sma_50' column to be present for sma50_atr_mult.

    Args:
        state: 増分計算用の前日値辞書（例: {'atr_14': 2.34, 'td9': 3.0}）。
            None（既定）なら現行どおり全期間計算。
    """
    close = df['close']
    high = df['high']
    low = df['low']
    window = 14

    # 3. ATR (14日) — raw & %
    try:
        true_range = _true_range(high, low, close)
        tr_values = true_range.to_numpy(dtype=float)
        prev_atr = state.get('atr_14') if state else None
        if prev_atr is not None:
            # 増分計算: シードを作り直さず、前日の atr_14 から1歩ずつ継続する。
            atr_values = _atr_wilder_kernel(tr_values, window, 0, float(prev_atr))
        else:
            # 全期間計算: window 本に満たない場合は ta の元実装（IndexError→例外捕捉で
            # NaN フォールバック）と同じ挙動にするため、ここで意図的に例外を送出する。
            if len(tr_values) < window:
                raise ValueError('true_range の長さが window 未満です')
            # true_range[0:window] の平均（true_range[0] は high-low のみで求まるため
            # NaN ではない。ta の _run() と同一のシード方法）
            seed_value = float(np.mean(tr_values[:window]))
            atr_values = _atr_wilder_kernel(tr_values, window, window - 1, seed_value)
        atr_values = pd.Series(atr_values, index=df.index)
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
    close_vals = close.values.astype(float)
    compare_vals = close.shift(4).values.astype(float)
    prev_td9 = state.get('td9') if state else None
    seed_td9 = float(prev_td9) if prev_td9 is not None else 0.0
    df['td9'] = _td9_kernel(close_vals, compare_vals, seed_td9)

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
