import json
import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base, Symbol, DailyPrice, Indicator, MarketSignal
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


# ---------------------------------------------------------------
# カウンタートレンドライン（構造が成立していない期間に引かれる）
# ---------------------------------------------------------------

#: 高値が切り下がり、安値も切り下がり続ける＝ LL-HL 構造が成立しない系列。
#: high と low を独立に置く必要があるため（`_seed_structure_prices` は high = low + 3 で
#: 連動してしまい、ピボット高値が strength >= 2 で立たない）専用のシーダーを使う。
#: 形は `test_counter_trend.py` の `downtrend` fixture と同じ。


def _seed_downtrend_ohlc(session, n=60, peaks=((5, 100.0), (15, 90.0), (25, 80.0)),
                         symbol_id=1, ticker="AAPL"):
    """下降トレンド（高値が切り下がる）を high/low 独立に作って投入する。"""
    import numpy as np

    high = np.full(n, 60.0)
    for idx, peak in peaks:
        high[idx] = peak
        high[idx - 1] = peak - 8
        high[idx + 1] = peak - 8
    i = np.arange(n, dtype=float)
    # 安値は切り下げつつジグザグに。単調だとピボット安値が出来ず limit_idx が立たない
    low = 58 - 0.45 * i + 3.0 * np.sin(i * 0.9)
    close = high - 1.0

    session.add(Symbol(id=symbol_id, ticker=ticker, name=ticker, category="個別", active=1))
    for k in range(n):
        session.add(DailyPrice(
            symbol_id=symbol_id, date=_SP_BASE_DATE + timedelta(days=k),
            open=float(close[k]), high=float(high[k]), low=float(low[k]),
            close=float(close[k]), volume=1000,
        ))
    session.commit()
    return session


def test_structure_pivot_response_always_has_counter_keys(seed_structure_data):
    """既存レスポンスにキーが増えただけで、構造側の形は変わらない。"""
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data)

    assert "counters" in resp and isinstance(resp["counters"], list)
    assert "current_counter" in resp
    assert {"metadata", "structures", "current"} <= set(resp)


def test_counter_lines_are_drawn_when_no_structure(db_session):
    """構造が立たない下降系列ではカウンター線が引かれる。"""
    resp = build_structure_pivot_response(symbol_id=1, db=_seed_downtrend_ohlc(db_session))

    assert resp["counters"], "カウンター線が1本も返っていない"
    for c in resp["counters"]:
        assert c["a1_date"] < c["a2_date"] < c["start_date"] <= c["end_date"]
        assert c["slope"] <= 0.0


def test_counter_line_endpoints_are_on_the_line(db_session):
    """start_value / end_value が傾きと整合する（フロントに再計算させないための値）。"""
    resp = build_structure_pivot_response(symbol_id=1, db=_seed_downtrend_ohlc(db_session))

    assert resp["counters"]
    for c in resp["counters"]:
        assert c["a1_value"] == pytest.approx(c["a1_price"])
        assert c["end_value"] <= c["start_value"] + 1e-9, "下向きの線になっていない"


def test_current_counter_matches_the_flagged_entry(db_session):
    resp = build_structure_pivot_response(symbol_id=1, db=_seed_downtrend_ohlc(db_session))

    flagged = [c for c in resp["counters"] if c["is_current"]]
    assert len(flagged) <= 1
    assert resp["current_counter"] == (flagged[0] if flagged else None)


def test_counter_and_structure_are_exclusive(seed_structure_data):
    """構造が生きている間はカウンター線が引かれない（sp_pivot と排他）。"""
    resp = build_structure_pivot_response(symbol_id=1, db=seed_structure_data)

    if resp["current"] is not None:
        assert resp["current_counter"] is None


