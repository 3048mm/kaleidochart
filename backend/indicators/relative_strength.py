import pandas as pd
import numpy as np
from numba import njit

# We can import calculate_ema_tv from the newly created moving_averages
from .moving_averages import calculate_ema_tv
from .incremental_merge import compute_recursive_series, finalize_incremental_column, prev_self_seed

# ============================================================
# 数値的に頑健な rolling std（rs_ratio_eN / rs_momentum_eN 用）
# ============================================================
# 仕様: doc/completed/rs_rolling_std_precision_plan.md
#
# なぜ pandas 標準の .rolling().std() を使わないのか:
#   pandas の rolling std は全履歴に対して1回のパスで逐次更新（online algorithm）
#   するため、極端に桁の異なる値（累積分割・併合で価格が数十億倍になった過去の
#   区間など）を通過した際の浮動小数点誤差が、何年も後の全く異なる桁の区間の
#   計算まで汚染することがある（rs_std が厳密に 0.0 になる、または大きく
#   ずれた値になる）。棚卸し（2026-09-12）で全3,279銘柄中6銘柄がこの影響を
#   受けていることを確認済み（価格レンジが極端な銘柄に強く相関するが、完全な
#   決定的しきい値ではない）。
#
#   本実装は各ウィンドウを毎回ゼロから独立に計算する（2パス法: 平均→偏差二乗和）
#   ため、過去の履歴の桁からの汚染を受けない。ddof=1 で pandas/numpy の
#   デフォルトと定義を揃える。
@njit(cache=True)
def _rolling_std_independent_kernel(values, window, min_periods):
    n = values.shape[0]
    out = np.full(n, np.nan)
    for i in range(n):
        start = i - window + 1
        if start < 0:
            start = 0
        cnt = 0
        s = 0.0
        for j in range(start, i + 1):
            v = values[j]
            if not np.isnan(v):
                cnt += 1
                s += v
        if cnt < min_periods or cnt < 2:
            continue
        mean = s / cnt
        ssd = 0.0
        for j in range(start, i + 1):
            v = values[j]
            if not np.isnan(v):
                d = v - mean
                ssd += d * d
        out[i] = np.sqrt(ssd / (cnt - 1))
    return out


def rolling_std_independent(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """各ウィンドウを独立に計算する rolling std（ddof=1）。
    pandas の `.rolling(window).std()` の代替。上の解説コメント参照。
    """
    values = np.ascontiguousarray(series.to_numpy(dtype=np.float64))
    out = _rolling_std_independent_kernel(values, int(window), int(min_periods))
    return pd.Series(out, index=series.index)

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
def _rs_dot_age_kernel(blue, red, cap, warmup, sentinel, use_state, seed_prev_b, seed_prev_r):
    """Numba-accelerated RS dot age counter.

    コア更新式（ブルー/レッドの点灯・継続・失効判定）は use_state の
    True/False を問わず共通（単一実装）。「どこから始めるか」
    （i=0 or 最終行のみ）と「シードをどう用意するか」だけが変わる
    （T3 増分化計画 5-4b。`_ema_kernel` と同じパターン）。

    use_state=False（既定の全期間計算）: 従来どおり。`i < warmup` の区間は
        母集団（rolling(252, min_periods=1)）が252本に満たない可能性があるため
        無条件でカウント未開始（sentinel）にする。このガードの意味は変えない。
    use_state=True（増分計算）: 供給された前日行（末尾から2番目。既に実値が
        入っている）を seed_prev_b/seed_prev_r としてそのまま使い、
        最終行だけを1歩計算する。warmup ガードは適用しない（供給された履歴が
        既に十分長いことが増分呼び出しの前提のため）。
    """
    n = blue.shape[0]
    ba = np.full(n, sentinel, np.int32)
    ra = np.full(n, sentinel, np.int32)
    if use_state:
        start_idx = n - 2
        ba[start_idx] = seed_prev_b
        ra[start_idx] = seed_prev_r
        prev_b, prev_r = seed_prev_b, seed_prev_r
        loop_start = start_idx + 1
    else:
        prev_b, prev_r = sentinel, sentinel
        loop_start = 0
    for i in range(loop_start, n):
        if (not use_state) and i < warmup:
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
                       sentinel: int = RS_DOT_AGE_NONE,
                       prev_blue_age: int = None,
                       prev_red_age: int = None):
    """点灯フラグ列から (rs_blue_dot_age, rs_red_dot_age) を導出する。

    T3（`calc_relative_strength`）と Parquet バックフィル
    （`backend/scripts/backfill_rs_dot_age.py`）の**両方がこの関数を使う**。
    ここを唯一の実装にしておかないと、前方計算と過去データで規則がずれる。

    Args:
        blue / red: 1銘柄分の時系列の点灯フラグ（bool 配列。日付昇順）。
        prev_blue_age / prev_red_age: 増分計算用。前日の保存値。
            指定すると warmup ガード（i<warmup は無条件 sentinel）を適用せず、
            この値をシードに最終行だけを1歩計算する（T3 増分化計画 5-4b）。
            どちらも None（既定）なら現行どおり全期間計算。

    Returns:
        (np.ndarray[int32], np.ndarray[int32])
    """
    blue_arr = np.ascontiguousarray(np.asarray(blue, dtype=np.bool_))
    red_arr = np.ascontiguousarray(np.asarray(red, dtype=np.bool_))
    if blue_arr.shape[0] != red_arr.shape[0]:
        raise ValueError('blue / red の長さが一致していません')
    if blue_arr.shape[0] == 0:
        return np.zeros(0, np.int32), np.zeros(0, np.int32)
    use_state = prev_blue_age is not None and prev_red_age is not None
    seed_b = int(prev_blue_age) if use_state else int(sentinel)
    seed_r = int(prev_red_age) if use_state else int(sentinel)
    return _rs_dot_age_kernel(
        blue_arr, red_arr, int(cap), int(warmup), int(sentinel), use_state, seed_b, seed_r
    )


