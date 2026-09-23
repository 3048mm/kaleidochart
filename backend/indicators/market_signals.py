import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# MTS の入力（^VIX / ^VIX3M）が供給停止したとき、直前値の持ち越し（ffill）が
# 何営業日続いたら警告するか。
#
# ffill 自体は残す。VIX3M は3ヶ月物で動きが緩やかなため、短い空白なら
# 直前値の持ち越しは良い近似である。2026-07-20〜07-29 に Yahoo が VIX3M を
# 8営業日 null で返した際、空白直前 20.54 / 空白明け 20.51 とほぼ動いておらず、
# ffill の MTS 誤差は平均 0.055pt だった（VIX 単独の代替式に切り替えると
# 平均 4.055pt ずれ、73倍悪化する）。
#
# 問題は精度ではなく「黙って・無制限に続くこと」。長期化すれば持ち越しは
# 信頼できなくなるので、閾値を超えたら気づけるようにする。
STALE_INPUT_WARN_DAYS = 10

# --- Tuning Parameters for MTS v3 ---
# 1. VXV/VIX Ratio bounds (Low = panic/0.0, High = overheat/1.0)
VXV_VIX_MIN = 0.90
VXV_VIX_MAX = 1.20

# 2. EMA/ATR deviation bounds
EMA50_ATR_MIN = -4.0
EMA50_ATR_MAX = 8.0

EMA200_ATR_MIN = -4.0
EMA200_ATR_MAX = 16.0

# 3. Market Breadth bounds (Low = oversold/0.0, High = overbought/1.0)
BREADTH_MIN = 0.20
BREADTH_MAX = 0.75

# 4. Component Weights (Must sum to 1.0)
WEIGHT_VXV_VIX    = 0.25
WEIGHT_BREADTH    = 0.25
WEIGHT_EMA50_ATR  = 0.25
WEIGHT_EMA200_ATR = 0.25

# T5（market_signals）を SQLite 基点で計算する際に、gap 日付の SPY 遡りが
# 十分か判定する基準（t5_signals.py の遡り不足ガード参照）。
#
# 根拠: 下の `calculate_market_signals()` で `sma_200` は `close.rolling(200, ...)`、
# `spy_sma200_rising` はその `sma_200` を `shift(20)` して比較するため、
# 対象日までに 200 + 20 = 220 本の SPY が必要。**決め打ちの数値ではなく、
# MTS の SMA200 窓（200）や rising 判定の比較幅（20）を変えたら、この定数も
# 追随して直す必要がある。**
#
# 用途（§7-8(1)・5-7d で変更）: 本数不足は「書き込み対象から外す合図」でも
# 「例外で止める合図」でもない。遡り不足の gap 日付も**行として書き込む**
# （5-9 により spy_above_sma200 / distribution_days / market_phase は None、
# market_trend_score は NaN になるため、偽の値は入らない）。この定数は
# `logger.error` で警告を出すかどうかの閾値としてのみ使う
# （詳細は t5_signals.py の `_find_insufficient_lookback_dates`）。
SPY_LOOKBACK_MIN_BARS = 220



def find_stale_input_gaps(df: pd.DataFrame, col: str) -> list[tuple]:
    """`col` が欠損している連続区間を (開始日, 終了日, 営業日数) で返す。

    ffill で埋められる直前に呼ぶこと。値そのものは変更しない。
    """
    if col not in df.columns or 'date' not in df.columns:
        return []

    missing = df[col].isna().to_numpy()
    dates = df['date'].tolist()
    gaps = []
    start = None
    for i, is_na in enumerate(missing):
        if is_na and start is None:
            start = i
        elif not is_na and start is not None:
            gaps.append((dates[start], dates[i - 1], i - start))
            start = None
    if start is not None:
        gaps.append((dates[start], dates[len(missing) - 1], len(missing) - start))
    return gaps


def report_stale_input_gaps(df: pd.DataFrame, col: str, ticker: str,
                            warn_days: int = STALE_INPUT_WARN_DAYS) -> list[tuple]:
    """MTS 入力の供給停止をログに出す（`STALE_INPUT_WARN_DAYS` 超過で WARNING）。

    ffill は行わない。呼び出し側が ffill する前に「何日ぶん持ち越すことになるか」を
    記録するのが目的。持ち越し自体は短期なら妥当な近似なので、値は変えない。
    """
    gaps = find_stale_input_gaps(df, col)
    for start, end, n in gaps:
        msg = (f"MTS 入力 {ticker} が {start} 〜 {end} の {n} 営業日ぶん欠損しています"
               f"（直前値を持ち越して計算します）")
        if n >= warn_days:
            logger.warning(
                f"{msg}。{warn_days} 営業日以上の持ち越しは信頼できません — "
                f"供給元を確認してください"
            )
        else:
            logger.info(msg)
    return gaps


