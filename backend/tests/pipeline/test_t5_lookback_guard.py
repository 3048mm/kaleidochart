"""T5（market_signals）の遡り不足ガードのテスト（計画書 §3.4・§4-1・§7-7・§4-8・§7-8(1)・5-7d）。

SQLite はホット期間（直近730日程度）しか SPY の価格を保持しないため、
`--rebuild-from T2 --category` / `--re-calculate` のように `market_signals` を
全削除する経路で全日付を再計算すると、窓の先頭（sma_200 の遡り）が足りず
MTS の SPY 由来列が誤った値になる（詳細は計画書 §1.1・§1.2）。

**方針（§7-8(1)・5-7d で確定。5-7c までの「除外＋警告」から変更）**:
gap_dates のうち、その日までの SPY 遡りが `SPY_LOOKBACK_MIN_BARS`（220本）に
満たない日付も**例外にせず、書き込み対象から外さない**。他の gap 日付と同様に
行として書き込むが、`spy_above_sma200` / `distribution_days` / `market_phase` /
`market_trend_score` は 5-9 の `calculate_market_signals()` 側の対応により
None（NaN）＝ NULL として保存される（判定不能）。遡り不足の日付があれば
`logger.error` で「件数・範囲・対処方法」を警告する（除外はしない）。

5-7c までの「除外」方式は、Parquet 経路（`recompute_parquet_signals.py`）が
NaN 行を必ず書くのと食い違い、`/available_dates`（MarketSignal テーブルから
日付一覧を作る）から該当日付が恒久的に消えてしまう欠陥があった（§7-8(1)）。
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
    **これが指摘の核心**（§7-7・§7-8(1)）: 例外を投げず、その日付も行として
    書き込まれる（各列は NULL＝判定不能）。
    """

    def test_writes_the_date_as_null_row(self, db_session, caplog):
        start_date = date(2024, 1, 1)
        # ホット期間相当（約730日）。先頭日だけ NULL スコアの修復対象にする。
        dates = _seed_spy_history(db_session, n_bars=SPY_LOOKBACK_MIN_BARS + 50, start_date=start_date)
        repair_date = dates[0]
        # repair_date 以外はすべて完了済みにする（NULL 修復ケースを再現）。
        _mark_completed(db_session, dates[1:])

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        # 例外を投げない。遡り不足の repair_date も行として書き込まれる。
        signal = db_session.query(MarketSignal).filter(MarketSignal.date == repair_date).first()
        assert signal is not None
        assert signal.spy_above_sma200 is None
        assert signal.distribution_days is None
        assert signal.market_phase is None
        assert signal.market_trend_score is None

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "遡り不足の日付は logger.error で警告されるはず"
        combined = " ".join(r.getMessage() for r in error_records)
        assert "1 件" in combined  # 件数
        assert str(repair_date) in combined  # 範囲（最古〜最新）
        assert "--rebuild-from T5" in combined  # 対処方法
        assert "除外" not in combined  # 「除外します」ではなく「NULL として書き込みます」の文面


class TestFullRebuildLikeGap:
    """全期間再構築相当: gap が系列の起点から全日付。例外なし。
    先頭199本（`sma_200` の窓 `min_periods=200` に届かない）は行として
    書き込まれるが値は NULL、200本目以降は通常どおり値が入って書き込まれる。
    警告対象（遡り不足として `logger.error` に数えられる件数）は
    `SPY_LOOKBACK_MIN_BARS`（220）基準のため、NULL になる本数（199）とは
    別（安全マージンぶん多い）。
    """

    def test_writes_head_as_null_and_tail_with_values(self, db_session, caplog):
        n_bars = SPY_LOOKBACK_MIN_BARS + 30
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=n_bars, start_date=start_date)
        # MarketSignal を1件も作らないので全日付が gap_dates になる。

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        # 先頭199本（sma_200 の窓に届かない）も行として書き込まれるが、値は NULL
        for d in dates[:199]:
            signal = db_session.query(MarketSignal).filter(MarketSignal.date == d).first()
            assert signal is not None
            assert signal.market_trend_score is None

        # 200本目以降は行として書き込まれ、値が入る
        for d in dates[199:]:
            signal = db_session.query(MarketSignal).filter(MarketSignal.date == d).first()
            assert signal is not None
            assert signal.market_trend_score is not None

        # 全 gap_dates が行として書き込まれている（消失なし）
        assert db_session.query(MarketSignal).count() == n_bars

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        combined = " ".join(r.getMessage() for r in error_records)
        assert str(SPY_LOOKBACK_MIN_BARS - 1) in combined  # 遡り不足（警告対象）の件数


class TestAllGapDatesInsufficientLookback:
    """すべての gap 日付が遡り不足（例: SPY が10本しかない）。
    例外なし・全日付が NULL 行として書き込まれる・警告が出る。
    """

    def test_writes_all_as_null_rows_and_warns(self, db_session, caplog):
        start_date = date(2026, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=10, start_date=start_date)

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        assert db_session.query(MarketSignal).count() == len(dates)
        for d in dates:
            signal = db_session.query(MarketSignal).filter(MarketSignal.date == d).first()
            assert signal is not None
            assert signal.market_trend_score is None

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records


class TestLookbackBoundary:
    """境界値: 遡り220本ちょうどの日付は警告対象外、219本の日付は警告対象。
    どちらも行としては書き込まれる（`_find_insufficient_lookback_dates` の
    閾値 `SPY_LOOKBACK_MIN_BARS` に対する境界であり、NULL になるかどうかの
    境界＝ `sma_200` の `min_periods=200` とは別。ここでは書き込まれること・
    警告の有無を検証する）。
    """

    def test_boundary_220_no_warning_219_warns(self, db_session, caplog):
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=SPY_LOOKBACK_MIN_BARS, start_date=start_date)
        boundary_219 = dates[SPY_LOOKBACK_MIN_BARS - 2]  # 遡り219本
        boundary_220 = dates[SPY_LOOKBACK_MIN_BARS - 1]  # 遡り220本
        # gap_dates を境界の2日だけに絞る（他の日付は完了済みにする）。
        _mark_completed(db_session, dates[:SPY_LOOKBACK_MIN_BARS - 2])

        with caplog.at_level(logging.ERROR):
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        # どちらも行として書き込まれる（除外されない）
        signal_219 = db_session.query(MarketSignal).filter(MarketSignal.date == boundary_219).first()
        assert signal_219 is not None
        signal_220 = db_session.query(MarketSignal).filter(MarketSignal.date == boundary_220).first()
        assert signal_220 is not None

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        combined = " ".join(r.getMessage() for r in error_records)
        assert "1 件" in combined  # 219本の1日だけが警告対象（220本は対象外）
        assert str(boundary_219) in combined
        assert str(boundary_220) not in combined