def calc_relative_strength(df: pd.DataFrame, df_spy: pd.DataFrame = None, state: bool = None) -> pd.DataFrame:
    """Calculate Relative Strength and RRG (Relative Rotation Graph) factors.
    Requires df_spy to be passed. If not passed, RS features will not be calculated.

    Args:
        state: 増分計算のフラグ（truthy で増分モード。T3 増分化計画 5-4b）。
            RECURSIVE 型列（`rs_value_eN` / `rs_roc_ema_N` / `rs_macd_signal_21` /
            `rs_blue_dot_age` / `rs_red_dot_age`）は、前日シードを df 自身の
            供給済み履歴（最終行の1つ前の行）から取り出し、最終行だけを1歩
            計算した上で供給済み履歴とマージする。WINDOW 型列（`rs_ratio_eN` /
            `rs_momentum_eN` / `rs_trend_sN` 等）は、入力（マージ済みの
            RECURSIVE 型列、または生価格）が全行にわたって実値になっているため、
            通常どおり計算すれば正しい（`incremental_merge.py` 参照）。
            None/False（既定）なら現行どおり全期間計算。
    """
    incremental = bool(state)
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

    # rs_value_e5 (Smoothing for rs_trend) — RECURSIVE
    # シード取得〜マージは compute_recursive_series に集約（5-15d・
    # code-review指摘3）。増分モードでシードが取得できない場合はNaNになり、
    # 「df全体（K+1本の窓）から再シード」という危険な経路には入らない。
    rs_ema_5 = compute_recursive_series(
        df, 'rs_value_e5', incremental,
        lambda prev: calculate_ema_tv(rs, 5, prev_ema=prev),
    )
    df['rs_value_e5'] = rs_ema_5

    # rs_trend_sN = rs_value_e5 / SMA(rs_value, N) — WINDOW（マージ済みrs_ema_5と生rsのみに依存）
    for n in [5, 14, 21, 63, 200]:
        rs_sma = rs.rolling(window=n, min_periods=max(1, n//2)).mean()
        df[f'rs_trend_s{n}'] = np.where(
            rs_sma.isna() | (rs_sma == 0), np.nan, rs_ema_5 / rs_sma
        )

    # rs_value_eN, rs_ratio_eN, rs_momentum_eN (Refined JdK methodology)
    for n in [5, 14, 21, 63, 200]:
        # 1. rs_value_eN (Smoothing of rs_value) — RECURSIVE
        if n == 5:
            rs_ema = rs_ema_5
        else:
            rs_ema = compute_recursive_series(
                df, f'rs_value_e{n}', incremental,
                lambda prev, n=n: calculate_ema_tv(rs, n, prev_ema=prev),
            )
            df[f'rs_value_e{n}'] = rs_ema

        # 2. rs_ratio_eN (Z-score of rs_value_eN over n days) — WINDOW
        #    rs_ema は上でマージ済み（全行が実値）のため、通常どおり計算すればよい。
        rs_mean = rs_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        rs_std  = rolling_std_independent(rs_ema, n, max(1, n//2))
        df[f'rs_ratio_e{n}'] = np.where(
            rs_std.isna() | (rs_std == 0), np.nan, (rs_ema - rs_mean) / rs_std
        )

        # 3. rs_momentum_eN (ROC of Ratio + EMA Smoothing + Z-score)
        ratio_val = df[f'rs_ratio_e{n}']
        ratio_offset = ratio_val + 100.0
        # Standard daily RRG uses a 14-day ROC period.
        roc = (ratio_offset / ratio_offset.shift(14)) * 100.0

        # Smooth the ROC recursively — RECURSIVE
        roc_ema = compute_recursive_series(
            df, f'rs_roc_ema_{n}', incremental,
            lambda prev, n=n, roc=roc: calculate_ema_tv(roc, n, prev_ema=prev),
        )
        df[f'rs_roc_ema_{n}'] = roc_ema

        # Standardize the smoothed ROC — WINDOW（マージ済みroc_emaに依存）
        roc_mean = roc_ema.rolling(window=n, min_periods=max(1, n//2)).mean()
        roc_std  = rolling_std_independent(roc_ema, n, max(1, n//2))
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

    # RECURSIVE
    prev_blue_age = prev_self_seed(df, 'rs_blue_dot_age', incremental)
    prev_red_age = prev_self_seed(df, 'rs_red_dot_age', incremental)
    # `compute_rs_dot_age` は2つのシードが「両方とも取得できたときだけ」増分経路
    # （use_state=True）に入る（`prev_blue_age is not None and prev_red_age is not None`）。
    # 増分モードで片方だけシードが取得できない場合、そのまま呼ぶと use_state=False の
    # 経路（df全体をwarmupガード付きで歩き直す）に落ちてしまう。df は増分呼び出しでは
    # K+1本の窓でしかないため、「それらしいが誤った経過日数」が計算されてしまう
    # （§1.2/§1.4と同型の危険。5-15d・2回目のcode-review指摘3）。
    # 安全側で番兵（RS_DOT_AGE_NONE=未点灯）にフォールバックする。
    if incremental and (prev_blue_age is None or prev_red_age is None):
        blue_age = np.full(len(df), RS_DOT_AGE_NONE, dtype=np.int32)
        red_age = np.full(len(df), RS_DOT_AGE_NONE, dtype=np.int32)
    else:
        blue_age, red_age = compute_rs_dot_age(
            blue_lit, red_lit,
            prev_blue_age=prev_blue_age,
            prev_red_age=prev_red_age,
        )
    df['rs_blue_dot_age'] = finalize_incremental_column(
        df, 'rs_blue_dot_age', pd.Series(blue_age, index=df.index), incremental
    )
    df['rs_red_dot_age'] = finalize_incremental_column(
        df, 'rs_red_dot_age', pd.Series(red_age, index=df.index), incremental
    )

    # --- RS-MACD(5, 21, 5) ---
    # rs_macd_line_21 — WINDOW（マージ済みのrs_value_e5・rs_value_e21のみに依存）
    df['rs_macd_line_21'] = df['rs_value_e5'] - df['rs_value_e21']
    # rs_macd_signal_21 — RECURSIVE
    macd_signal = compute_recursive_series(
        df, 'rs_macd_signal_21', incremental,
        lambda prev: calculate_ema_tv(df['rs_macd_line_21'], 5, prev_ema=prev),
    )
    df['rs_macd_signal_21'] = macd_signal
    # rs_macd_hist_21 — WINDOW
    df['rs_macd_hist_21'] = df['rs_macd_line_21'] - df['rs_macd_signal_21']

    return df
