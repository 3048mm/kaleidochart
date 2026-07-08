import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { appConfig, getIntensityColor } from '../config';
import { useWatchlist } from '../hooks/useWatchlist';
import { WatchlistButton } from '../components/WatchlistButton';
import { Sparkline } from '../components/Sparkline';
import { ScreenerDashboardItem, ScreenerDashboardCategory, ScreenerDashboardResponse } from '../types';

interface PresetItem {
    id: string;
    name: string;
    subtitle?: string;
    group: string;
    filters: Record<string, number | string>;
    expression?: string;
}

interface PresetsResponse {
    rise: PresetItem[];
    fall: PresetItem[];
}

export const ScreenerPage: React.FC = () => {
    const location = useLocation();
    const navigate = useNavigate();
    const queryParams = new URLSearchParams(location.search);
    const urlDate = queryParams.get('target_date');

    const [availableDates, setAvailableDates] = useState<string[]>([]);
    const [selectedDate, setSelectedDate] = useState<string>('');
    const [data, setData] = useState<ScreenerDashboardResponse | null>(null);
    const [presets, setPresets] = useState<PresetsResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'Rise' | 'Fall'>('Rise');
    
    const { isTickerActive, toggleWatchlist } = useWatchlist();

    // Load available dates
    useEffect(() => {
        fetch('/api/available_dates')
            .then(res => res.json())
            .then(json => {
                if (json.dates && json.dates.length > 0) {
                    setAvailableDates(json.dates);
                    // Initialize if not in URL
                    if (!urlDate) {
                        setSelectedDate(json.dates[0]);
                    } else {
                        setSelectedDate(urlDate);
                    }
                }
            })
            .catch(err => console.error('Failed to fetch available dates:', err));
    }, [urlDate]);

    const shiftDate = (direction: number) => {
        if (!selectedDate || availableDates.length === 0) return;
        const currentIndex = availableDates.indexOf(selectedDate);
        
        if (currentIndex !== -1) {
            const newIndex = currentIndex - direction;
            if (newIndex >= 0 && newIndex < availableDates.length) {
                setSelectedDate(availableDates[newIndex]);
            }
        } else {
            // Weekend/Holidays logic
            if (direction === -1) {
                const target = availableDates.find(d => d < selectedDate);
                if (target) setSelectedDate(target);
            } else {
                const target = [...availableDates].reverse().find(d => d > selectedDate);
                if (target) setSelectedDate(target);
            }
        }
    };

    // Load presets from API (once)
    useEffect(() => {
        fetch('/api/screener/presets')
            .then(res => res.json())
            .then((json: PresetsResponse) => setPresets(json))
            .catch(err => console.error('Failed to load presets:', err));
    }, []);

    // Build result link from preset filters
    const buildResultLink = (preset: PresetItem) => {
        const params = new URLSearchParams({ target_date: selectedDate });
        for (const [key, value] of Object.entries(preset.filters)) {
            params.set(key, String(value));
        }
        if (preset.expression) {
            params.set('expression', preset.expression);
        }
        return `/screener/result/${preset.id}?${params.toString()}`;
    };

    // Fetch dashboard summary data
    useEffect(() => {
        setLoading(true);
        const params = new URLSearchParams();
        if (selectedDate) {
            params.append('target_date', selectedDate);
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
        // Find matching preset for link building
        const allPresets = presets ? [...(presets.rise || []), ...(presets.fall || [])] : [];
        const matchingPreset = allPresets.find(p => p.id === category.id);

        const linkTarget = matchingPreset
            ? buildResultLink(matchingPreset)
            : `/screener/result/${category.id}?target_date=${selectedDate}`;

        return (
            <div key={category.id} className="glass-panel" style={{ padding: '15px', display: 'flex', flexDirection: 'column', minWidth: '300px', flex: '1 1 300px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    <Link to={linkTarget} style={{ textDecoration: 'none', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: '5px' }}>
                            <span style={{ fontSize: '16px', fontWeight: 'bold', color: '#fff' }}>{category.name}</span>
                            {category.subtitle && <span style={{ fontSize: '11px', color: '#aaa', fontWeight: 'normal' }}>{category.subtitle}</span>}
                            <span style={{ fontSize: '12px', color: '#aaa', fontWeight: 'normal' }}>›</span>
                        </div>
                        {category.subname && (
                            <div style={{ fontSize: '11px', color: '#888', marginTop: '2px', fontWeight: 'normal' }}>
                                {category.subname}
                            </div>
                        )}
                    </Link>
                    <span style={{ fontSize: '12px', color: '#aaa', marginTop: '4px' }}>Top 8</span>
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
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '2px' }}>
                                        <WatchlistButton 
                                            isActive={isTickerActive(item.ticker)} 
                                            onClick={(e) => {
                                                e.preventDefault();
                                                toggleWatchlist(item.ticker, selectedDate);
                                            }}
                                            size={16}
                                        />
                                        <Link to={`/chart/${encodeURIComponent(item.ticker)}`} target="_blank" style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: '500' }}>
                                            {item.ticker}
                                        </Link>
                                        
                                        {item.theme_name && (
                                            <span 
                                                title={`RSRatio %rrank: ${(item.theme_rs_ratio! * 100).toFixed(1)}%`}
                                                style={{
                                                    fontSize: '9px',
                                                    color: activeTab === 'Rise' ? '#00ff88' : '#ff4444',
                                                    background: activeTab === 'Rise' ? 'rgba(0, 255, 136, 0.06)' : 'rgba(255, 68, 68, 0.06)',
                                                    border: activeTab === 'Rise' ? '1px solid rgba(0, 255, 136, 0.15)' : '1px solid rgba(255, 68, 68, 0.15)',
                                                    padding: '1px 5px',
                                                    borderRadius: '4px',
                                                    marginLeft: '8px',
                                                    cursor: 'help',
                                                    fontWeight: 'normal',
                                                    whiteSpace: 'nowrap'
                                                }}
                                            >
                                                {item.theme_name}
                                            </span>
                                        )}
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                        {item.rs_trend_history && item.rs_trend_history.length > 0 && (
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
                                                <span style={{ fontSize: '8px', color: '#555', fontWeight: 'bold', letterSpacing: '0.05em' }}>RS</span>
                                                <div style={{ width: '60px', height: '16px', display: 'flex', alignItems: 'center' }} title="RSTrend21 (Last 30 days)">
                                                    {(() => {
                                                        const hist = item.rs_trend_history;
                                                        const firstVal = hist[0];
                                                        const lastVal = hist[hist.length - 1];
                                                        const isUp = lastVal >= firstVal;
                                                        return (
                                                            <Sparkline 
                                                                data={hist} 
                                                                width={60} 
                                                                height={16} 
                                                                color={isUp ? appConfig.colors.good : appConfig.colors.bad} 
                                                            />
                                                        );
                                                    })()}
                                                </div>
                                            </div>
                                        )}
                                        <div style={{
                                            padding: '2px 6px',
                                            borderRadius: '3px',
                                            backgroundColor: bgColor,
                                            color: textColor,
                                            fontWeight: '600',
                                            fontSize: '11px',
                                            minWidth: '55px',
                                            textAlign: 'right'
                                        }}>
                                            {formatPct(item.change_pct)}
                                        </div>
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

    const renderHotPicks = () => {
        if (!data) return null;
        const categories = activeTab === 'Rise' ? data.rise : data.fall;
        const tickerMap: Record<string, { item: ScreenerDashboardItem; categories: string[]; count: number }> = {};
        
        // Define exact allowed groups for confluence
        const allowedGroups = activeTab === 'Rise' ? ['Check'] : ['Warning'];

        categories.forEach(cat => {
            // Trim and check group name strictly
            const currentGroup = (cat.group || '').trim();
            if (!allowedGroups.includes(currentGroup)) return;

            cat.items.forEach(item => {
                if (!tickerMap[item.ticker]) {
                    tickerMap[item.ticker] = { item, categories: [], count: 0 };
                }
                tickerMap[item.ticker].count++;
                tickerMap[item.ticker].categories.push(cat.name);
            });
        });

        const hotPicks = Object.values(tickerMap)
            .filter(p => p.count > 1)
            .sort((a, b) => b.count - a.count || Math.abs(b.item.change_pct) - Math.abs(a.item.change_pct))
            .slice(0, 3);

        if (hotPicks.length === 0) return null;

        return (
            <div className="glass-panel" style={{ 
                padding: '20px', 
                marginBottom: '10px', 
                background: 'linear-gradient(135deg, rgba(34, 211, 160, 0.1) 0%, rgba(59, 130, 246, 0.05) 100%)',
                border: '1px solid rgba(34, 211, 160, 0.3)',
                boxShadow: '0 8px 32px rgba(0,0,0,0.3)'
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '15px' }}>
                    <span style={{ fontSize: '20px' }}>🔥</span>
                    <h2 style={{ margin: 0, fontSize: '18px', color: '#22d3a0', letterSpacing: '0.05em' }}>HOT PICKS <span style={{fontSize: '12px', color: '#888', fontWeight: 'normal', marginLeft: '8px'}}>— Multiple Signal Confluence</span></h2>
                </div>
                <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
                    {hotPicks.map((pick, idx) => (
                        <div key={pick.item.ticker} style={{ 
                            flex: '1', 
                            minWidth: '280px', 
                            background: 'rgba(255,255,255,0.03)', 
                            borderRadius: '12px', 
                            padding: '15px',
                            border: '1px solid rgba(255,255,255,0.05)',
                            position: 'relative',
                            overflow: 'hidden'
                        }}>
                            <div style={{ position: 'absolute', top: '-10px', right: '-10px', fontSize: '40px', opacity: 0.05, fontWeight: 'bold' }}>#{idx + 1}</div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <WatchlistButton 
                                        isActive={isTickerActive(pick.item.ticker)} 
                                        onClick={(e) => {
                                            e.preventDefault();
                                            toggleWatchlist(pick.item.ticker, selectedDate);
                                        }}
                                        size={18}
                                    />
                                    <Link to={`/chart/${encodeURIComponent(pick.item.ticker)}`} target="_blank" style={{ fontSize: '20px', fontWeight: 'bold', color: '#fff', textDecoration: 'none' }}>
                                        {pick.item.ticker}
                                    </Link>
                                    <span style={{ fontSize: '12px', color: '#aaa', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pick.item.name}</span>
                                </div>
                                <div style={{ 
                                    padding: '4px 8px', 
                                    borderRadius: '6px', 
                                    backgroundColor: getIntensityColor(pick.item.change_pct, 5, pick.item.change_pct >= 0 ? '#22d3a0' : '#f43f5e'), 
                                    color: '#000', 
                                    fontWeight: 'bold',
                                    fontSize: '13px'
                                }}>
                                    {pick.item.change_pct > 0 ? '+' : ''}{pick.item.change_pct.toFixed(2)}%
                                </div>
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                                {pick.categories.map(cat => (
                                    <span key={cat} style={{ 
                                        fontSize: '10px', 
                                        padding: '2px 8px', 
                                        background: 'rgba(59, 130, 246, 0.2)', 
                                        color: '#60a5fa', 
                                        borderRadius: '100px',
                                        border: '1px solid rgba(59, 130, 246, 0.3)'
                                    }}>
                                        {cat}
                                    </span>
                                ))}
                                <span style={{ 
                                    fontSize: '10px', 
                                    padding: '2px 8px', 
                                    background: 'rgba(34, 211, 160, 0.2)', 
                                    color: '#22d3a0', 
                                    borderRadius: '100px',
                                    fontWeight: 'bold'
                                }}>
                                    {pick.count} Signals
                                </span>
                            </div>
                        </div>
                    ))}
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
                    <button 
                        onClick={() => shiftDate(-1)} 
                        style={{ background: 'transparent', border: 'none', color: !availableDates.some(d => d < selectedDate) ? '#444' : '#aaa', cursor: 'pointer', fontSize: '18px', padding: '0 5px' }}
                        disabled={!availableDates.some(d => d < selectedDate)}
                    >
                        ◀
                    </button>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(255,255,255,0.05)', padding: '4px 10px', borderRadius: '6px', border: `1px solid ${appConfig.colors.glassBorder}` }}>
                        <input
                            type="date"
                            value={selectedDate}
                            onChange={e => setSelectedDate(e.target.value)}
                            style={{
                                background: 'transparent',
                                border: 'none',
                                color: '#fff',
                                fontSize: '14px',
                                outline: 'none',
                                colorScheme: 'dark',
                                cursor: 'pointer'
                            }}
                        />
                        <span style={{ fontSize: '13px', color: appConfig.colors.accent, fontWeight: 'bold', minWidth: '35px' }}>
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

                    {renderHotPicks()}

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
                                <div className="screener-panels-container" style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
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
