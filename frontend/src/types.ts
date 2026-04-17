export interface Symbol {
    id: number
    ticker: string
    exchange: string | null
    name: string
    category: string | null
    asset_class: string | null
    theme_type?: string | null
    tags?: string | null
}

export interface ChartDataPoint {
    time: string
    open: number
    high: number
    low: number
    close: number
    volume: number
    sma_5?: number | null
    sma_21?: number | null
    sma_50?: number | null
    sma_63?: number | null
    sma_150?: number | null
    sma_200?: number | null
    ema_5?: number | null
    ema_21?: number | null
    ema_50?: number | null
    ema_63?: number | null
    ema_150?: number | null
    ema_200?: number | null
    td9?: number | null
    atr_14?: number | null
    atr_pct_14?: number | null
    adr_pct_21?: number | null
    dist_sma50_atr?: number | null
    relative_strength_spy?: number | null
    rs_condition_14?: number | null
    rs_condition_21?: number | null
    rs_condition_63?: number | null
    rs_momentum_14?: number | null
    rs_momentum_21?: number | null
    rs_momentum_63?: number | null
    rs_ratio_14?: number | null
    rs_ratio_21?: number | null
    rs_ratio_63?: number | null
    vol_surge_21?: number | null
    rel_vol_vs_spy_21?: number | null
    pct_from_63d_high?: number | null
    pct_from_52w_high?: number | null
    trend_template_ok?: number | null
    market_cap?: number | null

    // Bollinger Bands (Calculated on the fly)
    bb_upper?: number | null
    bb_lower?: number | null

    // Relative Ranks
    rank_rs_ratio_14?: number | null
    rank_rs_ratio_21?: number | null
    rank_rs_ratio_63?: number | null
    rank_rs_momentum_14?: number | null
    rank_rs_momentum_21?: number | null
    rank_rs_momentum_63?: number | null
    rank_rs_condition_14?: number | null
    rank_rs_condition_21?: number | null
    rank_rs_condition_63?: number | null
}

export interface ChartSymbolMeta {
    id: number;
    ticker: string;
    name: string;
}

export interface ChartResponse {
    metadata: ChartSymbolMeta;
    themes: ChartSymbolMeta[];
    data: ChartDataPoint[];
}

export interface RankingItem {
    symbol_id: number
    ticker: string
    name: string
    group_name: string
    indicator_name: string
    percent_rank: number
    date: string
}

export interface DashboardPanelItem {
    id: number;
    ticker: string;
    name: string;
    category?: string;
    close: number;
    change_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    dist_21ema_pct: number;
    sparkline: number[];
    intensity_score: number;
    rs_ratio_21_rank: number;
    rs_ratio_63_rank: number;
    rs_ratio_21?: number;
    rs_ratio_63?: number;
    rs_momentum_21?: number;
}

export interface ScreenerResultItem extends DashboardPanelItem {
    vol_surge_21?: number | null;
    adr_pct_21?: number | null;
    dist_sma50_atr?: number | null;
    trend_template_ok?: number | null;
    market_cap?: number | null;
}

export interface ScreenerDashboardItem {
    id: number;
    ticker: string;
    name: string;
    change_pct: number;
}

export interface ScreenerDashboardCategory {
    id: string;
    name: string;
    subtitle?: string;
    group: string;
    items: ScreenerDashboardItem[];
}

export interface ScreenerDashboardResponse {
    rise: ScreenerDashboardCategory[];
    fall: ScreenerDashboardCategory[];
}

export interface LeadingIndicatorItem {
    id: number;
    ticker: string;
    name: string;
    category?: string | null;
    close: number;
    change_1d_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    dist_21ema_pct: number;
    sparkline: number[];
}

export interface SpyFeatureItem {
    id: number;
    ticker: string;
    name: string;
    close: number;
    change_1d_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    change_1y_pct: number;
    dist_sma5_pct: number;
    dist_sma21_pct: number;
    dist_sma63_pct: number;
    sma21_sma63_pct: number;
    chart_data: ChartDataPoint[];
}

export interface MarketTrendScoreHistoryItem {
    date: string;
    score: number;
}

export interface DashboardResponse {
    date: string
    market_phase: string
    distribution_days: number
    market_trend_score: number
    trend_score_history: MarketTrendScoreHistoryItem[]
    spy_feature?: SpyFeatureItem
    leading: LeadingIndicatorItem[]
    indices: DashboardPanelItem[]
    sectors: DashboardPanelItem[]
    themes_top: DashboardPanelItem[]
    themes_bottom: DashboardPanelItem[]
}

export interface ThemeConstituentItem {
    id: number;
    ticker: string;
    name: string;
    close: number;
    change_1d_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    rs_ratio_14?: number | null;
    rs_ratio_21?: number | null;
    rs_ratio_63?: number | null;
    rs_momentum_21?: number | null;
    rs_sparkline: number[];
    chart_data: ChartDataPoint[];
}

export interface ThemeDetailResponse {
    id: number;
    ticker: string;
    name: string;
    close: number;
    change_1d_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    dist_sma5_pct: number;
    dist_sma21_pct: number;
    dist_sma63_pct: number;
    sma21_sma63_pct: number;
    rs_ratio_14?: number | null;
    rs_ratio_21?: number | null;
    rs_ratio_63?: number | null;
    rs_momentum_14?: number | null;
    rs_momentum_21?: number | null;
    rs_momentum_63?: number | null;
    rs_condition_14?: number | null;
    rs_condition_21?: number | null;
    rs_condition_63?: number | null;
    adr_pct_21?: number | null;
    dist_sma50_atr?: number | null;
    rs14_sparkline: number[];
    rs21_sparkline: number[];
    rs63_sparkline: number[];
    chart_data: ChartDataPoint[];
    constituents: ThemeConstituentItem[];
}

export interface EarningData {
    period_date: string;
    eps_basic: number | null;
    eps_diluted: number | null;
    revenue: number | null;
    net_income: number | null;
}


export interface WatchlistItem {
    id: number;
    symbol_id: number;
    ticker: string;
    name: string;
    entry_date: string;
    entry_price: number;
    latest_close: number;
    latest_ema_21: number;
    gain_pct: number;
    max_gain_pct: number;
    min_gain_pct: number;
    latest_adr_pct: number;
    latest_dist_sma50_atr: number;
    rs_sparkline: number[];
    status: 'active' | 'removed';
    added_at: string;
    removed_at: string | null;
    removed_price: number | null;
}

export interface WatchlistResponse {
    active: WatchlistItem[];
    removed: WatchlistItem[];
}
