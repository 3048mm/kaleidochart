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
                    top: 0.1,
                    bottom: 0.1,
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
            color: 'rgba(255, 255, 255, 0.3)',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: 'Neutral',
        });

        const chartData = data.map(item => ({
            time: item.date,
            value: item.score,
        }));
        
        areaSeries.setData(chartData);
        chart.timeScale().fitContent();

        chartRef.current = chart;

        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [data, height]);

    return <div ref={chartContainerRef} style={{ width: '100%', height: `${height}px` }} />;
};
