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
    const sma63SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const sma200SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

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

        const sma63Series = chart.addLineSeries({
            color: '#8b5cf6',
            lineWidth: 1,
            priceLineVisible: false,
        });
        sma63SeriesRef.current = sma63Series;

        const sma200Series = chart.addLineSeries({
            color: '#f59e0b',
            lineWidth: 1,
            priceLineVisible: false,
        });
        sma200SeriesRef.current = sma200Series;

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

        const sma63Data = data
            .filter(d => d.sma_63 !== null && d.sma_63 !== undefined)
            .map(d => ({
                time: d.time,
                value: d.sma_63 as number,
            }));

        const sma200Data = data
            .filter(d => d.sma_200 !== null && d.sma_200 !== undefined)
            .map(d => ({
                time: d.time,
                value: d.sma_200 as number,
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
        sma63SeriesRef.current?.setData(sma63Data);
        sma200SeriesRef.current?.setData(sma200Data);
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
