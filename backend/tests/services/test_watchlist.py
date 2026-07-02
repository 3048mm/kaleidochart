"""
TDD tests for watchlist feature.
Tests the core watchlist logic: add, remove, 1-hour reactivation,
1-hour cancel, update entry_date, bulk clear, and metrics calculation.
Uses an in-memory SQLite DB for isolation.
"""
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank
from db.models_user import BaseUser, Watchlist


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all main tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture
def user_db_session():
    """Create an in-memory SQLite DB with user tables."""
    engine = create_engine("sqlite:///:memory:")
    BaseUser.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def seed_data(db_session, user_db_session):
    """Seed basic test data: 1 symbol with price/indicator history."""
    sym = Symbol(id=1, ticker="AAPL", name="Apple Inc.", category="個別",
                 asset_class="Equity", active=1)
    db_session.add(sym)

    # Add 5 days of price data
    base_date = date(2026, 4, 10)
    for i in range(5):
        d = base_date + timedelta(days=i)
        dp = DailyPrice(symbol_id=1, date=d,
                        open=150.0 + i, high=155.0 + i,
                        low=148.0 + i, close=152.0 + i,
                        volume=1000000)
        ind = Indicator(symbol_id=1, date=d,
                        ema_21=151.0 + i, adr_pct_21=2.5,
                        sma50_atr_mult=3.0)
        db_session.add(dp)
        db_session.add(ind)

    # Add relative rank data for sparkline
    for i in range(5):
        d = base_date + timedelta(days=i)
        rr = RelativeRank(symbol_id=1, date=d,
                          group_name="個別", rs_ratio_rank_e21=0.5 + i * 0.05)
        db_session.add(rr)

    db_session.commit()
    return db_session, user_db_session


# ============================================================
# Import the service functions
# ============================================================
from api.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    update_watchlist_entry_date,
    clear_removed,
    get_watchlist,
    get_watchlist_tickers,
    remove_bulk_from_watchlist,
)


class TestAddToWatchlist:
    """Test POST /api/watchlist logic."""

    def test_add_to_watchlist(self, seed_data):
        """New registration creates an active record with correct entry_price."""
        db, user_db = seed_data
        result = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        assert result is not None
        assert result.status == "active"
        assert result.entry_date == date(2026, 4, 10)
        assert result.entry_price == 152.0  # close on 2026-04-10
        assert result.removed_at is None

    def test_add_duplicate_active_returns_existing(self, seed_data):
        """Adding the same ticker while already active returns existing record."""
        db, user_db = seed_data
        r1 = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))
        r2 = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 12))

        assert r1.id == r2.id  # Same record
        assert r2.entry_date == date(2026, 4, 10)  # Original date preserved

    def test_reactivate_within_1hour(self, seed_data):
        """Re-adding within 1 hour of removal restores original entry_date/price."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # Simulate removal 30 minutes ago
        wl = user_db.query(Watchlist).filter_by(symbol_id=1).first()
        wl.status = "removed"
        wl.removed_at = datetime.utcnow() - timedelta(minutes=30)
        wl.added_at = datetime.utcnow() - timedelta(days=5)  # Registered 5 days ago
        user_db.commit()

        # Re-add: should restore original entry_date
        result = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 14))

        assert result.status == "active"
        assert result.entry_date == date(2026, 4, 10)   # Original date restored
        assert result.removed_at is None

    def test_reactivate_after_1hour(self, seed_data):
        """Re-adding after 1 hour of removal uses new entry_date/price."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # Simulate removal 2 hours ago
        wl = user_db.query(Watchlist).filter_by(symbol_id=1).first()
        wl.status = "removed"
        wl.removed_at = datetime.utcnow() - timedelta(hours=2)
        wl.added_at = datetime.utcnow() - timedelta(days=10)
        user_db.commit()

        # Re-add with new date
        result = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 14))

        assert result.status == "active"
        assert result.entry_date == date(2026, 4, 14)   # New date
        assert result.entry_price == 156.0               # close on 2026-04-14
        assert result.removed_at is None

    def test_added_at_not_updated_on_reactivation(self, seed_data):
        """added_at must NOT be updated when reactivating a removed record."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # Set known added_at and simulate removal
        original_added_at = datetime(2026, 4, 5, 12, 0, 0)
        wl = user_db.query(Watchlist).filter_by(symbol_id=1).first()
        wl.added_at = original_added_at
        wl.status = "removed"
        wl.removed_at = datetime.utcnow() - timedelta(hours=2)  # >1hr ago
        user_db.commit()

        # Re-add: added_at should remain the original value
        result = add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 14))

        assert result.added_at == original_added_at


class TestRemoveFromWatchlist:
    """Test DELETE /api/watchlist/{ticker} logic."""

    def test_remove_from_watchlist(self, seed_data):
        """Removal (after 1 hour) sets status to 'removed' with removed_price."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # Simulate added_at was 2 hours ago
        wl = user_db.query(Watchlist).filter_by(symbol_id=1).first()
        wl.added_at = datetime.utcnow() - timedelta(hours=2)
        user_db.commit()

        result = remove_from_watchlist(db, user_db, ticker="AAPL")

        assert result is not None
        assert result.status == "removed"
        assert result.removed_at is not None
        assert result.removed_price is not None  # Snapshot of latest close

    def test_remove_within_1hour_deletes(self, seed_data):
        """Removal within 1 hour of registration physically deletes the record."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # added_at is now (< 1 hour ago) by default
        result = remove_from_watchlist(db, user_db, ticker="AAPL")

        assert result is None  # Physically deleted
        assert user_db.query(Watchlist).filter_by(symbol_id=1).first() is None

    def test_remove_after_1hour_logical_delete(self, seed_data):
        """Removal after 1 hour performs logical deletion, not physical."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        # Set added_at to 3 hours ago
        wl = user_db.query(Watchlist).filter_by(symbol_id=1).first()
        wl.added_at = datetime.utcnow() - timedelta(hours=3)
        user_db.commit()

        result = remove_from_watchlist(db, user_db, ticker="AAPL")

        assert result is not None
        assert result.status == "removed"
        # Record still exists in DB
        assert user_db.query(Watchlist).filter_by(symbol_id=1).first() is not None


