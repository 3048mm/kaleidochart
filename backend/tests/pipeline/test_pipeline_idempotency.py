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
    vxv = Symbol(ticker="^VIX3M", exchange="INDEX", category="INDEX", active=1)
    stock = Symbol(ticker="AAPL", exchange="NASDAQ", category="個別", active=1)
    session.add_all([spy, vix, vxv, stock])
    session.commit()
    
    return session

def test_sync_phase_t5_does_not_recompute_existing_null_score_row(db_session):
    """
    既存の MarketSignal レコードがあれば、market_trend_score が NULL でも
    再計算対象にならないことを検証する。

    2026-09-23（5-8d・§3.6）: gap 判定を「行の有無」に統一したため、
    market_trend_score が NULL の行も「その日は既に処理済み」として扱われ、
    日次の自動実行では二度と上書きされなくなった（旧仕様は逆で、NULL の
    行を毎回再計算対象に戻し続けていた——本テストは元々それを固定する
    テストだったが、5-8d でその挙動自体が撤回されたため契約を反転させた）。
    正しい値を入れるには `--rebuild-from T5`（Parquet 基点）が必要。
    """
    spy = db_session.query(Symbol).filter(Symbol.ticker == "SPY").first()
    vix = db_session.query(Symbol).filter(Symbol.ticker == "^VIX").first()
    vxv = db_session.query(Symbol).filter(Symbol.ticker == "^VIX3M").first()
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
        db_session.add(DailyPrice(symbol_id=vxv.id, date=d, open=21.0, high=21.0, low=21.0, close=21.0, volume=0))
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
    db_session.add(DailyPrice(symbol_id=vxv.id, date=test_date, open=21.0, high=21.0, low=21.0, close=21.0, volume=0))
    db_session.add(DailyPrice(symbol_id=stock.id, date=test_date, open=150.0, high=151.0, low=149.0, close=150.0, volume=500))
    db_session.add(Indicator(symbol_id=spy.id, date=test_date, sma_200=90.0, sma_50=95.0, ema_21=98.0))
    db_session.add(Indicator(symbol_id=stock.id, date=test_date, sma_50=140.0))
    
    # 2. 既存の不完全な MarketSignal レコードを投入
    # market_trend_score が NULL の状態（何らかの理由で判定不能だった過去の行）
    db_session.add(MarketSignal(
        date=test_date,
        spy_above_sma200=1,
        distribution_days=0,
        follow_through_day=0,
        market_phase="RALLY_ATTEMPT",
        market_trend_score=None
    ))
    db_session.commit()
    existing_id = db_session.query(MarketSignal).filter(MarketSignal.date == test_date).first().id

    # 3. パイプライン実行
    sync_phase_t5_signals(db_session, logging.getLogger("test"))

    # 4. 検証: 行が既に存在するため gap から外れ、再計算・上書きされていない
    # （market_trend_score は None のまま、id も不変＝delete/insert されていない証拠）
    signal = db_session.query(MarketSignal).filter(MarketSignal.date == test_date).first()
    assert signal is not None
    assert signal.market_trend_score is None, (
        f"既存行があるため {test_date} は再計算されないはず"
    )
    assert signal.id == existing_id, "既存行が delete/insert されず維持されているはず"
