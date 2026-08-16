"""2つのローダ（SQLite / Parquet 側）が ScreenerFrame 契約を満たすことの検証（Phase 3 ステップ 3b）。

`doc/in_progress/screener_filter_unification_plan.md` §3.3.1 (b) に対応する。
この時点では API の切替（3c）は行わない。`load_cross_section()` の新設と、
2つのローダが同じ契約を満たすことだけを検証する。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Integer, SmallInteger, create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, DailyPrice, Indicator, RelativeRank, Symbol, ThemeConstituent
from indicators.screener_frame import IDENTITY_COLUMNS, PRICE_COLUMNS, assert_frame_contract
from indicators.screener_registry import to_frame_column
from api.screener_cross_section import load_cross_section
from backend.backtest.backtest_screener import apply_filters_to_df

# ============================================================
# 日付・シンボル定義
# ============================================================
PREV = date(2026, 6, 30)
LATEST = date(2026, 7, 1)

STOCK_IDS = [1, 2]          # 個別銘柄
THEME_ID = 901               # テーマ（構成銘柄への波及・リーディングテーマ判定用に残す想定）

# 意図的に全行 NULL のまま残す Indicator の Float 列（object dtype 化を誘発させるフィクスチャ）。
NULL_HEAVY_COL = "rs_momentum_e200"

_IND_COL_NAMES = [c.name for c in Indicator.__table__.columns if c.name not in ("id", "symbol_id", "date")]
_RANK_COL_NAMES = [
    c.name for c in RelativeRank.__table__.columns
    if c.name not in ("id", "symbol_id", "date", "group_name")
]


def _default_indicator_row(null_value) -> dict:
    """Indicator の全カラムへ値を入れる。NULL_HEAVY_COL だけは null_value のまま残す。"""
    row = {}
    for c in Indicator.__table__.columns:
        if c.name in ("id", "symbol_id", "date"):
            continue
        if c.name == NULL_HEAVY_COL:
            row[c.name] = null_value
            continue
        if isinstance(c.type, (Integer, SmallInteger)):
            row[c.name] = 0
        else:
            row[c.name] = 1.0
    return row


def _default_rank_row() -> dict:
    return {name: 0.5 for name in _RANK_COL_NAMES}


# ============================================================
# 「1つの真実」の組み立て
# ============================================================
SYMBOLS = (
    [{"id": t, "ticker": f"STK{t}", "name": f"Stock {t}", "category": "個別"} for t in STOCK_IDS]
    + [{"id": THEME_ID, "ticker": "_THA_", "name": "テーマA", "category": "テーマ"}]
)

THEME_CONSTITUENTS = [(THEME_ID, 1)]

_INDICATOR_TODAY: dict = {}
_INDICATOR_PREV: dict = {}
_RANK_BY_ID: dict = {}
_PRICE_BY_ID: dict = {}

for _t in STOCK_IDS:
    _row = _default_indicator_row(None)
    _row["is_trend_template"] = 1 if _t == 1 else 0
    _INDICATOR_TODAY[_t] = _row
    _INDICATOR_PREV[_t] = _default_indicator_row(None)
    _RANK_BY_ID[_t] = {**_default_rank_row(), "rs_ratio_rank_e21": 0.6 if _t == 1 else 0.3}
    _PRICE_BY_ID[_t] = {
        "open": 99.0, "high": 105.0, "low": 95.0, "close": 100.0,
        "volume": 1_000_000, "market_cap": _t * 2e8,
    }

_INDICATOR_TODAY[THEME_ID] = {**_default_indicator_row(None), "is_trend_template": 0}
_INDICATOR_PREV[THEME_ID] = _default_indicator_row(None)
_RANK_BY_ID[THEME_ID] = {**_default_rank_row(), "rs_ratio_rank_e21": 0.9}
_PRICE_BY_ID[THEME_ID] = {
    "open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0,
    "volume": 100_000, "market_cap": None,
}


def seed_sqlite(session) -> None:
    """①SQLite 経由（load_cross_section）へ「1つの真実」を投入する。"""
    for sym in SYMBOLS:
        session.add(Symbol(id=sym["id"], ticker=sym["ticker"], name=sym["name"],
                            category=sym["category"], active=1))
    for theme_id, symbol_id in THEME_CONSTITUENTS:
        session.add(ThemeConstituent(theme_id=theme_id, symbol_id=symbol_id, weight=1.0))
    for sym in SYMBOLS:
        session.add(DailyPrice(symbol_id=sym["id"], date=LATEST, **_PRICE_BY_ID[sym["id"]]))
    for sym in SYMBOLS:
        sid = sym["id"]
        session.add(Indicator(symbol_id=sid, date=LATEST, **_INDICATOR_TODAY[sid]))
        session.add(Indicator(symbol_id=sid, date=PREV, **_INDICATOR_PREV[sid]))
    for sym in SYMBOLS:
        sid = sym["id"]
        group_name = "テーマ" if sid == THEME_ID else "個別"
        session.add(RelativeRank(symbol_id=sid, date=LATEST, group_name=group_name, **_RANK_BY_ID[sid]))
    session.commit()


def build_parquet_style_frame() -> pd.DataFrame:
    """②③バックテスト経路（`scan_signals_for_date`）と同じ手順で1日分フレームを組む。

    Parquet から読んだ実データは既に適切な dtype（NULL の多い列も float64）を持つため、
    ここでは明示的に numeric へキャストしたうえでマージする（SQLite 側の
    `load_cross_section` が行う NULL 由来 object dtype の後始末とは別の観点）。
    """
    ind_rows = [{"symbol_id": sid, "date": LATEST, **row} for sid, row in _INDICATOR_TODAY.items()]
    df_ind = pd.DataFrame(ind_rows)
    for col in _IND_COL_NAMES:
        df_ind[col] = pd.to_numeric(df_ind[col], errors="coerce")

    price_rows = [{"symbol_id": sid, "date": LATEST, **price} for sid, price in _PRICE_BY_ID.items()]
    df_price = pd.DataFrame(price_rows)

    df_symbols = pd.DataFrame([
        {"id": s["id"], "ticker": s["ticker"], "name": s["name"], "category": s["category"], "active": 1}
        for s in SYMBOLS
    ])

    price_cols = ["symbol_id", "date", "open", "high", "low", "close", "volume", "market_cap"]
    merged = df_ind.merge(df_price[price_cols], on=["symbol_id", "date"], how="inner")
    merged = merged.merge(
        df_symbols[["id", "ticker", "name", "category", "active"]],
        left_on="symbol_id", right_on="id", how="inner",
    )
    return merged


def build_parquet_style_frames_for_apply_filters():
    """apply_filters_to_df が要求する周辺 DataFrame 一式（df_ind/df_ranks/df_symbols/df_tc）を組む。"""
    ind_rows = []
    for d, ind_map in ((LATEST, _INDICATOR_TODAY), (PREV, _INDICATOR_PREV)):
        for sid, row in ind_map.items():
            ind_rows.append({"symbol_id": sid, "date": d, **row})
    df_ind = pd.DataFrame(ind_rows)
    for col in _IND_COL_NAMES:
        df_ind[col] = pd.to_numeric(df_ind[col], errors="coerce")

    rank_rows = []
    for sid, row in _RANK_BY_ID.items():
        for canonical, value in row.items():
            rank_rows.append({"symbol_id": sid, "date": LATEST, "indicator_name": canonical, "percent_rank": value})
    df_ranks = pd.DataFrame(rank_rows, columns=["symbol_id", "date", "indicator_name", "percent_rank"])

    df_symbols = pd.DataFrame([
        {"id": s["id"], "ticker": s["ticker"], "name": s["name"], "category": s["category"], "active": 1}
        for s in SYMBOLS
    ])
    df_tc = pd.DataFrame(THEME_CONSTITUENTS, columns=["theme_id", "symbol_id"])
    return df_ind, df_ranks, df_symbols, df_tc


# ============================================================
# fixtures
# ============================================================
@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    seed_sqlite(s)
    yield s
    s.close()


# ============================================================
# テスト本体
# ============================================================
def test_sqlite_loader_satisfies_frame_contract(db):
    """1. SQLite ローダ（load_cross_section）が ScreenerFrame 契約を満たすこと。"""
    frame, df_tc = load_cross_section(db, LATEST, PREV)
    # load_cross_section 自身が内部で assert_frame_contract を通しているが、
    # ここでも明示的に検証する（呼び出し側からの回帰にも気づけるように）。
    assert_frame_contract(frame, where="test_sqlite_loader_satisfies_frame_contract")
    assert not frame.empty
    assert not df_tc.empty


def test_parquet_style_frame_satisfies_frame_contract():
    """2. Parquet 側（scan_signals_for_date と同じ手順）のフレームも契約を満たすこと。"""
    merged = build_parquet_style_frame()
    assert_frame_contract(merged, where="test_parquet_style_frame_satisfies_frame_contract")
    assert not merged.empty


def test_both_loaders_share_identity_and_price_columns(db):
    """3. 両ローダの列集合が同一性・価格の区分で一致すること。"""
    frame_sqlite, _ = load_cross_section(db, LATEST, PREV)
    frame_parquet = build_parquet_style_frame()

    for cols in (IDENTITY_COLUMNS, PRICE_COLUMNS):
        assert cols <= set(frame_sqlite.columns), f"SQLite 側に不足: {cols - set(frame_sqlite.columns)}"
        assert cols <= set(frame_parquet.columns), f"Parquet 側に不足: {cols - set(frame_parquet.columns)}"


def test_sqlite_loader_uses_frame_internal_rank_names(db):
    """4. ランクがフレーム内名で入っていること（rs_ratio_rank_e21 ではなく rs21_rank）。"""
    frame, _ = load_cross_section(db, LATEST, PREV)
    frame_col = to_frame_column("rs_ratio_rank_e21")
    assert frame_col == "rs21_rank"
    assert frame_col in frame.columns
    # 正準名そのままの列は存在しない（リネームされていることの確認）
    assert "rs_ratio_rank_e21" not in frame.columns


def test_theme_rows_are_retained_by_both_loaders(db):
    """5. テーマ行が残っていること（category=='テーマ' の行が存在する。出力除外はここではしない）。"""
    frame_sqlite, _ = load_cross_section(db, LATEST, PREV)
    frame_parquet = build_parquet_style_frame()

    assert (frame_sqlite["category"] == "テーマ").any()
    assert (frame_parquet["category"] == "テーマ").any()


def test_sqlite_loader_has_no_object_dtype_numeric_columns(db):
    """6. 数値列に object dtype が無いこと（NULL が多い列を含むフィクスチャで確認）。"""
    frame, _ = load_cross_section(db, LATEST, PREV)

    # NULL_HEAVY_COL は全行 NULL のまま投入したフィクスチャなので、object dtype に
    # 推論されやすい典型例（P1-2）。float 系 dtype に変換されていることを確認する。
    assert NULL_HEAVY_COL in frame.columns
    assert not pd.api.types.is_object_dtype(frame[NULL_HEAVY_COL])
    assert frame[NULL_HEAVY_COL].isna().all()

    excluded = IDENTITY_COLUMNS | {"date"}
    object_cols = [c for c in frame.columns if c not in excluded and pd.api.types.is_object_dtype(frame[c])]
    assert object_cols == []


def test_load_cross_section_result_can_be_passed_to_apply_filters_to_df(db):
    """7. load_cross_section の結果を apply_filters_to_df に渡して例外なく動くこと（3c の前哨）。"""
    frame, _ = load_cross_section(db, LATEST, PREV)
    df_ind, df_ranks, df_symbols, df_tc = build_parquet_style_frames_for_apply_filters()

    filtered = apply_filters_to_df(
        merged=frame,
        target_date=LATEST,
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_tc,
        strategy={"is_trend_template": True},
        prev_date=PREV,
    )

    # is_trend_template=1 なのは STK1（symbol_id=1）だけ。テーマ(THEME_ID)は
    # 出力除外ルール（OUTPUT_EXCLUDED_CATEGORIES）によりそもそも候補から落ちる。
    assert set(filtered["ticker"].tolist()) == {"STK1"}
