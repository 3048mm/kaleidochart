"""T5（market_signals）の遡り不足ガードのテスト（計画書 §3.4・§4-1・§7-6(1)・5-7b）。

SQLite はホット期間（直近730日程度）しか SPY の価格を保持しないため、
`--rebuild-from T2 --category` / `--re-calculate` のように `market_signals` を
全削除する経路で全日付を再計算すると、窓の先頭（sma_200 の遡り）が足りず
MTS の SPY 由来列が誤った値になる（詳細は計画書 §1.1・§1.2）。

**判定基準（§7-6(1) で変更）**: gap_dates の最古日付で SPY の遡りが
`SPY_LOOKBACK_MIN_BARS`（220本）に満たない場合、即座に例外にはしない。
SQLite の SPY 起点と Parquet マスタの SPY 起点を比較し、

- **切り詰められている**（SQLite 起点 > Parquet 起点。Parquet には使えるはずの
  履歴があるのに SQLite に無い）→ 例外で止める
- **一致している**（切り詰めではなく、そもそも Parquet にもこれ以上の履歴が無い）
  → 通す（先頭の遡り不足日は 5-9 により NaN になるため、偽の値は書かれない）
- **Parquet が読めない** → 黙って通さず、従来の本数判定にフォールバックする

このため、`pipeline.phases.t5_signals._get_parquet_spy_min_date` を
monkeypatch して、各シナリオの Parquet 起点を明示的に固定する。
"""

import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, MarketSignal
from indicators.market_signals import SPY_LOOKBACK_MIN_BARS
from pipeline.phases import t5_signals
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


class TestFullRebuildLikeGap:
    """全期間再構築相当: SQLite の SPY が Parquet と同じ起点から入っている
    （`--re-calculate` が空の作業用DBへ全履歴を再取得した直後の状態に相当）。
    gap_dates は全日付。止まらず、先頭行は NaN で書かれる。
    """

    def test_does_not_raise_and_head_row_is_nan(self, db_session, monkeypatch):
        n_bars = SPY_LOOKBACK_MIN_BARS + 30
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=n_bars, start_date=start_date)
        # MarketSignal を1件も作らないので全日付が gap_dates になる。
        # SQLite 起点 = Parquet 起点（同じ start_date）に固定する。
        monkeypatch.setattr(t5_signals, "_get_parquet_spy_min_date", lambda: start_date)

        sync_phase_t5_signals(db_session, logging.getLogger("test"))

        head_signal = db_session.query(MarketSignal).filter(MarketSignal.date == dates[0]).first()
        assert head_signal is not None
        assert head_signal.spy_above_sma200 is None
        assert head_signal.market_phase is None

        tail_signal = db_session.query(MarketSignal).filter(MarketSignal.date == dates[-1]).first()
        assert tail_signal is not None
        assert tail_signal.market_trend_score is not None


class TestTruncatedWindow:
    """切り詰めた窓: SQLite の SPY 起点が Parquet マスタの SPY 起点より後ろ
    （Parquet には使えるはずの履歴があるのに SQLite に無い）。例外で止まる。
    """

    def test_raises_with_both_origins_and_remediation_in_message(self, db_session, monkeypatch):
        start_date = date(2026, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=100, start_date=start_date)
        parquet_origin = date(2010, 4, 1)
        monkeypatch.setattr(t5_signals, "_get_parquet_spy_min_date", lambda: parquet_origin)

        with pytest.raises(RuntimeError) as exc_info:
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        msg = str(exc_info.value)
        assert str(start_date) in msg  # SQLite 起点
        assert str(parquet_origin) in msg  # Parquet 起点
        assert "--rebuild-from T5" in msg  # 対処方法

        # 止まった以上、先頭日の MarketSignal は書き込まれていない
        assert db_session.query(MarketSignal).filter(MarketSignal.date == dates[0]).first() is None


class TestDailyUpdateLikeGap:
    """日次相当: gap は最新日のみで、SPY の遡りは十分ある。
    （SQLite 起点が Parquet 起点より後ろでも、本数が足りているためガードに
    到達しない。）
    """

    def test_does_not_raise(self, db_session, monkeypatch):
        start_date = date(2024, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=503, start_date=start_date)
        target_date = dates[-1]
        _mark_completed(db_session, dates[:-1])
        # Parquet 起点が大きく異なっていても、本数が十分なため比較に到達しない
        # ことを確認する意図で、あえて古い日付を設定する。
        monkeypatch.setattr(t5_signals, "_get_parquet_spy_min_date", lambda: date(2010, 4, 1))

        sync_phase_t5_signals(db_session, logging.getLogger("test"))

        signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
        assert signal is not None
        assert signal.market_trend_score is not None


class TestHotWindowNullScoreRepair:
    """ホット期間の古い日付に market_trend_score が NULL の行が1件だけある、
    正常な修復ケース（SQLite 起点 = Parquet 起点）。**これが指摘の核心**:
    旧判定（本数のみ）ではここで例外になり、rotate に到達できなかった。
    """

    def test_does_not_raise_when_origin_matches_parquet(self, db_session, monkeypatch):
        start_date = date(2026, 1, 1)
        n_bars = SPY_LOOKBACK_MIN_BARS - 1  # 遡り本数はガードの閾値未満
        dates = _seed_spy_history(db_session, n_bars=n_bars, start_date=start_date)
        target_date = dates[-1]
        _mark_completed(db_session, dates[:-1])
        # SQLite の SPY 起点と Parquet の SPY 起点が一致 → 切り詰めではない
        monkeypatch.setattr(t5_signals, "_get_parquet_spy_min_date", lambda: start_date)

        sync_phase_t5_signals(db_session, logging.getLogger("test"))

        signal = db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first()
        assert signal is not None
        assert signal.market_trend_score is not None


class TestParquetUnreadableFallback:
    """Parquet が読めない場合は黙って通さず、従来の本数判定にフォールバックする。"""

    def test_falls_back_to_bar_count_check_and_raises(self, db_session, monkeypatch):
        start_date = date(2026, 1, 1)
        dates = _seed_spy_history(db_session, n_bars=100, start_date=start_date)
        monkeypatch.setattr(t5_signals, "_get_parquet_spy_min_date", lambda: None)

        with pytest.raises(RuntimeError) as exc_info:
            sync_phase_t5_signals(db_session, logging.getLogger("test"))

        msg = str(exc_info.value)
        assert str(dates[0]) in msg
        assert str(SPY_LOOKBACK_MIN_BARS) in msg
        assert "--rebuild-from T5" in msg
        assert db_session.query(MarketSignal).filter(MarketSignal.date == dates[0]).first() is None
