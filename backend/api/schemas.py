from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import date, datetime

class SymbolResponse(BaseModel):
    id: int
    ticker: str
    exchange: Optional[str] = None
    name: str
    category: Optional[str] = None
    asset_class: Optional[str] = None
    theme_type: Optional[str] = None
    tags: Optional[str] = None

    class Config:
        from_attributes = True

class ChartDataPoint(BaseModel):
    # Base T2 daily data
    time: str  # TradingView expects string 'YYYY-MM-DD' or unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: int
    
    # Optional T3 Indicators attached to the same date
    sma_5: Optional[float] = None
    sma_21: Optional[float] = None
    sma_50: Optional[float] = None
    sma_63: Optional[float] = None
    sma_150: Optional[float] = None
    sma_200: Optional[float] = None
    
    ema_5: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_63: Optional[float] = None
    ema_150: Optional[float] = None
    ema_200: Optional[float] = None
    
    td9: Optional[int] = None
    atr_14: Optional[float] = None
    atr_pct_14: Optional[float] = None
    adr_pct_21: Optional[float] = None
    change_1d_pct: Optional[float] = None
    change_1w_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    
    relative_strength_spy: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_ema_14: Optional[float] = None
    rs_ema_21: Optional[float] = None
    rs_ema_63: Optional[float] = None
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    
    vol_surge_21: Optional[float] = None
    rel_vol_vs_spy_21: Optional[float] = None
    pct_from_63d_high: Optional[float] = None
    pct_from_52w_high: Optional[float] = None
    trend_template_ok: Optional[int] = None
    market_cap: Optional[float] = None
    
    up_down_vol_ratio_50: Optional[float] = None
    rs_blue_dot: Optional[int] = None
    rs_red_dot: Optional[int] = None
    vcr: Optional[float] = None
    
    # Bollinger Bands (Calculated on the fly)
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    
    # Optional Relative Ranks
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_14: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None
    rank_rs_condition_14: Optional[float] = None
    rank_rs_condition_21: Optional[float] = None
    rank_rs_condition_63: Optional[float] = None

class ChartSymbolMeta(BaseModel):
    id: int
    ticker: str
    name: str

class ChartResponse(BaseModel):
    metadata: ChartSymbolMeta
    themes: List[ChartSymbolMeta] = []
    data: List[ChartDataPoint]

class RankingItem(BaseModel):
    symbol_id: int
    ticker: str
    name: str
    group_name: str
    indicator_name: str
    percent_rank: float
    date: str
    
    class Config:
        from_attributes = True

class DashboardPanelItem(BaseModel):
    id: int
    ticker: str
    name: str
    category: Optional[str] = None
    close: float
    change_pct: float
    change_1w_pct: float = 0.0
    change_1m_pct: float = 0.0
    dist_21ema_pct: float = 0.0
    sparkline: List[float] = [] # Array of normalized logic values for the SVG chart
    intensity_score: float = 0.0 # 0 to 1 scaling factor 
    rs_ratio_21_rank: float = 0.0
    rs_ratio_63_rank: float = 0.0
    rs_ratio_14_rank: float = 0.0
    rs_momentum_21_rank: float = 0.0
    rs_momentum_63_rank: float = 0.0
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_21: Optional[float] = None

class ScreenerResultItem(DashboardPanelItem):
    vol_surge_21: Optional[float] = None
    adr_pct_21: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    trend_template_ok: Optional[int] = None
    market_cap: Optional[float] = None
    up_down_vol_ratio_50: Optional[float] = None
    rs_blue_dot: Optional[int] = None
    rs_red_dot: Optional[int] = None
    vcr: Optional[float] = None

class ScreenerDashboardItem(BaseModel):
    id: int
    ticker: str
    name: str
    change_pct: float
    theme_ticker: Optional[str] = None
    theme_name: Optional[str] = None
    theme_rs_ratio: Optional[float] = None

class ScreenerDashboardCategory(BaseModel):
    id: str
    name: str
    subname: Optional[str] = None
    subtitle: Optional[str] = None
    group: str
    items: List[ScreenerDashboardItem]

