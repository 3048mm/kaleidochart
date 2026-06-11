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
                autoScale: false, // Turn off autoscale for stable fixed range
                scaleMargins: {
                    top: 0.05,
                    bottom: 0.05,
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

        // Price range fixed to 0.80 - 1.30 for stable layout and absolute level visualization
        lineSeries.applyOptions({
            autoscaleInfoProvider: () => ({
                priceRange: {
                    minValue: 0.80,
                    maxValue: 1.30,
                },
            }),
        });

        // VXV/VIX Ratio EMA5 (オレンジの細線)
        const emaSeries = chart.addLineSeries({
            color: '#ff9800',
            lineWidth: 1,
        });

        // VXV/VIX Ratio EMA21 (紫の細線)
        const emaSeries21 = chart.addLineSeries({
            color: '#9c27b0',
            lineWidth: 1,
        });

        // Add reference lines (remove titles to prevent overlap)
        lineSeries.createPriceLine({
            price: 1.0,
            color: 'rgba(244, 63, 94, 0.3)', // Subtler bad (red) color for line
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: '',
        });

        lineSeries.createPriceLine({
            price: 1.2,
            color: 'rgba(255, 204, 0, 0.3)', // Subtler yellow color for line
            lineWidth: 1,
            lineStyle: 1,
            axisLabelVisible: true,
            title: '',
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

    return (
        <div style={{ position: 'relative', width: '100%', height: `${height}px` }}>
            <div ref={chartContainerRef} style={{ width: '100%', height: '100%' }} />
            
            {/* Overheated Line Label Overlay (at 1.2 price which is 20% from top in 0.80-1.30 range) */}
            <div style={{ 
                position: 'absolute', left: '12px', top: '20%', 
                color: '#ffcc00', opacity: 0.7, fontSize: '10px', fontWeight: 'bold', 
                pointerEvents: 'none', zIndex: 10, transform: 'translateY(-50%)'
            }}>
                Overheated (1.2)
            </div>
            
            {/* Bottom Line Label Overlay (at 1.0 price which is 60% from top in 0.80-1.30 range) */}
            <div style={{ 
                position: 'absolute', left: '12px', top: '60%', 
                color: appConfig.colors.bad, opacity: 0.7, fontSize: '10px', fontWeight: 'bold', 
                pointerEvents: 'none', zIndex: 10, transform: 'translateY(-50%)'
            }}>
                Bottom (1.0)
            </div>
        </div>
    );
};
