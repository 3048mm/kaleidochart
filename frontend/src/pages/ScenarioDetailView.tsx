import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import {
  fetchScenarioSummary,
  fetchScenarioEquity,
  fetchScenarioTrades,
  BacktestScenarioSummary,
  BacktestEquityPoint,
  BacktestTradeLogItem
} from '../api/backtest';

interface ScenarioDetailViewProps {
  strategyId: string;
  modelId: string;
}

export const ScenarioDetailView: React.FC<ScenarioDetailViewProps> = ({ strategyId, modelId }) => {
  const [selectedSubRun, setSelectedSubRun] = useState<string>('all');
  const [summary, setSummary] = useState<BacktestScenarioSummary | null>(null);
  const [equityData, setEquityData] = useState<BacktestEquityPoint[]>([]);
  const [tradeLogs, setTradeLogs] = useState<BacktestTradeLogItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>('');
  const [hasMC, setHasMC] = useState<boolean>(false);
  
  // Sorting States for Trade Logs
  const [sortField, setSortField] = useState<keyof BacktestTradeLogItem>('date');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

  // Chart Display Toggles
  const [showEquity, setShowEquity] = useState<boolean>(true);
  const [showCash, setShowCash] = useState<boolean>(true);
  const [showSpy, setShowSpy] = useState<boolean>(true);
  const [showQqq, setShowQqq] = useState<boolean>(false);
  const [showTqqq, setShowTqqq] = useState<boolean>(false);
  const [showSoxl, setShowSoxl] = useState<boolean>(false);
  const [showTrendScore, setShowTrendScore] = useState<boolean>(false);

  // Table Filters
  const [searchTicker, setSearchTicker] = useState<string>('');
  const [filterReason, setFilterReason] = useState<string>('all');

  const baseScenarioName = `${strategyId}_${modelId}`;
  const targetRequestScenario = selectedSubRun !== 'all'
    ? `${baseScenarioName}_run_${selectedSubRun}`
    : baseScenarioName;

  // Reset state when strategy or model changes
  useEffect(() => {
    setSelectedSubRun('all');
    setHasMC(false);
    setSortField('date');
    setSortDirection('asc');
  }, [strategyId, modelId]);

  useEffect(() => {
    setLoading(true);
    setError('');
    
    Promise.all([
      fetchScenarioSummary(targetRequestScenario),
      fetchScenarioEquity(targetRequestScenario),
      fetchScenarioTrades(targetRequestScenario)
    ])
      .then(([sumData, eqData, trData]) => {
        setSummary(sumData);
        setEquityData(eqData);
        setTradeLogs(trData);
        
        // Lock hasMC to true if the base scenario is a Monte Carlo simulation
        if (sumData.is_monte_carlo) {
          setHasMC(true);
        }
        
        setLoading(false);
      })
      .catch((err) => {
        console.error(err);
        setError('データの読み込みに失敗しました。');
        setLoading(false);
      });
  }, [targetRequestScenario]);

  // Format Helpers
  const fmtCur = (v: number) =>
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);

  const fmtPct = (v: number, decimals = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;

  const renderKPICard = (title: string, value: string | number, subtext: string, type: 'good' | 'bad' | 'neutral' = 'neutral', mcInfo?: string) => {
    const color = type === 'good' ? 'var(--accent-green)' : type === 'bad' ? 'var(--accent-red)' : 'var(--text-primary)';

    return (
      <div
        className="glass-panel"
        style={{
          flex: '1',
          minWidth: '200px',
          padding: '20px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          position: 'relative',
          overflow: 'hidden',
          transition: 'transform 0.2s, box-shadow 0.2s',
          border: '1px solid var(--border)'
        }}
      >
        <div style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '3px',
          background: type === 'good' ? 'linear-gradient(90deg, var(--accent-green), #00ffcc)' : type === 'bad' ? 'linear-gradient(90deg, var(--accent-red), #ff0055)' : 'var(--border)'
        }} />

        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
          {title}
        </div>

        <div style={{ fontSize: '24px', fontWeight: 'bold', color, letterSpacing: '-0.02em', display: 'flex', alignItems: 'baseline', gap: '8px' }}>
          {value}
        </div>

        <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: '1.4' }}>
          {subtext}
        </div>

        {mcInfo && (
          <div style={{ fontSize: '9px', color: 'var(--accent-blue)', marginTop: '6px', fontWeight: '600', letterSpacing: '0.02em' }}>
            {mcInfo}
          </div>
        )}
      </div>
    );
  };

  const renderReasonBadge = (reason: string) => {
    let bg = 'rgba(255, 255, 255, 0.08)';
    let color = 'var(--text-secondary)';

    if (reason === 'stop_loss' || reason.includes('stop')) {
      bg = 'rgba(244, 63, 94, 0.12)';
      color = 'var(--accent-red)';
    } else if (reason === 'profit_target' || reason.includes('profit')) {
      bg = 'rgba(34, 211, 160, 0.12)';
      color = 'var(--accent-green)';
    } else if (reason === 'trailing_stop' || reason.includes('trail')) {
      bg = 'rgba(245, 158, 11, 0.12)';
      color = 'var(--accent-yellow)';
    }

    return (
      <span style={{
        padding: '3px 8px',
        borderRadius: '4px',
        fontSize: '9px',
        fontWeight: 'bold',
        backgroundColor: bg,
        color,
        textTransform: 'uppercase',
        letterSpacing: '0.05em'
      }}>
        {reason.replace('_', ' ')}
      </span>
    );
  };

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;

      // Find the first valid equity data point (simulation start date) to align return calculations
      const startPoint = equityData.find(d => d.equity !== undefined && d.equity !== null);
      const initialEquity = startPoint ? startPoint.equity : 1;

      const initialSpy = startPoint ? startPoint.spy_equity : null;
      const spyReturn = (initialSpy && data.spy_equity) ? ((data.spy_equity - initialSpy) / initialSpy) * 100 : null;

      return (
        <div style={{
          background: 'rgba(15, 23, 42, 0.92)',
          border: '1px solid var(--border)',
          borderRadius: '8px',
          padding: '12px',
          boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.5)',
          backdropFilter: 'blur(8px)'
        }}>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: 'bold' }}>
            {data.date}
          </div>

          {showEquity && data.equity !== undefined && data.equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Strategy (純資産):</span>
              <span style={{ color: 'var(--accent-green)', fontSize: '11px', fontWeight: 'bold' }}>
                {fmtCur(data.equity)} ({fmtPct(((data.equity - initialEquity) / initialEquity) * 100)})
              </span>
            </div>
          )}

          {showCash && data.cash !== undefined && data.cash !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Cash (手元資金):</span>
              <span style={{ color: '#fff', fontSize: '11px', fontWeight: 'bold' }}>
                {fmtCur(data.cash)}
              </span>
            </div>
          )}

          {showSpy && data.spy_equity !== undefined && data.spy_equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>SPY Benchmark:</span>
              <span style={{ color: '#3b82f6', fontSize: '11px', fontWeight: 'bold' }}>
                {fmtCur(data.spy_equity)} {spyReturn !== null ? `(${fmtPct(spyReturn)})` : ''}
              </span>
            </div>
          )}

          {showTrendScore && data.trend_score !== undefined && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '4px', marginTop: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Trend Score:</span>
              <span style={{ color: 'var(--accent-yellow)', fontSize: '11px', fontWeight: 'bold' }}>
                {data.trend_score}
              </span>
            </div>
          )}
        </div>
      );
    }
    return null;
  };

  const handleSort = (field: keyof BacktestTradeLogItem) => {
    if (sortField === field) {
      setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  // Filter & Sort Trades
  const filteredTrades = React.useMemo(() => {
    const filtered = tradeLogs.filter((t) => {
      const matchesTicker = t.ticker.toLowerCase().includes(searchTicker.toLowerCase());
      const matchesReason = filterReason === 'all' || t.reason === filterReason;
      return matchesTicker && matchesReason;
    });

    return [...filtered].sort((a, b) => {
      let aVal = a[sortField];
      let bVal = b[sortField];

      if (aVal === undefined || aVal === null) return sortDirection === 'asc' ? 1 : -1;
      if (bVal === undefined || bVal === null) return sortDirection === 'asc' ? -1 : 1;

      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortDirection === 'asc' 
          ? aVal.localeCompare(bVal) 
          : bVal.localeCompare(aVal);
      } else {
        return sortDirection === 'asc'
          ? (aVal as number) - (bVal as number)
          : (bVal as number) - (aVal as number);
      }
    });
  }, [tradeLogs, searchTicker, filterReason, sortField, sortDirection]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Monte Carlo Run Selector */}
      {summary && hasMC && (
        <div className="glass-panel" style={{ 
          padding: '16px 24px', 
          display: 'flex', 
          justifyContent: 'space-between', 
          alignItems: 'center', 
          flexWrap: 'wrap', 
          gap: '15px',
          background: 'linear-gradient(135deg, rgba(30, 41, 59, 0.5), rgba(15, 23, 42, 0.8))',
          border: '1px solid var(--border)',
          borderRadius: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '20px' }}>🎲</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              <span style={{ fontSize: '13px', color: '#fff', fontWeight: 'bold' }}>
                モンテカルロ・シミュレーション分析スコープ
              </span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                {selectedSubRun === 'all' 
                  ? `全 ${summary.runs_count || 10} 回のシミュレーション統計データを統合表示中` 
                  : `個別シミュレーション試行 [Run ${selectedSubRun}] の詳細レコードをロード中`}
              </span>
            </div>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{
              padding: '4px 8px',
              borderRadius: '4px',
              fontSize: '10px',
              fontWeight: 'bold',
              backgroundColor: selectedSubRun === 'all' ? 'rgba(34, 211, 160, 0.15)' : 'rgba(59, 130, 246, 0.15)',
              color: selectedSubRun === 'all' ? 'var(--accent-green)' : '#3b82f6',
              textTransform: 'uppercase',
              letterSpacing: '0.05em'
            }}>
              {selectedSubRun === 'all' ? 'STATISTICS' : `RUN ${selectedSubRun}`}
            </span>
            
            <select
              value={selectedSubRun}
              onChange={(e) => setSelectedSubRun(e.target.value)}
              style={{
                background: 'rgba(15, 23, 42, 0.8)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
                color: '#fff',
                padding: '8px 16px',
                fontSize: '13px',
                fontWeight: '600',
                cursor: 'pointer',
                outline: 'none',
                boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
                transition: 'border-color 0.2s'
              }}
            >
              <option value="all" style={{ background: '#0f172a' }}>📊 全体統計 (平均/最良/最悪)</option>
              {Array.from({ length: summary.runs_count || 10 }).map((_, idx) => (
                <option key={idx} value={String(idx)} style={{ background: '#0f172a' }}>
                  📁 Run {idx} (個別詳細)
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {error && (
        <div className="glass-panel" style={{ padding: '20px', borderLeft: '4px solid var(--accent-red)', color: 'var(--text-primary)' }}>
          <span style={{ fontWeight: 'bold', color: 'var(--accent-red)', display: 'block', marginBottom: '4px' }}>⚠️ 読み込みエラー</span>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '350px', gap: '15px' }}>
          <div className="spinner" style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.05)', borderTopColor: 'var(--accent-green)', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
          <span style={{ color: 'var(--text-secondary)', fontSize: '12px', letterSpacing: '0.05em' }}>シナリオデータを高度解析中...</span>
        </div>
      ) : summary ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          {/* KPI Metrics Row */}
          <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
            {renderKPICard(
              summary.is_monte_carlo ? 'CAGR (10回平均)' : 'CAGR (年平均成長率)',
              `${(summary.cagr * 100).toFixed(2)}%`,
              'ベンチマークを凌駕する実質年利換算値',
              summary.cagr >= 0 ? 'good' : 'bad',
              summary.is_monte_carlo && summary.cagr_max !== undefined && summary.cagr_min !== undefined
                ? `(最良: ${(summary.cagr_max * 100).toFixed(1)}% / 最悪: ${(summary.cagr_min * 100).toFixed(1)}%)` 
                : undefined
            )}
            {renderKPICard(
              summary.is_monte_carlo ? 'PF (10回平均)' : 'Profit Factor',
              summary.profit_factor.toFixed(2),
              '総利益 / 総損失の比率（期待値）',
              summary.profit_factor >= 1.0 ? 'good' : 'bad',
              summary.is_monte_carlo && summary.profit_factor_avg !== undefined 
                ? `(平均: ${summary.profit_factor_avg.toFixed(2)})` 
                : undefined
            )}
            {renderKPICard(
              summary.is_monte_carlo ? '最大DD (10回平均)' : 'Max Drawdown (最悪下落率)',
              `${(summary.max_drawdown * 100).toFixed(2)}%`,
              'ピークからの最大口座下落幅',
              summary.max_drawdown >= -0.2 ? 'neutral' : 'bad',
              summary.is_monte_carlo && summary.max_drawdown_min !== undefined && summary.max_drawdown_max !== undefined
                ? `(最悪: ${(summary.max_drawdown_min * 100).toFixed(1)}% / 最良: ${(summary.max_drawdown_max * 100).toFixed(1)}%)` 
                : undefined
            )}
            {renderKPICard(
              summary.is_monte_carlo ? '勝率 (10回平均)' : 'Win Rate (勝率)',
              `${(summary.win_rate * 100).toFixed(1)}%`,
              '全決済取引における利益取引の割合',
              summary.win_rate >= 0.5 ? 'good' : 'neutral',
              summary.is_monte_carlo && summary.win_rate_avg !== undefined 
                ? `(平均: ${(summary.win_rate_avg * 100).toFixed(1)}%)` 
                : undefined
            )}
            {renderKPICard(
              summary.is_monte_carlo ? '総決済数 (10回平均)' : 'Total Trades (総決済回数)',
              summary.total_trades,
              '決済が確定した累計ポジション数',
              'neutral',
              summary.is_monte_carlo ? `(代表Run 1のログを表示中)` : undefined
            )}
          </div>

          {/* Equity Chart Section */}
          <div className="glass-panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px', flexWrap: 'wrap', gap: '15px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
                  📈 Equity Curve & Portfolio Cash Allocation
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>純資産と余力のバランス・推移</span>
              </div>

              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  onClick={() => setShowEquity(!showEquity)}
                  style={{
                    background: showEquity ? 'rgba(34, 211, 160, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showEquity ? 'var(--accent-green)' : 'var(--border)'}`,
                    color: showEquity ? 'var(--accent-green)' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  Strategy Equity
                </button>
                <button
                  onClick={() => setShowCash(!showCash)}
                  style={{
                    background: showCash ? 'rgba(255, 255, 255, 0.1)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showCash ? 'rgba(255, 255, 255, 0.3)' : 'var(--border)'}`,
                    color: showCash ? '#fff' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  Cash Allocation
                </button>
                <button
                  onClick={() => setShowSpy(!showSpy)}
                  style={{
                    background: showSpy ? 'rgba(59, 130, 246, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showSpy ? '#3b82f6' : 'var(--border)'}`,
                    color: showSpy ? '#3b82f6' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  SPY Benchmark
                </button>
                <button
                  onClick={() => setShowQqq(!showQqq)}
                  style={{
                    background: showQqq ? 'rgba(249, 115, 22, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showQqq ? '#f97316' : 'var(--border)'}`,
                    color: showQqq ? '#f97316' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  QQQ Benchmark
                </button>
                <button
                  onClick={() => setShowTqqq(!showTqqq)}
                  style={{
                    background: showTqqq ? 'rgba(236, 72, 153, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showTqqq ? '#ec4899' : 'var(--border)'}`,
                    color: showTqqq ? '#ec4899' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  TQQQ Benchmark
                </button>
                <button
                  onClick={() => setShowSoxl(!showSoxl)}
                  style={{
                    background: showSoxl ? 'rgba(168, 85, 247, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showSoxl ? '#a855f7' : 'var(--border)'}`,
                    color: showSoxl ? '#a855f7' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  SOXL Benchmark
                </button>
                <button
                  onClick={() => setShowTrendScore(!showTrendScore)}
                  style={{
                    background: showTrendScore ? 'rgba(245, 158, 11, 0.15)' : 'rgba(255, 255, 255, 0.02)',
                    border: `1px solid ${showTrendScore ? 'var(--accent-yellow)' : 'var(--border)'}`,
                    color: showTrendScore ? 'var(--accent-yellow)' : 'var(--text-muted)',
                    padding: '5px 12px',
                    borderRadius: '20px',
                    fontSize: '11px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    transition: 'all 0.2s ease',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    outline: 'none'
                  }}
                >
                  Market Trend Score
                </button>
              </div>
            </div>

            {equityData.length > 0 ? (
              <div style={{ width: '100%', height: '370px' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={equityData}
                    margin={{ top: 10, right: showTrendScore ? 30 : 10, left: 10, bottom: 0 }}
                  >
                    <defs>
                      <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="var(--accent-green)" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="var(--accent-green)" stopOpacity={0.01} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255, 255, 255, 0.05)" />
                    <XAxis
                      dataKey="date"
                      stroke="var(--text-muted)"
                      fontSize={10}
                      tickLine={false}
                      dy={10}
                    />
                    <YAxis
                      yAxisId="left"
                      stroke="var(--text-muted)"
                      fontSize={10}
                      tickLine={false}
                      axisLine={false}
                      domain={['auto', 'auto']}
                      tickFormatter={(v) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
                    />
                    {showTrendScore && (
                      <YAxis
                        yAxisId="right"
                        orientation="right"
                        stroke="rgba(245, 158, 11, 0.4)"
                        fontSize={10}
                        tickLine={false}
                        axisLine={false}
                        domain={[0, 100]}
                        tickFormatter={(v) => `${v}`}
                      />
                    )}
                    <Tooltip content={<CustomTooltip />} />
                    {/* Background fan lines for Monte Carlo runs */}
                    {showEquity && equityData.length > 0 && equityData[0].run_equities && (
                      Object.keys(equityData[0].run_equities).map((runKey) => (
                        <Line
                          key={runKey}
                          yAxisId="left"
                          type="monotone"
                          dataKey={`run_equities.${runKey}`}
                          stroke="rgba(34, 211, 160, 0.12)"
                          strokeWidth={1}
                          dot={false}
                          activeDot={false}
                        />
                      ))
                    )}
                    {/* Main average equity line or standard area plot */}
                    {showEquity && (
                      equityData.length > 0 && equityData[0].run_equities ? (
                        <Line
                          yAxisId="left"
                          name="Strategy Equity (平均)"
                          type="monotone"
                          dataKey="equity"
                          stroke="var(--accent-green)"
                          strokeWidth={3}
                          dot={false}
                        />
                      ) : (
                        <Area
                          yAxisId="left"
                          name="Equity (純資産)"
                          type="monotone"
                          dataKey="equity"
                          stroke="var(--accent-green)"
                          strokeWidth={2}
                          fillOpacity={1}
                          fill="url(#colorEquity)"
                        />
                      )
                    )}
                    {showCash && (
                      <Line
                        yAxisId="left"
                        name="Cash (手元余力キャッシュ)"
                        type="monotone"
                        dataKey="cash"
                        stroke="rgba(255, 255, 255, 0.35)"
                        strokeDasharray="4 4"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    )}
                    {showSpy && (
                      <Line
                        yAxisId="left"
                        name="SPY (S&P 500 ベンチマーク)"
                        type="monotone"
                        dataKey="spy_equity"
                        stroke="#3b82f6"
                        strokeDasharray="3 3"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    )}
                    {showQqq && (
                      <Line
                        yAxisId="left"
                        name="QQQ (Nasdaq 100 ベンチマーク)"
                        type="monotone"
                        dataKey="qqq_equity"
                        stroke="#f97316"
                        strokeDasharray="3 3"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    )}
                    {showTqqq && (
                      <Line
                        yAxisId="left"
                        name="TQQQ (Nasdaq 100 レバレッジ3倍)"
                        type="monotone"
                        dataKey="tqqq_equity"
                        stroke="#ec4899"
                        strokeDasharray="3 3"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    )}
                    {showSoxl && (
                      <Line
                        yAxisId="left"
                        name="SOXL (半導体レバレッジ3倍)"
                        type="monotone"
                        dataKey="soxl_equity"
                        stroke="#a855f7"
                        strokeDasharray="3 3"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    )}
                    {showTrendScore && (
                      <Line
                        yAxisId="right"
                        name="Market Trend Score"
                        type="monotone"
                        dataKey="trend_score"
                        stroke="rgba(245, 158, 11, 0.25)"
                        strokeWidth={1}
                        dot={false}
                      />
                    )}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div style={{ height: '200px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                資産曲線データがありません
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            {/* Yearly Return Breakdown */}
            {summary.yearly_performance && Object.keys(summary.yearly_performance).length > 0 && (
              <div className="glass-panel" style={{ padding: '24px', flex: '1', minWidth: '400px' }}>
                <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '16px' }}>
                  <h3 style={{ margin: 0, color: '#fff' }}>📅 Yearly Returns & Benchmarks</h3>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>年度ごとの戦略リターンとS&P 500の対比</span>
                </div>

                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left', color: '#e2e8f0' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                        <th style={{ padding: '10px 8px' }}>Year</th>
                        <th style={{ padding: '10px 8px' }}>Strategy Return</th>
                        <th style={{ padding: '10px 8px' }}>Net P&L</th>
                        <th style={{ padding: '10px 8px' }}>Trades</th>
                        <th style={{ padding: '10px 8px' }}>Win Rate</th>
                        <th style={{ padding: '10px 8px' }}>PF</th>
                        <th style={{ padding: '10px 8px' }}>SPY Return</th>
                        <th style={{ padding: '10px 8px', textAlign: 'right' }}>Vs SPY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.keys(summary.yearly_performance).sort().map((year, idx) => {
                        const item = summary.yearly_performance![year];
                        const isProfit = item.net_pnl > 0;
                        const isLoss = item.net_pnl < 0;

                        const strategyReturn = item.return_pct ?? 0.0;
                        const pnlColor = isProfit ? 'var(--accent-green)' : isLoss ? 'var(--accent-red)' : 'var(--text-primary)';
                        const pfColor = item.profit_factor >= 1.0 ? 'var(--accent-green)' : 'var(--accent-red)';
                        const spyColor = item.spy_return_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

                        const rowBg = idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)';

                        let vsSpyLabel = 'GAIN';
                        let vsSpyBg = 'rgba(34, 211, 160, 0.12)';
                        let vsSpyColor = 'var(--accent-green)';

                        if (strategyReturn > item.spy_return_pct) {
                          vsSpyLabel = '🏆 OUTPERFORM';
                          vsSpyBg = 'rgba(0, 255, 136, 0.18)';
                          vsSpyColor = '#00ffaa';
                        } else if (strategyReturn < item.spy_return_pct) {
                          vsSpyLabel = 'UNDERPERFORM';
                          vsSpyBg = 'rgba(244, 63, 94, 0.12)';
                          vsSpyColor = 'var(--accent-red)';
                        } else {
                          vsSpyLabel = 'EQUAL';
                          vsSpyBg = 'rgba(255, 255, 255, 0.08)';
                          vsSpyColor = 'var(--text-secondary)';
                        }

                        return (
                          <tr
                            key={year}
                            style={{
                              borderBottom: '1px solid rgba(255,255,255,0.03)',
                              backgroundColor: rowBg,
                              transition: 'background-color 0.15s'
                            }}
                          >
                            <td style={{ padding: '12px 8px', fontWeight: 'bold' }}>{year}</td>
                            <td style={{ padding: '12px 8px', color: strategyReturn >= 0 ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 'bold' }}>
                              {fmtPct(strategyReturn)}
                            </td>
                            <td style={{ padding: '12px 8px', color: pnlColor }}>{fmtCur(item.net_pnl)}</td>
                            <td style={{ padding: '12px 8px' }}>{item.total_trades}</td>
                            <td style={{ padding: '12px 8px' }}>{fmtPct(item.win_rate * 100, 1)}</td>
                            <td style={{ padding: '12px 8px', color: pfColor, fontWeight: '500' }}>{item.profit_factor.toFixed(2)}</td>
                            <td style={{ padding: '12px 8px', color: spyColor }}>{fmtPct(item.spy_return_pct)}</td>
                            <td style={{ padding: '12px 8px', textAlign: 'right' }}>
                              <span style={{
                                padding: '3px 8px',
                                borderRadius: '4px',
                                fontSize: '8px',
                                fontWeight: 'bold',
                                backgroundColor: vsSpyBg,
                                color: vsSpyColor,
                                letterSpacing: '0.05em'
                              }}>
                                {vsSpyLabel}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Exit Reason Breakdown */}
            {summary.exit_reasons && Object.keys(summary.exit_reasons).length > 0 && (
              <div className="glass-panel" style={{ padding: '24px', flex: '0 0 320px' }}>
                <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '16px' }}>
                  <h3 style={{ margin: 0, color: '#fff' }}>🚪 Exit Reasons Analysis</h3>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>決済理由ごとの比率と平均損益</span>
                </div>

                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left', color: '#e2e8f0' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      <th style={{ padding: '10px 8px' }}>Reason</th>
                      <th style={{ padding: '10px 8px' }}>Count (%)</th>
                      <th style={{ padding: '10px 8px', textAlign: 'right' }}>Avg P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(summary.exit_reasons).map((reason, idx) => {
                      const stats = summary.exit_reasons![reason];
                      const totalExitTrades = Object.values(summary.exit_reasons!).reduce((sum, item) => sum + item.count, 0);
                      const sharePct = totalExitTrades > 0 ? (stats.count / totalExitTrades) * 100 : 0;

                      const isProfit = stats.avg_pnl_pct > 0;
                      const isLoss = stats.avg_pnl_pct < 0;
                      const pnlColor = isProfit ? 'var(--accent-green)' : isLoss ? 'var(--accent-red)' : 'var(--text-primary)';
                      const rowBg = idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)';

                      return (
                        <tr
                          key={reason}
                          style={{
                            borderBottom: '1px solid rgba(255,255,255,0.03)',
                            backgroundColor: rowBg,
                            transition: 'background-color 0.15s'
                          }}
                        >
                          <td style={{ padding: '12px 8px' }}>{renderReasonBadge(reason)}</td>
                          <td style={{ padding: '12px 8px' }}>
                            <span style={{ fontWeight: 'bold' }}>{stats.count}</span>
                            <span style={{ fontSize: '10px', color: 'var(--text-muted)', marginLeft: '4px' }}>
                              ({sharePct.toFixed(1)}%)
                            </span>
                          </td>
                          <td style={{ padding: '12px 8px', textAlign: 'right', color: pnlColor, fontWeight: 'bold' }}>
                            {fmtPct(stats.avg_pnl_pct)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Trade Transaction Logs */}
          <div className="glass-panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px', flexWrap: 'wrap', gap: '15px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', color: '#fff' }}>
                  📜 Trade Transaction Logs
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>全決済取引履歴レコード</span>
              </div>

              {/* Table Filters */}
              <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  type="text"
                  placeholder="🔍 Tickerでフィルタ..."
                  value={searchTicker}
                  onChange={(e) => setSearchTicker(e.target.value)}
                  style={{
                    background: 'rgba(255,255,255,0.05)',
                    border: '1px solid var(--border)',
                    borderRadius: '6px',
                    color: '#fff',
                    padding: '6px 12px',
                    fontSize: '12px',
                    outline: 'none',
                    width: '150px'
                  }}
                />

                <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.05)', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border)' }}>
                  <select
                    value={filterReason}
                    onChange={(e) => setFilterReason(e.target.value)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#fff',
                      fontSize: '12px',
                      cursor: 'pointer',
                      outline: 'none'
                    }}
                  >
                    <option value="all" style={{ background: '#0f172a' }}>🚪 全決済理由</option>
                    {summary.exit_reasons && Object.keys(summary.exit_reasons).map((reason) => (
                      <option key={reason} value={reason} style={{ background: '#0f172a' }}>
                        {reason.replace('_', ' ').toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div style={{ overflowX: 'auto', maxHeight: '400px' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left', color: '#e2e8f0' }}>
                <thead style={{ position: 'sticky', top: 0, background: '#0f172a', zIndex: 1 }}>
                  <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                    {[
                      { key: 'date', label: 'Date' },
                      { key: 'ticker', label: 'Ticker' },
                      { key: 'action', label: 'Action' },
                      { key: 'price', label: 'Price', align: 'right' },
                      { key: 'size', label: 'Size', align: 'right' },
                      { key: 'reason', label: 'Exit Reason', align: 'center' },
                      { key: 'pnl_pct', label: 'PnL%', align: 'right' }
                    ].map((col) => {
                      const isSorted = sortField === col.key;
                      return (
                        <th 
                          key={col.key} 
                          onClick={() => handleSort(col.key as keyof BacktestTradeLogItem)}
                          style={{ 
                            padding: '10px 12px', 
                            fontWeight: '600',
                            textAlign: col.align as any || 'left',
                            cursor: 'pointer',
                            userSelect: 'none',
                            transition: 'color 0.2s'
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.color = '#fff'}
                          onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                        >
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                            {col.label}
                            {isSorted ? (sortDirection === 'asc' ? ' ▲' : ' ▼') : ' ↕'}
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {filteredTrades.length > 0 ? (
                    filteredTrades.map((trade, idx) => {
                      const isSell = trade.action.toLowerCase() === 'sell';
                      const isProfit = trade.pnl_pct > 0;
                      const isLoss = trade.pnl_pct < 0;
                      const pnlColor = isProfit ? 'var(--accent-green)' : isLoss ? 'var(--accent-red)' : 'var(--text-primary)';
                      const rowBg = idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)';

                      return (
                        <tr
                          key={idx}
                          style={{
                            borderBottom: '1px solid rgba(255,255,255,0.03)',
                            backgroundColor: rowBg,
                            transition: 'background-color 0.15s'
                          }}
                        >
                          <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                            {trade.date}
                          </td>
                          <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>
                            <Link to={`/chart/${encodeURIComponent(trade.ticker)}`} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>
                              {trade.ticker}
                            </Link>
                          </td>
                          <td style={{ padding: '10px 12px' }}>
                            <span style={{
                              color: trade.action.toLowerCase() === 'buy' ? 'var(--accent)' : 'var(--accent-yellow)',
                              fontWeight: '600',
                              fontSize: '11px',
                              textTransform: 'uppercase'
                            }}>
                              {trade.action}
                            </span>
                          </td>
                          <td style={{ padding: '10px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                            ${trade.price.toFixed(2)}
                          </td>
                          <td style={{ padding: '10px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
                            {trade.size.toLocaleString()}
                          </td>
                          <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                            {isSell ? renderReasonBadge(trade.reason) : <span style={{ color: 'var(--text-muted)' }}>-</span>}
                          </td>
                          <td style={{ padding: '10px 12px', textAlign: 'right', fontWeight: '600', color: pnlColor, fontVariantNumeric: 'tabular-nums' }}>
                            {isSell ? (
                              <>
                                {isProfit ? '+' : ''}
                                {trade.pnl_pct.toFixed(2)}%
                              </>
                            ) : (
                              <span style={{ color: 'var(--text-muted)' }}>-</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td colSpan={7} style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>
                        該当する取引ログが見つかりません
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};
