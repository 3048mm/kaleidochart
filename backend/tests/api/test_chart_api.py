import json
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base, Symbol, DailyPrice, Indicator
from api.routers import get_chart_data
from fastapi import HTTPException

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture
def seed_chart_data(db_session):
    sym = Symbol(id=1, ticker="AAPL", name="Apple", category="個別", active=1)
    db_session.add(sym)
    
    # Add 10 days of data
    base_date = date(2026, 4, 1)
    for i in range(10):
        d = base_date + timedelta(days=i)
        dp = DailyPrice(symbol_id=1, date=d, open=100+i, high=105+i, low=95+i, close=102+i, volume=1000)
        ind = Indicator(symbol_id=1, date=d, sma_21=100)
        db_session.add(dp)
        db_session.add(ind)
    
    db_session.commit()
    return db_session

def test_get_chart_data_no_pagination(seed_chart_data):
    db = seed_chart_data
    # Call direct
    resp = get_chart_data(symbol_id=1, db=db)
    
    # Parse custom json response
    data = json.loads(resp.body)
    assert len(data["data"]) == 10
    assert data["metadata"]["ticker"] == "AAPL"
    # Check order: ascending by date
    assert data["data"][0]["time"] == "2026-04-01"
    assert data["data"][-1]["time"] == "2026-04-10"


# ============================================================
# GET /chart/{symbol_id}/structure_pivot
# ============================================================

from api.chart_router import build_structure_pivot_response


#: LL(idx10) -> 戻り高値(idx15) -> HL(idx20) -> 上昇 という素直な系列。
#: test_structure_pivot.py の base_series と同じ形。
_SP_LOWS = [50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 40,
            42, 44, 46, 48, 50, 48, 46, 44, 43, 42,
            44, 46, 48, 50, 52, 54, 56, 58, 60, 62]
_SP_BASE_DATE = date(2026, 4, 1)


def _seed_structure_prices(session, lows, symbol_id=1, ticker="AAPL"):
    session.add(Symbol(id=symbol_id, ticker=ticker, name=ticker, category="個別", active=1))
    for i, lo in enumerate(lows):
        session.add(DailyPrice(
            symbol_id=symbol_id, date=_SP_BASE_DATE + timedelta(days=i),
            open=float(lo) + 1.0, high=float(lo) + 3.0, low=float(lo),
            close=float(lo) + 1.5, volume=1000,
        ))
    session.commit()
    return session


@pytest.fixture
def seed_structure_data(db_session):
    return _seed_structure_prices(db_session, _SP_LOWS)


def test_structure_pivot_detects_ll_hl(seed_structure_data):
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data)

    assert len(resp["structures"]) == 1
    s = resp["structures"][0]
    assert s["ll_date"] == "2026-04-11"      # idx10
    assert s["ll_price"] == pytest.approx(40.0)
    assert s["hl_date"] == "2026-04-21"      # idx20
    assert s["hl_price"] == pytest.approx(42.0)
    assert s["pivot_date"] == "2026-04-16"   # idx15
    assert s["pivot_price"] == pytest.approx(53.0)


def test_structure_pivot_confirmation_date_respects_lag(seed_structure_data):
    """確定日は HL の日付ではなく、L 本先の確定バーの日付であること（先読み防止）。"""
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data)
    s = resp["structures"][0]

    assert s["length"] == 2
    assert s["confirmed_date"] == "2026-04-23"   # idx20 + 2 = idx22
    assert s["confirmed_date"] > s["hl_date"]


def test_structure_pivot_reports_current_structure(seed_structure_data):
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data)

    assert resp["current"] is not None
    assert resp["current"]["is_current"] is True
    assert resp["current"]["end_date"] == "2026-05-01"   # idx30
    assert resp["metadata"]["ticker"] == "AAPL"


def test_structure_pivot_invalidated_structure_kept_as_history(db_session):
    """HL 割れで死んだ構造は履歴として残るが、current にはならない。"""
    lows = list(_SP_LOWS)
    lows[26] = 35            # HL(42) を割り込む
    resp = build_structure_pivot_response(symbol_id=1, db=_seed_structure_prices(db_session, lows))

    assert len(resp["structures"]) == 1
    assert resp["structures"][0]["invalidated"] is True
    assert resp["structures"][0]["end_date"] == "2026-04-26"   # idx25
    assert resp["current"] is None


def test_structure_pivot_length_range_is_honored(seed_structure_data):
    """HL の強度は 8 なので min_len=9 では検出されない。"""
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data, min_len=9, max_len=10)

    assert resp["structures"] == []
    assert resp["current"] is None


def test_structure_pivot_insufficient_data_returns_empty(db_session):
    """データ不足はエラーではなく空で返す（チャートの一部なので落とさない）。"""
    resp = build_structure_pivot_response(symbol_id=1, db=_seed_structure_prices(db_session, [100, 99, 98]))

    assert resp["structures"] == []
    assert resp["current"] is None


def test_structure_pivot_unknown_symbol_returns_404(db_session):
    with pytest.raises(HTTPException) as exc:
        build_structure_pivot_response(symbol_id=999, db=db_session)

    assert exc.value.status_code == 404


def test_chart_data_exposes_structure_pivot_columns(seed_chart_data):
    """/chart のレスポンスに sp_pivot / sp_hl / sp_counter が含まれること。

    DATA VIEW の Pivot / Pivot HL / Counter 列はここから値を読む。T3 に列を足し、
    フロント側に表示を足しても、**この供給経路を配線し忘れると列は常に空になる**
    （2026-08-26 に sp_pivot で実際に発生。本番昇格後の動作確認で発覚した）。

    2026-09-01: `sp_counter` が同じ穴に落ちていた。DB・Parquet・screener_filters には
    入っていたのに chart API が返しておらず、チャートで検証できない状態だった。
    """
    resp = get_chart_data(symbol_id=1, db=seed_chart_data)
    data = json.loads(resp.body)["data"]

    assert data, "チャートデータが空"
    assert "sp_pivot" in data[-1], "chart API が sp_pivot を返していない"
    assert "sp_hl" in data[-1], "chart API が sp_hl を返していない"
    assert "sp_counter" in data[-1], "chart API が sp_counter を返していない"


def test_chart_data_exposes_rs_dot_age_columns(seed_chart_data):
    """/chart のレスポンスに rs_blue_dot_age / rs_red_dot_age が含まれること。

    DATA VIEW の Blue / Red 列とチャート上の ◆ マーカーがここから値を読む。
    経過日数（0=当日点灯 / n=n営業日前 / 999=未点灯）なので、**0 を falsy として
    扱う実装にすると点灯日が消える**。整数のまま返っていることを確かめる。
    """
    resp = get_chart_data(symbol_id=1, db=seed_chart_data)
    data = json.loads(resp.body)["data"]

    assert data, "チャートデータが空"
    assert "rs_blue_dot_age" in data[-1], "chart API が rs_blue_dot_age を返していない"
    assert "rs_red_dot_age" in data[-1], "chart API が rs_red_dot_age を返していない"
    # 旧フラグ名が復活していないこと（0/1 と経過日数は意味が反転する）
    assert "is_rs_blue_dot" not in data[-1], "旧フラグ is_rs_blue_dot が復活している"
