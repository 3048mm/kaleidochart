"""
TDD tests for portfolio DB models: Portfolio, PortfolioPosition, PositionHistory.
Phase 1 - Step 1-6. Uses in-memory SQLite for isolation.
"""
import pytest
from datetime import date, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice
from db.models_user import BaseUser, Portfolio, PortfolioPosition, PositionHistory, TotalPortfolio


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    BaseUser.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def seed_data(db_session):
    """Seed basic test data: 2 symbols."""
    sym1 = Symbol(id=1, ticker="AAPL", name="Apple Inc.", category="個別",
                  asset_class="Equity", active=1)
    sym2 = Symbol(id=2, ticker="MSFT", name="Microsoft Corp.", category="個別",
                  asset_class="Equity", active=1)
    db_session.add_all([sym1, sym2])

    # Price data for AAPL
    dp = DailyPrice(symbol_id=1, date=date(2026, 5, 1),
                    open=180.0, high=185.0, low=178.0, close=183.0,
                    volume=50000000)
    db_session.add(dp)
    
    tp = TotalPortfolio(id=1, name="Total", currency="JPY")
    db_session.add(tp)
    
    db_session.commit()
    return db_session


class TestPortfolioModel:
    """Test Portfolio table CRUD."""

    def test_create_portfolio(self, seed_data):
        """Can create a portfolio with all required fields."""
        db = seed_data
        pf = Portfolio(
            total_portfolio_id=1,
            name="スイング",
            currency="JPY",
            total_capital=3_000_000,
            risk_pct=1.0,
            default_stop_loss_pct=8.0,
            stop_loss_method="fixed_pct",
            max_positions=8,
            source="manual",
            status="active",
        )
        db.add(pf)
        db.commit()

        result = db.query(Portfolio).filter_by(name="スイング").first()
        assert result is not None
        assert result.total_capital == 3_000_000
        assert result.risk_pct == 1.0
        assert result.currency == "JPY"
        assert result.status == "active"

    def test_create_multiple_portfolios(self, seed_data):
        """Can have multiple portfolios (long-term, swing, etc)."""
        db = seed_data
        pf1 = Portfolio(total_portfolio_id=1, name="長期投資", currency="JPY", total_capital=5_000_000,
                        risk_pct=0.5, default_stop_loss_pct=5.0,
                        stop_loss_method="fixed_pct", max_positions=12,
                        source="manual", status="active")
        pf2 = Portfolio(total_portfolio_id=1, name="スイング", currency="JPY", total_capital=3_000_000,
                        risk_pct=1.0, default_stop_loss_pct=8.0,
                        stop_loss_method="fixed_pct", max_positions=8,
                        source="manual", status="active")
        db.add_all([pf1, pf2])
        db.commit()

        portfolios = db.query(Portfolio).filter_by(status="active").all()
        assert len(portfolios) == 2

    def test_archive_portfolio(self, seed_data):
        """Can archive a portfolio by changing status."""
        db = seed_data
        pf = Portfolio(total_portfolio_id=1, name="旧ポートフォリオ", currency="JPY",
                       total_capital=1_000_000, risk_pct=1.0,
                       default_stop_loss_pct=8.0, stop_loss_method="fixed_pct",
                       max_positions=8, source="manual", status="active")
        db.add(pf)
        db.commit()

        pf.status = "archived"
        db.commit()

        result = db.query(Portfolio).filter_by(name="旧ポートフォリオ").first()
        assert result.status == "archived"

    def test_atr_method_fields(self, seed_data):
        """ATR method portfolio stores atr_multiplier."""
        db = seed_data
        pf = Portfolio(total_portfolio_id=1, name="ATR Portfolio", currency="USD",
                       total_capital=100_000, risk_pct=1.0,
                       default_stop_loss_pct=8.0,
                       stop_loss_method="atr_multiple",
                       atr_multiplier=2.5,
                       max_positions=8, source="manual", status="active")
        db.add(pf)
        db.commit()

        result = db.query(Portfolio).first()
        assert result.stop_loss_method == "atr_multiple"
        assert result.atr_multiplier == 2.5


