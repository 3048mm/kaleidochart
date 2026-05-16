
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
    # Use direct call to router function (mocking dependencies if needed, but here we pass db)
    resp = get_chart_data(symbol_id=1, limit=None, offset=0, db=db)
    
    assert len(resp.data) == 10
    assert resp.total_count == 10
    assert resp.has_more is False
    # Check order: ascending by date
    assert resp.data[0].time == "2026-04-01"
    assert resp.data[-1].time == "2026-04-10"

def test_get_chart_data_with_limit(seed_chart_data):
    db = seed_chart_data
    # Fetch latest 3
    resp = get_chart_data(symbol_id=1, limit=3, offset=0, db=db)
    
    assert len(resp.data) == 3
    assert resp.total_count == 10
    assert resp.has_more is True
    # Should be the LATEST 3, but returned in ASC order for chart
    assert resp.data[0].time == "2026-04-08"
    assert resp.data[1].time == "2026-04-09"
    assert resp.data[2].time == "2026-04-10"

def test_get_chart_data_with_limit_and_offset(seed_chart_data):
    db = seed_chart_data
    # Fetch next 3 (offset 3)
    resp = get_chart_data(symbol_id=1, limit=3, offset=3, db=db)
    
    assert len(resp.data) == 3
    assert resp.total_count == 10
    assert resp.has_more is True
    # Offset 3 from latest means skips 4-10, 4-09, 4-08 -> takes 4-07, 4-06, 4-05
    assert resp.data[0].time == "2026-04-05"
    assert resp.data[1].time == "2026-04-06"
    assert resp.data[2].time == "2026-04-07"

def test_get_chart_data_has_more_false(seed_chart_data):
    db = seed_chart_data
    # Fetch all with limit larger than total
    resp = get_chart_data(symbol_id=1, limit=20, offset=0, db=db)
    
    assert len(resp.data) == 10
    assert resp.has_more is False
