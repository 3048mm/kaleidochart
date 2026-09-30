"""
screener_filters.py — Shared Screener Filter Functions

pandas DataFrame ベースの特殊フィルタ関数を純粋関数として提供する。
backtest_screener.py および scenario_scorer.py の両方から呼び出し可能。

各関数は pd.Series (bool mask) を返し、呼び出し元で mask &= ... として適用する。
"""
import pandas as pd
import numpy as np
from typing import Optional

try:
    from indicators.screener_registry import EXPLICIT_SPECS
except ModuleNotFoundError:
    from backend.indicators.screener_registry import EXPLICIT_SPECS


def filter_rs_rank_21_gt_63(merged: pd.DataFrame) -> pd.Series:
    """
    RS Rank 21 > RS Rank 63 の銘柄のみを通過させるフィルタ。

    前提: merged に 'rs21_rank' と 'rs63_rank' カラムが存在すること。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'rs21_rank' not in merged.columns or 'rs63_rank' not in merged.columns:
        return pd.Series(True, index=merged.index)
    return merged['rs21_rank'] > merged['rs63_rank']


def filter_theme_rs21_gt_63(
    merged: pd.DataFrame,
    df_ind_day: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """
    テーマの RS Ratio 21 > RS Ratio 63 に属する銘柄を通過させるフィルタ。

    - テーマ自体: rs_ratio_21 > rs_ratio_63 のテーマを通過
    - 個別銘柄: 上記テーマの構成銘柄を通過

    Returns:
        pd.Series[bool]: True = 通過
    """
    # テーマの中で RS21 > RS63 のものを抽出
    theme_ind = df_ind_day.merge(
        df_symbols[df_symbols['category'] == 'テーマ'][['id']],
        left_on='symbol_id', right_on='id', how='inner'
    )
    leading_themes = theme_ind[
        theme_ind['rs_ratio_e21'] > theme_ind['rs_ratio_e63']
    ]['symbol_id'].values

    # Leading テーマに属する個別銘柄を抽出
    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(leading_themes)
    ]['symbol_id'].values

    # テーマ自体 or テーマ構成銘柄 → True
    mask = (
        ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )
    return mask


def filter_rrg_leading_in(
    merged: pd.DataFrame,
    intensity_threshold: float = 0.0,
) -> pd.Series:
    """
    RRG Leading 象限に転換した銘柄を通過させるフィルタ。

    条件:
    - 当日: rs_ratio_21 > 0 AND rs_momentum_21 > 0 (Leading 象限)
    - 当日のモメンタムが前日より上昇 (加速)
    - 当日の intensity >= threshold
    - 前日: Leading 象限ではなかった OR intensity < threshold

    前提: merged に 'prev_rs_ratio_21', 'prev_rs_momentum_21' カラムが存在すること。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'prev_rs_ratio_e21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    # Intensity 計算
    intensity = np.sqrt(merged['rs_ratio_e21']**2 + merged['rs_momentum_e21']**2)
    prev_intensity = np.sqrt(merged['prev_rs_ratio_e21']**2 + merged['prev_rs_momentum_e21']**2)

    mask = (
        (merged['rs_ratio_e21'] > 0) & (merged['rs_momentum_e21'] > 0) &       # Leading 象限
        (merged['rs_momentum_e21'] > merged['prev_rs_momentum_e21']) &           # 加速
        (intensity >= intensity_threshold) &                                     # Intensity (当日)
        (
            ((merged['prev_rs_ratio_e21'] <= 0) | (merged['prev_rs_momentum_e21'] <= 0)) |  # 前日非Leading
            (prev_intensity < intensity_threshold)                                          # 前日低Intensity
        )
    )
    return mask


