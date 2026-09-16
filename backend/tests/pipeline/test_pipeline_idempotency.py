import pytest
import logging
from datetime import date, datetime, timedelta
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db.models import Base, Symbol, DailyPrice, Indicator, MarketSignal
from pipeline.phases.t5_signals import sync_phase_t5_signals

# In-memory SQLite for testing
@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Setup mock symbols
    spy = Symbol(ticker="SPY", exchange="NYSE", category="ETF", active=1)
    vix = Symbol(ticker="^VIX", exchange="INDEX", category="INDEX", active=1)
    stock = Symbol(ticker="AAPL", exchange="NASDAQ", category="個別", active=1)
    session.add_all([spy, vix, stock])
    session.commit()
    
    return session

def test_sync_phase_t5_backfills_null_score(db_session):
    """
    既存の MarketSignal レコードがあっても、market_trend_score が NULL ならば
    更新対象として認識され、計算されることを検証する (RED test)
    """
    spy = db_session.query(Symbol).filter(Symbol.ticker == "SPY").first()
    vix = db_session.query(Symbol).filter(Symbol.ticker == "^VIX").first()
    stock = db_session.query(Symbol).filter(Symbol.ticker == "AAPL").first()
    
    test_date = date(2026, 4, 1)
    
    # 1. 必要な先行データを投入 (T2, T3)
    # 大量データが必要（MA計算用）だが、モック計算用に最低限用意
    # ※ 本来はもっと多くの日付が必要だが、まずは gap_dates 抽出の論理をテスト
    #
    # 過去250日ぶんは「既に T5 計算済み」（market_trend_score あり）として投入する。
    # そうしないと250日全部が gap_dates に入り、最古日付は SPY の遡りが1本しか
    # ないため遡り不足ガード（§3.4）に引っかかってしまう — このテストの主眼は
    # 「既存レコードの NULL スコアを埋める」ロジックであり、遡り不足の検証は
    # `test_t5_lookback_guard.py` の担当。
    for i in range(250):
        d = test_date - timedelta(days=250-i)
        db_session.add(DailyPrice(symbol_id=spy.id, date=d, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000))
        db_session.add(DailyPrice(symbol_id=vix.id, date=d, open=20.0, high=21.0, low=19.0, close=20.0, volume=0))
        db_session.add(DailyPrice(symbol_id=stock.id, date=d, open=150.0, high=151.0, low=149.0, close=150.0, volume=500))
        db_session.add(Indicator(symbol_id=spy.id, date=d, sma_200=90.0, sma_50=95.0, ema_21=98.0))
        db_session.add(Indicator(symbol_id=stock.id, date=d, sma_50=140.0))
        db_session.add(MarketSignal(
            date=d, spy_above_sma200=1, distribution_days=0, follow_through_day=0,
            market_phase="BULL", market_trend_score=50.0
        ))

    # ターゲット日のデータ
    db_session.add(DailyPrice(symbol_id=spy.id, date=test_date, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000))
    db_session.add(DailyPrice(symbol_id=vix.id, date=test_date, open=20.0, high=21.0, low=19.0, close=20.0, volume=0))
    db_session.add(DailyPrice(symbol_id=stock.id, date=test_date, open=150.0, high=151.0, low=149.0, close=150.0, volume=500))
    db_session.add(Indicator(symbol_id=spy.id, date=test_date, sma_200=90.0, sma_50=95.0, ema_21=98.0))
    db_session.add(Indicator(symbol_id=stock.id, date=test_date, sma_50=140.0))
    
    # 2. 既存の不完全な MarketSignal レコードを投入
    # market_trend_score が NULL の状態
    db_session.add(MarketSignal(
        date=test_date,
        spy_above_sma200=1,
        distribution_days=0,
        follow_through_day=0,
        market_phase="RALLY_ATTEMPT",
        market_trend_score=None  # これを埋めてほしい
    ))
    db_session.commit()
    
    # 3. パイプライン実行
    sync_phase_t5_signals(db_session, logging.getLogger("test"))
    
    # 4. 検証
    signal = db_session.query(MarketSignal).filter(MarketSignal.date == test_date).first()
    
    # 現状のコード（date > t5_max）では、ここが None のままになりテストが失敗するはず
    assert signal.market_trend_score is not None, f"Score should be backfilled for {test_date}"
