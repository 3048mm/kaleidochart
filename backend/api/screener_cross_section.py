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

from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from indicators.screener_frame import assert_frame_contract
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
from indicators import screener_registry

logger = logging.getLogger(__name__)

# クロスセクションに必要な Indicator カラム。
# レジストリの kind='special' 全 spec の requires/prev_requires の和集合から導出する
# （doc/in_progress/screener_filter_unification_plan.md §3.1.4 (e)）。実在の Indicator 列だけに
# 絞る（requires にはランク列も混ざるため。ランクは _RANK_COL_MAP 経由で別途取り込む）。
_INDICATOR_COLUMN_NAMES = {c.name for c in Indicator.__table__.columns}

_SPECIAL_SPECS = [
    spec for spec in screener_registry.EXPLICIT_SPECS.values() if spec.kind == 'special'
]
_IND_COLS = sorted({
    col for spec in _SPECIAL_SPECS for col in spec.requires
    if col in _INDICATOR_COLUMN_NAMES
})
# 前日から prev_ プレフィックスで取り込むカラム
_PREV_COLS = sorted({
    col for spec in _SPECIAL_SPECS for col in spec.prev_requires
    if col in _INDICATOR_COLUMN_NAMES
})
# wide 形式 relative_ranks → 共通カラム名へのマッピング（レジストリを参照する）
_RANK_COL_MAP = dict(screener_registry.RANK_FRAME_ALIASES)

# ============================================================
# load_cross_section() 用の全カラムリスト（Phase 3 ステップ 3b）
# ============================================================
# ScreenerFrame 契約の「指標」区分は Indicator の全カラム（id/symbol_id/date を除く）を
# 正準名のまま持つ（doc/in_progress/screener_filter_unification_plan.md §3.3.1 (b)）。
# 「どの列を引くか」の手書きリストを作らないことが本計画の目的そのものなので、
# _IND_COLS（特殊フィルタが要求する列だけ）とは別に全カラムを持つ。
_ALL_INDICATOR_COLS = [
    c.name for c in Indicator.__table__.columns if c.name not in ('id', 'symbol_id', 'date')
]
# 「ランク」区分も同様に RelativeRank の全カラムを持つ（フレーム内名は to_frame_column() で変換）。
_ALL_RANK_COLS = [
    c.name for c in RelativeRank.__table__.columns
    if c.name not in ('id', 'symbol_id', 'date', 'group_name')
]
# 数値変換（pd.to_numeric）の対象外にする列。ticker/name/category/date は文字列/日付のまま保持し、
# symbol_id/active は SQLAlchemy から既に整数で返るため対象外にする。
_NON_NUMERIC_COLS = frozenset({'symbol_id', 'ticker', 'name', 'category', 'active', 'date'})


