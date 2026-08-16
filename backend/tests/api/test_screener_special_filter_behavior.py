"""スクリーナー特殊フィルタの挙動固定テスト（D-2 共通化後の恒久テスト）。

API の特殊ブールフィルタは screener_cross_section.evaluate_special_filters 経由で
indicators/screener_filters.py の純関数（バックテストと同一実体）を呼ぶ。
本テストの期待値は、共通化移行時に旧 SQLAlchemy 実装との同値性検証
（S-2 の意図した差異を除き完全一致）を経て確定したものである。

意味論メモ:
- is_rs_ratio_rank_e21_gt_e63: テーマも自行のランク比較で判定される
  （旧 API 実装はテーマを常に除外していた。バックテスト側の意味論に統一）。
- min_market_cap: テーマ（market_cap なし）は免除される（S-1、バックテストと同一）。
"""
from datetime import date

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from api.screener_cross_section import build_cross_section, evaluate_special_filters

_engine = create_engine(
    "sqlite:///file:equiv_mem?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

PREV = date(2026, 6, 30)
LATEST = date(2026, 7, 1)


@pytest.fixture()
def db():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    s = _TestSession()

    # --- Symbols: テーマ2 + 個別3 ---
    s.add_all([
        Symbol(id=100, ticker="_STRONG_", name="強テーマ", category="テーマ", active=1),
        Symbol(id=101, ticker="_WEAK_", name="弱テーマ", category="テーマ", active=1),
        Symbol(id=1, ticker="LEAD", name="Leading株", category="個別", active=1),
        Symbol(id=2, ticker="LAG", name="Lagging株", category="個別", active=1),
        Symbol(id=3, ticker="IMPR", name="Improving株(テーマ無所属)", category="個別", active=1),
    ])
    # 構成銘柄: 1→強テーマ, 2→弱テーマ, 3は無所属
    s.add_all([
        ThemeConstituent(theme_id=100, symbol_id=1, weight=1.0),
        ThemeConstituent(theme_id=101, symbol_id=2, weight=1.0),
    ])

    # --- DailyPrice (基準日のみ、JOIN 用)。market_cap はテーマ=None（S-1 検証用） ---
    _mcaps = {100: None, 101: None, 1: 1e9, 2: 6e8, 3: 1e8}
    for sid in (100, 101, 1, 2, 3):
        s.add(DailyPrice(symbol_id=sid, date=LATEST, open=100, high=105, low=95, close=100,
                         volume=1000, market_cap=_mcaps[sid]))

    # --- Indicators ---
    def ind(sid, d, **kw):
        return Indicator(symbol_id=sid, date=d, **kw)

    s.add_all([
        # 株1: Leading-In 成立 / s21<s63 / s14<s21 / MACD 上昇 / e14>e21>e63
        ind(1, PREV,   rs_ratio_e21=-0.1, rs_momentum_e21=0.6, rs_macd_hist_21=0.2),
        ind(1, LATEST, rs_ratio_e21=0.3, rs_momentum_e21=0.7, rs_macd_hist_21=0.5,
            rs_ratio_e14=0.4, rs_ratio_e63=0.1,
            rs_trend_s14=0.8, rs_trend_s21=1.0, rs_trend_s63=1.5, is_trend_template=1),
        # 株2: Lagging-In 成立（前日は非Lagging）/ s21>s63 / MACD 負 / e14<e21<e63
        ind(2, PREV,   rs_ratio_e21=0.1, rs_momentum_e21=0.0, rs_macd_hist_21=0.3),
        ind(2, LATEST, rs_ratio_e21=-0.5, rs_momentum_e21=-0.5, rs_macd_hist_21=-0.1,
            rs_ratio_e14=0.1, rs_ratio_e63=0.3,
            rs_trend_s14=2.5, rs_trend_s21=2.0, rs_trend_s63=1.5, is_trend_template=0),
        # 株3: Improving-In 成立（前日Lagging→当日Improving加速）/ 自身のe21>e63 (テーマ無所属の検証用)
        ind(3, PREV,   rs_ratio_e21=-0.5, rs_momentum_e21=-0.1, rs_macd_hist_21=0.1),
        ind(3, LATEST, rs_ratio_e21=-0.3, rs_momentum_e21=0.6, rs_macd_hist_21=0.4,
            rs_ratio_e14=0.5, rs_ratio_e63=0.1,
            rs_trend_s14=1.0, rs_trend_s21=1.1, rs_trend_s63=1.2, is_trend_template=0),
        # 強テーマ100: e14>e21>e63
        ind(100, PREV,   rs_ratio_e21=0.2, rs_momentum_e21=0.1, rs_macd_hist_21=0.1),
        ind(100, LATEST, rs_ratio_e21=0.4, rs_momentum_e21=0.2, rs_macd_hist_21=0.2,
            rs_ratio_e14=0.5, rs_ratio_e63=0.2,
            rs_trend_s14=1.2, rs_trend_s21=1.1, rs_trend_s63=1.0),
        # 弱テーマ101: e14<e21<e63
        ind(101, PREV,   rs_ratio_e21=-0.2, rs_momentum_e21=-0.1, rs_macd_hist_21=-0.1),
        ind(101, LATEST, rs_ratio_e21=0.2, rs_momentum_e21=-0.2, rs_macd_hist_21=-0.2,
            rs_ratio_e14=0.1, rs_ratio_e63=0.4,
            rs_trend_s14=0.8, rs_trend_s21=0.9, rs_trend_s63=1.0),
    ])

    # --- RelativeRanks (wide, 基準日) ---
    s.add_all([
        RelativeRank(symbol_id=1, date=LATEST, group_name="個別",
                     rs_ratio_rank_e14=0.95, rs_ratio_rank_e21=0.9, rs_ratio_rank_e63=0.4,
                     rs_trend_rank_s14=0.9, rs_trend_rank_s21=0.8, rs_trend_rank_s63=0.5),
        RelativeRank(symbol_id=2, date=LATEST, group_name="個別",
                     rs_ratio_rank_e14=0.2, rs_ratio_rank_e21=0.3, rs_ratio_rank_e63=0.7,
                     rs_trend_rank_s14=0.2, rs_trend_rank_s21=0.3, rs_trend_rank_s63=0.6),
        RelativeRank(symbol_id=3, date=LATEST, group_name="個別",
                     rs_ratio_rank_e14=0.5, rs_ratio_rank_e21=0.5, rs_ratio_rank_e63=0.5,
                     rs_trend_rank_s14=0.5, rs_trend_rank_s21=0.5, rs_trend_rank_s63=0.5),
        RelativeRank(symbol_id=100, date=LATEST, group_name="テーマ",
                     rs_ratio_rank_e14=0.9, rs_ratio_rank_e21=0.8, rs_ratio_rank_e63=0.5,
                     rs_trend_rank_s14=0.9, rs_trend_rank_s21=0.7, rs_trend_rank_s63=0.4),
        RelativeRank(symbol_id=101, date=LATEST, group_name="テーマ",
                     rs_ratio_rank_e14=0.2, rs_ratio_rank_e21=0.3, rs_ratio_rank_e63=0.6,
                     rs_trend_rank_s14=0.2, rs_trend_rank_s21=0.3, rs_trend_rank_s63=0.7),
    ])
    s.commit()
    yield s
    s.close()


def _new_evaluator_ids(db, key: str, intensity: float = 0.0) -> set:
    """共通フィルタ経由で通過する symbol_id 集合を返す。"""
    merged, df_tc = build_cross_section(db, LATEST, PREV)
    return evaluate_special_filters(merged, df_tc, {key: True}, intensity)


# 期待値（旧 SQLAlchemy 実装との同値性検証を経て確定した挙動）
EXPECTED = {
    "rrg_leading_in": {1},
    "rrg_lagging_in": {2},
    "rrg_improving_in": {3},
    "is_theme_rs_ratio_e14_gt_e21": {1, 100},
    "is_theme_rs_ratio_e21_gt_e63": {1, 100},
    "is_theme_rs_ratio_rank_e14_gt_e21": {1, 100},
    "is_theme_rs_ratio_rank_e21_gt_e63": {1, 100},
    "is_rs_trend_s21_lt_s63": {1, 3, 101},
    "is_rs_trend_s14_lt_s21": {1, 3, 101},
    "is_theme_rs_trend_rank_s14_gt_s21": {1, 100},
    "is_rs_macd_hist_rising_21": {1, 3, 100},
    # S-2: テーマ100 (rank e21=0.8 > e63=0.5) も通過する（バックテストと同一の意味論）
    "is_rs_ratio_rank_e21_gt_e63": {1, 100},
}


@pytest.mark.parametrize("key", sorted(EXPECTED.keys()))
def test_special_filter_behavior(db, key):
    assert _new_evaluator_ids(db, key) == EXPECTED[key]


def test_rrg_intensity_threshold(db):
    """RRG 強度閾値を上げても正しく判定されること。"""
    assert _new_evaluator_ids(db, "rrg_leading_in", intensity=0.5) == {1}
    assert _new_evaluator_ids(db, "rrg_improving_in", intensity=0.5) == {3}


def test_no_filters_returns_none(db):
    merged, df_tc = build_cross_section(db, LATEST, PREV)
    assert evaluate_special_filters(merged, df_tc, {}) is None


# 2026-08-17（Phase 3 ステップ 3c）: API 側の SQL フィルタエンジン（_apply_filter /
# _parse_expression_to_filter）は撤去され、apply_filters_to_df（backtest 側と共通の唯一の
# エンジン）に一本化された（doc/in_progress/screener_filter_unification_plan.md §3.3.1 (c)）。
# 以下の2件は削除された旧実装を直接呼んでいたテストで、その実装自体が無くなったため
# 削除する。同等のカバレッジは以下に引き継がれている:
#   - S-1（min_market_cap のテーマ免除）: apply_filters_to_df 自身に同じ免除ロジックが
#     あり（screener_router と backtest が完全に同一の関数を呼ぶため、経路間の差は
#     構造的に発生しなくなった）、test_screener_parity.py の "min_market_cap"
#     PARITY_CASE が経路間の一致を検証する。
#   - expression の true/false リテラル: apply_filters_to_df の expression 評価
#     （true/false を True/False へ正規化してから pandas.query に渡す実装）を
#     screener_router.py も直接呼ぶようになったため、SQL 側の別実装は存在しない。
