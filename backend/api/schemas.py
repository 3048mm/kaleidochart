from pydantic import BaseModel
from typing import List, Optional, Any, Dict
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
    sma50_atr_mult: Optional[float] = None
    
    rs_value: Optional[float] = None
    rs_trend_s5: Optional[float] = None
    rs_trend_s14: Optional[float] = None
    rs_trend_s21: Optional[float] = None
    rs_trend_s63: Optional[float] = None
    rs_trend_s200: Optional[float] = None
    rs_momentum_e5: Optional[float] = None
    rs_momentum_e14: Optional[float] = None
    rs_momentum_e21: Optional[float] = None
    rs_momentum_e63: Optional[float] = None
    rs_momentum_e200: Optional[float] = None
    rs_value_e5: Optional[float] = None
    rs_value_e14: Optional[float] = None
    rs_value_e21: Optional[float] = None
    rs_value_e63: Optional[float] = None
    rs_value_e200: Optional[float] = None
    rs_ratio_e5: Optional[float] = None
    rs_ratio_e14: Optional[float] = None
    rs_ratio_e21: Optional[float] = None
    rs_ratio_e63: Optional[float] = None
    rs_ratio_e200: Optional[float] = None
    
    # RS-MACD
    rs_macd_line_21: Optional[float] = None
    rs_macd_signal_21: Optional[float] = None
    rs_macd_hist_21: Optional[float] = None
    
    vol_surge_21: Optional[float] = None
    vol_surge_rel_spy_21: Optional[float] = None
    dist_63d_high_pct: Optional[float] = None
    dist_52w_high_pct: Optional[float] = None
    is_trend_template: Optional[int] = None
    market_cap: Optional[float] = None
    
    up_down_vol_ratio_50: Optional[float] = None
    is_rs_blue_dot: Optional[int] = None
    is_rs_red_dot: Optional[int] = None
    vcr: Optional[float] = None
    vol_accum_days_5: Optional[int] = None
    
    # Legacy fields for frontend backward compatibility
    dist_sma50_atr: Optional[float] = None
    relative_strength_spy: Optional[float] = None
    rs_ema_14: Optional[float] = None
    rs_ema_21: Optional[float] = None
    rs_ema_63: Optional[float] = None
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    rel_vol_vs_spy_21: Optional[float] = None
    pct_from_63d_high: Optional[float] = None
    pct_from_52w_high: Optional[float] = None
    trend_template_ok: Optional[int] = None
    rs_blue_dot: Optional[int] = None
    rs_red_dot: Optional[int] = None
    
    # Legacy Relative Ranks for backward compatibility
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_14: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None
    rank_rs_condition_14: Optional[float] = None
    rank_rs_condition_21: Optional[float] = None
    rank_rs_condition_63: Optional[float] = None
    
    # Bollinger Bands (Calculated on the fly)
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    
    # Optional Relative Ranks
    rs_ratio_rank_e14: Optional[float] = None
    rs_ratio_rank_e21: Optional[float] = None
    rs_ratio_rank_e63: Optional[float] = None
    rs_momentum_rank_e14: Optional[float] = None
    rs_momentum_rank_e21: Optional[float] = None
    rs_momentum_rank_e63: Optional[float] = None
    rs_trend_rank_s14: Optional[float] = None
    rs_trend_rank_s21: Optional[float] = None
    rs_trend_rank_s63: Optional[float] = None

class ChartSymbolMeta(BaseModel):
    id: int
    ticker: str
    name: str

class ChartResponse(BaseModel):
    metadata: ChartSymbolMeta
    themes: List[ChartSymbolMeta] = []
    data: List[ChartDataPoint]
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
    rs_ratio_rank_e21: float = 0.0
    rs_ratio_rank_e63: float = 0.0
    rs_ratio_rank_e14: float = 0.0
    rs_momentum_rank_e21: float = 0.0
    rs_momentum_rank_e63: float = 0.0
    rs_trend_rank_s14: float = 0.0
    rs_trend_rank_s21: float = 0.0
    rs_trend_rank_s63: float = 0.0
    rs_ratio_e21: Optional[float] = None
    rs_ratio_e63: Optional[float] = None
    rs_momentum_e21: Optional[float] = None
    
    # Legacy fields for frontend compatibility
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
    sma50_atr_mult: Optional[float] = None
    is_trend_template: Optional[int] = None
    market_cap: Optional[float] = None
    up_down_vol_ratio_50: Optional[float] = None
    is_rs_blue_dot: Optional[int] = None
    is_rs_red_dot: Optional[int] = None
    vcr: Optional[float] = None
    vol_accum_days_5: Optional[int] = None

    # Legacy fields for frontend compatibility
    dist_sma50_atr: Optional[float] = None
    trend_template_ok: Optional[int] = None
    rs_blue_dot: Optional[int] = None
    rs_red_dot: Optional[int] = None

class ScreenerDashboardItem(BaseModel):
    id: int
    ticker: str
    name: str
    change_pct: float
    theme_ticker: Optional[str] = None
    theme_name: Optional[str] = None
    theme_rs_ratio: Optional[float] = None
    rs_trend_history: List[float] = []

