import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ColorType } from 'lightweight-charts';
import { MarketTrendScoreHistoryItem } from '../types';
import { appConfig } from '../config';

interface VxvVixRatioChartProps {
    data: MarketTrendScoreHistoryItem[];
    height?: number;
}

// EMA (指数平滑移動平均) を動的に計算する関数
const calculateEMA = (data: { time: string; value: number }[], period: number) => {
    if (data.length === 0) return [];
    const k = 2 / (period + 1);
    const emaData: { time: string; value: number }[] = [];
    
    let ema = data[0].value;
    emaData.push({ time: data[0].time, value: ema });
    
    for (let i = 1; i < data.length; i++) {
        ema = data[i].value * k + ema * (1 - k);
        emaData.push({
            time: data[i].time,
            value: Number(ema.toFixed(4)),
        });
    }
    return emaData;
};

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

        // メインの VXV/VIX Ratio (青線)
        const lineSeries = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 2,
        });

        // VXV/VIX Ratio EMA5 (オレンジの細線)
        const emaSeries = chart.addLineSeries({
            color: '#ff9800',
            lineWidth: 1.5,
        });

        // VXV/VIX Ratio EMA21 (紫の細線)
        const emaSeries21 = chart.addLineSeries({
            color: '#9c27b0',
            lineWidth: 1.5,
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

        // EMA の計算と適用
        if (chartData.length > 0) {
            const emaData5 = calculateEMA(chartData, 5);
            emaSeries.setData(emaData5);

            const emaData21 = calculateEMA(chartData, 21);
            emaSeries21.setData(emaData21);
        }

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

    return <div ref={chartContainerRef} style={{ width: '100%', height: `${height}px` }} />;
};
