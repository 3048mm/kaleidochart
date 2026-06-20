import React, { useState, useEffect, useMemo } from 'react';
import {
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ComposedChart,
} from 'recharts';
import {
  fetchEtfSingleSummary,
  fetchEtfSingleEquity,
  fetchEtfSingleRegimes,
  EtfSingleSummary,
  EtfSingleEquityPoint,
  EtfSingleRegimeItem,
  EtfStrategyResult,
} from '../api/backtest';

// ============================================================
// Color & Style Constants
// ============================================================

const COLORS = {
  vxv: '#22d3a0',       // Green — VXV Strategy
  mts: '#a855f7',       // Purple — MTS Strategy
  buyhold: '#3b82f6',   // Blue — Buy & Hold
  dca: '#f59e0b',       // Amber — DCA
  based_sma200: '#38bdf8', // Sky Blue — Based ETF from SMA200
  based_sma63: '#818cf8',  // Indigo — Based ETF from SMA63
  option_a: '#ec4899',  // Pink — Option A (Perfect Order)
  option_c: '#ef4444',  // Red — Option C (Strict Zero)
  option_d: '#06b6d4',  // Cyan — Option D
  regime_bull: 'rgba(34, 211, 160, 0.08)',
  regime_bear: 'rgba(244, 63, 94, 0.08)',
  regime_bottom: 'rgba(168, 85, 247, 0.08)',
  regime_overheat: 'rgba(245, 158, 11, 0.08)',
};

const REGIME_BADGE: Record<string, { bg: string; color: string; label: string }> = {
  BULL:     { bg: 'rgba(34, 211, 160, 0.15)', color: '#22d3a0', label: '🟢 Bull' },
  BEAR:     { bg: 'rgba(244, 63, 94, 0.15)',  color: '#f43f5e', label: '🔴 Bear' },
  BOTTOM:   { bg: 'rgba(168, 85, 247, 0.15)', color: '#a855f7', label: '🟣 Bottom' },
  OVERHEAT: { bg: 'rgba(245, 158, 11, 0.15)', color: '#f59e0b', label: '🟡 Overheat' },
};

const AVAILABLE_TICKERS = ['SPY', 'QQQ', 'TQQQ', 'SOXL', 'UGL'];

// ============================================================
// Formatter Utils
// ============================================================

