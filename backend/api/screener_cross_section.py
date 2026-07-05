"""基準日クロスセクション構築と特殊フィルタ評価（D-2 共通化の API 側アダプタ）。

スクリーナー API の「特殊ブールフィルタ」を SQLAlchemy で再実装する代わりに、
基準日1日分のクロスセクション DataFrame を構築し、バックテストと同一の
純関数群 (indicators/screener_filters.py) でマスク評価する。

これにより RRG Leading In 等のロジックの実体は screener_filters.py の
1箇所のみとなり、スクリーナー画面とバックテストの銘柄抽出が常に一致する。
"""
import logging
from typing import Optional, Set, Dict, Any, Tuple

import pandas as pd

from db.models import Symbol, Indicator, RelativeRank, ThemeConstituent
from indicators.screener_filters import (
    filter_rs_rank_21_gt_63,
    filter_rs_rank_14_gt_21,
    filter_theme_rs21_gt_63,
    filter_theme_rs14_gt_21,
    filter_theme_rs_rank_21_gt_63,
    filter_theme_rs_rank_14_gt_21,
    filter_rrg_leading_in,
    filter_rrg_improving_in,
    filter_rrg_lagging_in,
    filter_rs_macd_hist_rising_21,
    filter_rs_trend_s21_lt_s63,
    filter_rs_trend_s14_lt_s21,
    filter_theme_rs_trend_rank_s14_gt_s21,
    filter_theme_rs_trend_rank_s21_gt_s63,
    filter_vcp_breakout,
    SPECIAL_FILTER_KEYS,
)

logger = logging.getLogger(__name__)

# クロスセクションに必要な Indicator カラム
_IND_COLS = [
    "rs_ratio_e14", "rs_ratio_e21", "rs_ratio_e63",
    "rs_momentum_e21",
    "rs_trend_s14", "rs_trend_s21", "rs_trend_s63",
    "rs_macd_hist_21",
    # VCP ブレイクアウト用（当日）
    "vcr", "dist_63d_high_pct", "dist_52w_high_pct", "vol_surge_21", "is_trend_template",
    "change_1d_pct",
]
# 前日から prev_ プレフィックスで取り込むカラム
_PREV_COLS = [
    "rs_ratio_e21", "rs_momentum_e21", "rs_macd_hist_21",
    # VCP ブレイクアウト用（前日）
    "vcr", "dist_63d_high_pct", "dist_52w_high_pct",
]
# wide 形式 relative_ranks → 共通カラム名へのマッピング
_RANK_COL_MAP = {
    "rs_ratio_rank_e14": "rs14_rank",
    "rs_ratio_rank_e21": "rs21_rank",
    "rs_ratio_rank_e63": "rs63_rank",
    "rs_trend_rank_s14": "rs_condition_14_rank",
    "rs_trend_rank_s21": "rs_condition_21_rank",
    "rs_trend_rank_s63": "rs_condition_63_rank",
}