def compute_breadth_momentum(raw_df: pd.DataFrame) -> pd.DataFrame:
    """個別銘柄の close/sma_50/前日close から、日付別の breadth_sma50/momentum_ratio を作る。

    T5（market_signals）の市場内訳指標。SQLite 経路（`t5_signals.py`）と
    Parquet 経路（`recompute_parquet_signals.py`）の両方から同じ関数を呼ぶこと
    （二重実装にしない）。

    元は `t5_signals.py` にインラインで書かれていたロジックを移したもの
    （2026-09-11 切り出し）。

    NaN の扱い（5-21・§2.3: 判定できなければ NaN。代用値を発明しない）:
    - `breadth_sma50`: その日の全銘柄で `sma_50` が NaN（＝判定できる銘柄が0）なら
      **NaN のまま返す**（0.5 に捏造しない）。捏造すると呼び出し側の
      `has_breadth = breadth_sma50.notna()` が True になり、全銘柄の sma_50 が
      算出できない期間（2017年Q1 など）に偽の中立 breadth を含む4成分 MTS が作られる。
    - `momentum_ratio`: `is_up` は bool 由来で NaN にならない（全 NaN の分岐は
      防御的な 0.5）。最終的な `fillna(0.5)` はこの列にだけ効かせる。

    Args:
        raw_df: `symbol_id` / `date` / `close` / `sma_50` 列を持つ DataFrame。
            銘柄ごとの `shift(1)`（前日比騰落判定）のために内部でソートする
            （呼び出し側が既にソート済みでも結果は変わらない）。

    Returns:
        `date` / `breadth_sma50` / `momentum_ratio` 列の DataFrame。
        入力が空なら同じ列を持つ空の DataFrame を返す。
    """
    if raw_df.empty:
        return pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])

    raw_df = raw_df.sort_values(['symbol_id', 'date']).copy()
    raw_df['prev_close'] = raw_df.groupby('symbol_id')['close'].shift(1)
    raw_df['is_up'] = raw_df['close'] > raw_df['prev_close']

    # SQLite 経路（pd.read_sql）では sma_50 が全行 NULL のとき列が object 型（None）
    # になり、下の `close > sma_50` が TypeError になる。数値化して NaN に揃える
    # （全銘柄 NaN の日＝判定不能の日を正しく NaN として流すための前提）。
    raw_df['sma_50'] = pd.to_numeric(raw_df['sma_50'], errors='coerce')
    raw_df['is_above_sma50'] = np.where(
        raw_df['sma_50'].isna(), np.nan,
        raw_df['close'] > raw_df['sma_50']
    )

    metrics_df = raw_df.groupby('date').agg(
        breadth_sma50=('is_above_sma50', lambda x: x.mean(skipna=True) if not x.isna().all() else np.nan),
        momentum_ratio=('is_up', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5)
    ).reset_index()
    # fillna(0.5) は momentum_ratio のみ（breadth_sma50 の NaN は判定不能として残す）
    metrics_df['momentum_ratio'] = metrics_df['momentum_ratio'].fillna(0.5)
    return metrics_df


