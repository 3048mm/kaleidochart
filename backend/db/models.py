from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, BigInteger, ForeignKey, UniqueConstraint, SmallInteger, Index
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
    next_earnings_date = Column(Date, nullable=True) # Next earnings announcement date
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('ticker', 'exchange', name='uq_symbols_ticker_exchange'),
        Index('ix_symbols_active_category', 'active', 'category'),
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
    adr_pct_21       = Column(Float)     # Average Daily Range %
    change_1d_pct    = Column(Float)     # Daily Change % (Close-to-Close)
    change_1w_pct    = Column(Float)     # Weekly Change % (5-day)
    change_1m_pct    = Column(Float)     # Monthly Change % (20-day)
    sma50_atr_mult   = Column(Float)     # (Close% from SMA50) / ATR% = ATR-multiple distance from SMA50
    
    # --- Fundamentals & Size ---
    # market_cap moved to DailyPrice
    
    # --- Relative Strength (vs SPY) ---
    rs_value          = Column(Float)     # Raw RS: close / spy_close
    rs_trend_s5       = Column(Float)     # RS Trend: rs_value_e5 / SMA(rs_value, 5)
    rs_trend_s14      = Column(Float)     # RS Trend: rs_value_e5 / SMA(rs_value, 14)
    rs_trend_s21      = Column(Float)     # RS Trend: rs_value_e5 / SMA(rs_value, 21)
    rs_trend_s63      = Column(Float)     # RS Trend: rs_value_e5 / SMA(rs_value, 63)
    rs_trend_s200     = Column(Float)     # RS Trend: rs_value_e5 / SMA(rs_value, 200)
    rs_value_e5       = Column(Float)     # 5-day EMA of rs_value (smoothing for rs_trend)
    rs_value_e14      = Column(Float)     # 14-day EMA of rs_value (RRG pre-processing)
    rs_value_e21      = Column(Float)     # 21-day EMA of rs_value (RRG pre-processing)
    rs_value_e63      = Column(Float)     # 63-day EMA of rs_value (RRG pre-processing)
    rs_value_e200     = Column(Float)     # 200-day EMA of rs_value (RRG pre-processing)
    rs_momentum_e5    = Column(Float)     # RRG RS-Momentum (EMA-5)
    rs_momentum_e14   = Column(Float)     # RRG RS-Momentum (Z-score of smoothed RS-Ratio, EMA-14)
    rs_momentum_e21   = Column(Float)     # RRG RS-Momentum (Z-score of smoothed RS-Ratio, EMA-21)
    rs_momentum_e63   = Column(Float)     # RRG RS-Momentum (Z-score of smoothed RS-Ratio, EMA-63)
    rs_momentum_e200  = Column(Float)     # RRG RS-Momentum (EMA-200)
    rs_ratio_e5       = Column(Float)     # RS-Ratio: Z-score of rs_value_e5 (5d)
    rs_ratio_e14     = Column(Float)     # RS-Ratio: Z-score of rs_value_e14 (RRG X-axis, 14d)
    rs_ratio_e21     = Column(Float)     # RS-Ratio: Z-score of rs_value_e21 (RRG X-axis, 21d)
    rs_ratio_e63     = Column(Float)     # RS-Ratio: Z-score of rs_value_e63 (RRG X-axis, 63d)
    rs_ratio_e200    = Column(Float)     # RS-Ratio: Z-score of rs_value_e200 (200d)
    rs_roc_ema_5     = Column(Float)     # EMA of the 14-day ROC of rs_ratio_e5
    rs_roc_ema_14    = Column(Float)     # EMA of the 14-day ROC of rs_ratio_e14 (intermediate)
    rs_roc_ema_21    = Column(Float)     # EMA of the 14-day ROC of rs_ratio_e21 (intermediate)
    rs_roc_ema_63    = Column(Float)     # EMA of the 14-day ROC of rs_ratio_e63 (intermediate)
    rs_roc_ema_200   = Column(Float)     # EMA of the 14-day ROC of rs_ratio_e200
    
    # --- RS-MACD(5, 21, 5) ---
    rs_macd_line_21   = Column(Float)     # rs_value_e5 - rs_value_e21
    rs_macd_signal_21 = Column(Float)     # EMA(rs_macd_line_21, 5)
    rs_macd_hist_21   = Column(Float)     # rs_macd_line_21 - rs_macd_signal_21
    
    # --- Volume ---
    vol_surge_21          = Column(Float)    # Volume / SMA(Volume, 21)
    vol_surge_rel_spy_21  = Column(Float)    # vol_surge_21 / SPY_vol_surge_21
    up_down_vol_ratio_50  = Column(Float)    # Sum(Vol on Up days) / Sum(Vol on Down days, 50d)
    vol_accum_days_5      = Column(Integer)  # Accumulation days in last 5d (Close>Prev & Vol>1.1x SMA21)
    avg_dollar_volume_21  = Column(Float)    # (close*volume) の21日平均。全戦略共通の流動性ハード制約用
    
    # --- Price Range from Highs ---
    dist_63d_high_pct  = Column(Float)   # % below 63-day high (swing)
    dist_52w_high_pct  = Column(Float)   # % below 52-week / 252-day high (long)
    
    # --- RS Leading Signals ---
    # 経過日数カウンタ: 0=当日点灯 / n=n営業日前に点灯 / 999=未点灯・無効
    # （上限60日で999に飽和。反対ドット点灯で即999。履歴252本未満は999）
    # 旧 is_rs_blue_dot / is_rs_red_dot（0/1 フラグ）からの改名。0 の意味が
    # 「非点灯」→「当日点灯」に反転するため、同名での意味変更は禁止した
    rs_blue_dot_age    = Column(SmallInteger) # RS が 252日新高値・株価は未達（強気先行）
    rs_red_dot_age     = Column(SmallInteger) # RS が 252日新安値・株価は未達（弱気先行）

    # --- Volatility Contraction ---
    vcr                = Column(Float)        # Volatility Contraction Ratio = ATR(10) / ATR(50)

    # --- Trend Quality ---
    is_trend_template  = Column(SmallInteger)  # 1 if all Minervini Trend Template conditions met

    # --- Structure Pivot (LL-HL) ---
    # 構造が生きていないバーは NULL。確定遅延を保つため、HL 確定バー以前も NULL
    sp_pivot           = Column(Float)        # ブレイクアウト水準（LL と HL の間の最高値）
    sp_hl              = Column(Float)        # HL の価格（そのまま損切り候補）
    # カウンタートレンド線。**構造が生きていない期間にだけ**引かれるので、
    # sp_pivot とは排他（どちらか一方が NULL になる）
    sp_counter         = Column(Float)        # 下向きの抵抗線。上抜けが Trend Line Break
    
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
    
    # Percentile ranks (0.00 to 1.00) within the group
    rs_value_rank        = Column(Float)  # rank of rs_value
    rs_ratio_rank_e5     = Column(Float)  # rank of rs_ratio_e5
    rs_ratio_rank_e14    = Column(Float)  # rank of rs_ratio_e14
    rs_ratio_rank_e21    = Column(Float)  # rank of rs_ratio_e21
    rs_ratio_rank_e63    = Column(Float)  # rank of rs_ratio_e63
    rs_ratio_rank_e200   = Column(Float)  # rank of rs_ratio_e200
    rs_momentum_rank_e5  = Column(Float)  # rank of rs_momentum_e5
    rs_momentum_rank_e14 = Column(Float)  # rank of rs_momentum_e14
    rs_momentum_rank_e21 = Column(Float)  # rank of rs_momentum_e21
    rs_momentum_rank_e63 = Column(Float)  # rank of rs_momentum_e63
    rs_momentum_rank_e200 = Column(Float) # rank of rs_momentum_e200
    rs_trend_rank_s5     = Column(Float)  # rank of rs_trend_s5
    rs_trend_rank_s14    = Column(Float)  # rank of rs_trend_s14
    rs_trend_rank_s21    = Column(Float)  # rank of rs_trend_s21
    rs_trend_rank_s63    = Column(Float)  # rank of rs_trend_s63
    rs_trend_rank_s200   = Column(Float)  # rank of rs_trend_s200
    rs_roc_ema_rank_e5   = Column(Float)  # rank of rs_roc_ema_5
    rs_roc_ema_rank_e14  = Column(Float)  # rank of rs_roc_ema_14
    rs_roc_ema_rank_e21  = Column(Float)  # rank of rs_roc_ema_21
    rs_roc_ema_rank_e63  = Column(Float)  # rank of rs_roc_ema_63
    rs_roc_ema_rank_e200 = Column(Float)  # rank of rs_roc_ema_200
    
    # --- RS-MACD Ranks ---
    rs_macd_hist_rank_21 = Column(Float)  # rank of rs_macd_hist_21
    
    __table_args__ = (
        UniqueConstraint('symbol_id', 'date', 'group_name', name='uq_relative_ranks_symbol_date_group'),
        Index('ix_relative_ranks_date_group', 'date', 'group_name'),
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
    is_distribution_day = Column(SmallInteger, default=0)  # 1 = Dist day occurred today
    
    # Follow Through Day signal
    follow_through_day = Column(SmallInteger, default=0)  # 1 = FTD occurred
    
    # Overall market phase
    # BULL | CORRECTION | RALLY_ATTEMPT | BEAR
    market_phase       = Column(String)
    
    # 0-100 market trend score
    market_trend_score  = Column(Float)
    
    # NEW: VXV/VIX ratio
    vxv_vix_ratio       = Column(Float)
    
    # Market breadth: fraction of active individual stocks above SMA50 (0.0 - 1.0)
    breadth_sma50       = Column(Float)
    
    created_at = Column(DateTime, default=datetime.utcnow)

class PipelineMeta(Base):
    """Execution metadata for self-determining 2-phase update pipeline."""
    __tablename__ = 'pipeline_meta'
    
    id = Column(Integer, primary_key=True)
    last_completed_at = Column(DateTime, nullable=False) # Start time of the last successful pipeline run (UTC)
    last_spy_date = Column(Date, nullable=False)          # SPY's latest date at completion (YYYY-MM-DD)

class FxRate(Base):
    """Stores historical exchange rates (e.g. USD/JPY for currency conversion)"""
    __tablename__ = 'fx_rates'
    
    id = Column(Integer, primary_key=True)
    currency_pair = Column(String(10), nullable=False, index=True)  # e.g., "USD/JPY"
    date = Column(Date, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('currency_pair', 'date', name='uq_fx_rates_pair_date'),
    )



