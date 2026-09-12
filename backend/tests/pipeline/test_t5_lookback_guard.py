"""T5（market_signals）の遡り不足ガードのテスト（計画書 §3.4・§4-1）。

SQLite はホット期間（直近730日程度）しか SPY の価格を保持しないため、
`--rebuild-from T2 --category` / `--re-calculate` のように `market_signals` を
全削除する経路で全日付を再計算すると、窓の先頭（sma_200 の遡り）が足りず
MTS の SPY 由来列が誤った値になる（詳細は計画書 §1.1・§1.2）。

このテストは、gap_dates の最古日付について SQLite の SPY が
`SPY_LOOKBACK_MIN_BARS`（220本）に満たない場合に例外で止まり、
足りている場合（デイリー相当）は従来どおり書き込まれることを担保する。
"""

import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, MarketSignal
from indicators.market_signals import SPY_LOOKBACK_MIN_BARS
from pipeline.phases.t5_signals import sync_phase_t5_signals


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    spy = Symbol(ticker="SPY", exchange="NYSE", category="ETF", active=1)
    vix = Symbol(ticker="^VIX", exchange="INDEX", category="INDEX", active=1)
    stock = Symbol(ticker="AAPL", exchange="NASDAQ", category="個別", active=1)
    session.add_all([spy, vix, stock])
    session.commit()

    return session


def _seed_spy_history(db_session, n_bars: int, start_date: date) -> list[date]:
    """SPY の DailyPrice/Indicator を `start_date` から連続 `n_bars` 日分投入する。"""
    spy = db_session.query(Symbol).filter(Symbol.ticker == "SPY").first()
    dates = [start_date + timedelta(days=i) for i in range(n_bars)]
    for d in dates:
        db_session.add(DailyPrice(
            symbol_id=spy.id, date=d, open=100.0, high=101.0, low=99.0,
            close=100.0, volume=1000
        ))
        db_session.add(Indicator(symbol_id=spy.id, date=d, sma_200=90.0, sma_50=95.0, ema_21=98.0))
    db_session.commit()
    return dates


def _mark_completed(db_session, dates: list[date]) -> None:
    """指定した日付をダミーの完了済み MarketSignal で埋める（gap_dates から外すため）。"""
    for d in dates:
        db_session.add(MarketSignal(
            date=d,
            spy_above_sma200=1,
            distribution_days=0,
            follow_through_day=0,
            market_phase="BULL",
            market_trend_score=50.0,
        ))
    db_session.commit()


def test_raises_when_lookback_insufficient(db_session):
    """gap_dates の最古日付で SPY が 220本に満たない場合、例外で止まり対処法を案内する。"""
    start_date = date(2026, 1, 1)
    dates = _seed_spy_history(db_session, n_bars=100, start_date=start_date)
    # MarketSignal を1件も作らないので t3_dates 全部（100日）が gap_dates になり、
    # 最古日付（1本目）では SPY の遡りが 1本しかない。

    with pytest.raises(RuntimeError) as exc_info:
        sync_phase_t5_signals(db_session, logging.getLogger("test"))

    msg = str(exc_info.value)
    assert str(dates[0]) in msg
    assert str(SPY_LOOKBACK_MIN_BARS) in msg
    assert "--rebuild-from T5" in msg

    # ガードで止まった以上、当該日の MarketSignal は書き込まれていない
    assert db_session.query(MarketSignal).filter(MarketSignal.date == dates[0]).first() is None


def test_boundary_exactly_min_bars_passes(db_session):
    """最古 gap 日付までの SPY がちょうど220本なら、ガードを通過して書き込まれる。"""
    start_date = date(2026, 1, 1)
    dates = _seed_spy_history(db_session, n_bars=SPY_LOOKBACK_MIN_BARS, start_date=start_date)
    # 最後の1日だけを gap として残す（それ以外は完了済み扱いにする）
    target_date = dates[-1]
    _mark_completed(db_session, dates[:-1])

    sync_phase_t5_signals(db_session, logging.getLogger("test"))

    signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
    assert signal is not None
    assert signal.market_trend_score is not None


def test_boundary_one_short_of_min_bars_raises(db_session):
    """最古 gap 日付までの SPY が219本（220本に1本足りない）なら例外で止まる。"""
    start_date = date(2026, 1, 1)
    n_bars = SPY_LOOKBACK_MIN_BARS - 1
    dates = _seed_spy_history(db_session, n_bars=n_bars, start_date=start_date)
    target_date = dates[-1]
    _mark_completed(db_session, dates[:-1])

    with pytest.raises(RuntimeError) as exc_info:
        sync_phase_t5_signals(db_session, logging.getLogger("test"))

    msg = str(exc_info.value)
    assert str(n_bars) in msg
    assert str(SPY_LOOKBACK_MIN_BARS) in msg

    assert db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first() is None


def test_daily_update_like_gap_does_not_raise(db_session):
    """デイリー更新相当（gap は最新日のみ・SPY は十分な本数）では止まらない。"""
    start_date = date(2024, 1, 1)
    dates = _seed_spy_history(db_session, n_bars=503, start_date=start_date)
    target_date = dates[-1]
    _mark_completed(db_session, dates[:-1])

    # 例外が出ないこと自体がアサーション
    sync_phase_t5_signals(db_session, logging.getLogger("test"))

    signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
    assert signal is not None
    assert signal.market_trend_score is not None
