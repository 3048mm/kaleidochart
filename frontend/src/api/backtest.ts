// frontend/src/api/backtest.ts

export interface YearlyPerformanceItem {
  total_trades: number;
  win_rate: number;
  net_pnl: number;
  avg_pnl_pct: number;
  profit_factor: number;
  spy_return_pct: number;
  return_pct?: number; // Strategy return percentage for the year
}

export interface BacktestScenarioSummary {
  cagr: number;
  profit_factor: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  is_monte_carlo?: boolean;
  runs_count?: number;
  cagr_avg?: number;
  cagr_max?: number;
  cagr_min?: number;
  max_drawdown_avg?: number;
  max_drawdown_min?: number;
  max_drawdown_max?: number;
  win_rate_avg?: number;
  total_trades_avg?: number;
  profit_factor_avg?: number;
  final_capital_avg?: number;
  final_capital_max?: number;
  final_capital_min?: number;
  avg_trade_pnl_pct?: number;
  avg_trade_pnl_pct_avg?: number;
  // Run Info（折りたたみ Run Info バー用。ETF 側 EtfSingleSummary と項目を揃えている）
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  trading_days?: number;
  consider_tax?: number;
  total_return_pct?: number;
  yearly_performance?: { [year: string]: YearlyPerformanceItem };
  exit_reasons?: {
    [reason: string]: {
      count: number;
      avg_pnl_pct: number;
      avg_holding_days: number;
    }
  };
  [key: string]: any; // Allow extra fields
}

export interface BacktestEquityPoint {
  date: string;
  equity: number;
  cash: number;
  spy_equity?: number;
  qqq_equity?: number;
  tqqq_equity?: number;
  soxl_equity?: number;
  trend_score?: number;
  run_equities?: { [key: string]: number };
}

export interface BacktestTradeLogItem {
  date: string;
  ticker: string;
  action: string;
  price: number;
  size: number;
  reason: string;
  pnl_pct: number;
}

export async function fetchScenarios(): Promise<string[]> {
  const response = await fetch('/api/backtest/scenarios');
  if (!response.ok) {
    throw new Error('Failed to fetch backtest scenarios');
  }
  return response.json();
}

export async function fetchScenarioSummary(name: string): Promise<BacktestScenarioSummary> {
  const response = await fetch(`/api/backtest/scenario/${name}/summary`);
  if (!response.ok) {
    throw new Error(`Failed to fetch backtest scenario summary for ${name}`);
  }
  return response.json();
}

export async function fetchScenarioEquity(name: string): Promise<BacktestEquityPoint[]> {
  const response = await fetch(`/api/backtest/scenario/${name}/equity`);
  if (!response.ok) {
    throw new Error(`Failed to fetch backtest scenario equity for ${name}`);
  }
  return response.json();
}

export async function fetchScenarioTrades(name: string): Promise<BacktestTradeLogItem[]> {
  const response = await fetch(`/api/backtest/scenario/${name}/trades`);
  if (!response.ok) {
    throw new Error(`Failed to fetch backtest scenario trades for ${name}`);
  }
  return response.json();
}


// ============================================================
// ETF Single Backtest Types & API
// ============================================================

export interface EtfStrategyResult {
  final_capital: number;
  total_return_pct: number;
  cagr: number;
  max_drawdown_pct: number;
  max_drawdown_date?: string;
  sharpe_ratio: number;
  yearly_returns?: { [year: string]: number };
  regime_changes?: number;
  rebalance_count?: number;
  time_in_market_pct?: number;
}

export interface EtfSingleSummary {
  ticker: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  consider_tax: number;
  trading_days: number;
  strategies: {
    vxv_vix_ema: EtfStrategyResult;
    mts_v3_raw?: EtfStrategyResult;
    based_sma200?: EtfStrategyResult;
    based_sma63?: EtfStrategyResult;
    buy_and_hold: EtfStrategyResult;
    dca: EtfStrategyResult;
  };
}

export interface EtfSingleEquityPoint {
  date: string;
  vxv_equity: number;
  buyhold_equity: number;
  dca_equity: number;
  vxv_position_pct: number;
  regime: string;
  mts_v3_raw_equity?: number;
  mts_v3_raw_position_pct?: number;
  mts_score?: number;
  mts_ema5?: number;
  based_sma200_equity?: number;
  based_sma200_position_pct?: number;
  based_sma63_equity?: number;
  based_sma63_position_pct?: number;
  [key: string]: any;
}

