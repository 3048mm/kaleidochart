import React, { useState, useEffect, useCallback } from 'react';
import { AreaChart, Area, ResponsiveContainer } from 'recharts';
import { fetchScenarioSummary, fetchScenarioEquity, BacktestScenarioSummary, BacktestEquityPoint } from '../api/backtest';
import { ScenarioDetailView } from './ScenarioDetailView';

// ─── 定数 ──────────────────────────────────────────────────────
const STRATEGIES = [
  { id: 'A', label: 'A' },
  { id: 'B1', label: 'B1' },
  { id: 'B2', label: 'B2' },
  { id: 'B3', label: 'B3' },
  { id: 'B4', label: 'B4' },
  { id: 'B5', label: 'B5' },
  { id: 'B6', label: 'B6' },
  { id: 'D', label: 'D' },
  { id: 'E1', label: 'E1' },
  { id: 'E2', label: 'E2' },
  { id: 'G1', label: 'G1' },
  { id: 'G2', label: 'G2' },
  { id: 'G3', label: 'G3' },
];

const MODELS = [
  { id: 'full_position', label: 'Full Position', color: '#ec4899', icon: '💎' },
  { id: 'spy_sma200', label: 'SPY SMA200', color: '#f59e0b', icon: '🎯' },
  { id: 'spy_sma63', label: 'SPY SMA63', color: '#8b5cf6', icon: '⚡' },
  { id: 'vxv_vix_ema', label: 'VXV/VIX EMA', color: '#10b981', icon: '📈' },
  { id: 'mts_raw', label: 'MTS Raw', color: '#3b82f6', icon: '📊' },
];

// ─── 型 ─────────────────────────────────────────────────────────
interface ModelData {
  summary: BacktestScenarioSummary | null;
  equity: BacktestEquityPoint[];
  loading: boolean;
  error: string | null;
}

interface PanelCardProps {
  model: typeof MODELS[number];
  data: ModelData;
  initialEquity: number;
  isActive?: boolean;
  onClick?: () => void;
}

// ─── ヘルパー ────────────────────────────────────────────────────
const fmt$ = (v: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);

