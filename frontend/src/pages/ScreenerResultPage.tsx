import React, { useState, useEffect } from 'react';
import { Link, useParams, useLocation, useNavigate } from 'react-router-dom';
import { ScreenerResultItem } from '../types';
import { Sparkline } from '../components/Sparkline';
import { appConfig, getIntensityColor } from '../config';

type SortKey = 'change_pct' | 'rs_ratio_21_rank' | 'vol_surge_21';

// Base Presets definition (Only for presets utilizing OR conditions or unsupported params)
const BASE_PRESETS = [
    { id: 'climax_top', label: 'Climax Top / Overextended', sub: 'dist_sma50_atr > 3 or TD9 = -9' },
    { id: 'trend_breakdown', label: 'Trend Breakdown', sub: 'RS63 < -1.0, 50SMA < 200SMA' },
    { id: 'oversold_rebound', label: 'Oversold Rebound', sub: 'TD9 = 9, RS14 < 0.2' },
];

const PRESET_LABELS: Record<string, string> = {
    check_1d_gain: '1D% Gain',
    check_volume_surge: 'Volume Surge',
    check_21ema: '21EMA Pullback',
    check_momentum97: 'Momentum 97',
    check_vcp: 'VCP',
    td9_overhead: 'TDR9 Overhead',
    td9_rebound: 'TDR9 Rebound',
    dist_sma50_atr_8: 'SMA50/ATR% > 8',
    high_vol_dist: 'High Vol Distribution',
    rrg_leading_in: 'RRG Leading In',
    rrg_lagging_in: 'RRG Lagging In'
};


interface FilterDef {
    id: string;
    label: string;
    type: 'range' | 'boolean' | 'select';
    paramMin?: string;
    paramMax?: string;
    paramBool?: string;
    paramSelect?: string;
    options?: { label: string; value: string }[];
    step?: number;
}

const FILTER_CATEGORIES: { title: string, filters: FilterDef[] }[] = [
    {
        title: "Price & Trend",
        filters: [
            { id: '1d_gain', label: '1D% Gain', type: 'range', paramMin: 'min_1d_gain_pct', paramMax: 'max_1d_gain_pct', step: 0.1 },
            { id: '21ema_dist', label: '21EMA Pullback %', type: 'range', paramMin: 'min_dist_21ema_pct', paramMax: 'max_dist_21ema_pct', step: 0.1 },
            { id: 'sma50_dist', label: 'SMA50乖離率 %', type: 'range', paramMin: 'min_dist_sma50_pct', paramMax: 'max_dist_sma50_pct', step: 0.1 },
            { id: 'trend_template', label: 'Trend Template', type: 'boolean', paramBool: 'trend_template_ok' }
        ]
    },
    {
        title: "Volume & Volatility",
        filters: [
            { id: 'vol_surge', label: 'Vol Surge x', type: 'range', paramMin: 'min_vol_surge_21', paramMax: 'max_vol_surge_21', step: 0.1 },
            { id: 'rel_vol', label: 'Relative Vol x', type: 'range', paramMin: 'min_rel_vol', paramMax: 'max_rel_vol', step: 0.1 },
            { id: 'adr', label: 'ADR %', type: 'range', paramMin: 'min_adr_pct_21', paramMax: 'max_adr_pct_21', step: 0.1 },
            { id: 'sma50_atr', label: '50SMA/ATR %', type: 'range', paramMin: 'min_dist_sma50_atr', paramMax: 'max_dist_sma50_atr', step: 1 }
        ]
    },
    {
        title: "Momentum & Indicators",
        filters: [
            { id: 'rs21_rank', label: 'RS21 Rank', type: 'range', paramMin: 'min_rs_ratio_21_rank', paramMax: 'max_rs_ratio_21_rank', step: 0.01 },
            { id: 'rs21_gt_63', label: 'RS21rank > RS63rank', type: 'boolean', paramBool: 'rs_rank_21_gt_63' },
            { id: 'theme_rs21_gt_63', label: 'Theme RS21 > RS63', type: 'boolean', paramBool: 'theme_rs21_gt_63' },
            { id: 'rrg_leading_in', label: 'RRG Leading In', type: 'boolean', paramBool: 'rrg_leading_in' },
            { id: 'rrg_lagging_in', label: 'RRG Lagging In', type: 'boolean', paramBool: 'rrg_lagging_in' },
            { id: 'rs_cond', label: 'RS Cond 21', type: 'range', paramMin: 'min_rs_condition_21', paramMax: 'max_rs_condition_21', step: 0.1 },
            { id: 'td9', label: 'TDR9 (Sequential)', type: 'range', paramMin: 'min_td9', paramMax: 'max_td9', step: 1 }
        ]
    },
    {
        title: "Fundamentals",
        filters: [
            { id: 'market_cap', label: 'Market Cap', type: 'select', paramSelect: 'min_market_cap', options: [
                { label: 'Any', value: '' },
                { label: '>= 10M', value: '10000000' },
                { label: '>= 100M', value: '100000000' },
                { label: '>= 300M', value: '300000000' },
                { label: '>= 1B', value: '1000000000' },
                { label: '>= 10B', value: '10000000000' },
            ]}
        ]
    }
];

