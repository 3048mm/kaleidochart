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
    dist_sma50_atr: Optional[float] = None
    
    relative_strength_spy: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_ratio_14: Optional[float] = None
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    
    vol_surge_21: Optional[float] = None
    rel_vol_vs_spy_21: Optional[float] = None
    pct_from_63d_high: Optional[float] = None
    pct_from_52w_high: Optional[float] = None
    trend_template_ok: Optional[int] = None
    
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
    rs_ratio_21: Optional[float] = None
    rs_ratio_63: Optional[float] = None
    rs_momentum_21: Optional[float] = None

class ScreenerResultItem(DashboardPanelItem):
    vol_surge_21: Optional[float] = None
    adr_pct_21: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    trend_template_ok: Optional[int] = None
    market_cap: Optional[float] = None

class ScreenerDashboardItem(BaseModel):
    id: int
    ticker: str
    name: str
    change_pct: float

class ScreenerDashboardCategory(BaseModel):
    id: str
    name: str
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

class SpyFeatureItem(BaseModel):
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
    chart_data: List[ChartDataPoint] = []

class RankingResponse(BaseModel):
    indicator_name: str
    items: List[RankingItem]

class DashboardResponse(BaseModel):
    date: str
    market_phase: str
    distribution_days: int
    spy_feature: Optional[SpyFeatureItem] = None
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
    rs_momentum_14: Optional[float] = None
    rs_momentum_21: Optional[float] = None
    rs_momentum_63: Optional[float] = None
    rs_condition_14: Optional[float] = None
    rs_condition_21: Optional[float] = None
    rs_condition_63: Optional[float] = None
    adr_pct_21: Optional[float] = None
    dist_sma50_atr: Optional[float] = None
    rs14_sparkline: List[float] = []
    rs21_sparkline: List[float] = []
    rs63_sparkline: List[float] = []
    chart_data: List[ChartDataPoint] = []  # 6-month OHLCV for MiniChart
    constituents: List[ThemeConstituentItem] = []
