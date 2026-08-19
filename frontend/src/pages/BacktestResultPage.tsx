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

export const BacktestResultPage: React.FC<{ hideHeader?: boolean }> = ({ hideHeader }) => {
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string>('latest');
  const [selectedRegime, setSelectedRegime] = useState<string>('default');
  const [selectedSubRun, setSelectedSubRun] = useState<string>('all');

  const baseGroups = React.useMemo(() => ["A", "B1", "B2", "B3", "B4", "B5", "B6", "D", "E1", "E2", "G1", "G2", "G3"], []);
  const isGroup = baseGroups.includes(selectedStrategy);

  // 実質的なベースシナリオ名 (グループ + レジーム)
  const baseScenarioName = isGroup && selectedRegime !== 'default'
    ? `${selectedStrategy}_${selectedRegime}`
    : selectedStrategy;

  // モンテカルログループとしての判定
  const isMCGroup = isGroup;

  const targetRequestScenario = isMCGroup && selectedSubRun !== 'all'
    ? `${baseScenarioName}_run_${selectedSubRun}`
    : baseScenarioName;

  // 第1階層（Strategy / Base Scenarios）の選択肢
  const strategyOptions = React.useMemo(() => {
    const opts = new Set<string>();
    opts.add('latest');

    scenarios.forEach(name => {
      const matchedBase = baseGroups.find(bg => name === bg || name.startsWith(bg + '_'));
      if (matchedBase) {
        opts.add(matchedBase);
      } else if (name !== 'latest') {
        opts.add(name);
      }
    });

    return Array.from(opts);
  }, [scenarios, baseGroups]);

  // 第2階層（Regime / トレードシナリオ）の選択肢
  const regimeOptions = React.useMemo(() => {
    if (!baseGroups.includes(selectedStrategy)) return [];

    const opts = [{ value: 'default', label: 'デフォルト (レジームなし/単体)' }];

    scenarios.forEach(name => {
      if (name.startsWith(selectedStrategy + '_')) {
        const regimeKey = name.substring(selectedStrategy.length + 1);

        let label = regimeKey;
        if (regimeKey === 'full_position') label = 'Full Position (ポジション制限なし)';
        else if (regimeKey === 'spy_sma200') label = 'SPY from SMA 200';
        else if (regimeKey === 'spy_sma63') label = 'SPY from SMA 63';
        else if (regimeKey === 'vxv_vix_ema') label = 'VXV/VIX Ratio EMA';
        else if (regimeKey === 'mts_raw') label = 'MTS Raw (生データ)';
        else {
          label = regimeKey.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
        }

        opts.push({ value: regimeKey, label });
      }
    });

    return opts;
  }, [scenarios, selectedStrategy, baseGroups]);

  const [summary, setSummary] = useState<BacktestScenarioSummary | null>(null);
  const [equityData, setEquityData] = useState<BacktestEquityPoint[]>([]);
  const [tradeLogs, setTradeLogs] = useState<BacktestTradeLogItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>('');

  // Chart Display Toggles
  const [showEquity, setShowEquity] = useState<boolean>(true);
  const [showCash, setShowCash] = useState<boolean>(true);
  const [showSpy, setShowSpy] = useState<boolean>(true);
  const [showQqq, setShowQqq] = useState<boolean>(false);
  const [showTqqq, setShowTqqq] = useState<boolean>(false);
  const [showSoxl, setShowSoxl] = useState<boolean>(false);
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
      fetchScenarioSummary(targetRequestScenario),
      fetchScenarioEquity(targetRequestScenario),
      fetchScenarioTrades(targetRequestScenario)
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
  }, [selectedStrategy, selectedRegime, selectedSubRun, targetRequestScenario]);

  // Handle strategy selector change
  const handleStrategyChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedStrategy(e.target.value);
    setSelectedRegime('default');
    setSelectedSubRun('all');
  };

  // Handle regime selector change
  const handleRegimeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedRegime(e.target.value);
    setSelectedSubRun('all');
  };

  // Handle sub-run selector change
  const handleSubRunChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedSubRun(e.target.value);
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
        {mcInfo && (
          <div style={{ fontSize: '10px', marginTop: '4px', color: 'var(--accent-yellow)', fontWeight: '500' }}>
            {mcInfo}
          </div>
        )}
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

      // Find the first valid equity data point (simulation start date) to align return calculations
      const startPoint = equityData.find(d => d.equity !== undefined && d.equity !== null);
      const initialEquity = startPoint ? startPoint.equity : 1;
      const totalReturn = (data.equity !== undefined && data.equity !== null) ? ((data.equity - initialEquity) / initialEquity) * 100 : 0;

      const initialSpy = startPoint ? startPoint.spy_equity : null;
      const spyReturn = (initialSpy && data.spy_equity) ? ((data.spy_equity - initialSpy) / initialSpy) * 100 : null;

      const initialQqq = startPoint ? startPoint.qqq_equity : null;
      const qqqReturn = (initialQqq && data.qqq_equity) ? ((data.qqq_equity - initialQqq) / initialQqq) * 100 : null;

      const initialTqqq = startPoint ? startPoint.tqqq_equity : null;
      const tqqqReturn = (initialTqqq && data.tqqq_equity) ? ((data.tqqq_equity - initialTqqq) / initialTqqq) * 100 : null;

      const initialSoxl = startPoint ? startPoint.soxl_equity : null;
      const soxlReturn = (initialSoxl && data.soxl_equity) ? ((data.soxl_equity - initialSoxl) / initialSoxl) * 100 : null;

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

          {showEquity && data.equity !== undefined && data.equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>Strategy (純資産):</span>
              <span style={{ color: 'var(--accent-green)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {showCash && data.cash !== undefined && data.cash !== null && (
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

          {showQqq && data.qqq_equity !== undefined && data.qqq_equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>QQQ Benchmark:</span>
              <span style={{ color: '#f97316', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.qqq_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {showTqqq && data.tqqq_equity !== undefined && data.tqqq_equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>TQQQ Benchmark:</span>
              <span style={{ color: '#ec4899', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.tqqq_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          )}

          {showSoxl && data.soxl_equity !== undefined && data.soxl_equity !== null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px', marginBottom: '4px' }}>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>SOXL Benchmark:</span>
              <span style={{ color: '#a855f7', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                ${data.soxl_equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
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

          <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '4px', marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {showEquity && data.equity !== undefined && data.equity !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px' }}>
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
            {showQqq && qqqReturn !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>QQQ Return:</span>
                <span style={{ color: qqqReturn >= 0 ? '#f97316' : 'var(--accent-red)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                  {qqqReturn >= 0 ? '+' : ''}{qqqReturn.toFixed(2)}%
                </span>
              </div>
            )}
            {showTqqq && tqqqReturn !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>TQQQ Return:</span>
                <span style={{ color: tqqqReturn >= 0 ? '#ec4899' : 'var(--accent-red)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                  {tqqqReturn >= 0 ? '+' : ''}{tqqqReturn.toFixed(2)}%
                </span>
              </div>
            )}
            {showSoxl && soxlReturn !== null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '20px' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>SOXL Return:</span>
                <span style={{ color: soxlReturn >= 0 ? '#a855f7' : 'var(--accent-red)', fontWeight: 'bold', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                  {soxlReturn >= 0 ? '+' : ''}{soxlReturn.toFixed(2)}%
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
    <div className="dashboard-page" style={{ padding: hideHeader ? '0' : '20px', width: '100%', maxWidth: hideHeader ? '100%' : '1200px', margin: '0 auto', boxSizing: 'border-box' }}>

      {/* Header section with Dropdown */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px', flexWrap: 'wrap', gap: '15px' }}>
        {!hideHeader ? (
          <div>
            <h1 style={{ margin: 0, background: 'linear-gradient(135deg, var(--accent-green), var(--accent))', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text', fontWeight: 'bold', fontSize: '24px' }}>
              📊 Scenario Test Results
            </h1>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>シナリオテスト結果</span>
          </div>
        ) : <div />}

        {/* Hierarchical selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
          {/* 1st Level: Strategy / Base Scenario */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <label style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: '600' }}>戦略選択 (A〜E2):</label>
            <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.04)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border)' }}>
              <select
                value={selectedStrategy}
                onChange={handleStrategyChange}
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
                {strategyOptions.map((name) => {
                  const isMC = baseGroups.includes(name);
                  const displayName = isMC ? `${name} (Monte Carlo 10x)` : (name === 'latest' ? '🔄 Latest (最新結果を自動ロード)' : name);
                  return (
                    <option key={name} value={name} style={{ backgroundColor: 'var(--bg-surface)' }}>
                      {isMC ? '🎲' : '📁'} {displayName}
                    </option>
                  );
                })}
              </select>
            </div>
          </div>

          {/* 2nd Level: Regime (Trade Scenario) */}
          {isGroup && regimeOptions.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <label style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: '600' }}>トレードシナリオ:</label>
              <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.04)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border)' }}>
                <select
                  value={selectedRegime}
                  onChange={handleRegimeChange}
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
                  {regimeOptions.map((opt) => (
                    <option key={opt.value} value={opt.value} style={{ backgroundColor: 'var(--bg-surface)' }}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {/* 3rd Level: Monte Carlo Sub-run (Run 0-9) */}
          {isMCGroup && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <label style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: '600' }}>モンテカルロ個別/平均:</label>
              <div style={{ display: 'flex', alignItems: 'center', background: 'rgba(255,255,255,0.04)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border)' }}>
                <select
                  value={selectedSubRun}
                  onChange={handleSubRunChange}
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
                  <option value="all" style={{ backgroundColor: 'var(--bg-surface)' }}>🎲 10回平均 (統合ファンチャート)</option>
                  {[...Array(10)].map((_, idx) => (
                    <option key={idx} value={idx.toString()} style={{ backgroundColor: 'var(--bg-surface)' }}>
                      📁 Run {idx}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
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
              summary.is_monte_carlo ? '1取引平均 (10回平均)' : '1取引平均リターン',
              summary.avg_trade_pnl_pct !== undefined && summary.avg_trade_pnl_pct !== null
                ? `${summary.avg_trade_pnl_pct >= 0 ? '+' : ''}${summary.avg_trade_pnl_pct.toFixed(2)}%`
                : '—',
              '取引1回あたりの平均損益率（建玉に対するリターン）',
              summary.avg_trade_pnl_pct !== undefined && summary.avg_trade_pnl_pct !== null
                ? (summary.avg_trade_pnl_pct >= 0 ? 'good' : 'bad')
                : 'neutral',
              summary.is_monte_carlo && summary.avg_trade_pnl_pct_avg !== undefined && summary.avg_trade_pnl_pct_avg !== null
                ? `(平均: ${summary.avg_trade_pnl_pct_avg >= 0 ? '+' : ''}${summary.avg_trade_pnl_pct_avg.toFixed(2)}%)`
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
                  <span style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    backgroundColor: showQqq ? '#f97316' : 'transparent',
                    border: `1px solid ${showQqq ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block'
                  }} />
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
                  <span style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    backgroundColor: showTqqq ? '#ec4899' : 'transparent',
                    border: `1px solid ${showTqqq ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block'
                  }} />
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
                  <span style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    backgroundColor: showSoxl ? '#a855f7' : 'transparent',
                    border: `1px solid ${showSoxl ? 'transparent' : 'var(--text-muted)'}`,
                    display: 'inline-block'
                  }} />
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

          {/* Exit Reasons Statistics Section */}
          {summary.exit_reasons && Object.keys(summary.exit_reasons).length > 0 && (
            <div className="glass-panel" style={{ padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '20px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                    🎯 Exit Reason Statistics
                  </h3>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>決済（売却）理由別の回数・比率・平均損益・保有日数の集計サマリー</span>
                </div>
              </div>

              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', textAlign: 'left' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                      <th style={{ padding: '8px 12px', fontWeight: '600' }}>Exit Reason</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Trade Count</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Share %</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Avg. PnL %</th>
                      <th style={{ padding: '8px 12px', fontWeight: '600', textAlign: 'right' }}>Avg. Holding Days</th>
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
                          onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.03)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = rowBg; }}
                        >
                          <td style={{ padding: '12px 12px', fontWeight: 'bold' }}>
                            {renderReasonBadge(reason)}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-primary)' }}>
                            {stats.count}
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
                            {sharePct.toFixed(1)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontWeight: '700', color: pnlColor, fontVariantNumeric: 'tabular-nums' }}>
                            {stats.avg_pnl_pct >= 0 ? '+' : ''}{stats.avg_pnl_pct.toFixed(2)}%
                          </td>
                          <td style={{ padding: '12px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }}>
                            {stats.avg_holding_days.toFixed(1)} days
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