const fmt = (n: number, decimals = 2) => n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
const fmtPct = (n: number) => `${n >= 0 ? '+' : ''}${fmt(n)}%`;
const fmtDollar = (n: number) => `$${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

// ============================================================
// Sub-Components
// ============================================================

/** Single Strategy Card */
const StrategyCard: React.FC<{
  name: string;
  label: string;
  color: string;
  result: EtfStrategyResult;
  isBest: boolean;
}> = ({ label, color, result, isBest }) => (
  <div style={{
    flex: 1,
    background: isBest ? `linear-gradient(135deg, ${color}12, ${color}05)` : 'rgba(15, 23, 42, 0.5)',
    border: `1px solid ${isBest ? color + '55' : 'rgba(99, 120, 180, 0.18)'}`,
    borderRadius: 12,
    padding: '20px 22px',
    position: 'relative',
    transition: 'all 0.3s',
  }}>
    {isBest && (
      <span style={{
        position: 'absolute', top: 8, right: 10,
        fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
        background: color, color: '#000', borderRadius: 4, padding: '2px 7px',
      }}>BEST CAGR</span>
    )}
    <div style={{ fontSize: 11, fontWeight: 600, color: color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>{label}</div>
    <div style={{ fontSize: 26, fontWeight: 700, color: '#e8edf7', marginBottom: 4 }}>{fmtDollar(result.final_capital)}</div>
    <div style={{ fontSize: 12, color: result.total_return_pct >= 0 ? '#22d3a0' : '#f43f5e', fontWeight: 600, marginBottom: 16 }}>
      {fmtPct(result.total_return_pct)}
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
      <MetricRow label="CAGR" value={fmtPct(result.cagr)} positive={result.cagr >= 0} />
      <MetricRow label="Max DD" value={fmtPct(result.max_drawdown_pct)} positive={false} />
      <MetricRow label="Sharpe" value={fmt(result.sharpe_ratio)} positive={result.sharpe_ratio >= 1} />
      {result.time_in_market_pct != null && (
        <MetricRow label="Time in Market" value={`${fmt(result.time_in_market_pct, 1)}%`} positive />
      )}
      {result.regime_changes != null && (
        <MetricRow label="Regime Changes" value={String(result.regime_changes)} positive />
      )}
      {result.rebalance_count != null && (
        <MetricRow label="Rebalances" value={String(result.rebalance_count)} positive />
      )}
    </div>
  </div>
);

const MetricRow: React.FC<{ label: string; value: string; positive?: boolean }> = ({ label, value, positive }) => (
  <div>
    <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685', marginBottom: 2 }}>{label}</div>
    <div style={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: positive === false ? '#f43f5e' : '#e8edf7' }}>{value}</div>
  </div>
);


/** Custom Tooltip for Equity Chart */
const EquityTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const regimeItem = payload.find((p: any) => p.name === 'regime');
  const regime = regimeItem?.value || payload[0]?.payload?.regime;
  const badge = regime ? REGIME_BADGE[regime] : null;

  return (
    <div style={{
      background: 'rgba(10, 15, 30, 0.95)', border: '1px solid rgba(99, 120, 180, 0.3)',
      borderRadius: 8, padding: '10px 14px', fontSize: 12,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
        {label}
        {badge && <span style={{ fontSize: 10, background: badge.bg, color: badge.color, borderRadius: 4, padding: '1px 6px' }}>{badge.label}</span>}
      </div>
      {payload.filter((p: any) => p.name !== 'regime').map((p: any) => (
        <div key={p.name} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
          <span style={{ color: p.color || '#8b9cc8' }}>{p.name}</span>
          <span style={{ fontWeight: 600, color: '#e8edf7' }}>{typeof p.value === 'number' ? fmtDollar(p.value) : p.value}</span>
        </div>
      ))}
    </div>
  );
};


// ============================================================
// Main Page Component
// ============================================================

export const EtfSingleBacktestPage: React.FC<{ hideHeader?: boolean }> = ({ hideHeader }) => {
  const [ticker, setTicker] = useState('SPY');
  const [summary, setSummary] = useState<EtfSingleSummary | null>(null);
  const [equityData, setEquityData] = useState<EtfSingleEquityPoint[]>([]);
  const [regimeData, setRegimeData] = useState<EtfSingleRegimeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Chart toggles
  const [showVxv, setShowVxv] = useState(true);
  const [showMts, setShowMts] = useState(true);
  const [showOptionA, setShowOptionA] = useState(false);
  const [showOptionC, setShowOptionC] = useState(false);
  const [showOptionD, setShowOptionD] = useState(true);
  const [showBasedSma200, setShowBasedSma200] = useState(true);
  const [showBasedSma63, setShowBasedSma63] = useState(false);
  const [showBuyHold, setShowBuyHold] = useState(true);
  const [showDca, setShowDca] = useState(true);

  // Load data
  const loadData = async (t: string) => {
    setLoading(true);
    setError('');
    try {
      const [sum, eq, reg] = await Promise.all([
        fetchEtfSingleSummary(t),
        fetchEtfSingleEquity(t),
        fetchEtfSingleRegimes(t),
      ]);
      setSummary(sum);
      setEquityData(eq);
      setRegimeData(reg);
    } catch (err: any) {
      setError(`${t} のバックテスト結果が見つかりません。先にバックテストを実行してください。`);
      setSummary(null);
      setEquityData([]);
      setRegimeData([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(ticker); }, [ticker]);

  // Yearly returns table data
  const yearlyData = useMemo(() => {
    if (!summary) return [];
    const vxvYears = summary.strategies.vxv_vix_ema?.yearly_returns || {};
    const mtsYears = summary.strategies.mts_v2?.yearly_returns || {};
    const optAYears = summary.strategies.option_a?.yearly_returns || {};
    const optCYears = summary.strategies.option_c_strict?.yearly_returns || {};
    const optDYears = summary.strategies.option_d?.yearly_returns || {};
    const basedYears = summary.strategies.based_sma200?.yearly_returns || {};
    const based63Years = summary.strategies.based_sma63?.yearly_returns || {};
    const bhYears = summary.strategies.buy_and_hold?.yearly_returns || {};
    const dcaYears = summary.strategies.dca?.yearly_returns || {};
    const allYears = [...new Set([
      ...Object.keys(vxvYears),
      ...Object.keys(mtsYears),
      ...Object.keys(optAYears),
      ...Object.keys(optCYears),
      ...Object.keys(optDYears),
      ...Object.keys(basedYears),
      ...Object.keys(based63Years),
      ...Object.keys(bhYears),
      ...Object.keys(dcaYears)
    ])].sort();

    return allYears.map(year => {
      const vxv = vxvYears[year] ?? null;
      const mts = mtsYears[year] ?? null;
      const optA = optAYears[year] ?? null;
      const optC = optCYears[year] ?? null;
      const optD = optDYears[year] ?? null;
      const based = basedYears[year] ?? null;
      const based63 = based63Years[year] ?? null;
      const bh = bhYears[year] ?? null;
      const dca = dcaYears[year] ?? null;
      const vals = [vxv, mts, optA, optC, optD, based, based63, bh, dca].filter(v => v !== null) as number[];
      const best = vals.length > 0 ? Math.max(...vals) : null;
      return { year, vxv, mts, optA, optC, optD, based, based63, bh, dca, best };
    });
  }, [summary]);

  // Sampled equity for chart (limit points)
  const chartEquity = useMemo(() => {
    if (equityData.length <= 500) return equityData;
    const step = Math.ceil(equityData.length / 500);
    return equityData.filter((_, i) => i % step === 0 || i === equityData.length - 1);
  }, [equityData]);

  // ============================================================
  // Render
  // ============================================================

  const containerStyle: React.CSSProperties = {
    height: '100%', overflowY: 'auto', overflowX: 'hidden',
    padding: hideHeader ? '0' : '24px 28px',
  };

  const sectionTitle: React.CSSProperties = {
    fontSize: 14, fontWeight: 700, letterSpacing: '0.04em', color: '#e8edf7',
    marginBottom: 14, marginTop: 28, display: 'flex', alignItems: 'center', gap: 8,
  };

  const glassPanel: React.CSSProperties = {
    background: 'rgba(15, 23, 42, 0.5)', border: '1px solid rgba(99, 120, 180, 0.18)',
    borderRadius: 12, padding: 20, backdropFilter: 'blur(10px)',
  };

  return (
    <div style={containerStyle}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16, marginBottom: 20 }}>
        {!hideHeader ? (
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e8edf7', margin: 0 }}>
              <span style={{ background: 'linear-gradient(135deg, #3b82f6, #22d3a0)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                ETF Regime Backtest
              </span>
            </h1>
            <p style={{ fontSize: 12, color: '#8b9cc8', marginTop: 4 }}>Regime-based position sizing (VXV/VIX EMA & MTS) vs Buy & Hold vs DCA</p>
          </div>
        ) : <div />}

        {/* Ticker Selector */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {AVAILABLE_TICKERS.map(t => (
            <button
              key={t}
              onClick={() => setTicker(t)}
              style={{
                padding: '7px 16px', fontSize: 12, fontWeight: 600, fontFamily: 'inherit',
                borderRadius: 8, cursor: 'pointer', transition: 'all 0.2s',
                background: ticker === t ? 'rgba(59, 130, 246, 0.2)' : 'rgba(30, 40, 70, 0.5)',
                border: `1px solid ${ticker === t ? 'rgba(59, 130, 246, 0.5)' : 'rgba(99, 120, 180, 0.18)'}`,
                color: ticker === t ? '#3b82f6' : '#8b9cc8',
              }}
            >{t}</button>
          ))}
        </div>
      </div>

      {/* Loading / Error */}
      {loading && (
        <div className="state-center" style={{ height: 300 }}>
          <div className="spinner" />
          <span>Loading {ticker} backtest...</span>
        </div>
      )}

      {error && !loading && (
        <div style={{ ...glassPanel, textAlign: 'center', padding: 40, color: '#8b9cc8' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
          <div style={{ fontSize: 14, marginBottom: 8 }}>{error}</div>
          <div style={{ fontSize: 11, color: '#475685' }}>
            Run: <code style={{ background: 'rgba(59,130,246,0.1)', padding: '2px 6px', borderRadius: 4 }}>
              python -m backend.backtest.etf_single_runner --ticker {ticker} --start-date 2010-01-01 --end-date 2025-12-31 --capital 100000 --tax 0.20
            </code>
          </div>
        </div>
      )}

      {!loading && summary && (
        <>
          {/* Period Info */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 20 }}>
            {[
              { label: 'Period', value: `${summary.start_date} → ${summary.end_date}` },
              { label: 'Trading Days', value: summary.trading_days.toLocaleString() },
              { label: 'Initial Capital', value: fmtDollar(summary.initial_capital) },
              { label: 'Tax Rate', value: `${(summary.consider_tax * 100).toFixed(0)}%` },
            ].map(item => (
              <div key={item.label} style={{
                background: 'rgba(15, 23, 42, 0.4)', borderRadius: 8, padding: '8px 14px',
                border: '1px solid rgba(99, 120, 180, 0.12)',
              }}>
                <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>{item.label}</div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#e8edf7', fontVariantNumeric: 'tabular-nums' }}>{item.value}</div>
              </div>
            ))}
          </div>

          {/* Strategy Comparison Cards */}
          <div style={sectionTitle}>Strategy Comparison</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
            {(() => {
              const strategyConfigs = [
                { key: 'buy_and_hold' as const, label: 'Buy & Hold', color: COLORS.buyhold },
                { key: 'dca' as const, label: 'DCA (Monthly)', color: COLORS.dca },
                ...(summary.strategies.based_sma200 ? [{ key: 'based_sma200' as const, label: 'Based ETF from SMA200', color: COLORS.based_sma200 }] : []),
                ...(summary.strategies.based_sma63 ? [{ key: 'based_sma63' as const, label: 'Based ETF from SMA63', color: COLORS.based_sma63 }] : []),
                { key: 'vxv_vix_ema' as const, label: 'VXV ratio', color: COLORS.vxv },
                ...(summary.strategies.mts_v2 ? [{ key: 'mts_v2' as const, label: 'MTS v2', color: COLORS.mts }] : []),
                ...(summary.strategies.option_d ? [{ key: 'option_d' as const, label: 'MTS v3 (Option D)', color: COLORS.option_d }] : []),
                ...(summary.strategies.option_a ? [{ key: 'option_a' as const, label: 'Option A (Perfect)', color: COLORS.option_a }] : []),
                ...(summary.strategies.option_c_strict ? [{ key: 'option_c_strict' as const, label: 'Option C (Strict)', color: COLORS.option_c }] : []),
              ];
              const bestCagr = Math.max(...strategyConfigs.map(s => summary.strategies[s.key]?.cagr || 0));
              return strategyConfigs.map(s => {
                const result = summary.strategies[s.key];
                if (!result) return null;
                return (
                  <StrategyCard
                    key={s.key} name={s.key} label={s.label}
                    color={s.color} result={result}
                    isBest={result.cagr === bestCagr}
                  />
                );
              });
            })()}
          </div>

          {/* Equity Curve Chart */}
          <div style={sectionTitle}>
            Equity Curve
            <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', flexWrap: 'wrap' }}>
              {[
                { label: 'B&H', show: showBuyHold, set: setShowBuyHold, color: COLORS.buyhold },
                { label: 'DCA', show: showDca, set: setShowDca, color: COLORS.dca },
                ...(summary.strategies.based_sma200 ? [{ label: 'Based SMA200', show: showBasedSma200, set: setShowBasedSma200, color: COLORS.based_sma200 }] : []),
                ...(summary.strategies.based_sma63 ? [{ label: 'Based SMA63', show: showBasedSma63, set: setShowBasedSma63, color: COLORS.based_sma63 }] : []),
                { label: 'VXV', show: showVxv, set: setShowVxv, color: COLORS.vxv },
                ...(summary.strategies.mts_v2 ? [{ label: 'MTS', show: showMts, set: setShowMts, color: COLORS.mts }] : []),
                ...(summary.strategies.option_d ? [{ label: 'Opt D', show: showOptionD, set: setShowOptionD, color: COLORS.option_d }] : []),
                ...(summary.strategies.option_a ? [{ label: 'Opt A', show: showOptionA, set: setShowOptionA, color: COLORS.option_a }] : []),
                ...(summary.strategies.option_c_strict ? [{ label: 'Opt C', show: showOptionC, set: setShowOptionC, color: COLORS.option_c }] : []),
              ].map(t => (
                <button key={t.label} onClick={() => t.set(!t.show)} style={{
                  padding: '3px 10px', fontSize: 10, fontWeight: 600, fontFamily: 'inherit',
                  borderRadius: 6, cursor: 'pointer', transition: 'all 0.15s',
                  background: t.show ? `${t.color}25` : 'transparent',
                  border: `1px solid ${t.show ? t.color + '55' : 'rgba(99, 120, 180, 0.18)'}`,
                  color: t.show ? t.color : '#475685',
                }}>{t.label}</button>
              ))}
            </div>
          </div>
          <div style={{ ...glassPanel, padding: '16px 10px 10px' }}>
            <ResponsiveContainer width="100%" height={380}>
              <ComposedChart data={chartEquity} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(99, 120, 180, 0.1)" />
                <XAxis
                  dataKey="date" tick={{ fontSize: 10, fill: '#475685' }}
                  tickFormatter={(v: string) => v.substring(0, 7)}
                  interval={Math.floor(chartEquity.length / 8)}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: '#475685' }}
                  tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                  width={55}
                />
                <Tooltip content={<EquityTooltip />} />
                {showBuyHold && <Line type="monotone" dataKey="buyhold_equity" name="Buy & Hold" stroke={COLORS.buyhold} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />}
                {showDca && <Line type="monotone" dataKey="dca_equity" name="DCA" stroke={COLORS.dca} strokeWidth={1.5} dot={false} strokeDasharray="6 3" />}
                {summary.strategies.based_sma200 && showBasedSma200 && <Area type="monotone" dataKey="based_sma200_equity" name="Based ETF from SMA200" stroke={COLORS.based_sma200} fill={COLORS.based_sma200} fillOpacity={0.08} strokeWidth={2} dot={false} />}
                {summary.strategies.based_sma63 && showBasedSma63 && <Area type="monotone" dataKey="based_sma63_equity" name="Based ETF from SMA63" stroke={COLORS.based_sma63} fill={COLORS.based_sma63} fillOpacity={0.08} strokeWidth={2} dot={false} />}
                {showVxv && <Area type="monotone" dataKey="vxv_equity" name="VXV Strategy" stroke={COLORS.vxv} fill={COLORS.vxv} fillOpacity={0.08} strokeWidth={2} dot={false} />}
                {summary.strategies.mts_v2 && showMts && <Area type="monotone" dataKey="mts_v2_equity" name="Market Trend Score" stroke={COLORS.mts} fill={COLORS.mts} fillOpacity={0.08} strokeWidth={2} dot={false} />}
                {summary.strategies.option_d && showOptionD && <Area type="monotone" dataKey="option_d_equity" name="Option D" stroke={COLORS.option_d} fill={COLORS.option_d} fillOpacity={0.08} strokeWidth={2} dot={false} />}
                {summary.strategies.option_a && showOptionA && <Line type="monotone" dataKey="option_a_equity" name="Option A" stroke={COLORS.option_a} strokeWidth={1.5} dot={false} />}
                {summary.strategies.option_c_strict && showOptionC && <Line type="monotone" dataKey="option_c_equity" name="Option C" stroke={COLORS.option_c} strokeWidth={1.5} dot={false} />}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Regime Timeline */}
          <div style={sectionTitle}>Regime Timeline</div>
          <div style={{ ...glassPanel, padding: '12px 10px' }}>
            <div style={{ display: 'flex', height: 28, borderRadius: 6, overflow: 'hidden', position: 'relative' }}>
              {regimeData.length > 0 && (() => {
                // Group consecutive regime blocks
                const blocks: { regime: string; start: number; end: number }[] = [];
                let cur = regimeData[0].regime;
                let startIdx = 0;
                for (let i = 1; i <= regimeData.length; i++) {
                  if (i === regimeData.length || regimeData[i].regime !== cur) {
                    blocks.push({ regime: cur, start: startIdx, end: i - 1 });
                    if (i < regimeData.length) {
                      cur = regimeData[i].regime;
                      startIdx = i;
                    }
                  }
                }
                const total = regimeData.length;
                return blocks.map((b, i) => {
                  const widthPct = ((b.end - b.start + 1) / total) * 100;
                  const badge = REGIME_BADGE[b.regime] || REGIME_BADGE.BULL;
                  return (
                    <div key={i} title={`${b.regime}: ${regimeData[b.start].date} → ${regimeData[b.end].date}`}
                      style={{
                        width: `${widthPct}%`, background: badge.bg.replace('0.15', '0.5'),
                        borderRight: i < blocks.length - 1 ? '1px solid rgba(0,0,0,0.3)' : 'none',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: widthPct > 5 ? 9 : 0, fontWeight: 700, color: badge.color,
                        overflow: 'hidden', whiteSpace: 'nowrap',
                      }}>
                      {widthPct > 8 ? b.regime : ''}
                    </div>
                  );
                });
              })()}
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 16, marginTop: 8, fontSize: 10, color: '#8b9cc8' }}>
              {Object.entries(REGIME_BADGE).map(([key, val]) => (
                <span key={key} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: val.bg.replace('0.15', '0.6'), display: 'inline-block' }} />
                  {val.label}
                </span>
              ))}
            </div>
          </div>

          {/* Yearly Returns Table */}
          <div style={sectionTitle}>Yearly Returns</div>
          <div style={{ ...glassPanel, padding: 0, overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 800, borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'rgba(15, 23, 42, 0.8)', borderBottom: '1px solid rgba(99, 120, 180, 0.18)' }}>
                  <th style={{ padding: '10px 14px', textAlign: 'left', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Year</th>
                  <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Buy & Hold</th>
                  <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>DCA</th>
                  {summary.strategies.based_sma200 && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Based ETF from SMA200</th>}
                  {summary.strategies.based_sma63 && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Based ETF from SMA63</th>}
                  <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>VXV Strategy</th>
                  {summary.strategies.mts_v2 && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Market Trend Score</th>}
                  {summary.strategies.option_d && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Opt D (Stage)</th>}
                  {summary.strategies.option_a && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Opt A (Perfect)</th>}
                  {summary.strategies.option_c_strict && <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Opt C (Strict)</th>}
                  <th style={{ padding: '10px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#475685' }}>Best</th>
                </tr>
              </thead>
              <tbody>
                {yearlyData.map(row => (
                  <tr key={row.year} style={{ borderBottom: '1px solid rgba(99, 120, 180, 0.08)' }}>
                    <td style={{ padding: '8px 14px', fontWeight: 600, color: '#e8edf7' }}>{row.year}</td>
                    {[
                      { val: row.bh, isBest: row.bh === row.best, color: COLORS.buyhold },
                      { val: row.dca, isBest: row.dca === row.best, color: COLORS.dca },
                      ...(summary.strategies.based_sma200 ? [{ val: row.based, isBest: row.based === row.best, color: COLORS.based_sma200 }] : []),
                      ...(summary.strategies.based_sma63 ? [{ val: row.based63, isBest: row.based63 === row.best, color: COLORS.based_sma63 }] : []),
                      { val: row.vxv, isBest: row.vxv === row.best, color: COLORS.vxv },
                      ...(summary.strategies.mts_v2 ? [{ val: row.mts, isBest: row.mts === row.best, color: COLORS.mts }] : []),
                      ...(summary.strategies.option_d ? [{ val: row.optD, isBest: row.optD === row.best, color: COLORS.option_d }] : []),
                      ...(summary.strategies.option_a ? [{ val: row.optA, isBest: row.optA === row.best, color: COLORS.option_a }] : []),
                      ...(summary.strategies.option_c_strict ? [{ val: row.optC, isBest: row.optC === row.best, color: COLORS.option_c }] : []),
                    ].map((cell, i) => (
                      <td key={i} style={{
                        padding: '8px 14px', textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums',
                        color: cell.val === null ? '#475685' : (cell.val! >= 0 ? '#22d3a0' : '#f43f5e'),
                        background: cell.isBest ? `${cell.color}10` : 'transparent',
                      }}>
                        {cell.val !== null ? fmtPct(cell.val!) : '—'}
                      </td>
                    ))}
                    <td style={{ padding: '8px 14px', textAlign: 'right', fontSize: 10, fontWeight: 700, color: '#8b9cc8' }}>
                      {row.best !== null && (() => {
                        if (row.bh === row.best) return 'B&H';
                        if (row.dca === row.best) return 'DCA';
                        if (row.based === row.best) return 'Based 200';
                        if (row.based63 === row.best) return 'Based 63';
                        if (row.vxv === row.best) return 'VXV';
                        if (row.mts === row.best) return 'MTS';
                        if (row.optD === row.best) return 'Opt D';
                        if (row.optA === row.best) return 'Opt A';
                        return 'Opt C';
                      })()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ height: 40 }} />
        </>
      )}
    </div>
  );
};
