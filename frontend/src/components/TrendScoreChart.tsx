import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ColorType } from 'lightweight-charts';
import { MarketTrendScoreHistoryItem } from '../types';
import { appConfig } from '../config';

interface TrendScoreChartProps {
    data: MarketTrendScoreHistoryItem[];
    height?: number;
}

export const TrendScoreChart: React.FC<TrendScoreChartProps> = ({ data, height = 150 }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!chartContainerRef.current) return;

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#aaa',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { color: 'rgba(255, 255, 255, 0.1)' },
            },
            width: chartContainerRef.current.clientWidth,
            height: height,
            timeScale: {
                borderColor: appConfig.colors.glassBorder,
            },
            rightPriceScale: {
                borderColor: appConfig.colors.glassBorder,
                autoScale: false,
                scaleMargins: {
                    top: 0,
                    bottom: 0,
                },
            },
            handleScroll: false,
            handleScale: false,
        });

        // Add 0-100 price range
        // Since lightweight-charts doesn't have a simple min/max fixed range directly, 
        // we use a hidden series or custom scaling. 
        // Or just let it autoscale but we prefer fixed 0-100 for a score.
        
        const areaSeries = chart.addAreaSeries({
            topColor: 'rgba(76, 175, 80, 0.5)',
            bottomColor: 'rgba(76, 175, 80, 0.0)',
            lineColor: appConfig.colors.good,
            lineWidth: 2,
        });

        // Price range 0-100
        areaSeries.applyOptions({
            autoscaleInfoProvider: () => ({
                priceRange: {
                    minValue: 0,
                    maxValue: 100,
                },
            }),
        });

        // Add reference line at 50
        areaSeries.createPriceLine({
            price: 50,
            color: 'rgba(255, 255, 255, 0.15)', // Make line subtler
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: '', // Remove title to prevent overlapping the graph line
        });

        const chartData = data.map(item => ({
            time: item.date,
            value: item.score,
        }));
        
        areaSeries.setData(chartData);

        // Invisible series to align markers
        const ddSeries = chart.addLineSeries({
            color: 'transparent',
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
        });
        const ftdSeries = chart.addLineSeries({
            color: 'transparent',
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
        });

        ddSeries.setData(data.map(item => ({ time: item.date, value: 90 })));
        ftdSeries.setData(data.map(item => ({ time: item.date, value: 10 })));

        // Add Price Lines for the markers (without axis labels)
        ddSeries.createPriceLine({
            price: 90,
            color: 'rgba(255, 82, 82, 0.1)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
        });
        ftdSeries.createPriceLine({
            price: 10,
            color: 'rgba(33, 150, 243, 0.1)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
        });

        // Distribution Day Markers (Alert logic: 5=Yellow, 8=Red)
        const ddMarkers = data
            .filter(item => item.is_distribution_day === 1 && ((item.distribution_days || 0) === 5 || (item.distribution_days || 0) === 8))
            .map(item => ({
                time: item.date,
                position: 'inBar',
                color: (item.distribution_days || 0) === 8 ? appConfig.colors.bad : '#ffcc00',
                shape: 'arrowDown',
                size: 1,
            }));
        ddSeries.setMarkers(ddMarkers as any);

        // Follow Through Day Markers (Arrows only)
        const ftdMarkers = data
            .filter(item => item.follow_through_day === 1)
            .map(item => ({
                time: item.date,
                position: 'inBar',
                color: appConfig.colors.accent,
                shape: 'arrowUp',
                size: 1,
            }));
        ftdSeries.setMarkers(ftdMarkers as any);

        chart.timeScale().fitContent();

        chartRef.current = chart;

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
    }, [data, height]);

    return (
        <div style={{ position: 'relative', width: '100%', height: `${height}px` }}>
            <div ref={chartContainerRef} style={{ width: '100%', height: '100%' }} />
            
            {/* Left-aligned labels for DD/FTD zones */}
            <div style={{ 
                position: 'absolute', left: '12px', top: '10%', 
                color: appConfig.colors.bad, opacity: 0.8, fontSize: '10px', fontWeight: 'bold', 
                pointerEvents: 'none', zIndex: 10, transform: 'translateY(-50%)'
            }}>
                DD
            </div>
            {/* Neutral line label overlay */}
            <div style={{ 
                position: 'absolute', left: '12px', top: '50%', 
                color: 'rgba(255, 255, 255, 0.4)', fontSize: '10px', fontWeight: 'bold', 
                pointerEvents: 'none', zIndex: 10, transform: 'translateY(-50%)'
            }}>
                Neutral (50)
            </div>
            <div style={{ 
                position: 'absolute', left: '12px', bottom: '10%', 
                color: appConfig.colors.accent, opacity: 0.8, fontSize: '10px', fontWeight: 'bold', 
                pointerEvents: 'none', zIndex: 10, transform: 'translateY(50%)'
            }}>
                FTD
            </div>
        </div>
    );
};
