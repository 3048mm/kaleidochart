"""
screener_filters.py — Shared Screener Filter Functions

pandas DataFrame ベースの特殊フィルタ関数を純粋関数として提供する。
backtest_screener.py および scenario_scorer.py の両方から呼び出し可能。

各関数は pd.Series (bool mask) を返し、呼び出し元で mask &= ... として適用する。
"""
import pandas as pd
import numpy as np
from typing import Optional


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


def filter_rs_macd_hist_rising_21(merged: pd.DataFrame) -> pd.Series:
    """
    RS-MACD ヒストグラムが正かつ前日より上昇（加速）している銘柄を通過させるフィルタ。

    - 'prev_rs_macd_hist_21' が無い場合（前日データなし）は「正」のみで判定。
    - 'rs_macd_hist_21' 自体が無い場合は全通過。

    Returns:
        pd.Series[bool]: True = 通過
    """
    if 'rs_macd_hist_21' not in merged.columns:
        return pd.Series(True, index=merged.index)
    if 'prev_rs_macd_hist_21' not in merged.columns:
        return merged['rs_macd_hist_21'] > 0
    return (merged['rs_macd_hist_21'] > 0) & (merged['rs_macd_hist_21'] > merged['prev_rs_macd_hist_21'])


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


# ============================================================
# 特殊ブールフィルタキーのレジストリ
# ============================================================
# スクリーナープリセット (screener_presets.toml) およびバックテスト戦略
# (backtest_config.toml) で使用可能な「特殊ブールフィルタ」のキー一覧。
# API (screener_router) とバックテスト (backtest_runner の検証) の両方が
# この単一のレジストリを参照する。
SPECIAL_FILTER_KEYS = {
    # RRG 象限転換
    "rrg_leading_in", "rrg_lagging_in", "rrg_improving_in",
    # テーマ RS Ratio 生値比較
    "is_theme_rs_ratio_e14_gt_e21", "is_theme_rs_ratio_e21_gt_e63",
    # テーマ RS Ratio %rank 比較
    "is_theme_rs_ratio_rank_e14_gt_e21", "is_theme_rs_ratio_rank_e21_gt_e63",
    # 個別 RS Ratio %rank 比較
    "is_rs_ratio_rank_e14_gt_e21", "is_rs_ratio_rank_e21_gt_e63",
    # RS Trend 生値比較
    "is_rs_trend_s21_lt_s63", "is_rs_trend_s14_lt_s21",
    # テーマ RS Trend %rank 比較
    "is_theme_rs_trend_rank_s14_gt_s21", "is_theme_rs_trend_rank_s21_gt_s63",
    # RS-MACD 加速
    "is_rs_macd_hist_rising_21",
    # VCP ブレイクアウト（収縮からのピボット上抜けイベント）
    "is_vcp_breakout",
}


