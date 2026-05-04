import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, CrosshairMode } from 'lightweight-charts';
import { ChartDataPoint } from '../types';

interface RsLineChartProps {
    data: ChartDataPoint[];
}

export const RsLineChart: React.FC<RsLineChartProps> = ({ data }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!containerRef.current || data.length === 0) return;

        const chart = createChart(containerRef.current, {
            layout: {
                background: { color: 'transparent' },
                textColor: '#d1d4dc',
            },
            grid: {
                vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
                horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
            },
            crosshair: {
                mode: CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                autoScale: true,
            },
            timeScale: {
                borderColor: 'rgba(255, 255, 255, 0.1)',
                timeVisible: true,
                fixLeftEdge: true,
            },
            height: 250,
            autoSize: true,
        });

        chartRef.current = chart;

        const filteredData = data.filter(d => d.relative_strength_spy != null);
        if (filteredData.length === 0) {
            chart.remove();
            return;
        }

        // Main RS Line
        const rsSeries = chart.addLineSeries({
            color: '#ffffff',
            lineWidth: 2,
            title: 'RS vs SPY',
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });

        const rsData = filteredData.map(d => ({
            time: d.time as any,
            value: d.relative_strength_spy as number,
        }));
        rsSeries.setData(rsData);

        // EMA lines
        const ema14Series = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 1,
            lineStyle: 2,
            title: 'EMA 14',
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema14Series.setData(data.filter(d => d.rs_ema_14 != null).map(d => ({
            time: d.time as any,
            value: d.rs_ema_14 as number,
        })));

        const ema21Series = chart.addLineSeries({
            color: '#FF9800',
            lineWidth: 1,
            lineStyle: 2,
            title: 'EMA 21',
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema21Series.setData(data.filter(d => d.rs_ema_21 != null).map(d => ({
            time: d.time as any,
            value: d.rs_ema_21 as number,
        })));

        const ema63Series = chart.addLineSeries({
            color: '#E040FB',
            lineWidth: 1,
            lineStyle: 2,
            title: 'EMA 63',
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema63Series.setData(data.filter(d => d.rs_ema_63 != null).map(d => ({
            time: d.time as any,
            value: d.rs_ema_63 as number,
        })));

        // Set default visible range to last 6 months (exact replica from ChartPage logic)
        if (data.length > 0) {
            const lastDate = new Date(data[data.length - 1].time);
            const sixMonthsAgo = new Date(lastDate);
            sixMonthsAgo.setMonth(sixMonthsAgo.getMonth() - 6);
            const sixMonthsAgoStr = sixMonthsAgo.toISOString().split('T')[0];
            
            chart.timeScale().setVisibleRange({
                from: sixMonthsAgoStr as any,
                to: data[data.length - 1].time as any,
            });
        } else {
            chart.timeScale().fitContent();
        }

        const handleResize = () => {
            if (containerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
            }
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [data]);

    return (
        <div style={{ marginBottom: '20px', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', padding: '15px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <h4 style={{ margin: 0, fontSize: '14px', color: '#ccc' }}>Relative Strength vs SPY</h4>
                <div style={{ display: 'flex', gap: '15px', fontSize: '11px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#ffffff' }} />
                        <span>RS Line</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#2962FF' }} />
                        <span>EMA 14</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#FF6D00' }} />
                        <span>EMA 21</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#9c27b0' }} />
                        <span>EMA 63</span>
                    </div>
                </div>
            </div>
            <div ref={containerRef} data-testid="rs-line-chart-container" style={{ width: '100%', height: '250px' }} />
        </div>
    );
};

export default RsLineChart;
