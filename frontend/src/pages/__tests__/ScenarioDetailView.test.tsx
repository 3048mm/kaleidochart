import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ScenarioDetailView } from '../ScenarioDetailView';
import { BrowserRouter } from 'react-router-dom';

// mock appConfig to avoid config load issues
vi.mock('../../config/appConfig', () => ({
  default: {
    colors: {
      glassBorder: '#000',
      good: '#0f0',
      bad: '#f00'
    }
  }
}));

// mock fetch API
const mockSummary = {
  is_monte_carlo: true,
  cagr: 0.1774,
  cagr_max: 0.25,
  cagr_min: 0.10,
  profit_factor: 1.35,
  profit_factor_avg: 1.35,
  max_drawdown: -0.4525,
  max_drawdown_min: -0.55,
  max_drawdown_max: -0.35,
  win_rate: 0.51,
  win_rate_avg: 0.51,
  total_trades: 42,
  final_capital: 177436.0
};

const mockEquity = [
  { date: '2022-01-03', equity: 100000.0, cash: 100000.0 },
  { date: '2022-01-04', equity: 101000.0, cash: 99000.0 }
];

const mockTrades = [
  { date: '2022-01-03', ticker: 'AAPL', action: 'BUY', price: 150.0, size: 100, reason: '', pnl_pct: 0.0 },
  { date: '2022-01-04', ticker: 'AAPL', action: 'SELL', price: 155.0, size: 100, reason: 'stop_loss', pnl_pct: 0.033 }
];

describe('ScenarioDetailView Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url.includes('/summary')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) });
      }
      if (url.includes('/equity')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockEquity) });
      }
      if (url.includes('/trades')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTrades) });
      }
      return Promise.resolve({ ok: false });
    }) as any;
  });

  it('should fetch data and render KPI cards with correct information', async () => {
    render(
      <BrowserRouter>
        <ScenarioDetailView strategyId="B1" modelId="full_position" />
      </BrowserRouter>
    );

    // Wait for the data to be loaded
    await waitFor(() => {
      // Check if CAGR card is rendered
      expect(screen.queryByText('CAGR (10回平均)')).not.toBeNull();
    });

    // Check CAGR value
    expect(screen.queryByText('17.74%')).not.toBeNull();
    // Check Profit Factor value
    expect(screen.queryByText('1.35')).not.toBeNull();
    // Check Max Drawdown value
    expect(screen.queryByText('-45.25%')).not.toBeNull();
    // Check Win Rate value
    expect(screen.queryByText('51.0%')).not.toBeNull();
    // avg_trade_pnl_pct が未定義（古い結果）のときは — 表示になり、落ちないこと
    expect(screen.queryByText('—')).not.toBeNull();
  });

  it('should render avg_trade_pnl_pct when present in the summary (Monte Carlo)', async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url.includes('/summary')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            ...mockSummary,
            avg_trade_pnl_pct: 5.14,
            avg_trade_pnl_pct_avg: 5.14
          })
        });
      }
      if (url.includes('/equity')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockEquity) });
      }
      if (url.includes('/trades')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockTrades) });
      }
      return Promise.resolve({ ok: false });
    }) as any;

    render(
      <BrowserRouter>
        <ScenarioDetailView strategyId="B1" modelId="full_position" />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.queryByText('+5.14%')).not.toBeNull();
    });

    // モンテカルロ時の平均併記
    expect(screen.queryByText('(平均: +5.14%)')).not.toBeNull();
  });
});
