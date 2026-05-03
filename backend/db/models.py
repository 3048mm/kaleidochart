from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, BigInteger, ForeignKey, UniqueConstraint, SmallInteger
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Symbol(Base):
    __tablename__ = 'symbols'
    
    id = Column(Integer, primary_key=True)
    ticker = Column(String, index=True, nullable=False)
    exchange = Column(String, nullable=True) # NYSEARCA, BATS, NASDAQ, VIRTUAL etc.
    name = Column(String)
    category = Column(String)
    asset_class = Column(String)
    theme_type = Column(String, nullable=True)  # 'etf', 'virtual', None
    tags = Column(String, nullable=True)        # comma-separated tags
    active = Column(SmallInteger, default=1)    # 1=active, 0=inactive (soft delete)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('ticker', 'exchange', name='uq_symbols_ticker_exchange'),
    )
    
    # Relationships
    daily_prices = relationship("DailyPrice", back_populates="symbol", cascade="all, delete-orphan")
    indicators = relationship("Indicator", back_populates="symbol", cascade="all, delete-orphan")
    relative_ranks = relationship("RelativeRank", back_populates="symbol", cascade="all, delete-orphan")
    earnings = relationship("Earning", back_populates="symbol", cascade="all, delete-orphan")

class ThemeConstituent(Base):
    """Maps virtual theme tickers to their active constituent symbols"""
    __tablename__ = 'theme_constituents'
    
    id = Column(Integer, primary_key=True)
    theme_id = Column(Integer, ForeignKey('symbols.id'), nullable=False)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    weight = Column(Float, default=1.0)
    
    __table_args__ = (
        UniqueConstraint('theme_id', 'symbol_id', name='uq_theme_constituents_theme_symbol'),
    )

class DailyPrice(Base):
    __tablename__ = 'daily_prices'
    
    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger, default=0) # ^VIX etc can have 0 volume.
    market_cap = Column(Float)             # yfinance: close × shares_outstanding
    
    __table_args__ = (
        UniqueConstraint('symbol_id', 'date', name='uq_daily_prices_symbol_date'),
    )
    
    symbol = relationship("Symbol", back_populates="daily_prices")

class Earning(Base):
    """Historical quarterly fundamentals"""
    __tablename__ = 'earnings'
    
    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    period_date = Column(Date, nullable=False, index=True)
    eps_basic = Column(Float)
    eps_diluted = Column(Float)
    revenue = Column(Float)
    net_income = Column(Float)
    
    __table_args__ = (
        UniqueConstraint('symbol_id', 'period_date', name='uq_earnings_symbol_period'),
    )
    
    symbol = relationship("Symbol", back_populates="earnings")