def test_counter_history_is_capped(db_session):
    """履歴は COUNTER_HISTORY_LIMIT 本までに切る（ペイロード肥大の防止）。"""
    from api.chart_router import COUNTER_HISTORY_LIMIT

    n = 400
    peaks = tuple((5 + 10 * k, 200.0 - 3.0 * k) for k in range((n - 12) // 10))
    resp = build_structure_pivot_response(
        symbol_id=1, db=_seed_downtrend_ohlc(db_session, n=n, peaks=peaks))

    assert len(resp["counters"]) <= COUNTER_HISTORY_LIMIT


def test_counter_empty_on_insufficient_data(db_session):
    resp = build_structure_pivot_response(
        symbol_id=1, db=_seed_structure_prices(db_session, [100, 99, 98]))

    assert resp["counters"] == []
    assert resp["current_counter"] is None


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


# ============================================================
# GET /chart/{symbol_id}/zone_break
# ============================================================

from api.chart_router import build_zone_break_response


#: `backend/tests/indicators/test_zone_break.py::test_フリップ反転` と同じ系列
#: （検証済みの期待値: bar14でフリップ確定、is_bull=False、zb_ssl=5、zb_bsl=15）。
#: high/low/close を個別に持つ必要があるため専用のシーダーを使う。
_ZB_FLIP_HIGH = [10, 11, 10, 12, 11, 13, 12, 15, 13, 9, 8, 10, 10, 9, 8]
_ZB_FLIP_LOW = [9, 10, 9, 10, 10, 11, 11, 12, 11, 7, 6, 8, 7, 5, 6]
_ZB_FLIP_CLOSE = [9.5, 10.5, 9.5, 11, 10.5, 12, 11.5, 14, 12, 8, 7, 9, 8, 7, 6.5]


def _seed_zone_break_ohlc(session, high, low, close, symbol_id=1, ticker="AAPL"):
    session.add(Symbol(id=symbol_id, ticker=ticker, name=ticker, category="個別", active=1))
    for k in range(len(high)):
        session.add(DailyPrice(
            symbol_id=symbol_id, date=_SP_BASE_DATE + timedelta(days=k),
            open=float(close[k]), high=float(high[k]), low=float(low[k]),
            close=float(close[k]), volume=1000,
        ))
    session.commit()
    return session


def test_zone_break_reports_current_direction_and_levels(db_session):
    """フリップ確定後の状態（is_bull=False, zb_ssl=5, zb_bsl=15）がそのまま返ること。"""
    resp = build_zone_break_response(
        symbol_id=1,
        db=_seed_zone_break_ohlc(db_session, _ZB_FLIP_HIGH, _ZB_FLIP_LOW, _ZB_FLIP_CLOSE),
    )

    assert resp["current_direction"] == "bear"
    assert resp["metadata"]["ticker"] == "AAPL"
    assert resp["metadata"]["bars"] == len(_ZB_FLIP_HIGH)

    current_ssl = [lv for lv in resp["ssl_levels"] if lv["is_current"]]
    current_bsl = [lv for lv in resp["bsl_levels"] if lv["is_current"]]
    assert len(current_ssl) == 1 and current_ssl[0]["price"] == pytest.approx(5.0)
    assert len(current_bsl) == 1 and current_bsl[0]["price"] == pytest.approx(15.0)


def test_zone_break_unknown_symbol_returns_404(db_session):
    with pytest.raises(HTTPException) as exc:
        build_zone_break_response(symbol_id=999, db=db_session)

    assert exc.value.status_code == 404


def test_zone_break_insufficient_data_returns_empty(db_session):
    """データ不足はエラーではなく空で返す（構造ピボットと同じ方針）。"""
    resp = build_zone_break_response(
        symbol_id=1,
        db=_seed_zone_break_ohlc(db_session, [100, 99], [98, 97], [99, 98]),
    )

    assert resp["ssl_levels"] == []
    assert resp["bsl_levels"] == []
    assert resp["fvg_boxes"] == []
    # is_bull の初期値は True（未確定のままフラットに終わるため）
    assert resp["current_direction"] == "bull"


def test_zone_break_response_shape(db_session):
    """レスポンスの必須キーが揃っていること。"""
    resp = build_zone_break_response(
        symbol_id=1,
        db=_seed_zone_break_ohlc(db_session, _ZB_FLIP_HIGH, _ZB_FLIP_LOW, _ZB_FLIP_CLOSE),
    )

    assert {"metadata", "ssl_levels", "bsl_levels", "fvg_boxes", "current_direction"} <= set(resp)
    for lv in resp["ssl_levels"] + resp["bsl_levels"]:
        assert {"kind", "price", "start_date", "end_date", "is_current"} <= set(lv)
    for box in resp["fvg_boxes"]:
        assert {"kind", "left_date", "right_date", "top", "bottom",
                "invalidated", "is_current"} <= set(box)


# ============================================================
# GET /chart/99998 (Market Trend Score) — min_periods=window 化（5-9）
# ============================================================

_MTS_DAY_COUNT = 25


def _seed_market_signals(session, n=_MTS_DAY_COUNT):
    """MarketSignal を n 日分投入する（market_trend_score は単調増加）。"""
    base_date = date(2026, 4, 1)
    for i in range(n):
        session.add(MarketSignal(
            date=base_date + timedelta(days=i),
            market_trend_score=50.0 + i,
        ))
    session.commit()
    return session


@pytest.fixture
def seed_market_signal_data(db_session):
    return _seed_market_signals(db_session)


def test_chart_mts_sma21_is_null_before_warmup(seed_market_signal_data):
    """sma_21 は遡り21本に満たない先頭20件（0-indexed 0..19）で None であること。

    `min_periods=1` の旧実装は「ある分だけの平均」を返し、窓に満たない期間にも
    それらしい値を出してしまっていた（min_periods_warmup_plan.md §3.4）。
    """
    resp = get_chart_data(symbol_id=99998, db=seed_market_signal_data)
    data = json.loads(resp.body)["data"]

    assert len(data) == _MTS_DAY_COUNT
    for i in range(20):
        assert data[i]["sma_21"] is None, f"position {i} の sma_21 が窓未満なのに値を持っている"


def test_chart_mts_sma21_is_filled_from_warmup_boundary(seed_market_signal_data):
    """21本目（0-indexed position 20）から sma_21 が非NULLになること。"""
    resp = get_chart_data(symbol_id=99998, db=seed_market_signal_data)
    data = json.loads(resp.body)["data"]

    assert data[20]["sma_21"] is not None
    assert data[-1]["sma_21"] is not None
