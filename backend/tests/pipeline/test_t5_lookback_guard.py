"""T5（market_signals）の遡り不足ガードのテスト（計画書 §3.4・§4-1・§7-7・§4-8・5-7c）。

SQLite はホット期間（直近730日程度）しか SPY の価格を保持しないため、
`--rebuild-from T2 --category` / `--re-calculate` のように `market_signals` を
全削除する経路で全日付を再計算すると、窓の先頭（sma_200 の遡り）が足りず
MTS の SPY 由来列が誤った値になる（詳細は計画書 §1.1・§1.2）。

**方針（§7-7・§4-8・5-7c で確定。例外で止める旧方式から変更）**:
gap_dates のうち、その日までの SPY 遡りが `SPY_LOOKBACK_MIN_BARS`（220本）に
満たない日付は**例外にせず、書き込み対象から外す**。除外が発生したら
`logger.error` で「除外した日付数・範囲・対処方法」を警告する。除外後に
残った日付は通常どおり書き込まれる。全て除外された場合は書き込まずに
`logger.error` を出して早期 return する。

旧方式（本数不足を検知したときだけ Parquet マスタの SPY 起点と比較し、
切り詰めなら例外にする）は、**ホット期間の古い日付に `market_trend_score`
NULL が1件あるだけの正常な修復ケース**（SKILL.md が「再実行でバックフィルされる」
と案内している挙動）でも必ず発火し、日次更新が毎晩落ちて rotate に到達せず
Parquet の更新が止まる欠陥があった（§7-7）。新方式は Parquet を一切参照しない
ため、テストで Parquet 関連の monkeypatch は不要になった。
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


class TestDailyUpdateLikeGap:
    """日次相当: gap は最新日のみで、SPY の遡りは十分ある。警告なしで従来どおり書き込まれる。"""

    def test_does_not_raise_and_writes_without_warning(self, db_session, caplog):
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=503, start_date=start_date)
        target_date = dates[-1]
        _mark_completed(db_session, dates[:-1])

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
        assert signal is not None
        assert signal.market_trend_score is not None
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)


class TestHotWindowNullScoreRepair:
    """ホット期間の古い日付に market_trend_score が NULL の行が1件だけある、
    正常な修復ケース（SQLite 起点は Parquet 起点より後ろ＝実運用と同じ状況）。
    **これが指摘の核心**（§7-7）: 旧方式ではここで例外になり rotate に到達
    できなかった。新方式は例外を投げず、その日付だけ書き込み対象から外す。
    """

    def test_excludes_only_the_unrepairable_date(self, db_session, caplog):
        start_date = date(2024, 1, 1)
        # ホット期間相当（約730日）。先頭日だけ NULL スコアの修復対象にする。
        dates = _seed_spy_history(db_session, n_bars=SPY_LOOKBACK_MIN_BARS + 50, start_date=start_date)
        repair_date = dates[0]
        # repair_date 以外はすべて完了済みにする（NULL 修復ケースを再現）。
        _mark_completed(db_session, dates[1:])

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        # 例外を投げない
        # 遡り不足の repair_date は書き込まれない
        assert db_session.query(MarketSignal).filter(MarketSignal.date == repair_date).first() is None

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "遡り不足の除外は logger.error で警告されるはず"
        combined = " ".join(r.getMessage() for r in error_records)
        assert "1 件" in combined  # 除外した日付数
        assert str(repair_date) in combined  # 除外した日付範囲（最古〜最新）
        assert "--rebuild-from T5" in combined  # 対処方法


class TestFullRebuildLikeGap:
    """全期間再構築相当: gap が系列の起点から全日付。例外なし。
    先頭の遡り不足日は書き込まれず、220本目以降の日付は書き込まれる。
    """

    def test_excludes_head_and_writes_tail(self, db_session, caplog):
        n_bars = SPY_LOOKBACK_MIN_BARS + 30
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=n_bars, start_date=start_date)
        # MarketSignal を1件も作らないので全日付が gap_dates になる。

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        # 先頭219本（遡り不足）は書き込まれない
        for d in dates[:SPY_LOOKBACK_MIN_BARS - 1]:
            assert db_session.query(MarketSignal).filter(MarketSignal.date == d).first() is None

        # 220本目以降は書き込まれる
        tail_signal = db_session.query(MarketSignal).filter(MarketSignal.date == dates[-1]).first()
        assert tail_signal is not None
        assert tail_signal.market_trend_score is not None

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        combined = " ".join(r.getMessage() for r in error_records)
        assert str(SPY_LOOKBACK_MIN_BARS - 1) in combined  # 除外した日付数


class TestAllGapDatesInsufficientLookback:
    """すべての gap 日付が遡り不足（例: SPY が10本しかない）。
    例外なし・1行も書き込まれない・警告が出る。
    """

    def test_writes_nothing_and_warns(self, db_session, caplog):
        start_date = date(2026, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=10, start_date=start_date)

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        assert db_session.query(MarketSignal).count() == 0

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records


class TestLookbackBoundary:
    """境界値: 遡りちょうど220本の日付は書き込まれる、219本の日付は除外される。"""

    def test_boundary_220_written_219_excluded(self, db_session, caplog):
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=SPY_LOOKBACK_MIN_BARS, start_date=start_date)
        boundary_219 = dates[SPY_LOOKBACK_MIN_BARS - 2]  # 遡り219本
        boundary_220 = dates[SPY_LOOKBACK_MIN_BARS - 1]  # 遡り220本

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        assert db_session.query(MarketSignal).filter(MarketSignal.date == boundary_219).first() is None
        signal_220 = db_session.query(MarketSignal).filter(MarketSignal.date == boundary_220).first()
        assert signal_220 is not None