class Indicator(Base):
    __tablename__ = 'indicators'
    
    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    
    # --- Simple Moving Averages ---
    sma_5   = Column(Float)
    sma_21  = Column(Float)
    sma_50  = Column(Float)
    sma_63  = Column(Float)
    sma_150 = Column(Float)  # NEW: required for Trend Template
    sma_200 = Column(Float)
    
    # --- Exponential Moving Averages ---
    ema_5   = Column(Float)
    ema_21  = Column(Float)
    ema_50  = Column(Float)
    ema_63  = Column(Float)
    ema_150 = Column(Float)
    ema_200 = Column(Float)
    
    # --- Volatility ---
    td9              = Column(Integer)   # -9 to +9
    atr_14           = Column(Float)     # ATR raw value
    atr_pct_14       = Column(Float)     # ATR% = ATR/Close*100
    adr_pct_21       = Column(Float)     # NEW: Average Daily Range %
    change_1d_pct    = Column(Float)     # NEW: Daily Change % (Close-to-Close)
    change_1w_pct    = Column(Float)     # NEW: Weekly Change % (5-day)
    change_1m_pct    = Column(Float)     # NEW: Monthly Change % (20-day)
    dist_sma50_atr   = Column(Float)     # NEW: (Close - SMA50) / ATR14
    
    # --- Fundamentals & Size ---
    # market_cap moved to DailyPrice
    
    # --- Relative Strength (vs SPY) ---
    relative_strength_spy = Column(Float)
    rs_condition_14   = Column(Float)     # NEW: RS-Condition (RS / SMA14(RS))
    rs_condition_21   = Column(Float)     # NEW: RS-Condition (RS / SMA21(RS))
    rs_condition_63   = Column(Float)     # NEW: RS-Condition (RS / SMA63(RS))
    rs_ema_14         = Column(Float)     # NEW: EMA of Relative Strength (Smoothing for RRG)
    rs_ema_21         = Column(Float)     # NEW: EMA of Relative Strength (Smoothing for RRG)
    rs_ema_63         = Column(Float)     # NEW: EMA of Relative Strength (Smoothing for RRG)
    rs_momentum_14   = Column(Float)     # NEW: RRG RS-Momentum (Z-score of smoothed RS-Ratio)
    rs_momentum_21   = Column(Float)     # NEW: RRG RS-Momentum (Z-score of smoothed RS-Ratio)
    rs_momentum_63   = Column(Float)     # NEW: RRG RS-Momentum (Z-score of smoothed RS-Ratio)
    rs_ratio_14      = Column(Float)     # NEW: RS-Ratio (Z-score of smoothed RS over 14 days)
    rs_ratio_21      = Column(Float)     # NEW: RS-Ratio (Z-score of smoothed RS over 21 days)
    rs_ratio_63      = Column(Float)     # NEW: RS-Ratio (Z-score of smoothed RS over 63 days)
    rs_roc_ema_14    = Column(Float)     # NEW: EMA of the 14-day ROC of RS-Ratio
    rs_roc_ema_21    = Column(Float)     # NEW: EMA of the 14-day ROC of RS-Ratio (smoothed by 21)
    rs_roc_ema_63    = Column(Float)     # NEW: EMA of the 14-day ROC of RS-Ratio (smoothed by 63)
    
    # --- Volume ---
    vol_surge_21     = Column(Float)     # NEW: Volume / SMA(Volume,21)
    rel_vol_vs_spy_21 = Column(Float)    # NEW: vol_surge_21 / SPY_vol_surge_21
    up_down_vol_ratio_50 = Column(Float) # NEW: Sum(Vol on Up days) / Sum(Vol on Down days)
    
    # --- Price Range from Highs ---
    pct_from_63d_high  = Column(Float)   # NEW: % below 63-day high (swing)
    pct_from_52w_high  = Column(Float)   # NEW: % below 52-week / 252-day high (long)
    
    # --- RS Leading Signals ---
    rs_blue_dot        = Column(SmallInteger) # NEW: 1 if RS at 252d high AND price is NOT at 252d high
    rs_red_dot         = Column(SmallInteger) # NEW: 1 if RS at 252d low AND price is NOT at 252d low

    # --- Volatility Contraction ---
    vcr                = Column(Float)        # NEW: Volatility Contraction Ratio = ATR(10) / ATR(50)

    # --- Trend Quality ---
    trend_template_ok  = Column(SmallInteger)  # NEW: 1 if all Trend Template conditions met
    
    __table_args__ = (
        UniqueConstraint('symbol_id', 'date', name='uq_indicators_symbol_date'),
    )
    
    symbol = relationship("Symbol", back_populates="indicators")

class RelativeRank(Base):
    __tablename__ = 'relative_ranks'
    
    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    group_name = Column(String, nullable=False)
    indicator_name = Column(String, nullable=False)
    percent_rank = Column(Float)  # 0.00 to 1.00
    
    __table_args__ = (
        UniqueConstraint('symbol_id', 'date', 'group_name', 'indicator_name', name='uq_relative_ranks_symbol_date_group_ind'),
    )
    
    symbol = relationship("Symbol", back_populates="relative_ranks")

class MarketSignal(Base):
    """T5: Daily market phase signals derived from SPY data."""
    __tablename__ = 'market_signals'
    
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, unique=True, index=True)
    
    # SPY trend status
    spy_above_sma200   = Column(SmallInteger)  # 1 = above, 0 = below
    spy_sma200_rising  = Column(SmallInteger)  # 1 = rising vs 20 days ago
    
    # Distribution Days (O'Neil method, last 25 trading days)
    distribution_days  = Column(Integer)
    
    # Follow Through Day signal
    follow_through_day = Column(SmallInteger, default=0)  # 1 = FTD occurred
    
    # Overall market phase
    # BULL | CORRECTION | RALLY_ATTEMPT | BEAR
    market_phase       = Column(String)
    
    # 0-100 market trend score
    market_trend_score  = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)