const FilterRow: React.FC<{
    def: FilterDef;
    searchParams: URLSearchParams;
    onChange: (keysToSet: Record<string, string>, keysToRemove: string[]) => void;
}> = ({ def, searchParams, onChange }) => {
    
    // Determine if active from URL
    const isActive = def.type === 'range' ? 
        (searchParams.has(def.paramMin!) || searchParams.has(def.paramMax!)) :
        def.type === 'boolean' ? searchParams.has(def.paramBool!) :
        searchParams.has(def.paramSelect!);

    const [checked, setChecked] = useState(isActive);
    const [minVal, setMinVal] = useState(def.paramMin ? searchParams.get(def.paramMin) || '' : '');
    const [maxVal, setMaxVal] = useState(def.paramMax ? searchParams.get(def.paramMax) || '' : '');
    const [selVal, setSelVal] = useState(def.paramSelect ? searchParams.get(def.paramSelect) || '' : '');

    // Sync state if URL changes externally (e.g. browser back/forward)
    useEffect(() => {
        setChecked(isActive);
        if (def.paramMin) setMinVal(searchParams.get(def.paramMin) || '');
        if (def.paramMax) setMaxVal(searchParams.get(def.paramMax) || '');
        if (def.paramSelect) setSelVal(searchParams.get(def.paramSelect) || '');
    }, [searchParams.toString()]);

    // Debounce effect for Range Inputs
    useEffect(() => {
        if (checked && def.type === 'range') {
            const timer = setTimeout(() => {
                const toSet: Record<string, string> = {};
                const toRemove: string[] = [];
                if (minVal) toSet[def.paramMin!] = minVal; else toRemove.push(def.paramMin!);
                if (maxVal) toSet[def.paramMax!] = maxVal; else toRemove.push(def.paramMax!);
                
                // Only trigger if changes exist against URL
                const currentMin = searchParams.get(def.paramMin!) || '';
                const currentMax = searchParams.get(def.paramMax!) || '';
                if (currentMin !== minVal || currentMax !== maxVal) {
                    onChange(toSet, toRemove);
                }
            }, 500);
            return () => clearTimeout(timer);
        }
    }, [minVal, maxVal]); // only run when typing

    const handleCheck = (c: boolean) => {
        setChecked(c);
        if (c) {
            if (def.type === 'boolean') {
                onChange({ [def.paramBool!]: '1' }, []);
            } else if (def.type === 'select' && selVal) {
                onChange({ [def.paramSelect!]: selVal }, []);
            }
        } else {
            // Uncheck action -> Remove all relevant keys
            const toRemove = [];
            if (def.paramMin) toRemove.push(def.paramMin);
            if (def.paramMax) toRemove.push(def.paramMax);
            if (def.paramBool) toRemove.push(def.paramBool);
            if (def.paramSelect) toRemove.push(def.paramSelect);
            onChange({}, toRemove);
            
            // Optionally clear local state
            setMinVal('');
            setMaxVal('');
        }
    };

    const handleSelectChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
        const val = e.target.value;
        setSelVal(val);
        if (val) onChange({ [def.paramSelect!]: val }, []);
        else onChange({}, [def.paramSelect!]);
    };

    const inputStyle = { 
        width: '45px', 
        padding: '2px 4px', 
        fontSize: '11px', 
        background: '#1a1d26', 
        border: '1px solid rgba(255,255,255,0.2)', 
        color: '#fff', 
        borderRadius: '3px', 
        outline: 'none', 
        colorScheme: 'dark' 
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', padding: '4px 0' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', cursor: 'pointer', color: checked ? '#fff' : '#aaa' }}>
                <input type="checkbox" checked={checked} onChange={e => handleCheck(e.target.checked)} style={{ accentColor: appConfig.colors.accent, outline: 'none' }} />
                {def.label}
            </label>
            {checked && def.type === 'range' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px', paddingLeft: '22px' }}>
                    <input type="number" step={def.step || 1} value={minVal} onChange={e => setMinVal(e.target.value)} placeholder="Min" style={inputStyle} />
                    <span style={{ fontSize: '11px', color: '#666' }}>〜</span>
                    <input type="number" step={def.step || 1} value={maxVal} onChange={e => setMaxVal(e.target.value)} placeholder="Max" style={inputStyle} />
                </div>
            )}
            {checked && def.type === 'select' && def.options && (
                <div style={{ paddingLeft: '22px' }}>
                    <select value={selVal} onChange={handleSelectChange} style={{ ...inputStyle, width: '90px' }}>
                        {def.options.map(o => <option key={o.value} value={o.value} style={{ background: '#1a1d26' }}>{o.label}</option>)}
                    </select>
                </div>
            )}
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

    const [sortKey, setSortKey] = useState<SortKey>('change_pct');
    const [sortDesc, setSortDesc] = useState(true);

    const basePresetDef = BASE_PRESETS.find(p => p.id === presetId);
    
    const DisplayTitle = basePresetDef ? `Base Filter: ${basePresetDef.label}` : 
                         presetId ? `Custom Screener (${PRESET_LABELS[presetId] || presetId})` : 
                         'Custom Screener';

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

    const shiftDate = (days: number) => {
        if (!targetDate) return;
        const d = new Date(targetDate);
        d.setDate(d.getDate() + days);
        updateDateURL(d.toISOString().split('T')[0]);
    };

    useEffect(() => {
        // If it's just the root /screener/result without a preset ID, that's fine too.
        setLoading(true);
        setError('');

        const params = new URLSearchParams(location.search);
        // Apply Base Filter preset param if it matches a predefined OR filter
        if (basePresetDef && !params.has('preset')) {
            params.set('preset', basePresetDef.id);
        }

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
                    <div style={{ flex: '1', minWidth: '80px' }}>Name</div>
                    <div style={{ width: '52px', textAlign: 'right', paddingRight: '5px' }}>Close</div>
                    <div 
                        style={{ width: '50px', textAlign: 'center', cursor: 'pointer', color: sortKey === 'change_pct' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('change_pct')}
                        title="Sort by %1D"
                    >
                        %1D {sortKey === 'change_pct' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '50px', textAlign: 'right', paddingRight: '5px' }}>21E%</div>
                    
                    <div 
                        style={{ width: '50px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'vol_surge_21' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('vol_surge_21')}
                        title="Sort by Volume Surge"
                    >
                        Vol SG {sortKey === 'vol_surge_21' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>

                    <div style={{ width: '36px', textAlign: 'right' }}>ADR%</div>
                    <div style={{ width: '40px', textAlign: 'right' }}>50dATR</div>
                    <div style={{ width: '60px', textAlign: 'center' }}>Trend</div>
                    
                    <div 
                        style={{ width: '32px', textAlign: 'right', cursor: 'pointer', color: sortKey === 'rs_ratio_21_rank' ? '#fff' : '#aaa' }}
                        onClick={() => handleSort('rs_ratio_21_rank')}
                        title="Sort by RS21 Rank"
                    >
                        RS21 {sortKey === 'rs_ratio_21_rank' ? (sortDesc ? '▼' : '▲') : ''}
                    </div>
                    <div style={{ width: '32px', textAlign: 'right' }}>RS63</div>
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
                            display: 'flex',
                            alignItems: 'center',
                            padding: '6px 0',
                            borderBottom: `1px solid ${appConfig.colors.glassBorder}`,
                            gap: '8px',
                        }}>
                            {/* 1. Name */}
                            <div style={{ flex: '1', minWidth: '80px', display: 'flex', flexDirection: 'column' }}>
                                <Link to={`/chart/${encodeURIComponent(item.ticker)}`} target="_blank" style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>
                                    {item.ticker}
                                </Link>
                                <span style={{ fontSize: '10px', color: '#666', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{item.category ? `[${item.category}] ` : ''}{item.name}</span>
                            </div>

                            {/* 2. Close */}
                            <div style={{ width: '52px', textAlign: 'right', paddingRight: '5px', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>
                                {item.close.toFixed(2)}
                            </div>

                            {/* 3. %1D */}
                            <div style={{
                                width: '50px',
                                textAlign: 'center',
                                padding: '3px',
                                borderRadius: '4px',
                                backgroundColor: bgColor,
                                color: textColor,
                                fontWeight: '600',
                                fontSize: '11px',
                                flexShrink: 0,
                            }}>
                                {formatPct(item.change_pct)}
                            </div>

                            {/* 6. Dist 21EMA */}
                            <div style={{ width: '50px', textAlign: 'right', paddingRight: '5px', color: colorNeutral(item.dist_21ema_pct), fontSize: '11px', fontWeight: '600', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                                {formatPct(item.dist_21ema_pct)}
                            </div>

                            {/* Vol Surge */}
                            <div style={{ width: '50px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', fontWeight: item.vol_surge_21 && item.vol_surge_21 > 1.5 ? 'bold' : 'normal', color: item.vol_surge_21 && item.vol_surge_21 > 2.0 ? appConfig.colors.accent : '#fff', flexShrink: 0 }}>
                                {formatNum(item.vol_surge_21, 1)}x
                            </div>
                            
                            {/* ADR% */}
                            <div style={{ width: '36px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', color: '#ccc', flexShrink: 0 }}>
                                {formatNum(item.adr_pct_21, 1)}
                            </div>

                            {/* Dist 50SMA ATR */}
                            <div style={{ width: '40px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums', fontWeight: item.dist_sma50_atr && Math.abs(item.dist_sma50_atr) > 2.0 ? 'bold' : 'normal', color: item.dist_sma50_atr && item.dist_sma50_atr > 2.0 ? appConfig.colors.good : item.dist_sma50_atr && item.dist_sma50_atr < -2.0 ? appConfig.colors.bad : '#ccc', flexShrink: 0 }}>
                                {formatNum(item.dist_sma50_atr, 1)}
                            </div>

                            {/* 7. Sparkline */}
                            <div style={{ width: '60px', flexShrink: 0 }}>
                                <Sparkline
                                    data={item.sparkline}
                                    width={60}
                                    height={22}
                                    color={item.change_1m_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad}
                                    fixedRange={true}
                                />
                            </div>

                            {/* 8. RS Score (21, 63) */}
                            <div style={{
                                width: '32px',
                                textAlign: 'right',
                                fontSize: '11px',
                                fontVariantNumeric: 'tabular-nums',
                                color: item.rs_ratio_21_rank >= 0.7 ? appConfig.colors.good :
                                    item.rs_ratio_21_rank <= 0.3 ? appConfig.colors.bad : '#aaa',
                                fontWeight: '600',
                                flexShrink: 0,
                            }}>
                                {(item.rs_ratio_21_rank * 100).toFixed(0)}
                            </div>
                            <div style={{
                                width: '32px',
                                textAlign: 'right',
                                fontSize: '11px',
                                fontVariantNumeric: 'tabular-nums',
                                color: item.rs_ratio_63_rank >= 0.7 ? appConfig.colors.good :
                                    item.rs_ratio_63_rank <= 0.3 ? appConfig.colors.bad : '#aaa',
                                fontWeight: '600',
                                flexShrink: 0,
                            }}>
                                {(item.rs_ratio_63_rank * 100).toFixed(0)}
                            </div>
                        </div>
                    );
                })}
            </div>
        );
    };

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
                    <button onClick={() => shiftDate(-1)} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '16px', padding: '0 5px' }}>◀</button>
                    <input
                        type="date"
                        value={targetDate}
                        onChange={e => updateDateURL(e.target.value)}
                        style={{
                            padding: '4px 8px',
                            borderRadius: '4px',
                            background: 'rgba(255,255,255,0.05)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            color: '#fff',
                            colorScheme: 'dark',
                            fontSize: '12px'
                        }}
                    />
                    <button onClick={() => shiftDate(1)} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '16px', padding: '0 5px' }}>▶</button>
                </div>
            </div>

            <div style={{ display: 'flex', gap: '20px', flex: 1, minHeight: 0 }}>
                {/* Left Panel: Checkboxes & Ranges */}
                <div className="glass-panel" style={{ width: '270px', padding: '15px', display: 'flex', flexDirection: 'column', flexShrink: 0, overflowY: 'auto' }}>
                    <h3 style={{ fontSize: '14px', margin: '0 0 15px 0', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '10px' }}>
                        Custom Filters
                    </h3>
                    
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                        {FILTER_CATEGORIES.map((category, idx) => (
                            <div key={idx}>
                                <div style={{ fontSize: '11px', color: appConfig.colors.accent, textTransform: 'uppercase', marginBottom: '8px', letterSpacing: '0.5px' }}>
                                    {category.title}
                                </div>
                                <div>
                                    {category.filters.map(f => (
                                        <FilterRow key={f.id} def={f} searchParams={queryParams} onChange={handleFilterChange} />
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Right Panel: Results */}
                <div className="glass-panel" style={{ flex: 1, padding: '20px', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                        <div>
                            <h2 style={{ margin: 0, fontSize: '20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                                {DisplayTitle}
                                {basePresetDef && <span style={{ fontSize: '11px', padding: '2px 6px', background: 'rgba(255,100,100,0.2)', color: '#ff8888', borderRadius: '4px', fontWeight: 'bold' }}>BASE FILTER ACTIVE</span>}
                            </h2>
                            {basePresetDef && <p style={{ margin: '5px 0 0 0', color: '#aaa', fontSize: '12px' }}>{basePresetDef.sub}</p>}
                            {!basePresetDef && presetId && <p style={{ margin: '5px 0 0 0', color: '#888', fontSize: '12px' }}>Additional criteria can be added from the left panel.</p>}
                        </div>
                        <div style={{ fontSize: '14px', color: '#888', background: 'rgba(255,255,255,0.05)', padding: '4px 10px', borderRadius: '4px' }}>
                            {results.length} results
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
