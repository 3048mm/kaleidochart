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
  fetchScenarios,
  fetchScenarioSummary,
  fetchScenarioEquity,
  fetchScenarioTrades,
  BacktestScenarioSummary,
  BacktestEquityPoint,
  BacktestTradeLogItem
} from '../api/backtest';

export const BacktestResultPage: React.FC = () => {
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string>('latest');
  const [summary, setSummary] = useState<BacktestScenarioSummary | null>(null);
  const [equityData, setEquityData] = useState<BacktestEquityPoint[]>([]);
  const [tradeLogs, setTradeLogs] = useState<BacktestTradeLogItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>('');

  // Chart Display Toggles
  const [showEquity, setShowEquity] = useState<boolean>(true);
  const [showCash, setShowCash] = useState<boolean>(true);
  const [showSpy, setShowSpy] = useState<boolean>(true);
  const [showTrendScore, setShowTrendScore] = useState<boolean>(false);

  // Filters for Trade Logs
  const [filterTicker, setFilterTicker] = useState<string>('');
  const [filterReason, setFilterReason] = useState<string>('all');

  // Load scenarios on mount
  useEffect(() => {
    fetchScenarios()
      .then((data) => {
        setScenarios(data);
      })
      .catch((err) => {
        console.error('Failed to load scenarios:', err);
        setError('シナリオ一覧の取得に失敗しました。');
      });
  }, []);

  // Fetch results when selected scenario changes
  useEffect(() => {
    setLoading(true);
    setError('');
    
    Promise.all([
      fetchScenarioSummary(selectedScenario),
      fetchScenarioEquity(selectedScenario),
      fetchScenarioTrades(selectedScenario)
    ])
      .then(([sum, eq, tr]) => {
        setSummary(sum);
        setEquityData(eq);
        setTradeLogs(tr);
      })
      .catch((err) => {
        console.error('Failed to load scenario details:', err);
        setError('シミュレーション結果の読み込みに失敗しました。バッチファイル実行成果物が出力されているかご確認ください。');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [selectedScenario]);

  // Handle scenario selector change
  const handleScenarioChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedScenario(e.target.value);
  };

  // Filtered trades list
  const filteredTrades = tradeLogs.filter((trade) => {
    const matchTicker = trade.ticker.toLowerCase().includes(filterTicker.toLowerCase());
    const matchReason = filterReason === 'all' || trade.reason === filterReason;
    return matchTicker && matchReason;
  });

  // Unique exit reasons for filtering dropdown
  const uniqueReasons = Array.from(new Set(tradeLogs.map((t) => t.reason).filter(Boolean)));

  // Helper for KPI styling
  const renderKPICard = (title: string, value: string | number, subtext: string, type: 'good' | 'bad' | 'neutral' = 'neutral') => {
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
          cursor: 'default'
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'translateY(-2px)';
          e.currentTarget.style.boxShadow = '0 8px 24px rgba(0,0,0,0.4)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'translateY(0)';
          e.currentTarget.style.boxShadow = 'none';
        }}
      >
        {/* Subtle top indicator bar */}
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
        <div style={{ fontSize: '28px', fontWeight: 'bold', color: color, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
          {value}
        </div>
        <div style={{ fontSize: '11px', marginTop: '6px', color: 'var(--text-muted)' }}>
          {subtext}
        </div>
      </div>
    );
  };

  // Helper for Exit Reason Badges
  const renderReasonBadge = (reason: string) => {
    let bg = 'rgba(255, 255, 255, 0.08)';
    let color = 'var(--text-secondary)';
    
    if (reason === 'stop_loss' || reason.includes('stop')) {
      bg = 'rgba(244, 63, 94, 0.12)';
      color = 'var(--accent-red)';
    } else if (reason.includes('take_profit') || reason.includes('trim') || reason.includes('partial')) {
      bg = 'rgba(34, 211, 160, 0.12)';
      color = 'var(--accent-green)';
    } else if (reason.includes('time') || reason.includes('timeout')) {
      bg = 'rgba(245, 158, 11, 0.12)';
      color = 'var(--accent-yellow)';
    }
    
    return (
      <span style={{
        padding: '3px 8px',
        borderRadius: '12px',
        fontSize: '10px',
        fontWeight: 'bold',
        backgroundColor: bg,
        color: color,
        display: 'inline-block',
        textTransform: 'capitalize'
      }}>
        {reason.replace('_', ' ')}
      </span>
    );
  };

  // Custom tooltips for Recharts
  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      const initialEquity = equityData.length > 0 ? equityData[0].equity : 1;
      const totalReturn = ((data.equity - initialEquity) / initialEquity) * 100;
      
      const initialSpy = (equityData.length > 0 && equityData[0].spy_equity) ? equityData[0].spy_equity : null;
      const spyReturn = (initialSpy && data.spy_equity) ? ((data.spy_equity - initialSpy) / initialSpy) * 100 : null;

      return (
        <div style={{
          background: 'rgba(15, 23, 42, 0.95)',
          border: '1px solid var(--border-accent)',
          borderRadius: '8px',
          padding: '12px',
          boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
          backdropFilter: 'blur(10px)'
        }}>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: 'bold' }}>
            {data.date}
          </div>
          
          {showEquity && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Strategy (純資産):</span>
              <span style={{ color: 'var(--accent-green)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}
          
          {showCash && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Cash (手元資金):</span>
              <span style={{ color: 'rgba(255,255,255,0.7)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.cash.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {showSpy && data.spy_equity !== undefined && data.spy_equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>SPY Benchmark:</span>
              <span style={{ color: '#3b82f6', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.spy_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {showTrendScore && data.trend_score !== undefined && data.trend_score !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Trend Score:</span>
              <span style={{ color: 'var(--accent-yellow)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                {data.trend_score.toFixed(1)}
              </span>
            </div>
          )}

          <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '4px', marginTop: '4px' }}>
            {showEquity && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '2px' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Strategy Return:</span>
                <span style={{ color: totalReturn >= 0 ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                  {totalReturn >= 0 ? '+' : ''}{totalReturn.toFixed(2)}%
                </span>
              </div>
            )}
            {showSpy && spyReturn !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>SPY Return:</span>
                <span style={{ color: spyReturn >= 0 ? '#3b82f6' : 'var(--accent-red)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                  {spyReturn >= 0 ? '+' : ''}{spyReturn.toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="dashboard-page" style={{ padding: '20px', width: '100%', maxWidth: '1200px', margin: '0 auto', boxSizing: 'border-box' }}>
      
      {/* Header section with Dropdown */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px', flexWrap: 'wrap', gap: '15px' }}>
        <div>
          <h1 style={{ margin: 0, background: 'linear-gradient(135deg, var(--accent-green), var(--accent))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text', fontWeight: 'bold', fontSize: '24px' }}>
            📊 Scenario Test Results
          </h1>
          <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>シナリオテスト結果</span>
        </div>
        
        {/* Scenario selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <label style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: '600' }}>Select Run:</label>
          <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.04)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border)' }}>
            <select
              value={selectedScenario}
              onChange={handleScenarioChange}
              style={{
                background: 'transparent',
                border: 'none',
                color: '#fff',
                fontSize: '13px',
                outline: 'none',
                cursor: 'pointer',
                fontFamily: 'var(--font)'
              }}
            >
              <option value="latest" style={{ backgroundColor: 'var(--bg-surface)' }}>🔄 Latest (最新の結果を自動ロード)</option>
              {scenarios.map((name) => (
                <option key={name} value={name} style={{ backgroundColor: 'var(--bg-surface)' }}>
                  📁 {name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {error && (
        <div className="glass-panel" style={{ padding: '20px', borderLeft: '4px solid var(--accent-red)', marginBottom: '20px', color: 'var(--text-primary)' }}>
          <span style={{ fontWeight: 'bold', color: 'var(--accent-red)', display: 'block', marginBottom: '4px' }}>⚠️ 読み込みエラー</span>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '350px', gap: '15px' }}>
          <div className="spinner" />
          <span style={{ color: 'var(--text-secondary)', fontSize: '12px', letterSpacing: '0.05em' }}>シナリオデータを高度解析中...</span>
        </div>
      ) : summary ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          
          {/* KPI Metrics Row */}
          <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
            {renderKPICard(
              'CAGR (年平均成長率)', 
              `${(summary.cagr * 100).toFixed(2)}%`, 
              'ベンチマークを凌駕する実質年利換算値',
              summary.cagr >= 0 ? 'good' : 'bad'
            )}
            {renderKPICard(
              'Profit Factor', 
              summary.profit_factor.toFixed(2), 
              '総利益 / 総損失の比率（期待値）',
              summary.profit_factor >= 1.0 ? 'good' : 'bad'
            )}
            {renderKPICard(
              'Max Drawdown (最悪下落率)', 
              `${(summary.max_drawdown * 100).toFixed(2)}%`, 
              'ピークからの最大口座下落幅',
              summary.max_drawdown >= -0.2 ? 'neutral' : 'bad'
            )}
            {renderKPICard(
              'Win Rate (勝率)', 
              `${(summary.win_rate * 100).toFixed(1)}%`, 
              '全決済取引における利益取引の割合',
              summary.win_rate >= 0.5 ? 'good' : 'neutral'
            )}
            {renderKPICard(
              'Total Trades (総決済回数)', 
              summary.total_trades, 
              '決済が確定した累計ポジション数',
              'neutral'
            )}
          </div>

          {/* Equity Chart Section */}
          <div className="glass-panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px', flexWrap: 'wrap', gap: '15px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  📈 Equity Curve & Portfolio Cash Allocation
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>純資産と余力のバランス・推移</span>
              </div>
              
              {/* Premium Glassmorphic Toggle Switches */}
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
                  <span style={{ 
                    width: '6px', 
                    height: '6px', 
                    borderRadius: '50%', 
                    backgroundColor: showEquity ? 'var(--accent-green)' : 'transparent',
                    border: `1px solid ${showEquity ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block' 
                  }} />
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
                  <span style={{ 
                    width: '6px', 
                    height: '6px', 
                    borderRadius: '50%', 
                    backgroundColor: showCash ? '#fff' : 'transparent',
                    border: `1px solid ${showCash ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block' 
                  }} />
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
                  <span style={{ 
                    width: '6px', 
                    height: '6px', 
                    borderRadius: '50%', 
                    backgroundColor: showSpy ? '#3b82f6' : 'transparent',
                    border: `1px solid ${showSpy ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block' 
                  }} />
                  SPY Benchmark
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
                  <span style={{ 
                    width: '6px', 
                    height: '6px', 
                    borderRadius: '50%', 
                    backgroundColor: showTrendScore ? 'var(--accent-yellow)' : 'transparent',
                    border: `1px solid ${showTrendScore ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block' 
                  }} />
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
                        <stop offset="5%" stopColor="var(--accent-green)" stopOpacity={0.35}/>
                        <stop offset="95%" stopColor="var(--accent-green)" stopOpacity={0.01}/>
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
                    {showEquity && (
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
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '250px', color: 'var(--text-muted)' }}>
                資産曲線データがありません。
              </div>
            )}
          </div>

          {/* Yearly Performance Section */}
          {summary.yearly_performance && Object.keys(summary.yearly_performance).length > 0 && (
            <div className="glass-panel" style={{ padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px' }}>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  📅 Annual Performance Comparison vs SPY
                </h3>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>年度ごとの戦略リターンとS&P 500の対比</span>
              </div>
              
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      <th style={{ padding: '8px 12px', fontWeight: '600' }}>Year</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Total Trades</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Win Rate</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Profit Factor</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Avg Trade PnL%</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Strategy Net PnL</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Strategy Return</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>SPY Return</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'center' }}>vs SPY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(summary.yearly_performance).sort().map((year, idx) => {
                      const item = summary.yearly_performance![year];
                      const isProfit = item.net_pnl > 0;
                      const isLoss = item.net_pnl < 0;
                      
                      const strategyReturn = item.return_pct ?? 0.0;
                      const isStrategyReturnPositive = strategyReturn >= 0;
                      
                      const pnlColor = isProfit ? 'var(--accent-green)' : isLoss ? 'var(--accent-red)' : 'var(--text-primary)';
                      const pfColor = item.profit_factor >= 1.0 ? 'var(--accent-green)' : 'var(--accent-red)';
                      const spyColor = item.spy_return_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                      const strategyReturnColor = isStrategyReturnPositive ? 'var(--accent-green)' : 'var(--accent-red)';
                      
                      const rowBg = idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)';
                      
                      // Precise outperformance comparison logic (Strategy Return vs SPY Return)
                      let vsSpyLabel = 'GAIN';
                      let vsSpyBg = 'rgba(34, 211, 160, 0.12)';
                      let vsSpyColor = 'var(--accent-green)';
                      
                      if (strategyReturn > item.spy_return_pct) {
                        vsSpyLabel = '🏆 OUTPERFORM';
                        vsSpyBg = 'rgba(0, 255, 136, 0.18)';
                        vsSpyColor = '#00ff88';
                      } else if (strategyReturn <= 0) {
                        vsSpyLabel = 'LOSS';
                        vsSpyBg = 'rgba(244, 63, 94, 0.12)';
                        vsSpyColor = 'var(--accent-red)';
                      } else {
                        vsSpyLabel = 'UNDERPERFORM';
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
                          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.03)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = rowBg; }}
                        >
                          <td style={{ padding: '12px 12px', fontWeight: 'bold', color: 'var(--text-primary)' }}>
                            {year}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
                            {item.total_trades}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
                            {(item.win_rate * 100).toFixed(1)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '600', color: pfColor, fontVariantNumeric: 'tabular-nums' }}>
                            {item.profit_factor.toFixed(2)}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '600', color: item.avg_pnl_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)', fontVariantNumeric: 'tabular-nums' }}>
                            {item.avg_pnl_pct >= 0 ? '+' : ''}{item.avg_pnl_pct.toFixed(2)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '700', color: pnlColor, fontVariantNumeric: 'tabular-nums' }}>
                            {item.net_pnl >= 0 ? '+' : ''}${item.net_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '700', color: strategyReturnColor, fontVariantNumeric: 'tabular-nums' }}>
                            {strategyReturn >= 0 ? '+' : ''}{strategyReturn.toFixed(2)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '600', color: spyColor, fontVariantNumeric: 'tabular-nums' }}>
                            {item.spy_return_pct >= 0 ? '+' : ''}{item.spy_return_pct.toFixed(2)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'center' }}>
                            <span style={{
                              padding: '4px 10px',
                              borderRadius: '12px',
                              fontSize: '9px',
                              fontWeight: 'bold',
                              backgroundColor: vsSpyBg,
                              color: vsSpyColor,
                              display: 'inline-block'
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

          {/* Trade Logs Data Table */}
          <div className="glass-panel" style={{ padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px', flexWrap: 'wrap', gap: '15px' }}>
              <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                📜 Trade Transaction Logs
              </h3>
              
              {/* Tables filters */}
              <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                <input
                  type="text"
                  placeholder="🔍 Ticker..."
                  value={filterTicker}
                  onChange={(e) => setFilterTicker(e.target.value)}
                  style={{
                    backgroundColor: 'rgba(255,255,255,0.05)',
                    border: '1px solid var(--border)',
                    borderRadius: '6px',
                    padding: '5px 10px',
                    color: '#fff',
                    fontSize: '11px',
                    outline: 'none',
                    width: '100px'
                  }}
                />
                
                <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.05)', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border)' }}>
                  <select
                    value={filterReason}
                    onChange={(e) => setFilterReason(e.target.value)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--text-secondary)',
                      fontSize: '11px',
                      outline: 'none',
                      cursor: 'pointer'
                    }}
                  >
                    <option value="all" style={{ backgroundColor: 'var(--bg-surface)' }}>🎯 All Exit Reasons</option>
                    {uniqueReasons.map((reason) => (
                      <option key={reason} value={reason} style={{ backgroundColor: 'var(--bg-surface)' }}>
                        {reason.replace('_', ' ')}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {filteredTrades.length > 0 ? (
              <div style={{ overflowX: 'auto', maxHeight: '450px', overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      <th style={{ padding: '8px 12px', fontWeight: '600' }}>Date</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600' }}>Ticker</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600' }}>Action</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Price</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Size</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'center' }}>Exit Reason</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>PnL%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTrades.map((trade, idx) => {
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
                          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.03)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = rowBg; }}
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
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '150px', color: 'var(--text-muted)' }}>
                条件にマッチする売買ログはありません。
              </div>
            )}
          </div>

        </div>
      ) : null}
    </div>
  );
};
