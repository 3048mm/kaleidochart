import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Portfolio List Page — Shows all active portfolios with summary stats.
 * Users can create new portfolios or navigate to portfolio detail.
 */

interface PortfolioSummary {
    id: number;
    name: string;
    currency: string;
    total_capital: number;
    risk_pct: number;
    max_positions: number;
    stop_loss_method: string;
    default_stop_loss_pct: number;
    status: string;
}

export const PortfolioListPage: React.FC = () => {
    const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const navigate = useNavigate();

    // Create form state
    const [form, setForm] = useState({
        name: '', currency: 'JPY', total_capital: 3_000_000,
        risk_pct: 1.0, default_stop_loss_pct: 8.0,
        stop_loss_method: 'fixed_pct', max_positions: 8,
    });

    const fetchPortfolios = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/portfolio');
            const data = await res.json();
            setPortfolios(data);
        } catch (err) {
            console.error('Failed to fetch portfolios:', err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchPortfolios(); }, []);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const res = await fetch('/api/portfolio', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(form),
            });
            if (res.ok) {
                setShowCreate(false);
                setForm({ name: '', currency: 'JPY', total_capital: 3_000_000, risk_pct: 1.0, default_stop_loss_pct: 8.0, stop_loss_method: 'fixed_pct', max_positions: 8 });
                await fetchPortfolios();
            }
        } catch (err) {
            console.error('Failed to create portfolio:', err);
        }
    };

    const handleArchive = async (id: number, name: string) => {
        if (!window.confirm(`Archive "${name}"?`)) return;
        try {
            await fetch(`/api/portfolio/${id}`, { method: 'DELETE' });
            await fetchPortfolios();
        } catch (err) {
            console.error('Failed to archive:', err);
        }
    };

    const formatCurrency = (val: number, currency: string) => {
        if (currency === 'JPY') return `¥${val.toLocaleString()}`;
        return `$${val.toLocaleString()}`;
    };

    /* ─── Styles ────────────────────────────────────────────── */
    const cardStyle: React.CSSProperties = {
        background: 'rgba(15, 23, 42, 0.6)',
        border: '1px solid rgba(99, 120, 180, 0.18)',
        borderRadius: '12px',
        padding: '24px',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        position: 'relative',
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

    return (
        <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '28px' }}>
                <div>
                    <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 700,
                        background: 'linear-gradient(135deg, #3b82f6, #22d3a0)',
                        WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                        Portfolio
                    </h1>
                    <p style={{ margin: '4px 0 0', fontSize: '13px', color: '#8b9cc8' }}>
                        {portfolios.length} active portfolio{portfolios.length !== 1 ? 's' : ''}
                    </p>
                </div>
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
                    {showCreate ? 'Cancel' : '＋ New Portfolio'}
                </button>
            </div>

            {/* Create Form */}
            {showCreate && (
                <form onSubmit={handleCreate} style={{
                    ...cardStyle, cursor: 'default', marginBottom: '24px',
                    border: '1px solid rgba(59, 130, 246, 0.3)',
                    boxShadow: '0 0 30px rgba(59, 130, 246, 0.08)',
                }}>
                    <h3 style={{ margin: '0 0 20px', fontSize: '16px', fontWeight: 600 }}>Create New Portfolio</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
                        <div>
                            <label style={labelStyle}>Name</label>
                            <input style={inputStyle} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. スイング" required />
                        </div>
                        <div>
                            <label style={labelStyle}>Currency</label>
                            <select style={inputStyle} value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value })}>
                                <option value="JPY">JPY (¥)</option>
                                <option value="USD">USD ($)</option>
                            </select>
                        </div>
                        <div>
                            <label style={labelStyle}>Total Capital</label>
                            <input style={inputStyle} type="number" value={form.total_capital} onChange={e => setForm({ ...form, total_capital: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Risk %</label>
                            <input style={inputStyle} type="number" step="0.1" value={form.risk_pct} onChange={e => setForm({ ...form, risk_pct: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Stop Loss %</label>
                            <input style={inputStyle} type="number" step="0.5" value={form.default_stop_loss_pct} onChange={e => setForm({ ...form, default_stop_loss_pct: Number(e.target.value) })} />
                        </div>
                        <div>
                            <label style={labelStyle}>Max Positions</label>
                            <input style={inputStyle} type="number" value={form.max_positions} onChange={e => setForm({ ...form, max_positions: Number(e.target.value) })} />
                        </div>
                    </div>
                    <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end' }}>
                        <button type="submit" style={{
                            background: 'linear-gradient(135deg, #22d3a0, #10b981)',
                            border: 'none', color: '#fff', padding: '10px 32px',
                            borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                            cursor: 'pointer',
                        }}>Create Portfolio</button>
                    </div>
                </form>
            )}

            {/* Portfolio Cards */}
            {loading ? (
                <div style={{ textAlign: 'center', padding: '80px', color: '#666' }}>Loading portfolios...</div>
            ) : portfolios.length === 0 ? (
                <div style={{
                    ...cardStyle, cursor: 'default', textAlign: 'center', padding: '80px',
                    border: '1px dashed rgba(99, 120, 180, 0.3)',
                }}>
                    <div style={{ fontSize: '40px', marginBottom: '12px' }}>📊</div>
                    <div style={{ color: '#8b9cc8', fontSize: '15px' }}>No portfolios yet</div>
                    <div style={{ color: '#475685', fontSize: '12px', marginTop: '6px' }}>Create one to start tracking your positions</div>
                </div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '16px' }}>
                    {portfolios.map(pf => (
                        <div
                            key={pf.id}
                            style={cardStyle}
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
                                <div>
                                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 700 }}>{pf.name}</h3>
                                    <span style={{ fontSize: '11px', color: '#8b9cc8' }}>
                                        {pf.stop_loss_method === 'fixed_pct' ? `Fixed ${pf.default_stop_loss_pct}%` : 'ATR Multiple'}
                                    </span>
                                </div>
                                <button
                                    onClick={(e) => { e.stopPropagation(); handleArchive(pf.id, pf.name); }}
                                    style={{
                                        background: 'transparent', border: 'none', color: '#475685',
                                        fontSize: '18px', cursor: 'pointer', padding: '4px',
                                    }}
                                    title="Archive portfolio"
                                >×</button>
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Capital</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                                        {formatCurrency(pf.total_capital, pf.currency)}
                                    </div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Risk</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700 }}>{pf.risk_pct}%</div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Max Positions</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700 }}>{pf.max_positions}</div>
                                </div>
                                <div>
                                    <div style={{ fontSize: '10px', color: '#475685', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Currency</div>
                                    <div style={{ fontSize: '16px', fontWeight: 700 }}>{pf.currency}</div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
