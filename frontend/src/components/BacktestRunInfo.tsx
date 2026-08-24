import React, { useState } from 'react';

/**
 * バックテストの実行条件バー（ETF Backtest タブ / Scenario Test タブ 共通）
 *
 * PERIOD / TRADING DAYS / INITIAL CAP / TAX RATE を出す。
 * 毎回見る値ではないので **デフォルトは閉じた状態**で、ヘッダのタップで展開する。
 */

export interface RunInfoItem {
  label: string;
  value: string;
}

export interface BacktestRunInfoProps {
  items: RunInfoItem[];
  /** ヘッダ右側に添える補足（例: "10 Monte Carlo runs"）。無ければ非表示 */
  note?: string;
}

export const BacktestRunInfo: React.FC<BacktestRunInfoProps> = ({ items, note }) => {
  const [open, setOpen] = useState(false);

  if (items.length === 0) return null;

  return (
    <div style={{ marginBottom: '20px' }}>
      <button
        type="button"
        onClick={() => setOpen(prev => !prev)}
        aria-expanded={open}
        style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          padding: '6px 12px', borderRadius: '8px', cursor: 'pointer',
          background: 'rgba(15, 23, 42, 0.4)',
          border: '1px solid rgba(99, 120, 180, 0.18)',
          color: '#8b9cc8', fontSize: '11px', fontWeight: 600,
          fontFamily: 'inherit', letterSpacing: '0.06em', textTransform: 'uppercase',
          transition: 'all 0.2s',
        }}
      >
        <span style={{
          display: 'inline-block', transition: 'transform 0.2s',
          transform: open ? 'rotate(90deg)' : 'none',
        }}>▶</span>
        Run Info
        {note && (
          <span style={{ color: '#475685', fontWeight: 500, textTransform: 'none', letterSpacing: 0 }}>
            {note}
          </span>
        )}
      </button>

      {open && (
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginTop: '10px' }}>
          {items.map(item => (
            <div key={item.label} style={{
              background: 'rgba(15, 23, 42, 0.4)', borderRadius: 8, padding: '8px 14px',
              border: '1px solid rgba(99, 120, 180, 0.12)',
            }}>
              <div style={{
                fontSize: 9, fontWeight: 600, letterSpacing: '0.06em',
                textTransform: 'uppercase', color: '#475685',
              }}>
                {item.label}
              </div>
              <div style={{
                fontSize: 13, fontWeight: 600, color: '#e8edf7', fontVariantNumeric: 'tabular-nums',
              }}>
                {item.value}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
