import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { DashboardResponse, LeadingIndicatorItem } from '../types';
import { Sparkline } from '../components/Sparkline';
import { MarketPhaseMeter } from '../components/MarketPhaseMeter';
import { TrendScoreChart } from '../components/TrendScoreChart';
import { RrgChart, RrgSeries } from '../components/RrgChart';
import { appConfig, getIntensityColor } from '../config';
import { SummaryTable } from '../components/SummaryTable';
import { EtfFeaturePanel } from '../components/EtfFeaturePanel';
import { VxvVixRatioChart } from '../components/VxvVixRatioChart';

export const DashboardPage: React.FC = () => {
    const [availableDates, setAvailableDates] = useState<string[]>([]);
    const [selectedDate, setSelectedDate] = useState<string>('');
    const [data, setData] = useState<DashboardResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'indicator' | 'market' | 'sector-theme'>('indicator');
    const [themesVisibleCount, setThemesVisibleCount] = useState(10);
    const [rankType, setRankType] = useState<'rs_trend' | 'rs_ratio'>('rs_trend');
    const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth <= 768);
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Initial load: Fetch available dates
    useEffect(() => {
        fetch('/api/available_dates')
            .then(res => res.json())
            .then(json => {
                if (json.dates && json.dates.length > 0) {
                    setAvailableDates(json.dates);
                    setSelectedDate(json.dates[0]); // Set to latest
                }
            })
            .catch(err => console.error('Failed to fetch available dates:', err));
    }, []);

    const shiftDate = (direction: number) => {
        if (!selectedDate || availableDates.length === 0) return;
        const currentIndex = availableDates.indexOf(selectedDate);

        if (currentIndex !== -1) {
            // Standard case: date is a business day
            const newIndex = currentIndex - direction;
            if (newIndex >= 0 && newIndex < availableDates.length) {
                setSelectedDate(availableDates[newIndex]);
            }
        } else {
            // Edge case: date is NOT in the list (e.g., manually selected Weekend)
            // direction -1 (Backwards) -> find first date < selectedDate
            // direction +1 (Forwards) -> find first date > selectedDate
            if (direction === -1) {
                const target = availableDates.find(d => d < selectedDate);
                if (target) setSelectedDate(target);
            } else {
                const target = [...availableDates].reverse().find(d => d > selectedDate);
                if (target) setSelectedDate(target);
            }
        }
    };

    useEffect(() => {
        setLoading(true);
        setError('');

        const params = new URLSearchParams();
        if (selectedDate) params.append('date', selectedDate);
        params.append('rank_type', rankType);

        const url = `/api/dashboard?${params.toString()}`;

        fetch(url)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch dashboard data');
                return res.json();
            })
            .then((json: DashboardResponse) => {
                setData(json);
                if (!selectedDate) {
                    setSelectedDate(json.date);
                }
            })
            .catch(err => {
                console.error(err);
                setError(err.message);
            })
            .finally(() => setLoading(false));
    }, [selectedDate, rankType]);


    // Helper component for Leading Indicators
    const renderLeadingList = (items: LeadingIndicatorItem[]) => {
        if (items.length === 0) return <div style={{ color: '#888' }}>データなし</div>;

        return (
            <div className="dashboard-list" style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', width: '100%', maxWidth: '100%' }}>
                <div style={{ minWidth: '550px' }}>
                    {/* Header Row */}
                    <div style={{
                        display: 'flex', fontSize: '11px', color: '#aaa', paddingBottom: '8px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '8px'
                    }}>
                        <div style={{ flex: '1', minWidth: '80px' }}>Indicator</div>
                        <div style={{ width: '60px', textAlign: 'right', paddingRight: '10px' }}>Close</div>
                        <div style={{ width: '56px', textAlign: 'center' }}>1D%</div>
                        <div style={{ width: '56px', textAlign: 'center', marginLeft: '5px' }}>1W%</div>
                        <div style={{ width: '56px', textAlign: 'center', marginLeft: '5px' }}>1M%</div>
                        <div style={{ width: '60px', textAlign: 'right', paddingRight: '10px' }}>21EMA乖離</div>
                        <div style={{ width: '80px', textAlign: 'center' }}>1M Price</div>
                    </div>

                    {items.map(item => {
                        const getBgColor = (pct: number) => {
                            let baseColor = pct >= 0 ? appConfig.colors.good : appConfig.colors.bad;
                            // Invert color for VIX indicators (Rise is bad, Fall is good)
                            if (item.ticker.startsWith('^VIX')) {
                                baseColor = pct >= 0 ? appConfig.colors.bad : appConfig.colors.good;
                            }
                            return getIntensityColor(pct, appConfig.thresholds.sparkline_max_pct_index, baseColor);
                        };
                        const getTextColor = (pct: number) => Math.abs(pct) > appConfig.thresholds.sparkline_max_pct_index * 0.5 ? '#000' : '#fff';
                        const formatPct = (pct: number) => `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
                        const colorNeutral = (pct: number) => {
                            let color = pct > 0 ? appConfig.colors.good : pct < 0 ? appConfig.colors.bad : '#fff';
                            if (item.ticker.startsWith('^VIX')) {
                                color = pct > 0 ? appConfig.colors.bad : pct < 0 ? appConfig.colors.good : '#fff';
                            }
                            return color;
                        };

                        return (
                            <div key={item.id} className="dashboard-item" style={{
                                display: 'flex', alignItems: 'center', padding: '6px 0', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, gap: '8px',
                            }}>
                                {/* 1. Name */}
                                <div style={{ flex: '1', minWidth: '80px', display: 'flex', flexDirection: 'column' }}>
                                    <Link to={`/chart/${encodeURIComponent(item.ticker)}`} style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>
                                        {item.ticker}
                                    </Link>
                                    <span style={{ fontSize: '10px', color: '#666', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{item.name}</span>
                                </div>

                                {/* 2. Close */}
                                <div style={{ width: '60px', textAlign: 'right', paddingRight: '10px', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>
                                    {item.close.toFixed(2)}
                                </div>

                                {/* 3. %1D */}
                                <div style={{ width: '56px', textAlign: 'center', padding: '3px', borderRadius: '4px', backgroundColor: getBgColor(item.change_1d_pct), color: getTextColor(item.change_1d_pct), fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>
                                    {formatPct(item.change_1d_pct)}
                                </div>

                                {/* 4. %1W */}
                                <div style={{ width: '56px', textAlign: 'center', padding: '3px', borderRadius: '4px', border: `1px solid ${appConfig.colors.glassBorder}`, color: colorNeutral(item.change_1w_pct), fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>
                                    {formatPct(item.change_1w_pct)}
                                </div>

                                {/* 5. %1M */}
                                <div style={{ width: '56px', textAlign: 'center', padding: '3px', borderRadius: '4px', border: `1px solid ${appConfig.colors.glassBorder}`, color: colorNeutral(item.change_1m_pct), fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>
                                    {formatPct(item.change_1m_pct)}
                                </div>

                                {/* 6. Dist 21EMA */}
                                <div style={{ width: '60px', textAlign: 'right', paddingRight: '10px', color: colorNeutral(item.dist_21ema_pct), fontSize: '11px', fontWeight: '600', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                                    {formatPct(item.dist_21ema_pct)}
                                </div>

                                {/* 7. Sparkline */}
                                <div style={{ width: '80px', flexShrink: 0 }}>
                                    <Sparkline
                                        data={item.sparkline}
                                        width={80}
                                        height={28}
                                        color={item.change_1m_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad}
                                        fixedRange={false}
                                    />
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        );
    };

    return (
        <div className="dashboard-page" style={{ padding: isMobile ? '10px' : '20px', width: '100%', maxWidth: '1200px', margin: '0 auto', boxSizing: 'border-box' }}>
            <div style={{ 
                display: 'flex', 
                flexDirection: isMobile ? 'column' : 'row', 
                justifyContent: 'space-between', 
                alignItems: isMobile ? 'flex-start' : 'center', 
                marginBottom: '20px',
                gap: isMobile ? '12px' : '0'
            }}>
                <h1 style={{ margin: 0, fontSize: isMobile ? '20px' : '28px' }}>Market Dashboard</h1>
                <div style={{ 
                    display: 'flex', 
                    alignItems: 'center', 
                    gap: isMobile ? '4px' : '10px', 
                    width: isMobile ? '100%' : 'auto', 
                    justifyContent: isMobile ? 'space-between' : 'flex-end' 
                }}>
                    <label style={{ fontSize: '13px', color: '#aaa' }}>Base Date:</label>
                    <button 
                        onClick={() => shiftDate(-1)} 
                        style={{ background: 'transparent', border: 'none', color: !availableDates.some(d => d < selectedDate) ? '#444' : '#aaa', cursor: 'pointer', fontSize: '18px', padding: '0 5px' }}
                        disabled={!availableDates.some(d => d < selectedDate)}
                    >
                        ◀
                    </button>
                    <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: '4px', 
                        background: 'rgba(255,255,255,0.05)', 
                        padding: '4px 8px', 
                        borderRadius: '6px', 
                        border: `1px solid ${appConfig.colors.glassBorder}`,
                        flex: isMobile ? 1 : 'none',
                        justifyContent: 'center'
                    }}>
                        <input
                            type="date"
                            value={selectedDate}
                            onChange={e => setSelectedDate(e.target.value)}
                            style={{
                                background: 'transparent',
                                border: 'none',
                                color: '#fff',
                                fontSize: '13px',
                                outline: 'none',
                                colorScheme: 'dark',
                                cursor: 'pointer',
                                width: '105px'
                            }}
                        />
                        <span style={{ fontSize: '12px', color: appConfig.colors.accent, fontWeight: 'bold' }}>
                            ({selectedDate ? new Date(selectedDate).toLocaleDateString('en-US', { weekday: 'short' }) : '---'})
                        </span>
                    </div>
                    <button 
                        onClick={() => shiftDate(1)} 
                        style={{ background: 'transparent', border: 'none', color: !availableDates.some(d => d > selectedDate) ? '#444' : '#aaa', cursor: 'pointer', fontSize: '18px', padding: '0 5px' }}
                        disabled={!availableDates.some(d => d > selectedDate)}
                    >
                        ▶
                    </button>
                </div>
            </div>

            {error && <div style={{ color: appConfig.colors.bad, marginBottom: '20px' }}>{error}</div>}

            {loading && !data ? (
                <div style={{ textAlign: 'center', padding: '50px' }}>Loading Data...</div>
            ) : data ? (
                <>
                    {/* Tabs Navigation */}
                    <div className="dashboard-tabs" style={{ display: 'flex', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '20px' }}>
                        {[
                            { id: 'indicator', label: '🧭 Indicators' },
                            { id: 'market', label: '🌍 Markets' },
                            { id: 'sector-theme', label: '🏢 Sectors & Themes' }
                        ].map(tab => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id as 'indicator' | 'market' | 'sector-theme')}
                                style={{
                                    flex: 1,
                                    padding: '12px 20px',
                                    background: 'transparent',
                                    border: 'none',
                                    borderBottom: activeTab === tab.id ? `3px solid ${appConfig.colors.accent}` : '3px solid transparent',
                                    color: activeTab === tab.id ? '#fff' : '#aaa',
                                    fontSize: '15px',
                                    fontWeight: 'bold',
                                    cursor: 'pointer',
                                    transition: 'all 0.2s',
                                    outline: 'none',
                                }}
                                onMouseEnter={e => {
                                    if (activeTab !== tab.id) {
                                        (e.target as HTMLButtonElement).style.color = '#fff';
                                        (e.target as HTMLButtonElement).style.backgroundColor = 'rgba(255,255,255,0.05)';
                                    }
                                }}
                                onMouseLeave={e => {
                                    if (activeTab !== tab.id) {
                                        (e.target as HTMLButtonElement).style.color = '#aaa';
                                        (e.target as HTMLButtonElement).style.backgroundColor = 'transparent';
                                    }
                                }}
                            >
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {/* Conditional Rendering based on activeTab */}
                    {activeTab === 'indicator' && (
                        <>
                            {/* Top Row: Trend */}
                            <div className="indicators-row-container" style={{ marginBottom: '20px', display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                                {/* 1. Market Phase Meter */}
                                <div style={{ flex: '2', minWidth: '280px' }}>
                                    <MarketPhaseMeter phase={data.market_phase} />
                                </div>
                                
                                {/* 2 & 3. Trend & Distribution Cards Wrapper */}
                                <div className="trend-cards-container" style={{ flex: '1.2', display: 'flex', gap: '20px', minWidth: '280px', width: '100%' }}>
                                    {/* 2. Market Trend Score Card */}
                                    <div className="glass-panel" style={{ flex: '1', padding: '20px', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', minWidth: '120px' }}>
                                        <div style={{ fontSize: '11px', color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>Trend Score</div>
                                        <div style={{ 
                                            fontSize: '42px', 
                                            fontWeight: 'bold', 
                                            color: data.market_trend_score >= 50 ? appConfig.colors.good : appConfig.colors.bad,
                                            lineHeight: 1
                                        }}>
                                            {Math.round(data.market_trend_score)}
                                        </div>
                                        <div style={{ fontSize: '12px', marginTop: '5px', color: '#888', textAlign: 'center' }}>
                                            {data.market_trend_score >= 80 ? 'EXTREME BULL' : data.market_trend_score >= 50 ? 'BULL' : data.market_trend_score >= 20 ? 'BEAR' : 'EXTREME BEAR'}
                                        </div>
                                    </div>

                                    {/* 3. Distribution Days Card */}
                                    <div className="glass-panel" style={{ flex: '1', padding: '20px', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', minWidth: '120px' }}>
                                        <div style={{ fontSize: '11px', color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px' }}>Distribution Days</div>
                                        <div style={{ fontSize: '42px', fontWeight: 'bold', color: data.distribution_days >= 4 ? appConfig.colors.bad : '#fff', lineHeight: 1 }}>
                                            {data.distribution_days}
                                        </div>
                                        <div style={{ fontSize: '12px', marginTop: '5px', color: '#888', textAlign: 'center' }}>
                                            {data.distribution_days >= 5 ? 'DISTRIBUTION' : 'CALM'}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Market Trend Score History Graph */}
                            {data.trend_score_history && data.trend_score_history.length > 0 && (
                                <div className="charts-row-container" style={{ display: 'flex', gap: '20px', marginBottom: '20px', flexWrap: 'wrap' }}>
                                    <div className="glass-panel" style={{ flex: '1', padding: '20px', minWidth: '280px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                                            <h3 style={{ margin: 0 }}>📈 Market Trend Score History</h3>
                                            <div style={{ fontSize: '12px', color: '#aaa' }}>Last 100 Trading Days</div>
                                        </div>
                                        <TrendScoreChart data={data.trend_score_history} height={180} />
                                    </div>

                                    <div className="glass-panel" style={{ flex: '1', padding: '20px', minWidth: '280px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                                            <h3 style={{ margin: 0 }}>📊 VXV/VIX Ratio History</h3>
                                            <div style={{ fontSize: '12px', color: '#aaa' }}>Ref: Bottom (1.0) / Top (1.2)</div>
                                        </div>
                                        <VxvVixRatioChart data={data.trend_score_history} height={180} />
                                    </div>
                                </div>
                            )}

                            {/* SPY Feature Panel */}
                            {data.spy_feature && (
                                <EtfFeaturePanel 
                                    feature={data.spy_feature} 
                                    titleSuffix="S&P 500 ETF"
                                />
                            )}

                            {/* Leading Indicators row (full width) */}
                            {data.leading && data.leading.length > 0 && (
                                <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px', marginBottom: '15px' }}>
                                        <h3 style={{ margin: 0 }}>🧭 Leading Indicators (先行指標)</h3>
                                        {data.vxv_vix_ratio && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', background: 'rgba(255,255,255,0.05)', padding: '4px 12px', borderRadius: '20px', border: `1px solid ${appConfig.colors.glassBorder}` }}>
                                                <span style={{ fontSize: '11px', color: '#aaa', textTransform: 'uppercase' }}>VXV/VIX Ratio:</span>
                                                <span style={{ 
                                                    fontSize: '14px', 
                                                    fontWeight: 'bold', 
                                                    color: data.vxv_vix_ratio <= 1.0 ? appConfig.colors.bad : data.vxv_vix_ratio >= 1.2 ? '#ffcc00' : appConfig.colors.good 
                                                }}>
                                                    {data.vxv_vix_ratio.toFixed(3)}
                                                </span>
                                                <span style={{ fontSize: '10px', color: data.vxv_vix_ratio <= 1.0 ? appConfig.colors.bad : '#888' }}>
                                                    {data.vxv_vix_ratio <= 1.0 ? '(Bottom ⚠️)' : data.vxv_vix_ratio >= 1.2 ? '(Overheated)' : '(Normal)'}
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                    {renderLeadingList(data.leading)}
                                </div>
                            )}
                        </>
                    )}

                    {activeTab === 'market' && (
                        <>
                            {/* Indices row (full width) */}
                            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                                <h3 style={{ marginTop: 0, borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>🌍 Global & Broad Markets (各市場インデックス)</h3>
                                <SummaryTable 
                                    items={data.indices} 
                                    maxPct={appConfig.thresholds.sparkline_max_pct_index} 
                                 />
                            </div>
                        </>
                    )}

                    {activeTab === 'sector-theme' && (
                        <>
                            {/* Ranking Type Toggle */}
                            <div style={{ display: 'flex', gap: '8px', marginBottom: '20px', justifyContent: 'flex-end', width: '100%' }}>
                                {[
                                    { id: 'rs_trend', label: 'RS Trend rank' },
                                    { id: 'rs_ratio', label: 'RS Ratio rank' }
                                ].map(type => (
                                    <button
                                        key={type.id}
                                        onClick={() => setRankType(type.id as 'rs_trend' | 'rs_ratio')}
                                        style={{
                                            padding: '6px 14px',
                                            borderRadius: '20px',
                                            background: rankType === type.id ? 'rgba(0, 255, 136, 0.15)' : 'rgba(255, 255, 255, 0.03)',
                                            border: `1px solid ${rankType === type.id ? appConfig.colors.good : appConfig.colors.glassBorder}`,
                                            color: rankType === type.id ? '#fff' : '#8b9cc8',
                                            fontSize: '12px',
                                            fontWeight: 'bold',
                                            cursor: 'pointer',
                                            transition: 'all 0.2s',
                                            boxShadow: rankType === type.id ? '0 0 10px rgba(0, 255, 136, 0.1)' : 'none'
                                        }}
                                        onMouseEnter={e => {
                                            if (rankType !== type.id) {
                                                (e.target as HTMLButtonElement).style.background = 'rgba(255, 255, 255, 0.08)';
                                                (e.target as HTMLButtonElement).style.color = '#fff';
                                            }
                                        }}
                                        onMouseLeave={e => {
                                            if (rankType !== type.id) {
                                                (e.target as HTMLButtonElement).style.background = 'rgba(255, 255, 255, 0.03)';
                                                (e.target as HTMLButtonElement).style.color = '#8b9cc8';
                                            }
                                        }}
                                    >
                                        {type.id === 'rs_trend' ? '📈 ' : '📊 '}{type.label}
                                    </button>
                                ))}
                            </div>

                            {/* Columns: Sectors + Themes */}
                            <div className="sectors-themes-container" style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                                {/* Column 1: Sectors */}
                                <div style={{ flex: '1', minWidth: '280px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                                    <div className="glass-panel" style={{ padding: '20px' }}>
                                        <h3 style={{ marginTop: 0, borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                                            🏢 Sectors Overview <span style={{ fontSize: '11px', fontWeight: 'normal', color: '#888', marginLeft: '5px' }}>({rankType === 'rs_trend' ? 'Trend Rank' : 'Ratio Rank'})</span>
                                        </h3>
                                        <SummaryTable 
                                            items={data.sectors} 
                                            maxPct={appConfig.thresholds.sparkline_max_pct_sector} 
                                            linkTo={(item) => `/group/${encodeURIComponent(item.ticker)}${selectedDate ? `?date=${selectedDate}` : ''}`}
                                            defaultSortKey={rankType === 'rs_trend' ? 'rs_trend_rank_s21' : 'rs_ratio_rank_e21'}
                                            rankType={rankType}
                                        />
                                    </div>
                                </div>

                                {/* Column 2: Themes Top/Bottom */}
                                <div style={{ flex: '1', minWidth: '280px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
                                    <div className="glass-panel" style={{ padding: '20px' }}>
                                        <h3 style={{ marginTop: 0, borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                                            🔥 Top Themes ({rankType === 'rs_trend' ? '1M RS Trend Rank' : '1M RS Ratio Rank'}) <span style={{ fontSize: '12px', fontWeight: 'normal', color: '#aaa' }}>Top {Math.min(themesVisibleCount, data.themes_top.length)}</span>
                                        </h3>
                                        <SummaryTable 
                                            items={data.themes_top.slice(0, themesVisibleCount)} 
                                            maxPct={appConfig.thresholds.sparkline_max_pct_theme} 
                                            linkTo={(item) => `/group/${encodeURIComponent(item.ticker)}${selectedDate ? `?date=${selectedDate}` : ''}`}
                                            defaultSortKey={rankType === 'rs_trend' ? 'rs_trend_rank_s21' : 'rs_ratio_rank_e21'}
                                            rankType={rankType}
                                        />
                                    </div>

                                    {/* Expand / Collapse button */}
                                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', justifyContent: 'center' }}>
                                        {themesVisibleCount > 10 && (
                                            <button
                                                onClick={() => setThemesVisibleCount(prev => Math.max(10, prev - 10))}
                                                style={{
                                                    flex: 1,
                                                    padding: '8px 12px',
                                                    background: 'rgba(255,255,255,0.07)',
                                                    border: `1px solid ${appConfig.colors.glassBorder}`,
                                                    borderRadius: '6px',
                                                    color: '#aaa',
                                                    cursor: 'pointer',
                                                    fontSize: '12px',
                                                    transition: 'all 0.2s'
                                                }}
                                                onMouseEnter={e => { (e.target as HTMLButtonElement).style.background = 'rgba(255,255,255,0.15)'; (e.target as HTMLButtonElement).style.color = '#fff'; }}
                                                onMouseLeave={e => { (e.target as HTMLButtonElement).style.background = 'rgba(255,255,255,0.07)'; (e.target as HTMLButtonElement).style.color = '#aaa'; }}
                                            >
                                                ▲ 表示を減らす (-10)
                                            </button>
                                        )}
                                        {themesVisibleCount < (data.themes_top.length) && (
                                            <button
                                                onClick={() => setThemesVisibleCount(prev => prev + 10)}
                                                style={{
                                                    flex: 1,
                                                    padding: '8px 12px',
                                                    background: 'rgba(255,255,255,0.07)',
                                                    border: `1px solid ${appConfig.colors.glassBorder}`,
                                                    borderRadius: '6px',
                                                    color: '#aaa',
                                                    cursor: 'pointer',
                                                    fontSize: '12px',
                                                    transition: 'all 0.2s'
                                                }}
                                                onMouseEnter={e => { (e.target as HTMLButtonElement).style.background = 'rgba(255,255,255,0.15)'; (e.target as HTMLButtonElement).style.color = '#fff'; }}
                                                onMouseLeave={e => { (e.target as HTMLButtonElement).style.background = 'rgba(255,255,255,0.07)'; (e.target as HTMLButtonElement).style.color = '#aaa'; }}
                                            >
                                                ▼ さらに表示 (+10)
                                            </button>
                                        )}
                                    </div>

                                    <div className="glass-panel" style={{ padding: '20px' }}>
                                        <h3 style={{ marginTop: 0, borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                                            ❄️ Weak Themes ({rankType === 'rs_trend' ? '1M RS Trend Rank' : '1M RS Ratio Rank'}) <span style={{ fontSize: '12px', fontWeight: 'normal', color: '#aaa' }}>Bottom {Math.min(themesVisibleCount, data.themes_bottom.length)}</span>
                                        </h3>
                                        <SummaryTable 
                                            items={data.themes_bottom.slice(0, themesVisibleCount)} 
                                            maxPct={appConfig.thresholds.sparkline_max_pct_theme} 
                                            linkTo={(item) => `/group/${encodeURIComponent(item.ticker)}${selectedDate ? `?date=${selectedDate}` : ''}`}
                                            defaultSortKey={rankType === 'rs_trend' ? 'rs_trend_rank_s21' : 'rs_ratio_rank_e21'}
                                            defaultSortDirection="desc"
                                            rankType={rankType}
                                        />
                                    </div>
                                </div>
                            </div>

                            {/* Theme RRG Scatter Plot */}
                            <div className="glass-panel" style={{ padding: '20px', marginTop: '20px' }}>
                                <h3 style={{ marginTop: 0, borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>📡 Themes RRG Map (Latest)</h3>
                                <div style={{ height: '500px' }}>
                                    <RrgChart
                                        series={[
                                            ...data.themes_top.slice(0, themesVisibleCount).map((item) => ({
                                                ticker: item.ticker,
                                                color: appConfig.colors.good,
                                                data: [{
                                                    time: data.date,
                                                    close: item.close,
                                                    rs_ratio_e14: item.rs_ratio_e21,
                                                    rs_momentum_e14: item.rs_momentum_e21,
                                                    rs_ratio_e21: item.rs_ratio_e21,
                                                    rs_momentum_e21: item.rs_momentum_e21,
                                                    rs_ratio_e63: item.rs_ratio_e21,
                                                    rs_momentum_e63: item.rs_momentum_e21,
                                                    open: item.close, high: item.close, low: item.close, volume: 0
                                                }]
                                            })),
                                            ...data.themes_bottom.slice(0, themesVisibleCount).map((item) => ({
                                                ticker: item.ticker,
                                                color: appConfig.colors.bad,
                                                data: [{
                                                    time: data.date,
                                                    close: item.close,
                                                    rs_ratio_e14: item.rs_ratio_e21,
                                                    rs_momentum_e14: item.rs_momentum_e21,
                                                    rs_ratio_e21: item.rs_ratio_e21,
                                                    rs_momentum_e21: item.rs_momentum_e21,
                                                    rs_ratio_e63: item.rs_ratio_e21,
                                                    rs_momentum_e63: item.rs_momentum_e21,
                                                    open: item.close, high: item.close, low: item.close, volume: 0
                                                }]
                                            }))
                                        ] as RrgSeries[]}
                                    />
                                </div>
                                <div style={{ fontSize: '12px', color: '#666', marginTop: '10px', textAlign: 'center' }}>
                                    ※ 表示中の全テーマ（Top & Weak）を最新の 21日 RS Ratio/Momentum でプロットしています。
                                </div>
                            </div>
                        </>
                    )}
                </>
            ) : null}
        </div>
    );
};
