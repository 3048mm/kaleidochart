import React, { useState, useEffect } from 'react';
import { Link, useParams, useLocation, useNavigate } from 'react-router-dom';
import { ScreenerResultItem } from '../types';
import { Sparkline } from '../components/Sparkline';
import { appConfig, getIntensityColor } from '../config';
import { useWatchlist } from '../hooks/useWatchlist';
import { WatchlistButton } from '../components/WatchlistButton';


type SortKey = 'change_pct' | 'rs_ratio_rank_e21' | 'vol_surge_21' | 'up_down_vol_ratio_50' | 'vcr';

// --- Types for Meta API ---
interface ColumnMeta {
    name: string;
    label: string;
    category: string;
    type: string; // 'float' | 'int'
    step?: number;
}

interface MetaResponse {
    columns: ColumnMeta[];
    rank_indicators: string[];
    virtual_columns: ColumnMeta[];
    labels?: Record<string, string>;
}

// Hard-wired boolean filters that cannot be expressed as simple min/max
const BOOLEAN_FILTERS = [
    { id: 'is_rs_ratio_rank_e21_gt_e63', label: 'RS Ratio Rank 21 > 63', param: 'is_rs_ratio_rank_e21_gt_e63' },
    { id: 'is_theme_rs_ratio_e21_gt_e63', label: 'Theme RS21 > RS63', param: 'is_theme_rs_ratio_e21_gt_e63' },
    { id: 'rrg_leading_in', label: 'RRG Leading In', param: 'rrg_leading_in' },
    { id: 'rrg_lagging_in', label: 'RRG Lagging In', param: 'rrg_lagging_in' },
    { id: 'rrg_improving_in', label: 'RRG Improving In', param: 'rrg_improving_in' },
];

// --- Dynamic Filter Row ---
const DynFilterRow: React.FC<{
    col: ColumnMeta;
    searchParams: URLSearchParams;
    onChange: (keysToSet: Record<string, string>, keysToRemove: string[]) => void;
}> = ({ col, searchParams, onChange }) => {
    const minKey = `min_${col.name}`;
    const maxKey = `max_${col.name}`;
    // Check for rank variant
    const minRankKey = `min_${col.name}_rank`;
    const maxRankKey = `max_${col.name}_rank`;

    const isActive = searchParams.has(minKey) || searchParams.has(maxKey) ||
                     searchParams.has(minRankKey) || searchParams.has(maxRankKey) ||
                     searchParams.has(col.name);
    const [checked, setChecked] = useState(isActive);
    const [minVal, setMinVal] = useState(searchParams.get(minKey) || '');
    const [maxVal, setMaxVal] = useState(searchParams.get(maxKey) || '');

    useEffect(() => {
        setChecked(isActive);
        setMinVal(searchParams.get(minKey) || '');
        setMaxVal(searchParams.get(maxKey) || '');
    }, [searchParams.toString()]);

    useEffect(() => {
        if (checked) {
            const timer = setTimeout(() => {
                const toSet: Record<string, string> = {};
                const toRemove: string[] = [];
                if (minVal) toSet[minKey] = minVal; else toRemove.push(minKey);
                if (maxVal) toSet[maxKey] = maxVal; else toRemove.push(maxKey);
                const currentMin = searchParams.get(minKey) || '';
                const currentMax = searchParams.get(maxKey) || '';
                if (currentMin !== minVal || currentMax !== maxVal) {
                    onChange(toSet, toRemove);
                }
            }, 500);
            return () => clearTimeout(timer);
        }
    }, [minVal, maxVal]);

    const handleCheck = (c: boolean) => {
        setChecked(c);
        if (!c) {
            onChange({}, [minKey, maxKey, col.name, minRankKey, maxRankKey]);
            setMinVal('');
            setMaxVal('');
        }
    };

    const inputStyle = {
        width: '55px', padding: '2px 4px', fontSize: '11px',
        background: '#1a1d26', border: '1px solid rgba(255,255,255,0.2)',
        color: '#fff', borderRadius: '3px', outline: 'none', colorScheme: 'dark' as const
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '4px 0' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer', color: checked ? '#fff' : '#aaa' }}>
                <input type="checkbox" checked={checked} onChange={e => handleCheck(e.target.checked)} style={{ accentColor: appConfig.colors.accent, outline: 'none' }} />
                {col.label}
            </label>
            {checked && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', paddingLeft: '22px' }}>
                    <input type="number" step={col.step || 0.1} value={minVal} onChange={e => setMinVal(e.target.value)} placeholder="Min" style={inputStyle} />
                    <span style={{ fontSize: '11px', color: '#666' }}>〜</span>
                    <input type="number" step={col.step || 0.1} value={maxVal} onChange={e => setMaxVal(e.target.value)} placeholder="Max" style={inputStyle} />
                </div>
            )}
        </div>
    );
};