class ScreenerDashboardCategory(BaseModel):
    id: str
    name: str
    subname: Optional[str] = None
    subtitle: Optional[str] = None
    group: str
    items: List[ScreenerDashboardItem]
    error: Optional[str] = None  # プリセット構築失敗時のエラー内容（U-1 (b)。他カテゴリは正常表示を継続）
    applied_filters: Optional[List[str]] = None  # 実際に適用されたフィルタキー一覧（ソート済み。§5 Phase 1）

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
    dist_sma200_pct: float = 0.0
    rs_ratio_e14: Optional[float] = None
    rs_ratio_e21: Optional[float] = None
    rs_ratio_e63: Optional[float] = None
    rs14_sparkline: List[float] = []
    rs21_sparkline: List[float] = []
    rs63_sparkline: List[float] = []
    rs_ratio_rank_e14: Optional[float] = None
    rs_ratio_rank_e21: Optional[float] = None
    rs_ratio_rank_e63: Optional[float] = None
    chart_data: List[ChartDataPoint] = []

    # Legacy fields for frontend compatibility
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
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
    qqq_feature: Optional[EtfFeatureItem] = None
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
    rs_ratio_e14: Optional[float] = None
    rs_ratio_e21: Optional[float] = None
    rs_ratio_e63: Optional[float] = None
    rs_momentum_e21: Optional[float] = None
    rs_ratio_rank_e14: Optional[float] = None
    rs_ratio_rank_e21: Optional[float] = None
    rs_ratio_rank_e63: Optional[float] = None
    rs_momentum_rank_e21: Optional[float] = None
    rs_momentum_rank_e63: Optional[float] = None
    rs_sparkline: List[float] = []  # rs_ratio_e21 history for minimap
    chart_data: List[ChartDataPoint] = []  # For RRG usage

    # Legacy fields for frontend compatibility
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None

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
    dist_sma200_pct: float = 0.0
    rs_ratio_e5: Optional[float] = None
    rs_ratio_e14: Optional[float] = None
    rs_ratio_e21: Optional[float] = None
    rs_ratio_e63: Optional[float] = None
    rs_ratio_e200: Optional[float] = None
    rs_roc_ema_5: Optional[float] = None
    rs_roc_ema_14: Optional[float] = None
    rs_roc_ema_21: Optional[float] = None
    rs_roc_ema_63: Optional[float] = None
    rs_roc_ema_200: Optional[float] = None
    rs_momentum_e5: Optional[float] = None
    rs_momentum_e14: Optional[float] = None
    rs_momentum_e21: Optional[float] = None
    rs_momentum_e63: Optional[float] = None
    rs_momentum_e200: Optional[float] = None
    rs_trend_s5: Optional[float] = None
    rs_trend_s14: Optional[float] = None
    rs_trend_s21: Optional[float] = None
    rs_trend_s63: Optional[float] = None
    rs_trend_s200: Optional[float] = None
    
    # RS-MACD
    rs_macd_line_21: Optional[float] = None
    rs_macd_signal_21: Optional[float] = None
    rs_macd_hist_21: Optional[float] = None
    rs_value_e5: Optional[float] = None
    rs_value_e14: Optional[float] = None
    rs_value_e21: Optional[float] = None
    rs_value_e63: Optional[float] = None
    adr_pct_21: Optional[float] = None
    vol_accum_days_5: Optional[int] = None
    sma50_atr_mult: Optional[float] = None
    rs14_sparkline: List[float] = []
    rs21_sparkline: List[float] = []
    rs63_sparkline: List[float] = []
    # Ranks for the theme ETF itself
    rs_ratio_rank_e5: Optional[float] = None
    rs_ratio_rank_e14: Optional[float] = None
    rs_ratio_rank_e21: Optional[float] = None
    rs_ratio_rank_e63: Optional[float] = None
    rs_ratio_rank_e200: Optional[float] = None
    rs_momentum_rank_e5: Optional[float] = None
    rs_momentum_rank_e14: Optional[float] = None
    rs_momentum_rank_e21: Optional[float] = None
    rs_momentum_rank_e63: Optional[float] = None
    rs_momentum_rank_e200: Optional[float] = None
    rs_trend_rank_s5: Optional[float] = None
    rs_trend_rank_s14: Optional[float] = None
    rs_trend_rank_s21: Optional[float] = None
    rs_trend_rank_s63: Optional[float] = None
    rs_trend_rank_s200: Optional[float] = None
    rs_macd_hist_rank_21: Optional[float] = None
    chart_data: List[ChartDataPoint] = []  # 6-month OHLCV for MiniChart
    constituents: List[ThemeConstituentItem] = []

    # Legacy fields for frontend compatibility
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    rs_ema_14: Optional[float] = None
    rs_ema_21: Optional[float] = None
    rs_ema_63: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    rank_rs_ratio_14: Optional[float] = None
    rank_rs_ratio_21: Optional[float] = None
    rank_rs_ratio_63: Optional[float] = None
    rank_rs_momentum_14: Optional[float] = None
    rank_rs_momentum_21: Optional[float] = None
    rank_rs_momentum_63: Optional[float] = None
    rank_rs_condition_14: Optional[float] = None
    rank_rs_condition_21: Optional[float] = None
    rank_rs_condition_63: Optional[float] = None

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
    labels: Optional[Dict[str, str]] = None

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
    # 上場廃止・ticker変更等で symbols から解決できない場合は None（heal_watchlist_ids が NULL 化する）
    symbol_id: Optional[int] = None
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
    latest_sma50_atr_mult: float
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
    final_capital: Optional[float] = None
    yearly_performance: Optional[dict] = None
    exit_reasons: Optional[dict] = None
    is_monte_carlo: Optional[bool] = None
    runs_count: Optional[int] = None
    cagr_avg: Optional[float] = None
    cagr_max: Optional[float] = None
    cagr_min: Optional[float] = None
    max_drawdown_avg: Optional[float] = None
    max_drawdown_min: Optional[float] = None
    max_drawdown_max: Optional[float] = None
    win_rate_avg: Optional[float] = None
    total_trades_avg: Optional[int] = None
    profit_factor_avg: Optional[float] = None
    final_capital_avg: Optional[float] = None
    final_capital_max: Optional[float] = None
    final_capital_min: Optional[float] = None
    avg_trade_pnl_pct: Optional[float] = None
    avg_trade_pnl_pct_avg: Optional[float] = None
    # Allow extra fields for safety
    model_config = {
        "extra": "allow"
    }

