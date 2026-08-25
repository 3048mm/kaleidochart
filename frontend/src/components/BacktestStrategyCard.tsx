import React from 'react';
import { Area, AreaChart, ResponsiveContainer } from 'recharts';

/**
 * バックテスト戦略カード（ETF Backtest タブ / Scenario Test タブ 共通）
 *
 * 骨格は両タブで固定する:
 *   ヘッダ → ミニグラフ → 主結果(CAGR / Max DD) → 2行目 → 3行目 → 末尾(Final Capital + 総ゲイン)
 * 行の中身だけを呼び出し側が差し替える。
 *
 * 数値の単位は **すべて % 表記の実数**（0.182 ではなく 18.2）に揃える。
 * ETF 側 API は % で、シナリオ側 API は率で返すため、率のものは呼び出し側で 100 倍して渡すこと。
 * Max DD は負値（-22.1）で渡す。
 */

export interface CardMetric {
  label: string;
  value: string;
  /** warn: 黄系（DD 等のリスク指標） / good: 緑 / bad: 赤 / 未指定: 通常色 */
  tone?: 'good' | 'bad' | 'warn';
}

export interface CardChartPoint {
  date: string;
  /** 戦略の equity（実線） */
  strategy: number | null;
  /** ベンチマークの equity（グレー点線）。ETF タブ=Buy & Hold、シナリオタブ=SPY */
  benchmark?: number | null;
}

export interface BacktestStrategyCardProps {
  /** gradient の id に使うためカード間でユニークな文字列 */
  cardId: string;
  label: string;
  color: string;
  icon?: string;
  chartData?: CardChartPoint[];
  /** 主結果・左（% 表記の実数） */
  cagrPct: number | null;
  /** 主結果・右（% 表記の実数、負値） */
  maxDrawdownPct: number | null;
  /** Monte Carlo の best / worst（% 表記の実数）。無ければ非表示 */
  cagrRangePct?: { max: number; min: number };
  /** 2行目・3行目。各行 1〜2 個 */
  rows: CardMetric[][];
  finalCapital?: number | null;
  /** 末尾に括弧書きする総ゲイン（% 表記の実数） */
  totalReturnPct?: number | null;
  /** 右上に出す小バッジ（例: BEST CAGR） */
  badge?: string;
  loading?: boolean;
  error?: string | null;
  isActive?: boolean;
  onClick?: () => void;
}

const BENCHMARK_COLOR = '#6b7280';

