import React, { useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { appConfig, getIntensityColor } from '../config';
import { useWatchlist } from '../hooks/useWatchlist';
import { WatchlistButton } from '../components/WatchlistButton';

// Types for preset API response
interface PresetItem {
    id: string;
    name: string;
    subtitle?: string;
    group: string;
    filters: Record<string, number | string>;
    expression?: string;
    special?: string;
}

interface PresetsResponse {
    rise: PresetItem[];
    fall: PresetItem[];
}

interface ScreenerDashboardItem {
    id: number;
    ticker: string;
    name: string;
    change_pct: number;
}

interface ScreenerDashboardCategory {
    id: string;
    name: string;
    subtitle?: string;
    group: string;
    items: ScreenerDashboardItem[];
}

interface ScreenerDashboardResponse {
    rise: ScreenerDashboardCategory[];
    fall: ScreenerDashboardCategory[];
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
        if (preset.special) {
            // Map special flags to boolean params
            if (preset.special === 'rrg_leading_in') params.set('rrg_leading_in', 'true');
            if (preset.special === 'rrg_lagging_in') params.set('rrg_lagging_in', 'true');
            if (preset.special === 'rrg_improving_in') params.set('rrg_improving_in', 'true');
            if (preset.special === 'theme_rs21_gt_63') params.set('theme_rs21_gt_63', 'true');
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
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    <Link to={linkTarget} style={{ textDecoration: 'none', color: '#fff', fontSize: '16px', fontWeight: 'bold' }}>
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
                                    </div>
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