class BacktestEquityPoint(BaseModel):
    date: str
    equity: float
    cash: float
    spy_equity: Optional[float] = None
    qqq_equity: Optional[float] = None
    tqqq_equity: Optional[float] = None
    soxl_equity: Optional[float] = None
    trend_score: Optional[float] = None
    run_equities: Optional[Dict[str, float]] = None

class BacktestTradeLogItem(BaseModel):
    date: str
    ticker: str
    action: str
    price: float
    size: float
    reason: str
    pnl_pct: float


# --- ETF Single Backtest Results ---

class EtfStrategyResult(BaseModel):
    final_capital: float
    total_return_pct: float
    cagr: float
    max_drawdown_pct: float
    max_drawdown_date: Optional[str] = None
    sharpe_ratio: float
    yearly_returns: Optional[Dict[str, float]] = None
    regime_changes: Optional[int] = None
    rebalance_count: Optional[int] = None
    time_in_market_pct: Optional[float] = None

class EtfSingleSummary(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float
    consider_tax: float
    trading_days: int
    strategies: Dict[str, EtfStrategyResult]
    model_config = {
        "extra": "allow"
    }

class EtfSingleEquityPoint(BaseModel):
    date: str
    vxv_equity: float
    buyhold_equity: float
    dca_equity: float
    vxv_position_pct: float
    regime: str
    mts_v3_raw_equity: Optional[float] = None
    mts_v3_raw_position_pct: Optional[float] = None
    mts_score: Optional[float] = None
    mts_ema5: Optional[float] = None
    based_sma200_equity: Optional[float] = None
    based_sma200_position_pct: Optional[float] = None
    based_sma63_equity: Optional[float] = None
    based_sma63_position_pct: Optional[float] = None
    model_config = {
        "extra": "allow"
    }

class EtfSingleRegimeItem(BaseModel):
    date: str
    regime: str
    vxv_vix_ratio: float
    ema5: Optional[float] = None
    ema21: Optional[float] = None
    position_pct: float

# --- Scenario Comparison Results ---

class ScenarioComparisonStrategyMetrics(BaseModel):
    final_capital: float
    cagr: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    profit_factor: float

class ScenarioComparisonSummary(BaseModel):
    start_date: str
    end_date: str
    strategies: Dict[str, ScenarioComparisonStrategyMetrics]

class ScenarioComparisonEquityPoint(BaseModel):
    date: str
    spy_equity: float
    equity_full_position: float
    equity_spy_sma200: float
    equity_spy_sma63: float
    equity_vxv_vix_ema: float
    equity_mts_raw: float

# --- System Health Visibility ---

class FreshnessInfo(BaseModel):
    daily_prices: Optional[str] = None
    indicators: Optional[str] = None
    relative_ranks: Optional[str] = None
    market_signals: Optional[str] = None
    spy_latest: Optional[str] = None
    delay_days: Optional[int] = None

class IntegrityInfo(BaseModel):
    latest_date: Optional[str] = None
    daily_prices_count: int
    indicators_count: int
    is_consistent: bool

class PipelineStatusInfo(BaseModel):
    is_running: bool
    last_completed_at: Optional[str] = None
    last_spy_date: Optional[str] = None

class ValidationInfo(BaseModel):
    presets_path: str
    is_valid: bool
    warnings: List[str]

class SystemHealthResponse(BaseModel):
    overall_status: str
    data_freshness: FreshnessInfo
    data_integrity: IntegrityInfo
    pipeline_status: PipelineStatusInfo
    validation: ValidationInfo