const fmtPctSigned = (v: number, decimals = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;

const fmtDollar = (v: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);

const toneColor = (tone?: CardMetric['tone']) => {
  if (tone === 'warn') return '#fbbf24';
  if (tone === 'good') return '#34d399';
  if (tone === 'bad') return '#f87171';
  return '#cbd5e1';
};

/** 主結果の大きい数値 1 つ分 */
const HeadlineMetric: React.FC<{ label: string; value: string; color: string }> = ({ label, value, color }) => (
  <div style={{ textAlign: 'center', minWidth: 0 }}>
    <div style={{
      fontSize: '24px', fontWeight: 800, color,
      lineHeight: 1.1, fontVariantNumeric: 'tabular-nums',
      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
    }}>
      {value}
    </div>
    <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px', letterSpacing: '0.05em' }}>
      {label}
    </div>
  </div>
);

/** 2行目・3行目のメトリクスタイル */
const MetricTile: React.FC<{ metric: CardMetric }> = ({ metric }) => (
  <div style={{ background: 'rgba(30, 41, 59, 0.5)', borderRadius: '6px', padding: '6px 8px', minWidth: 0 }}>
    <div style={{
      color: '#64748b', fontSize: '9px', letterSpacing: '0.05em',
      marginBottom: '2px', textTransform: 'uppercase',
      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
    }}>
      {metric.label}
    </div>
    <div style={{
      color: toneColor(metric.tone), fontWeight: 600, fontSize: '11px',
      fontVariantNumeric: 'tabular-nums',
    }}>
      {metric.value}
    </div>
  </div>
);

export const BacktestStrategyCard: React.FC<BacktestStrategyCardProps> = ({
  cardId, label, color, icon, chartData, cagrPct, maxDrawdownPct, cagrRangePct,
  rows, finalCapital, totalReturnPct, badge, loading, error, isActive, onClick,
}) => {
  const hasChart = !!chartData && chartData.length > 0;
  const hasBenchmark = hasChart && chartData!.some(p => p.benchmark !== undefined && p.benchmark !== null);

  return (
    <div
      onClick={onClick}
      style={{
        background: isActive ? 'rgba(30, 41, 59, 0.95)' : 'rgba(15, 23, 42, 0.85)',
        border: isActive ? `2px solid ${color}` : `1px solid ${color}33`,
        borderRadius: '12px',
        padding: isActive ? '15px' : '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px',
        backdropFilter: 'blur(12px)',
        boxShadow: isActive ? `0 0 25px ${color}30` : `0 0 20px ${color}15`,
        minWidth: 0,
        position: 'relative',
        cursor: onClick ? 'pointer' : 'default',
        transition: 'all 0.2s ease',
        transform: isActive ? 'scale(1.02)' : 'none',
      }}
    >
      {badge && (
        <span style={{
          position: 'absolute', top: 8, right: 10,
          fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
          background: color, color: '#000', borderRadius: 4, padding: '2px 7px',
        }}>{badge}</span>
      )}

      {/* ヘッダ */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
            background: color, display: 'inline-block', boxShadow: `0 0 8px ${color}`,
          }} />
          <span style={{
            fontWeight: 700, fontSize: '13px', color: '#e2e8f0', letterSpacing: '0.02em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {icon ? `${icon} ${label}` : label}
          </span>
        </div>
        {loading && (
          <span style={{
            width: 14, height: 14, border: `2px solid ${color}`, flexShrink: 0,
            borderTopColor: 'transparent', borderRadius: '50%',
            display: 'inline-block', animation: 'spin 0.8s linear infinite',
          }} />
        )}
        {error && <span title={error} style={{ color: '#ef4444', fontSize: '14px', flexShrink: 0 }}>⚠️</span>}
      </div>

      {/* ミニグラフ */}
      <div style={{ height: 80 }}>
        {hasChart ? (
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={chartData} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
              <defs>
                <linearGradient id={`bsc-grad-${cardId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="strategy" stroke={color} strokeWidth={1.5}
                fill={`url(#bsc-grad-${cardId})`} dot={false} isAnimationActive={false} />
              {hasBenchmark && (
                <Area type="monotone" dataKey="benchmark" stroke={BENCHMARK_COLOR} strokeWidth={1}
                  strokeDasharray="3 2" fill="none" dot={false} isAnimationActive={false} />
              )}
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

      {/* 主結果: CAGR / Max DD */}
      <div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
          <HeadlineMetric
            label="CAGR"
            value={cagrPct !== null && cagrPct !== undefined ? fmtPctSigned(cagrPct) : '—'}
            color={cagrPct !== null && cagrPct !== undefined ? (cagrPct >= 0 ? '#34d399' : '#f87171') : '#64748b'}
          />
          <HeadlineMetric
            label="Max DD"
            value={maxDrawdownPct !== null && maxDrawdownPct !== undefined ? `${maxDrawdownPct.toFixed(1)}%` : '—'}
            color={maxDrawdownPct !== null && maxDrawdownPct !== undefined ? '#fbbf24' : '#64748b'}
          />
        </div>
        {cagrRangePct && (
          <div style={{ fontSize: '10px', color: '#94a3b8', marginTop: '4px', textAlign: 'center' }}>
            <span style={{ color: '#34d399' }}>{fmtPctSigned(cagrRangePct.max)}</span>
            {' / '}
            <span style={{ color: '#f87171' }}>{fmtPctSigned(cagrRangePct.min)}</span>
          </div>
        )}
      </div>

      {/* 2行目・3行目 */}
      {rows.map((row, rowIdx) => (
        <div key={rowIdx} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          {row.map(metric => <MetricTile key={metric.label} metric={metric} />)}
        </div>
      ))}

      {/* 末尾: Final Capital + 総ゲイン */}
      {finalCapital !== undefined && finalCapital !== null && (
        <div style={{
          background: `${color}11`, borderRadius: '6px',
          padding: '6px 10px', display: 'flex', justifyContent: 'space-between',
          alignItems: 'center', gap: '8px', borderLeft: `3px solid ${color}`,
        }}>
          <span style={{ fontSize: '10px', color: '#64748b', letterSpacing: '0.05em' }}>Final Capital</span>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: '6px', minWidth: 0 }}>
            <span style={{ fontSize: '13px', fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
              {fmtDollar(finalCapital)}
            </span>
            {totalReturnPct !== undefined && totalReturnPct !== null && (
              <span style={{
                fontSize: '10px', fontWeight: 600, fontVariantNumeric: 'tabular-nums',
                color: totalReturnPct >= 0 ? '#34d399' : '#f87171',
              }}>
                ({fmtPctSigned(totalReturnPct)})
              </span>
            )}
          </span>
        </div>
      )}
    </div>
  );
};