def filter_rrg_improving_in(
    merged: pd.DataFrame,
    intensity_threshold: float = 0.0,
) -> pd.Series:
    """
    RRG Improving 象限に転換した銘柄を通過させるフィルタ。

    条件:
    - 当日: rs_ratio_21 < 0 AND rs_momentum_21 > 0 (Improving 象限)
    - 当日のモメンタムが前日より上昇 (加速)
    - 当日の intensity >= threshold
    - 前日: Lagging 象限だった (ratio<0 & mom<=0) OR 低 intensity で左半面

    前提: merged に 'prev_rs_ratio_21', 'prev_rs_momentum_21' カラムが存在すること。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'prev_rs_ratio_e21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    intensity = np.sqrt(merged['rs_ratio_e21']**2 + merged['rs_momentum_e21']**2)
    prev_intensity = np.sqrt(merged['prev_rs_ratio_e21']**2 + merged['prev_rs_momentum_e21']**2)

    mask = (
        (merged['rs_ratio_e21'] < 0) & (merged['rs_momentum_e21'] > 0) &       # Improving 象限
        (merged['rs_momentum_e21'] > merged['prev_rs_momentum_e21']) &           # 加速
        (intensity >= intensity_threshold) &                                     # Intensity (当日)
        (
            ((merged['prev_rs_ratio_e21'] < 0) & (merged['prev_rs_momentum_e21'] <= 0)) |  # 前日 Lagging
            ((prev_intensity < intensity_threshold) & (merged['prev_rs_ratio_e21'] < 0))   # 前日低Intensity+左半面
        )
    )
    return mask


def filter_rrg_lagging_in(merged: pd.DataFrame) -> pd.Series:
    """
    RRG Lagging 象限に転換した銘柄を通過させるフィルタ。

    条件:
    - 当日: rs_ratio_21 < 0 AND rs_momentum_21 < 0 (Lagging 象限)
    - 前日: 非 Lagging (ratio>=0 OR momentum>=0)

    前提: merged に 'prev_rs_ratio_21', 'prev_rs_momentum_21' カラムが存在すること。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'prev_rs_ratio_e21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    mask = (
        (merged['rs_ratio_e21'] < 0) & (merged['rs_momentum_e21'] < 0) &       # Lagging 象限
        ((merged['prev_rs_ratio_e21'] >= 0) | (merged['prev_rs_momentum_e21'] >= 0))  # 前日非Lagging
    )
    return mask


def filter_theme_rs_rank_21_gt_63(
    merged: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """
    テーマの RS%rank 21 > RS%rank 63 に属する銘柄を通過させるフィルタ。

    前提: merged に 'rs21_rank' と 'rs63_rank' が存在すること。

    - テーマ自体: rs21_rank > rs63_rank のテーマを通過
    - 個別銘柄: 上記テーマの構成銘柄を通過

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'rs21_rank' not in merged.columns or 'rs63_rank' not in merged.columns:
        return pd.Series(True, index=merged.index)

    # merged からテーマシンボルを抽出し、その中で rs21_rank > rs63_rank のものを特定
    theme_rows = merged[merged['category'] == 'テーマ']
    leading_themes = theme_rows[
        theme_rows['rs21_rank'] > theme_rows['rs63_rank']
    ]['symbol_id'].values

    # Leading テーマに属する個別銘柄を抽出
    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(leading_themes)
    ]['symbol_id'].values

    # テーマ自体 or テーマ構成銘柄 → True
    mask = (
        ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )
    return mask


def filter_theme_rs14_gt_21(
    merged: pd.DataFrame,
    df_ind_day: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """
    テーマの RS Ratio 14 > RS Ratio 21 に属する銘柄を通過させるフィルタ。

    - テーマ自体: rs_ratio_14 > rs_ratio_21 のテーマを通過
    - 個別銘柄: 上記テーマの構成銘柄を通過

    Returns:
        pd.Series[bool]: True = 通過
    """
    # テーマの中で RS14 > RS21 のものを抽出
    theme_ind = df_ind_day.merge(
        df_symbols[df_symbols['category'] == 'テーマ'][['id']],
        left_on='symbol_id', right_on='id', how='inner'
    )
    leading_themes = theme_ind[
        theme_ind['rs_ratio_e14'] > theme_ind['rs_ratio_e21']
    ]['symbol_id'].values

    # Leading テーマに属する個別銘柄を抽出
    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(leading_themes)
    ]['symbol_id'].values

    # テーマ自体 or テーマ構成銘柄 → True
    mask = (
        ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )
    return mask


def filter_theme_rs_rank_14_gt_21(
    merged: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """
    テーマの RS%rank 14 > RS%rank 21 に属する銘柄を通過させるフィルタ。

    前提: merged に 'rs14_rank' と 'rs21_rank' が存在すること。

    - テーマ自体: rs14_rank > rs21_rank のテーマを通過
    - 個別銘柄: 上記テーマの構成銘柄を通過

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'rs14_rank' not in merged.columns or 'rs21_rank' not in merged.columns:
        return pd.Series(True, index=merged.index)

    # merged からテーマシンボルを抽出し、その中で rs14_rank > rs21_rank のものを特定
    theme_rows = merged[merged['category'] == 'テーマ']
    leading_themes = theme_rows[
        theme_rows['rs14_rank'] > theme_rows['rs21_rank']
    ]['symbol_id'].values

    # Leading テーマに属する個別銘柄を抽出
    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(leading_themes)
    ]['symbol_id'].values

    # テーマ自体 or テーマ構成銘柄 → True
    mask = (
        ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )
    return mask


