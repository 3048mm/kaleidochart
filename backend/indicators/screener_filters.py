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
        theme_ind['rs_ratio_21'] > theme_ind['rs_ratio_63']
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
    if 'prev_rs_ratio_21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    # Intensity 計算
    intensity = np.sqrt(merged['rs_ratio_21']**2 + merged['rs_momentum_21']**2)
    prev_intensity = np.sqrt(merged['prev_rs_ratio_21']**2 + merged['prev_rs_momentum_21']**2)

    mask = (
        (merged['rs_ratio_21'] > 0) & (merged['rs_momentum_21'] > 0) &       # Leading 象限
        (merged['rs_momentum_21'] > merged['prev_rs_momentum_21']) &           # 加速
        (intensity >= intensity_threshold) &                                     # Intensity (当日)
        (
            ((merged['prev_rs_ratio_21'] <= 0) | (merged['prev_rs_momentum_21'] <= 0)) |  # 前日非Leading
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
    if 'prev_rs_ratio_21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    intensity = np.sqrt(merged['rs_ratio_21']**2 + merged['rs_momentum_21']**2)
    prev_intensity = np.sqrt(merged['prev_rs_ratio_21']**2 + merged['prev_rs_momentum_21']**2)

    mask = (
        (merged['rs_ratio_21'] < 0) & (merged['rs_momentum_21'] > 0) &       # Improving 象限
        (merged['rs_momentum_21'] > merged['prev_rs_momentum_21']) &           # 加速
        (intensity >= intensity_threshold) &                                     # Intensity (当日)
        (
            ((merged['prev_rs_ratio_21'] < 0) & (merged['prev_rs_momentum_21'] <= 0)) |  # 前日 Lagging
            ((prev_intensity < intensity_threshold) & (merged['prev_rs_ratio_21'] < 0))   # 前日低Intensity+左半面
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
    if 'prev_rs_ratio_21' not in merged.columns:
        return pd.Series(True, index=merged.index)

    mask = (
        (merged['rs_ratio_21'] < 0) & (merged['rs_momentum_21'] < 0) &       # Lagging 象限
        ((merged['prev_rs_ratio_21'] >= 0) | (merged['prev_rs_momentum_21'] >= 0))  # 前日非Lagging
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

