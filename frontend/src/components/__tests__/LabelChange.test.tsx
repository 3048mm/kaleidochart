import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SummaryTable } from '../SummaryTable';
import { BrowserRouter } from 'react-router-dom';

// Mock appConfig
vi.mock('../../config/appConfig', () => ({
  default: {
    colors: {
      glassBorder: '#000',
      good: '#0f0',
      bad: '#f00'
    }
  }
}));

describe('Label Consistency Test', () => {
  it('SummaryTable should display correct labels', () => {
    const mockItems = [
      { id: 1, ticker: 'AAPL', name: 'Apple', close: 150, change_pct: 1.5, change_1w_pct: 2, change_1m_pct: 5, dist_21ema_pct: 1, rs_ratio_21_rank: 0.8, rs_ratio_63_rank: 0.7, sparkline: [] }
    ];
    
    render(
      <BrowserRouter>
        <SummaryTable items={mockItems as any} maxPct={10} />
      </BrowserRouter>
    );
    
    // Check if new labels are present
    expect(screen.queryByText('1D%')).not.toBeNull();
    expect(screen.queryByText('1W%')).not.toBeNull();
    expect(screen.queryByText('1M%')).not.toBeNull();
    
    // Check if old labels are gone
    expect(screen.queryByText('% 1D')).toBeNull();
    expect(screen.queryByText('% 1W')).toBeNull();
    expect(screen.queryByText('% 1M')).toBeNull();
    expect(screen.queryByText('1Day%')).toBeNull();
    expect(screen.queryByText('1Week%')).toBeNull();
    expect(screen.queryByText('1Month%')).toBeNull();
  });
});