class TestPortfolioPositionModel:
    """Test PortfolioPosition table CRUD."""

    def _create_portfolio(self, db):
        pf = Portfolio(total_portfolio_id=1, name="Test PF", currency="JPY", total_capital=10_000_000,
                       risk_pct=1.0, default_stop_loss_pct=8.0,
                       stop_loss_method="fixed_pct", max_positions=8,
                       source="manual", status="active")
        db.add(pf)
        db.commit()
        return pf

    def test_add_position(self, seed_data):
        """Can add a position to a portfolio."""
        db = seed_data
        pf = self._create_portfolio(db)
        pos = PortfolioPosition(
            portfolio_id=pf.id, symbol_id=1,
            entry_date=date(2026, 5, 1), entry_price=183.0,
            shares=100, original_shares=100,
            status="open",
        )
        db.add(pos)
        db.commit()

        result = db.query(PortfolioPosition).filter_by(portfolio_id=pf.id).first()
        assert result.shares == 100
        assert result.entry_price == 183.0
        assert result.status == "open"

    def test_trim_position(self, seed_data):
        """Trimming reduces shares and changes status."""
        db = seed_data
        pf = self._create_portfolio(db)
        pos = PortfolioPosition(
            portfolio_id=pf.id, symbol_id=1,
            entry_date=date(2026, 5, 1), entry_price=183.0,
            shares=100, original_shares=100,
            status="open",
        )
        db.add(pos)
        db.commit()

        # Trim 33 shares
        pos.shares -= 33
        pos.status = "partially_closed"
        db.commit()

        result = db.query(PortfolioPosition).first()
        assert result.shares == 67
        assert result.original_shares == 100
        assert result.status == "partially_closed"

    def test_multiple_positions_in_portfolio(self, seed_data):
        """Portfolio can hold multiple positions."""
        db = seed_data
        pf = self._create_portfolio(db)
        pos1 = PortfolioPosition(portfolio_id=pf.id, symbol_id=1,
                                 entry_date=date(2026, 5, 1), entry_price=183.0,
                                 shares=100, original_shares=100, status="open")
        pos2 = PortfolioPosition(portfolio_id=pf.id, symbol_id=2,
                                 entry_date=date(2026, 5, 1), entry_price=420.0,
                                 shares=50, original_shares=50, status="open")
        db.add_all([pos1, pos2])
        db.commit()

        positions = db.query(PortfolioPosition).filter_by(portfolio_id=pf.id).all()
        assert len(positions) == 2

    def test_custom_stop_loss_override(self, seed_data):
        """Position-level stop_loss_pct overrides portfolio default (NULL=inherit)."""
        db = seed_data
        pf = self._create_portfolio(db)
        pos_default = PortfolioPosition(
            portfolio_id=pf.id, symbol_id=1,
            entry_date=date(2026, 5, 1), entry_price=183.0,
            shares=100, original_shares=100, status="open",
            stop_loss_pct=None,  # Inherit from portfolio
        )
        pos_custom = PortfolioPosition(
            portfolio_id=pf.id, symbol_id=2,
            entry_date=date(2026, 5, 1), entry_price=420.0,
            shares=50, original_shares=50, status="open",
            stop_loss_pct=5.0,  # Custom override
        )
        db.add_all([pos_default, pos_custom])
        db.commit()

        result_default = db.query(PortfolioPosition).filter_by(symbol_id=1).first()
        result_custom = db.query(PortfolioPosition).filter_by(symbol_id=2).first()
        assert result_default.stop_loss_pct is None
        assert result_custom.stop_loss_pct == 5.0


class TestPositionHistoryModel:
    """Test PositionHistory table CRUD."""

    def test_create_history_record(self, seed_data):
        """Selling creates a history record with PnL."""
        db = seed_data
        pf = Portfolio(total_portfolio_id=1, name="Test", currency="JPY", total_capital=10_000_000,
                       risk_pct=1.0, default_stop_loss_pct=8.0,
                       stop_loss_method="fixed_pct", max_positions=8,
                       source="manual", status="active")
        db.add(pf)
        db.commit()

        hist = PositionHistory(
            portfolio_id=pf.id, symbol_id=1,
            entry_date=date(2026, 4, 1), entry_price=170.0,
            entry_shares=100,
            exit_date=date(2026, 5, 1), exit_price=183.0,
            exit_shares=100,
            exit_reason="take_profit_full",
            pnl_pct=7.647,
            pnl_amount=1300.0,
            holding_days=30,
        )
        db.add(hist)
        db.commit()

        result = db.query(PositionHistory).first()
        assert result.exit_reason == "take_profit_full"
        assert result.pnl_pct == pytest.approx(7.647, abs=0.01)
        assert result.holding_days == 30

    def test_trim_creates_partial_history(self, seed_data):
        """Trimming creates history with exit_shares < entry_shares."""
        db = seed_data
        pf = Portfolio(total_portfolio_id=1, name="Test", currency="JPY", total_capital=10_000_000,
                       risk_pct=1.0, default_stop_loss_pct=8.0,
                       stop_loss_method="fixed_pct", max_positions=8,
                       source="manual", status="active")
        db.add(pf)
        db.commit()

        hist = PositionHistory(
            portfolio_id=pf.id, symbol_id=1,
            entry_date=date(2026, 4, 1), entry_price=170.0,
            entry_shares=100,
            exit_date=date(2026, 4, 15), exit_price=185.0,
            exit_shares=33,  # Trim
            exit_reason="take_profit_trim",
            pnl_pct=8.824,
            pnl_amount=495.0,
            holding_days=14,
        )
        db.add(hist)
        db.commit()

        result = db.query(PositionHistory).first()
        assert result.exit_shares == 33
        assert result.exit_reason == "take_profit_trim"

    def test_multiple_history_for_same_symbol(self, seed_data):
        """Multiple sells (trim + final) create multiple history records."""
        db = seed_data
        pf = Portfolio(total_portfolio_id=1, name="Test", currency="JPY", total_capital=10_000_000,
                       risk_pct=1.0, default_stop_loss_pct=8.0,
                       stop_loss_method="fixed_pct", max_positions=8,
                       source="manual", status="active")
        db.add(pf)
        db.commit()

        # First trim
        h1 = PositionHistory(portfolio_id=pf.id, symbol_id=1,
                             entry_date=date(2026, 4, 1), entry_price=170.0,
                             entry_shares=100,
                             exit_date=date(2026, 4, 15), exit_price=185.0,
                             exit_shares=33, exit_reason="take_profit_trim",
                             pnl_pct=8.824, pnl_amount=495.0, holding_days=14)
        # Final sell
        h2 = PositionHistory(portfolio_id=pf.id, symbol_id=1,
                             entry_date=date(2026, 4, 1), entry_price=170.0,
                             entry_shares=67,
                             exit_date=date(2026, 5, 1), exit_price=190.0,
                             exit_shares=67, exit_reason="take_profit_full",
                             pnl_pct=11.765, pnl_amount=1340.0, holding_days=30)
        db.add_all([h1, h2])
        db.commit()

        records = db.query(PositionHistory).filter_by(
            portfolio_id=pf.id, symbol_id=1).all()
        assert len(records) == 2
