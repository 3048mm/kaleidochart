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
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

    React.useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth <= 768);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

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
            const condKey = `rs_trend_s${rrgTimeframe}` as keyof ChartDataPoint;

            const sData = s.data || [];
            const points = sData.filter(d => d[ratioKey] != null && d[momKey] != null).map(d => ({
                time: d.time as string,
                x: d[ratioKey] as number,
                y: d[momKey] as number,
                cond: d[condKey] as number | undefined,
                close: d.close,
            })).slice(-Math.abs(trailLength));

            return { ...s, points };
        });
    }, [allSeries, rrgTimeframe, trailLength]);

    // Minimap data (3x3 Matrix, last 30 days)
    const minimaps3x3 = useMemo(() => {
        if (!isIndividualMode || !data || data.length === 0) return null;
        
        const last30 = data.slice(-30);
        const timeframes: (14 | 21 | 63)[] = [14, 21, 63];
        
        const res: Record<14 | 21 | 63, { ratio: number[]; mom: number[]; cond: number[] }> = {
            14: { ratio: [], mom: [], cond: [] },
            21: { ratio: [], mom: [], cond: [] },
            63: { ratio: [], mom: [], cond: [] },
        };
        
        timeframes.forEach(tf => {
            res[tf] = {
                ratio: last30.map(d => (d[`rank_rs_ratio_${tf}` as keyof ChartDataPoint] as number) || 0),
                mom: last30.map(d => (d[`rank_rs_momentum_${tf}` as keyof ChartDataPoint] as number) || 0),
                cond: last30.map(d => (d[`rs_trend_rank_s${tf}` as keyof ChartDataPoint] as number) || 0),
            };
        });
        
        return res;
    }, [isIndividualMode, data]);

    // SVG dimensions (Internal coordinate space)
    const width = 800;
    const height = 800;
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

    const renderControls = () => (
        <div style={{ 
            display: 'flex', 
            flexDirection: isMobile ? 'column' : 'row', 
            gap: isMobile ? '6px' : '15px', 
            padding: isMobile ? '4px 0' : '10px 20px', 
            alignItems: isMobile ? 'flex-start' : 'center', 
            borderBottom: isMobile ? 'none' : '1px solid rgba(255,255,255,0.05)', 
            flexShrink: 0 
        }}>
            {/* Timeframe */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#aaa', fontSize: isMobile ? '11px' : '13px' }}>RRG Timeframe:</span>
                <div style={{ display: 'flex', gap: '3px' }}>
                    {[14, 21, 63].map(tf => (
                        <button
                            key={tf}
                            className={`toggle-btn ${rrgTimeframe === tf ? 'active' : ''}`}
                            onClick={() => setRrgTimeframe(tf as any)}
                            style={isMobile ? { padding: '4px 8px', fontSize: '11px' } : undefined}
                        >
                            {tf}d
                        </button>
                    ))}
                </div>
            </div>

            {/* Trail */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: '#aaa', fontSize: isMobile ? '11px' : '13px', marginLeft: isMobile ? '0' : '10px' }}>Trail:</span>
                <div style={{ display: 'flex', gap: '3px' }}>
                    {[10, 20, 30, 60].map(tr => (
                        <button
                            key={tr}
                            className={`toggle-btn ${trailLength === tr ? 'active' : ''}`}
                            onClick={() => setTrailLength(tr)}
                            style={isMobile ? { padding: '4px 8px', fontSize: '11px' } : undefined}
                        >
                            {tr}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );

    const hasData = computedSeries.some(s => s.points.length > 0);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
            {/* Note: Top controls removed. Controls are now rendered via renderControls helper */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflowY: 'auto' }}>
                {/* 1. Top Panel: RS Line Chart (Individual Mode Only) */}
                {isIndividualMode && data && (
                    <div style={{ padding: isMobile ? '10px 10px 0 10px' : '20px 20px 0 20px', flexShrink: 0 }}>
                        <RsLineChart data={data} height={isMobile ? 160 : 250} />
                    </div>
                )}

                {/* PC Only: Global RRG Controls placed above both Minimaps and RRG Graph */}
                <div style={{ 
                    flex: 1, 
                    display: 'flex', 
                    flexDirection: 'column', 
                    minHeight: 0,
                    paddingBottom: '20px'
                }}>
                    {/* 2. Left Panel: RS Minimaps (Individual Mode, PC ONLY - 3x3 Matrix) */}
                    {isIndividualMode && minimaps3x3 && !isMobile && (
                        <div style={{ 
                            padding: '15px 20px', 
                            borderBottom: '1px solid rgba(255,255,255,0.05)', 
                            display: 'flex', 
                            flexDirection: 'column', 
                            gap: '15px',
                            flexShrink: 0,
                            maxWidth: '680px' // 横伸びを防ぐために最大幅を制限
                        }}>
                            <div style={{ fontSize: '12px', color: '#aaa', fontWeight: 'bold', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '6px' }}>
                                Relative Rank (30d) Matrix
                            </div>
                            
                            {/* Matrix Column Headers */}
                            <div style={{ display: 'flex', alignItems: 'center', fontSize: '11px', color: '#666', fontWeight: 'bold', paddingBottom: '4px', textAlign: 'center' }}>
                                <div style={{ width: '85px', textAlign: 'left' }}>Indicator</div>
                                <div style={{ flex: 1 }}>RS14</div>
                                <div style={{ flex: 1 }}>RS21</div>
                                <div style={{ flex: 1 }}>RS63</div>
                            </div>
                            
                            {/* Matrix Rows */}
                            {[
                                { key: 'ratio' as const, label: 'RS Ratio', pctLabel: '%Rank' },
                                { key: 'mom' as const, label: 'RS Momentum', pctLabel: '%Rank' },
                                { key: 'cond' as const, label: 'RS Trend', pctLabel: '%Rank' }
                            ].map(row => (
                                <div key={row.key} style={{ display: 'flex', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.02)' }}>
                                    {/* Row Header Label */}
                                    <div style={{ width: '85px', display: 'flex', flexDirection: 'column' }}>
                                        <span style={{ fontSize: '11px', color: '#ccc', fontWeight: 'bold' }}>{row.label.split(' ')[1]}</span>
                                        <span style={{ fontSize: '8px', color: '#555' }}>{row.pctLabel}</span>
                                    </div>
                                    
                                    {/* 3 Columns (14d, 21d, 63d) */}
                                    {([14, 21, 63] as const).map(tf => {
                                        const d = minimaps3x3[tf][row.key];
                                        const lastVal = d[d.length - 1] || 0;
                                        return (
                                            <div key={tf} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', background: 'rgba(255,255,255,0.015)', margin: '0 6px', padding: '6px', borderRadius: '4px', maxWidth: '170px' }}>
                                                <Sparkline 
                                                    data={d} 
                                                    width={125} 
                                                    height={26} 
                                                    color={lastVal > 0.5 ? appConfig.colors.good : appConfig.colors.bad}
                                                    fixedRange={true}
                                                />
                                                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', marginTop: '3px', fontSize: '9px', color: '#555', boxSizing: 'border-box', padding: '0 2px' }}>
                                                    <span>30d</span>
                                                    <span style={{ color: lastVal > 0.8 ? appConfig.colors.good : 'inherit', fontWeight: 'bold' }}>
                                                        {(lastVal * 100).toFixed(0)}%
                                                    </span>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* 2.2 Middle Panel: RS Minimaps (Individual Mode, MOBILE ONLY - 3x3 Matrix) */}
                    {isIndividualMode && minimaps3x3 && isMobile && (
                        <div style={{ 
                            padding: '10px 12px', 
                            borderTop: '1px solid rgba(255,255,255,0.05)', 
                            borderBottom: '1px solid rgba(255,255,255,0.05)', 
                            display: 'flex', 
                            flexDirection: 'column', 
                            gap: '8px',
                            flexShrink: 0
                        }}>
                            <div style={{ fontSize: '11px', color: '#aaa', fontWeight: 'bold' }}>Relative Rank (30d) Matrix</div>
                            
                            {/* Mobile Columns Headers */}
                            <div style={{ display: 'flex', fontSize: '9px', color: '#555', fontWeight: 'bold', paddingLeft: '65px', textAlign: 'center' }}>
                                <div style={{ flex: 1 }}>RS14</div>
                                <div style={{ flex: 1 }}>RS21</div>
                                <div style={{ flex: 1 }}>RS63</div>
                            </div>

                            {/* Mobile Rows */}
                            {[
                                { key: 'ratio' as const, label: 'RS Ratio' },
                                { key: 'mom' as const, label: 'RS Momentum' },
                                { key: 'cond' as const, label: 'RS Trend' }
                            ].map(row => (
                                <div key={row.key} style={{ display: 'flex', alignItems: 'center', padding: '2px 0' }}>
                                    {/* Row label on the left */}
                                    <div style={{ width: '60px', fontSize: '10px', color: '#ccc', fontWeight: 'bold', display: 'flex', flexDirection: 'column' }}>
                                        <span>{row.label.split(' ')[1]}</span>
                                        <span style={{ fontSize: '8px', color: '#555', fontWeight: 'normal' }}>%Rank</span>
                                    </div>
                                    
                                    {/* 3 Columns */}
                                    {([14, 21, 63] as const).map(tf => {
                                        const d = minimaps3x3[tf][row.key];
                                        const lastVal = d[d.length - 1] || 0;
                                        return (
                                            <div key={tf} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', background: 'rgba(255,255,255,0.01)', margin: '0 2px', padding: '4px', borderRadius: '4px' }}>
                                                <Sparkline 
                                                    data={d} 
                                                    width={75} 
                                                    height={22} 
                                                    color={lastVal > 0.5 ? appConfig.colors.good : appConfig.colors.bad}
                                                    fixedRange={true}
                                                />
                                                <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: '8px', color: '#444', marginTop: '1px', padding: '0 1px', boxSizing: 'border-box' }}>
                                                    <span>30d</span>
                                                    <span style={{ color: lastVal > 0.8 ? appConfig.colors.good : 'inherit', fontWeight: 'bold' }}>
                                                        {(lastVal * 100).toFixed(0)}%
                                                    </span>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* 3. Main RRG Plot Area */}
                    <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                        {/* RRG Controls (PC / Mobile 共通でRRGグラフの直上に配置) */}
                        <div style={{ padding: isMobile ? '8px 12px 2px 12px' : '10px 20px 0 20px', flexShrink: 0 }}>
                            {renderControls()}
                        </div>

                        {/* 6. Main RRG SVG Area */}
                        <div style={{ 
                            flexShrink: 0, 
                            position: 'relative', 
                            display: 'flex', 
                            justifyContent: 'center', 
                            alignItems: 'center', 
                            padding: isMobile ? '10px' : '20px', 
                            height: isMobile ? '380px' : '600px',
                            boxSizing: 'border-box'
                        }}>
                            {!hasData ? (
                                <div style={{ 
                                    color: '#888', 
                                    fontSize: isMobile ? '12px' : '14px', 
                                    textAlign: 'center',
                                    padding: '40px',
                                    background: '#131722',
                                    borderRadius: '8px',
                                    border: '1px solid #2B3139',
                                    width: '100%',
                                    height: '100%',
                                    display: 'flex',
                                    justifyContent: 'center',
                                    alignItems: 'center',
                                    boxSizing: 'border-box',
                                    minHeight: '300px'
                                }}>
                                    Not enough data for RRG ({rrgTimeframe}-day)
                                </div>
                            ) : (
                                <svg
                                    viewBox={`0 0 ${width} ${height}`}
                                    preserveAspectRatio="xMidYMid meet"
                                    style={{ 
                                        background: '#131722', 
                                        borderRadius: '8px', 
                                        border: '1px solid #2B3139',
                                        width: '100%',
                                        height: '100%',
                                        maxWidth: isMobile ? '100%' : '600px',
                                        maxHeight: isMobile ? '380px' : '600px',
                                        aspectRatio: '1 / 1'
                                    }}
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
                                    <text x={width - padding - 15} y={padding + 30}          fill="rgba(38, 166, 154, 0.25)" fontSize="26" fontWeight="bold" textAnchor="end">LEADING</text>
                                    <text x={width - padding - 15} y={height - padding - 15} fill="rgba(255, 235, 59, 0.15)" fontSize="26" fontWeight="bold" textAnchor="end">WEAKENING</text>
                                    <text x={padding + 15}          y={height - padding - 15} fill="rgba(239, 83, 80, 0.25)" fontSize="26" fontWeight="bold">LAGGING</text>
                                    <text x={padding + 15}          y={padding + 30}          fill="rgba(41, 98, 255, 0.25)" fontSize="26" fontWeight="bold">IMPROVING</text>

                                    {/* Axes */}
                                    <line x1={padding} y1={originY} x2={width - padding} y2={originY} stroke="#333" strokeWidth="2" />
                                    <line x1={originX} y1={padding} x2={originX} y2={height - padding} stroke="#333" strokeWidth="2" />

                                    {/* Axis Labels */}
                                    <text x={width / 2} y={height - 15} fill="#aaa" fontSize="16" fontWeight="bold" textAnchor="middle">RS-Ratio (Trend)</text>
                                    <text x={20} y={height / 2} fill="#aaa" fontSize="16" fontWeight="bold" textAnchor="middle" transform={`rotate(-90, 20, ${height / 2})`}>RS-Momentum (Rate of Change)</text>

                                    {/* Render each series trail + points */}
                                    {computedSeries.map(s => {
                                        if (s.points.length === 0) return null;
                                        const pathD = s.points.map((d, i) => `${i === 0 ? 'M' : 'L'} ${scaleX(d.x)} ${scaleY(d.y)}`).join(' ');
                                        return (
                                            <g key={s.ticker}>
                                                {/* Trail */}
                                                <path d={pathD} fill="none" stroke={s.color} strokeWidth="3.5" strokeOpacity="0.8" />

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
                                                                r={isLast ? 9 : 4}
                                                                fill={isLast ? s.color : `${s.color}99`}
                                                                stroke="#131722"
                                                                strokeWidth={isLast ? 2.5 : 1}
                                                            />
                                                            {isLast && (
                                                                <text x={px + 12} y={py + 4} fill={s.color} fontSize="14" fontWeight="bold" style={{ textShadow: '1px 1px 2px #000' }}>
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
                            )}

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
        </div>
    );
};

export default RrgChart;
