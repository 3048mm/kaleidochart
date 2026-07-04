import React, { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, CrosshairMode } from 'lightweight-charts';
import { ChartDataPoint } from '../types';

interface RsLineChartProps {
    data: ChartDataPoint[];
    height?: number;
}

export const RsLineChart: React.FC<RsLineChartProps> = ({ data, height }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const [hoverData, setHoverData] = useState<ChartDataPoint | null>(null);
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth <= 768);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

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
                rightOffset: 5,
            },
            height: height || 250,
            autoSize: true,
        });

        chartRef.current = chart;

        const filteredData = data.filter(d => d.rs_value != null);
        if (filteredData.length === 0) {
            chart.remove();
            return;
        }

        // Main RS Line
        const rsSeries = chart.addLineSeries({
            color: '#ffffff',
            lineWidth: 2,
            lastValueVisible: true,
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });

        const rsData = filteredData.map(d => ({
            time: d.time as any,
            value: d.rs_value as number,
        }));
        rsSeries.setData(rsData);

        // EMA lines
        const ema14Series = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 1,
            lineStyle: 2,
            lastValueVisible: false,
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema14Series.setData(data.filter(d => d.rs_value_e14 != null).map(d => ({
            time: d.time as any,
            value: d.rs_value_e14 as number,
        })));

        const ema21Series = chart.addLineSeries({
            color: '#FF9800',
            lineWidth: 1,
            lineStyle: 2,
            lastValueVisible: false,
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema21Series.setData(data.filter(d => d.rs_value_e21 != null).map(d => ({
            time: d.time as any,
            value: d.rs_value_e21 as number,
        })));

        const ema63Series = chart.addLineSeries({
            color: '#E040FB',
            lineWidth: 1,
            lineStyle: 2,
            lastValueVisible: false,
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema63Series.setData(data.filter(d => d.rs_value_e63 != null).map(d => ({
            time: d.time as any,
            value: d.rs_value_e63 as number,
        })));

        const ema200Series = chart.addLineSeries({
            color: '#f43f5e',
            lineWidth: 1,
            lineStyle: 2,
            lastValueVisible: false,
            priceFormat: {
                type: 'price',
                precision: 4,
                minMove: 0.0001,
            },
        });
        ema200Series.setData(data.filter(d => d.rs_value_e200 != null).map(d => ({
            time: d.time as any,
            value: d.rs_value_e200 as number,
        })));

        // RS Volume histogram series (Relative volume vs SPY)
        const rsVolSeries = chart.addHistogramSeries({
            color: 'rgba(99, 120, 180, 0.3)',
            priceFormat: {
                type: 'custom',
                formatter: (val: number) => val.toFixed(2),
            },
            priceScaleId: 'rs_vol',
        });
        chart.priceScale('rs_vol').applyOptions({
            scaleMargins: {
                top: 0.75,
                bottom: 0,
            },
            visible: true,
            borderColor: 'rgba(255, 255, 255, 0.1)',
        });
        rsVolSeries.setData(data.filter(d => d.vol_surge_rel_spy_21 != null).map(d => ({
            time: d.time as any,
            value: d.vol_surge_rel_spy_21 as number,
            color: (d.vol_surge_rel_spy_21 as number) >= 1.2 ? 'rgba(0, 255, 136, 0.35)' : 'rgba(99, 120, 180, 0.25)',
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

        // Crosshair move subscription to track hover value
        const dataMap = new Map<string, ChartDataPoint>();
        data.forEach(d => dataMap.set(d.time, d));

        const handleMove = (param: any) => {
            if (param.time) {
                const point = dataMap.get(String(param.time));
                setHoverData(point || null);
            } else {
                setHoverData(null);
            }
        };
        chart.subscribeCrosshairMove(handleMove);

        const handleResize = () => {
            if (containerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
            }
        };
        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.unsubscribeCrosshairMove(handleMove);
            chart.remove();
        };
    }, [data]);

    const latest = data.length > 0 ? data[data.length - 1] : null;
    const activePoint = hoverData || latest;

    const formatValue = (val: number | undefined | null) => {
        if (val == null) return '-';
        return val.toFixed(4);
    };

    return (
        <div style={{ marginBottom: '20px', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', padding: '15px' }}>
            <div style={{ 
                display: 'flex', 
                flexDirection: isMobile ? 'column' : 'row', 
                justifyContent: 'space-between', 
                alignItems: isMobile ? 'flex-start' : 'center', 
                marginBottom: '10px',
                gap: isMobile ? '8px' : '0'
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <h4 style={{ margin: 0, fontSize: '14px', color: '#ccc' }}>Relative Strength vs SPY</h4>
                    {activePoint && (
                        <span style={{ 
                            fontSize: '11px', 
                            color: hoverData ? '#60a5fa' : '#888', 
                            background: hoverData ? 'rgba(96,165,250,0.1)' : 'rgba(255,255,255,0.05)',
                            padding: '1px 6px',
                            borderRadius: '3px',
                            fontWeight: hoverData ? 'bold' : 'normal'
                        }}>
                            {activePoint.time} {hoverData && '(Cursor)'}
                        </span>
                    )}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: isMobile ? '10px' : '15px', fontSize: '11px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#ffffff' }} />
                        <span style={{ color: '#aaa' }}>RS:</span>
                        <strong style={{ color: '#ffffff' }}>{formatValue(activePoint?.rs_value)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#2962FF' }} />
                        <span style={{ color: '#aaa' }}>EMA14:</span>
                        <strong style={{ color: '#2962FF' }}>{formatValue(activePoint?.rs_value_e14)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#FF9800' }} />
                        <span style={{ color: '#aaa' }}>EMA21:</span>
                        <strong style={{ color: '#FF9800' }}>{formatValue(activePoint?.rs_value_e21)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#E040FB' }} />
                        <span style={{ color: '#aaa' }}>EMA63:</span>
                        <strong style={{ color: '#E040FB' }}>{formatValue(activePoint?.rs_value_e63)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '10px', height: '2px', background: '#f43f5e' }} />
                        <span style={{ color: '#aaa' }}>EMA200:</span>
                        <strong style={{ color: '#f43f5e' }}>{formatValue(activePoint?.rs_value_e200)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginLeft: '5px' }}>
                        <span style={{ color: '#aaa' }}>RS Vol (vs SPY):</span>
                        <strong style={{ color: (activePoint?.vol_surge_rel_spy_21 || 0) >= 1.2 ? '#00ff88' : '#aaa' }}>
                            {activePoint?.vol_surge_rel_spy_21 != null ? activePoint.vol_surge_rel_spy_21.toFixed(2) : '-'}
                        </strong>
                    </div>
                </div>
            </div>
            <div ref={containerRef} data-testid="rs-line-chart-container" style={{ width: '100%', height: `${height || 250}px` }} />
        </div>
    );
};

export default RsLineChart;
