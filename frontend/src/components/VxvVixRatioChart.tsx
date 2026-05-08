import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ColorType } from 'lightweight-charts';
import { MarketTrendScoreHistoryItem } from '../types';
import { appConfig } from '../config';

interface VxvVixRatioChartProps {
    data: MarketTrendScoreHistoryItem[];
    height?: number;
}

export const VxvVixRatioChart: React.FC<VxvVixRatioChartProps> = ({ data, height = 150 }) => {
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
                autoScale: true,
                scaleMargins: {
                    top: 0.1,
                    bottom: 0.1,
                },
            },
            handleScroll: false,
            handleScale: false,
        });

        const lineSeries = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 2,
        });

        // Add reference lines
        lineSeries.createPriceLine({
            price: 1.0,
            color: appConfig.colors.bad,
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: 'Bottom (1.0)',
        });

        lineSeries.createPriceLine({
            price: 1.2,
            color: '#ffcc00',
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: 'Overheated (1.2)',
        });

        const chartData = data
            .filter(item => item.vxv_vix_ratio != null)
            .map(item => ({
                time: item.date,
                value: item.vxv_vix_ratio!,
            }));
        
        lineSeries.setData(chartData);
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