const fmtPct = (v: number, decimals = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;

const fmtCagr = (v: number | undefined) => {
  if (v === undefined || v === null) return '—';
  return fmtPct(v * 100);
};

const fmtDD = (v: number | undefined) => {
  if (v === undefined || v === null) return '—';
  return `${(Math.abs(v) * 100).toFixed(1)}%`;
};

// CAGR を equity curve から算出
function calcCagrFromEquity(equity: BacktestEquityPoint[], initialCap: number): number | null {
  if (!equity.length) return null;
  const last = equity[equity.length - 1];
  const first = equity[0];
  if (!last || !first) return null;
  const years = (new Date(last.date).getTime() - new Date(first.date).getTime()) / (365.25 * 24 * 3600 * 1000);
  if (years <= 0 || initialCap <= 0) return null;
  return (last.equity / initialCap) ** (1 / years) - 1;
}

// ─── パネルカード ─────────────────────────────────────────────────
const PanelCard: React.FC<PanelCardProps> = ({ model, data, initialEquity, isActive, onClick }) => {
  const { summary, equity, loading, error } = data;

  const cagr = summary?.cagr_avg !== undefined
    ? summary.cagr_avg
    : summary?.cagr !== undefined
      ? summary.cagr
      : calcCagrFromEquity(equity, initialEquity);

  const maxDD = summary?.max_drawdown_avg ?? summary?.max_drawdown;
  const winRate = summary?.win_rate_avg ?? summary?.win_rate;
  const trades = summary?.total_trades_avg ?? summary?.total_trades;
  const pf = summary?.profit_factor_avg ?? summary?.profit_factor;
  const avgTradePnlPct = summary?.avg_trade_pnl_pct_avg ?? summary?.avg_trade_pnl_pct;
  const finalCap = summary?.final_capital_avg ?? summary?.final_capital;
  const cagrMax = summary?.cagr_max;
  const cagrMin = summary?.cagr_min;

  // Normalize equity for % chart
  const chartData = equity.length > 0 ? equity.map(pt => ({
    date: pt.date,
    strategy: pt.equity,
    spy: pt.spy_equity,
  })) : [];


  return (
    <div
      onClick={onClick}
      style={{
        background: isActive ? 'rgba(30, 41, 59, 0.95)' : 'rgba(15, 23, 42, 0.85)',
        border: isActive ? `2px solid ${model.color}` : `1px solid ${model.color}33`,
        borderRadius: '12px',
        padding: isActive ? '15px' : '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        backdropFilter: 'blur(12px)',
        boxShadow: isActive ? `0 0 25px ${model.color}30` : `0 0 20px ${model.color}15`,
        minWidth: 0,
        cursor: 'pointer',
        transition: 'all 0.2s ease',
        transform: isActive ? 'scale(1.02)' : 'none',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%',
            background: model.color, display: 'inline-block',
            boxShadow: `0 0 8px ${model.color}`,
          }} />
          <span style={{ fontWeight: 700, fontSize: '13px', color: '#e2e8f0', letterSpacing: '0.02em' }}>
            {model.icon} {model.label}
          </span>
        </div>
        {loading && (
          <span style={{
            width: 14, height: 14, border: `2px solid ${model.color}`,
            borderTopColor: 'transparent', borderRadius: '50%',
            display: 'inline-block', animation: 'spin 0.8s linear infinite'
          }} />
        )}
        {error && <span title={error} style={{ color: '#ef4444', fontSize: '14px' }}>⚠️</span>}
      </div>

      {/* Mini chart */}
      <div style={{ height: 80 }}>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={chartData} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
              <defs>
                <linearGradient id={`grad-${model.id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={model.color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={model.color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="strategy" stroke={model.color} strokeWidth={1.5}
                fill={`url(#grad-${model.id})`} dot={false} isAnimationActive={false} />
              <Area type="monotone" dataKey="spy" stroke="#6b7280" strokeWidth={1}
                strokeDasharray="3 2" fill="none" dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {loading
              ? <span style={{ color: '#64748b', fontSize: '11px' }}>Loading...</span>
              : <span style={{ color: '#374151', fontSize: '11px' }}>{error ? 'Error loading data' : 'No data yet'}</span>
            }
          </div>
        )}
      </div>

      {/* CAGR big number */}
      <div style={{ textAlign: 'center' }}>
        <div style={{
          fontSize: '28px', fontWeight: 800,
          color: cagr !== null && cagr !== undefined
            ? (cagr >= 0 ? '#34d399' : '#f87171')
            : '#64748b',
          lineHeight: 1.1, fontVariantNumeric: 'tabular-nums',
        }}>
          {cagr !== null && cagr !== undefined ? fmtCagr(cagr) : (loading ? '—' : '—')}
        </div>
        <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px', letterSpacing: '0.05em' }}>
          CAGR (年率)
        </div>
        {cagrMax !== undefined && cagrMin !== undefined && (
          <div style={{ fontSize: '10px', color: '#94a3b8', marginTop: '2px' }}>
            <span style={{ color: '#34d399' }}>{fmtCagr(cagrMax)}</span>
            {' / '}
            <span style={{ color: '#f87171' }}>{fmtCagr(cagrMin)}</span>
          </div>
        )}
      </div>

      {/* Metrics grid */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        gap: '6px', fontSize: '11px',
      }}>
        {[
          { label: 'Max DD', value: maxDD !== undefined ? fmtDD(maxDD) : '—', warn: true },
          { label: 'Win Rate', value: winRate !== undefined ? `${(winRate * 100).toFixed(1)}%` : '—' },
          { label: 'Trades', value: trades !== undefined ? String(Math.round(trades)) : '—' },
          { label: 'Profit Factor', value: pf !== undefined ? pf.toFixed(2) : '—' },
          { label: '1取引平均', value: avgTradePnlPct !== undefined && avgTradePnlPct !== null ? fmtPct(avgTradePnlPct, 2) : '—' },
        ].map(({ label, value, warn }) => (
          <div key={label} style={{
            background: 'rgba(30, 41, 59, 0.5)',
            borderRadius: '6px', padding: '6px 8px',
          }}>
            <div style={{ color: '#64748b', fontSize: '9px', letterSpacing: '0.05em', marginBottom: '2px' }}>
              {label}
            </div>
            <div style={{ color: warn ? '#fbbf24' : '#cbd5e1', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Final capital */}
      {finalCap !== undefined && (
        <div style={{
          background: `${model.color}11`, borderRadius: '6px',
          padding: '6px 10px', display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', borderLeft: `3px solid ${model.color}`,
        }}>
          <span style={{ fontSize: '10px', color: '#64748b' }}>最終資産</span>
          <span style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
            {fmt$(finalCap)}
          </span>
        </div>
      )}
    </div>
  );
};

// ─── メインページ ─────────────────────────────────────────────────
export const RegimeComparisonPage: React.FC = () => {
  const [selectedStrategy, setSelectedStrategy] = useState('A');
  const [selectedModel, setSelectedModel] = useState<string>('full_position');
  const [modelData, setModelData] = useState<Record<string, ModelData>>(
    Object.fromEntries(MODELS.map(m => [m.id, { summary: null, equity: [], loading: false, error: null }]))
  );

  const loadModel = useCallback(async (strategy: string, modelId: string) => {
    const groupName = `${strategy}__${modelId}`;

    setModelData(prev => ({
      ...prev,
      [modelId]: { ...prev[modelId], loading: true, error: null, summary: null, equity: [] },
    }));

    try {
      const [summary, equity] = await Promise.all([
        fetchScenarioSummary(groupName),
        fetchScenarioEquity(groupName),
      ]);
      setModelData(prev => ({
        ...prev,
        [modelId]: { summary, equity, loading: false, error: null },
      }));
    } catch (e: any) {
      setModelData(prev => ({
        ...prev,
        [modelId]: { summary: null, equity: [], loading: false, error: e.message || 'Failed to load' },
      }));
    }
  }, []);

  // 戦略が切り替わったら全モデルを再ロード
  useEffect(() => {
    MODELS.forEach(m => loadModel(selectedStrategy, m.id));
  }, [selectedStrategy, loadModel]);

  const initialEquity = 100000;

  return (
    <div style={{ padding: '0', color: '#e2e8f0' }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .rc-tab-bar {
          display: flex;
          gap: 4px;
          margin-bottom: 20px;
          background: rgba(15, 23, 42, 0.6);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 10px;
          padding: 4px;
          width: fit-content;
          max-width: 100%;
          overflow-x: auto;
          -webkit-overflow-scrolling: touch;
          scrollbar-width: none;
        }
        .rc-tab-bar::-webkit-scrollbar { display: none; }
        .rc-panel-grid {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          gap: 12px;
        }
        .rc-legend {
          margin-top: 12px;
          display: flex;
          align-items: center;
          gap: 12px;
          font-size: 10px;
          color: #64748b;
          flex-wrap: wrap;
        }
        @media (max-width: 1440px) {
          .rc-panel-grid {
            grid-template-columns: repeat(3, 1fr);
          }
        }
        @media (max-width: 1024px) {
          .rc-panel-grid {
            grid-template-columns: repeat(2, 1fr);
          }
        }
        @media (max-width: 640px) {
          .rc-panel-grid {
            grid-template-columns: 1fr;
          }
          .rc-legend {
            flex-direction: column;
            align-items: flex-start;
            gap: 6px;
          }
        }
      `}</style>

      {/* 戦略タブ */}
      <div className="rc-tab-bar">
        {STRATEGIES.map(s => (
          <button
            key={s.id}
            onClick={() => setSelectedStrategy(s.id)}
            style={{
              padding: '6px 18px',
              borderRadius: '7px',
              border: 'none',
              background: selectedStrategy === s.id
                ? 'linear-gradient(135deg, #3b82f6, #6366f1)'
                : 'transparent',
              color: selectedStrategy === s.id ? '#fff' : '#94a3b8',
              fontWeight: selectedStrategy === s.id ? 700 : 500,
              fontSize: '13px',
              cursor: 'pointer',
              transition: 'all 0.2s',
              boxShadow: selectedStrategy === s.id ? '0 2px 12px #3b82f655' : 'none',
              letterSpacing: '0.02em',
            }}
          >
            {s.label}
          </button>
        ))}
      </div>

      {/* 5パネル */}
      <div className="rc-panel-grid">
        {MODELS.map(m => (
          <PanelCard
            key={m.id}
            model={m}
            data={modelData[m.id]}
            initialEquity={initialEquity}
            isActive={selectedModel === m.id}
            onClick={() => setSelectedModel(m.id)}
          />
        ))}
      </div>

      {/* 詳細分析セクション */}
      {selectedModel && (
        <div style={{ marginTop: '40px', borderTop: '1px solid rgba(255, 255, 255, 0.1)', paddingTop: '30px' }}>
          <h2 style={{ fontSize: '16px', fontWeight: 'bold', color: '#fff', marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            📊 詳細分析: {selectedStrategy} - {MODELS.find(m => m.id === selectedModel)?.label}
          </h2>
          <ScenarioDetailView strategyId={selectedStrategy} modelId={selectedModel} />
        </div>
      )}

      {/* 凡例 */}
      <div className="rc-legend">
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 20, height: 1.5, background: '#94a3b8', display: 'inline-block' }} />
          Strategy equity (avg of 10 MC runs)
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 20, height: 1, background: '#6b7280', borderTop: '1px dashed', display: 'inline-block' }} />
          SPY benchmark
        </span>
        <span style={{ color: '#475569' }}>
          Initial: $100,000 · Period: 2022-01 to 2026-03 · 10 Monte Carlo runs
        </span>
      </div>
    </div>
  );
};