def calculate_market_signals(
    df_spy: pd.DataFrame,
    df_vix: pd.DataFrame = None,
    df_vxv: pd.DataFrame = None,
    df_metrics: pd.DataFrame = None
) -> pd.DataFrame:
    """
    Calculates market phase signals and numerical score (0-100) using MTS v3.
    - Uses VXV/VIX ratio (0.90 to 1.25) [25%]
    - Uses Market Breadth (50 SMA above ratio) [25%]
    - Uses SPY Distance to 50 EMA / ATR (-4.0 to +8.0) [25%]
    - Uses SPY Distance to 200 EMA / ATR (-4.0 to +16.0) [25%]
    - Distribution Days are calculated but excluded from the composite trend score.
    """
    if df_spy is None or df_spy.empty:
        return pd.DataFrame()

    df = df_spy.copy().sort_values('date').reset_index(drop=True)
    close  = df['close']
    volume = df['volume'].astype(float)

    # 1. SPY Trend Components (required for phase classification)
    # min_periods を窓幅と一致させる（① 計画 §4-4）。遡りが足りない先頭区間は
    # 「それらしい値」を返さず NaN にする。Parquet 全期間計算時の先頭
    # （SPY は 2010-04-01 開始のため、2010-04〜2011-02 の先頭219本が該当）で
    # NaN になるのはもちろん、SQLite 経路（t5_signals.py）でも同じ窓を使うため、
    # 遡りが足りない gap 日付（§7-8(1)・5-7d で除外をやめ、行として書き込む
    # 方針に変更済み）はここで同じように NaN になる。
    df['sma_50'] = close.rolling(50, min_periods=50).mean()
    df['sma_200'] = close.rolling(200, min_periods=200).mean()

    sma200_20d_ago = df['sma_200'].shift(20)

    # sma_200 の遡りが足りない行は None（判定不能。spy_sma200_rising と同じ慣習）。
    # NaN を 0（200日線割れ）に潰すと、遡り不足が「200日線割れ」として
    # 誤って扱われてしまう。
    df['spy_above_sma200']  = np.where(
        df['sma_200'].isna(), None,
        (close > df['sma_200']).astype(int)
    )
    df['spy_sma200_rising'] = np.where(
        sma200_20d_ago.isna(), None,
        (df['sma_200'] >= sma200_20d_ago).astype(int)
    )

    # 2. Distribution Days: SPY drop >= 0.2% on higher volume
    daily_ret    = close.pct_change()
    vol_increase = volume > volume.shift(1)
    is_dist_day  = (daily_ret <= -0.002) & vol_increase
    df['is_distribution_day'] = is_dist_day.astype(int)
    dist_days_raw = is_dist_day.rolling(window=25, min_periods=25).sum()
    # NaN のまま .astype(int) すると IntCastingNaNError になるため、
    # 先に fillna(0) してから cast し、NaN だった行だけ None に戻す
    # （spy_above_sma200 と同じ「None = 判定不能」の慣習）。
    df['distribution_days'] = np.where(
        dist_days_raw.isna(), None,
        dist_days_raw.fillna(0).astype(int)
    )

    # 3. Follow Through Day (FTD)
    is_ftd = (daily_ret >= 0.017) & vol_increase
    df['follow_through_day'] = is_ftd.astype(int)

    # 4. Market Phase classification
    def phase(row):
        # 遡り不足で spy_above_sma200 / distribution_days が None（判定不能）の行は
        # 既存の分岐（1 か 0 かの2値）に押し込めない。None は None==1 でも
        # None==0 でも False になるため、そのままでは else 節の 'BULL' に落ちて
        # 誤って強気相場と判定されてしまう。ここで明示的に判定不能を返す。
        if row['spy_above_sma200'] is None or row['distribution_days'] is None:
            return None
        if row['spy_above_sma200'] == 1 and row['distribution_days'] <= 3:
            return 'BULL'
        elif row['spy_above_sma200'] == 1 and row['distribution_days'] >= 5:
            return 'CORRECTION'
        elif row['spy_above_sma200'] == 0 and row['follow_through_day'] == 1:
            # FTD で確定するので spy_sma200_rising の値は見ない（None でも判定不能にしない）。
            return 'RALLY_ATTEMPT'
        elif row['spy_above_sma200'] == 0:
            spy_sma200_rising = row.get('spy_sma200_rising')
            # spy_sma200_rising は sma_200.shift(20) 由来のため、sma_200 が算出され
            # 始めた直後の20本（200〜219本目）で None になる（5-9d）。この分岐でしか
            # spy_sma200_rising を使わないため、None のときは判定不能を返す
            # （BEAR / RALLY_ATTEMPT のどちらかを None==0 の偽判定で捏造しない）。
            if spy_sma200_rising is None:
                return None
            if spy_sma200_rising == 0:
                return 'BEAR'
            return 'RALLY_ATTEMPT'
        else:
            return 'BULL'

    df['market_phase'] = df.apply(phase, axis=1)

    # 5. Integrate VXV/VIX Ratio
    if df_vix is not None and not df_vix.empty:
        vix_ref = df_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
        df = pd.merge(df, vix_ref, on='date', how='left')
        report_stale_input_gaps(df, 'vix_close', '^VIX')
        df['vix_close'] = df['vix_close'].ffill()
    else:
        # VIX が無いのに 20.0 の固定値を入れると vxv_vix_ratio が「VXV / 20」という
        # 捏造比率になる（5-26・R22。VXV 側の推定式撤去 5-8b② と同じ扱いに揃える）。
        # NaN にして vxv_vix_ratio を NULL にする。
        logger.warning(
            "^VIX データが供給されていません。vix_close を NaN にし、vxv_vix_ratio を NULL にします"
            "（固定値 20.0 による『VXV / 20』という捏造比率は撤去済み）"
        )
        df['vix_close'] = np.nan

    df['vxv_vix_ratio'] = None
    if df_vxv is not None and not df_vxv.empty:
        vxv_ref = df_vxv[['date', 'close']].rename(columns={'close': 'vxv_close'})
        df = pd.merge(df, vxv_ref, on='date', how='left')
        report_stale_input_gaps(df, 'vxv_close', '^VIX3M')
        df['vxv_close'] = df['vxv_close'].ffill()
        df['vxv_vix_ratio'] = df['vxv_close'] / df['vix_close']
    else:
        logger.warning(
            "^VIX3M(VXV) データが供給されていません。vxv_vix_ratio を NULL にします"
            "（推定式によるフォールバックは撤去済み — 実データ値域[0.744,1.408]に対し"
            "推定式は[0.85,1.30]にクリップしており、パニック局面を隠す危険があったため）"
        )
        df['vxv_vix_ratio'] = np.nan

    # 6. Integrate Breadth and Momentum
    if df_metrics is not None and not df_metrics.empty:
        df = pd.merge(df, df_metrics, on='date', how='left')
    else:
        df['breadth_sma50'] = np.nan
        df['momentum_ratio'] = np.nan

    # has_breadth: breadth_sma50 が実際に算出できているか（NaN=判定不能でないか）で
    # 判定する。従来は date >= '2018-04-01' という日付ハードコードだったが、
    # NULL判定に置換した（§3.7 ④・5-8b）。この判定が成立するには、上流の
    # compute_breadth_momentum が全銘柄判定不能の日に 0.5 ではなく NaN を返す必要が
    # ある（5-21で `else 0.5` / breadth への fillna(0.5) を撤去済み）。
    # df_metrics 側に日付が無い日も merge 後 NaN になり、同様に3成分スコアになる。
    has_breadth = df['breadth_sma50'].notna()

    # 7. Calculate individual component scores (0.0 to 1.0)
    
    # Component A: VXV/VIX Score
    vxv_vix_score = (df['vxv_vix_ratio'] - VXV_VIX_MIN) / (VXV_VIX_MAX - VXV_VIX_MIN)
    vxv_vix_score = vxv_vix_score.clip(0.0, 1.0)
    
    # Component B: Market Breadth Score
    breadth_score = (df['breadth_sma50'] - BREADTH_MIN) / (BREADTH_MAX - BREADTH_MIN)
    breadth_score = breadth_score.clip(0.0, 1.0)
    
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
        df['atr_14'] = tr.rolling(14, min_periods=14).mean()
    else:
        # high/low が無ければ ATR は計算できない（判定不能）。1.0ドルという
        # 捏造値ではなく NaN にする（§3.7）。
        df['atr_14'] = np.nan

    # Calculate ATR% (14-day) to match the standard indicator formula
    df['atr_pct_14'] = np.where(close == 0, np.nan, (df['atr_14'] / close) * 100)

    # Component C1: SPY 50SMA / ATR Distance Score (-4.0 to +8.0)
    # Using SMA50 to match the standard volatility.py sma50_atr_mult formula
    dist_50sma = np.where(
        df['atr_pct_14'].isna() | (df['atr_pct_14'] == 0), np.nan,
        ((close / df['sma_50'] * 100) - 100) / df['atr_pct_14']
    )
    score_50sma_atr = (dist_50sma - EMA50_ATR_MIN) / (EMA50_ATR_MAX - EMA50_ATR_MIN)
    score_50sma_atr = np.clip(score_50sma_atr, 0.0, 1.0)

    # Component C2: SPY 200SMA / ATR Distance Score (-4.0 to +16.0)
    # Using SMA200 to match the standard volatility.py sma200_atr_mult formula logic
    dist_200sma = np.where(
        df['atr_pct_14'].isna() | (df['atr_pct_14'] == 0), np.nan,
        ((close / df['sma_200'] * 100) - 100) / df['atr_pct_14']
    )
    score_200sma_atr = (dist_200sma - EMA200_ATR_MIN) / (EMA200_ATR_MAX - EMA200_ATR_MIN)
    score_200sma_atr = np.clip(score_200sma_atr, 0.0, 1.0)
    
    # 8. Weight and aggregate score (0 to 100)
    score_4comp = (
        vxv_vix_score * 0.25 +
        breadth_score * 0.25 +
        score_50sma_atr * 0.25 +
        score_200sma_atr * 0.25
    ) * 100.0
    
    score_3comp = (
        vxv_vix_score * (1.0/3.0) +
        score_50sma_atr * (1.0/3.0) +
        score_200sma_atr * (1.0/3.0)
    ) * 100.0

    raw_score = np.where(has_breadth, score_4comp, score_3comp)
    df['market_trend_score'] = np.clip(raw_score, 0.0, 100.0)

    return df[['date', 'spy_above_sma200', 'spy_sma200_rising',
                'distribution_days', 'is_distribution_day', 'follow_through_day', 'market_phase', 'market_trend_score', 'vxv_vix_ratio']]

