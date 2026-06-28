import React from 'react';
import { Link } from 'react-router-dom';
import { DashboardPanelItem } from '../types';
import { Sparkline } from './Sparkline';
import { appConfig, getIntensityColor } from '../config';

interface SummaryTableProps {
    items: DashboardPanelItem[];
    maxPct: number;
    linkTo?: (item: DashboardPanelItem) => string;
    defaultSortKey?: SortKey;
    defaultSortDirection?: SortDirection;
    rankType?: 'rs_trend' | 'rs_ratio';
}

type SortKey = 'ticker' | 'close' | 'change_pct' | 'change_1w_pct' | 'change_1m_pct' | 'dist_21ema_pct' | 'rs_ratio_rank_e14' | 'rs_ratio_rank_e21' | 'rs_ratio_rank_e63' | 'rs_trend_rank_s14' | 'rs_trend_rank_s21' | 'rs_trend_rank_s63';
type SortDirection = 'asc' | 'desc';

export const SummaryTable: React.FC<SummaryTableProps> = ({ 
    items, 
    maxPct, 
    linkTo,
    defaultSortKey = 'change_pct',
    defaultSortDirection = 'desc',
    rankType = 'rs_trend'
}) => {
    const [sortConfig, setSortConfig] = React.useState<{ key: SortKey, direction: SortDirection }>({
        key: defaultSortKey,
        direction: defaultSortDirection
    });

    // Synchronize state if default props change (important for tab switching or re-mounts)
    React.useEffect(() => {
        setSortConfig({
            key: defaultSortKey,
            direction: defaultSortDirection
        });
    }, [defaultSortKey, defaultSortDirection]);

    const handleSort = (key: SortKey) => {
        setSortConfig(prev => ({
            key,
            direction: prev.key === key && prev.direction === 'desc' ? 'asc' : 'desc'
        }));
    };

    const sortedItems = React.useMemo(() => {
        return [...items].sort((a, b) => {
            const valA = a[sortConfig.key] ?? 0;
            const valB = b[sortConfig.key] ?? 0;

            if (typeof valA === 'string' && typeof valB === 'string') {
                return sortConfig.direction === 'asc' 
                    ? valA.localeCompare(valB) 
                    : valB.localeCompare(valA);
            }

            return sortConfig.direction === 'asc' 
                ? (valA as number) - (valB as number) 
                : (valB as number) - (valA as number);
        });
    }, [items, sortConfig]);

    if (items.length === 0) return <div style={{ color: '#888' }}>データなし</div>;

    const formatPct = (pct: number | undefined | null) => {
        if (pct === undefined || pct === null) return '0.0%';
        return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
    };
    const colorNeutral = (pct: number | undefined | null) => {
        if (!pct) return '#fff';
        return pct > 0 ? appConfig.colors.good : pct < 0 ? appConfig.colors.bad : '#fff';
    };

    const SortIcon = ({ column }: { column: SortKey }) => {
        if (sortConfig.key !== column) return <span style={{ opacity: 0.1, marginLeft: '2px' }}>▼</span>;
        return <span style={{ marginLeft: '2px', color: appConfig.colors.star }}>{sortConfig.direction === 'desc' ? '▼' : '▲'}</span>;
    };

    const HeaderItem = ({ label, column, style }: { label: string, column: SortKey, style: React.CSSProperties }) => (
        <div 
            onClick={() => handleSort(column)} 
            style={{ ...style, flexShrink: 0, cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', justifyContent: style.textAlign === 'right' ? 'flex-end' : (style.textAlign === 'center' ? 'center' : 'flex-start') }}
        >
            {label} <SortIcon column={column} />
        </div>
    );

    const isTrend = rankType === 'rs_trend';
    const r14Key: SortKey = isTrend ? 'rs_trend_rank_s14' : 'rs_ratio_rank_e14';
    const r21Key: SortKey = isTrend ? 'rs_trend_rank_s21' : 'rs_ratio_rank_e21';
    const r63Key: SortKey = isTrend ? 'rs_trend_rank_s63' : 'rs_ratio_rank_e63';
    const r14Label = isTrend ? 'RST14%' : 'RSR14%';
    const r21Label = isTrend ? 'RST21%' : 'RSR21%';
    const r63Label = isTrend ? 'RST63%' : 'RSR63%';
    const miniMapLabel = isTrend ? 'RST21% (30d)' : 'RSR21% (30d)';

    return (
        <div className="dashboard-list" style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', width: '100%', maxWidth: '100%' }}>
            <div style={{ minWidth: '680px' }}>
                {/* Header Row */}
                <div style={{
                    display: 'flex', fontSize: '11px', color: '#aaa', paddingBottom: '8px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '8px',
                    gap: '8px'
                }}>
                    <HeaderItem 
                        label="Name" 
                        column="ticker" 
                        style={{ 
                            flex: '1', 
                            minWidth: '80px', 
                            position: 'sticky', 
                            left: 0, 
                            background: '#0d1117', // Match --bg-surface background color
                            zIndex: 2,
                            paddingLeft: '4px',
                            borderRight: '1px solid rgba(255, 255, 255, 0.1)'
                        }} 
                    />
                    <HeaderItem label="Close" column="close" style={{ width: '52px', textAlign: 'right', paddingRight: '5px' }} />
                    <HeaderItem label="1D%" column="change_pct" style={{ width: '50px', textAlign: 'center' }} />
                    <HeaderItem label="1W%" column="change_1w_pct" style={{ width: '50px', textAlign: 'center' }} />
                    <HeaderItem label="1M%" column="change_1m_pct" style={{ width: '50px', textAlign: 'center' }} />
                    <HeaderItem label="E21%" column="dist_21ema_pct" style={{ width: '50px', textAlign: 'right', paddingRight: '5px' }} />
                    <div style={{ width: '60px', textAlign: 'center', flexShrink: 0 }}>{miniMapLabel}</div>
                    <HeaderItem label={r14Label} column={r14Key} style={{ width: '56px', textAlign: 'right' }} />
                    <HeaderItem label={r21Label} column={r21Key} style={{ width: '56px', textAlign: 'right' }} />
                    <HeaderItem label={r63Label} column={r63Key} style={{ width: '56px', textAlign: 'right' }} />
                </div>

                {sortedItems.map(item => {
                    const bgColor = getIntensityColor(
                        item.change_pct,
                        maxPct,
                        item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad
                    );
                    const textColor = Math.abs(item.change_pct) > maxPct * 0.5 ? '#000' : '#fff';

                    const val14 = item[r14Key] ?? 0;
                    const val21 = item[r21Key] ?? 0;
                    const val63 = item[r63Key] ?? 0;

                    return (
                        <div key={item.id} className="dashboard-item" style={{
                            display: 'flex',
                            alignItems: 'center',
                            padding: '6px 0',
                            borderBottom: `1px solid ${appConfig.colors.glassBorder}`,
                            gap: '8px',
                        }}>
                            {/* 1. Name */}
                            <div style={{ 
                                flex: '1', 
                                minWidth: '80px', 
                                flexShrink: 0, 
                                display: 'flex', 
                                flexDirection: 'column',
                                position: 'sticky',
                                left: 0,
                                background: '#0d1117', // Match --bg-surface background color
                                zIndex: 1,
                                paddingLeft: '4px',
                                borderRight: '1px solid rgba(255, 255, 255, 0.1)'
                            }}>
                                <Link to={linkTo ? linkTo(item) : `/chart/${encodeURIComponent(item.ticker)}`} style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>
                                    {item.ticker}
                                </Link>
                                {item.name && item.name.includes('::') ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2, marginTop: '2px' }}>
                                        <span style={{ fontSize: '9px', color: '#8b9cc8', fontWeight: 600, textTransform: 'uppercase', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '120px' }}>
                                            {item.name.split('::')[0]}
                                        </span>
                                        <span style={{ fontSize: '11px', color: '#ccc', fontWeight: 'bold', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '120px' }}>
                                            {item.name.split('::')[1]}
                                        </span>
                                    </div>
                                ) : (
                                    <span style={{ fontSize: '10px', color: '#666', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{item.name}</span>
                                )}
                            </div>

                            {/* 2. Close */}
                            <div style={{ width: '52px', flexShrink: 0, textAlign: 'right', paddingRight: '5px', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>
                                {(item.close || 0).toFixed(2)}
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

                            {/* 4. %1W */}
                            <div style={{ width: '50px', textAlign: 'center', padding: '3px', borderRadius: '4px', border: `1px solid ${appConfig.colors.glassBorder}`, color: colorNeutral(item.change_1w_pct), fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>
                                {formatPct(item.change_1w_pct)}
                            </div>

                            {/* 5. %1M */}
                            <div style={{ width: '50px', textAlign: 'center', padding: '3px', borderRadius: '4px', border: `1px solid ${appConfig.colors.glassBorder}`, color: colorNeutral(item.change_1m_pct), fontWeight: '600', fontSize: '11px', flexShrink: 0 }}>
                                {formatPct(item.change_1m_pct)}
                            </div>

                            {/* 6. Dist 21EMA */}
                            <div style={{ width: '50px', textAlign: 'right', paddingRight: '5px', color: colorNeutral(item.dist_21ema_pct), fontSize: '11px', fontWeight: '600', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                                {formatPct(item.dist_21ema_pct)}
                            </div>

                            {/* 7. Sparkline */}
                            <div style={{ width: '60px', flexShrink: 0 }}>
                                <Sparkline
                                    data={item.sparkline}
                                    width={60}
                                    height={28}
                                    color={item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad}
                                    fixedRange={true}
                                />
                            </div>

                            {/* 8. RS Score (14, 21, 63) */}
                            <div style={{
                                width: '56px',
                                textAlign: 'right',
                                fontSize: '11px',
                                fontVariantNumeric: 'tabular-nums',
                                color: val14 >= 0.7 ? appConfig.colors.good :
                                    val14 <= 0.3 ? appConfig.colors.bad : '#aaa',
                                fontWeight: '600',
                                flexShrink: 0,
                            }}>
                                {(val14 * 100).toFixed(0)}
                            </div>
                            <div style={{
                                width: '56px',
                                textAlign: 'right',
                                fontSize: '11px',
                                fontVariantNumeric: 'tabular-nums',
                                color: val21 >= 0.7 ? appConfig.colors.good :
                                    val21 <= 0.3 ? appConfig.colors.bad : '#aaa',
                                fontWeight: '600',
                                flexShrink: 0,
                            }}>
                                {(val21 * 100).toFixed(0)}
                            </div>
                            <div style={{
                                width: '56px',
                                textAlign: 'right',
                                fontSize: '11px',
                                fontVariantNumeric: 'tabular-nums',
                                color: val63 >= 0.7 ? appConfig.colors.good :
                                    val63 <= 0.3 ? appConfig.colors.bad : '#aaa',
                                fontWeight: '600',
                                flexShrink: 0,
                            }}>
                                {(val63 * 100).toFixed(0)}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
};
