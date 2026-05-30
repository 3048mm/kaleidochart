"""
TDD tests for portfolio API service layer.
Phase 2 - Steps 2-1 through 2-5.
Tests portfolio CRUD, position management, sell/trim flow, and history.
Uses an in-memory SQLite DB for isolation.
"""
import pytest
from datetime import date, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank
from db.models_user import BaseUser, Portfolio, PortfolioPosition, PositionHistory, TotalPortfolio
from api.portfolio_service import (
    create_portfolio,
    get_portfolios,
    get_portfolio,
    update_portfolio,
    archive_portfolio,
    add_position,
    get_positions_with_metrics,
    sell_position,
    get_history,
    get_portfolio_summary,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with main tables."""
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
    """Seed basic test data: 2 symbols with price and indicator data."""
    sym1 = Symbol(id=1, ticker="AAPL", name="Apple Inc.", category="個別",
                  asset_class="Equity", active=1)
    sym2 = Symbol(id=2, ticker="MSFT", name="Microsoft Corp.", category="個別",
                  asset_class="Equity", active=1)
    db_session.add_all([sym1, sym2])

    # Price data for AAPL (5 days)
    base_date = date(2026, 5, 1)
    for i in range(5):
        d = date(2026, 5, 1 + i)
        dp = DailyPrice(symbol_id=1, date=d,
                        open=180.0 + i, high=185.0 + i, low=178.0 + i,
                        close=183.0 + i, volume=50_000_000)
        ind = Indicator(symbol_id=1, date=d,
                        ema_21=181.0 + i, sma_50=175.0, atr_14=3.5,
                        atr_pct_14=1.9, adr_pct_21=2.1,
                        dist_sma50_atr=2.3)
        db_session.add(dp)
        db_session.add(ind)

    # Price data for MSFT
    dp_msft = DailyPrice(symbol_id=2, date=date(2026, 5, 1),
                         open=420.0, high=425.0, low=418.0, close=422.0,
                         volume=30_000_000)
    ind_msft = Indicator(symbol_id=2, date=date(2026, 5, 1),
                         ema_21=418.0, sma_50=410.0, atr_14=5.0,
                         atr_pct_14=1.2, adr_pct_21=1.8, dist_sma50_atr=2.4)
    db_session.add(dp_msft)
    db_session.add(ind_msft)

    # Rank data for AAPL
    for i in range(5):
        d = date(2026, 5, 1 + i)
        rr = RelativeRank(symbol_id=1, date=d, group_name="個別",
                          rs_ratio_21=0.8 + i * 0.02)
        db_session.add(rr)

    db_session.commit()
    return db_session, user_db_session


# ============================================================
# 2-1: Portfolio CRUD
# ============================================================
class TestPortfolioCRUD:
    """Test portfolio creation, listing, update, archive."""

    def test_create_portfolio(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="スイング", currency="JPY",
                              total_capital=3_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0, stop_loss_method="fixed_pct",
                              max_positions=8)
        assert pf is not None
        assert pf.name == "スイング"
        assert pf.total_capital == 3_000_000
        assert pf.status == "active"
        # TotalPortfolio auto created
        tp = user_db.query(TotalPortfolio).first()
        assert tp is not None
        assert pf.total_portfolio_id == tp.id

    def test_get_portfolios_list(self, seed_data):
        db, user_db = seed_data
        create_portfolio(user_db, name="長期", currency="JPY", total_capital=5_000_000,
                         risk_pct=0.5, default_stop_loss_pct=5.0,
                         stop_loss_method="fixed_pct", max_positions=12)
        create_portfolio(user_db, name="スイング", currency="JPY", total_capital=3_000_000,
                         risk_pct=1.0, default_stop_loss_pct=8.0,
                         stop_loss_method="fixed_pct", max_positions=8)
        result = get_portfolios(user_db)
        assert len(result) == 2

    def test_get_single_portfolio(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Test", currency="JPY", total_capital=1_000_000,
                              risk_pct=1.0, default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        result = get_portfolio(user_db, pf.id)
        assert result is not None
        assert result.name == "Test"

    def test_get_nonexistent_portfolio_returns_none(self, seed_data):
        db, user_db = seed_data
        result = get_portfolio(user_db, 9999)
        assert result is None

    def test_update_portfolio(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Old Name", currency="JPY",
                              total_capital=1_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0, stop_loss_method="fixed_pct",
                              max_positions=8)
        updated = update_portfolio(user_db, pf.id, name="New Name", total_capital=5_000_000)
        assert updated.name == "New Name"
        assert updated.total_capital == 5_000_000
        assert updated.risk_pct == 1.0  # Unchanged

    def test_archive_portfolio(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="To Archive", currency="JPY",
                              total_capital=1_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0, stop_loss_method="fixed_pct",
                              max_positions=8)
        result = archive_portfolio(user_db, pf.id)
        assert result.status == "archived"
        # Archived should not appear in active list
        active = get_portfolios(user_db)
        assert len(active) == 0


# ============================================================
# 2-2: Position Management
# ============================================================
class TestPositionManagement:
    """Test adding positions and getting enriched position list."""

    def _make_pf(self, user_db):
        return create_portfolio(user_db, name="Test PF", currency="JPY",
                                total_capital=10_000_000, risk_pct=1.0,
                                default_stop_loss_pct=8.0,
                                stop_loss_method="fixed_pct", max_positions=8)

    def test_add_position(self, seed_data):
        db, user_db = seed_data
        pf = self._make_pf(user_db)
        pos = add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                           entry_date=date(2026, 5, 1), shares=100)
        assert pos is not None
        assert pos.entry_price == 183.0  # close on 2026-05-01
        assert pos.shares == 100
        assert pos.original_shares == 100
        assert pos.status == "open"

    def test_add_position_invalid_ticker(self, seed_data):
        db, user_db = seed_data
        pf = self._make_pf(user_db)
        pos = add_position(db, user_db, portfolio_id=pf.id, ticker="INVALID",
                           entry_date=date(2026, 5, 1), shares=100)
        assert pos is None

    def test_get_positions_with_metrics(self, seed_data):
        db, user_db = seed_data
        pf = self._make_pf(user_db)
        add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                     entry_date=date(2026, 5, 1), shares=100)
        positions = get_positions_with_metrics(db, user_db, pf.id)
        assert len(positions) == 1
        p = positions[0]
        assert p["ticker"] == "AAPL"
        assert p["entry_price"] == 183.0
        assert p["shares"] == 100
        assert "current_price" in p
        assert "total_gain_pct" in p
        assert "stop_loss_price" in p


# ============================================================
# 2-3: Sell / Trim Flow
# ============================================================
class TestSellFlow:
    """Test sell and trim operations."""

    def _make_pf_with_position(self, db, user_db):
        pf = create_portfolio(user_db, name="Test PF", currency="JPY",
                              total_capital=10_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        pos = add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                           entry_date=date(2026, 5, 1), shares=100)
        return pf, pos

    def test_full_sell(self, seed_data):
        """Full sell removes position and creates history."""
        db, user_db = seed_data
        pf, pos = self._make_pf_with_position(db, user_db)
        hist = sell_position(user_db, position_id=pos.id,
                             exit_date=date(2026, 5, 5), exit_price=187.0,
                             exit_shares=100, exit_reason="take_profit_full")
        assert hist is not None
        assert hist.exit_shares == 100
        assert hist.pnl_pct == pytest.approx((187.0 - 183.0) / 183.0 * 100, abs=0.01)
        assert hist.holding_days == 4
        # Position should be deleted
        remaining = user_db.query(PortfolioPosition).filter_by(id=pos.id).first()
        assert remaining is None

    def test_trim_sell(self, seed_data):
        """Trim reduces shares and creates partial history."""
        db, user_db = seed_data
        pf, pos = self._make_pf_with_position(db, user_db)
        hist = sell_position(user_db, position_id=pos.id,
                             exit_date=date(2026, 5, 3), exit_price=185.0,
                             exit_shares=33, exit_reason="take_profit_trim")
        assert hist is not None
        assert hist.exit_shares == 33
        # Position should remain with reduced shares
        remaining = user_db.query(PortfolioPosition).filter_by(id=pos.id).first()
        assert remaining is not None
        assert remaining.shares == 67
        assert remaining.status == "partially_closed"

    def test_sell_more_than_held_fails(self, seed_data):
        """Cannot sell more shares than held."""
        db, user_db = seed_data
        pf, pos = self._make_pf_with_position(db, user_db)
        result = sell_position(user_db, position_id=pos.id,
                               exit_date=date(2026, 5, 5), exit_price=187.0,
                               exit_shares=200, exit_reason="manual")
        assert result is None

    def test_sequential_trims_then_full(self, seed_data):
        """Multiple trims followed by full sell."""
        db, user_db = seed_data
        pf, pos = self._make_pf_with_position(db, user_db)
        # First trim: 33 shares
        sell_position(user_db, position_id=pos.id, exit_date=date(2026, 5, 2),
                      exit_price=184.0, exit_shares=33,
                      exit_reason="take_profit_trim")
        # Second trim: 33 shares
        sell_position(user_db, position_id=pos.id, exit_date=date(2026, 5, 3),
                      exit_price=185.0, exit_shares=33,
                      exit_reason="take_profit_trim")
        # Final sell: remaining 34 shares
        sell_position(user_db, position_id=pos.id, exit_date=date(2026, 5, 5),
                      exit_price=187.0, exit_shares=34,
                      exit_reason="take_profit_full")

        # Position should be gone
        remaining = user_db.query(PortfolioPosition).filter_by(id=pos.id).first()
        assert remaining is None
        # 3 history records
        history = user_db.query(PositionHistory).filter_by(
            portfolio_id=pf.id, symbol_id=1).all()
        assert len(history) == 3


# ============================================================
# 2-4: History API
# ============================================================
class TestHistoryAPI:
    """Test position history retrieval."""

    def test_get_history(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Test", currency="JPY",
                              total_capital=10_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        pos = add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                           entry_date=date(2026, 5, 1), shares=100)
        sell_position(user_db, position_id=pos.id, exit_date=date(2026, 5, 5),
                      exit_price=187.0, exit_shares=100,
                      exit_reason="take_profit_full")
        history = get_history(db, user_db, pf.id)
        assert len(history) == 1
        h = history[0]
        assert h["ticker"] == "AAPL"
        assert h["exit_reason"] == "take_profit_full"
        assert "cumulative_pnl" in h

    def test_empty_history(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Empty", currency="JPY",
                              total_capital=1_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        history = get_history(db, user_db, pf.id)
        assert len(history) == 0


# ============================================================
# 2-5: Portfolio Summary
# ============================================================
class TestPortfolioSummary:
    """Test portfolio summary (risk dashboard data)."""

    def test_summary_with_positions(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Test", currency="JPY",
                              total_capital=10_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                     entry_date=date(2026, 5, 1), shares=100)
        summary = get_portfolio_summary(db, user_db, pf.id)
        assert summary is not None
        assert summary["total_capital"] == 10_000_000
        assert summary["risk_amount"] == 100_000  # 1% of 10M
        assert summary["max_investment"] == 1_250_000  # 100K / 8%
        assert summary["open_positions"] == 1
        assert summary["max_positions"] == 8
        assert "invested_total" in summary
        assert "market_value_total" in summary
        assert "unrealized_pnl" in summary

    def test_summary_empty_portfolio(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Empty", currency="JPY",
                              total_capital=5_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        summary = get_portfolio_summary(db, user_db, pf.id)
        assert summary["open_positions"] == 0
        assert summary["invested_total"] == 0
        assert summary["market_value_total"] == 0
        assert summary["unrealized_pnl"] == 0


# ============================================================
# 2-6: Analytics
# ============================================================
class TestAnalytics:
    """Test portfolio analytics (sector, win rate, equity curve)."""

    def test_analytics_with_trades(self, seed_data):
        from api.portfolio_service import get_analytics
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Analytics", currency="JPY",
                              total_capital=10_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        # Add and sell a winning trade
        pos1 = add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                            entry_date=date(2026, 5, 1), shares=100)
        sell_position(user_db, position_id=pos1.id,
                      exit_date=date(2026, 5, 5), exit_price=190.0,
                      exit_shares=100, exit_reason="take_profit_full")

        # Add open position for sector breakdown
        add_position(db, user_db, portfolio_id=pf.id, ticker="MSFT",
                     entry_date=date(2026, 5, 1), shares=50)

        analytics = get_analytics(db, user_db, pf.id)
        assert analytics is not None
        # Sector breakdown (open positions: MSFT)
        assert len(analytics["sector_breakdown"]) >= 1
        # Trade stats
        assert analytics["total_trades"] == 1
        assert analytics["win_count"] == 1
        assert analytics["win_rate"] == 100.0
        assert analytics["avg_win_pct"] > 0
        # Equity curve
        assert len(analytics["equity_curve"]) == 1
        assert analytics["equity_curve"][0]["cumulative_pnl"] > 0
        # Monthly returns
        assert len(analytics["monthly_returns"]) == 1

    def test_analytics_empty(self, seed_data):
        from api.portfolio_service import get_analytics
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Empty", currency="JPY",
                              total_capital=1_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
        analytics = get_analytics(db, user_db, pf.id)
        assert analytics["total_trades"] == 0
        assert analytics["win_rate"] == 0.0
        assert len(analytics["equity_curve"]) == 0
        assert len(analytics["monthly_returns"]) == 0


class TestSelfHealing:
    """Test self-healing logic for portfolios."""

    def test_heal_portfolio_ids(self, seed_data):
        db, user_db = seed_data
        pf = create_portfolio(user_db, name="Self Healing PF", currency="JPY",
                              total_capital=10_000_000, risk_pct=1.0,
                              default_stop_loss_pct=8.0,
                              stop_loss_method="fixed_pct", max_positions=8)
                              
        # Add position (normally binds symbol_id = 1)
        pos = add_position(db, user_db, portfolio_id=pf.id, ticker="AAPL",
                           entry_date=date(2026, 5, 1), shares=100)
        assert pos is not None
        assert pos.symbol_id == 1
        
        # Corrupt symbol_id: make it incorrect (e.g. 999) or None
        pos.symbol_id = 999
        user_db.commit()
        
        # Adding sold position to history to test history healing as well
        hist = sell_position(user_db, position_id=pos.id, exit_date=date(2026, 5, 5),
                             exit_price=187.0, exit_shares=50, exit_reason="trim")
        assert hist is not None
        assert hist.symbol_id == 999
        
        # Corrupt history symbol_id
        hist.symbol_id = None
        user_db.commit()
        
        # Calling get_positions_with_metrics should trigger self-healing for pos
        positions = get_positions_with_metrics(db, user_db, pf.id)
        assert len(positions) == 1
        
        # Re-fetch pos and hist from DB to verify they have been healed back to 1
        healed_pos = user_db.query(PortfolioPosition).filter_by(id=pos.id).first()
        assert healed_pos.symbol_id == 1
        
        # Calling get_history should trigger self-healing for history as well
        history = get_history(db, user_db, pf.id)
        assert len(history) == 1
        
        healed_hist = user_db.query(PositionHistory).filter_by(id=hist.id).first()
        assert healed_hist.symbol_id == 1
