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
  yearly_performance?: { [year: string]: YearlyPerformanceItem };
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
