import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import RsLineChart from '../RsLineChart';
import { ChartDataPoint } from '../../types';

// Mock lightweight-charts
vi.mock('lightweight-charts', () => {
    return {
        createChart: vi.fn(() => ({
            addLineSeries: vi.fn(() => ({
                setData: vi.fn(),
                applyOptions: vi.fn(),
            })),
            subscribeCrosshairMove: vi.fn(),
            timeScale: vi.fn(() => ({
                fitContent: vi.fn(),
            })),
            remove: vi.fn(),
            applyOptions: vi.fn(),
        })),
        CrosshairMode: {
            Normal: 0,
        },
    };
});

const mockData: ChartDataPoint[] = [
    {
        time: '2023-01-01',
        open: 100, high: 110, low: 90, close: 105, volume: 1000,
        relative_strength_spy: 1.1,
        rs_ema_14: 1.0,
        rs_ema_21: 1.0,
        rs_ema_63: 1.0,
    },
    {
        time: '2023-01-02',
        open: 105, high: 115, low: 100, close: 110, volume: 1100,
        relative_strength_spy: 1.2,
        rs_ema_14: 1.05,
        rs_ema_21: 1.02,
        rs_ema_63: 1.01,
    }
];

describe('RsLineChart', () => {
    it('renders without crashing', () => {
        render(<RsLineChart data={mockData} />);
        expect(screen.getByTestId('rs-line-chart-container')).toBeInTheDocument();
    });

    it('displays the title', () => {
        render(<RsLineChart data={mockData} />);
        expect(screen.getByText('Relative Strength vs SPY')).toBeInTheDocument();
    });
});