class ScreenerDashboardResponse(BaseModel):
    rise: List[ScreenerDashboardCategory]
    fall: List[ScreenerDashboardCategory]

class LeadingIndicatorItem(BaseModel):
    id: int
    ticker: str
    name: str
    category: Optional[str] = None
    close: float
    change_1d_pct: float
    change_1w_pct: float
    change_1m_pct: float
    dist_21ema_pct: float
    sparkline: List[float] = []

class EtfFeatureItem(BaseModel):
    id: int
    ticker: str
    name: str
    close: float
    change_1d_pct: float
    change_1w_pct: float
    change_1m_pct: float
    change_1y_pct: float
    dist_sma5_pct: float
    dist_sma21_pct: float
    dist_sma63_pct: float
    sma21_sma63_pct: float
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs14_sparkline: List[float] = []
    rs21_sparkline: List[float] = []
    rs63_sparkline: List[float] = []
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    chart_data: List[ChartDataPoint] = []

class RankingResponse(BaseModel):
    indicator_name: str
    items: List[RankingItem]

class MarketTrendScoreHistoryItem(BaseModel):
    date: str
    score: float
    vxv_vix_ratio: Optional[float] = None
    distribution_days: Optional[int] = None
    is_distribution_day: Optional[int] = None
    follow_through_day: Optional[int] = None

class DashboardResponse(BaseModel):
    date: str
    market_phase: str
    distribution_days: int
    market_trend_score: float = 0.0
    vxv_vix_ratio: Optional[float] = None
    trend_score_history: List[MarketTrendScoreHistoryItem] = []
    spy_feature: Optional[EtfFeatureItem] = None
    leading: List[LeadingIndicatorItem] = []
    indices: List[DashboardPanelItem] = []
    sectors: List[DashboardPanelItem] = []
    themes_top: List[DashboardPanelItem] = []
    themes_bottom: List[DashboardPanelItem] = []

class ThemeConstituentItem(BaseModel):
    id: int
    ticker: str
    name: str
    close: float
    change_1d_pct: float = 0.0
    change_1w_pct: float = 0.0
    change_1m_pct: float = 0.0
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None
    rs_sparkline: List[float] = []  # rs_ratio_21 history for minimap
    chart_data: List[ChartDataPoint] = []  # For RRG usage

class ThemeDetailResponse(BaseModel):
    id: int
    ticker: str
    name: str
    close: float
    change_1d_pct: float = 0.0
    change_1w_pct: float = 0.0
    change_1m_pct: float = 0.0
    dist_sma5_pct: float = 0.0
    dist_sma21_pct: float = 0.0
    dist_sma63_pct: float = 0.0
    sma21_sma63_pct: float = 0.0
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_roc_ema_14: Optional[float] = None
    rs_roc_ema_21: Optional[float] = None
    rs_roc_ema_63: Optional[float] = None
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    rs_ema_14: Optional[float] = None
    rs_ema_21: Optional[float] = None
    rs_ema_63: Optional[float] = None
    adr_pct_21: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    rs14_sparkline: List[float] = []
    rs21_sparkline: List[float] = []
    rs63_sparkline: List[float] = []
    # Ranks for the theme ETF itself
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_14: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None
    rank_rs_condition_14: Optional[float] = None
    rank_rs_condition_21: Optional[float] = None
    rank_rs_condition_63: Optional[float] = None
    chart_data: List[ChartDataPoint] = []  # 6-month OHLCV for MiniChart
    constituents: List[ThemeConstituentItem] = []

class GroupDataResponse(BaseModel):
    ticker: str
    name: str
    group_type: str  # 'sector' | 'theme'
    feature: EtfFeatureItem
    constituents: List[DashboardPanelItem]

class EarningResponse(BaseModel):
    period_date: str
    eps_basic: Optional[float] = None
    eps_diluted: Optional[float] = None
    revenue: Optional[float] = None
    net_income: Optional[float] = None

    class Config:
        from_attributes = True

# --- Screener Presets / Meta ---

class ScreenerPresetItem(BaseModel):
    id: str
    name: str
    subname: Optional[str] = None
    subtitle: Optional[str] = None
    group: str
    filters: dict = {}
    expression: Optional[str] = None
    special: Optional[str] = None

