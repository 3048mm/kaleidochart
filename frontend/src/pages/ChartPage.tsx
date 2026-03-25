// frontend/src/pages/ChartPage.tsx
import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { createChart, IChartApi, ISeriesApi, CrosshairMode, SeriesMarker } from 'lightweight-charts';
import { Symbol, ChartDataPoint, EarningData } from '../types';
import { appConfig } from '../config';
import { RrgChart } from '../components/RrgChart';
import { SymbolDataTable } from '../components/SymbolDataTable';

interface ChartPageProps {
    symbols: Symbol[];
}

const getChange = (data: ChartDataPoint[], point: ChartDataPoint): number | null => {
    const idx = data.findIndex(d => d.time === point.time);
    if (idx <= 0) return null;
    const prev = data[idx - 1];
    return ((point.close - prev.close) / prev.close) * 100;
};

const formatMarketCap = (val: number): string => {
    if (Math.abs(val) >= 1.0e12) return `$${(val / 1.0e12).toFixed(2)}T`;
    if (Math.abs(val) >= 1.0e9) return `$${(val / 1.0e9).toFixed(2)}B`;
    if (Math.abs(val) >= 1.0e6) return `$${(val / 1.0e6).toFixed(2)}M`;
    return `$${val.toLocaleString()}`;
};

