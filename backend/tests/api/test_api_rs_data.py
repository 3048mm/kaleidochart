import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank
from api.routers import get_chart_data


def test_rs_data_availability():
    """TDD Phase 2: Verify chart data API is populated correctly using mock seed data (No real DB dependency)."""
    # 1. Create a clean in-memory database for testing
    engine = create_engine(
        "sqlite:///file::memory:?cache=shared&uri=true",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    
    db = TestingSessionLocal()
    try:
        # Seed test data for DUOL
        from datetime import date
        d = date(2026, 5, 20)
        
        duol = Symbol(id=1, ticker="DUOL", name="Duolingo", category="個別", active=1)
        db.add(duol)
        
        dp = DailyPrice(symbol_id=1, date=d, open=100.0, high=105.0, low=98.0, close=102.0, volume=1000)
        ind = Indicator(
            symbol_id=1,
            date=d,
            change_1d_pct=2.0,
            ema_21=100.0,
            rs_ratio_e14=0.85,
            rs_momentum_e14=0.75,
            rs_trend_s14=0.65
        ) 
        db.add_all([dp, ind])
        db.commit()
        
        # 2. Call API directly
        response = get_chart_data(symbol_id=1, db=db)
        import json
        content = json.loads(response.body)
        
        assert len(content['data']) > 0
        last_point = content['data'][-1]
        
        # Check T3 indicators are fetched correctly
        assert last_point['rs_ratio_14'] == 0.85
        assert last_point['rs_momentum_14'] == 0.75
        assert last_point['rs_condition_14'] == 0.65
        
        # Verify change_1d_pct is present and matches the mock seed
        assert last_point['change_1d_pct'] is not None
        assert last_point['change_1d_pct'] == 2.0

    finally:
        db.close()
        Base.metadata.drop_all(engine)
