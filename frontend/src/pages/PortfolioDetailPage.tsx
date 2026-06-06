import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { appConfig } from '../config';

type Tab = 'positions' | 'history' | 'analytics' | 'settings';

interface Analytics {
    sector_breakdown: { category: string; invested: number }[];
    total_trades: number; win_count: number; loss_count: number;
    win_rate: number; avg_win_pct: number; avg_loss_pct: number; expectancy_pct: number;
    equity_curve: { date: string; cumulative_pnl: number; ticker: string }[];
    monthly_returns: { month: string; pnl: number }[];
}

interface Position {
    id: number; symbol_id: number; ticker: string; name: string;
    entry_date: string; entry_price: number; shares: number; original_shares: number;
    invested: number; current_price: number; total_gain_pct: number; total_gain_amount: number;
    stop_loss_price: number; stop_loss_alert: boolean; distance_to_stop_pct: number;
    atr_pct: number | null; status: string; memo: string | null;
}

interface HistoryItem {
    id: number; ticker: string; name: string;
    entry_date: string; entry_price: number; exit_date: string; exit_price: number;
    exit_shares: number; exit_reason: string; pnl_pct: number; pnl_amount: number;
    holding_days: number; cumulative_pnl: number; memo: string | null;
}

interface Summary {
    portfolio_id: number; name: string; currency: string; total_capital: number;
    risk_pct: number; risk_amount: number; max_investment: number;
    stop_loss_method: string; default_stop_loss_pct: number;
    max_positions: number; open_positions: number;
    invested_total: number; market_value_total: number; unrealized_pnl: number;
    dynamic_max_positions?: number;
    market_regime?: string;
    vxv_vix_ema5?: number;
    vxv_vix_ema21?: number;
    target_cash_ratio?: number;
    tighten_stop_loss?: boolean;
}

interface PortfolioDetail {
    id: number; name: string; currency: string; total_capital: number;
    risk_pct: number; default_stop_loss_pct: number; stop_loss_method: string;
    atr_multiplier: number | null; profit_take_method: string | null;
    max_positions: number; source: string; status: string;
}

const formatPct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
const pctColor = (v: number) => v > 0 ? appConfig.colors.good : v < 0 ? appConfig.colors.bad : '#ccc';

const inputStyle: React.CSSProperties = {
    background: 'rgba(30,40,70,0.5)', border: '1px solid rgba(99,120,180,0.25)',
    borderRadius: '6px', padding: '8px 12px', color: '#e8edf7', fontSize: '13px',
    width: '100%', outline: 'none',
};
const labelStyle: React.CSSProperties = {
    fontSize: '11px', fontWeight: 600, color: '#8b9cc8',
    textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px', display: 'block',
};
const thStyle: React.CSSProperties = { padding: '12px 10px', textAlign: 'left' as const };
const tdStyle: React.CSSProperties = { padding: '12px 10px' };
const tdRight: React.CSSProperties = { ...tdStyle, textAlign: 'right' as const, fontVariantNumeric: 'tabular-nums' };