def filter_rs_rank_14_gt_21(merged: pd.DataFrame) -> pd.Series:
    """
    個別銘柄の RS%rank 14 > RS%rank 21 の銘柄のみを通過させるフィルタ。

    前提: merged に 'rs14_rank' と 'rs21_rank' カラムが存在すること。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'rs14_rank' not in merged.columns or 'rs21_rank' not in merged.columns:
        return pd.Series(True, index=merged.index)
    return merged['rs14_rank'] > merged['rs21_rank']


def _rs_macd_hist_rising_mask(merged: pd.DataFrame) -> pd.Series:
    """RS-MACD ヒストグラムが前日より上昇している行（銘柄・テーマ共通の判定式）。

    - 2026-09-30: 旧仕様の「hist > 0」を外し、上昇のみの判定に統一した。水準（正負）は
      min_rs_macd_hist_21 / min_theme_rs_macd_hist_21 で別に指定する（条件の重複を避けるため）。
    - 'prev_rs_macd_hist_21' が無い／NaN の行は上昇を確認できないため False（不通過）。
    - 'rs_macd_hist_21' 自体が無い場合は全通過。
    """
    if 'rs_macd_hist_21' not in merged.columns:
        return pd.Series(True, index=merged.index)
    if 'prev_rs_macd_hist_21' not in merged.columns:
        return pd.Series(False, index=merged.index)
    return merged['rs_macd_hist_21'] > merged['prev_rs_macd_hist_21']


def filter_rs_macd_hist_rising_21(merged: pd.DataFrame) -> pd.Series:
    """RS-MACD ヒストグラムが前日より上昇（加速）している銘柄を通過させるフィルタ。

    判定式は _rs_macd_hist_rising_mask を参照。

    Returns:
        pd.Series[bool]: True = 通過
    """
    return _rs_macd_hist_rising_mask(merged)


def filter_theme_rs_macd_hist_rising_21(
    merged: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """テーマの RS-MACD ヒストグラムが前日より上昇しているテーマと、その構成銘柄を通過させるフィルタ。

    判定式は銘柄版と同一（_rs_macd_hist_rising_mask をテーマ行に適用）。
    """
    if 'rs_macd_hist_21' not in merged.columns:
        return pd.Series(True, index=merged.index)
    is_theme = merged['category'] == 'テーマ'
    rising = _rs_macd_hist_rising_mask(merged)
    rising_themes = merged.loc[is_theme & rising, 'symbol_id'].values
    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(rising_themes)
    ]['symbol_id'].values
    return (
        (is_theme & merged['symbol_id'].isin(rising_themes)) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )


def filter_rs_trend_s21_lt_s63(merged: pd.DataFrame) -> pd.Series:
    """RS Trend の生値で s21 < s63（中期の相対トレンドが優位）の銘柄を通過させるフィルタ。"""
    if 'rs_trend_s21' not in merged.columns or 'rs_trend_s63' not in merged.columns:
        return pd.Series(True, index=merged.index)
    return merged['rs_trend_s21'] < merged['rs_trend_s63']


def filter_rs_trend_s14_lt_s21(merged: pd.DataFrame) -> pd.Series:
    """RS Trend の生値で s14 < s21 の銘柄を通過させるフィルタ。"""
    if 'rs_trend_s14' not in merged.columns or 'rs_trend_s21' not in merged.columns:
        return pd.Series(True, index=merged.index)
    return merged['rs_trend_s14'] < merged['rs_trend_s21']


def _theme_comparison_mask(
    merged: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
    col_a: str,
    col_b: str,
) -> pd.Series:
    """テーマ行で col_a > col_b のテーマと、その構成銘柄を通過させる共通ロジック。"""
    if col_a not in merged.columns or col_b not in merged.columns:
        return pd.Series(True, index=merged.index)

    theme_rows = merged[merged['category'] == 'テーマ']
    leading_themes = theme_rows[theme_rows[col_a] > theme_rows[col_b]]['symbol_id'].values

    stocks_in_themes = df_theme_constituents[
        df_theme_constituents['theme_id'].isin(leading_themes)
    ]['symbol_id'].values

    return (
        ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
        ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
    )


def filter_theme_rs_trend_rank_s14_gt_s21(
    merged: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """テーマの RS Trend %rank s14 > s21 に属する銘柄を通過させるフィルタ。

    前提: merged に 'rs_condition_14_rank' と 'rs_condition_21_rank' が存在すること。
    """
    return _theme_comparison_mask(merged, df_theme_constituents,
                                  'rs_condition_14_rank', 'rs_condition_21_rank')


def filter_theme_rs_trend_rank_s21_gt_s63(
    merged: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
) -> pd.Series:
    """テーマの RS Trend %rank s21 > s63 に属する銘柄を通過させるフィルタ。

    前提: merged に 'rs_condition_21_rank' と 'rs_condition_63_rank' が存在すること。
    """
    return _theme_comparison_mask(merged, df_theme_constituents,
                                  'rs_condition_21_rank', 'rs_condition_63_rank')


def filter_vcp_breakout(
    merged: pd.DataFrame,
    high_window: int = 63,
    vcr_contraction_max: float = 0.8,
    base_high_tol: float = 15.0,
    near_high_tol: float = 4.0,
    breakout_change: float = 4.0,
    breakout_vol_mult: float = 1.5,
    pivot_tol: float = None,
    base_vol_dry_max: float = None,
) -> pd.Series:
    """VCP 収縮からのブレイクアウト（出来高膨張を伴う大陽線）イベントを通過させるフィルタ。

    「昨日まで収縮した高値圏の土台にあった銘柄が、今日 N日高値の近傍で
    出来高を伴う大陽線を出した日」だけを True にするイベントフィルタ。

    条件:
    - 前日まで収縮: prev_vcr <= vcr_contraction_max
    - 高値圏の土台: prev_dist_52w_high_pct >= -base_high_tol（浅い＝高値近くの土台）
    - 当日は N日高値の近傍: dist_{W}_high_pct >= -near_high_tol
    - ブレイクの大陽線: change_1d_pct >= breakout_change（イベントトリガー）
    - 出来高膨張: vol_surge_21 >= breakout_vol_mult（収縮の逆）
    - 上昇トレンド地合い: is_trend_template == 1
    - [pivot_tol 指定時] 真のピボットクロス: 今日の終値が「昨日までの」N日最大高値の
      (1 - pivot_tol/100) 倍以上。dist_Nd_high_pct は当日を含むローリング最大で
      計算されるため直接は使えないが、change_1d_pct（終値の前日比）から前日終値を
      復元すると次の等価式で判定できる（doc: e4_vcp_pivot_dryup_plan §3.1）:
        (1 + change_1d_pct/100) × (1 + prev_dist_{W}_high_pct/100) >= 1 - pivot_tol/100
    - [base_vol_dry_max 指定時] 土台のドライアップ: prev_vol_surge_21 <= base_vol_dry_max
      （前日の出来高が21日平均比で枯れていた。枯れ→膨張のコントラストが VCP の核心）

    high_window: 63（3ヶ月ベース→dist_63d_high_pct）または 252（52週→dist_52w_high_pct）。

    設計メモ（2026-07-05）: 当初はピボット距離（dist_Nd_high_pct）の前日→当日クロスで
    ブレイクを検出したが、dist はローリング最大に当日を含むため、新高値を付けた強い
    ブレイク日ほど終値が(新)高値から乖離して落ちるという構造的欠陥があり検出数が過少に
    なった（診断: 収縮＋高値圏の翌日 dist は95%点でも-2.34%）。ブレイクの検出を
    change_1d_pct の大陽線に切り替え、dist は緩い「高値近傍」ゲートに降格した。
    2026-07-09: 比較対象を「前日までの」最大に取ることで上記欠陥を回避した真のクロス条件を
    pivot_tol として追加（None なら従来挙動のまま）。

    prev カラムが無い場合は **全 False**（deny-by-default）。イベントは前日比較が本質で、
    前日情報が無いのに全通過させると「全銘柄がブレイク」という危険な過剰包含になるため、
    他の prev 依存フィルタ（RRG 等）の no-op フォールバックとは意図的に挙動を変える。
    pivot_tol / base_vol_dry_max 有効時に必要な prev カラムが無い場合も同様に全 False。
    """
    today_col = 'dist_52w_high_pct' if high_window == 252 else 'dist_63d_high_pct'
    prev_dist_col = 'prev_dist_52w_high_pct' if high_window == 252 else 'prev_dist_63d_high_pct'

    required_prev = ['prev_vcr', 'prev_dist_52w_high_pct']
    if pivot_tol is not None:
        required_prev.append(prev_dist_col)
    if base_vol_dry_max is not None:
        required_prev.append('prev_vol_surge_21')
    if any(c not in merged.columns for c in required_prev):
        return pd.Series(False, index=merged.index)
    required_today = [today_col, 'change_1d_pct', 'vol_surge_21', 'is_trend_template']
    if any(c not in merged.columns for c in required_today):
        return pd.Series(False, index=merged.index)

    prev_contracted = merged['prev_vcr'] <= vcr_contraction_max
    prev_high_base = merged['prev_dist_52w_high_pct'] >= -base_high_tol
    near_high = merged[today_col] >= -near_high_tol
    breakout_bar = merged['change_1d_pct'] >= breakout_change
    vol_expansion = merged['vol_surge_21'] >= breakout_vol_mult
    uptrend = merged['is_trend_template'] == 1

    mask = (prev_contracted & prev_high_base & near_high
            & breakout_bar & vol_expansion & uptrend)

    if pivot_tol is not None:
        # close_today >= prev_rollmax × (1 - tol/100) の等価式（docstring 参照）
        growth_vs_prev_max = ((1 + merged['change_1d_pct'] / 100)
                              * (1 + merged[prev_dist_col] / 100))
        mask &= growth_vs_prev_max >= (1 - pivot_tol / 100)

    if base_vol_dry_max is not None:
        mask &= merged['prev_vol_surge_21'] <= base_vol_dry_max

    return mask.fillna(False)


def filter_structure_1st_break(
    merged: pd.DataFrame,
    fib_1st: float = 0.618,
) -> pd.Series:
    """LL-HL 構造の 1st Pivot（fib 0.618 戻し）を当日上抜けたイベントを通過させる。

    改良版 Advanced Structure Pivot の `rt_1st_break` の移植。原文の定義:

        fib_range     = pivot - hl
        fib_1st_price = hl + fib_range * 0.618
        rt_1st_break  = is_setup and close[1] <= fib_1st_price and close > fib_1st_price
                        and (na(break_val) or close <= break_val)

    作者は「LL-HL 構造における Fib 0.618 ブレイクであり、**教科書的な押し目買い
    ポイント**に相当」と説明している。本ピボット（2nd）未達であることも条件で、
    2nd を既に抜けていれば `rt_2nd_break` 側の管轄になる（排他）。

    **前日終値は `change_1d_pct` から復元する。** `prev_close` は ScreenerFrame に
    供給されていないため（`filter_vcp_breakout` と同じ手法）:
        prev_close = close / (1 + change_1d_pct / 100)

    比較に使う 1st 水準は Pine と同じく**当日の構造から算出した値**を前日側にも使う
    （Pine の `d_long.fib_1st_price` は描画オブジェクトの現在値であり、前日の値では
    ないため）。

    構造が生きていない行（`sp_pivot` が NULL）は自然に False になる。
    必要カラムが無い場合は **全 False**（deny-by-default）。イベントは前日比較が
    本質であり、前日情報が無いのに全通過させると危険な過剰包含になるため。
    """
    required = ('sp_pivot', 'sp_hl', 'close', 'change_1d_pct')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)

    fib_range = merged['sp_pivot'] - merged['sp_hl']
    level = merged['sp_hl'] + fib_range * fib_1st
    prev_close = merged['close'] / (1 + merged['change_1d_pct'] / 100)

    mask = (
        (prev_close <= level)                    # 前日は 1st 以下
        & (merged['close'] > level)              # 当日に上抜け
        & (merged['close'] <= merged['sp_pivot'])  # 2nd（本ピボット）は未達
    )
    return mask.fillna(False)


def filter_structure_2nd_break(merged: pd.DataFrame) -> pd.Series:
    """LL-HL 構造の 2nd Pivot（本ピボット）を当日上抜けたイベント。

    作者は「2nd pivot が本来のエントリーポイント」と説明している。

        rt_2nd_break = is_setup and close[1] <= break_val and close > break_val

    **TP1 上限は付けない。** Pine の原文には `close <= tp1_price` の排他があるが、
    作者が公開しているスクリーナー出力と突き合わせたところ、TP1 を超えた銘柄も
    2nd のリストに含まれていた（2026-08-25 の BEAM: close 31.03 / pivot 28.47）。
    上限を外すと3日分・27銘柄すべてが一致する（2026-08-27 検証）。

    前日終値は `change_1d_pct` から復元する（`filter_structure_1st_break` と同じ）。
    構造が生きていない行は `sp_pivot` が NULL なので自然に False になる。
    """
    required = ('sp_pivot', 'close', 'change_1d_pct')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)

    prev_close = merged['close'] / (1 + merged['change_1d_pct'] / 100)
    mask = (prev_close <= merged['sp_pivot']) & (merged['close'] > merged['sp_pivot'])
    return mask.fillna(False)


def filter_structure_trend_line_break(merged: pd.DataFrame) -> pd.Series:
    """カウンタートレンド線を当日上抜けたイベント（作者の `rt_cnt_break`）。

    LL-HL 構造が**成立していない**期間に、ショート側のピボット高値2点を結んだ
    下向きの抵抗線が引かれる。これを終値が上抜けることが
    「次の上昇トレンドへの転換」シグナル。構造が生きている行は `sp_counter` が
    NULL なので自然に False になる（`sp_pivot` とは排他）。

    前日終値は `change_1d_pct` から復元する（1st / 2nd break と同じ）。

    > [!NOTE]
    > **前日のライン値は当日の値で代用している。** `sp_counter` は傾きを持つ線なので
    > 厳密には前日の水準は当日と異なるが、スクリーナーは当日の1行しか見ないため
    > 復元できない。1日ぶんの傾き（実測で概ね終値の 0.1〜0.5%）は許容する。
    > 作者の実出力（2026-08-26 の BHVN / ERAS）はこの近似で両方とも再現できている。
    """
    required = ('sp_counter', 'close', 'change_1d_pct')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)

    prev_close = merged['close'] / (1 + merged['change_1d_pct'] / 100)
    mask = (prev_close <= merged['sp_counter']) & (merged['close'] > merged['sp_counter'])
    return mask.fillna(False)

def filter_is_zone_break_bull_flip(merged: pd.DataFrame) -> pd.Series:
    """Direction via Zone Break: 前日Bear→当日Bullに転換した銘柄を通過させる。

    Pine原文の `isBreak_bl` 由来の反転イベント（計画書 doc/completed/zone_break_plan.md
    §3.1.1 参照）。イベント型フィルタのため、必要カラムが無い場合は全False
    （`filter_structure_1st_break` 等と同じ deny-by-default 方針）。
    """
    required = ('is_zone_break_bull', 'prev_is_zone_break_bull')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)
    mask = (merged['prev_is_zone_break_bull'] == False) & (merged['is_zone_break_bull'] == True)
    return mask.fillna(False)


def filter_is_zone_break_bear_flip(merged: pd.DataFrame) -> pd.Series:
    """Direction via Zone Break: 前日Bull→当日Bearに転換した銘柄を通過させる（対称）。"""
    required = ('is_zone_break_bull', 'prev_is_zone_break_bull')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)
    mask = (merged['prev_is_zone_break_bull'] == True) & (merged['is_zone_break_bull'] == False)
    return mask.fillna(False)


def filter_is_zone_break_bull_breakout(merged: pd.DataFrame) -> pd.Series:
    """Direction via Zone Break: Bull継続中にzb_bslが前日から上昇した銘柄を通過させる。

    前日・当日ともBullが継続していること（フリップ当日は prev_is_zone_break_bull==False
    になるため自然に除外される）に加え、`zb_bsl` が前日から更新（上昇）されていることを条件とする。

    **注意（2026-09-16 code-review Angle Aで検出）**: 名前は「継続ブレイク」だが、
    Pine原文の `isConf_bl`（確定closeがBSLを上抜けた後の高値フラクタル確定）に厳密には
    一致しない。`zone_break.py` の内部実装では、`isConf_bl` を経ない素朴なBSL追従更新
    （新しいトレンドの立ち上がり時、内部候補int_ssl_blが確立する前の高値フラクタル追従。
    `zone_break.py::_zone_break_scan` L113 相当）でも `zb_bsl` は上昇しうるため、本フィルタは
    それも含む広い「Bull継続中のBSL上昇」を検出する。`isConf_bl`確定イベントだけに絞るには
    `zone_break_series` 側でその遷移を別途公開する実装変更が要る（計画書 §8.1 参照）。
    """
    required = ('is_zone_break_bull', 'prev_is_zone_break_bull', 'zb_bsl', 'prev_zb_bsl')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)
    mask = (
        (merged['prev_is_zone_break_bull'] == True)
        & (merged['is_zone_break_bull'] == True)
        & (merged['zb_bsl'] > merged['prev_zb_bsl'])
    )
    return mask.fillna(False)


def filter_is_zone_break_bear_breakout(merged: pd.DataFrame) -> pd.Series:
    """Direction via Zone Break: Bear継続中にzb_sslが前日から下落した銘柄を通過させる（対称）。

    `filter_is_zone_break_bull_breakout` と同じ注意点（isConf_bl確定イベントだけに
    絞られておらず、より広い「Bear継続中のSSL下落」を検出する）が当てはまる。
    """
    required = ('is_zone_break_bull', 'prev_is_zone_break_bull', 'zb_ssl', 'prev_zb_ssl')
    if any(c not in merged.columns for c in required):
        return pd.Series(False, index=merged.index)
    mask = (
        (merged['prev_is_zone_break_bull'] == False)
        & (merged['is_zone_break_bull'] == False)
        & (merged['zb_ssl'] < merged['prev_zb_ssl'])
    )
    return mask.fillna(False)


# ============================================================
# 特殊ブールフィルタキーのレジストリ
# ============================================================
# スクリーナープリセット (screener_presets.toml) およびバックテスト戦略
# (backtest_config.toml) で使用可能な「特殊ブールフィルタ」のキー一覧。
# API (screener_router) とバックテスト (backtest_runner の検証) の両方が
# この単一のレジストリを参照する。
# 唯一の定義場所は screener_registry.EXPLICIT_SPECS（kind="special"）であり、
# ここではその導出値として後方互換のために残す（外部参照が3ファイルあるため）。
SPECIAL_FILTER_KEYS = {
    key for key, spec in EXPLICIT_SPECS.items() if spec.kind == "special"
}


