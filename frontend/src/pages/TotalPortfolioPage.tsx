import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';

/**
 * Total Portfolio Page — Master dashboard for all user capital.
 * Displays aggregate performance, cash allocation recommendation, pie charts,
 * transaction history, and sub-portfolio list.
 */

interface SubPortfolio {
    id: number;
    name: string;
    total_capital: number;
    invested: number;
    cash: number;
    market_value: number;
    unrealized_pnl: number;
}

interface TotalSummary {
    total_portfolio_id: number;
    name: string;
    currency: string;
    net_injected_capital: number;
    net_injected_jpy: number;
    total_allocated_capital: number;
    unallocated_cash: number;
    allocated_cash: number;
    total_system_cash: number;
    total_equities_value: number;
    total_equity_value: number;
    total_unrealized_pnl: number;
    total_unrealized_pnl_pct: number;
    total_equity_jpy: number;
    total_unrealized_pnl_jpy: number;
    total_unrealized_pnl_pct_jpy: number;
    current_exchange_rate: number;
    asset_allocation: { name: string, value: number }[];
    theme_breakdown: { name: string, value: number }[];
    ticker_breakdown: { name: string, value: number }[];
    recommended_cash: {
        phase: string;
        trend_score: number;
        recommended_min_pct: number;
        recommended_max_pct: number;
        message: string;
    };
    sub_portfolios: SubPortfolio[];
}

interface BreakdownCardProps {
    title: string;
    data: { name: string; value: number }[];
    totalValue: number;
    currency: string;
}

