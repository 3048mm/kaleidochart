import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ScreenerDashboardResponse, ScreenerDashboardCategory } from '../types';
import { appConfig, getIntensityColor } from '../config';

export const ScreenerPage: React.FC = () => {
    const location = useLocation();
    const navigate = useNavigate();
    const queryParams = new URLSearchParams(location.search);
    const urlDate = queryParams.get('target_date');

    const [selectedDate, setSelectedDate] = useState<string>(() => {
        if (urlDate) return urlDate;
        const today = new Date();
        return today.toISOString().split('T')[0];
    });
    const [data, setData] = useState<ScreenerDashboardResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'Rise' | 'Fall'>('Rise');

    const shiftDate = (days: number) => {
        if (!selectedDate) return;
        const d = new Date(selectedDate);
        d.setDate(d.getDate() + days);
        setSelectedDate(d.toISOString().split('T')[0]);
    };

    // Preset -> initial URL params for the result page
    const PRESET_PARAMS: Record<string, Record<string, string>> = {
        'check_1d_gain': {
            min_1d_gain_pct: '4',
            min_rel_vol: '1',
            rs_rank_21_gt_63: 'true',
            max_dist_sma50_atr: '6',
            min_adr_pct_21: '4',
            min_market_cap: '1000000000',
        },
        'check_volume_surge': {
            min_vol_surge_21: '1.5',
            min_rel_vol: '1.2',
            min_1d_gain_pct: '0',
            min_adr_pct_21: '4',
            min_market_cap: '1000000000',
            max_dist_sma50_atr: '6',
        },
        'check_21ema': {
            min_dist_21ema_pct: '-2',
            max_dist_21ema_pct: '2',
            max_dist_sma50_atr: '6',
            min_adr_pct_21: '4',
            min_market_cap: '1000000000',
        },
        'check_momentum97': {
            min_rs_ratio_21_rank: '0.97',
            trend_template_ok: '1',
        },
        'check_vcp': {
            max_adr_pct_21: '3',
            min_dist_sma50_pct: '0',
            min_rs_condition_21: '1',
            rs_rank_21_gt_63: 'true',
            min_market_cap: '1000000000',
        },
        'td9_overhead': { min_td9: '8' },
        'td9_rebound': { max_td9: '-8' },
        'dist_sma50_atr_8': { min_dist_sma50_atr: '8' },
        'high_vol_dist': { min_vol_surge_21: '1.5', max_1d_gain_pct: '-2' },
    };

    const buildResultLink = (presetId: string) => {
        const pp = PRESET_PARAMS[presetId] || {};
        const params = new URLSearchParams({ target_date: selectedDate, ...pp });
        return `/screener/result/${presetId}?${params.toString()}`;
    };

    useEffect(() => {
        setLoading(true);
        const params = new URLSearchParams();
        if (selectedDate) {
            params.append('target_date', selectedDate);
            // Sync URL without triggering a full scroll/reload if possible, 
            // but navigate(..., { replace: true }) is standard.
            navigate(`/screener?${params.toString()}`, { replace: true });
        }

        fetch(`/api/screener/dashboard?${params.toString()}`)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch screener dashboard');
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
    }, [selectedDate, navigate]);

    const renderPanel = (category: ScreenerDashboardCategory) => {
        return (
            <div key={category.id} className="glass-panel" style={{ padding: '15px', display: 'flex', flexDirection: 'column', minWidth: '300px', flex: '1 1 300px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    <Link to={buildResultLink(category.id)} style={{ textDecoration: 'none', color: '#fff', fontSize: '16px', fontWeight: 'bold' }}>
                        {category.name}
                        {category.subtitle && <span style={{ fontSize: '11px', color: '#aaa', marginLeft: '6px', fontWeight: 'normal' }}>{category.subtitle}</span>}
                        <span style={{ fontSize: '12px', color: '#aaa', marginLeft: '4px', fontWeight: 'normal' }}>›</span>
                    </Link>
                    <span style={{ fontSize: '12px', color: '#aaa' }}>Top 8</span>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {category.items.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '10px', color: '#666', fontSize: '12px' }}>該当なし (0)</div>
                    ) : (
                        category.items.map((item) => {
                            const bgColor = getIntensityColor(
                                item.change_pct,
                                appConfig.thresholds.sparkline_max_pct_sector,
                                item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad
                            );
                            const textColor = Math.abs(item.change_pct) > appConfig.thresholds.sparkline_max_pct_sector * 0.5 ? '#000' : '#fff';
                            const formatPct = (pct: number) => `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;

                            return (
                                <div key={item.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '13px' }}>
                                    <Link to={`/chart/${encodeURIComponent(item.ticker)}`} target="_blank" style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: '500' }}>
                                        {item.ticker}
                                    </Link>
                                    <div style={{
                                        padding: '2px 6px',
                                        borderRadius: '3px',
                                        backgroundColor: bgColor,
                                        color: textColor,
                                        fontWeight: '600',
                                        fontSize: '11px'
                                    }}>
                                        {formatPct(item.change_pct)}
                                    </div>
                                </div>
                            );
                        })
                    )}
                    {category.items.length >= 8 && (
                        <div style={{ textAlign: 'center', fontSize: '16px', color: '#666', marginTop: '4px' }}>...</div>
                    )}
                </div>
            </div>
        );
    };

    return (
        <div style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            
            {/* Header / Date Picker */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h1 style={{ margin: 0, fontSize: '24px' }}>Market Screener</h1>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <label style={{ fontSize: '14px', color: '#aaa' }}>Base Date:</label>
                    <button onClick={() => shiftDate(-1)} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '18px', padding: '0 5px' }}>◀</button>
                    <input
                        type="date"
                        value={selectedDate}
                        onChange={e => setSelectedDate(e.target.value)}
                        style={{
                            padding: '6px 12px',
                            borderRadius: '4px',
                            background: '#1a1d26',
                            border: '1px solid rgba(255,255,255,0.2)',
                            color: '#fff',
                            colorScheme: 'dark'
                        }}
                    />
                    <button onClick={() => shiftDate(1)} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '18px', padding: '0 5px' }}>▶</button>
                </div>
            </div>

            {loading && !data && <div style={{ padding: '40px', textAlign: 'center' }}>Loading dashboard...</div>}
            {error && <div style={{ color: appConfig.colors.bad, padding: '20px' }}>{error}</div>}

            {data && (
                <>
                    {/* Tabs */}
                    <div style={{ display: 'flex', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '10px' }}>
                        <button
                            onClick={() => setActiveTab('Rise')}
                            style={{
                                flex: 1,
                                padding: '10px 20px',
                                background: 'transparent',
                                border: 'none',
                                borderBottom: activeTab === 'Rise' ? `3px solid ${appConfig.colors.accent}` : '3px solid transparent',
                                color: activeTab === 'Rise' ? appConfig.colors.good : '#aaa',
                                fontSize: '16px',
                                fontWeight: activeTab === 'Rise' ? 'bold' : 'normal',
                                cursor: 'pointer',
                                outline: 'none',
                                transition: 'all 0.2s'
                            }}
                        >
                            <span style={{ fontSize: '18px', marginRight: '8px' }}>🚀</span> Rise (買い目線)
                        </button>
                        <div style={{ width: '1px', background: appConfig.colors.glassBorder }} />
                        <button
                            onClick={() => setActiveTab('Fall')}
                            style={{
                                flex: 1,
                                padding: '10px 20px',
                                background: 'transparent',
                                border: 'none',
                                borderBottom: activeTab === 'Fall' ? `3px solid ${appConfig.colors.accent}` : '3px solid transparent',
                                color: activeTab === 'Fall' ? appConfig.colors.bad : '#aaa',
                                fontSize: '16px',
                                fontWeight: activeTab === 'Fall' ? 'bold' : 'normal',
                                cursor: 'pointer',
                                outline: 'none',
                                transition: 'all 0.2s'
                            }}
                        >
                            <span style={{ fontSize: '18px', marginRight: '8px' }}>📉</span> Fall (警戒・売り目線)
                        </button>
                    </div>

                    {/* Dashboard Content */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '30px' }}>
                        {Object.entries(
                            (activeTab === 'Rise' ? data.rise : data.fall).reduce((acc, cat) => {
                                if (!acc[cat.group]) acc[cat.group] = [];
                                acc[cat.group].push(cat);
                                return acc;
                            }, {} as Record<string, ScreenerDashboardCategory[]>)
                        ).map(([groupName, categories]) => (
                            <div key={groupName}>
                                <h3 style={{ margin: '0 0 15px 5px', fontSize: '18px', color: '#ccc', borderLeft: `4px solid ${appConfig.colors.accent}`, paddingLeft: '10px' }}>
                                    [{groupName}]
                                </h3>
                                <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                                    {categories.map(cat => renderPanel(cat))}
                                </div>
                            </div>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
};
