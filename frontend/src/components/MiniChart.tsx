import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ISeriesApi, CrosshairMode } from 'lightweight-charts';
import { ChartDataPoint } from '../types';
import { appConfig } from '../config';

interface MiniChartProps {
    data: ChartDataPoint[];
    height?: number;
}

export const MiniChart: React.FC<MiniChartProps> = ({ data, height = 200 }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

    useEffect(() => {
        if (!chartContainerRef.current) return;

        // Initialize chart
        const chart = createChart(chartContainerRef.current, {
            width: chartContainerRef.current.clientWidth,
            height: height,
            layout: {
                background: { color: 'transparent' },
                textColor: '#aaa',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { visible: false },
            },
            crosshair: {
                mode: CrosshairMode.Magnet,
            },
            timeScale: {
                borderColor: appConfig.colors.glassBorder,
                rightOffset: 5,
                barSpacing: 6,
                fixLeftEdge: true,
            },
            rightPriceScale: {
                borderColor: appConfig.colors.glassBorder,
                visible: true,
            },
        });

        chartRef.current = chart;

        const candleSeries = chart.addCandlestickSeries({
            upColor: appConfig.colors.good,
            downColor: appConfig.colors.bad,
            borderVisible: false,
            wickUpColor: appConfig.colors.good,
            wickDownColor: appConfig.colors.bad,
        });

        candleSeriesRef.current = candleSeries;

        // Handle resize
        const resizeObserver = new ResizeObserver(entries => {
            if (entries.length === 0 || !chartRef.current) return;
            const { width } = entries[0].contentRect;
            chartRef.current.applyOptions({ width });
        });

        if (chartContainerRef.current) {
            resizeObserver.observe(chartContainerRef.current);
        }

        return () => {
            resizeObserver.disconnect();
            chart.remove();
        };
    }, [height]);

    useEffect(() => {
        if (!candleSeriesRef.current || !data.length) return;
        
        // Transform for lightweight-charts
        const chartData = data.map(d => ({
            time: d.time,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));

        // Add horizontal center line if data exists
        if (data.length > 0) {
            const highs = data.map(d => d.high);
            const lows = data.map(d => d.low);
            const max = Math.max(...highs);
            const min = Math.min(...lows);
            const mid = (max + min) / 2;

            candleSeriesRef.current.createPriceLine({
                price: mid,
                color: 'rgba(255, 255, 255, 0.4)',
                lineWidth: 1,
                lineStyle: 1, // Dotted
                axisLabelVisible: false,
                title: '',
            });
        }
        
        candleSeriesRef.current.setData(chartData as any);
        chartRef.current?.timeScale().fitContent();
        
    }, [data]);

    return (
        <div 
            ref={chartContainerRef} 
            style={{ 
                width: '100%', 
                height: `${height}px`,
                position: 'relative'
            }} 
        />
    );
};
