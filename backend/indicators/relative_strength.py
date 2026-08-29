import pandas as pd
import numpy as np
from numba import njit

# We can import calculate_ema_tv from the newly created moving_averages
from .moving_averages import calculate_ema_tv

# ============================================================
# RS ドットの経過日数カウンタ（rs_blue_dot_age / rs_red_dot_age）
# ============================================================
# 仕様: doc/completed/rs_dot_age_plan.md §3.1
#   0=当日点灯 / n=n営業日前に点灯 / RS_DOT_AGE_NONE=未点灯・無効
#
# なぜフラグ(0/1)ではなく経過日数なのか:
#   ブルードットは「ウォッチリスト昇格の資格」であってエントリーシグナルではない。
#   実際の買いは数日〜数週間後のベース上抜けで取る **2段構え**の指標なので、
#   当日フラグでは本来の用法を表現できない（スクリーナーは基準日1行しか見ないため）。
#   経過日数にすると `max_rs_blue_dot_age = 10` という numeric フィルタ1条件で
#   「点灯から10日以内」を表現でき、窓 N が Optuna の探索対象にもなる。
RS_DOT_AGE_MAX = 60        # 上限。これを超えた行は未点灯と同一視する
RS_DOT_AGE_NONE = 999      # 番兵。「未点灯」「十分昔」「無効化された」を表す
RS_DOT_WARMUP_BARS = 252   # rolling(252, min_periods=1) 由来の偽点灯を止めるガード


@njit(cache=True)
def _rs_dot_age_kernel(blue, red, cap, warmup, sentinel):
    """Numba-accelerated RS dot age counter."""
    n = blue.shape[0]
    ba = np.full(n, sentinel, np.int32)
    ra = np.full(n, sentinel, np.int32)
    prev_b, prev_r = sentinel, sentinel
    for i in range(n):
        if i < warmup:
            # 母集団が 252 本に満たない区間はカウントを開始しない
            prev_b, prev_r = sentinel, sentinel
            continue
        # --- ブルー ---
        if blue[i]:
            cur_b = 0
        elif red[i]:
            cur_b = sentinel          # 反対ドット点灯で即無効化
        elif prev_b < sentinel:
            cur_b = prev_b + 1
            if cur_b > cap:
                cur_b = sentinel      # 上限超過は未点灯と同一視
        else:
            cur_b = sentinel
        # --- レッド ---
        if red[i]:
            cur_r = 0
        elif blue[i]:
            cur_r = sentinel
        elif prev_r < sentinel:
            cur_r = prev_r + 1
            if cur_r > cap:
                cur_r = sentinel
        else:
            cur_r = sentinel
        ba[i], ra[i] = cur_b, cur_r
        prev_b, prev_r = cur_b, cur_r
    return ba, ra


def compute_rs_dot_age(blue, red, cap: int = RS_DOT_AGE_MAX,
                       warmup: int = RS_DOT_WARMUP_BARS,
                       sentinel: int = RS_DOT_AGE_NONE):
    """点灯フラグ列から (rs_blue_dot_age, rs_red_dot_age) を導出する。

    T3（`calc_relative_strength`）と Parquet バックフィル
    （`backend/scripts/backfill_rs_dot_age.py`）の**両方がこの関数を使う**。
    ここを唯一の実装にしておかないと、前方計算と過去データで規則がずれる。

    Args:
        blue / red: 1銘柄分の時系列の点灯フラグ（bool 配列。日付昇順）。

    Returns:
        (np.ndarray[int32], np.ndarray[int32])
    """
    blue_arr = np.ascontiguousarray(np.asarray(blue, dtype=np.bool_))
    red_arr = np.ascontiguousarray(np.asarray(red, dtype=np.bool_))
    if blue_arr.shape[0] != red_arr.shape[0]:
        raise ValueError('blue / red の長さが一致していません')
    if blue_arr.shape[0] == 0:
        return np.zeros(0, np.int32), np.zeros(0, np.int32)
    return _rs_dot_age_kernel(blue_arr, red_arr, int(cap), int(warmup), int(sentinel))


def calc_relative_strength(df: pd.DataFrame, df_spy: pd.DataFrame = None) -> pd.DataFrame:
    """Calculate Relative Strength and RRG (Relative Rotation Graph) factors.
    Requires df_spy to be passed. If not passed, RS features will not be calculated.
    """
    close = df['close']

    if df_spy is None or df_spy.empty:
        # RS が計算できない場合は「未点灯」で埋める。0 は「当日点灯」の意味なので使わない
        df['rs_blue_dot_age'] = RS_DOT_AGE_NONE
        df['rs_red_dot_age'] = RS_DOT_AGE_NONE
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
    # 点灯判定そのものは従来どおり。出力は経過日数カウンタに変換する（§3.1）
    # 1. Blue Dot (Bullish Leading): RS が 252日新高値・株価はまだ新高値でない
    rs_252_high = rs.rolling(window=252, min_periods=1).max()
    close_252_high = close.rolling(window=252, min_periods=1).max()
    blue_lit = (
        ~(rs.isna() | rs_252_high.isna())
        & (rs >= rs_252_high) & (close < close_252_high)
    ).to_numpy()

    # 2. Red Dot (Bearish Leading): RS が 252日新安値・株価はまだ新安値でない
    rs_252_low = rs.rolling(window=252, min_periods=1).min()
    close_252_low = close.rolling(window=252, min_periods=1).min()
    red_lit = (
        ~(rs.isna() | rs_252_low.isna())
        & (rs <= rs_252_low) & (close > close_252_low)
    ).to_numpy()

    blue_age, red_age = compute_rs_dot_age(blue_lit, red_lit)
    df['rs_blue_dot_age'] = blue_age
    df['rs_red_dot_age'] = red_age

    # --- RS-MACD(5, 21, 5) ---
    df['rs_macd_line_21'] = df['rs_value_e5'] - df['rs_value_e21']
    df['rs_macd_signal_21'] = calculate_ema_tv(df['rs_macd_line_21'], 5)
    df['rs_macd_hist_21'] = df['rs_macd_line_21'] - df['rs_macd_signal_21']

    return df
