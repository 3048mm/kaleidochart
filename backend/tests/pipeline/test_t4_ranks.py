"""SQLite 側の T4（relative_ranks）計算のテスト（pipeline/phases/t4_ranks.py）。

## なぜ必要か

T4 は SQLite（`t4_ranks.py`）と Parquet（`pipeline/parquet_recompute.py`）の
2経路で同じ `PERCENT_RANK` ロジックを再実装している。片方だけ直すと
2経路の結果が乖離するため、両方に同じ仕様のテストを置く
（Parquet 側: `backend/tests/pipeline/test_parquet_recompute.py`）。

## NULL の扱い（2026-09-23 改訂）

`sync_phase_t4_ranks` は
    PERCENT_RANK() OVER(PARTITION BY s.category ORDER BY i.{col} ASC)
を素朴に使っている。SQLite の `PERCENT_RANK` は **NULL を最小値として扱う**ため、
判定不能な銘柄にランク 0.0（最下位）を与え、かつ母集団 n にも数えてしまう。

新仕様では NULL は判定不能として母集団から除外し、ランクも NULL のまま返す
（`CASE WHEN col IS NULL THEN NULL ELSE PERCENT_RANK() OVER(... PARTITION BY
category, (col IS NULL) ...) END`）。

このテストファイルは新仕様を固定する。現行実装（修正前）に対しては
意図的に red になる。
"""

import logging
import os
import sys
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db.models import Base, Indicator, RelativeRank, Symbol  # noqa: E402
from pipeline.phases.t4_ranks import sync_phase_t4_ranks  # noqa: E402


def _add_symbol(db, sid: int, ticker: str, category: str = "個別", active: int = 1):
    db.add(Symbol(id=sid, ticker=ticker, exchange="NASDAQ", name=ticker,
                  category=category, active=active))


def test_null_indicator_gets_null_rank_and_is_excluded_from_population():
    """NULL 銘柄のランクは NULL になり、かつ他銘柄の母集団からも除外される。

    同一カテゴリに4銘柄（rs_value = NULL, 2.0, 3.0, 4.0）を投入する。
    新仕様では:
      - NULL 銘柄（A）のランクは NULL
      - 残り3銘柄（B, C, D）は NULL を除いた母集団（n=3）で正規化される
        → (rank-1)/(n-1) = [0.0, 0.5, 1.0]

    現行実装（NULL を最小値として扱う）では、A のランクが 0.0 になり、
    B/C/D も NULL を含む母集団（n=4）で計算されるため 1/3, 2/3, 1.0 になる
    → このテストは意図的に red になる。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        d = date(2026, 7, 2)
        _add_symbol(db, 1, "AAA")
        _add_symbol(db, 2, "BBB")
        _add_symbol(db, 3, "CCC")
        _add_symbol(db, 4, "DDD")
        db.add(Indicator(symbol_id=1, date=d, rs_value=None))
        db.add(Indicator(symbol_id=2, date=d, rs_value=2.0))
        db.add(Indicator(symbol_id=3, date=d, rs_value=3.0))
        db.add(Indicator(symbol_id=4, date=d, rs_value=4.0))
        db.commit()

        sync_phase_t4_ranks(db, d, logging.getLogger("test"), default_start_date=d)

        ranks = {r.symbol_id: r.rs_value_rank
                 for r in db.query(RelativeRank).filter_by(date=d).all()}

        assert ranks[1] is None, "NULL 銘柄のランクが NULL になっていない"
        assert ranks[2] == pytest.approx(0.0)
        assert ranks[3] == pytest.approx(0.5)
        assert ranks[4] == pytest.approx(1.0)
    finally:
        db.close()
        engine.dispose()


def test_all_null_indicators_get_null_rank():
    """カテゴリ内の全銘柄が NULL のとき、全員のランクが NULL になる。

    現行実装（PERCENT_RANK が NULL を最小値=同順位として扱う）では
    全員 0.0 になるため、このテストは意図的に red になる。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        d = date(2026, 7, 2)
        _add_symbol(db, 1, "AAA")
        _add_symbol(db, 2, "BBB")
        db.add(Indicator(symbol_id=1, date=d, rs_value=None))
        db.add(Indicator(symbol_id=2, date=d, rs_value=None))
        db.commit()

        sync_phase_t4_ranks(db, d, logging.getLogger("test"), default_start_date=d)

        ranks = {r.symbol_id: r.rs_value_rank
                 for r in db.query(RelativeRank).filter_by(date=d).all()}

        assert ranks[1] is None
        assert ranks[2] is None
    finally:
        db.close()
        engine.dispose()


def test_non_null_ranking_is_unaffected_when_no_null_present():
    """NULL を含まない場合は従来どおりのランクになる（影響を受けないケースの固定）。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        d = date(2026, 7, 2)
        _add_symbol(db, 1, "AAA")
        _add_symbol(db, 2, "BBB")
        _add_symbol(db, 3, "CCC")
        db.add(Indicator(symbol_id=1, date=d, rs_value=1.0))
        db.add(Indicator(symbol_id=2, date=d, rs_value=2.0))
        db.add(Indicator(symbol_id=3, date=d, rs_value=3.0))
        db.commit()

        sync_phase_t4_ranks(db, d, logging.getLogger("test"), default_start_date=d)

        ranks = {r.symbol_id: r.rs_value_rank
                 for r in db.query(RelativeRank).filter_by(date=d).all()}

        assert ranks[1] == pytest.approx(0.0)
        assert ranks[2] == pytest.approx(0.5)
        assert ranks[3] == pytest.approx(1.0)
    finally:
        db.close()
        engine.dispose()
