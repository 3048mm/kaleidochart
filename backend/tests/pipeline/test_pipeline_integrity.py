"""
Tests for pipeline data integrity auditing (T2 = T3 count matching, SPY date alignment).
Verifies that the orchestrator throws an error and rolls back when data is inconsistent.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date

from db.models import Base, Symbol, DailyPrice, Indicator
from pipeline.orchestrator import verify_pipeline_integrity


class TestPipelineIntegrityVerification:
    """TDD tests for verify_pipeline_integrity function."""

    @pytest.fixture
    def test_db_setup(self):
        """Prepare clean in-memory database with seeded symbols."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = TestingSessionLocal()
        
        # Seed basic symbols including SPY
        spy = Symbol(ticker="SPY", exchange="US", category="市場", active=1)
        aapl = Symbol(ticker="AAPL", exchange="US", category="個別", active=1)
        db.add_all([spy, aapl])
        db.commit()
        
        # Fresh query to ensure IDs are bound and populated
        spy_id = spy.id
        aapl_id = aapl.id
        
        yield db, spy_id, aapl_id
        
        db.close()
        Base.metadata.drop_all(engine)

    def test_consistent_data_passes(self, test_db_setup):
        """Verify that when DailyPrice and Indicator counts match and SPY is aligned, validation passes."""
        db, spy_id, aapl_id = test_db_setup
        d = date(2026, 7, 15)
        
        # Both SPY and AAPL have price and indicator
        db_entries = [
            DailyPrice(symbol_id=spy_id, date=d, open=100, high=101, low=99, close=100, volume=100),
            DailyPrice(symbol_id=aapl_id, date=d, open=200, high=201, low=199, close=200, volume=200),
            Indicator(symbol_id=spy_id, date=d, ema_21=100),
            Indicator(symbol_id=aapl_id, date=d, ema_21=200),
        ]
        db.add_all(db_entries)
        db.commit()
        
        # Should complete without throwing any exception
        verify_pipeline_integrity(db, categories=None)

    def test_mismatched_counts_raises_value_error(self, test_db_setup):
        """Verify that when Indicator count is lower than DailyPrice count, validation fails."""
        db, spy_id, aapl_id = test_db_setup
        d = date(2026, 7, 15)
        
        # SPY and AAPL have prices, but AAPL is missing indicator
        db_entries = [
            DailyPrice(symbol_id=spy_id, date=d, open=100, high=101, low=99, close=100, volume=100),
            DailyPrice(symbol_id=aapl_id, date=d, open=200, high=201, low=199, close=200, volume=200),
            Indicator(symbol_id=spy_id, date=d, ema_21=100),
            # Missing AAPL indicator!
        ]
        db.add_all(db_entries)
        db.commit()
        
        with pytest.raises(ValueError) as exc_info:
            verify_pipeline_integrity(db, categories=None)
        
        assert "Mismatched record counts" in str(exc_info.value)

    def test_spy_date_not_aligned_raises_value_error(self, test_db_setup):
        """Verify that when AAPL has a newer price than SPY, validation fails."""
        db, spy_id, aapl_id = test_db_setup
        d_old = date(2026, 7, 14)
        d_new = date(2026, 7, 15)
        
        # SPY is at 7/14, AAPL has advanced to 7/15
        db_entries = [
            DailyPrice(symbol_id=spy_id, date=d_old, open=100, high=101, low=99, close=100, volume=100),
            DailyPrice(symbol_id=aapl_id, date=d_new, open=200, high=201, low=199, close=200, volume=200),
            Indicator(symbol_id=spy_id, date=d_old, ema_21=100),
            Indicator(symbol_id=aapl_id, date=d_new, ema_21=200),
        ]
        db.add_all(db_entries)
        db.commit()
        
        with pytest.raises(ValueError) as exc_info:
            verify_pipeline_integrity(db, categories=None)
            
        assert "SPY date is not aligned" in str(exc_info.value)