class ScreenerPresetsResponse(BaseModel):
    rise: List[ScreenerPresetItem]
    fall: List[ScreenerPresetItem]

class ScreenerColumnMeta(BaseModel):
    name: str
    label: str
    category: str
    type: str  # 'float', 'int'
    step: Optional[float] = None

class ScreenerMetaResponse(BaseModel):
    columns: List[ScreenerColumnMeta]
    rank_indicators: List[str]
    virtual_columns: List[ScreenerColumnMeta]

class AvailableDatesResponse(BaseModel):
    dates: List[str]

class SystemInfoResponse(BaseModel):
    db_path: str
    db_name: str
    is_production: bool


# --- Watchlist ---

class WatchlistAddRequest(BaseModel):
    ticker: str
    entry_date: date

class WatchlistUpdateRequest(BaseModel):
    entry_date: date

class WatchlistBulkDeleteRequest(BaseModel):
    tickers: List[str]

class WatchlistItem(BaseModel):
    id: int
    symbol_id: int
    ticker: str
    name: str
    entry_date: str           # YYYY-MM-DD
    entry_price: float
    latest_close: float
    latest_ema_21: float
    gain_pct: float
    max_gain_pct: float
    min_gain_pct: float
    latest_adr_pct: float
    latest_dist_sma50_atr: float
    rs_sparkline: List[float]
    next_earnings_date: Optional[date] = None
    status: str               # 'active' | 'removed'
    added_at: str
    removed_at: Optional[str] = None
    removed_price: Optional[float] = None

class WatchlistResponse(BaseModel):
    active: List[WatchlistItem]
    removed: List[WatchlistItem]


# --- Portfolio ---

class PortfolioCreateRequest(BaseModel):
    name: str
    currency: str = "JPY"
    total_capital: float
    risk_pct: float = 1.0
    default_stop_loss_pct: float = 8.0
    stop_loss_method: str = "fixed_pct"
    atr_multiplier: float = 2.0
    profit_take_method: Optional[str] = None
    max_positions: int = 8

class PortfolioUpdateRequest(BaseModel):
    name: Optional[str] = None
    currency: Optional[str] = None
    total_capital: Optional[float] = None
    risk_pct: Optional[float] = None
    default_stop_loss_pct: Optional[float] = None
    stop_loss_method: Optional[str] = None
    atr_multiplier: Optional[float] = None
    profit_take_method: Optional[str] = None
    max_positions: Optional[int] = None

class PositionAddRequest(BaseModel):
    ticker: str
    entry_date: date
    shares: float
    entry_price: Optional[float] = None
    memo: Optional[str] = None
    stop_loss_pct: Optional[float] = None
    custom_take_profit_pct: Optional[float] = None

class PositionUpdateRequest(BaseModel):
    entry_date: Optional[date] = None
    entry_price: Optional[float] = None
    shares: Optional[float] = None

class PositionSellRequest(BaseModel):
    exit_date: date
    exit_price: float
    exit_shares: float
    exit_reason: str  # stop_loss / take_profit_trim / take_profit_full / trailing_stop / manual
    memo: Optional[str] = None

class PositionHistoryUpdateRequest(BaseModel):
    entry_date: Optional[date] = None
    entry_price: Optional[float] = None
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_shares: Optional[float] = None
    memo: Optional[str] = None

class TransactionRequest(BaseModel):
    transaction_type: str
    amount: float
    local_amount: Optional[float] = None
    exchange_rate: Optional[float] = None
    local_currency: Optional[str] = None
    date: date
    memo: Optional[str] = None


# --- Backtest Results (Phase 1) ---

class BacktestScenarioSummary(BaseModel):
    cagr: float
    profit_factor: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    yearly_performance: Optional[dict] = None
    # Allow extra fields for safety
    model_config = {
        "extra": "allow"
    }

class BacktestEquityPoint(BaseModel):
    date: str
    equity: float
    cash: float
    spy_equity: Optional[float] = None
    trend_score: Optional[float] = None

class BacktestTradeLogItem(BaseModel):
    date: str
    ticker: str
    action: str
    price: float
    size: float
    reason: str
    pnl_pct: float

