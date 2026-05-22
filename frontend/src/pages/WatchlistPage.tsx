import React, { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { WatchlistItem, WatchlistResponse } from '../types';
import { Sparkline } from '../components/Sparkline';
import { appConfig } from '../config';
import { useWatchlist } from '../hooks/useWatchlist';

type Tab = 'active' | 'removed';
type SortKey = 'entry_date' | 'ticker' | 'gain_pct' | 'max_gain_pct' | 'min_gain_pct' | 'latest_dist_sma50_atr' | 'removed_at' | 'result_pct';
type SortOrder = 'asc' | 'desc';

export const WatchlistPage: React.FC = () => {
    const [tab, setTab] = useState<Tab>('active');
    const [watchlist, setWatchlist] = useState<WatchlistResponse>({ active: [], removed: [] });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selectedTickers, setSelectedTickers] = useState<Set<string>>(new Set());
    
    // Sorting state
    const [sortKey, setSortKey] = useState<SortKey>('entry_date');
    const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

    // Portfolio buy flow state
    const [portfolioList, setPortfolioList] = useState<{id: number; name: string}[]>([]);
    const [buyTarget, setBuyTarget] = useState<{ticker: string; entry_date: string} | null>(null);
    const [buyForm, setBuyForm] = useState({ portfolio_id: 0, shares: 100 });

    const { refreshActiveTickers } = useWatchlist();

    const fetchWatchlist = async () => {
        setLoading(true);
        setError('');
        try {
            const res = await fetch('/api/watchlist');
            if (!res.ok) throw new Error('Failed to fetch watchlist');
            const data: WatchlistResponse = await res.json();
            setWatchlist(data);
            setSelectedTickers(new Set()); // Reset selection on refresh
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchWatchlist();
        // Fetch portfolios for Buy flow
        fetch('/api/portfolio').then(r => r.json()).then(data => {
            if (Array.isArray(data)) {
                setPortfolioList(data);
                if (data.length > 0) setBuyForm(f => ({ ...f, portfolio_id: data[0].id }));
            }
        }).catch(() => {});
    }, []);

    const sortedActive = useMemo(() => {
        const items = [...watchlist.active];
        return items.sort((a, b) => {
            let valA: any = a[sortKey as keyof WatchlistItem] ?? 0;
            let valB: any = b[sortKey as keyof WatchlistItem] ?? 0;
            
            if (valA < valB) return sortOrder === 'asc' ? -1 : 1;
            if (valA > valB) return sortOrder === 'asc' ? 1 : -1;
            return 0;
        });
    }, [watchlist.active, sortKey, sortOrder]);

    const sortedRemoved = useMemo(() => {
        const items = [...watchlist.removed];
        return items.sort((a, b) => {
            let valA: any;
            let valB: any;

            if (sortKey === 'result_pct') {
                valA = a.removed_price ? ((a.removed_price - a.entry_price) / a.entry_price) * 100 : 0;
                valB = b.removed_price ? ((b.removed_price - b.entry_price) / b.entry_price) * 100 : 0;
            } else if (sortKey === 'removed_at') {
                valA = a.removed_at || '';
                valB = b.removed_at || '';
            } else {
                valA = a[sortKey as keyof WatchlistItem] ?? 0;
                valB = b[sortKey as keyof WatchlistItem] ?? 0;
            }
            
            if (valA < valB) return sortOrder === 'asc' ? -1 : 1;
            if (valA > valB) return sortOrder === 'asc' ? 1 : -1;
            return 0;
        });
    }, [watchlist.removed, sortKey, sortOrder]);

    const handleSort = (key: SortKey) => {
        if (sortKey === key) {
            setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortOrder('desc');
        }
    };

    const renderSortIcon = (key: SortKey) => {
        if (sortKey !== key) return <span style={{ marginLeft: '4px', opacity: 0.2 }}>▼</span>;
        return <span style={{ marginLeft: '4px', color: appConfig.colors.star }}>{sortOrder === 'asc' ? '▲' : '▼'}</span>;
    };

    const handleRemove = async (ticker: string) => {
        if (!window.confirm(`Remove ${ticker} from watchlist?`)) return;
        try {
            const res = await fetch(`/api/watchlist/${ticker}`, { method: 'DELETE' });
            if (res.ok) {
                await fetchWatchlist();
                await refreshActiveTickers();
            }
        } catch (err) {
            console.error('Failed to remove item:', err);
        }
    };

    const handleBulkRemove = async () => {
        const count = selectedTickers.size;
        if (!window.confirm(`Remove ${count} selected items from watchlist?`)) return;
        
        try {
            const res = await fetch('/api/watchlist/delete-bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tickers: Array.from(selectedTickers) })
            });
            if (res.ok) {
                await fetchWatchlist();
                await refreshActiveTickers();
            }
        } catch (err) {
            console.error('Failed to bulk remove items:', err);
        }
    };

    const toggleSelect = (ticker: string) => {
        const next = new Set(selectedTickers);
        if (next.has(ticker)) {
            next.delete(ticker);
        } else {
            next.add(ticker);
        }
        setSelectedTickers(next);
    };

    const toggleSelectAll = () => {
        if (selectedTickers.size === watchlist.active.length) {
            setSelectedTickers(new Set());
        } else {
            setSelectedTickers(new Set(watchlist.active.map(i => i.ticker)));
        }
    };

    const handleReactivate = async (ticker: string, entry_date: string) => {
        try {
            const res = await fetch('/api/watchlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker, entry_date })
            });
            if (res.ok) {
                await fetchWatchlist();
                await refreshActiveTickers();
            }
        } catch (err) {
            console.error('Failed to reactivate item:', err);
        }
    };

    const handleUpdateDate = async (ticker: string, newDate: string) => {
        try {
            const res = await fetch(`/api/watchlist/${ticker}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entry_date: newDate })
            });
            if (res.ok) {
                await fetchWatchlist();
            }
        } catch (err) {
            console.error('Failed to update entry date:', err);
        }
    };

    const handleClearRemoved = async () => {
        if (!window.confirm('Clear all removed items permanently?')) return;
        try {
            const res = await fetch('/api/watchlist/removed/clear', { method: 'DELETE' });
            if (res.ok) {
                await fetchWatchlist();
            }
        } catch (err) {
            console.error('Failed to clear items:', err);
        }
    };

    const formatPct = (val: number) => `${val > 0 ? '+' : ''}${val.toFixed(2)}%`;
    const getPctColor = (val: number) => val > 0 ? appConfig.colors.good : val < 0 ? appConfig.colors.bad : '#ccc';

    const handleBuy = async () => {
        if (!buyTarget || !buyForm.portfolio_id) return;
        try {
            const res = await fetch(`/api/portfolio/${buyForm.portfolio_id}/positions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: buyTarget.ticker, entry_date: buyTarget.entry_date, shares: buyForm.shares }),
            });
            if (res.ok) {
                setBuyTarget(null);
                alert(`${buyTarget.ticker} added to portfolio!`);
            } else {
                const err = await res.json();
                alert(err.detail || 'Failed to add position');
            }
        } catch (err) {
            console.error('Buy failed:', err);
        }
    };

    const headerStyle: React.CSSProperties = { padding: '12px 10px', cursor: 'pointer', userSelect: 'none' };

    return (
        <div style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
                <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 700 }}>Watchlist</h1>
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button 
                        className={`toggle-btn ${tab === 'active' ? 'active' : ''}`}
                        onClick={() => setTab('active')}
                        style={{ padding: '8px 20px', borderRadius: '6px' }}
                    >
                        Active ({watchlist.active.length})
                    </button>
                    <button 
                        className={`toggle-btn ${tab === 'removed' ? 'active' : ''}`}
                        onClick={() => setTab('removed')}
                        style={{ padding: '8px 20px', borderRadius: '6px' }}
                    >
                        Removed ({watchlist.removed.length})
                    </button>
                </div>
            </div>

            {error && <div className="glass-panel" style={{ padding: '20px', color: appConfig.colors.bad, marginBottom: '20px' }}>Error: {error}</div>}

            {loading ? (
                <div style={{ textAlign: 'center', padding: '100px', color: '#888' }}>Loading watchlist...</div>
            ) : (
                <div className="glass-panel" style={{ padding: '20px', minHeight: '400px' }}>
                    {tab === 'active' ? (
                        watchlist.active.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: '60px', color: '#666' }}>No active stocks in watchlist. Start by adding some stars!</div>
                        ) : (
                            <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', width: '100%', maxWidth: '100%' }}>
                                {selectedTickers.size > 0 && (
                                    <div style={{ marginBottom: '15px', display: 'flex', alignItems: 'center', gap: '15px', padding: '10px', background: 'rgba(255,255,255,0.05)', borderRadius: '8px' }}>
                                        <span style={{ fontSize: '14px', color: '#aaa' }}>{selectedTickers.size} items selected</span>
                                        <button 
                                            onClick={handleBulkRemove}
                                            style={{ 
                                                background: '#ff4444', 
                                                border: 'none', 
                                                color: '#fff',
                                                padding: '6px 16px',
                                                borderRadius: '4px',
                                                fontSize: '13px',
                                                fontWeight: 600,
                                                cursor: 'pointer'
                                            }}
                                        >
                                            Remove Selected
                                        </button>
                                        <button 
                                            onClick={() => setSelectedTickers(new Set())}
                                            style={{ background: 'transparent', border: 'none', color: '#888', cursor: 'pointer', fontSize: '13px' }}
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                )}
                                <table className="screener-table" style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                    <thead style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                                        <tr style={{ color: '#888', textAlign: 'left' }}>
                                            <th style={{ padding: '12px 10px', width: '30px' }}>
                                                <input 
                                                    type="checkbox" 
                                                    checked={selectedTickers.size === watchlist.active.length && watchlist.active.length > 0}
                                                    onChange={toggleSelectAll}
                                                    style={{ cursor: 'pointer' }}
                                                />
                                            </th>
                                            <th style={headerStyle} onClick={() => handleSort('ticker')}>Ticker {renderSortIcon('ticker')}</th>
                                            <th style={headerStyle} onClick={() => handleSort('entry_date')}>Entry Date {renderSortIcon('entry_date')}</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>Entry Price</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>Latest</th>
                                            <th style={{ ...headerStyle, textAlign: 'right' }} onClick={() => handleSort('gain_pct')}>Gain% {renderSortIcon('gain_pct')}</th>
                                            <th style={{ ...headerStyle, textAlign: 'right' }} onClick={() => handleSort('max_gain_pct')}>Max Gain {renderSortIcon('max_gain_pct')}</th>
                                            <th style={{ ...headerStyle, textAlign: 'right' }} onClick={() => handleSort('min_gain_pct')}>Min Gain {renderSortIcon('min_gain_pct')}</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'right' }}>ADR%(21)</th>
                                            <th style={{ ...headerStyle, textAlign: 'right' }} onClick={() => handleSort('latest_dist_sma50_atr')}>50dATR {renderSortIcon('latest_dist_sma50_atr')}</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'center', width: '80px' }}>RS21 (30D)</th>
                                            <th style={{ padding: '12px 10px', textAlign: 'center' }}>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sortedActive.map(item => (
                                            <tr 
                                                key={item.id} 
                                                style={{ 
                                                    borderBottom: '1px solid rgba(255,255,255,0.05)',
                                                    background: selectedTickers.has(item.ticker) ? 'rgba(41, 98, 255, 0.08)' : 'transparent'
                                                }}
                                            >
                                                <td style={{ padding: '12px 10px' }}>
                                                    <input 
                                                        type="checkbox" 
                                                        checked={selectedTickers.has(item.ticker)}
                                                        onChange={() => toggleSelect(item.ticker)}
                                                        style={{ cursor: 'pointer' }}
                                                    />
                                                </td>
                                                <td style={{ padding: '12px 10px' }}>
                                                    <Link to={`/chart/${item.ticker}`} style={{ color: '#fff', textDecoration: 'none', fontWeight: 600 }}>
                                                        {item.ticker}
                                                    </Link>
                                                    <div style={{ fontSize: '11px', color: '#666' }}>{item.name}</div>
                                                </td>
                                                <td style={{ padding: '12px 10px' }}>
                                                    <input 
                                                        type="date" 
                                                        defaultValue={item.entry_date} 
                                                        onBlur={(e) => {
                                                            if (e.target.value !== item.entry_date) handleUpdateDate(item.ticker, e.target.value);
                                                        }}
                                                        style={{ 
                                                            background: 'rgba(255,255,255,0.05)', 
                                                            border: '1px solid rgba(255,255,255,0.1)',
                                                            color: '#fff',
                                                            borderRadius: '4px',
                                                            padding: '2px 4px',
                                                            fontSize: '12px',
                                                            cursor: 'pointer'
                                                        }}
                                                    />
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right' }}>{item.entry_price.toFixed(2)}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right' }}>{item.latest_close.toFixed(2)}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', fontWeight: 700, color: getPctColor(item.gain_pct) }}>
                                                    {formatPct(item.gain_pct)}
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: appConfig.colors.good }}>{formatPct(item.max_gain_pct)}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: appConfig.colors.bad }}>{formatPct(item.min_gain_pct)}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: '#ccc' }}>{item.latest_adr_pct.toFixed(2)}</td>
                                                <td style={{ padding: '12px 10px', textAlign: 'right', color: item.latest_dist_sma50_atr >= 8 ? '#ffff00' : '#ccc' }}>
                                                    {item.latest_dist_sma50_atr.toFixed(1)}
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'center' }}>
                                                    <Sparkline data={item.rs_sparkline} width={70} height={28} color={item.gain_pct >= 0 ? appConfig.colors.good : appConfig.colors.bad} fixedRange={true} />
                                                </td>
                                                <td style={{ padding: '12px 10px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                                                    <button 
                                                        onClick={() => setBuyTarget({ ticker: item.ticker, entry_date: item.entry_date })}
                                                        style={{ 
                                                            background: 'rgba(59, 130, 246, 0.1)', 
                                                            border: '1px solid rgba(59, 130, 246, 0.2)', 
                                                            color: '#3b82f6',
                                                            padding: '4px 10px',
                                                            borderRadius: '4px',
                                                            fontSize: '11px',
                                                            cursor: 'pointer',
                                                            marginRight: '4px'
                                                        }}
                                                    >
                                                        Buy
                                                    </button>
                                                    <button 
                                                        onClick={() => handleRemove(item.ticker)}
                                                        style={{ 
                                                            background: 'rgba(255, 68, 68, 0.1)', 
                                                            border: '1px solid rgba(255, 68, 68, 0.2)', 
                                                            color: '#ff4444',
                                                            padding: '4px 10px',
                                                            borderRadius: '4px',
                                                            fontSize: '11px',
                                                            cursor: 'pointer'
                                                        }}
                                                    >
                                                        Remove
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )
                    ) : (
                        <div>
                            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '15px' }}>
                                <button 
                                    onClick={handleClearRemoved}
                                    style={{ 
                                        background: 'transparent', 
                                        border: '1px solid rgba(255,255,255,0.2)', 
                                        color: '#888',
                                        padding: '4px 12px',
                                        borderRadius: '4px',
                                        fontSize: '12px',
                                        cursor: 'pointer'
                                    }}
                                >
                                    Clear History
                                </button>
                            </div>
                            {watchlist.removed.length === 0 ? (
                                <div style={{ textAlign: 'center', padding: '60px', color: '#666' }}>History is empty.</div>
                            ) : (
                                <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', width: '100%', maxWidth: '100%' }}>
                                    <table className="screener-table" style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                        <thead style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                                            <tr style={{ color: '#888', textAlign: 'left' }}>
                                                <th style={headerStyle} onClick={() => handleSort('ticker')}>Ticker {renderSortIcon('ticker')}</th>
                                                <th style={headerStyle} onClick={() => handleSort('entry_date')}>Entry Date {renderSortIcon('entry_date')}</th>
                                                <th style={headerStyle} onClick={() => handleSort('removed_at')}>Removed Date {renderSortIcon('removed_at')}</th>
                                                <th style={{ padding: '12px 10px', textAlign: 'right' }}>Entry Price</th>
                                                <th style={{ padding: '12px 10px', textAlign: 'right' }}>Removed Price</th>
                                                <th style={{ ...headerStyle, textAlign: 'right' }} onClick={() => handleSort('result_pct')}>Result% {renderSortIcon('result_pct')}</th>
                                                <th style={{ padding: '12px 10px', textAlign: 'center' }}>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {sortedRemoved.map(item => {
                                                const resultPct = item.removed_price ? ((item.removed_price - item.entry_price) / item.entry_price) * 100 : 0;
                                                return (
                                                    <tr key={item.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                                        <td style={{ padding: '12px 10px' }}>
                                                            <div style={{ color: '#aaa', fontWeight: 600 }}>{item.ticker}</div>
                                                            <div style={{ fontSize: '11px', color: '#555' }}>{item.name}</div>
                                                        </td>
                                                        <td style={{ padding: '12px 10px', color: '#888' }}>{item.entry_date}</td>
                                                        <td style={{ padding: '12px 10px', color: '#888' }}>{item.removed_at?.split('T')[0]}</td>
                                                        <td style={{ padding: '12px 10px', textAlign: 'right', color: '#888' }}>{item.entry_price.toFixed(2)}</td>
                                                        <td style={{ padding: '12px 10px', textAlign: 'right', color: '#888' }}>{item.removed_price?.toFixed(2)}</td>
                                                        <td style={{ padding: '12px 10px', textAlign: 'right', fontWeight: 600, color: getPctColor(resultPct) }}>
                                                            {formatPct(resultPct)}
                                                        </td>
                                                        <td style={{ padding: '12px 10px', textAlign: 'center' }}>
                                                            <button 
                                                                 onClick={() => handleReactivate(item.ticker, item.entry_date)}
                                                                style={{ 
                                                                    background: 'rgba(0, 255, 136, 0.1)', 
                                                                    border: '1px solid rgba(0, 255, 136, 0.2)', 
                                                                    color: '#00ff88',
                                                                    padding: '4px 10px',
                                                                    borderRadius: '4px',
                                                                    fontSize: '11px',
                                                                    cursor: 'pointer'
                                                                }}
                                                            >
                                                                Restore
                                                            </button>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Buy Modal */}
            {buyTarget && (
                <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
                     onClick={() => setBuyTarget(null)}>
                    <div style={{ background: '#0f172a', border: '1px solid rgba(59,130,246,0.3)', borderRadius: '12px', padding: '28px', width: '380px', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}
                         onClick={e => e.stopPropagation()}>
                        <h3 style={{ margin: '0 0 20px', fontSize: '16px' }}>Add <span style={{ color: '#3b82f6' }}>{buyTarget.ticker}</span> to Portfolio</h3>
                        <div style={{ marginBottom: '14px' }}>
                            <label style={{ fontSize: '11px', fontWeight: 600, color: '#8b9cc8', textTransform: 'uppercase' as const, letterSpacing: '0.06em', display: 'block', marginBottom: '4px' }}>Portfolio</label>
                            <select
                                value={buyForm.portfolio_id}
                                onChange={e => setBuyForm({ ...buyForm, portfolio_id: Number(e.target.value) })}
                                style={{ background: 'rgba(30,40,70,0.5)', border: '1px solid rgba(99,120,180,0.25)', borderRadius: '6px', padding: '8px 12px', color: '#e8edf7', fontSize: '13px', width: '100%' }}>
                                {portfolioList.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                            </select>
                        </div>
                        <div style={{ marginBottom: '14px' }}>
                            <label style={{ fontSize: '11px', fontWeight: 600, color: '#8b9cc8', textTransform: 'uppercase' as const, letterSpacing: '0.06em', display: 'block', marginBottom: '4px' }}>Shares</label>
                            <input type="number" value={buyForm.shares} onChange={e => setBuyForm({ ...buyForm, shares: Number(e.target.value) })}
                                style={{ background: 'rgba(30,40,70,0.5)', border: '1px solid rgba(99,120,180,0.25)', borderRadius: '6px', padding: '8px 12px', color: '#e8edf7', fontSize: '13px', width: '100%' }} />
                        </div>
                        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                            <button onClick={() => setBuyTarget(null)} style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.2)', color: '#888', padding: '8px 16px', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }}>Cancel</button>
                            <button onClick={handleBuy} disabled={portfolioList.length === 0}
                                style={{ background: 'linear-gradient(135deg, #3b82f6, #2563eb)', border: 'none', color: '#fff', padding: '8px 24px', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', fontWeight: 600 }}>Confirm Buy</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