export interface EtfSingleRegimeItem {
  date: string;
  regime: string;
  vxv_vix_ratio: number;
  ema5?: number;
  ema21?: number;
  position_pct: number;
}

export async function fetchEtfSingleSummary(ticker: string): Promise<EtfSingleSummary> {
  const response = await fetch(`/api/backtest/etf-single/${ticker}/summary`);
  if (!response.ok) {
    throw new Error(`Failed to fetch ETF single summary for ${ticker}`);
  }
  return response.json();
}

export async function fetchEtfSingleEquity(ticker: string): Promise<EtfSingleEquityPoint[]> {
  const response = await fetch(`/api/backtest/etf-single/${ticker}/equity`);
  if (!response.ok) {
    throw new Error(`Failed to fetch ETF single equity for ${ticker}`);
  }
  return response.json();
}

export async function fetchEtfSingleRegimes(ticker: string): Promise<EtfSingleRegimeItem[]> {
  const response = await fetch(`/api/backtest/etf-single/${ticker}/regimes`);
  if (!response.ok) {
    throw new Error(`Failed to fetch ETF single regimes for ${ticker}`);
  }
  return response.json();
}


// ============================================================
// Scenario Comparison Types & API
// ============================================================

export interface ScenarioComparisonStrategyMetrics {
  final_capital: number;
  cagr: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  profit_factor: number;
}

export interface ScenarioComparisonSummary {
  start_date: string;
  end_date: string;
  strategies: {
    full_position?: ScenarioComparisonStrategyMetrics;
    spy_sma200: ScenarioComparisonStrategyMetrics;
    spy_sma63: ScenarioComparisonStrategyMetrics;
    vxv_vix_ema: ScenarioComparisonStrategyMetrics;
    mts_raw: ScenarioComparisonStrategyMetrics;
  };
}

export interface ScenarioComparisonEquityPoint {
  date: string;
  spy_equity: number;
  equity_mts_raw: number;
  equity_vxv_vix_ema: number;
  equity_spy_sma200: number;
  equity_spy_sma63: number;
  equity_full_position?: number;
}

export interface ScenarioComparisonProgress {
  status: 'not_started' | 'running' | 'completed' | 'error';
  progress_pct: number;
}

export async function runScenarioComparison(params: {
  start_date: string;
  end_date: string;
  initial_capital?: number;
  max_positions?: number;
  min_score?: number;
  stop_loss_pct?: number;
  profit_target_pct?: number;
  refresh_cache?: boolean;
  use_vxv_vix?: boolean;
}): Promise<{ status: string; message: string }> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, val]) => {
    if (val !== undefined) query.append(key, String(val));
  });

  const response = await fetch(`/api/backtest/comparison/run?${query.toString()}`, {
    method: 'POST'
  });
  if (!response.ok) {
    throw new Error('Failed to trigger comparison run');
  }
  return response.json();
}

export async function fetchComparisonProgress(): Promise<ScenarioComparisonProgress> {
  const response = await fetch('/api/backtest/comparison/progress');
  if (!response.ok) {
    throw new Error('Failed to fetch comparison progress');
  }
  return response.json();
}

export async function fetchComparisonSummary(): Promise<ScenarioComparisonSummary> {
  const response = await fetch('/api/backtest/comparison/summary');
  if (!response.ok) {
    throw new Error('Failed to fetch comparison summary');
  }
  return response.json();
}

export async function fetchComparisonEquity(): Promise<ScenarioComparisonEquityPoint[]> {
  const response = await fetch('/api/backtest/comparison/equity');
  if (!response.ok) {
    throw new Error('Failed to fetch comparison equity curve');
  }
  return response.json();
}

export interface ScenarioGroupComparisonResult {
  group: string;
  start_date: string;
  end_date: string;
  strategies: {
    [model: string]: ScenarioComparisonStrategyMetrics;
  };
  equity_curves: {
    date: string;
    spy_equity: number;
    equity_mts_raw: number;
    equity_vxv_vix_ema: number;
    equity_spy_sma200: number;
    equity_spy_sma63: number;
    equity_full_position?: number;
  }[];
}

export async function fetchGroupComparison(group: string): Promise<ScenarioGroupComparisonResult> {
  const response = await fetch(`/api/backtest/comparison/group/${group}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch group comparison for ${group}`);
  }
  return response.json();
}