const BreakdownCard: React.FC<BreakdownCardProps> = ({ title, data, totalValue, currency }) => {
    const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart');
    const COLORS = ['#3b82f6', '#22d3a0', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];

    const formatCurrency = (val: number, c: string) => {
        const curr = c || 'JPY';
        if (curr === 'JPY') return `¥${Math.round(val).toLocaleString()}`;
        return `$${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    };

    return (
        <div style={{
            background: 'rgba(15, 23, 42, 0.6)', border: '1px solid rgba(99, 120, 180, 0.18)',
            borderRadius: '12px', padding: '24px', transition: 'all 0.2s ease', position: 'relative',
            display: 'flex', flexDirection: 'column'
        }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600 }}>{title}</h3>
                <div style={{ display: 'flex', background: 'rgba(30, 40, 70, 0.5)', borderRadius: '4px', overflow: 'hidden' }}>
                    <button
                        type="button"
                        onClick={(e) => { e.preventDefault(); setViewMode('chart'); }}
                        style={{
                            background: viewMode === 'chart' ? '#3b82f6' : 'transparent', color: viewMode === 'chart' ? '#fff' : '#8b9cc8',
                            border: 'none', padding: '4px 12px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', transition: '0.2s'
                        }}
                    >Chart</button>
                    <button
                        type="button"
                        onClick={(e) => { e.preventDefault(); setViewMode('table'); }}
                        style={{
                            background: viewMode === 'table' ? '#3b82f6' : 'transparent', color: viewMode === 'table' ? '#fff' : '#8b9cc8',
                            border: 'none', padding: '4px 12px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', transition: '0.2s'
                        }}
                    >Table</button>
                </div>
            </div>
            
            <div style={{ height: '250px', overflowY: 'auto', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                {data.length === 0 ? (
                    <div style={{ textAlign: 'center', paddingTop: '80px', color: '#666' }}>No data</div>
                ) : viewMode === 'chart' ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                            <Pie data={data} cx="50%" cy="50%" innerRadius={60} outerRadius={80} paddingAngle={5} dataKey="value">
                                {data.map((_, index) => <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />)}
                            </Pie>
                            <Tooltip formatter={(value: any) => formatCurrency(Number(value || 0), currency)} contentStyle={{ background: '#1e293b', border: 'none', borderRadius: '8px', color: '#fff' }} />
                            <Legend wrapperStyle={{ fontSize: '12px' }} />
                        </PieChart>
                    </ResponsiveContainer>
                ) : (
                    <table style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                        <thead style={{ position: 'sticky', top: 0, background: 'rgba(15, 23, 42, 0.95)' }}>
                            <tr style={{ color: '#8b9cc8', textAlign: 'left', borderBottom: '1px solid rgba(99, 120, 180, 0.2)' }}>
                                <th style={{ padding: '8px 4px', fontWeight: 600 }}>Name</th>
                                <th style={{ padding: '8px 4px', fontWeight: 600, textAlign: 'right' }}>Value</th>
                                <th style={{ padding: '8px 4px', fontWeight: 600, textAlign: 'right' }}>Weight</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((item, idx) => (
                                <tr key={idx} style={{ borderBottom: '1px solid rgba(99, 120, 180, 0.1)' }}>
                                    <td style={{ padding: '8px 4px', color: '#e8edf7' }}>{item.name}</td>
                                    <td style={{ padding: '8px 4px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                                        {formatCurrency(item.value, currency)}
                                    </td>
                                    <td style={{ padding: '8px 4px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#22d3a0' }}>
                                        {totalValue > 0 ? ((item.value / totalValue) * 100).toFixed(1) : 0}%
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
};

export const TotalPortfolioPage: React.FC = () => {
    const [summary, setSummary] = useState<TotalSummary | null>(null);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [showTxForm, setShowTxForm] = useState(false);
    const navigate = useNavigate();

    // Create Sub-Portfolio form state
    const [pfForm, setPfForm] = useState({
        name: '', currency: 'USD', total_capital: 30000,
        risk_pct: 1.0, default_stop_loss_pct: 8.0,
        stop_loss_method: 'fixed_pct', max_positions: 8,
    });

    // Transaction form state
    const [txForm, setTxForm] = useState({
        transaction_type: 'DEPOSIT', amount: 0, local_amount: 0, exchange_rate: 150.0,
        date: new Date().toISOString().split('T')[0],
        portfolio_id: '', memo: ''
    });

    const fetchSummary = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/portfolio/total/summary');
            const data = await res.json();
            setSummary(data);
        } catch (err) {
            console.error('Failed to fetch total portfolio summary:', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchSummary(); }, []);

    useEffect(() => {
        const fetchHistoricalRate = async () => {
            if (txForm.transaction_type === 'DEPOSIT' || txForm.transaction_type === 'WITHDRAWAL') {
                try {
                    const res = await fetch(`/api/portfolio/total/fx-rate?date=${txForm.date}`);
                    if (res.ok) {
                        const data = await res.json();
                        setTxForm(prev => ({ ...prev, exchange_rate: data.exchange_rate }));
                    }
                } catch (err) {
                    console.error('Failed to fetch historical fx rate', err);
                }
            }
        };
        fetchHistoricalRate();
    }, [txForm.date, txForm.transaction_type]);

    const handleCreatePortfolio = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const res = await fetch('/api/portfolio', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(pfForm),
            });
            if (res.ok) {
                setShowCreate(false);
                setPfForm({ name: '', currency: 'USD', total_capital: 30000, risk_pct: 1.0, default_stop_loss_pct: 8.0, stop_loss_method: 'fixed_pct', max_positions: 8 });
                await fetchSummary();
            }
        } catch (err) {
            console.error('Failed to create portfolio:', err);
        }
    };

    const handleArchive = async (id: number, name: string) => {
        if (!window.confirm(`Archive "${name}"?`)) return;
        try {
            await fetch(`/api/portfolio/${id}`, { method: 'DELETE' });
            await fetchSummary();
        } catch (err) {
            console.error('Failed to archive:', err);
        }
    };

    const handleTransaction = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const isExternal = txForm.transaction_type === 'DEPOSIT' || txForm.transaction_type === 'WITHDRAWAL';
            const payload = {
                ...txForm,
                local_amount: isExternal ? txForm.local_amount : null,
                exchange_rate: isExternal ? txForm.exchange_rate : null,
                local_currency: isExternal ? 'JPY' : null,
                amount: isExternal ? (txForm.local_amount / txForm.exchange_rate) : txForm.amount,
                portfolio_id: txForm.portfolio_id ? parseInt(txForm.portfolio_id) : null
            };
            const res = await fetch('/api/portfolio/total/transactions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (res.ok) {
                setShowTxForm(false);
                setTxForm({ transaction_type: 'DEPOSIT', amount: 0, local_amount: 0, exchange_rate: summary?.current_exchange_rate || 150.0, date: new Date().toISOString().split('T')[0], portfolio_id: '', memo: '' });
                await fetchSummary();
            }
        } catch (err) {
            console.error('Failed to create transaction:', err);
        }
    };

    const formatCurrency = (val: number, currency: string) => {
        const c = currency || 'JPY';
        if (c === 'JPY') return `¥${Math.round(val).toLocaleString()}`;
        return `$${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    };

    /* ─── Styles ────────────────────────────────────────────── */
    const cardStyle: React.CSSProperties = {
        background: 'rgba(15, 23, 42, 0.6)',
        border: '1px solid rgba(99, 120, 180, 0.18)',
        borderRadius: '12px',
        padding: '24px',
        transition: 'all 0.2s ease',
        position: 'relative',
    };

    const subCardStyle: React.CSSProperties = {
        ...cardStyle,
        cursor: 'pointer',
    };

    const inputStyle: React.CSSProperties = {
        background: 'rgba(30, 40, 70, 0.5)',
        border: '1px solid rgba(99, 120, 180, 0.25)',
        borderRadius: '6px',
        padding: '8px 12px',
        color: '#e8edf7',
        fontSize: '13px',
        width: '100%',
        outline: 'none',
    };

    const labelStyle: React.CSSProperties = {
        fontSize: '11px', fontWeight: 600, color: '#8b9cc8',
        textTransform: 'uppercase' as const, letterSpacing: '0.06em',
        marginBottom: '4px', display: 'block',
    };

    if (loading || !summary) {
        return <div style={{ textAlign: 'center', padding: '80px', color: '#666' }}>Loading Total Portfolio Dashboard...</div>;
    }

    const currentCashPct = summary.total_equity_value > 0 ? (summary.total_system_cash / summary.total_equity_value) * 100 : 0;
    const isCashOk = currentCashPct >= summary.recommended_cash.recommended_min_pct && currentCashPct <= summary.recommended_cash.recommended_max_pct;

    return (
        <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px', marginBottom: '28px' }}>
                <div>
                    <h1 style={{ margin: 0, fontSize: '28px', fontWeight: 700,
                        background: 'linear-gradient(135deg, #3b82f6, #22d3a0)',
                        WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                        Total Portfolio Dashboard
                    </h1>
                    <p style={{ margin: '4px 0 0', fontSize: '14px', color: '#8b9cc8' }}>
                        {summary.name}
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                    <button
                        onClick={() => setShowTxForm(!showTxForm)}
                        style={{
                            background: 'rgba(59, 130, 246, 0.1)',
                            border: '1px solid rgba(59, 130, 246, 0.5)', color: '#60a5fa', padding: '10px 24px',
                            borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                            cursor: 'pointer', transition: 'all 0.2s',
                        }}
                    >
                        {showTxForm ? 'Cancel Transaction' : '⇄ Deposit / Fund'}
                    </button>
                    <button
                        onClick={() => setShowCreate(!showCreate)}
                        style={{
                            background: 'linear-gradient(135deg, #3b82f6, #2563eb)',
                            border: 'none', color: '#fff', padding: '10px 24px',
                            borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                            cursor: 'pointer', transition: 'all 0.2s',
                            boxShadow: '0 4px 15px rgba(59, 130, 246, 0.3)',
                        }}
                    >
                        {showCreate ? 'Cancel Portfolio' : '＋ New Sub-Portfolio'}
                    </button>
                </div>
            </div>

            {/* Master Metrics */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px', marginBottom: '24px' }}>
                <div style={cardStyle}>
                    <div style={labelStyle}>Total Equity (総評価額)</div>
                    <div style={{ fontSize: '24px', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                        {formatCurrency(summary.total_equity_value, 'USD')}
                    </div>
                    <div style={{ fontSize: '13px', color: '#8b9cc8', marginTop: '4px' }}>
                        ({formatCurrency(summary.total_equity_jpy, 'JPY')})
                    </div>
                </div>
                <div style={cardStyle}>
                    <div style={labelStyle}>Net Injected (入金累計)</div>
                    <div style={{ fontSize: '24px', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                        {formatCurrency(summary.net_injected_capital, 'USD')}
                    </div>
                    <div style={{ fontSize: '13px', color: '#8b9cc8', marginTop: '4px' }}>
                        ({formatCurrency(summary.net_injected_jpy, 'JPY')})
                    </div>
                </div>
                <div style={cardStyle}>
                    <div style={labelStyle}>Total System Cash (待機資金)</div>
                    <div style={{ fontSize: '24px', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: '#22d3a0' }}>
                        {formatCurrency(summary.total_system_cash, 'USD')}
                    </div>
                </div>
                <div style={cardStyle}>
                    <div style={labelStyle}>Total P&L (総利益)</div>
                    <div style={{ fontSize: '24px', fontWeight: 700, fontVariantNumeric: 'tabular-nums', color: summary.total_unrealized_pnl >= 0 ? '#22d3a0' : '#ef4444' }}>
                        {summary.total_unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(summary.total_unrealized_pnl, 'USD')} ({summary.total_unrealized_pnl >= 0 ? '+' : ''}{summary.total_unrealized_pnl_pct.toFixed(2)}%)
                    </div>
                    <div style={{ fontSize: '13px', color: summary.total_unrealized_pnl_jpy >= 0 ? '#22d3a0' : '#ef4444', marginTop: '4px' }}>
                        ({summary.total_unrealized_pnl_jpy >= 0 ? '+' : ''}{formatCurrency(summary.total_unrealized_pnl_jpy, 'JPY')} / {summary.total_unrealized_pnl_pct_jpy.toFixed(2)}%)
                    </div>
                </div>
            </div>

            {/* Cash Recommendation Alert */}
            <div style={{
                background: isCashOk ? 'rgba(34, 211, 160, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                border: `1px solid ${isCashOk ? 'rgba(34, 211, 160, 0.4)' : 'rgba(239, 68, 68, 0.4)'}`,
                borderRadius: '8px', padding: '16px 24px', marginBottom: '24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between'
            }}>
                <div>
                    <h4 style={{ margin: '0 0 4px', color: isCashOk ? '#22d3a0' : '#ef4444' }}>
                        Market Phase: {summary.recommended_cash.phase} (Score: {summary.recommended_cash.trend_score.toFixed(1)})
                    </h4>
                    <p style={{ margin: 0, fontSize: '13px', color: '#e8edf7' }}>
                        {summary.recommended_cash.message} Current cash ratio: <b>{currentCashPct.toFixed(1)}%</b>.
                    </p>
                </div>
                <div>
                    {!isCashOk && (
                        <span style={{ background: '#ef4444', color: '#fff', padding: '4px 8px', borderRadius: '4px', fontSize: '12px', fontWeight: 700 }}>
                            ACTION RECOMMENDED
                        </span>
                    )}
                </div>
            </div>

            {/* Transaction Form */}
            {showTxForm && (
                <form onSubmit={handleTransaction} style={{
                    ...cardStyle, marginBottom: '24px', border: '1px solid rgba(96, 165, 250, 0.5)',
                    boxShadow: '0 0 30px rgba(96, 165, 250, 0.1)',
                }}>
                    <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>Execute Transaction</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px' }}>
                        <div>
                            <label style={labelStyle}>Type</label>
                            <select style={inputStyle} value={txForm.transaction_type} onChange={e => {
                                const newType = e.target.value;
                                setTxForm(prev => ({ 
                                    ...prev, 
                                    transaction_type: newType,
                                    exchange_rate: (newType === 'DEPOSIT' || newType === 'WITHDRAWAL') ? (summary?.current_exchange_rate || prev.exchange_rate) : prev.exchange_rate
                                }));
                            }}>
                                <option value="DEPOSIT">DEPOSIT (To Master)</option>
                                <option value="WITHDRAWAL">WITHDRAWAL (From Master)</option>
                                <option value="FUNDING">FUNDING (To Sub-Portfolio)</option>
                                <option value="REFUND">REFUND (From Sub-Portfolio)</option>
                            </select>
                        </div>
                        {(txForm.transaction_type === 'DEPOSIT' || txForm.transaction_type === 'WITHDRAWAL') ? (
                            <>
                                <div>
                                    <label style={labelStyle}>Amount (JPY)</label>
                                    <input style={inputStyle} type="number" step="1" value={txForm.local_amount} onChange={e => setTxForm({ ...txForm, local_amount: Number(e.target.value) })} required />
                                </div>
                                <div>
                                    <label style={labelStyle}>FX Rate (USD/JPY)</label>
                                    <input style={inputStyle} type="number" step="0.01" value={txForm.exchange_rate} onChange={e => setTxForm({ ...txForm, exchange_rate: Number(e.target.value) })} required />
                                </div>
                            </>
                        ) : (
                            <div>
                                <label style={labelStyle}>Amount (USD)</label>
                                <input style={inputStyle} type="number" step="0.01" value={txForm.amount} onChange={e => setTxForm({ ...txForm, amount: Number(e.target.value) })} required />
                            </div>
                        )}
                        <div>
                            <label style={labelStyle}>Date</label>
                            <input style={inputStyle} type="date" value={txForm.date} onChange={e => setTxForm({ ...txForm, date: e.target.value })} required />
                        </div>
                        <div>
                            <label style={labelStyle}>Target Portfolio</label>
                            <select style={inputStyle} value={txForm.portfolio_id} onChange={e => setTxForm({ ...txForm, portfolio_id: e.target.value })} disabled={txForm.transaction_type === 'DEPOSIT' || txForm.transaction_type === 'WITHDRAWAL'}>
                                <option value="">-- None --</option>
                                {summary.sub_portfolios.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                            </select>
                        </div>
                        <div>
                            <label style={labelStyle}>Memo</label>
                            <input style={inputStyle} value={txForm.memo} onChange={e => setTxForm({ ...txForm, memo: e.target.value })} placeholder="Optional" />
                        </div>
                    </div>
                    <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end' }}>
                        <button type="submit" style={{
                            background: 'linear-gradient(135deg, #60a5fa, #3b82f6)', border: 'none', color: '#fff', padding: '10px 32px',
                            borderRadius: '8px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                        }}>Submit Transaction</button>
                    </div>
                </form>
            )}

            {/* Create Portfolio Form */}
            {showCreate && (
                <form onSubmit={handleCreatePortfolio} style={{
                    ...cardStyle, cursor: 'default', marginBottom: '24px', border: '1px solid rgba(59, 130, 246, 0.3)',
                    boxShadow: '0 0 30px rgba(59, 130, 246, 0.08)',
                }}>
                    <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>Create New Sub-Portfolio</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px' }}>
                        <div>
                            <label style={labelStyle}>Name</label>
                            <input style={inputStyle} value={pfForm.name} onChange={e => setPfForm({ ...pfForm, name: e.target.value })} placeholder="e.g. スイング" required />
                        </div>
                        <div>
                            <label style={labelStyle}>Currency</label>
                            <select style={inputStyle} value={pfForm.currency} disabled>
                                <option value="USD">USD ($)</option>
                            </select>
                        </div>
                        <div>
                            <label style={labelStyle}>Initial Funding Capital</label>
                            <input style={inputStyle} type="number" value={pfForm.total_capital} onChange={e => setPfForm({ ...pfForm, total_capital: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Risk %</label>
                            <input style={inputStyle} type="number" step="0.1" value={pfForm.risk_pct} onChange={e => setPfForm({ ...pfForm, risk_pct: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Stop Loss %</label>
                            <input style={inputStyle} type="number" step="0.5" value={pfForm.default_stop_loss_pct} onChange={e => setPfForm({ ...pfForm, default_stop_loss_pct: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Max Positions</label>
                            <input style={inputStyle} type="number" value={pfForm.max_positions} onChange={e => setPfForm({ ...pfForm, max_positions: Number(e.target.value) })} />
                        </div>
                    </div>
                    <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end' }}>
                        <button type="submit" style={{
                            background: 'linear-gradient(135deg, #22d3a0, #10b981)', border: 'none', color: '#fff', padding: '10px 32px',
                            borderRadius: '8px', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                        }}>Create Sub-Portfolio</button>
                    </div>
                </form>
            )}

            {/* Pie Charts / Tables */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px', marginBottom: '24px' }}>
                <BreakdownCard
                    title="Asset Allocation (Cash vs Equity)"
                    data={summary.asset_allocation}
                    totalValue={summary.total_equity_value}
                    currency={summary.currency}
                />
                <BreakdownCard
                    title="Theme Allocation"
                    data={summary.theme_breakdown}
                    totalValue={summary.total_equities_value}
                    currency={summary.currency}
                />
                <BreakdownCard
                    title="Ticker Allocation"
                    data={summary.ticker_breakdown}
                    totalValue={summary.total_equities_value}
                    currency={summary.currency}
                />
            </div>

            {/* Sub-Portfolios List */}
            <h2 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>Sub-Portfolios</h2>
            {summary.sub_portfolios.length === 0 ? (
                <div style={{ ...cardStyle, textAlign: 'center', padding: '60px', border: '1px dashed rgba(99, 120, 180, 0.3)' }}>
                    <div style={{ fontSize: '40px', marginBottom: '12px' }}>📊</div>
                    <div style={{ color: '#8b9cc8', fontSize: '15px' }}>No sub-portfolios yet</div>
                    <div style={{ color: '#475685', fontSize: '12px', marginTop: '6px' }}>Create one to start tracking your positions</div>
                </div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '16px' }}>
                    {summary.sub_portfolios.map(pf => (
                        <div
                            key={pf.id}
                            style={subCardStyle}
                            onClick={() => navigate(`/portfolio/${pf.id}`)}
                            onMouseEnter={e => {
                                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(59, 130, 246, 0.4)';
                                (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 30px rgba(59, 130, 246, 0.1)';
                            }}
                            onMouseLeave={e => {
                                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(99, 120, 180, 0.18)';
                                (e.currentTarget as HTMLDivElement).style.boxShadow = 'none';
                            }}
                        >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
                                <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 700 }}>{pf.name}</h3>
                                <button
                                    onClick={(e) => { e.stopPropagation(); handleArchive(pf.id, pf.name); }}
                                    style={{ background: 'transparent', border: 'none', color: '#475685', fontSize: '18px', cursor: 'pointer', padding: '4px' }}
                                    title="Archive portfolio"
                                >×</button>
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Funded Capital</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                                        {formatCurrency(pf.total_capital, summary.currency)}
                                    </div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Available Cash</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700, color: '#22d3a0' }}>{formatCurrency(pf.cash, summary.currency)}</div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Invested</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700 }}>{formatCurrency(pf.invested, summary.currency)}</div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Unrealized P&L</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700, color: pf.unrealized_pnl >= 0 ? '#22d3a0' : '#ef4444' }}>
                                        {pf.unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(pf.unrealized_pnl, summary.currency)}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
