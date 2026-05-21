from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

BaseUser = declarative_base()

class Watchlist(BaseUser):
    """User watchlist: tracks watched symbols with entry date and performance baseline."""
    __tablename__ = 'watchlist'

    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, nullable=True, index=True) # Cross-DB reference to symbols.id (nullable for self-healing)
    ticker = Column(String, nullable=False, index=True)    # For self-healing
    exchange = Column(String, nullable=True)               # For self-healing
    entry_date = Column(Date, nullable=False)
    entry_price = Column(Float, nullable=False)
    status = Column(String, nullable=False, default='active')
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)
    removed_price = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint('ticker', 'exchange', name='uq_watchlist_ticker_exchange'),
    )


class TotalPortfolio(BaseUser):
    """The master account that manages the overall capital and funds sub-portfolios."""
    __tablename__ = 'total_portfolios'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, default="Main Account")
    currency = Column(String, nullable=False, default='USD')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    portfolios = relationship("Portfolio", back_populates="total_portfolio", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="total_portfolio", cascade="all, delete-orphan")


class Transaction(BaseUser):
    """Deposit, Withdrawal, Funding, or Refund history."""
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True)
    total_portfolio_id = Column(Integer, ForeignKey('total_portfolios.id'), nullable=False, index=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True, index=True) # Null for external deposit/withdrawal
    transaction_type = Column(String, nullable=False) # 'DEPOSIT', 'WITHDRAWAL', 'FUNDING', 'REFUND'
    amount = Column(Float, nullable=False) # USD Amount
    local_amount = Column(Float, nullable=True) # Original JPY amount
    exchange_rate = Column(Float, nullable=True) # USD/JPY rate applied
    local_currency = Column(String, nullable=True) # e.g. 'JPY'
    date = Column(Date, nullable=False)
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    total_portfolio = relationship("TotalPortfolio", back_populates="transactions")
    portfolio = relationship("Portfolio", back_populates="transactions")


class Portfolio(BaseUser):
    """User sub-portfolio: manages a set of positions with risk parameters."""
    __tablename__ = 'portfolios'

    id = Column(Integer, primary_key=True)
    total_portfolio_id = Column(Integer, ForeignKey('total_portfolios.id'), nullable=False, index=True)
    name = Column(String, nullable=False)
    currency = Column(String, nullable=False, default='USD')
    total_capital = Column(Float, nullable=False) # Funded from TotalPortfolio
    risk_pct = Column(Float, nullable=False, default=1.0)
    default_stop_loss_pct = Column(Float, nullable=False, default=8.0)
    stop_loss_method = Column(String, nullable=False, default='fixed_pct')
    atr_multiplier = Column(Float, nullable=True, default=2.0)
    profit_take_method = Column(String, nullable=True)
    max_positions = Column(Integer, nullable=False, default=8)
    source = Column(String, nullable=False, default='manual')
    status = Column(String, nullable=False, default='active')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    total_portfolio = relationship("TotalPortfolio", back_populates="portfolios")
    transactions = relationship("Transaction", back_populates="portfolio", cascade="all, delete-orphan")
    positions = relationship("PortfolioPosition", back_populates="portfolio", cascade="all, delete-orphan")
    history = relationship("PositionHistory", back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioPosition(BaseUser):
    """Active position within a portfolio."""
    __tablename__ = 'portfolio_positions'

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False, index=True)
    symbol_id = Column(Integer, nullable=True, index=True) # Cross-DB (nullable for self-healing)
    ticker = Column(String, nullable=False, index=True)    # For self-healing
    exchange = Column(String, nullable=True)               # For self-healing
    entry_date = Column(Date, nullable=False)
    entry_price = Column(Float, nullable=False)
    shares = Column(Float, nullable=False)
    original_shares = Column(Float, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    custom_take_profit_pct = Column(Float, nullable=True)
    status = Column(String, nullable=False, default='open')
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    portfolio = relationship("Portfolio", back_populates="positions")


class PositionHistory(BaseUser):
    """Closed position history (sold or trimmed)."""
    __tablename__ = 'position_history'

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False, index=True)
    symbol_id = Column(Integer, nullable=True, index=True) # Cross-DB (nullable for self-healing)
    ticker = Column(String, nullable=False, index=True)    # For self-healing
    exchange = Column(String, nullable=True)               # For self-healing
    entry_date = Column(Date, nullable=False)
    entry_price = Column(Float, nullable=False)
    entry_shares = Column(Float, nullable=False)
    exit_date = Column(Date, nullable=False)
    exit_price = Column(Float, nullable=False)
    exit_shares = Column(Float, nullable=False)
    exit_reason = Column(String, nullable=False)
    pnl_pct = Column(Float)
    pnl_amount = Column(Float)
    holding_days = Column(Integer)
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    portfolio = relationship("Portfolio", back_populates="history")
