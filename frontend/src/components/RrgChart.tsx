import React, { useState, useMemo } from 'react';
import { ChartDataPoint } from '../types';
import { Sparkline } from './Sparkline';
import { appConfig } from '../config';
import RsLineChart from './RsLineChart';

export interface RrgSeries {
    ticker: string;
    data: ChartDataPoint[];
    color: string;
}

interface RrgChartProps {
    /** Single primary ticker (legacy / ChartPage usage) */
    data?: ChartDataPoint[];
    ticker?: string;
    /** Multi-ticker series (ThemePage usage) */
    series?: RrgSeries[];
}

// Colour palette for multi-ticker mode
const PALETTE = [
    '#26a69a', '#ab47bc', '#ffa726', '#42a5f5', '#ef5350',
    '#66bb6a', '#ec407a', '#ffee58', '#26c6da', '#ff7043',
    '#8d6e63', '#78909c', '#d4e157', '#29b6f6', '#ff5252',
    '#69f0ae', '#e040fb', '#ffcc02', '#00bcd4', '#ff6d00',
];

export const RrgChart: React.FC<RrgChartProps> = ({ data, ticker, series: propSeries }) => {
    const [rrgTimeframe, setRrgTimeframe] = useState<14 | 21 | 63>(21);
    const [trailLength, setTrailLength] = useState<number>(20);
    const [hoveredPoint, setHoveredPoint] = useState<{ ticker: string; time: string; x: number; y: number; cond?: number; close: number } | null>(null);

    const isIndividualMode = !propSeries || propSeries.length === 0;

    // Build normalised series from either prop
    const allSeries: RrgSeries[] = useMemo(() => {
        if (!isIndividualMode && propSeries) return propSeries;
        if (data && ticker) return [{ ticker, data, color: PALETTE[0] }];
        return [];
    }, [propSeries, data, ticker, isIndividualMode]);

    // Extract RRG points per series
    const computedSeries = useMemo(() => {
        return allSeries.map(s => {
            const ratioKey = `rs_ratio_${rrgTimeframe}` as keyof ChartDataPoint;
            const momKey = `rs_momentum_${rrgTimeframe}` as keyof ChartDataPoint;
            const condKey = `rs_condition_${rrgTimeframe}` as keyof ChartDataPoint;

            const points = s.data.filter(d => d[ratioKey] != null && d[momKey] != null).map(d => ({
                time: d.time as string,
                x: d[ratioKey] as number,
                y: d[momKey] as number,
                cond: d[condKey] as number | undefined,
                close: d.close,
            })).slice(-Math.abs(trailLength));

            return { ...s, points };
        });
    }, [allSeries, rrgTimeframe, trailLength]);

    // Minimap data (Individual symbol only, last 30 days)
    const minimaps = useMemo(() => {
        if (!isIndividualMode || !data || data.length === 0) return null;
        
        const last30 = data.slice(-30);
        return {
            ratio: last30.map(d => (d[`rank_rs_ratio_${rrgTimeframe}` as keyof ChartDataPoint] as number) || 0),
            mom: last30.map(d => (d[`rank_rs_momentum_${rrgTimeframe}` as keyof ChartDataPoint] as number) || 0),
            cond: last30.map(d => (d[`rank_rs_condition_${rrgTimeframe}` as keyof ChartDataPoint] as number) || 0),
        };
    }, [isIndividualMode, data, rrgTimeframe]);

    // SVG dimensions (Internal coordinate space)
    const width = 800;
    const height = 600;
    const padding = 50;
    const innerWidth = width - padding * 2;
    const innerHeight = height - padding * 2;

    // Global bounds across all series
    const { minX, maxX, minY, maxY } = useMemo(() => {
        const allPts = computedSeries.flatMap(s => s.points);
        if (allPts.length === 0) return { minX: -2, maxX: 2, minY: -2, maxY: 2 };

        let mX = Math.min(...allPts.map(d => d.x));
        let mMX = Math.max(...allPts.map(d => d.x));
        let mY = Math.min(...allPts.map(d => d.y));
        let mMY = Math.max(...allPts.map(d => d.y));

        mX = Math.min(mX, -0.5);
        mMX = Math.max(mMX, 0.5);
        mY = Math.min(mY, -0.5);
        mMY = Math.max(mMY, 0.5);

        const xRange = mMX - mX;
        const yRange = mMY - mY;

        return {
            minX: mX - xRange * 0.1,
            maxX: mMX + xRange * 0.1,
            minY: mY - yRange * 0.1,
            maxY: mMY + yRange * 0.1,
        };
    }, [computedSeries]);

    const scaleX = (x: number) => padding + ((x - minX) / (maxX - minX)) * innerWidth;
    const scaleY = (y: number) => padding + innerHeight - ((y - minY) / (maxY - minY)) * innerHeight;

    const originX = scaleX(0);
    const originY = scaleY(0);

    const hasData = computedSeries.some(s => s.points.length > 0);

    if (!hasData) {
        return <div style={{ padding: '20px', color: '#888' }}>Not enough data for RRG ({rrgTimeframe}-day)</div>;
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
            {/* Controls */}
            <div style={{ display: 'flex', gap: '15px', padding: '10px 20px', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.05)', flexWrap: 'wrap', flexShrink: 0 }}>
                <span style={{ color: '#aaa', fontSize: '13px' }}>RRG Timeframe:</span>
                <div style={{ display: 'flex', gap: '5px' }}>
                    {[14, 21, 63].map(tf => (
                        <button
                            key={tf}
                            className={`toggle-btn ${rrgTimeframe === tf ? 'active' : ''}`}
                            onClick={() => setRrgTimeframe(tf as any)}
                        >
                            {tf}d
                        </button>
                    ))}
                </div>

                <span style={{ color: '#aaa', fontSize: '13px', marginLeft: '10px' }}>Trail Length:</span>
                <div style={{ display: 'flex', gap: '5px' }}>
                    {[10, 20, 30, 60].map(tr => (
                        <button
                            key={tr}
                            className={`toggle-btn ${trailLength === tr ? 'active' : ''}`}
                            onClick={() => setTrailLength(tr)}
                        >
                            {tr}
                        </button>
                    ))}
                </div>
            </div>

            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto' }}>
                {/* Top Panel: RS Line Chart (Individual Mode Only) */}
                {isIndividualMode && data && (
                    <div style={{ padding: '20px 20px 0 20px', flexShrink: 0 }}>
                        <RsLineChart data={data} />
                    </div>
                )}

                <div style={{ flex: 1, display: 'flex', minHeight: '600px' }}>
                    {/* Left Panel: RS Minimaps (Individual Mode Only) */}
                    {isIndividualMode && minimaps && (
                        <div style={{ 
                            width: '180px', 
                            padding: '15px', 
                            borderRight: '1px solid rgba(255,255,255,0.05)', 
                            display: 'flex', 
                            flexDirection: 'column', 
                            gap: '20px',
                            flexShrink: 0,
                            overflowY: 'auto'
                        }}>
                            <div style={{ fontSize: '12px', color: '#aaa', fontWeight: 'bold', marginBottom: '-10px' }}>Relative Rank (30d)</div>
                            
                            {[
                                { label: 'RS Ratio', data: minimaps.ratio },
                                { label: 'RS Momentum', data: minimaps.mom },
                                { label: 'RS Condition', data: minimaps.cond },
                            ].map(m => (
                                <div key={m.label} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px' }}>
                                    <div style={{ fontSize: '10px', color: '#888', marginBottom: '8px' }}>{m.label} %Rank</div>
                                    <Sparkline 
                                        data={m.data} 
                                        width={140} 
                                        height={40} 
                                        color={m.data[m.data.length-1] > 0.5 ? appConfig.colors.good : appConfig.colors.bad}
                                        fixedRange={true}
                                    />
                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px', fontSize: '9px', color: '#555' }}>
                                        <span>30d ago</span>
                                        <span style={{ color: (m.data[m.data.length-1] || 0) > 0.8 ? appConfig.colors.good : 'inherit' }}>
                                            {((m.data[m.data.length-1] || 0) * 100).toFixed(0)}%
                                        </span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Main RRG SVG Area */}
                    <div style={{ flex: 1, position: 'relative', display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '20px', minHeight: 0 }}>
                        <svg
                            width="100%"
                            height="100%"
                            viewBox={`0 0 ${width} ${height}`}
                            preserveAspectRatio="xMidYMid meet"
                            style={{ background: '#131722', borderRadius: '8px', border: '1px solid #2B3139', flex: 1 }}
                        >
                            <defs>
                                <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
                                    <polygon points="0 0, 10 3.5, 0 7" fill="#fff" />
                                </marker>
                            </defs>

                            {/* Quadrant Backgrounds */}
                            <rect x={originX} y={padding}      width={Math.max(0, width - originX - padding)} height={Math.max(0, originY - padding)}              fill="rgba(38, 166, 154, 0.1)" />
                            <rect x={originX} y={originY}      width={Math.max(0, width - originX - padding)} height={Math.max(0, padding + innerHeight - originY)} fill="rgba(255, 235, 59, 0.05)" />
                            <rect x={padding} y={originY}      width={Math.max(0, originX - padding)}         height={Math.max(0, padding + innerHeight - originY)} fill="rgba(239, 83, 80, 0.1)" />
                            <rect x={padding} y={padding}      width={Math.max(0, originX - padding)}         height={Math.max(0, originY - padding)}               fill="rgba(41, 98, 255, 0.1)" />

                            {/* Quadrant Labels */}
                            <text x={width - padding - 10} y={padding + 20}          fill="rgba(38, 166, 154, 0.3)"  fontSize="20" fontWeight="bold" textAnchor="end">LEADING</text>
                            <text x={width - padding - 10} y={height - padding - 10} fill="rgba(255, 235, 59, 0.2)"  fontSize="20" fontWeight="bold" textAnchor="end">WEAKENING</text>
                            <text x={padding + 10}          y={height - padding - 10} fill="rgba(239, 83, 80, 0.3)"  fontSize="20" fontWeight="bold">LAGGING</text>
                            <text x={padding + 10}          y={padding + 20}          fill="rgba(41, 98, 255, 0.3)"  fontSize="20" fontWeight="bold">IMPROVING</text>

                            {/* Axes */}
                            <line x1={padding} y1={originY} x2={width - padding} y2={originY} stroke="#333" strokeWidth="2" />
                            <line x1={originX} y1={padding} x2={originX} y2={height - padding} stroke="#333" strokeWidth="2" />

                            {/* Axis Labels */}
                            <text x={width / 2} y={height - 15} fill="#888" fontSize="12" textAnchor="middle">RS-Ratio (Trend)</text>
                            <text x={15} y={height / 2} fill="#888" fontSize="12" textAnchor="middle" transform={`rotate(-90, 15, ${height / 2})`}>RS-Momentum (Rate of Change)</text>

                            {/* Render each series trail + points */}
                            {computedSeries.map(s => {
                                if (s.points.length === 0) return null;
                                const pathD = s.points.map((d, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(d.x)} ${scaleY(d.y)}`).join(' ');
                                return (
                                    <g key={s.ticker}>
                                        {/* Trail */}
                                        <path d={pathD} fill="none" stroke={s.color} strokeWidth="2" strokeOpacity="0.6" />

                                        {/* Trail Dots */}
                                        {s.points.map((d, i) => {
                                            const isLast = i === s.points.length - 1;
                                            const px = scaleX(d.x);
                                            const py = scaleY(d.y);
                                            return (
                                                <g key={i}
                                                    onMouseEnter={() => setHoveredPoint({ ticker: s.ticker, ...d })}
                                                    onMouseLeave={() => setHoveredPoint(null)}
                                                    style={{ cursor: 'pointer' }}
                                                >
                                                    <circle
                                                        cx={px}
                                                        cy={py}
                                                        r={isLast ? 7 : 3}
                                                        fill={isLast ? s.color : `${s.color}99`}
                                                        stroke="#131722"
                                                        strokeWidth={isLast ? 2 : 1}
                                                    />
                                                    {isLast && (
                                                        <text x={px + 10} y={py + 4} fill={s.color} fontSize="13" fontWeight="bold" style={{ textShadow: '1px 1px 2px #000' }}>
                                                            {s.ticker}
                                                        </text>
                                                    )}
                                                </g>
                                            );
                                        })}
                                    </g>
                                );
                            })}
                        </svg>

                        {/* Floating Tooltip */}
                        {hoveredPoint && (() => {
                            const px = scaleX(hoveredPoint.x);
                            const py = scaleY(hoveredPoint.y);
                            const series = computedSeries.find(s => s.ticker === hoveredPoint.ticker);
                            return (
                                <div style={{
                                    position: 'absolute',
                                    top: py + 15,
                                    left: px + 15,
                                    background: 'rgba(0,0,0,0.85)',
                                    border: `1px solid ${series?.color || '#444'}`,
                                    padding: '8px 12px',
                                    borderRadius: '4px',
                                    color: '#fff',
                                    fontSize: '12px',
                                    pointerEvents: 'none',
                                    zIndex: 10,
                                    boxShadow: '0 4px 12px rgba(0,0,0,0.5)'
                                }}>
                                    <div style={{ fontWeight: 'bold', marginBottom: '4px', color: series?.color || '#ffb74d' }}>{hoveredPoint.ticker} — {hoveredPoint.time}</div>
                                    <div>Close: ${hoveredPoint.close.toFixed(2)}</div>
                                    <div>Ratio: {hoveredPoint.x.toFixed(3)}</div>
                                    <div>Mom: {hoveredPoint.y.toFixed(3)}</div>
                                    {hoveredPoint.cond != null && <div>Cond: {hoveredPoint.cond.toFixed(3)}</div>}
                                </div>
                            );
                        })()}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default RrgChart;