class Watchlist(Base):
    """User watchlist: tracks watched symbols with entry date and performance baseline."""
    __tablename__ = 'watchlist'

    id = Column(Integer, primary_key=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    entry_date = Column(Date, nullable=False)          # 指定日（パフォーマンス基準日）
    entry_price = Column(Float, nullable=False)        # 指定日の終値スナップショット
    status = Column(String, nullable=False, default='active')  # 'active' | 'removed'
    added_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # 登録操作日時
    removed_at = Column(DateTime, nullable=True)       # 解除操作日時
    removed_price = Column(Float, nullable=True)       # 解除時の最新終値スナップショット

    __table_args__ = (
        UniqueConstraint('symbol_id', name='uq_watchlist_symbol'),
    )


class Portfolio(Base):
    """User portfolio: manages a set of positions with risk parameters."""
    __tablename__ = 'portfolios'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)               # ポートフォリオ名
    currency = Column(String, nullable=False, default='JPY')  # JPY / USD
    total_capital = Column(Float, nullable=False)        # 総投資資金
    risk_pct = Column(Float, nullable=False, default=1.0)  # リスク許容% (例: 1.0)
    default_stop_loss_pct = Column(Float, nullable=False, default=8.0)  # デフォルト損切%
    stop_loss_method = Column(String, nullable=False, default='fixed_pct')  # fixed_pct / atr_multiple
    atr_multiplier = Column(Float, nullable=True, default=2.0)  # ATR倍率 (method=atr時)
    profit_take_method = Column(String, nullable=True)   # ema21 / sma50_atr / fixed_pct
    max_positions = Column(Integer, nullable=False, default=8)  # 最大同時保有数
    source = Column(String, nullable=False, default='manual')  # manual / moomoo_api
    status = Column(String, nullable=False, default='active')  # active / archived
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    positions = relationship("PortfolioPosition", back_populates="portfolio", cascade="all, delete-orphan")
    history = relationship("PositionHistory", back_populates="portfolio", cascade="all, delete-orphan")


class PortfolioPosition(Base):
    """Active position within a portfolio."""
    __tablename__ = 'portfolio_positions'

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False, index=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    entry_date = Column(Date, nullable=False)            # 購入日
    entry_price = Column(Float, nullable=False)          # 購入価格
    shares = Column(Integer, nullable=False)             # 現在の株数 (部分売却で減少)
    original_shares = Column(Integer, nullable=False)    # 購入時の株数
    stop_loss_pct = Column(Float, nullable=True)         # 個別損切% (NULL=ポートフォリオデフォルト)
    custom_take_profit_pct = Column(Float, nullable=True)  # 個別利確% (NULL=デフォルト)
    status = Column(String, nullable=False, default='open')  # open / partially_closed
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    portfolio = relationship("Portfolio", back_populates="positions")


class PositionHistory(Base):
    """Closed position history (sold or trimmed)."""
    __tablename__ = 'position_history'

    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False, index=True)
    symbol_id = Column(Integer, ForeignKey('symbols.id'), nullable=False, index=True)
    entry_date = Column(Date, nullable=False)            # 購入日
    entry_price = Column(Float, nullable=False)          # 購入価格
    entry_shares = Column(Integer, nullable=False)       # この売却分の元株数
    exit_date = Column(Date, nullable=False)             # 売却日
    exit_price = Column(Float, nullable=False)           # 売却価格
    exit_shares = Column(Integer, nullable=False)        # 売却株数
    exit_reason = Column(String, nullable=False)         # stop_loss / take_profit_trim / take_profit_full / trailing_stop / manual
    pnl_pct = Column(Float)                              # 損益%
    pnl_amount = Column(Float)                           # 損益額 (通貨単位)
    holding_days = Column(Integer)                       # 保有日数
    memo = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    portfolio = relationship("Portfolio", back_populates="history")

