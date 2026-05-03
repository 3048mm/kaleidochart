import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ThemeDetailResponse, ThemeConstituentItem } from '../types';
import { MiniChart } from '../components/MiniChart';
import { Sparkline } from '../components/Sparkline';
import { RrgChart, RrgSeries } from '../components/RrgChart';
import RsLineChart from '../components/RsLineChart';
import { appConfig } from '../config';

// Colour palette for multi-ticker RRG
const PALETTE = [
    '#26a69a', '#ab47bc', '#ffa726', '#42a5f5', '#ef5350',
    '#66bb6a', '#ec407a', '#ffee58', '#26c6da', '#ff7043',
    '#8d6e63', '#78909c', '#d4e157', '#29b6f6', '#ff5252',
    '#69f0ae', '#e040fb', '#ffcc02', '#00bcd4', '#ff6d00',
];

export const ThemePage: React.FC = () => {
    const { symbolId } = useParams<{ symbolId: string }>();
    const [data, setData] = useState<ThemeDetailResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Selected tickers for RRG (theme itself + any checked constituents)
    const [selectedTickers, setSelectedTickers] = useState<Set<string>>(new Set());

    useEffect(() => {
        if (!symbolId) return;
        setLoading(true);
        setError('');
        fetch(`/api/theme/${symbolId}`)
            .then(res => {
                if (!res.ok) throw new Error(`Failed to load theme (${res.status})`);
                return res.json();
            })
            .then((json: ThemeDetailResponse) => {
                setData(json);
                // Default: only the theme ETF itself is selected in RRG
                setSelectedTickers(new Set([json.ticker]));
            })
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, [symbolId]);

    const formatPct = (v: number | null | undefined) => {
        if (v == null) return '-';
        return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
    };
    const fmtN = (v: number | null | undefined, dec = 2) => {
        if (v == null) return '-';
        return v.toFixed(dec);
    };
    const colorPct = (v: number | null | undefined) => {
        if (v == null) return '#aaa';
        return v > 0 ? appConfig.colors.good : v < 0 ? appConfig.colors.bad : '#fff';
    };

    const toggleTicker = (ticker: string) => {
        setSelectedTickers(prev => {
            const next = new Set(prev);
            next.has(ticker) ? next.delete(ticker) : next.add(ticker);
            return next;
        });
    };

    // Build multi-series for RRG from selected tickers
    const rrgSeries: RrgSeries[] = useMemo(() => {
        if (!data) return [];
        const result: RrgSeries[] = [];
        let colorIdx = 0;

        // Theme itself first
        if (selectedTickers.has(data.ticker)) {
            result.push({ ticker: data.ticker, data: data.chart_data, color: PALETTE[colorIdx++] });
        }

        // Constituents that are selected
        data.constituents
            .filter(c => selectedTickers.has(c.ticker))
            .forEach(c => {
                result.push({ ticker: c.ticker, data: c.chart_data, color: PALETTE[colorIdx % PALETTE.length] });
                colorIdx++;
            });

        return result;
    }, [data, selectedTickers]);

    // Map ticker -> color for legend and row highlight
    const colorMap = useMemo(() => {
        const m: Record<string, string> = {};
        rrgSeries.forEach(s => { m[s.ticker] = s.color; });
        return m;
    }, [rrgSeries]);

    if (loading) return <div style={{ padding: '40px', textAlign: 'center', color: '#aaa' }}>Loading Theme Data...</div>;
    if (error) return <div style={{ padding: '40px', color: appConfig.colors.bad }}>{error}</div>;
    if (!data) return null;

    return (
        <div style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto' }}>
            {/* Breadcrumb */}
            <div style={{ fontSize: '13px', color: '#aaa', marginBottom: '16px' }}>
                <Link to="/" style={{ color: '#aaa', textDecoration: 'none' }}>Dashboard</Link>
                &nbsp;›&nbsp;
                <span style={{ color: '#fff' }}>{data.name}</span>
            </div>

            {/* ── Section 1: Theme Summary ── */}
            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px', display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
                {/* Left: KPIs */}
                <div style={{ flex: '1', minWidth: '360px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                    {/* Title row */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                        <h2 style={{ margin: 0, fontSize: '20px' }}>{data.ticker} <span style={{ fontSize: '13px', color: '#aaa', fontWeight: 'normal' }}>{data.name}</span></h2>
                        <span style={{ fontSize: '20px', fontWeight: 'bold' }}>{fmtN(data.close)}</span>
                    </div>

                    {/* Gain row */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                        {[
                            { label: '1D%', val: data.change_1d_pct },
                            { label: '1W%', val: data.change_1w_pct },
                            { label: '1M%', val: data.change_1m_pct },
                        ].map(m => (
                            <div key={m.label} style={{ background: 'rgba(255,255,255,0.05)', padding: '10px', borderRadius: '6px', textAlign: 'center' }}>
                                <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '4px' }}>{m.label}</div>
                                <div style={{ fontSize: '15px', fontWeight: 'bold', color: colorPct(m.val) }}>{formatPct(m.val)}</div>
                            </div>
                        ))}
                    </div>

                    {/* SMA row */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
                        {[
                            { label: 'SMA5', val: data.dist_sma5_pct },
                            { label: 'SMA21', val: data.dist_sma21_pct },
                            { label: 'SMA63', val: data.dist_sma63_pct },
                            { label: '21/63', val: data.sma21_sma63_pct },
                        ].map(m => (
                            <div key={m.label} style={{ background: 'rgba(255,255,255,0.05)', padding: '8px', borderRadius: '6px', textAlign: 'center' }}>
                                <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '3px' }}>dist {m.label}</div>
                                <div style={{ fontSize: '13px', fontWeight: 'bold', color: colorPct(m.val) }}>{formatPct(m.val)}</div>
                            </div>
                        ))}
                    </div>

                    {/* RS row */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                        {[
                            { label: 'RS Ratio 14', val: data.rs_ratio_14 },
                            { label: 'RS Ratio 21', val: data.rs_ratio_21 },
                            { label: 'RS Ratio 63', val: data.rs_ratio_63 },
                            { label: 'RS Mom 14', val: data.rs_momentum_14 },
                            { label: 'RS Mom 21', val: data.rs_momentum_21 },
                            { label: 'RS Mom 63', val: data.rs_momentum_63 },
                            { label: 'ADR%', val: data.adr_pct_21 },
                            { label: 'SMA50/ATR', val: data.dist_sma50_atr },
                        ].map(m => (
                            <div key={m.label} style={{ background: 'rgba(255,255,255,0.05)', padding: '8px', borderRadius: '6px', textAlign: 'center' }}>
                                <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '3px' }}>{m.label}</div>
                                <div style={{ fontSize: '12px', fontWeight: 'bold', color: colorPct(m.val) }}>{fmtN(m.val, 3)}</div>
                            </div>
                        ))}
                    </div>

                    {/* RS Sparklines */}
                    <div style={{ display: 'flex', gap: '12px' }}>
                        {[
                            { label: 'RS Ratio 14', data: data.rs14_sparkline },
                            { label: 'RS Ratio 21', data: data.rs21_sparkline },
                            { label: 'RS Ratio 63', data: data.rs63_sparkline },
                        ].map(s => (
                            <div key={s.label} style={{ flex: 1 }}>
                                <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '4px' }}>{s.label}</div>
                                <Sparkline
                                    data={s.data}
                                    width={120}
                                    height={30}
                                    color={s.data.length && s.data[s.data.length - 1] > (s.data[0] || 0) ? appConfig.colors.good : appConfig.colors.bad}
                                    fixedRange={false}
                                />
                            </div>
                        ))}
                    </div>
                </div>

                {/* Right: 6M Mini Chart */}
                <div style={{ flex: '1', minWidth: '300px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '10px' }}>
                    <div style={{ fontSize: '11px', color: '#aaa', marginBottom: '8px', textAlign: 'center' }}>6-Month Trend</div>
                    <MiniChart data={data.chart_data} height={180} />
                </div>
            </div>

            {/* RS Line Chart */}
            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                <RsLineChart data={data.chart_data} />
            </div>

            {/* ── Section 2: Constituent Stocks ── */}
            {data.constituents.length > 0 && (
                <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                        <h3 style={{ margin: 0 }}>📋 構成銘柄 ({data.constituents.length})</h3>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                                onClick={() => {
                                    const all = new Set([data.ticker, ...data.constituents.map(c => c.ticker)]);
                                    setSelectedTickers(all);
                                }}
                                style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}
                            >全て選択</button>
                            <button
                                onClick={() => setSelectedTickers(new Set([data.ticker]))}
                                style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}
                            >テーマのみ</button>
                        </div>
                    </div>
                    {/* Header */}
                    <div style={{ display: 'flex', fontSize: '11px', color: '#aaa', paddingBottom: '8px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '4px' }}>
                        <div style={{ width: '28px' }}>RRG</div>
                        <div style={{ flex: 1, minWidth: '100px' }}>銘柄</div>
                        <div style={{ width: '70px', textAlign: 'right' }}>Close</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>1D%</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>1W%</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>1M%</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>RS14</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>RS21</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>RS63</div>
                        <div style={{ width: '80px', textAlign: 'center' }}>RS推移</div>
                    </div>
                    {data.constituents.map((c: ThemeConstituentItem) => {
                        const isSelected = selectedTickers.has(c.ticker);
                        const dotColor = colorMap[c.ticker];
                        return (
                            <div
                                key={c.id}
                                style={{
                                    display: 'flex', alignItems: 'center', padding: '6px 0',
                                    borderBottom: `1px solid ${appConfig.colors.glassBorder}`, gap: '4px',
                                    background: isSelected ? 'rgba(255,255,255,0.03)' : 'transparent',
                                    cursor: 'pointer',
                                }}
                                onClick={() => toggleTicker(c.ticker)}
                            >
                                {/* Checkbox / color dot */}
                                <div style={{ width: '28px', display: 'flex', justifyContent: 'center' }}>
                                    <div style={{
                                        width: '12px', height: '12px',
                                        borderRadius: '2px',
                                        border: `2px solid ${isSelected ? (dotColor || appConfig.colors.accent) : '#555'}`,
                                        background: isSelected ? (dotColor || appConfig.colors.accent) : 'transparent',
                                        flexShrink: 0,
                                    }} />
                                </div>
                                <div style={{ flex: 1, minWidth: '100px', display: 'flex', flexDirection: 'column' }}>
                                    <Link to={`/chart/${c.ticker}`} onClick={e => e.stopPropagation()} style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>{c.ticker}</Link>
                                    <span style={{ fontSize: '10px', color: '#666', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{c.name}</span>
                                </div>
                                <div style={{ width: '70px', textAlign: 'right', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>{fmtN(c.close)}</div>
                                {[c.change_1d_pct, c.change_1w_pct, c.change_1m_pct].map((v, i) => (
                                    <div key={i} style={{ width: '56px', textAlign: 'center', fontSize: '11px', fontWeight: '600', color: colorPct(v) }}>{formatPct(v)}</div>
                                ))}
                                {[c.rs_ratio_14, c.rs_ratio_21, c.rs_ratio_63].map((v, i) => (
                                    <div key={`rs${i}`} style={{ width: '56px', textAlign: 'center', fontSize: '11px', color: colorPct(v) }}>{fmtN(v, 2)}</div>
                                ))}
                                <div style={{ width: '80px' }}>
                                    <Sparkline
                                        data={c.rs_sparkline}
                                        width={80}
                                        height={20}
                                        color={c.rs_sparkline.length && c.rs_sparkline[c.rs_sparkline.length - 1] > 0 ? appConfig.colors.good : appConfig.colors.bad}
                                        fixedRange={false}
                                    />
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* ── Section 3: RRG Chart ── */}
            <div className="glass-panel" style={{ padding: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px', flexWrap: 'wrap', gap: '8px' }}>
                    <h3 style={{ margin: 0 }}>📡 RRG Chart</h3>
                    {/* Legend */}
                    <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                        {/* Theme itself */}
                        <div
                            style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', opacity: selectedTickers.has(data.ticker) ? 1 : 0.4 }}
                            onClick={() => toggleTicker(data.ticker)}
                        >
                            <div style={{ width: '12px', height: '12px', borderRadius: '2px', background: colorMap[data.ticker] || PALETTE[0] }} />
                            <span style={{ fontSize: '12px', color: colorMap[data.ticker] || '#ccc' }}>{data.ticker}</span>
                        </div>
                    </div>
                </div>
                {rrgSeries.length > 0 ? (
                    <div style={{ height: '600px' }}>
                        <RrgChart series={rrgSeries} />
                    </div>
                ) : (
                    <div style={{ color: '#666', padding: '40px', textAlign: 'center' }}>銘柄を選択してください</div>
                )}
            </div>
        </div>
    );
};