def load_cross_section(db, target_date, prev_date=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """SQLite から ScreenerFrame 契約を満たすクロスセクションを構築する。

    `backtest_screener.scan_signals_for_date` が Parquet 側で組む merged と同じ形の
    1営業日分フレームを SQLite から構築する（doc/in_progress/screener_filter_unification_plan.md
    §3.3.1 (b)）。まだ API はこの関数を呼ばない（切替は Phase 3 ステップ 3c）。

    Returns:
        (frame, df_theme_constituents)
    """
    # 母集団: アクティブなテーマ・個別銘柄 × 基準日の指標がある銘柄（INNER JOIN）
    sym_rows = db.query(
        Symbol.id, Symbol.ticker, Symbol.name, Symbol.category, Symbol.active
    ).filter(
        Symbol.active == 1, Symbol.category.in_(["テーマ", "個別"])
    ).all()
    df_sym = pd.DataFrame(sym_rows, columns=["symbol_id", "ticker", "name", "category", "active"])

    # 価格（DailyPrice）
    price_rows = db.query(
        DailyPrice.symbol_id, DailyPrice.date, DailyPrice.open, DailyPrice.high,
        DailyPrice.low, DailyPrice.close, DailyPrice.volume, DailyPrice.market_cap,
    ).filter(DailyPrice.date == target_date).all()
    df_price = pd.DataFrame(
        price_rows,
        columns=["symbol_id", "date", "open", "high", "low", "close", "volume", "market_cap"],
    )

    # 指標（Indicator の全カラム）
    ind_rows = db.query(
        Indicator.symbol_id, *[getattr(Indicator, c) for c in _ALL_INDICATOR_COLS]
    ).filter(Indicator.date == target_date).all()
    df_ind = pd.DataFrame(ind_rows, columns=["symbol_id"] + _ALL_INDICATOR_COLS)

    # ベース = 基準日の指標が存在する銘柄（既存 build_cross_section と同一の母集合）
    frame = df_sym.merge(df_ind, on="symbol_id", how="inner")
    frame = frame.merge(df_price, on="symbol_id", how="left")

    # 前日: kind='special' 全 spec の prev_requires の和集合（既存 _PREV_COLS と同じ導出）
    if prev_date is not None and _PREV_COLS:
        prev_rows = db.query(
            Indicator.symbol_id, *[getattr(Indicator, c) for c in _PREV_COLS]
        ).filter(Indicator.date == prev_date).all()
        df_prev = pd.DataFrame(
            prev_rows, columns=["symbol_id"] + [f"prev_{c}" for c in _PREV_COLS]
        )
        frame = frame.merge(df_prev, on="symbol_id", how="left")

    # ランク: 基準日以前の直近日（wide → フレーム内名。to_frame_column() 経由でリネーム）
    from sqlalchemy import func
    rank_date = db.query(func.max(RelativeRank.date)).filter(
        RelativeRank.date <= target_date
    ).scalar()
    if rank_date is not None:
        rank_frame_cols = [screener_registry.to_frame_column(c) for c in _ALL_RANK_COLS]
        rank_rows = db.query(
            RelativeRank.symbol_id, *[getattr(RelativeRank, c) for c in _ALL_RANK_COLS]
        ).filter(RelativeRank.date == rank_date).all()
        df_rank = pd.DataFrame(rank_rows, columns=["symbol_id"] + rank_frame_cols)
        frame = frame.merge(df_rank, on="symbol_id", how="left")

    # dtype: SQLAlchemy の行タプルから作った DataFrame は NULL の多い列が object dtype に
    # 推論されうるため、数値列を明示的に float へ変換してから契約検査に通す（§3.3.1 (b)）。
    for col in frame.columns:
        if col not in _NON_NUMERIC_COLS:
            frame[col] = pd.to_numeric(frame[col], errors='coerce')

    assert_frame_contract(frame, where='load_cross_section')

    # 構成銘柄マッピング
    tc_rows = db.query(ThemeConstituent.theme_id, ThemeConstituent.symbol_id).all()
    df_tc = pd.DataFrame(tc_rows, columns=["theme_id", "symbol_id"])

    return frame, df_tc


def build_cross_section(db, latest_date, previous_date=None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """基準日1日分のクロスセクション DataFrame を構築する（特殊フィルタ評価用の縮小版）。

    `load_cross_section()`（ScreenerFrame 契約のフル版）の薄いラッパ。特殊フィルタの評価にしか
    使わないため、戻り値の列は従来どおり symbol_id/category + 特殊フィルタが要求する指標/ランクに
    絞り込む。Phase 3 ステップ 3c で API がフル版へ切り替わるまで、戻り値の形・挙動は変えない
    （`evaluate_special_filters` および既存テストが依存しているため）。

    Returns:
        (merged, df_theme_constituents)
        merged: symbol_id, category + 指標/ランク（screener_filters が期待する共通カラム名）
    """
    frame, df_tc = load_cross_section(db, latest_date, previous_date)

    cols = ["symbol_id", "category"] + _IND_COLS
    if previous_date is not None:
        cols += [f"prev_{c}" for c in _PREV_COLS]
    cols += [c for c in _RANK_COL_MAP.values() if c in frame.columns]
    merged = frame[cols].copy()

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
                pivot_tol=(float(params["pivot_tol"])
                           if params.get("pivot_tol") is not None else None),
                base_vol_dry_max=(float(params["base_vol_dry_max"])
                                  if params.get("base_vol_dry_max") is not None else None),
            )

    return set(merged.loc[mask, "symbol_id"].tolist())
