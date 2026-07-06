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
    sma50_atr_mult?: number | null
    rs_value?: number | null
    rs_trend_s5?: number | null
    rs_trend_s14?: number | null
    rs_trend_s21?: number | null
    rs_trend_s63?: number | null
    rs_trend_s200?: number | null
    rs_value_e5?: number | null
    rs_value_e14?: number | null
    rs_value_e21?: number | null
    rs_value_e63?: number | null
    rs_value_e200?: number | null
    rs_momentum_e5?: number | null
    rs_momentum_e14?: number | null
    rs_momentum_e21?: number | null
    rs_momentum_e63?: number | null
    rs_momentum_e200?: number | null
    rs_ratio_e5?: number | null
    rs_ratio_e14?: number | null
    rs_ratio_e21?: number | null
    rs_ratio_e63?: number | null
    rs_ratio_e200?: number | null
    rs_roc_ema_5?: number | null
    rs_roc_ema_14?: number | null
    rs_roc_ema_21?: number | null
    rs_roc_ema_63?: number | null
    rs_roc_ema_200?: number | null
    rs_macd_line_21?: number | null
    rs_macd_signal_21?: number | null
    rs_macd_hist_21?: number | null
    vol_accum_days_5?: number | null
    vol_surge_21?: number | null
    vol_surge_rel_spy_21?: number | null
    dist_63d_high_pct?: number | null
    dist_52w_high_pct?: number | null
    is_trend_template?: number | null
    market_cap?: number | null
    up_down_vol_ratio_50?: number | null
    is_rs_blue_dot?: number | null
    is_rs_red_dot?: number | null
    vcr?: number | null

    // Bollinger Bands (Calculated on the fly)
    bb_upper?: number | null
    bb_lower?: number | null

    // Relative Ranks
    rs_value_rank?: number | null
    rs_ratio_rank_e5?: number | null
    rs_ratio_rank_e14?: number | null
    rs_ratio_rank_e21?: number | null
    rs_ratio_rank_e63?: number | null
    rs_ratio_rank_e200?: number | null
    rs_momentum_rank_e5?: number | null
    rs_momentum_rank_e14?: number | null
    rs_momentum_rank_e21?: number | null
    rs_momentum_rank_e63?: number | null
    rs_momentum_rank_e200?: number | null
    rs_trend_rank_s5?: number | null
    rs_trend_rank_s14?: number | null
    rs_trend_rank_s21?: number | null
    rs_trend_rank_s63?: number | null
    rs_trend_rank_s200?: number | null
    rs_roc_ema_rank_e5?: number | null
    rs_roc_ema_rank_e14?: number | null
    rs_roc_ema_rank_e21?: number | null
    rs_roc_ema_rank_e63?: number | null
    rs_roc_ema_rank_e200?: number | null
    rs_macd_hist_rank_21?: number | null
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
    rs_ratio_rank_e21: number;
    rs_ratio_rank_e63: number;
    rs_ratio_rank_e14?: number;
    rs_momentum_rank_e21?: number;
    rs_momentum_rank_e63?: number;
    rs_ratio_e21?: number;
    rs_ratio_e63?: number;
    rs_momentum_e21?: number;
    rs_trend_rank_s14?: number;
    rs_trend_rank_s21?: number;
    rs_trend_rank_s63?: number;
}

export interface ScreenerResultItem extends DashboardPanelItem {
    vol_surge_21?: number | null;
    adr_pct_21?: number | null;
    sma50_atr_mult?: number | null;
    is_trend_template?: number | null;
    market_cap?: number | null;
    up_down_vol_ratio_50?: number | null;
    is_rs_blue_dot?: number | null;
    is_rs_red_dot?: number | null;
    vcr?: number | null;
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

export interface EtfFeatureItem {
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
    dist_sma200_pct?: number;
    rs_ratio_e14?: number | null;
    rs_ratio_e21?: number | null;
    rs_ratio_e63?: number | null;
    rs14_sparkline?: number[];
    rs21_sparkline?: number[];
    rs63_sparkline?: number[];
    rs_ratio_rank_e14?: number | null;
    rs_ratio_rank_e21?: number | null;
    rs_ratio_rank_e63?: number | null;
    chart_data: ChartDataPoint[];
    group_type?: 'sector' | 'theme';
}

export interface MarketTrendScoreHistoryItem {
    date: string
    score: number
    vxv_vix_ratio?: number | null
    distribution_days?: number | null
    is_distribution_day?: number | null
    follow_through_day?: number | null
}

export interface DashboardResponse {
    date: string
    market_phase: string
    distribution_days: number
    market_trend_score: number
    vxv_vix_ratio?: number | null
    trend_score_history: MarketTrendScoreHistoryItem[]
    spy_feature?: EtfFeatureItem
    qqq_feature?: EtfFeatureItem
    leading: LeadingIndicatorItem[]
    indices: DashboardPanelItem[]
    sectors: DashboardPanelItem[]
    themes_top: DashboardPanelItem[]
    themes_bottom: DashboardPanelItem[]
}

export interface SystemInfo {
    db_path: string;
    db_name: string;
    is_production: boolean;
}

export interface ThemeConstituentItem {
    id: number;
    ticker: string;
    name: string;
    close: number;
    change_1d_pct: number;
    change_1w_pct: number;
    change_1m_pct: number;
    rs_ratio_e14?: number | null;
    rs_ratio_e21?: number | null;
    rs_ratio_e63?: number | null;
    rs_momentum_e21?: number | null;
    rs_ratio_rank_e14?: number | null;
    rs_ratio_rank_e21?: number | null;
    rs_ratio_rank_e63?: number | null;
    rs_momentum_rank_e21?: number | null;
    rs_momentum_rank_e63?: number | null;
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
    dist_sma200_pct?: number;
    rs_ratio_e14?: number | null;
    rs_ratio_e21?: number | null;
    rs_ratio_e63?: number | null;
    rs_momentum_e14?: number | null;
    rs_momentum_e21?: number | null;
    rs_momentum_e63?: number | null;
    rs_trend_s14?: number | null;
    rs_trend_s21?: number | null;
    rs_trend_s63?: number | null;
    rs_value_e14?: number | null;
    rs_value_e21?: number | null;
    rs_value_e63?: number | null;
    adr_pct_21?: number | null;
    sma50_atr_mult?: number | null;
    rs14_sparkline: number[];
    rs21_sparkline: number[];
    rs63_sparkline: number[];
    // Ranks for the theme ETF itself
    rs_ratio_rank_e14?: number | null;
    rs_ratio_rank_e21?: number | null;
    rs_ratio_rank_e63?: number | null;
    rs_momentum_rank_e14?: number | null;
    rs_momentum_rank_e21?: number | null;
    rs_momentum_rank_e63?: number | null;
    rs_trend_rank_s14?: number | null;
    rs_trend_rank_s21?: number | null;
    rs_trend_rank_s63?: number | null;
    chart_data: ChartDataPoint[];
    constituents: ThemeConstituentItem[];
}

export interface GroupDataResponse {
    ticker: string;
    name: string;
    group_type: 'sector' | 'theme';
    feature: EtfFeatureItem;
    constituents: DashboardPanelItem[];
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
    // 上場廃止・ticker変更等で symbols から解決できない場合は null
    symbol_id: number | null;
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
    latest_sma50_atr_mult: number;
    rs_sparkline: number[];
    next_earnings_date?: string | null;
    status: 'active' | 'removed';
    added_at: string;
    removed_at: string | null;
    removed_price: number | null;
}

export interface WatchlistResponse {
    active: WatchlistItem[];
    removed: WatchlistItem[];
}
