import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RegimeComparisonPage } from '../RegimeComparisonPage';
import { BrowserRouter } from 'react-router-dom';

// Mock ScenarioDetailView to spy on its props
vi.mock('../ScenarioDetailView', () => ({
  ScenarioDetailView: vi.fn(({ strategyId, modelId }) => (
    <div data-testid="mock-detail-view" data-strategy={strategyId} data-model={modelId}>
      Mock Detail View: {strategyId} - {modelId}
    </div>
  ))
}));

// Mock fetch API (used in RegimeComparisonPage parent)
const mockGroupComparison = {
  group: 'A',
  start_date: '2022-01-01',
  end_date: '2026-03-26',
  strategies: {
    full_position: { final_capital: 106000, cagr: 0.06, max_drawdown: 0.1, win_rate: 0.5, total_trades: 10, profit_factor: 1.2 },
    spy_sma200: { final_capital: 102000, cagr: 0.02, max_drawdown: 0.15, win_rate: 0.4, total_trades: 8, profit_factor: 1.05 }
  },
  equity_curves: []
};

describe('RegimeComparisonPage Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockGroupComparison),
      })
    ) as any;
  });

  it('should render RegimeComparisonPage and embed ScenarioDetailView with default model "full_position"', async () => {
    render(
      <BrowserRouter>
        <RegimeComparisonPage />
      </BrowserRouter>
    );

    // Verify the mock detail view is rendered with the default model
    const detailView = await screen.findByTestId('mock-detail-view');
    expect(detailView).not.toBeNull();
    expect(detailView.getAttribute('data-model')).toBe('full_position');
  });

  it('should change the selected model and reload ScenarioDetailView when a regime card is clicked', async () => {
    const { fireEvent } = await import('@testing-library/react');
    
    render(
      <BrowserRouter>
        <RegimeComparisonPage />
      </BrowserRouter>
    );

    // Find the MTS Raw card and click it
    const mtsCard = await screen.findByText(/MTS Raw/);
    expect(mtsCard).not.toBeNull();
    
    // Click the card
    fireEvent.click(mtsCard);

    // Verify the detail view prop was updated to mts_raw
    const detailView = await screen.findByTestId('mock-detail-view');
    expect(detailView.getAttribute('data-model')).toBe('mts_raw');
  });
});