def build_cross_section(db, latest_date, previous_date=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """基準日1日分のクロスセクション DataFrame を構築する。

    Returns:
        (merged, df_theme_constituents)
        merged: symbol_id, category + 指標/ランク（screener_filters が期待する共通カラム名）
    """
    # T1: アクティブなテーマ・個別銘柄
    sym_rows = db.query(Symbol.id, Symbol.category).filter(
        Symbol.active == 1, Symbol.category.in_(["テーマ", "個別"])
    ).all()
    df_sym = pd.DataFrame(sym_rows, columns=["symbol_id", "category"])

    # T3: 基準日の指標
    ind_rows = db.query(
        Indicator.symbol_id, *[getattr(Indicator, c) for c in _IND_COLS]
    ).filter(Indicator.date == latest_date).all()
    df_ind = pd.DataFrame(ind_rows, columns=["symbol_id"] + _IND_COLS)

    # ベース = 基準日の指標が存在する銘柄（エンドポイントの INNER JOIN と同一の母集合）
    merged = df_sym.merge(df_ind, on="symbol_id", how="inner")

    # T3: 前日の指標（RRG 転換・MACD 加速の判定用）
    if previous_date is not None:
        prev_rows = db.query(
            Indicator.symbol_id, *[getattr(Indicator, c) for c in _PREV_COLS]
        ).filter(Indicator.date == previous_date).all()
        df_prev = pd.DataFrame(
            prev_rows, columns=["symbol_id"] + [f"prev_{c}" for c in _PREV_COLS]
        )
        merged = merged.merge(df_prev, on="symbol_id", how="left")

    # T4: 基準日以前の直近ランク（wide → 共通カラム名）
    from sqlalchemy import func
    rank_date = db.query(func.max(RelativeRank.date)).filter(
        RelativeRank.date <= latest_date
    ).scalar()
    if rank_date is not None:
        rank_rows = db.query(
            RelativeRank.symbol_id,
            *[getattr(RelativeRank, c) for c in _RANK_COL_MAP.keys()]
        ).filter(RelativeRank.date == rank_date).all()
        df_rank = pd.DataFrame(
            rank_rows, columns=["symbol_id"] + list(_RANK_COL_MAP.values())
        )
        merged = merged.merge(df_rank, on="symbol_id", how="left")

    # 構成銘柄マッピング
    tc_rows = db.query(ThemeConstituent.theme_id, ThemeConstituent.symbol_id).all()
    df_tc = pd.DataFrame(tc_rows, columns=["theme_id", "symbol_id"])

    return merged, df_tc


def evaluate_special_filters(
    merged: pd.DataFrame,
    df_tc: pd.DataFrame,
    flags: Dict[str, Any],
    intensity_threshold: float = 0.0,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Set[int]]:
    """有効化された特殊ブールフィルタを AND 適用し、通過 symbol_id 集合を返す。

    Args:
        flags: {フィルタキー: True} の辞書（SPECIAL_FILTER_KEYS のサブセット）
        intensity_threshold: RRG フィルタの強度閾値
        params: フィルタの数値パラメータ辞書（VCP ブレイクアウトの vcr_contraction_max 等）。
            None の場合は各フィルタのデフォルト値を使用する。

    Returns:
        通過した symbol_id の set。有効なフィルタが1つも無ければ None（=無フィルタ）。
    """
    params = params or {}
    enabled = {k for k, v in flags.items() if v is True and k in SPECIAL_FILTER_KEYS}
    if not enabled:
        return None
    if merged.empty:
        return set()

    # テーマ系フィルタが期待する補助 DataFrame（merged 自身から構成）
    df_symbols_proxy = merged[["symbol_id", "category"]].rename(columns={"symbol_id": "id"})

    mask = pd.Series(True, index=merged.index)
    for key in enabled:
        if key == "rrg_leading_in":
            mask &= filter_rrg_leading_in(merged, intensity_threshold)
        elif key == "rrg_lagging_in":
            mask &= filter_rrg_lagging_in(merged)
        elif key == "rrg_improving_in":
            mask &= filter_rrg_improving_in(merged, intensity_threshold)
        elif key == "is_theme_rs_ratio_e14_gt_e21":
            mask &= filter_theme_rs14_gt_21(merged, merged, df_symbols_proxy, df_tc)
        elif key == "is_theme_rs_ratio_e21_gt_e63":
            mask &= filter_theme_rs21_gt_63(merged, merged, df_symbols_proxy, df_tc)
        elif key == "is_theme_rs_ratio_rank_e14_gt_e21":
            mask &= filter_theme_rs_rank_14_gt_21(merged, df_symbols_proxy, df_tc)
        elif key == "is_theme_rs_ratio_rank_e21_gt_e63":
            mask &= filter_theme_rs_rank_21_gt_63(merged, df_symbols_proxy, df_tc)
        elif key == "is_rs_ratio_rank_e14_gt_e21":
            mask &= filter_rs_rank_14_gt_21(merged)
        elif key == "is_rs_ratio_rank_e21_gt_e63":
            mask &= filter_rs_rank_21_gt_63(merged)
        elif key == "is_rs_trend_s21_lt_s63":
            mask &= filter_rs_trend_s21_lt_s63(merged)
        elif key == "is_rs_trend_s14_lt_s21":
            mask &= filter_rs_trend_s14_lt_s21(merged)
        elif key == "is_theme_rs_trend_rank_s14_gt_s21":
            mask &= filter_theme_rs_trend_rank_s14_gt_s21(merged, df_tc)
        elif key == "is_theme_rs_trend_rank_s21_gt_s63":
            mask &= filter_theme_rs_trend_rank_s21_gt_s63(merged, df_tc)
        elif key == "is_rs_macd_hist_rising_21":
            mask &= filter_rs_macd_hist_rising_21(merged)
        elif key == "is_vcp_breakout":
            mask &= filter_vcp_breakout(
                merged,
                high_window=int(params.get("breakout_high_window", 63)),
                vcr_contraction_max=float(params.get("vcr_contraction_max", 0.8)),
                base_high_tol=float(params.get("base_high_tol", 15.0)),
                near_high_tol=float(params.get("near_high_tol", 4.0)),
                breakout_change=float(params.get("breakout_change", 4.0)),
                breakout_vol_mult=float(params.get("breakout_vol_mult", 1.5)),
            )

    return set(merged.loc[mask, "symbol_id"].tolist())
