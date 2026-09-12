"""T5 の書き込み経路（`sync_phase_t5_signals`）が NaN 行で落ちないことのテスト。

背景（`doc/in_progress/t5_parquet_rebuild_plan.md` §4-4・5-9）:
`spy_above_sma200` / `distribution_days` は SPY の遡りが足りない先頭区間で
None（判定不能）になりうる（`indicators/market_signals.py`）。書き込み側
（`sync_phase_t5_signals`）が `int(row['spy_above_sma200'])` のように無条件で
`int()` していると、None を渡した瞬間に `TypeError` で落ちる。

本番の SPY_LOOKBACK_MIN_BARS ガード（§3.4）は、ガードを通過した時点で
sma_200 の遡りが必ず足りている設計のため、実運用でこの経路が NaN を
書き込むことは無い。ここでは `SPY_LOOKBACK_MIN_BARS` を意図的に下げて、
ガードを通過しつつ実際の遡りが不足する状況を作り、書き込み側の
NaN 耐性そのものを検証する。
"""
import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, MarketSignal
import pipeline.phases.t5_signals as t5_signals


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    spy = Symbol(ticker="SPY", exchange="NYSE", category="ETF", active=1)
    session.add(spy)
    session.commit()

    return session


def test_write_path_does_not_raise_when_signals_contain_none(db_session, monkeypatch):
    """ガードは通過するが sma_200 の実遡りが足りない場合でも例外にならず、
    spy_above_sma200 / distribution_days に NULL を書き込むこと。"""
    # ガードの閾値を下げ、10本の SPY 履歴（sma_200 の 200本には遠く及ばない）
    # でも通過させる。
    monkeypatch.setattr(t5_signals, "SPY_LOOKBACK_MIN_BARS", 5)

    spy = db_session.query(Symbol).filter(Symbol.ticker == "SPY").first()
    start_date = date(2026, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(10)]
    for d in dates:
        db_session.add(DailyPrice(
            symbol_id=spy.id, date=d, open=100.0, high=101.0, low=99.0,
            close=100.0, volume=1000,
        ))
        db_session.add(Indicator(symbol_id=spy.id, date=d, sma_200=90.0, sma_50=95.0, ema_21=98.0))
    # 最古日付だけを gap にすると、閾値ガードが「そこまでの本数=1」で
    # 弾いてしまう（ガードの根拠は§3.4「最古の gap 日付が最も遡りが浅い」で
    # 変わらない）。ガードを通過させつつ実遡り不足を作るため、最終日以外は
    # 「計算済み」のダミー MarketSignal で埋め、gap を最終日1件だけにする
    # （`test_t5_lookback_guard.py` の `_mark_completed` と同じ手法）。
    for d in dates[:-1]:
        db_session.add(MarketSignal(
            date=d, spy_above_sma200=1, distribution_days=0, follow_through_day=0,
            market_phase="BULL", market_trend_score=50.0,
        ))
    db_session.commit()

    # 例外が出ないこと自体がアサーション
    t5_signals.sync_phase_t5_signals(db_session, logging.getLogger("test"))

    target_date = dates[-1]
    signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
    assert signal is not None
    # 10本しかないため sma_200（200本必要）は全行 NaN -> None として書き込まれる
    assert signal.spy_above_sma200 is None
    assert signal.distribution_days is None
    assert signal.market_phase is None
