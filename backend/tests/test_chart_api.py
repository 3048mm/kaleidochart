import json
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
    # Call direct
    resp = get_chart_data(symbol_id=1, db=db)
    
    # Parse custom json response
    data = json.loads(resp.body)
    assert len(data["data"]) == 10
    assert data["metadata"]["ticker"] == "AAPL"
    # Check order: ascending by date
    assert data["data"][0]["time"] == "2026-04-01"
    assert data["data"][-1]["time"] == "2026-04-10"
