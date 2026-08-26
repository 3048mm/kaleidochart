// frontend/src/pages/ChartPage.tsx
import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { createChart, IChartApi, ISeriesApi, CrosshairMode, SeriesMarker } from 'lightweight-charts';
import { Symbol, ChartDataPoint, EarningData, ChartResponse, ChartSymbolMeta, StructurePivot } from '../types';
import { buildStructureMarkers, buildStructureSegments, fetchStructurePivot } from '../api/structurePivot';
import { appConfig } from '../config';
import { RrgChart } from '../components/RrgChart';
import { SymbolDataTable } from '../components/SymbolDataTable';
import { useWatchlist } from '../hooks/useWatchlist';
import { WatchlistButton } from '../components/WatchlistButton';


interface ChartPageProps {
    symbols: Symbol[];
}

const getChange = (data: ChartDataPoint[], point: ChartDataPoint): number | null => {
    const idx = data.findIndex(d => d.time === point.time);
    if (idx <= 0) return null;
    const prev = data[idx - 1];
    return ((point.close - prev.close) / prev.close) * 100;
};

const formatMarketCap = (val: number | undefined | null): string => {
    if (val == null) return '-';
    if (Math.abs(val) >= 1.0e12) return `$${(val / 1.0e12).toFixed(2)}T`;
    if (Math.abs(val) >= 1.0e9) return `$${(val / 1.0e9).toFixed(2)}B`;
    if (Math.abs(val) >= 1.0e6) return `$${(val / 1.0e6).toFixed(2)}M`;
    return `$${val.toLocaleString()}`;
};

