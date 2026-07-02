import os
import sys
from datetime import datetime, date
import pytest
import pytz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend and project root to path prioritizing local packages (tests -> pipeline -> backend -> project_root と4段階上に遡る)
test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(test_dir)
project_root = os.path.dirname(backend_dir)

sys.path.insert(0, backend_dir)
sys.path.insert(0, project_root)

from db.models import Base

from pipeline.utils import update_pipeline_meta
    # We will implement these functions in backend/pipeline/orchestrator.py
from pipeline.orchestrator import should_run_pipeline_for_date, clear_pipeline_data_for_date
from db.models import Base, DailyPrice, Indicator, RelativeRank, MarketSignal

@pytest.fixture
def db_session():
    """Sets up an in-memory SQLite DB for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_case_1_new_trading_day(db_session):
    """
    Case 1: When DB is empty or SPY latest date has advanced.
    Expect: Should trigger run ('prompt' mode).
    """
    spy_latest = date(2026, 5, 22)
    # 1. DB is completely empty
    current_time_utc = datetime(2026, 5, 23, 10, 0, 0, tzinfo=pytz.utc) # 10:00 UTC (06:00 EST)
    should_run, mode = should_run_pipeline_for_date(db_session, current_time_utc, spy_latest)
    assert should_run is True
    assert mode == "prompt"

    # 2. DB has older SPY date
    update_pipeline_meta(db_session, datetime(2026, 5, 22, 11, 15, 0, tzinfo=pytz.utc), date(2026, 5, 21))
    should_run, mode = should_run_pipeline_for_date(db_session, current_time_utc, spy_latest)
    assert should_run is True
    assert mode == "prompt"

def test_case_2_morning_duplicate_prevention(db_session):
    """
    Case 2: SPY date is same, last run was in the morning (unconfirmed),
    and current time is STILL in the morning (unconfirmed window, < 22:00 EST).
    Expect: Should SKIP execution to avoid redundant API fetching.
    """
    spy_latest = date(2026, 5, 22)
    # Morning run started at JST 07:15 (UTC 22:15 on May 22 = EST 18:15 on May 22)
    start_time_utc = datetime(2026, 5, 22, 22, 15, 0, tzinfo=pytz.utc)
    update_pipeline_meta(db_session, start_time_utc, spy_latest)

    # Current time is JST 09:30 (UTC 00:30 on May 23 = EST 20:30 on May 22, before 22:00 EST)
    current_time_utc = datetime(2026, 5, 23, 0, 30, 0, tzinfo=pytz.utc)
    should_run, mode = should_run_pipeline_for_date(db_session, current_time_utc, spy_latest)
    assert should_run is False
    assert mode == "skip"

def test_case_3_volume_confirmation_update(db_session):
    """
    Case 3: SPY date is same, last run was in the morning (unconfirmed),
    but current time is NOW past volume confirmation time (>= 22:00 EST / 11:00 JST summer / 12:00 JST winter).
    Expect: Should trigger run ('confirm' mode) to overwrite with final volume.
    """
    spy_latest = date(2026, 5, 22)
    # Morning run started at JST 07:15 (UTC 22:15 on May 22 = EST 18:15 on May 22)
    start_time_utc = datetime(2026, 5, 22, 22, 15, 0, tzinfo=pytz.utc)
    update_pipeline_meta(db_session, start_time_utc, spy_latest)

    # Current time is JST 11:30 (UTC 02:30 on May 23 = EST 22:30 on May 22, past 22:00 EST)
    current_time_utc = datetime(2026, 5, 23, 2, 30, 0, tzinfo=pytz.utc)
    should_run, mode = should_run_pipeline_for_date(db_session, current_time_utc, spy_latest)
    assert should_run is True
    assert mode == "confirm"

def test_case_4_confirmed_duplicate_prevention(db_session):
    """
    Case 4: SPY date is same, last run was already done past volume confirmation (>= 22:00 EST).
    Expect: Should SKIP execution as data is fully confirmed.
    """
    spy_latest = date(2026, 5, 22)
    # Afternoon confirmed run started at JST 11:30 (UTC 02:30 on May 23 = EST 22:30 on May 22)
    start_time_utc = datetime(2026, 5, 23, 2, 30, 0, tzinfo=pytz.utc)
    update_pipeline_meta(db_session, start_time_utc, spy_latest)

    # Current time is JST 13:00 (UTC 04:00 on May 23 = EST 00:00 on May 23)
    current_time_utc = datetime(2026, 5, 23, 4, 0, 0, tzinfo=pytz.utc)
    should_run, mode = should_run_pipeline_for_date(db_session, current_time_utc, spy_latest)
    assert should_run is False
    assert mode == "skip"

def test_clear_pipeline_data_for_date(db_session):
    """
    Verifies that clear_pipeline_data_for_date deletes records only on the target date,
    preserving downstream historical data on other dates.
    """
    from db.models import Symbol
    
    # 1. Setup mock active symbols
    stock = Symbol(id=10, ticker="AAPL", exchange="NASDAQ", category="個別", active=1)
    db_session.add(stock)
    db_session.commit()
    
    target_date = date(2026, 5, 22)
    other_date = date(2026, 5, 21)
    
    # Add dummy records for both target and other dates
    db_session.add_all([
        DailyPrice(symbol_id=10, date=target_date, open=100.0, close=100.0),
        DailyPrice(symbol_id=10, date=other_date, open=99.0, close=99.0),
        
        Indicator(symbol_id=10, date=target_date, sma_200=100.0),
        Indicator(symbol_id=10, date=other_date, sma_200=99.0),
        
        RelativeRank(symbol_id=10, date=target_date, group_name="個別", rs_value_rank=0.5),
        RelativeRank(symbol_id=10, date=other_date, group_name="個別", rs_value_rank=0.4),
        
        MarketSignal(date=target_date, market_phase="BULL", market_trend_score=80.0),
        MarketSignal(date=other_date, market_phase="CORRECTION", market_trend_score=70.0)
    ])
    db_session.commit()
    
    # Verify everything exists before deletion
    assert db_session.query(DailyPrice).count() == 2
    assert db_session.query(Indicator).count() == 2
    assert db_session.query(RelativeRank).count() == 2
    assert db_session.query(MarketSignal).count() == 2
    
    # 2. Trigger targeted clear for target_date
    clear_pipeline_data_for_date(db_session, target_date)
    
    # Verify target date records are deleted, but other date is preserved
    assert db_session.query(DailyPrice).filter(DailyPrice.date == target_date).first() is None
    assert db_session.query(Indicator).filter(Indicator.date == target_date).first() is None
    assert db_session.query(RelativeRank).filter(RelativeRank.date == target_date).first() is None
    assert db_session.query(MarketSignal).filter(MarketSignal.date == target_date).first() is None
    
    # Check other date count (should be preserved)
    assert db_session.query(DailyPrice).filter(DailyPrice.date == other_date).first() is not None
    assert db_session.query(Indicator).filter(Indicator.date == other_date).first() is not None
    assert db_session.query(RelativeRank).filter(RelativeRank.date == other_date).first() is not None
    assert db_session.query(MarketSignal).filter(MarketSignal.date == other_date).first() is not None