const EarningsChart: React.FC<{ earnings: EarningData[] }> = ({ earnings }) => {
    if (!earnings || earnings.length === 0) return null;

    const chartData = [...earnings].reverse();
    
    const maxVal = Math.max(
        ...chartData.map(e => e.revenue || 0),
        ...chartData.map(e => e.net_income || 0)
    ) || 1;
    const minVal = Math.min(
        0, 
        ...chartData.map(e => e.net_income || 0)
    );
    
    const paddedMax = maxVal * 1.1; 
    const range = paddedMax - minVal;
    const zeroY = (Math.abs(minVal) / range) * 100;

    return (
        <div style={{ padding: '0 0 30px', borderBottom: '1px solid rgba(255,255,255,0.1)', marginBottom: '30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                <h4 style={{ margin: 0, fontSize: '15px', color: '#ccc' }}>Quarterly Performance</h4>
                <div style={{ display: 'flex', gap: '15px', fontSize: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '12px', height: '12px', background: '#2962FF', borderRadius: '2px' }} />
                        <span style={{ color: '#888' }}>Revenue</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <div style={{ width: '12px', height: '12px', background: appConfig.colors.good, borderRadius: '2px' }} />
                        <span style={{ color: '#888' }}>Net Income</span>
                    </div>
                </div>
            </div>
            
            <div style={{ position: 'relative', height: '160px', display: 'flex', gap: '4px', overflowX: 'auto', paddingBottom: '20px' }}>
                {/* Zero Line */}
                <div style={{ position: 'absolute', left: 0, right: 0, bottom: `${zeroY}%`, height: '1px', background: 'rgba(255,255,255,0.2)', zIndex: 0 }} />
                
                {chartData.map(e => {
                    const rev = e.revenue || 0;
                    const net = e.net_income || 0;
                    
                    const revH = (Math.abs(rev) / range) * 100;
                    const netH = (Math.abs(net) / range) * 100;
                    
                    return (
                        <div key={e.period_date} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: '0 0 60px', height: '100%', position: 'relative', zIndex: 1 }} title={`Period: ${e.period_date}\nRevenue: ${formatMarketCap(rev)}\nNet Income: ${formatMarketCap(net)}`}>
                            
                            {/* Revenue Bar */}
                            {rev > 0 && (
                                <div style={{ width: '16px', height: `${revH}%`, background: '#2962FF', opacity: 0.9, borderRadius: '2px 2px 0 0', position: 'absolute', bottom: `${zeroY}%`, left: '12px', transition: 'height 0.3s' }} />
                            )}
                            
                            {/* Net Income Bar */}
                            {net > 0 && (
                                <div style={{ width: '16px', height: `${netH}%`, background: appConfig.colors.good, opacity: 0.9, borderRadius: '2px 2px 0 0', position: 'absolute', bottom: `${zeroY}%`, right: '12px', transition: 'height 0.3s' }} />
                            )}
                            {net < 0 && (
                                <div style={{ width: '16px', height: `${netH}%`, background: appConfig.colors.bad, opacity: 0.9, borderRadius: '0 0 2px 2px', position: 'absolute', top: `${100 - zeroY}%`, right: '12px', transition: 'height 0.3s' }} />
                            )}
                            
                            {/* X-axis Label */}
                            <div style={{ position: 'absolute', bottom: '-22px', fontSize: '11px', color: '#888', whiteSpace: 'nowrap' }}>
                                {e.period_date.substring(0, 7)}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};


export const ChartPage: React.FC<ChartPageProps> = ({ symbols }) => {
    const { ticker } = useParams<{ ticker: string }>();
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const [data, setData] = useState<ChartDataPoint[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [viewMode, setViewMode] = useState<'chart' | 'rs' | 'table' | 'fundamentals'>('chart');
    const [earnings, setEarnings] = useState<EarningData[]>([]);
    const [earningsLoading, setEarningsLoading] = useState(false);

    // State for hovering
    const [hoverData, setHoverData] = useState<ChartDataPoint | null>(null);

    // Compare symbol state
    const [compareTicker, setCompareTicker] = useState('');
    const [compareData, setCompareData] = useState<ChartDataPoint[]>([]);
    const [compareLoading, setCompareLoading] = useState(false);

    // active toggles
    const [showSma21, setShowSma21] = useState(true);
    const [showSma50, setShowSma50] = useState(true);
    const [showSma63, setShowSma63] = useState(false);
    const [showSma150, setShowSma150] = useState(false);
    const [showSma200, setShowSma200] = useState(false);
    const [showEma5, setShowEma5] = useState(false);
    const [showEma21, setShowEma21] = useState(false);
    const [showEma50, setShowEma50] = useState(false);
    const [showEma63, setShowEma63] = useState(false);
    const [showEma200, setShowEma200] = useState(false);
    const [showVolume, setShowVolume] = useState(true);
    const [showTd9, setShowTd9] = useState(true);

    const chartRef = useRef<IChartApi | null>(null);
    const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const smaSeriesRefs = useRef<Record<string, ISeriesApi<"Line"> | null>>({});
    const emaSeriesRefs = useRef<Record<string, ISeriesApi<"Line"> | null>>({});

    const selected = useMemo(() => {
        const parsedTicker = ticker?.includes(':') ? ticker.split(':')[1] : ticker;
        return symbols.find(s => {
            const symIdentifier = s.exchange ? `${s.exchange}:${s.ticker}` : s.ticker;
            return symIdentifier === ticker || s.ticker === ticker || s.ticker === parsedTicker;
        });
    }, [symbols, ticker]);

    useEffect(() => {
        if (!selected) return;
        setLoading(true);
        setError('');
        fetch(`/api/chart/${selected.id}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch data');
                return res.json();
            })
            .then(json => {
                setData(json);
            })
            .catch(err => {
                console.error(err);
                setError(err.message);
            })
            .finally(() => setLoading(false));

        setEarningsLoading(true);
        fetch(`/api/earnings/${selected.id}`)
            .then(res => res.ok ? res.json() : [])
            .then(json => setEarnings(json))
            .catch(err => console.error(err))
            .finally(() => setEarningsLoading(false));
    }, [selected]);

    // Fetch comparison data when user requests
    const handleCompare = (e: React.FormEvent) => {
        e.preventDefault();
        if (!compareTicker) {
            setCompareData([]);
            return;
        }

        const compSymbol = symbols.find(s => s.ticker.toUpperCase() === compareTicker.toUpperCase());
        if (!compSymbol) {
            alert(`Symbol ${compareTicker} not found in database.`);
            return;
        }

        setCompareLoading(true);
        fetch(`/api/chart/${compSymbol.id}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch comparison data');
                return res.json();
            })
            .then(json => setCompareData(json))
            .catch(err => {
                console.error(err);
                alert(`Error loading comparison: ${err.message}`);
                setCompareData([]);
            })
            .finally(() => setCompareLoading(false));
    };

    useEffect(() => {
        if (!chartContainerRef.current || data.length === 0) return;

        const chart = createChart(chartContainerRef.current, {
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
            },
            autoSize: true,
        });

        chartRef.current = chart;

        const candleSeries = chart.addCandlestickSeries({
            upColor: '#00ff88',
            downColor: '#ff4444',
            borderVisible: false,
            wickUpColor: '#00ff88',
            wickDownColor: '#ff4444',
        });
        candleSeriesRef.current = candleSeries;

        const candleData = data.map(d => ({
            time: d.time as any,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));
        candleSeries.setData(candleData);

        // --- Volume Histogram ---
        if (showVolume) {
            const volSeries = chart.addHistogramSeries({
                color: 'rgba(99, 120, 180, 0.3)',
                priceFormat: { type: 'volume' },
                priceScaleId: 'vol',
            });
            chart.priceScale('vol').applyOptions({
                scaleMargins: { top: 0.85, bottom: 0 },
            });
            volSeries.setData(data.map(d => ({
                time: d.time as any,
                value: d.volume,
                color: d.close >= d.open ? 'rgba(0, 255, 136, 0.25)' : 'rgba(255, 68, 68, 0.25)',
            })));
        }

        // --- Comparison Series ---
        if (compareData.length > 0) {
            const compSeries = chart.addLineSeries({
                color: '#E040FB',
                lineWidth: 2,
                title: `Vs ${compareTicker.toUpperCase()}`,
                priceScaleId: 'left',
                crosshairMarkerVisible: false,
            });

            const cData = compareData.map(d => ({
                time: d.time as any,
                value: d.close
            }));

            compSeries.setData(cData);

            chart.priceScale('left').applyOptions({
                visible: true,
                borderColor: 'rgba(255, 255, 255, 0.1)',
            });
        }

        // --- Markers for TD9 and ATR Divergence ---
        const markers: SeriesMarker<any>[] = [];

        // Pre-compute which indices should show TD9 (only complete 1-9 sequences, or latest >=6)
        const validTd9Indices = new Set<number>();
        for (let i = 0; i < data.length; i++) {
            const td9 = data[i].td9;
            if (!td9) continue;
            const absTd9 = Math.abs(td9);

            if (absTd9 >= 9) {
                let j = i;
                while (j >= 0 && data[j].td9 && Math.sign(data[j].td9!) === Math.sign(td9) && Math.abs(data[j].td9!) <= absTd9) {
                    if (Math.abs(data[j].td9!) <= 9) validTd9Indices.add(j);
                    if (Math.abs(data[j].td9!) === 1) break;
                    j--;
                }
            }

            if (i === data.length - 1 && absTd9 >= 6) {
                let j = i;
                while (j >= 0 && data[j].td9 && Math.sign(data[j].td9!) === Math.sign(td9)) {
                    if (Math.abs(data[j].td9!) <= 9) validTd9Indices.add(j);
                    if (Math.abs(data[j].td9!) === 1) break;
                    j--;
                }
            }
        }

        // TD9 markers first (closest to bars)
        if (showTd9) {
            data.forEach((d, idx) => {
                if (!validTd9Indices.has(idx)) return;
                const td9Val = Math.abs(d.td9!);
                markers.push({
                    time: d.time as any,
                    position: d.td9! > 0 ? 'aboveBar' : 'belowBar',
                    color: d.td9! > 0 ? appConfig.colors.bad : appConfig.colors.good,
                    shape: 'square',
                    text: td9Val.toString(),
                    size: 0,
                });
            });
        }

        // ATR markers second (stacked further from bars, above TD9)
        data.forEach((d) => {
            const dist = d.dist_sma50_atr != null ? d.dist_sma50_atr : 0;
            // Only show markers when rising (dist > 0)
            const showAtrRed = dist >= appConfig.thresholds.atr_multiple_red;
            const showAtrYellow = dist >= appConfig.thresholds.atr_multiple_yellow;

            if (showAtrRed || showAtrYellow) {
                markers.push({
                    time: d.time as any,
                    position: (d.dist_sma50_atr || 0) > 0 ? 'aboveBar' : 'belowBar',
                    color: showAtrRed ? '#ff0000' : '#ffff00',
                    shape: 'circle',
                    text: '',
                    size: 2,
                });
            }
        });

        // Sort markers by time (required by lightweight-charts)
        markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));

        candleSeries.setMarkers(markers);

        // Factory for moving averages
        const addMaSeries = (
            key: string,
            dataField: keyof ChartDataPoint,
            color: string,
            visible: boolean,
            refsObj: React.MutableRefObject<Record<string, ISeriesApi<"Line"> | null>>
        ) => {
            const seriesData = data.filter(d => d[dataField] != null).map(d => ({
                time: d.time as any,
                value: d[dataField] as number
            }));
            const series = chart.addLineSeries({
                color,
                lineWidth: 1,
                title: key.toUpperCase(),
                visible,
                crosshairMarkerVisible: false,
            });
            series.setData(seriesData);
            refsObj.current[key] = series;
        };

        // SMAs
        addMaSeries('sma21', 'sma_21', '#2962FF', showSma21, smaSeriesRefs);
        addMaSeries('sma50', 'sma_50', '#FF6D00', showSma50, smaSeriesRefs);
        addMaSeries('sma63', 'sma_63', '#9c27b0', showSma63, smaSeriesRefs);
        addMaSeries('sma150', 'sma_150', '#00bcd4', showSma150, smaSeriesRefs);
        addMaSeries('sma200', 'sma_200', '#f44336', showSma200, smaSeriesRefs);

        // EMAs
        addMaSeries('ema5', 'ema_5', '#e91e63', showEma5, emaSeriesRefs);
        addMaSeries('ema21', 'ema_21', '#3f51b5', showEma21, emaSeriesRefs);
        addMaSeries('ema50', 'ema_50', '#ff9800', showEma50, emaSeriesRefs);
        addMaSeries('ema63', 'ema_63', '#8bc34a', showEma63, emaSeriesRefs);
        addMaSeries('ema200', 'ema_200', '#795548', showEma200, emaSeriesRefs);

        chart.subscribeCrosshairMove((param) => {
            if (param.time && param.seriesData.size > 0) {
                const point = data.find(d => d.time === param.time);
                if (point) {
                    setHoverData(point);
                    return;
                }
            }
            setHoverData(null);
        });

        chart.timeScale().fitContent();

        return () => {
            chart.remove();
        };

    }, [
        data, compareData, showVolume, showTd9,
        showSma21, showSma50, showSma63, showSma150, showSma200,
        showEma5, showEma21, showEma50, showEma63, showEma200
    ]);

    const latest = data.length > 0 ? data[data.length - 1] : null;

    // Change % for latest and hover
    const latestChange = useMemo(() => {
        if (!latest) return null;
        return getChange(data, latest);
    }, [latest, data]);

    const hoverChange = useMemo(() => {
        if (!hoverData) return null;
        return getChange(data, hoverData);
    }, [hoverData, data]);

    if (!selected) return <div className="glass-panel" style={{ padding: '20px' }}>Symbol not found.</div>;

    // TradingView URL
    const tradingViewUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(selected.exchange ? `${selected.exchange}:${selected.ticker}` : selected.ticker)}`;

    function StatRow({ label, point, change }: { label: string, point: ChartDataPoint, change: number | null }) {
        const color = point.close >= point.open ? appConfig.colors.good : appConfig.colors.bad;
        return (
            <tr style={{ fontSize: '12px' }}>
                <td style={{ padding: '2px 10px', textAlign: 'left', fontWeight: 'bold' }}>{label}</td>
                <td style={{ padding: '2px 10px', textAlign: 'left' }}>{point.time}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right', color }}>{point.close.toFixed(2)}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right', color: change && change >= 0 ? appConfig.colors.good : appConfig.colors.bad }}>
                    {change != null ? `${change.toFixed(2)}%` : '-'}
                </td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_21?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_50?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_200?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.adr_pct_21?.toFixed(1) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right', color: (point.dist_sma50_atr || 0) >= appConfig.thresholds.atr_multiple_red ? '#ff0000' : (point.dist_sma50_atr || 0) >= appConfig.thresholds.atr_multiple_yellow ? '#ffff00' : 'inherit' }}>
                    {point.dist_sma50_atr?.toFixed(1) ?? '-'}
                </td>
            </tr>
        );
    }

    function PlaceholderRow() {
        return (
            <tr style={{ fontSize: '12px', opacity: 0.5 }}>
                <td style={{ padding: '2px 10px', textAlign: 'left', fontWeight: 'bold' }}>Cursor</td>
                <td style={{ padding: '2px 10px', textAlign: 'left' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
            </tr>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ padding: '10px 20px', display: 'flex', alignItems: 'center', gap: '20px' }}>
                <Link to="/" style={{ color: '#00ff88', textDecoration: 'none' }}>&larr; Dashboard</Link>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px' }}>
                    <span style={{ fontSize: '18px', fontWeight: 700 }}>{selected.ticker}</span>
                    <span style={{ fontSize: '12px', color: '#888' }}>{selected.name} ({selected.category})</span>
                    {latest && latest.market_cap && (
                        <span style={{ fontSize: '12px', color: '#aaa', marginLeft: '10px', padding: '2px 8px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px' }}>
                            Market Cap: <strong style={{ color: '#fff' }}>{formatMarketCap(latest.market_cap)}</strong>
                        </span>
                    )}
                </div>
                <a href={tradingViewUrl} target="_blank" rel="noopener noreferrer" style={{ color: '#2962FF', textDecoration: 'none', fontSize: '12px', marginLeft: 'auto' }}>
                    TradingView ↗
                </a>
            </div>

            <div style={{ padding: '0 20px', display: 'flex', gap: '10px', marginBottom: '10px' }}>
                <button
                    className={`toggle-btn ${viewMode === 'chart' ? 'active' : ''}`}
                    onClick={() => setViewMode('chart')}
                    style={{ padding: '6px 16px', fontSize: '14px', borderRadius: '4px' }}
                >
                    Chart View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'rs' ? 'active' : ''}`}
                    onClick={() => setViewMode('rs')}
                    style={{ padding: '6px 16px', fontSize: '14px', borderRadius: '4px' }}
                >
                    RS View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'table' ? 'active' : ''}`}
                    onClick={() => setViewMode('table')}
                    style={{ padding: '6px 16px', fontSize: '14px', borderRadius: '4px' }}
                >
                    Data View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'fundamentals' ? 'active' : ''}`}
                    onClick={() => setViewMode('fundamentals')}
                    style={{ padding: '6px 16px', fontSize: '14px', borderRadius: '4px' }}
                >
                    Fundamentals
                </button>
            </div>

            {error && <div style={{ color: '#ff4444', padding: '0 20px' }}>{error}</div>}

            {/* === Two-row Stats Table === */}
            {latest && (
                <div style={{ padding: '0 20px 6px 20px', overflowX: 'auto' }}>
                    <table style={{ borderCollapse: 'collapse', fontSize: '12px', maxWidth: '800px' }}>
                        <thead>
                            <tr style={{ color: '#555', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                <th style={{ padding: '2px 10px', textAlign: 'left' }}></th>
                                <th style={{ padding: '2px 10px', textAlign: 'left' }}>Date</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>Close</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>Chg%</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>SMA21</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>SMA50</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>SMA200</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>ADR%(21)</th>
                                <th style={{ padding: '2px 10px', textAlign: 'right' }}>SMA50/ATR%(14)</th>
                            </tr>
                        </thead>
                        <tbody>
                            <StatRow label="Latest" point={latest} change={latestChange} />
                            {hoverData && hoverData.time !== latest.time ? (
                                <StatRow label="Cursor" point={hoverData} change={hoverChange} />
                            ) : (
                                <PlaceholderRow />
                            )}
                        </tbody>
                    </table>
                </div>
            )}

            <div className="main-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <main className="chart-area glass-panel" style={{ flex: 1, display: viewMode === 'rs' && data.length > 0 ? 'flex' : 'none', flexDirection: 'column', margin: '0 20px 20px 20px' }}>
                    <RrgChart data={data} ticker={selected.ticker} />
                </main>
                {viewMode === 'table' && <SymbolDataTable data={data} />}
                <main className="chart-area glass-panel" style={{ flex: 1, display: viewMode === 'chart' ? 'flex' : 'none', flexDirection: 'column', margin: '0 20px 20px 20px' }}>
                    {loading && <div className="loading">Loading chart data...</div>}
                    {!loading && data.length > 0 && (
                        <>
                            <div className="chart-controls" style={{ display: 'flex', flexWrap: 'wrap', gap: '5px', padding: '10px' }}>
                                <span style={{ color: '#aaa', marginRight: '10px', alignSelf: 'center' }}>SMA:</span>
                                <button className={`toggle-btn ${showSma21 ? 'active' : ''}`} onClick={() => setShowSma21(!showSma21)}>21</button>
                                <button className={`toggle-btn ${showSma50 ? 'active' : ''}`} onClick={() => setShowSma50(!showSma50)}>50</button>
                                <button className={`toggle-btn ${showSma63 ? 'active' : ''}`} onClick={() => setShowSma63(!showSma63)}>63</button>
                                <button className={`toggle-btn ${showSma150 ? 'active' : ''}`} onClick={() => setShowSma150(!showSma150)}>150</button>
                                <button className={`toggle-btn ${showSma200 ? 'active' : ''}`} onClick={() => setShowSma200(!showSma200)}>200</button>

                                <span style={{ color: '#aaa', margin: '0 10px', alignSelf: 'center' }}>EMA:</span>
                                <button className={`toggle-btn ${showEma5 ? 'active' : ''}`} onClick={() => setShowEma5(!showEma5)}>5</button>
                                <button className={`toggle-btn ${showEma21 ? 'active' : ''}`} onClick={() => setShowEma21(!showEma21)}>21</button>
                                <button className={`toggle-btn ${showEma50 ? 'active' : ''}`} onClick={() => setShowEma50(!showEma50)}>50</button>
                                <button className={`toggle-btn ${showEma63 ? 'active' : ''}`} onClick={() => setShowEma63(!showEma63)}>63</button>
                                <button className={`toggle-btn ${showEma200 ? 'active' : ''}`} onClick={() => setShowEma200(!showEma200)}>200</button>

                                <div style={{ borderLeft: '1px solid #333', margin: '0 10px', height: '24px', alignSelf: 'center' }}></div>

                                <button className={`toggle-btn ${showVolume ? 'active' : ''}`} onClick={() => setShowVolume(!showVolume)}>Vol</button>
                                <button className={`toggle-btn ${showTd9 ? 'active' : ''}`} onClick={() => setShowTd9(!showTd9)}>TD9</button>

                                <div style={{ borderLeft: '1px solid #333', margin: '0 10px', height: '24px', alignSelf: 'center' }}></div>

                                {/* Compare Logic */}
                                <form onSubmit={handleCompare} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                                    <span style={{ color: '#aaa' }}>Vs:</span>
                                    <input
                                        type="text"
                                        value={compareTicker}
                                        onChange={e => setCompareTicker(e.target.value)}
                                        placeholder="Ticker..."
                                        style={{
                                            padding: '4px 8px',
                                            borderRadius: '4px',
                                            background: 'rgba(255,255,255,0.1)',
                                            border: '1px solid rgba(255,255,255,0.2)',
                                            color: '#fff',
                                            width: '80px',
                                            textTransform: 'uppercase'
                                        }}
                                    />
                                    <button type="submit" className="toggle-btn active" style={{ padding: '4px 12px' }}>
                                        {compareLoading ? '...' : 'Compare'}
                                    </button>
                                    {compareData.length > 0 && (
                                        <button type="button" className="toggle-btn" onClick={() => { setCompareTicker(''); setCompareData([]); }}>
                                            Clear
                                        </button>
                                    )}
                                </form>
                            </div>
                            <div ref={chartContainerRef} className="chart-container" />
                        </>
                    )}
                    {!loading && data.length === 0 && !error && (
                        <div className="no-data">Select a symbol to view chart</div>
                    )}
                </main>
                {viewMode === 'fundamentals' && (
                    <main className="chart-area glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', margin: '0 20px 20px 20px', padding: '20px', overflowY: 'auto' }}>
                        <h3 style={{ margin: '0 0 20px 0', fontSize: '18px', fontWeight: 600 }}>Earnings & Fundamentals</h3>
                        
                        {earningsLoading ? (
                            <div className="loading" style={{ padding: '40px', textAlign: 'center' }}>Loading earnings data...</div>
                        ) : earnings.length === 0 ? (
                            <div style={{ color: '#aaa', padding: '40px', textAlign: 'center' }}>No quarterly earnings data available for this symbol.</div>
                        ) : (
                            <div style={{ overflowX: 'auto', display: 'flex', flexDirection: 'column' }}>
                                <EarningsChart earnings={earnings} />
                                
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px', minWidth: '600px' }}>
                                    <thead>
                                        <tr style={{ color: '#888', borderBottom: '1px solid rgba(255,255,255,0.1)', fontSize: '12px', textTransform: 'uppercase' }}>
                                            <th style={{ padding: '12px 10px', textAlign: 'left' }}>Period Date</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>Revenue</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>Net Income</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>EPS (Basic)</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>EPS (Diluted)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {earnings.map(e => (
                                            <tr key={e.period_date} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                                <td style={{ padding: '12px 10px', textAlign: 'left', fontWeight: 'bold' }}>{e.period_date}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right' }}>{e.revenue ? formatMarketCap(e.revenue) : '-'}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: e.net_income && e.net_income > 0 ? appConfig.colors.good : e.net_income && e.net_income < 0 ? appConfig.colors.bad : 'inherit' }}>
                                                    {e.net_income ? formatMarketCap(e.net_income) : '-'}
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: e.eps_basic && e.eps_basic > 0 ? appConfig.colors.good : e.eps_basic && e.eps_basic < 0 ? appConfig.colors.bad : 'inherit' }}>
                                                    {e.eps_basic != null ? e.eps_basic.toFixed(2) : '-'}
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: e.eps_diluted && e.eps_diluted > 0 ? appConfig.colors.good : e.eps_diluted && e.eps_diluted < 0 ? appConfig.colors.bad : 'inherit' }}>
                                                    {e.eps_diluted != null ? e.eps_diluted.toFixed(2) : '-'}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </main>
                )}
            </div>
        </div>
    );
};