const formatNumberCompact = (val: number | undefined | null): string => {
    if (val == null) return '-';
    if (Math.abs(val) >= 1.0e9) return `${(val / 1.0e9).toFixed(2)}B`;
    if (Math.abs(val) >= 1.0e6) return `${(val / 1.0e6).toFixed(2)}M`;
    if (Math.abs(val) >= 1.0e3) return `${(val / 1.0e3).toFixed(1)}K`;
    return val.toLocaleString();
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
    const [themes, setThemes] = useState<ChartSymbolMeta[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [viewMode, setViewMode] = useState<'chart' | 'rs' | 'table' | 'fundamentals'>('chart');
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
    const [showIndicatorModal, setShowIndicatorModal] = useState(false);

    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth <= 768);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);
    const [earnings, setEarnings] = useState<EarningData[]>([]);
    const [earningsLoading, setEarningsLoading] = useState(false);
    const { isTickerActive, toggleWatchlist } = useWatchlist();


    // State for hovering
    const [hoverData, setHoverData] = useState<ChartDataPoint | null>(null);

    // State for full range (parquet master)
    const [fullRange, setFullRange] = useState(false);

    // Compare symbol state
    const [compareTicker, setCompareTicker] = useState('');
    const [compareData, setCompareData] = useState<ChartDataPoint[]>([]);
    const [compareLoading, setCompareLoading] = useState(false);

    // active toggles
    const [showSma21, setShowSma21] = useState(false);
    const [showSma50, setShowSma50] = useState(false);
    const [showSma63, setShowSma63] = useState(false);
    const [showSma150, setShowSma150] = useState(false);
    const [showSma200, setShowSma200] = useState(false);
    const [showEma5, setShowEma5] = useState(false);
    const [showEma21, setShowEma21] = useState(true);
    const [showEma50, setShowEma50] = useState(false);
    const [showEma63, setShowEma63] = useState(true);
    const [showEma200, setShowEma200] = useState(true);
    const [showVolume, setShowVolume] = useState(true);
    const [showTd9, setShowTd9] = useState(true);
    const [showBB, setShowBB] = useState(true);
    const [showRsDots, setShowRsDots] = useState(true);
    const [showSma50Atr, setShowSma50Atr] = useState(false);
    const [showStructurePivot, setShowStructurePivot] = useState(true);
    const [structures, setStructures] = useState<StructurePivot[]>([]);

    const chartRef = useRef<IChartApi | null>(null);
    const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const lineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const smaSeriesRefs = useRef<Record<string, ISeriesApi<"Line"> | null>>({});
    const emaSeriesRefs = useRef<Record<string, ISeriesApi<"Line"> | null>>({});
    const bbSeriesRefs = useRef<{ upper: ISeriesApi<"Line"> | null, lower: ISeriesApi<"Line"> | null }>({ upper: null, lower: null });
    const volSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const rsSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const compSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const sma50AtrSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const structureSeriesRef = useRef<ISeriesApi<"Line">[]>([]);

    const selected = useMemo(() => {
        const parsedTicker = ticker?.includes(':') ? ticker.split(':')[1] : ticker;
        return symbols.find(s => {
            const symIdentifier = s.exchange ? `${s.exchange}:${s.ticker}` : s.ticker;
            return symIdentifier === ticker || s.ticker === ticker || s.ticker === parsedTicker;
        });
    }, [symbols, ticker]);

    // 1. Initial Load
    useEffect(() => {
        if (!selected) return;
        
        setLoading(true);
        setError('');
        
        fetch(`/api/chart/${selected.id}?full_range=${fullRange}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch data');
                return res.json();
            })
            .then(json => {
                const res = json as ChartResponse;
                setData(res.data);
                setThemes(res.themes || []);
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
    }, [selected, fullRange]);

    // 1b. 構造ピボット (LL-HL)。トグル ON のときだけ取得する
    useEffect(() => {
        if (!selected || !showStructurePivot) {
            setStructures([]);
            return;
        }
        let cancelled = false;
        fetchStructurePivot(selected.id, fullRange)
            .then(list => { if (!cancelled) setStructures(list); })
            // 構造が出ないだけでチャート全体を落とさない
            .catch(() => { if (!cancelled) setStructures([]); });
        return () => { cancelled = true; };
    }, [selected, fullRange, showStructurePivot]);



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
        fetch(`/api/chart/${compSymbol.id}?full_range=${fullRange}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch comparison data');
                return res.json();
            })
            .then(json => {
                const resData = json as ChartResponse;
                setCompareData(resData.data || []);
            })
            .catch(err => {
                console.error(err);
                alert(`Error loading comparison: ${err.message}`);
                setCompareData([]);
            })
            .finally(() => setCompareLoading(false));
    };

    // 3. Chart Initialization
    useEffect(() => {
        if (!chartContainerRef.current || !selected || loading) return;

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

        const isVirtualLineChart = selected.ticker === '^VXV_VIX' || selected.ticker === '^MKT_TREND';
        if (isVirtualLineChart) {
            const lineSeries = chart.addLineSeries({
                color: '#2962FF',
                lineWidth: 2,
                title: selected.name || selected.ticker,
            });
            lineSeriesRef.current = lineSeries;
        } else {
            const candleSeries = chart.addCandlestickSeries({
                upColor: '#00ff88',
                downColor: '#ff4444',
                borderVisible: false,
                wickUpColor: '#00ff88',
                wickDownColor: '#ff4444',
            });
            candleSeriesRef.current = candleSeries;
        }

        return () => {
            chart.remove();
            chartRef.current = null;
            candleSeriesRef.current = null;
            lineSeriesRef.current = null;
            smaSeriesRefs.current = {};
            emaSeriesRefs.current = {};
            bbSeriesRefs.current = { upper: null, lower: null };
            volSeriesRef.current = null;
            rsSeriesRef.current = null;
            compSeriesRef.current = null;
            sma50AtrSeriesRef.current = null;
        };
    }, [selected, loading]);

    // 4. Data & Series Updates
    useEffect(() => {
        const chart = chartRef.current;
        const candleSeries = candleSeriesRef.current;
        const lineSeries = lineSeriesRef.current;
        if (!chart || data.length === 0 || !selected) return;
        if (!candleSeries && !lineSeries) return;

        const isVirtualLineChart = selected.ticker === '^VXV_VIX' || selected.ticker === '^MKT_TREND';
        // --- Candle or Line Data ---
        if (isVirtualLineChart && lineSeries) {
            lineSeries.setData(data.map(d => ({
                time: d.time as any,
                value: d.close,
            })));
        } else if (candleSeries) {
            candleSeries.setData(data.map(d => ({
                time: d.time as any,
                open: d.open,
                high: d.high,
                low: d.low,
                close: d.close,
            })));
        }

        // --- Markers ---
        const markers: SeriesMarker<any>[] = [];
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

        if (showTd9) {
            data.forEach((d, idx) => {
                if (!validTd9Indices.has(idx)) return;
                markers.push({
                    time: d.time as any,
                    position: d.td9! > 0 ? 'aboveBar' : 'belowBar',
                    color: d.td9! > 0 ? appConfig.colors.bad : appConfig.colors.good,
                    shape: 'square',
                    text: Math.abs(d.td9!).toString(),
                    size: 0,
                });
            });
        }

        data.forEach((d) => {
            const dist = d.sma50_atr_mult != null ? d.sma50_atr_mult : 0;
            const showAtrRed = dist >= appConfig.thresholds.atr_multiple_red;
            const showAtrYellow = dist >= appConfig.thresholds.atr_multiple_yellow;
            if (showAtrRed || showAtrYellow) {
                markers.push({
                    time: d.time as any,
                    position: (d.sma50_atr_mult || 0) > 0 ? 'aboveBar' : 'belowBar',
                    color: showAtrRed ? '#ff0000' : '#ffff00',
                    shape: 'circle',
                    text: '',
                    size: 2,
                });
            }
        });
        // --- Structure Pivot: 現在生きている構造の LL / HL ---
        if (showStructurePivot) {
            buildStructureMarkers(structures, new Set(data.map(d => d.time))).forEach(m => {
                markers.push({
                    time: m.time as any,
                    position: 'belowBar',
                    color: m.color,
                    shape: 'arrowUp',
                    text: m.text,
                });
            });
        }

        markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
        
        if (isVirtualLineChart && lineSeries) {
            lineSeries.setMarkers([]);
        } else if (candleSeries) {
            candleSeries.setMarkers(markers);
        }

        // --- Indicators (SMAs/EMAs/BB) ---
        const updateLineSeries = (
            key: string, 
            dataField: keyof ChartDataPoint, 
            color: string, 
            visible: boolean, 
            refsObj: React.MutableRefObject<Record<string, ISeriesApi<"Line"> | null>>
        ) => {
            let series = refsObj.current[key];
            if (!series) {
                series = chart.addLineSeries({ color, lineWidth: 1, title: key.toUpperCase(), crosshairMarkerVisible: false });
                refsObj.current[key] = series;
            }
            series.applyOptions({ visible });
            if (visible) {
                series.setData(data.filter(d => d[dataField] != null).map(d => ({
                    time: d.time as any,
                    value: d[dataField] as number
                })));
            }
        };

        updateLineSeries('sma21', 'sma_21', '#2962FF', showSma21, smaSeriesRefs);
        updateLineSeries('sma50', 'sma_50', '#FF6D00', showSma50, smaSeriesRefs);
        updateLineSeries('sma63', 'sma_63', '#9c27b0', showSma63, smaSeriesRefs);
        updateLineSeries('sma150', 'sma_150', '#00bcd4', showSma150, smaSeriesRefs);
        updateLineSeries('sma200', 'sma_200', '#f44336', showSma200, smaSeriesRefs);
        updateLineSeries('ema5', 'ema_5', '#e91e63', showEma5, emaSeriesRefs);
        updateLineSeries('ema21', 'ema_21', '#3f51b5', showEma21, emaSeriesRefs);
        updateLineSeries('ema50', 'ema_50', '#ff9800', showEma50, emaSeriesRefs);
        updateLineSeries('ema63', 'ema_63', '#8bc34a', showEma63, emaSeriesRefs);
        updateLineSeries('ema200', 'ema_200', '#795548', showEma200, emaSeriesRefs);

        // Bollinger Bands
        if (!bbSeriesRefs.current.upper) {
            bbSeriesRefs.current.upper = chart.addLineSeries({ color: 'rgba(255, 255, 255, 0.25)', lineWidth: 1, lineStyle: 2, title: 'BB UPPER', crosshairMarkerVisible: false });
            bbSeriesRefs.current.lower = chart.addLineSeries({ color: 'rgba(255, 255, 255, 0.25)', lineWidth: 1, lineStyle: 2, title: 'BB LOWER', crosshairMarkerVisible: false });
        }
        bbSeriesRefs.current.upper!.applyOptions({ visible: showBB });
        bbSeriesRefs.current.lower!.applyOptions({ visible: showBB });
        if (showBB) {
            bbSeriesRefs.current.upper!.setData(data.filter(d => d.bb_upper != null).map(d => ({ time: d.time as any, value: d.bb_upper as number })));
            bbSeriesRefs.current.lower!.setData(data.filter(d => d.bb_lower != null).map(d => ({ time: d.time as any, value: d.bb_lower as number })));
        }

        // --- Structure Pivot (LL-HL) ---
        // 構造ごとに線を作り直す。本数は「現在の構造 2本 + 履歴の上限」で頭打ちになる
        structureSeriesRef.current.forEach(series => {
            try { chart.removeSeries(series); } catch { /* チャート破棄済み */ }
        });
        structureSeriesRef.current = [];

        if (showStructurePivot && structures.length > 0) {
            const segments = buildStructureSegments(structures, new Set(data.map(d => d.time)));
            segments.forEach(seg => {
                const series = chart.addLineSeries({
                    color: seg.color, lineWidth: seg.width, lineStyle: seg.style,
                    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
                });
                series.setData([
                    { time: seg.from as any, value: seg.fromValue },
                    { time: seg.to as any, value: seg.toValue },
                ]);
                structureSeriesRef.current.push(series);
            });
        }

        // --- Volume Histogram ---
        let volSeries = volSeriesRef.current;
        if (!volSeries) {
            volSeries = chart.addHistogramSeries({ color: 'rgba(99, 120, 180, 0.3)', priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
            chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
            volSeriesRef.current = volSeries;
        }
        volSeries.applyOptions({ visible: showVolume });
        if (showVolume) {
            volSeries.setData(data.map(d => ({
                time: d.time as any,
                value: d.volume,
                color: d.close >= d.open ? 'rgba(0, 255, 136, 0.25)' : 'rgba(255, 68, 68, 0.25)',
            })));
        }

        // --- RS Leading Dots ---
        let rsSeries = rsSeriesRef.current;
        if (!rsSeries) {
            rsSeries = chart.addLineSeries({ color: 'transparent', priceScaleId: 'signals', lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
            chart.priceScale('signals').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.93 }, visible: false });
            rsSeriesRef.current = rsSeries;
        }
        rsSeries.applyOptions({ visible: showRsDots });
        if (showRsDots) {
            rsSeries.setData(data.map(d => ({ time: d.time as any, value: 100 })));
            const dotMarkers: SeriesMarker<any>[] = [];
            data.forEach(d => {
                if (d.is_rs_blue_dot === 1) dotMarkers.push({ time: d.time as any, position: 'inBar', color: '#00d0ff', shape: 'circle', text: '◆', size: 0 });
                if (d.is_rs_red_dot === 1) dotMarkers.push({ time: d.time as any, position: 'inBar', color: '#ff4444', shape: 'circle', text: '◆', size: 0 });
            });
            dotMarkers.sort((a,b) => (a.time < b.time ? -1 : 1));
            rsSeries.setMarkers(dotMarkers);
        }

        // --- Comparison Series ---
        let compSeries = compSeriesRef.current;
        if (compareData.length > 0) {
            if (!compSeries) {
                compSeries = chart.addLineSeries({ color: '#E040FB', lineWidth: 2, title: `Vs ${compareTicker.toUpperCase()}`, priceScaleId: 'left', crosshairMarkerVisible: false });
                chart.priceScale('left').applyOptions({ visible: true, borderColor: 'rgba(255, 255, 255, 0.1)' });
                compSeriesRef.current = compSeries;
            }
            compSeries.applyOptions({ title: `Vs ${compareTicker.toUpperCase()}` });
            compSeries.setData(compareData.map(d => ({ time: d.time as any, value: d.close })));
        } else if (compSeries) {
            chart.removeSeries(compSeries);
            compSeriesRef.current = null;
            chart.priceScale('left').applyOptions({ visible: false });
        }

        // --- 50SMA/ATR% Line ---
        let sma50AtrSeries = sma50AtrSeriesRef.current;
        if (showSma50Atr) {
            if (!sma50AtrSeries) {
                sma50AtrSeries = chart.addLineSeries({
                    color: '#FFD700', // ゴールド
                    lineWidth: 1,
                    title: '50SMA/ATR',
                    priceScaleId: 'sma50_atr',
                    crosshairMarkerVisible: true,
                    lastValueVisible: false,
                    priceLineVisible: false,
                });
                chart.priceScale('sma50_atr').applyOptions({
                    scaleMargins: {
                        top: 0.65,
                        bottom: 0.18,
                    },
                    visible: false,
                });
                
                // 0 line
                sma50AtrSeries.createPriceLine({
                    price: 0,
                    color: 'rgba(255, 255, 255, 0.25)',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: false,
                });

                // Yellow Caution line (+8, -8)
                sma50AtrSeries.createPriceLine({
                    price: appConfig.thresholds.atr_multiple_yellow,
                    color: 'rgba(255, 255, 0, 0.15)',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: false,
                });
                sma50AtrSeries.createPriceLine({
                    price: -appConfig.thresholds.atr_multiple_yellow,
                    color: 'rgba(255, 255, 0, 0.15)',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: false,
                });

                // Red Danger line (+10, -10)
                sma50AtrSeries.createPriceLine({
                    price: appConfig.thresholds.atr_multiple_red,
                    color: 'rgba(255, 0, 0, 0.2)',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: false,
                });
                sma50AtrSeries.createPriceLine({
                    price: -appConfig.thresholds.atr_multiple_red,
                    color: 'rgba(255, 0, 0, 0.2)',
                    lineWidth: 1,
                    lineStyle: 2,
                    axisLabelVisible: false,
                });

                sma50AtrSeriesRef.current = sma50AtrSeries;
            }
            sma50AtrSeries.applyOptions({ visible: true });
            sma50AtrSeries.setData(data.filter(d => d.sma50_atr_mult != null).map(d => ({
                time: d.time as any,
                value: d.sma50_atr_mult as number,
            })));
        } else if (sma50AtrSeries) {
            chart.removeSeries(sma50AtrSeries);
            sma50AtrSeriesRef.current = null;
        }

        // Set default visible range to last 6 months
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

        // Update hover handler to use current data
        const handleMove = (param: any) => {
            if (param.time) {
                const point = data.find(d => d.time === param.time);
                if (point) setHoverData(point);
            } else {
                setHoverData(null);
            }
        };
        chart.subscribeCrosshairMove(handleMove);
        return () => chart.unsubscribeCrosshairMove(handleMove);

    }, [
        data, compareData, showVolume, showTd9, showBB, showRsDots, showSma50Atr,
        showSma21, showSma50, showSma63, showSma150, showSma200,
        showEma5, showEma21, showEma50, showEma63, showEma200,
        showStructurePivot, structures
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
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{formatNumberCompact(point.volume)}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_21?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_50?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.sma_200?.toFixed(2) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>{point.adr_pct_21?.toFixed(1) ?? '-'}</td>
                <td style={{ padding: '2px 10px', textAlign: 'right', color: (point.sma50_atr_mult || 0) >= appConfig.thresholds.atr_multiple_red ? '#ff0000' : (point.sma50_atr_mult || 0) >= appConfig.thresholds.atr_multiple_yellow ? '#ffff00' : 'inherit' }}>
                    {point.sma50_atr_mult?.toFixed(1) ?? '-'}
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
                <td style={{ padding: '2px 10px', textAlign: 'right' }}>-</td>
            </tr>
        );
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: isMobile ? 'calc(100vh - 130px)' : '100%', boxSizing: 'border-box' }}>
            <div style={{ padding: isMobile ? '10px' : '10px 20px', display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: isMobile ? '8px' : '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', gap: '15px' }}>
                        <Link to="/" style={{ color: '#00ff88', textDecoration: 'none', fontSize: isMobile ? '13px' : '14px' }}>&larr; Dashboard</Link>
                        {(selected.category === 'テーマ' || selected.category === 'セクタ') && (
                            <Link to={`/group/${selected.ticker}`} style={{ color: '#60a5fa', textDecoration: 'none', fontSize: isMobile ? '13px' : '14px', fontWeight: 'bold' }}>
                                &rarr; Theme Details ({selected.ticker})
                            </Link>
                        )}
                    </div>
                    <a href={tradingViewUrl} target="_blank" rel="noopener noreferrer" style={{ color: '#2962FF', textDecoration: 'none', fontSize: '12px' }}>
                        TradingView ↗
                    </a>
                </div>
                
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: isMobile ? '6px' : '12px', width: '100%' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <WatchlistButton 
                            isActive={isTickerActive(selected.ticker)} 
                            onClick={() => toggleWatchlist(selected.ticker, latest?.time)}
                            size={isMobile ? 20 : 24}
                        />
                        <span style={{ fontSize: isMobile ? '16px' : '18px', fontWeight: 700 }}>{selected.ticker}</span>
                    </div>

                    <span style={{ fontSize: '11px', color: '#888' }}>
                        {selected.name && selected.name.includes('::') ? selected.name.split('::')[1] : selected.name} ({selected.category})
                    </span>
                    
                    {latest && latest.market_cap && (
                        <span style={{ fontSize: '11px', color: '#aaa', padding: '2px 8px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px' }}>
                            Market Cap: <strong style={{ color: '#fff' }}>{formatMarketCap(latest.market_cap)}</strong>
                        </span>
                    )}
                </div>

                {themes.length > 0 && (
                    <div style={{ 
                        display: 'flex', 
                        gap: '6px', 
                        alignItems: 'center',
                        overflowX: isMobile ? 'auto' : 'visible',
                        whiteSpace: 'nowrap',
                        maxWidth: '100%',
                        paddingBottom: isMobile ? '2px' : '0',
                        paddingLeft: isMobile ? '28px' : '32px',
                        marginTop: isMobile ? '-2px' : '-4px',
                        marginBottom: isMobile ? '2px' : '4px',
                        WebkitOverflowScrolling: 'touch',
                        scrollbarWidth: 'none', // hide scrollbar for Firefox
                    }}>
                        {!isMobile && <span style={{ fontSize: '11px', color: '#666' }}>Themes:</span>}
                        {themes.map(t => {
                            const hasDelim = t.name && t.name.includes('::');
                            const themeGroup = hasDelim ? t.name.split('::')[0] : '';
                            const themeName = hasDelim ? t.name.split('::')[1] : t.name;

                            return (
                                <Link 
                                    key={t.id} 
                                    to={`/chart/${t.ticker}`} 
                                    style={{ 
                                        fontSize: '10px', 
                                        color: '#00ff88', 
                                        background: 'rgba(0, 255, 136, 0.08)', 
                                        padding: hasDelim ? '2px 8px' : '4px 8px', 
                                        borderRadius: '6px', 
                                        textDecoration: 'none',
                                        border: '1px solid rgba(0, 255, 136, 0.2)',
                                        transition: 'all 0.2s',
                                        display: 'inline-block',
                                        flexShrink: 0
                                    }}
                                    onMouseEnter={e => {
                                        e.currentTarget.style.background = 'rgba(0, 255, 136, 0.15)';
                                        e.currentTarget.style.borderColor = 'rgba(0, 255, 136, 0.4)';
                                    }}
                                    onMouseLeave={e => {
                                        e.currentTarget.style.background = 'rgba(0, 255, 136, 0.08)';
                                        e.currentTarget.style.borderColor = 'rgba(0, 255, 136, 0.2)';
                                    }}
                                >
                                    {hasDelim ? (
                                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', lineHeight: 1.1 }}>
                                            <span style={{ fontSize: '7px', color: '#8b9cc8', fontWeight: 600, letterSpacing: '0.02em' }}>{themeGroup}</span>
                                            <span style={{ fontSize: '9px', color: '#00ff88', fontWeight: 700 }}>{themeName}</span>
                                        </div>
                                    ) : (
                                        <span style={{ fontSize: '9px', color: '#00ff88', fontWeight: 700 }}>{themeName}</span>
                                    )}
                                </Link>
                            );
                        })}
                    </div>
                )}
            </div>

            <div style={{ padding: isMobile ? '0 10px' : '0 20px', display: 'flex', gap: '8px', marginBottom: '8px', overflowX: 'auto', whiteSpace: 'nowrap' }}>
                <button
                    className={`toggle-btn ${viewMode === 'chart' ? 'active' : ''}`}
                    onClick={() => setViewMode('chart')}
                    style={{ padding: '6px 12px', fontSize: '12px', borderRadius: '4px', flexShrink: 0 }}
                >
                    Chart View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'rs' ? 'active' : ''}`}
                    onClick={() => setViewMode('rs')}
                    style={{ padding: '6px 12px', fontSize: '12px', borderRadius: '4px', flexShrink: 0 }}
                >
                    RS View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'table' ? 'active' : ''}`}
                    onClick={() => setViewMode('table')}
                    style={{ padding: '6px 12px', fontSize: '12px', borderRadius: '4px', flexShrink: 0 }}
                >
                    Data View
                </button>
                <button
                    className={`toggle-btn ${viewMode === 'fundamentals' ? 'active' : ''}`}
                    onClick={() => setViewMode('fundamentals')}
                    style={{ padding: '6px 12px', fontSize: '12px', borderRadius: '4px', flexShrink: 0 }}
                >
                    Fundamentals
                </button>

                <button
                    className={`toggle-btn ${fullRange ? 'active' : ''}`}
                    onClick={() => setFullRange(!fullRange)}
                    style={{ 
                        padding: '6px 12px', 
                        fontSize: '12px', 
                        borderRadius: '4px', 
                        flexShrink: 0,
                        marginLeft: isMobile ? '0' : 'auto',
                        borderColor: fullRange ? '#00ff88' : 'rgba(255,255,255,0.1)',
                        color: fullRange ? '#00ff88' : '#aaa'
                    }}
                    title="Load all-time historical data from Parquet master cache"
                >
                    Full Range
                </button>
            </div>

            {error && <div style={{ color: '#ff4444', padding: '0 20px' }}>{error}</div>}

            {/* === Stats Table (PC) or HUD (Mobile) === */}
            {latest && (
                isMobile ? (
                    <div style={{ 
                        padding: '6px 12px', 
                        background: 'rgba(255,255,255,0.02)', 
                        borderBottom: '1px solid rgba(255,255,255,0.05)',
                        fontSize: '11px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        color: '#aaa',
                        gap: '6px',
                        flexWrap: 'wrap'
                    }}>
                        {(() => {
                            const activePoint = hoverData && hoverData.time !== latest.time ? hoverData : latest;
                            const activeChange = hoverData && hoverData.time !== latest.time ? hoverChange : latestChange;
                            const isHover = hoverData && hoverData.time !== latest.time;
                            const changeColor = activeChange != null && activeChange >= 0 ? appConfig.colors.good : appConfig.colors.bad;
                            const priceColor = activePoint.close >= activePoint.open ? appConfig.colors.good : appConfig.colors.bad;

                            return (
                                <>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        <span style={{ 
                                            padding: '1px 4px', 
                                            background: isHover ? 'rgba(41,98,255,0.2)' : 'rgba(0,255,136,0.1)', 
                                            color: isHover ? '#60a5fa' : '#00ff88', 
                                            borderRadius: '3px',
                                            fontSize: '8px',
                                            fontWeight: 'bold'
                                        }}>
                                            {isHover ? 'CURSOR' : 'LATEST'}
                                        </span>
                                        <span style={{ fontWeight: '500', color: '#fff' }}>{activePoint.time}</span>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        <span>C: <strong style={{ color: priceColor }}>{activePoint.close.toFixed(2)}</strong></span>
                                        <span>(<strong style={{ color: changeColor }}>{activeChange != null ? `${activeChange.toFixed(2)}%` : '-'}</strong>)</span>
                                        <span>V: <strong style={{ color: '#fff' }}>{formatNumberCompact(activePoint.volume)}</strong></span>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '9px', color: '#666' }}>
                                        {showSma21 && activePoint.sma_21 && <span>SMA21:<strong style={{ color: '#2962FF' }}>{activePoint.sma_21.toFixed(1)}</strong></span>}
                                        {showSma50 && activePoint.sma_50 && <span>SMA50:<strong style={{ color: '#FF6D00' }}>{activePoint.sma_50.toFixed(1)}</strong></span>}
                                        {activePoint.adr_pct_21 && <span>ADR:<strong style={{ color: '#ccc' }}>{activePoint.adr_pct_21.toFixed(1)}%</strong></span>}
                                        {activePoint.sma50_atr_mult != null && (() => {
                                            const distColor = activePoint.sma50_atr_mult >= appConfig.thresholds.atr_multiple_red 
                                                ? '#ff4444' 
                                                : activePoint.sma50_atr_mult >= appConfig.thresholds.atr_multiple_yellow 
                                                    ? '#ffff00' 
                                                    : '#ccc';
                                            return (
                                                <span>50/ATR:<strong style={{ color: distColor }}>{activePoint.sma50_atr_mult.toFixed(1)}</strong></span>
                                            );
                                        })()}
                                    </div>
                                </>
                            );
                        })()}
                    </div>
                ) : (
                    <div style={{ padding: '0 20px 6px 20px', overflowX: 'auto' }}>
                        <table style={{ borderCollapse: 'collapse', fontSize: '12px', maxWidth: '850px', width: '100%' }}>
                            <thead>
                                <tr style={{ color: '#555', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                    <th style={{ padding: '2px 10px', textAlign: 'left' }}></th>
                                    <th style={{ padding: '2px 10px', textAlign: 'left' }}>Date</th>
                                    <th style={{ padding: '2px 10px', textAlign: 'right' }}>Close</th>
                                    <th style={{ padding: '2px 10px', textAlign: 'right' }}>1D%</th>
                                    <th style={{ padding: '2px 10px', textAlign: 'right' }}>Volume</th>
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
                )
            )}

            <div className="main-content" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                {viewMode === 'rs' && data.length > 0 && (
                    <main className="chart-area glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', margin: isMobile ? '0 10px 10px 10px' : '0 20px 20px 20px' }}>
                        <RrgChart data={data} ticker={selected.ticker} />
                    </main>
                )}
                {viewMode === 'table' && <SymbolDataTable data={data} />}
                <main className="chart-area glass-panel" style={{ flex: 1, display: viewMode === 'chart' ? 'flex' : 'none', flexDirection: 'column', margin: isMobile ? '0 10px 10px 10px' : '0 20px 20px 20px', minHeight: '0' }}>
                    {loading && <div className="loading">Loading chart data...</div>}
                    {!loading && data.length > 0 && (
                        <>
                            {isMobile ? (
                                <div style={{ display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'space-between', padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,0.05)', boxSizing: 'border-box' }}>
                                    <button 
                                        className="toggle-btn active"
                                        onClick={() => setShowIndicatorModal(true)}
                                        style={{ 
                                            display: 'flex', 
                                            alignItems: 'center', 
                                            gap: '6px', 
                                            padding: '6px 14px', 
                                            fontSize: '12px',
                                            borderRadius: '6px',
                                            background: 'linear-gradient(135deg, #2962FF 0%, #1565C0 100%)',
                                            border: 'none',
                                            color: '#fff',
                                            fontWeight: 'bold',
                                            boxShadow: '0 4px 10px rgba(41,98,255,0.3)',
                                            cursor: 'pointer'
                                        }}
                                    >
                                        <span>📊 指標設定</span>
                                    </button>
                                    
                                    {/* Compare Logic Mobile */}
                                    <form onSubmit={handleCompare} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                                        <span style={{ fontSize: '11px', color: '#aaa' }}>Vs:</span>
                                        <input
                                            type="text"
                                            value={compareTicker}
                                            onChange={e => setCompareTicker(e.target.value)}
                                            placeholder="Ticker..."
                                            style={{
                                                padding: '4px 8px',
                                                fontSize: '11px',
                                                borderRadius: '4px',
                                                background: 'rgba(255,255,255,0.1)',
                                                border: '1px solid rgba(255,255,255,0.2)',
                                                color: '#fff',
                                                width: '65px',
                                                textTransform: 'uppercase',
                                                boxSizing: 'border-box'
                                            }}
                                        />
                                        <button type="submit" className="toggle-btn active" style={{ padding: '4px 8px', fontSize: '11px', cursor: 'pointer' }}>
                                            {compareLoading ? '...' : '比較'}
                                        </button>
                                        {compareData.length > 0 && (
                                            <button type="button" className="toggle-btn" style={{ fontSize: '11px', padding: '4px 8px', cursor: 'pointer' }} onClick={() => { setCompareTicker(''); setCompareData([]); }}>
                                                ✕
                                            </button>
                                        )}
                                    </form>
                                </div>
                            ) : (
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
                                    <button className={`toggle-btn ${showBB ? 'active' : ''}`} onClick={() => setShowBB(!showBB)}>BB</button>
                                    <button className={`toggle-btn ${showRsDots ? 'active' : ''}`} onClick={() => setShowRsDots(!showRsDots)}>RS.</button>
                                    <button className={`toggle-btn ${showSma50Atr ? 'active' : ''}`} onClick={() => setShowSma50Atr(!showSma50Atr)}>50/ATR</button>
                                    <button className={`toggle-btn ${showStructurePivot ? 'active' : ''}`} onClick={() => setShowStructurePivot(!showStructurePivot)} title="LL-HL 構造ピボット">Pivot</button>

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
                            )}
                            <div ref={chartContainerRef} className="chart-container" style={{ flex: 1, minHeight: 0 }} />
                        </>
                    )}
                    {!loading && data.length === 0 && !error && (
                        <div className="no-data">Select a symbol to view chart</div>
                    )}
                </main>
                {viewMode === 'fundamentals' && (
                    <main className="chart-area glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', margin: isMobile ? '0 10px 10px 10px' : '0 20px 20px 20px', padding: '20px', overflowY: 'auto' }}>
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

            {/* Indicator Modal (Mobile Only) */}
            {isMobile && showIndicatorModal && (
                <div style={{
                    position: 'fixed',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0, 0, 0, 0.75)',
                    backdropFilter: 'blur(8px)',
                    zIndex: 2000,
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'flex-end',
                }} onClick={() => setShowIndicatorModal(false)}>
                    <div style={{
                        background: '#121620',
                        borderTop: '1px solid rgba(255,255,255,0.08)',
                        borderRadius: '20px 20px 0 0',
                        padding: '20px',
                        maxHeight: '85%',
                        overflowY: 'auto',
                        display: 'flex',
                        flexDirection: 'column',
                        // flex column の子で overflowY を効かせるのに必要（無いと縮まず末尾が切れる）
                        minHeight: 0,
                        gap: '16px',
                        boxShadow: '0 -10px 30px rgba(0,0,0,0.5)'
                    }} onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '10px' }}>
                            <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 'bold', color: '#fff', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                📊 テクニカル指標設定
                            </h3>
                            <button 
                                onClick={() => setShowIndicatorModal(false)}
                                style={{ background: 'transparent', border: 'none', color: '#8b9cc8', fontSize: '18px', cursor: 'pointer', padding: '4px' }}
                            >
                                ✕
                            </button>
                        </div>

                        {/* SMAs */}
                        <div>
                            <span style={{ fontSize: '10px', color: '#687fa1', textTransform: 'uppercase', display: 'block', marginBottom: '6px', fontWeight: '600', letterSpacing: '0.05em' }}>SMA (単純移動平均)</span>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                {[21, 50, 63, 150, 200].map(period => {
                                    const showVar = period === 21 ? showSma21 : period === 50 ? showSma50 : period === 63 ? showSma63 : period === 150 ? showSma150 : showSma200;
                                    const setFunc = period === 21 ? setShowSma21 : period === 50 ? setShowSma50 : period === 63 ? setShowSma63 : period === 150 ? setShowSma150 : setShowSma200;
                                    return (
                                        <button 
                                            key={period} 
                                            className={`toggle-btn ${showVar ? 'active' : ''}`} 
                                            style={{ padding: '8px', fontSize: '11px', borderRadius: '6px', flex: '1 0 18%', minWidth: '45px', cursor: 'pointer' }}
                                            onClick={() => setFunc(!showVar)}
                                        >
                                            {period}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>

                        {/* EMAs */}
                        <div>
                            <span style={{ fontSize: '10px', color: '#687fa1', textTransform: 'uppercase', display: 'block', marginBottom: '6px', fontWeight: '600', letterSpacing: '0.05em' }}>EMA (指数平滑移動平均)</span>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                {[5, 21, 50, 63, 200].map(period => {
                                    const showVar = period === 5 ? showEma5 : period === 21 ? showEma21 : period === 50 ? showEma50 : period === 63 ? showEma63 : showEma200;
                                    const setFunc = period === 5 ? setShowEma5 : period === 21 ? setShowEma21 : period === 50 ? setShowEma50 : period === 63 ? setShowEma63 : setShowEma200;
                                    return (
                                        <button 
                                            key={period} 
                                            className={`toggle-btn ${showVar ? 'active' : ''}`} 
                                            style={{ padding: '8px', fontSize: '11px', borderRadius: '6px', flex: '1 0 18%', minWidth: '45px', cursor: 'pointer' }}
                                            onClick={() => setFunc(!showVar)}
                                        >
                                            {period}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>

                        {/* Other Indicators */}
                        <div>
                            <span style={{ fontSize: '10px', color: '#687fa1', textTransform: 'uppercase', display: 'block', marginBottom: '6px', fontWeight: '600', letterSpacing: '0.05em' }}>その他表示</span>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                                <button className={`toggle-btn ${showStructurePivot ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer', gridColumn: 'span 2' }} onClick={() => setShowStructurePivot(!showStructurePivot)}>Pivot (LL-HL 構造ピボット)</button>
                                <button className={`toggle-btn ${showVolume ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer' }} onClick={() => setShowVolume(!showVolume)}>出来高 (Volume)</button>
                                <button className={`toggle-btn ${showTd9 ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer' }} onClick={() => setShowTd9(!showTd9)}>TD9 シーケンシャル</button>
                                <button className={`toggle-btn ${showBB ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer' }} onClick={() => setShowBB(!showBB)}>ボリンジャーバンド</button>
                                <button className={`toggle-btn ${showRsDots ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer' }} onClick={() => setShowRsDots(!showRsDots)}>RSシグナルドット</button>
                                <button className={`toggle-btn ${showSma50Atr ? 'active' : ''}`} style={{ padding: '8px', borderRadius: '6px', fontSize: '11px', cursor: 'pointer', gridColumn: 'span 2' }} onClick={() => setShowSma50Atr(!showSma50Atr)}>50SMA/ATR% 乖離</button>
                            </div>
                        </div>

                        <button 
                            onClick={() => setShowIndicatorModal(false)}
                            style={{ 
                                marginTop: '8px', 
                                padding: '10px', 
                                background: 'linear-gradient(135deg, #2962FF 0%, #1565C0 100%)',
                                border: 'none', 
                                borderRadius: '8px', 
                                color: '#fff', 
                                fontWeight: 'bold',
                                fontSize: '13px',
                                cursor: 'pointer'
                            }}
                        >
                            決定
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};
