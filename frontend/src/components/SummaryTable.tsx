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
}

type SortKey = 'ticker' | 'close' | 'change_pct' | 'change_1w_pct' | 'change_1m_pct' | 'dist_21ema_pct' | 'rs_ratio_21_rank' | 'rs_ratio_63_rank';
type SortDirection = 'asc' | 'desc';

export const SummaryTable: React.FC<SummaryTableProps> = ({ 
    items, 
    maxPct, 
    linkTo,
    defaultSortKey = 'change_pct',
    defaultSortDirection = 'desc'
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
            style={{ ...style, cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', justifyContent: style.textAlign === 'right' ? 'flex-end' : (style.textAlign === 'center' ? 'center' : 'flex-start') }}
        >
            {label} <SortIcon column={column} />
        </div>
    );

    return (
        <div className="dashboard-list">
            {/* Header Row */}
            <div style={{
                display: 'flex', fontSize: '11px', color: '#aaa', paddingBottom: '8px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, marginBottom: '8px',
                gap: '8px'
            }}>
                <HeaderItem label="Name" column="ticker" style={{ flex: '1', minWidth: '80px' }} />
                <HeaderItem label="Close" column="close" style={{ width: '52px', textAlign: 'right', paddingRight: '5px' }} />
                <HeaderItem label="1D%" column="change_pct" style={{ width: '50px', textAlign: 'center' }} />
                <HeaderItem label="1W%" column="change_1w_pct" style={{ width: '50px', textAlign: 'center' }} />
                <HeaderItem label="1M%" column="change_1m_pct" style={{ width: '50px', textAlign: 'center' }} />
                <HeaderItem label="21E%" column="dist_21ema_pct" style={{ width: '50px', textAlign: 'right', paddingRight: '5px' }} />
                <div style={{ width: '60px', textAlign: 'center' }}>Trend</div>
                <HeaderItem label="RS21" column="rs_ratio_21_rank" style={{ width: '32px', textAlign: 'right' }} />
                <HeaderItem label="RS63" column="rs_ratio_63_rank" style={{ width: '32px', textAlign: 'right' }} />
            </div>

            {sortedItems.map(item => {
                const bgColor = getIntensityColor(
                    item.change_pct,
                    maxPct,
                    item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad
                );
                const textColor = Math.abs(item.change_pct) > maxPct * 0.5 ? '#000' : '#fff';

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
                            <Link to={linkTo ? linkTo(item) : `/chart/${encodeURIComponent(item.ticker)}`} style={{ color: appConfig.colors.chartText, textDecoration: 'none', fontWeight: 'bold', fontSize: '13px' }}>
                                {item.ticker}
                            </Link>
                            <span style={{ fontSize: '10px', color: '#666', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>{item.name}</span>
                        </div>

                        {/* 2. Close */}
                        <div style={{ width: '52px', textAlign: 'right', paddingRight: '5px', fontSize: '12px', fontVariantNumeric: 'tabular-nums' }}>
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
                                height={22}
                                color={item.change_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad}
                                fixedRange={true}
                            />
                        </div>

                        {/* 8. RS Score (21, 63) */}
                        <div style={{
                            width: '32px',
                            textAlign: 'right',
                            fontSize: '11px',
                            fontVariantNumeric: 'tabular-nums',
                            color: (item.rs_ratio_21_rank || 0) >= 0.7 ? appConfig.colors.good :
                                (item.rs_ratio_21_rank || 0) <= 0.3 ? appConfig.colors.bad : '#aaa',
                            fontWeight: '600',
                            flexShrink: 0,
                        }}>
                            {((item.rs_ratio_21_rank || 0) * 100).toFixed(0)}
                        </div>
                        <div style={{
                            width: '32px',
                            textAlign: 'right',
                            fontSize: '11px',
                            fontVariantNumeric: 'tabular-nums',
                            color: (item.rs_ratio_63_rank || 0) >= 0.7 ? appConfig.colors.good :
                                (item.rs_ratio_63_rank || 0) <= 0.3 ? appConfig.colors.bad : '#aaa',
                            fontWeight: '600',
                            flexShrink: 0,
                        }}>
                            {((item.rs_ratio_63_rank || 0) * 100).toFixed(0)}
                        </div>
                    </div>
                );
            })}
        </div>
    );
};