// --- Boolean Filter Row ---
const BoolFilterRow: React.FC<{
    label: string;
    param: string;
    searchParams: URLSearchParams;
    onChange: (keysToSet: Record<string, string>, keysToRemove: string[]) => void;
}> = ({ label, param, searchParams, onChange }) => {
    const isActive = searchParams.has(param) && searchParams.get(param) !== 'false';
    const [checked, setChecked] = useState(isActive);

    useEffect(() => {
        setChecked(isActive);
    }, [searchParams.toString()]);

    const handleCheck = (c: boolean) => {
        setChecked(c);
        if (c) onChange({ [param]: 'true' }, []);
        else onChange({}, [param]);
    };

    return (
        <div style={{ padding: '4px 0' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer', color: checked ? '#fff' : '#aaa' }}>
                <input type="checkbox" checked={checked} onChange={e => handleCheck(e.target.checked)} style={{ accentColor: appConfig.colors.accent, outline: 'none' }} />
                {label}
            </label>
        </div>
    );
};


export const ScreenerResultPage: React.FC = () => {
    const { presetId } = useParams<{ presetId: string }>();
    const location = useLocation();
    const navigate = useNavigate();
    const queryParams = new URLSearchParams(location.search);
    const targetDate = queryParams.get('target_date') || '';

    // Data State
    const [results, setResults] = useState<ScreenerResultItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [meta, setMeta] = useState<MetaResponse | null>(null);
    const [availableDates, setAvailableDates] = useState<string[]>([]);

    const [sortKey, setSortKey] = useState<SortKey>('change_pct');
    const [sortDesc, setSortDesc] = useState(true);
    const [isFilterOpen, setIsFilterOpen] = useState(false);
    const { isTickerActive, toggleWatchlist } = useWatchlist();

    const getLabel = (key: string, fallback: string) => {
        return meta?.labels?.[key] || fallback;
    };


    // Load available dates
    useEffect(() => {
        fetch('/api/available_dates')
            .then(res => res.json())
            .then(json => {
                if (json.dates && json.dates.length > 0) {
                    setAvailableDates(json.dates);
                    // If no targetDate in URL, initialize with latest?
                    // Usually we come here with a date from the dashboard/screener, 
                    // but let's handle the direct navigation case.
                    if (!targetDate) {
                        updateDateURL(json.dates[0]);
                    }
                }
            })
            .catch(err => console.error('Failed to fetch available dates:', err));
    }, []);

    // Load column meta from API (once)
    useEffect(() => {
        fetch('/api/screener/meta')
            .then(res => res.json())
            .then((json: MetaResponse) => setMeta(json))
            .catch(err => console.error('Failed to load screener meta:', err));
    }, []);

    const handleFilterChange = (keysToSet: Record<string, string>, keysToRemove: string[]) => {
        const nextParams = new URLSearchParams(location.search);
        for (const [k, v] of Object.entries(keysToSet)) nextParams.set(k, v);
        for (const k of keysToRemove) nextParams.delete(k);
        navigate(`${location.pathname}?${nextParams.toString()}`, { replace: true });
    };

    const updateDateURL = (newDate: string) => {
        const nextParams = new URLSearchParams(location.search);
        nextParams.set('target_date', newDate);
        navigate(`${location.pathname}?${nextParams.toString()}`, { replace: true });
    };

    const shiftDate = (direction: number) => {
        if (!targetDate || availableDates.length === 0) return;
        const currentIndex = availableDates.indexOf(targetDate);
        
        if (currentIndex !== -1) {
            const newIndex = currentIndex - direction;
            if (newIndex >= 0 && newIndex < availableDates.length) {
                updateDateURL(availableDates[newIndex]);
            }
        } else {
            // Weekend logic
            if (direction === -1) {
                const target = availableDates.find(d => d < targetDate);
                if (target) updateDateURL(target);
            } else {
                const target = [...availableDates].reverse().find(d => d > targetDate);
                if (target) updateDateURL(target);
            }
        }
    };

    useEffect(() => {
        setLoading(true);
        setError('');
        const params = new URLSearchParams(location.search);
        fetch(`/api/screener?${params.toString()}`)
            .then(res => {
                if (!res.ok) throw new Error('API fetch error');
                return res.json();
            })
            .then(json => setResults(json))
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, [presetId, location.search]);

    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortDesc(!sortDesc);
        } else {
            setSortKey(key);
            setSortDesc(true);
        }
    };

    // Group columns by category for left panel
    const groupedColumns = (): Record<string, ColumnMeta[]> => {
        if (!meta) return {};
        const all = [...meta.virtual_columns, ...meta.columns];
        const groups: Record<string, ColumnMeta[]> = {};
        for (const col of all) {
            if (!groups[col.category]) groups[col.category] = [];
            groups[col.category].push(col);
        }
        return groups;
    };

    const renderResults = () => {
        if (loading) return <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>読み込み中...</div>;
        if (error) return <div style={{ padding: '40px', textAlign: 'center', color: '#ff6b6b' }}>Error: {error}</div>;
        if (!results || results.length === 0) return <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>データなし</div>;

        const sortedResults = [...results].sort((a, b) => {
            const valA = (a as any)[sortKey] ?? -999999;
            const valB = (b as any)[sortKey] ?? -999999;
            if (valA < valB) return sortDesc ? 1 : -1;
            if (valA > valB) return sortDesc ? -1 : 1;
            return 0;
        });

        return (
            <div className="dashboard-list">
                {/* Header Row */}
                <div style={{
                    display: 'flex', fontSize: '11px', color: '#aaa', paddingBottom: '8px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '8px',
                    gap: '8px', userSelect: 'none'
                }}>
                    <div style={{ width: '24px' }}></div>
                    <div style={{ flex: '1', minWidth: '80px' }}>Name</div>

                    <div style={{ width: '52px', textAlign: 'right', paddingRight: '5px' }}>Close</div>
                    <div
                        style={{ width: '50px', textAlign: 'center', cursor: 'pointer', color: sortKey === 'change_pct' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('change_pct')}
                        title="Sort by 1D%"
                    >
                        {getLabel('change_1d_pct', '1D%')} {sortKey === 'change_pct' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '50px', textAlign: 'right', paddingRight: '5px' }}>{getLabel('dist_ema21_pct', '21E%')}</div>
                    <div
                        style={{ width: '50px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'vol_surge_21' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('vol_surge_21')}
                        title="Sort by Volume Surge"
                    >
                        {getLabel('vol_surge_21', 'Vol SG')} {sortKey === 'vol_surge_21' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '36px', textAlign: 'right' }}>{getLabel('adr_pct_21', 'ADR%')}</div>
                    <div
                        style={{ width: '36px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'up_down_vol_ratio_50' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('up_down_vol_ratio_50')}
                        title="U/D Volume Ratio (50d)"
                    >
                        {getLabel('up_down_vol_ratio_50', 'U/D')} {sortKey === 'up_down_vol_ratio_50' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div
                        style={{ width: '36px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'vcr' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('vcr')}
                        title="Volume Contraction Ratio"
                    >
                        {getLabel('vcr', 'VCR')} {sortKey === 'vcr' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '24px', textAlign: 'center' }} title="RS Leading Dots">RS.</div>
                    <div style={{ width: '40px', textAlign: 'right' }}>{getLabel('sma50_atr_mult', '50/ATR')}</div>
                    <div style={{ width: '60px', textAlign: 'center' }}>Price Trend</div>
                    <div
                        style={{ width: '32px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'rs_ratio_rank_e21' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('rs_ratio_rank_e21')}
                        title="Sort by RSR21% Rank"
                    >
                        {getLabel('rs_ratio_rank_e21', 'RSR21')} {sortKey === 'rs_ratio_rank_e21' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '32px', textAlign: 'right' }}>{getLabel('rs_ratio_rank_e63', 'RSR63')}</div>
                </div>

                {sortedResults.map(item => {
                    const bgColor = getIntensityColor(
                        item.change_pct,
                        appConfig.thresholds.sparkline_max_pct_sector,
                        item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad
                    );
                    const textColor = Math.abs(item.change_pct) > appConfig.thresholds.sparkline_max_pct_sector * 0.5 ? '#000' : '#fff';
                    const formatPct = (pct: number | undefined | null) => pct !== undefined && pct !== null ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '-';
                    const formatNum = (num: number | undefined | null, dec: number) => num !== undefined && num !== null ? num.toFixed(dec) : '-';
                    const colorNeutral = (pct: number | undefined | null) => pct !== undefined && pct !== null ? (pct > 0 ? appConfig.colors.good : pct < 0 ? appConfig.colors.bad : '#fff') : '#888';

                    return (
                        <div key={item.id} className="dashboard-item" style={{
                            display: 'flex', alignItems: 'center', padding: '6px 0',
                            borderBottom: `1px solid ${appConfig.colors.glassBorder}`, gap: '8px',
                        }}>
                            <div style={{ width: '24px', display: 'flex', alignItems: 'center' }}>
                                <WatchlistButton 
                                    isActive={isTickerActive(item.ticker)} 
                                    onClick={(e) => {
                                        e.preventDefault();
                                        toggleWatchlist(item.ticker, targetDate);
                                    }}
                                    size={18}
                                />
                            </div>
                            <div style={{ flex: '1', minWidth: '80px', display: 'flex', flexDirection: 'column' }}>

                                <Link to={`/chart/${encodeURIComponent(item.ticker)}`} target="_blank" style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>
                                    {item.ticker}
                                </Link>
                                <span style={{ fontSize: '10px', color: '#666', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{item.category ? `[${item.category}] ` : ''}{item.name}</span>
                            </div>
                            <div style={{ width: '52px', textAlign: 'right', paddingRight: '5px', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>{item.close.toFixed(2)}</div>
                            <div style={{ width: '50px', textAlign: 'center', padding: '3px', borderRadius: '4px', backgroundColor: bgColor, color: textColor, fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>{formatPct(item.change_pct)}</div>
                            <div style={{ width: '50px', textAlign: 'right', paddingRight: '5px', color: colorNeutral(item.dist_21ema_pct), fontSize: '11px', fontWeight: '600', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>{formatPct(item.dist_21ema_pct)}</div>
                            <div style={{ width: '50px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', fontWeight: item.vol_surge_21 && item.vol_surge_21 > 1.5 ? 'bold' : 'normal', color: item.vol_surge_21 && item.vol_surge_21 > 2.0 ? appConfig.colors.accent : '#fff', flexShrink: 0 }}>{formatNum(item.vol_surge_21, 1)}x</div>
                            <div style={{ width: '36px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: '#ccc', flexShrink: 0 }}>{formatNum(item.adr_pct_21, 1)}</div>
                            <div style={{ width: '36px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: item.up_down_vol_ratio_50 && item.up_down_vol_ratio_50 > 1.5 ? appConfig.colors.good : '#ccc', fontWeight: item.up_down_vol_ratio_50 && item.up_down_vol_ratio_50 > 1.5 ? 'bold' : 'normal', flexShrink: 0 }}>{formatNum(item.up_down_vol_ratio_50, 1)}</div>
                            <div style={{ width: '36px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: item.vcr && item.vcr < 0.5 ? appConfig.colors.accent : '#ccc', fontWeight: item.vcr && item.vcr < 0.5 ? 'bold' : 'normal', flexShrink: 0 }}>{formatNum(item.vcr, 2)}</div>
                            <div style={{ width: '24px', textAlign: 'center', flexShrink: 0, display: 'flex', justifyContent: 'center', gap: '2px' }}>
                                {item.is_rs_blue_dot === 1 && <span style={{ color: '#00d0ff', fontSize: '14px', lineHeight: 1 }}>●</span>}
                                {item.is_rs_red_dot === 1 && <span style={{ color: '#ff4444', fontSize: '14px', lineHeight: 1 }}>●</span>}
                            </div>
                             <div style={{ width: '40px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', fontWeight: item.sma50_atr_mult && Math.abs(item.sma50_atr_mult) > 2.0 ? 'bold' : 'normal', color: item.sma50_atr_mult && item.sma50_atr_mult > 2.0 ? appConfig.colors.good : item.sma50_atr_mult && item.sma50_atr_mult < -2.0 ? appConfig.colors.bad : '#ccc', flexShrink: 0 }}>{formatNum(item.sma50_atr_mult, 1)}</div>
                            <div style={{ width: '60px', flexShrink: 0 }}>
                                <Sparkline data={item.sparkline} width={60} height={22} color={item.change_1m_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad} fixedRange={true} />
                            </div>
                            <div style={{ width: '32px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: item.rs_ratio_rank_e21 >= 0.7 ? appConfig.colors.good : item.rs_ratio_rank_e21 <= 0.3 ? appConfig.colors.bad : '#aaa', fontWeight: '600', flexShrink: 0 }}>{(item.rs_ratio_rank_e21 * 100).toFixed(0)}</div>
                            <div style={{ width: '32px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: item.rs_ratio_rank_e63 >= 0.7 ? appConfig.colors.good : item.rs_ratio_rank_e63 <= 0.3 ? appConfig.colors.bad : '#aaa', fontWeight: '600', flexShrink: 0 }}>{(item.rs_ratio_rank_e63 * 100).toFixed(0)}</div>
                        </div>
                    );
                })}
            </div>
        );
    };

    const grouped = groupedColumns();

    return (
        <div style={{ padding: '20px', maxWidth: '1200px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '20px', height: 'calc(100vh - 40px)' }}>
            
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
                    <Link to={`/screener?target_date=${targetDate}`} style={{ color: '#aaa', textDecoration: 'none', fontSize: '14px', display: 'flex', alignItems: 'center' }}>
                        <span style={{ marginRight: '5px' }}>←</span> Back to Dashboard
                    </Link>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <label style={{ fontSize: '13px', color: '#888' }}>Base Date:</label>
                    <button 
                        onClick={() => shiftDate(-1)} 
                        style={{ background: 'transparent', border: 'none', color: !availableDates.some(d => d < targetDate) ? '#444' : '#aaa', cursor: 'pointer', fontSize: '16px', padding: '0 5px' }}
                        disabled={!availableDates.some(d => d < targetDate)}
                    >
                        ◀
                    </button>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(255,255,255,0.05)', padding: '4px 8px', borderRadius: '4px', border: `1px solid rgba(255,255,255,0.1)` }}>
                        <input
                            type="date"
                            value={targetDate}
                            onChange={e => updateDateURL(e.target.value)}
                            style={{
                                background: 'transparent',
                                border: 'none',
                                color: '#fff',
                                fontSize: '12px',
                                outline: 'none',
                                colorScheme: 'dark',
                                cursor: 'pointer'
                            }}
                        />
                        <span style={{ fontSize: '12px', color: appConfig.colors.accent, fontWeight: 'bold', minWidth: '32px' }}>
                            ({targetDate ? new Date(targetDate).toLocaleDateString('en-US', { weekday: 'short' }) : '---'})
                        </span>
                    </div>
                    <button 
                        onClick={() => shiftDate(1)} 
                        style={{ background: 'transparent', border: 'none', color: !availableDates.some(d => d > targetDate) ? '#444' : '#aaa', cursor: 'pointer', fontSize: '16px', padding: '0 5px' }}
                        disabled={!availableDates.some(d => d > targetDate)}
                    >
                        ▶
                    </button>
                </div>
            </div>

            {/* Backdrop for Mobile Drawer */}
            <div className={`screener-filter-backdrop ${isFilterOpen ? 'open' : ''}`} onClick={() => setIsFilterOpen(false)} />

            <div style={{ display: 'flex', gap: '20px', flex: 1, minHeight: 0 }}>
                {/* Left Panel: Dynamic Filters */}
                <div className={`screener-filter-sidebar glass-panel ${isFilterOpen ? 'open' : ''}`} style={{ width: '270px', padding: '15px', display: 'flex', flexDirection: 'column', flexShrink: 0, overflowY: 'auto' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '0 0 15px 0', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '10px' }}>
                        <h3 style={{ fontSize: '14px', margin: 0 }}>
                            Custom Filters
                        </h3>
                        <button 
                            className="mobile-filter-close-btn"
                            onClick={() => setIsFilterOpen(false)}
                            style={{
                                display: 'none',
                                background: 'transparent',
                                border: 'none',
                                color: '#aaa',
                                fontSize: '16px',
                                cursor: 'pointer',
                                padding: '4px'
                            }}
                        >
                            ✕
                        </button>
                    </div>
                    
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                        {Object.entries(grouped).map(([categoryName, cols]) => (
                            <div key={categoryName}>
                                <div style={{ fontSize: '11px', color: appConfig.colors.accent, textTransform: 'uppercase', marginBottom: '8px', letterSpacing: '0.5px' }}>
                                    {categoryName}
                                </div>
                                <div>
                                    {cols.map(col => (
                                        <DynFilterRow key={col.name} col={col} searchParams={queryParams} onChange={handleFilterChange} />
                                    ))}
                                </div>
                            </div>
                        ))}

                        {/* Boolean Filters */}
                        <div>
                            <div style={{ fontSize: '11px', color: appConfig.colors.accent, textTransform: 'uppercase', marginBottom: '8px', letterSpacing: '0.5px' }}>
                                Special Conditions
                            </div>
                            {BOOLEAN_FILTERS.map(bf => {
                                const dynamicLabel = meta?.labels?.[bf.param] || bf.label;
                                return <BoolFilterRow key={bf.id} label={dynamicLabel} param={bf.param} searchParams={queryParams} onChange={handleFilterChange} />;
                            })}
                        </div>
                    </div>
                </div>

                {/* Right Panel: Results */}
                <div className="glass-panel" style={{ flex: 1, padding: '20px', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', flexWrap: 'wrap', gap: '10px' }}>
                        <div>
                            <h2 style={{ margin: 0, fontSize: '20px' }}>
                                {presetId ? `Screener: ${presetId}` : 'Custom Screener'}
                            </h2>
                            <p style={{ margin: '5px 0 0 0', color: '#888', fontSize: '12px' }} className="pc-only-text">Additional criteria can be added from the left panel.</p>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <button
                                className="mobile-filter-toggle-btn"
                                onClick={() => setIsFilterOpen(true)}
                                style={{
                                    display: 'none',
                                    alignItems: 'center',
                                    gap: '6px',
                                    padding: '6px 12px',
                                    fontSize: '12px',
                                    fontWeight: 'bold',
                                    background: 'rgba(59, 130, 246, 0.15)',
                                    border: `1px solid ${appConfig.colors.accent}`,
                                    borderRadius: '6px',
                                    color: appConfig.colors.accent,
                                    cursor: 'pointer',
                                }}
                            >
                                🔍 フィルター ({results.length})
                            </button>
                            <div style={{ fontSize: '14px', color: '#888', background: 'rgba(255,255,255,0.05)', padding: '4px 10px', borderRadius: '4px' }}>
                                {results.length} results
                            </div>
                        </div>
                    </div>
                    
                    <div style={{ flex: 1, overflowY: 'auto', paddingRight: '10px' }}>
                        {renderResults()}
                    </div>
                </div>
            </div>
        </div>
    );
};
