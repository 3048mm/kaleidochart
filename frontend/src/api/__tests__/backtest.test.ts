// frontend/src/api/__tests__/backtest.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  fetchScenarios,
  fetchScenarioSummary,
  fetchScenarioEquity,
  fetchScenarioTrades
} from '../backtest';

describe('backtest API Client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetchScenarios should call correct endpoint and return scenario names', async () => {
    const mockScenarios = ['scenario_alpha', 'scenario_beta'];
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockScenarios),
      })
    ) as any;

    const result = await fetchScenarios();
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/backtest/scenarios');
    expect(result).toEqual(mockScenarios);
  });

  it('fetchScenarioSummary should call correct endpoint with dynamic name', async () => {
    const mockSummary = {
      cagr: 0.402,
      profit_factor: 1.43,
      max_drawdown: -0.12,
      win_rate: 0.55,
      total_trades: 120
    };
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockSummary),
      })
    ) as any;

    const result = await fetchScenarioSummary('scenario_beta');
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/backtest/scenario/scenario_beta/summary');
    expect(result).toEqual(mockSummary);
  });

  it('fetchScenarioEquity should call correct endpoint and parse equity data', async () => {
    const mockEquity = [
      { date: '2026-05-20', equity: 10000.0, cash: 10000.0 },
      { date: '2026-05-21', equity: 10500.0, cash: 9500.0 }
    ];
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockEquity),
      })
    ) as any;

    const result = await fetchScenarioEquity('scenario_beta');
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/backtest/scenario/scenario_beta/equity');
    expect(result).toEqual(mockEquity);
  });

  it('fetchScenarioTrades should call correct endpoint and parse trades', async () => {
    const mockTrades = [
      { date: '2026-05-20', ticker: 'MSFT', action: 'buy', price: 300.0, size: 5, reason: 'strategy', pnl_pct: 0.0 },
      { date: '2026-05-21', ticker: 'MSFT', action: 'sell', price: 320.0, size: 5, reason: 'stop_loss', pnl_pct: 6.67 }
    ];
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockTrades),
      })
    ) as any;

    const result = await fetchScenarioTrades('scenario_beta');
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/backtest/scenario/scenario_beta/trades');
    expect(result).toEqual(mockTrades);
  });

  it('should throw an error when API response is not ok', async () => {
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: false,
        statusText: 'Internal Server Error',
      })
    ) as any;

    await expect(fetchScenarios()).rejects.toThrow('Failed to fetch backtest scenarios');
  });
});
