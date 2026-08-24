import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchScenarioSummary, fetchScenarioEquity, BacktestScenarioSummary, BacktestEquityPoint } from '../api/backtest';
import { BacktestStrategyCard, CardChartPoint } from '../components/BacktestStrategyCard';
import { BacktestRunInfo } from '../components/BacktestRunInfo';
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
// 骨格は BacktestStrategyCard（ETF タブと共通）。ここでは API の値を
// カードが期待する「% 表記の実数」へ揃えて渡すだけにする。
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

  // 総ゲイン: API が total_return_pct を返さない旧レスポンス向けに initial からも算出できるようにする
  const baseCapital = summary?.initial_capital ?? initialEquity;
  const totalReturnPct = summary?.total_return_pct
    ?? (finalCap !== undefined && baseCapital > 0 ? (finalCap / baseCapital - 1) * 100 : undefined);

  // ミニグラフ: 実線=戦略 / グレー点線=SPY
  const chartData: CardChartPoint[] = Array.isArray(equity)
    ? equity.map(pt => ({
        date: pt.date,
        strategy: pt.equity,
        benchmark: pt.spy_equity ?? null,
      }))
    : [];

  return (
    <BacktestStrategyCard
      cardId={`scenario-${model.id}`}
      label={model.label}
      icon={model.icon}
      color={model.color}
      chartData={chartData}
      cagrPct={cagr !== null && cagr !== undefined ? cagr * 100 : null}
      maxDrawdownPct={maxDD !== undefined ? -Math.abs(maxDD) * 100 : null}
      cagrRangePct={cagrMax !== undefined && cagrMin !== undefined
        ? { max: cagrMax * 100, min: cagrMin * 100 }
        : undefined}
      rows={[
        [
          {
            label: 'Avg Trade',
            value: avgTradePnlPct !== undefined && avgTradePnlPct !== null ? fmtPct(avgTradePnlPct, 2) : '—',
          },
          { label: 'Win Rate', value: winRate !== undefined ? `${(winRate * 100).toFixed(1)}%` : '—' },
        ],
        [
          { label: 'Trades', value: trades !== undefined ? String(Math.round(trades)) : '—' },
          { label: 'Profit Factor', value: pf !== undefined ? pf.toFixed(2) : '—' },
        ],
      ]}
      finalCapital={finalCap}
      totalReturnPct={totalReturnPct}
      loading={loading}
      error={error}
      isActive={isActive}
      onClick={onClick}
    />
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

  // Run Info は全モデル共通の実行条件。最初に読み込めた summary を代表値として使う。
  const runInfoSource = useMemo(
    () => MODELS.map(m => modelData[m.id]?.summary).find(s => s != null) ?? null,
    [modelData]
  );

  const runInfoItems = useMemo(() => {
    if (!runInfoSource) return [];
    const items: { label: string; value: string }[] = [];
    if (runInfoSource.start_date && runInfoSource.end_date) {
      items.push({ label: 'Period', value: `${runInfoSource.start_date} → ${runInfoSource.end_date}` });
    }
    if (runInfoSource.trading_days != null) {
      items.push({ label: 'Trading Days', value: runInfoSource.trading_days.toLocaleString() });
    }
    if (runInfoSource.initial_capital != null) {
      items.push({ label: 'Initial Cap', value: fmt$(runInfoSource.initial_capital) });
    }
    if (runInfoSource.consider_tax != null) {
      items.push({ label: 'Tax Rate', value: `${(runInfoSource.consider_tax * 100).toFixed(0)}%` });
    }
    return items;
  }, [runInfoSource]);

  const runsNote = runInfoSource?.runs_count != null
    ? `${runInfoSource.runs_count} Monte Carlo runs`
    : undefined;

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

      {/* Run Info（デフォルト非表示・タップで展開） */}
      <BacktestRunInfo items={runInfoItems} note={runsNote} />

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

      {/* 凡例（実行条件は Run Info バー側に出すので、ここは線の意味だけ） */}
      <div className="rc-legend">
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 20, height: 1.5, background: '#94a3b8', display: 'inline-block' }} />
          {runsNote ? `Strategy equity (avg of ${runInfoSource?.runs_count} MC runs)` : 'Strategy equity'}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ width: 20, height: 1, background: '#6b7280', borderTop: '1px dashed', display: 'inline-block' }} />
          SPY benchmark
        </span>
      </div>
    </div>
  );
};