class TestUpdateEntryDate:
    """Test PUT /api/watchlist/{ticker} logic."""

    def test_update_entry_date(self, seed_data):
        """Updating entry_date also updates entry_price to new date's close."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        result = update_watchlist_entry_date(db, user_db, ticker="AAPL",
                                             new_entry_date=date(2026, 4, 12))

        assert result.entry_date == date(2026, 4, 12)
        assert result.entry_price == 154.0  # close on 2026-04-12


class TestBulkClear:
    """Test DELETE /api/watchlist/removed/clear logic."""

    def test_bulk_clear_removed(self, seed_data):
        """Clearing removed items deletes only 'removed' records."""
        db, user_db = seed_data

        # Add two symbols
        sym2 = Symbol(id=2, ticker="MSFT", name="Microsoft", category="個別",
                      asset_class="Equity", active=1)
        db.add(sym2)
        dp2 = DailyPrice(symbol_id=2, date=date(2026, 4, 10),
                         open=300, high=305, low=298, close=302, volume=500000)
        db.add(dp2)
        db.commit()

        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))
        add_to_watchlist(db, user_db, ticker="MSFT", entry_date=date(2026, 4, 10))

        # Remove MSFT (not within 1 hour)
        wl_msft = user_db.query(Watchlist).filter_by(symbol_id=2).first()
        wl_msft.status = "removed"
        wl_msft.removed_at = datetime.utcnow() - timedelta(hours=2)
        wl_msft.added_at = datetime.utcnow() - timedelta(days=5)
        user_db.commit()

        deleted_count = clear_removed(user_db)

        assert deleted_count == 1
        assert user_db.query(Watchlist).filter_by(status="active").count() == 1
        assert user_db.query(Watchlist).filter_by(status="removed").count() == 0


class TestGetWatchlist:
    """Test GET /api/watchlist response shape and metrics."""

    def test_watchlist_metrics_calculation(self, seed_data):
        """Verify gain_pct, max_gain_pct, min_gain_pct are correctly computed."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        response = get_watchlist(db, user_db)

        assert len(response["active"]) == 1
        item = response["active"][0]

        # entry_price = 152.0 (close on 2026-04-10)
        assert item["entry_price"] == 152.0
        assert item["latest_close"] == 156.0
        expected_gain = (156.0 - 152.0) / 152.0 * 100
        assert abs(item["gain_pct"] - expected_gain) < 0.01

        # max_gain from closes: max(152,153,154,155,156) = 156
        # max_gain_pct = (156 - 152) / 152 * 100 = 2.631...
        expected_max_gain = (156.0 - 152.0) / 152.0 * 100
        assert abs(item["max_gain_pct"] - expected_max_gain) < 0.01

        # min_gain from closes: min(152,153,154,155,156) = 152
        # min_gain_pct = (152 - 152) / 152 * 100 = 0.0
        assert item["min_gain_pct"] == 0.0

        # rs_sparkline should have data
        assert isinstance(item["rs_sparkline"], list)
        assert len(item["rs_sparkline"]) > 0

    def test_remove_bulk_always_logical_deletion(self, seed_data):
        """Bulk removal should always perform logical deletion, ignoring the 1-hour rule."""
        db, user_db = seed_data
        
        # Add another symbol
        sym2 = Symbol(id=2, ticker="MSFT", name="Microsoft", category="個別", asset_class="Equity", active=1)
        db.add(sym2)
        dp2 = DailyPrice(symbol_id=2, date=date(2026, 4, 10), open=300, high=305, low=298, close=302, volume=500000)
        db.add(dp2)
        db.commit()

        # Add both to watchlist (added_at is NOW)
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))
        add_to_watchlist(db, user_db, ticker="MSFT", entry_date=date(2026, 4, 10))

        # Bulk remove
        count = remove_bulk_from_watchlist(db, user_db, tickers=["AAPL", "MSFT"])

        assert count == 2
        # Both should still exist in DB as 'removed'
        aapl = user_db.query(Watchlist).filter(Watchlist.symbol_id == 1).first()
        msft = user_db.query(Watchlist).filter(Watchlist.symbol_id == 2).first()
        assert aapl.status == "removed"
        assert msft.status == "removed"


class TestGetWatchlistTickers:
    """Test GET /api/watchlist/tickers."""

    def test_get_tickers(self, seed_data):
        """Returns only active tickers."""
        db, user_db = seed_data
        add_to_watchlist(db, user_db, ticker="AAPL", entry_date=date(2026, 4, 10))

        tickers = get_watchlist_tickers(db, user_db)

        assert "AAPL" in tickers