export const PortfolioDetailPage: React.FC = () => {
    const { portfolioId } = useParams<{ portfolioId: string }>();
    const [tab, setTab] = useState<Tab>('positions');
    const [detail, setDetail] = useState<PortfolioDetail | null>(null);
    const [summary, setSummary] = useState<Summary | null>(null);
    const [positions, setPositions] = useState<Position[]>([]);
    const [history, setHistory] = useState<HistoryItem[]>([]);
    const [analytics, setAnalytics] = useState<Analytics | null>(null);
    const [loading, setLoading] = useState(true);
    const [showAddForm, setShowAddForm] = useState(false);
    const [showSellForm, setShowSellForm] = useState<number | null>(null);

    // Add position form
    const [addForm, setAddForm] = useState({ ticker: '', entry_date: '', shares: 100, entry_price: 0 });
    // Sell form
    const [sellForm, setSellForm] = useState({ exit_date: '', exit_price: 0, exit_shares: 0, exit_reason: 'manual' });
    // Edit form
    const [showEditForm, setShowEditForm] = useState<number | null>(null);
    const [editForm, setEditForm] = useState({ entry_date: '', entry_price: 0, shares: 0 });
    // History Edit form
    const [showEditHistForm, setShowEditHistForm] = useState<number | null>(null);
    const [editHistForm, setEditHistForm] = useState({ entry_date: '', entry_price: 0, exit_date: '', exit_price: 0, exit_shares: 0 });
    // Settings form
    const [settingsForm, setSettingsForm] = useState<Partial<PortfolioDetail>>({});

    const pfId = portfolioId ? parseInt(portfolioId) : 0;

    const fetchAll = async () => {
        setLoading(true);
        try {
            const [dRes, sRes, pRes, hRes, aRes] = await Promise.all([
                fetch(`/api/portfolio/${pfId}`),
                fetch(`/api/portfolio/${pfId}/summary`),
                fetch(`/api/portfolio/${pfId}/positions`),
                fetch(`/api/portfolio/${pfId}/history`),
                fetch(`/api/portfolio/${pfId}/analytics`),
            ]);
            if (dRes.ok) { const d = await dRes.json(); setDetail(d); setSettingsForm(d); }
            if (sRes.ok) setSummary(await sRes.json());
            if (pRes.ok) setPositions(await pRes.json());
            if (hRes.ok) setHistory(await hRes.json());
            if (aRes.ok) setAnalytics(await aRes.json());
        } catch (err) { console.error(err); }
        finally { setLoading(false); }
    };

    useEffect(() => { if (pfId) fetchAll(); }, [pfId]);

    useEffect(() => {
        const fetchPrice = async () => {
            if (addForm.ticker && addForm.entry_date) {
                try {
                    const res = await fetch(`/api/portfolio/ticker-price?ticker=${addForm.ticker}&date=${addForm.entry_date}`);
                    if (res.ok) {
                        const data = await res.json();
                        setAddForm(prev => ({ ...prev, entry_price: data.price }));
                    } else {
                        // Reset if ticker not found or no data
                        setAddForm(prev => ({ ...prev, entry_price: 0 }));
                    }
                } catch (err) {
                    setAddForm(prev => ({ ...prev, entry_price: 0 }));
                }
            }
        };
        const timer = setTimeout(fetchPrice, 500);
        return () => clearTimeout(timer);
    }, [addForm.ticker, addForm.entry_date]);

    const handleAddPosition = async (e: React.FormEvent) => {
        e.preventDefault();
        const res = await fetch(`/api/portfolio/${pfId}/positions`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(addForm),
        });
        if (res.ok) { setShowAddForm(false); setAddForm({ ticker: '', entry_date: '', shares: 100, entry_price: 0 }); await fetchAll(); }
        else { const err = await res.json(); alert(err.detail || 'Failed'); }
    };

    const handleSell = async (posId: number, e: React.FormEvent) => {
        e.preventDefault();
        const res = await fetch(`/api/portfolio/${pfId}/positions/${posId}/sell`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sellForm),
        });
        if (res.ok) { setShowSellForm(null); await fetchAll(); }
        else { const err = await res.json(); alert(err.detail || 'Failed'); }
    };

    const handleEdit = async (posId: number, e: React.FormEvent) => {
        e.preventDefault();
        const res = await fetch(`/api/portfolio/${pfId}/positions/${posId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(editForm),
        });
        if (res.ok) { setShowEditForm(null); await fetchAll(); }
        else { const err = await res.json(); alert(err.detail || 'Failed'); }
    };

    const handleDeletePosition = async (posId: number) => {
        if (!window.confirm('Are you sure you want to delete this position record? (This will remove the data permanently)')) return;
        const res = await fetch(`/api/portfolio/${pfId}/positions/${posId}`, { method: 'DELETE' });
        if (res.ok) { await fetchAll(); }
        else { const err = await res.json(); alert(err.detail || 'Failed'); }
    };

    const handleEditHistory = async (histId: number, e: React.FormEvent) => {
        e.preventDefault();
        const res = await fetch(`/api/portfolio/${pfId}/history/${histId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(editHistForm),
        });
        if (res.ok) { setShowEditHistForm(null); await fetchAll(); }
        else { const err = await res.json(); alert(err.detail || 'Failed'); }
    };

    const handleSaveSettings = async (e: React.FormEvent) => {
        e.preventDefault();
        const { name, currency, total_capital, risk_pct, default_stop_loss_pct, stop_loss_method, max_positions } = settingsForm;
        await fetch(`/api/portfolio/${pfId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, currency, total_capital, risk_pct, default_stop_loss_pct, stop_loss_method, max_positions }),
        });
        await fetchAll();
    };

    const fmtCur = (v: number) => {
        const c = detail?.currency || 'JPY';
        return c === 'JPY' ? `¥${v.toLocaleString()}` : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
    };

    if (loading) return <div style={{ padding: '80px', textAlign: 'center', color: '#666' }}>Loading...</div>;
    if (!detail) return <div style={{ padding: '80px', textAlign: 'center', color: '#f43f5e' }}>Portfolio not found</div>;

    const tabBtn = (t: Tab, label: string) => (
        <button
            className={`toggle-btn ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
            style={{ padding: '8px 20px', borderRadius: '6px' }}
        >{label}</button>
    );

    /* ─── Summary Cards ──────────────────────────────────── */
    const summaryCards = summary ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '12px', marginBottom: '24px' }}>
            {[
                { label: 'Capital (総資金)', value: fmtCur(summary.total_capital) },
                { label: 'Risk Amount (リスク額)', value: fmtCur(summary.risk_amount), sub: `${summary.risk_pct}%` },
                { label: 'Max/Position (推奨投資額)', value: fmtCur(summary.max_investment), sub: summary.market_regime ? `VIX EMA: ${summary.market_regime}` : undefined },
                { label: 'Positions (保有数)', value: `${summary.open_positions} / ${summary.dynamic_max_positions ?? summary.max_positions}`, sub: summary.dynamic_max_positions !== undefined && summary.dynamic_max_positions !== summary.max_positions ? `Config limit: ${summary.max_positions}` : undefined },
                { label: 'Invested (投資済額)', value: fmtCur(summary.invested_total) },
                { label: 'Market Value (評価額)', value: fmtCur(summary.market_value_total) },
                { label: 'Unrealized P&L (含み損益)', value: fmtCur(summary.unrealized_pnl), color: pctColor(summary.unrealized_pnl) },
            ].map((c, i) => (
                <div key={i} style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '14px 16px' }}>
                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{c.label}</div>
                    <div style={{ fontSize: '16px', fontWeight: 700, marginTop: '4px', color: c.color || '#e8edf7', fontVariantNumeric: 'tabular-nums' }}>{c.value}</div>
                    {c.sub && <div style={{ fontSize: '11px', color: '#8b9cc8', marginTop: '2px' }}>{c.sub}</div>}
                </div>
            ))}
        </div>
    ) : null;

    return (
        <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
            {/* Breadcrumb + Header */}
            <div style={{ marginBottom: '20px' }}>
                <Link to="/portfolio" style={{ color: '#8b9cc8', textDecoration: 'none', fontSize: '12px' }}>← Portfolio</Link>
                <h1 style={{ margin: '8px 0 0', fontSize: '24px', fontWeight: 700 }}>{detail.name}</h1>
            </div>

            {summaryCards}

            {/* Tabs */}
            {/* VIX EMA Market Regime Badge */}
            {summary?.market_regime === 'OVERHEAT' && (
                <div style={{ marginBottom: '12px', padding: '10px 16px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px' }}>🔥</span>
                    <span style={{ color: '#f59e0b', fontWeight: 600, fontSize: '13px' }}>
                        Market Phase: OVERHEAT (過熱状態)
                    </span>
                    <span style={{ color: '#8b9cc8', fontSize: '12px' }}>— 推奨アクション: 新規買付制限（最大4枠）, 既存ポジションのストップロスを買値に引き上げて利益保護を徹底してください。</span>
                </div>
            )}
            {summary?.market_regime === 'BEAR' && (
                <div style={{ marginBottom: '12px', padding: '10px 16px', background: 'rgba(244,63,94,0.08)', border: '1px solid rgba(244,63,94,0.25)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px' }}>🐻</span>
                    <span style={{ color: '#f43f5e', fontWeight: 600, fontSize: '13px' }}>
                        Market Phase: BEAR (弱気相場)
                    </span>
                    <span style={{ color: '#8b9cc8', fontSize: '12px' }}>— 推奨アクション: 新規買付停止（0枠）, キャッシュ比率引き上げ（推奨 70%以上）でディフェンシブに。</span>
                </div>
            )}
            {summary?.market_regime === 'BOTTOM' && (
                <div style={{ marginBottom: '12px', padding: '10px 16px', background: 'rgba(34,211,160,0.08)', border: '1px solid rgba(34,211,160,0.25)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px' }}>💎</span>
                    <span style={{ color: '#22d3a0', fontWeight: 600, fontSize: '13px' }}>
                        Market Phase: BOTTOM (大底シグナル)
                    </span>
                    <span style={{ color: '#8b9cc8', fontSize: '12px' }}>— 推奨アクション: 打診買い開始（最大2枠）, キャッシュ50%を維持しつつ段階的に優良株を仕込んでください。</span>
                </div>
            )}
            {summary?.market_regime === 'BULL' && (
                <div style={{ marginBottom: '12px', padding: '10px 16px', background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.25)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px' }}>🐂</span>
                    <span style={{ color: '#3b82f6', fontWeight: 600, fontSize: '13px' }}>
                        Market Phase: BULL (強気トレンド)
                    </span>
                    <span style={{ color: '#8b9cc8', fontSize: '12px' }}>— 推奨アクション: 通常運転（フルサイズ許容）, アクティブにポジションを積み上げて利益を最大化します。</span>
                </div>
            )}

            {/* Alert badge */}
            {positions.some(p => p.stop_loss_alert) && (
                <div style={{ marginBottom: '12px', padding: '10px 16px', background: 'rgba(244,63,94,0.1)', border: '1px solid rgba(244,63,94,0.3)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px' }}>⚠️</span>
                    <span style={{ color: '#f43f5e', fontWeight: 600, fontSize: '13px' }}>
                        {positions.filter(p => p.stop_loss_alert).length} position{positions.filter(p => p.stop_loss_alert).length > 1 ? 's' : ''} near stop loss
                    </span>
                    <span style={{ color: '#8b9cc8', fontSize: '12px' }}>— {positions.filter(p => p.stop_loss_alert).map(p => p.ticker).join(', ')}</span>
                </div>
            )}

            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '20px' }}>
                {tabBtn('positions', `Positions (保有銘柄: ${positions.length})`)}
                {tabBtn('history', `History (取引履歴: ${history.length})`)}
                {tabBtn('analytics', 'Analytics (分析)')}
                {tabBtn('settings', 'Settings (設定)')}
            </div>

            {/* ─── Positions Tab ─── */}
            {tab === 'positions' && (
                <div>
                    <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'flex-end' }}>
                        <button onClick={() => setShowAddForm(!showAddForm)} style={{
                            background: showAddForm ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #3b82f6, #2563eb)',
                            border: 'none', color: '#fff', padding: '8px 20px', borderRadius: '6px',
                            fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                        }}>{showAddForm ? 'Cancel' : '＋ Add Position'}</button>
                    </div>

                    {showAddForm && (() => {
                        const maxInvestment = summary ? summary.max_investment : 0;
                        const totalCost = addForm.entry_price * addForm.shares;
                        const targetShares = addForm.entry_price > 0 ? Math.floor(maxInvestment / addForm.entry_price) : 0;
                        return (
                        <form onSubmit={handleAddPosition} style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(59,130,246,0.3)', borderRadius: '10px', padding: '20px', marginBottom: '16px' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px', alignItems: 'end' }}>
                                <div><label style={labelStyle}>Ticker (ティッカー)</label><input style={inputStyle} value={addForm.ticker} onChange={e => setAddForm({ ...addForm, ticker: e.target.value.toUpperCase() })} required /></div>
                                <div><label style={labelStyle}>Entry Date (購入日)</label><input style={inputStyle} type="date" value={addForm.entry_date} onChange={e => setAddForm({ ...addForm, entry_date: e.target.value })} required /></div>
                                <div><label style={labelStyle}>Price (単価)</label><input style={inputStyle} type="number" step="0.01" value={addForm.entry_price} onChange={e => setAddForm({ ...addForm, entry_price: Number(e.target.value) })} required /></div>
                                <div>
                                    <label style={labelStyle}>Shares (株数) <span style={{color: '#8b9cc8', fontWeight: 'normal'}}>(Target: {targetShares})</span></label>
                                    <input style={inputStyle} type="number" step="0.01" value={addForm.shares} onChange={e => setAddForm({ ...addForm, shares: Number(e.target.value) })} required />
                                </div>
                                <button type="submit" style={{ background: '#22d3a0', border: 'none', color: '#fff', padding: '8px 20px', borderRadius: '6px', fontWeight: 600, cursor: 'pointer', height: '38px' }}>Buy</button>
                            </div>
                            <div style={{ marginTop: '12px', fontSize: '13px', color: totalCost > maxInvestment ? '#ef4444' : '#22d3a0', display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                                Entry Cost: {fmtCur(totalCost)} <span style={{color: '#666'}}>|</span> Max/Pos: {fmtCur(maxInvestment)}
                            </div>
                        </form>
                        );
                    })()}

                    {positions.length === 0 ? (
                        <div style={{ textAlign: 'center', padding: '60px', color: '#475685' }}>No open positions</div>
                    ) : (
                        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                            <table style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                                <thead style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                                    <tr style={{ color: '#888' }}>
                                        <th style={thStyle}>Ticker</th>
                                        <th style={thStyle}>Entry Date</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Entry</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Current</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Shares</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Gain%</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Gain</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Stop Loss</th>
                                        <th style={{ ...thStyle, textAlign: 'right' }}>Dist%</th>
                                        <th style={{ ...thStyle, textAlign: 'center' }}>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {positions.map(p => (
                                        <React.Fragment key={p.id}>
                                            <tr style={{
                                                borderBottom: '1px solid rgba(255,255,255,0.05)',
                                                background: p.stop_loss_alert ? 'rgba(244,63,94,0.08)' : 'transparent',
                                            }}>
                                                <td style={tdStyle}>
                                                    <Link to={`/chart/${p.ticker}`} style={{ color: '#fff', textDecoration: 'none', fontWeight: 600 }}>{p.ticker}</Link>
                                                    <div style={{ fontSize: '11px', color: '#666' }}>{p.name}</div>
                                                </td>
                                                <td style={tdStyle}>{p.entry_date}</td>
                                                <td style={tdRight}>{p.entry_price.toFixed(2)}</td>
                                                <td style={tdRight}>{p.current_price.toFixed(2)}</td>
                                                <td style={tdRight}>{p.shares}{p.shares < p.original_shares && <span style={{ color: '#8b9cc8', fontSize: '11px' }}> /{p.original_shares}</span>}</td>
                                                <td style={{ ...tdRight, fontWeight: 700, color: pctColor(p.total_gain_pct) }}>{formatPct(p.total_gain_pct)}</td>
                                                <td style={{ ...tdRight, color: pctColor(p.total_gain_amount) }}>{fmtCur(p.total_gain_amount)}</td>
                                                <td style={{ ...tdRight, color: p.stop_loss_alert ? appConfig.colors.bad : '#ccc' }}>
                                                    {p.stop_loss_price.toFixed(2)}
                                                    {p.stop_loss_alert && <span style={{ marginLeft: '4px', fontSize: '11px' }}>⚠️</span>}
                                                </td>
                                                <td style={{ ...tdRight, color: p.distance_to_stop_pct < 3 ? appConfig.colors.bad : '#ccc' }}>
                                                    {p.distance_to_stop_pct.toFixed(1)}%
                                                </td>
                                                <td style={{ ...tdStyle, textAlign: 'center' }}>
                                                    <button onClick={() => { setShowSellForm(showSellForm === p.id ? null : p.id); setShowEditForm(null); setSellForm({ exit_date: new Date().toISOString().split('T')[0], exit_price: p.current_price, exit_shares: p.shares, exit_reason: 'manual' }); }}
                                                        style={{ background: 'rgba(244,63,94,0.1)', border: '1px solid rgba(244,63,94,0.2)', color: '#f43f5e', padding: '4px 12px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer', marginRight: '4px' }}>
                                                        Sell
                                                    </button>
                                                    <button onClick={() => { setShowEditForm(showEditForm === p.id ? null : p.id); setShowSellForm(null); setEditForm({ entry_date: p.entry_date, entry_price: p.entry_price, shares: p.shares }); }}
                                                        style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)', color: '#3b82f6', padding: '4px 12px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer' }}>
                                                        Edit
                                                    </button>
                                                </td>
                                            </tr>
                                            {showSellForm === p.id && (
                                                <tr><td colSpan={10} style={{ padding: '12px 10px', background: 'rgba(15,23,42,0.4)' }}>
                                                    <form onSubmit={(e) => handleSell(p.id, e)} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '10px', alignItems: 'end' }}>
                                                        <div><label style={labelStyle}>Exit Date (売却日)</label><input style={inputStyle} type="date" value={sellForm.exit_date} onChange={e => setSellForm({ ...sellForm, exit_date: e.target.value })} required /></div>
                                                        <div><label style={labelStyle}>Exit Price (売却単価)</label><input style={inputStyle} type="number" step="0.01" value={sellForm.exit_price} onChange={e => setSellForm({ ...sellForm, exit_price: Number(e.target.value) })} required /></div>
                                                        <div><label style={labelStyle}>Shares (株数) (max {p.shares})</label><input style={inputStyle} type="number" step="0.01" max={p.shares} value={sellForm.exit_shares} onChange={e => setSellForm({ ...sellForm, exit_shares: Number(e.target.value) })} required /></div>
                                                        <div><label style={labelStyle}>Reason (理由)</label>
                                                            <select style={inputStyle} value={sellForm.exit_reason} onChange={e => setSellForm({ ...sellForm, exit_reason: e.target.value })}>
                                                                <option value="manual">Manual (手動)</option>
                                                                <option value="stop_loss">Stop Loss (損切り)</option>
                                                                <option value="take_profit_trim">Take Profit (Trim) (利確分割)</option>
                                                                <option value="take_profit_full">Take Profit (Full) (利確全売)</option>
                                                                <option value="trailing_stop">Trailing Stop (トレイリング)</option>
                                                            </select>
                                                        </div>
                                                        <button type="submit" style={{ background: '#f43f5e', border: 'none', color: '#fff', padding: '8px 16px', borderRadius: '6px', fontWeight: 600, cursor: 'pointer', height: '38px' }}>Confirm Sell</button>
                                                    </form>
                                                </td></tr>
                                            )}
                                            {showEditForm === p.id && (
                                                <tr><td colSpan={10} style={{ padding: '12px 10px', background: 'rgba(15,23,42,0.4)' }}>
                                                    <form onSubmit={(e) => handleEdit(p.id, e)} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '10px', alignItems: 'end' }}>
                                                        <div><label style={labelStyle}>Entry Date (購入日)</label><input style={inputStyle} type="date" value={editForm.entry_date} onChange={e => setEditForm({ ...editForm, entry_date: e.target.value })} required /></div>
                                                        <div><label style={labelStyle}>Entry Price (購入単価)</label><input style={inputStyle} type="number" step="0.01" value={editForm.entry_price} onChange={e => setEditForm({ ...editForm, entry_price: Number(e.target.value) })} required /></div>
                                                        <div><label style={labelStyle}>Shares (株数)</label><input style={inputStyle} type="number" step="0.01" value={editForm.shares} onChange={e => setEditForm({ ...editForm, shares: Number(e.target.value) })} required /></div>
                                                        <div style={{ display: 'flex', gap: '8px' }}>
                                                            <button type="submit" style={{ background: '#3b82f6', border: 'none', color: '#fff', padding: '8px 16px', borderRadius: '6px', fontWeight: 600, cursor: 'pointer', height: '38px' }}>Save Changes</button>
                                                            <button type="button" onClick={() => handleDeletePosition(p.id)} style={{ background: 'rgba(244,63,94,0.1)', border: '1px solid rgba(244,63,94,0.2)', color: '#f43f5e', padding: '8px 16px', borderRadius: '6px', fontWeight: 600, cursor: 'pointer', height: '38px' }}>Delete</button>
                                                        </div>
                                                    </form>
                                                    <div style={{ marginTop: '8px', fontSize: '12px', color: '#22d3a0', textAlign: 'right' }}>
                                                        Entry Cost: {fmtCur(editForm.entry_price * editForm.shares)}
                                                    </div>
                                                </td></tr>
                                            )}
                                        </React.Fragment>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* ─── History Tab ─── */}
            {tab === 'history' && (
                history.length === 0 ? (
                    <div style={{ textAlign: 'center', padding: '60px', color: '#475685' }}>No trade history yet</div>
                ) : (
                    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                        <table style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                            <thead style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                                <tr style={{ color: '#888' }}>
                                    <th style={thStyle}>Ticker</th><th style={thStyle}>Entry</th><th style={thStyle}>Exit</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Entry $</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Exit $</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Shares</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>P&L%</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>P&L</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Days</th>
                                    <th style={thStyle}>Reason</th>
                                    <th style={{ ...thStyle, textAlign: 'right' }}>Cumulative</th>
                                    <th style={{ ...thStyle, textAlign: 'center' }}>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {history.map(h => (
                                    <React.Fragment key={h.id}>
                                        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                            <td style={tdStyle}><Link to={`/chart/${h.ticker}`} style={{ color: '#fff', textDecoration: 'none', fontWeight: 600 }}>{h.ticker}</Link></td>
                                            <td style={tdStyle}>{h.entry_date}</td>
                                            <td style={tdStyle}>{h.exit_date}</td>
                                            <td style={tdRight}>{h.entry_price.toFixed(2)}</td>
                                            <td style={tdRight}>{h.exit_price.toFixed(2)}</td>
                                            <td style={tdRight}>{h.exit_shares}</td>
                                            <td style={{ ...tdRight, fontWeight: 700, color: pctColor(h.pnl_pct) }}>{formatPct(h.pnl_pct)}</td>
                                            <td style={{ ...tdRight, color: pctColor(h.pnl_amount) }}>{fmtCur(h.pnl_amount)}</td>
                                            <td style={tdRight}>{h.holding_days}d</td>
                                            <td style={{ ...tdStyle, fontSize: '11px', color: '#8b9cc8' }}>{h.exit_reason.replace(/_/g, ' ')}</td>
                                            <td style={{ ...tdRight, fontWeight: 600, color: pctColor(h.cumulative_pnl) }}>{fmtCur(h.cumulative_pnl)}</td>
                                            <td style={{ ...tdStyle, textAlign: 'center' }}>
                                                <button onClick={() => { setShowEditHistForm(showEditHistForm === h.id ? null : h.id); setEditHistForm({ entry_date: h.entry_date, entry_price: h.entry_price, exit_date: h.exit_date, exit_price: h.exit_price, exit_shares: h.exit_shares }); }}
                                                    style={{ background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.2)', color: '#3b82f6', padding: '4px 12px', borderRadius: '4px', fontSize: '11px', cursor: 'pointer' }}>
                                                    Edit
                                                </button>
                                            </td>
                                        </tr>
                                        {showEditHistForm === h.id && (
                                            <tr><td colSpan={12} style={{ padding: '12px 10px', background: 'rgba(15,23,42,0.4)' }}>
                                                <form onSubmit={(e) => handleEditHistory(h.id, e)} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '10px', alignItems: 'end' }}>
                                                    <div><label style={labelStyle}>Entry Date (購入日)</label><input style={inputStyle} type="date" value={editHistForm.entry_date} onChange={e => setEditHistForm({ ...editHistForm, entry_date: e.target.value })} required /></div>
                                                    <div><label style={labelStyle}>Entry Price (購入単価)</label><input style={inputStyle} type="number" step="0.01" value={editHistForm.entry_price} onChange={e => setEditHistForm({ ...editHistForm, entry_price: Number(e.target.value) })} required /></div>
                                                    <div><label style={labelStyle}>Exit Date (売却日)</label><input style={inputStyle} type="date" value={editHistForm.exit_date} onChange={e => setEditHistForm({ ...editHistForm, exit_date: e.target.value })} required /></div>
                                                    <div><label style={labelStyle}>Exit Price (売却単価)</label><input style={inputStyle} type="number" step="0.01" value={editHistForm.exit_price} onChange={e => setEditHistForm({ ...editHistForm, exit_price: Number(e.target.value) })} required /></div>
                                                    <div><label style={labelStyle}>Shares (株数)</label><input style={inputStyle} type="number" step="0.01" value={editHistForm.exit_shares} onChange={e => setEditHistForm({ ...editHistForm, exit_shares: Number(e.target.value) })} required /></div>
                                                    <button type="submit" style={{ background: '#3b82f6', border: 'none', color: '#fff', padding: '8px 16px', borderRadius: '6px', fontWeight: 600, cursor: 'pointer', height: '38px' }}>Save Changes</button>
                                                </form>
                                                <div style={{ marginTop: '8px', fontSize: '12px', display: 'flex', justifyContent: 'flex-end', gap: '16px' }}>
                                                    <span style={{ color: '#22d3a0' }}>Entry Cost: {fmtCur(editHistForm.entry_price * editHistForm.exit_shares)}</span>
                                                    <span style={{ color: '#f43f5e' }}>Exit Value: {fmtCur(editHistForm.exit_price * editHistForm.exit_shares)}</span>
                                                </div>
                                            </td></tr>
                                        )}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )
            )}

            {/* ─── Analytics Tab ─── */}
            {tab === 'analytics' && analytics && (
                <div>
                    {/* Performance Stats */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px', marginBottom: '24px' }}>
                        {[
                            { label: 'Total Trades (総トレード数)', value: String(analytics.total_trades) },
                            { label: 'Win Rate (勝率)', value: `${analytics.win_rate}%`, color: analytics.win_rate >= 50 ? appConfig.colors.good : appConfig.colors.bad },
                            { label: 'Wins / Losses (勝敗数)', value: `${analytics.win_count} / ${analytics.loss_count}` },
                            { label: 'Avg Win (平均利益%)', value: `+${analytics.avg_win_pct.toFixed(2)}%`, color: appConfig.colors.good },
                            { label: 'Avg Loss (平均損失%)', value: `${analytics.avg_loss_pct.toFixed(2)}%`, color: appConfig.colors.bad },
                            { label: 'Expectancy (期待値%)', value: `${analytics.expectancy_pct >= 0 ? '+' : ''}${analytics.expectancy_pct.toFixed(2)}%`, color: pctColor(analytics.expectancy_pct) },
                        ].map((c, i) => (
                            <div key={i} style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '14px 16px' }}>
                                <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{c.label}</div>
                                <div style={{ fontSize: '18px', fontWeight: 700, marginTop: '4px', color: c.color || '#e8edf7' }}>{c.value}</div>
                            </div>
                        ))}
                    </div>

                    {/* Sector Breakdown */}
                    {analytics.sector_breakdown.length > 0 && (
                        <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '20px', marginBottom: '24px' }}>
                            <h3 style={{ margin: '0 0 16px', fontSize: '14px', color: '#8b9cc8' }}>Sector Distribution (セクタ分布)</h3>
                            {(() => { const total = analytics.sector_breakdown.reduce((s, c) => s + c.invested, 0); return analytics.sector_breakdown.map((s, i) => {
                                const pct = total > 0 ? (s.invested / total * 100) : 0;
                                const colors = ['#3b82f6', '#22d3a0', '#f59e0b', '#a855f7', '#f43f5e', '#06b6d4'];
                                return (
                                    <div key={i} style={{ marginBottom: '10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                                            <span>{s.category}</span>
                                            <span style={{ color: '#8b9cc8' }}>{fmtCur(s.invested)} ({pct.toFixed(1)}%)</span>
                                        </div>
                                        <div style={{ height: '6px', background: 'rgba(255,255,255,0.05)', borderRadius: '3px', overflow: 'hidden' }}>
                                            <div style={{ width: `${pct}%`, height: '100%', background: colors[i % colors.length], borderRadius: '3px', transition: 'width 0.5s ease' }} />
                                        </div>
                                    </div>
                                );
                            }); })()}
                        </div>
                    )}

                    {/* Equity Curve */}
                    {analytics.equity_curve.length > 0 && (
                        <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '20px', marginBottom: '24px' }}>
                            <h3 style={{ margin: '0 0 16px', fontSize: '14px', color: '#8b9cc8' }}>Equity Curve (資産曲線)</h3>
                            {(() => {
                                const data = analytics.equity_curve;
                                const vals = data.map(d => d.cumulative_pnl);
                                const maxV = Math.max(...vals, 0);
                                const minV = Math.min(...vals, 0);
                                const range = maxV - minV || 1;
                                const h = 200;
                                const w = 100;
                                const points = data.map((d, i) => {
                                    const x = (i / Math.max(data.length - 1, 1)) * w;
                                    const y = 100 - ((d.cumulative_pnl - minV) / range * 100);
                                    return `${x},${y}`;
                                }).join(' ');
                                const lastVal = data[data.length - 1]?.cumulative_pnl || 0;
                                return (
                                    <div>
                                        <svg viewBox={`0 0 ${w} 100`} style={{ width: '100%', height: `${h}px` }} preserveAspectRatio="none">
                                            {/* Zero line */}
                                            <line x1="0" y1={100 - ((0 - minV) / range * 100)} x2={w} y2={100 - ((0 - minV) / range * 100)} stroke="rgba(255,255,255,0.1)" strokeWidth="0.3" />
                                            <polyline points={points} fill="none" stroke={lastVal >= 0 ? '#22d3a0' : '#f43f5e'} strokeWidth="0.8" vectorEffect="non-scaling-stroke" />
                                        </svg>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: '#8b9cc8', marginTop: '8px' }}>
                                            <span>{data[0]?.date}</span>
                                            <span style={{ fontWeight: 700, color: pctColor(lastVal) }}>Cumulative: {fmtCur(lastVal)}</span>
                                            <span>{data[data.length - 1]?.date}</span>
                                        </div>
                                    </div>
                                );
                            })()}
                        </div>
                    )}

                    {/* Monthly Returns */}
                    {analytics.monthly_returns.length > 0 && (
                        <div style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '20px' }}>
                            <h3 style={{ margin: '0 0 16px', fontSize: '14px', color: '#8b9cc8' }}>Monthly Returns (月次損益)</h3>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))', gap: '8px' }}>
                                {analytics.monthly_returns.map((m, i) => (
                                    <div key={i} style={{
                                        padding: '12px', borderRadius: '8px', textAlign: 'center',
                                        background: m.pnl >= 0 ? 'rgba(34,211,160,0.1)' : 'rgba(244,63,94,0.1)',
                                        border: `1px solid ${m.pnl >= 0 ? 'rgba(34,211,160,0.2)' : 'rgba(244,63,94,0.2)'}`,
                                    }}>
                                        <div style={{ fontSize: '11px', color: '#8b9cc8', marginBottom: '4px' }}>{m.month}</div>
                                        <div style={{ fontSize: '14px', fontWeight: 700, color: pctColor(m.pnl) }}>{fmtCur(m.pnl)}</div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {analytics.total_trades === 0 && (
                        <div style={{ textAlign: 'center', padding: '60px', color: '#475685' }}>No completed trades yet. Analytics will appear after your first sell.</div>
                    )}
                </div>
            )}

            {/* ─── Settings Tab ─── */}
            {tab === 'settings' && (
                <form onSubmit={handleSaveSettings} style={{ background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(99,120,180,0.18)', borderRadius: '10px', padding: '24px', maxWidth: '600px' }}>
                    <h3 style={{ margin: '0 0 20px', fontSize: '16px' }}>Portfolio Settings (ポートフォリオ設定)</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                        <div><label style={labelStyle}>Name (名前)</label><input style={inputStyle} value={settingsForm.name || ''} onChange={e => setSettingsForm({ ...settingsForm, name: e.target.value })} /></div>
                        <div><label style={labelStyle}>Currency (通貨)</label>
                            <select style={inputStyle} value={settingsForm.currency || 'JPY'} onChange={e => setSettingsForm({ ...settingsForm, currency: e.target.value })}>
                                <option value="JPY">JPY</option><option value="USD">USD</option>
                            </select>
                        </div>
                        <div><label style={labelStyle}>Total Capital (運用資金)</label><input style={inputStyle} type="number" value={settingsForm.total_capital || 0} onChange={e => setSettingsForm({ ...settingsForm, total_capital: Number(e.target.value) })} /></div>
                        <div><label style={labelStyle}>Risk % (リスク許容度%)</label><input style={inputStyle} type="number" step="0.1" value={settingsForm.risk_pct || 1} onChange={e => setSettingsForm({ ...settingsForm, risk_pct: Number(e.target.value) })} /></div>
                        <div><label style={labelStyle}>Stop Loss % (損切り目安%)</label><input style={inputStyle} type="number" step="0.5" value={settingsForm.default_stop_loss_pct || 8} onChange={e => setSettingsForm({ ...settingsForm, default_stop_loss_pct: Number(e.target.value) })} /></div>
                        <div><label style={labelStyle}>Max Positions (最大銘柄数)</label><input style={inputStyle} type="number" value={settingsForm.max_positions || 8} onChange={e => setSettingsForm({ ...settingsForm, max_positions: Number(e.target.value) })} /></div>
                    </div>
                    <div style={{ marginTop: '20px' }}>
                        <button type="submit" style={{ background: 'linear-gradient(135deg, #3b82f6, #2563eb)', border: 'none', color: '#fff', padding: '10px 32px', borderRadius: '8px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>Save Changes</button>
                    </div>
                </form>
            )}
        </div>
    );
};
