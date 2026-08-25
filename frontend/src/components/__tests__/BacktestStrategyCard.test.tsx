import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { BacktestStrategyCard } from '../BacktestStrategyCard';

// ETF タブとシナリオタブで同じ骨格を使うためのカード。
// 「ミニグラフ → 主結果(CAGR/Max DD) → 2行目 → 3行目 → 末尾(Final Capital + 総ゲイン)」
// の並びと単位（% 表記の実数）を固定する。

const baseProps = {
  cardId: 'test',
  label: 'VXV ratio ema',
  color: '#22d3a0',
  cagrPct: 18.24,
  maxDrawdownPct: -22.13,
  rows: [
    [{ label: 'Sharpe', value: '0.92' }, { label: 'Time in Market', value: '74.3%' }],
    [{ label: 'Rebalances', value: '41' }, { label: 'Regime Changes', value: '18' }],
  ],
  finalCapital: 284120,
  totalReturnPct: 184.12,
};

describe('BacktestStrategyCard', () => {
  it('主結果として CAGR と Max DD を出す', () => {
    render(<BacktestStrategyCard {...baseProps} />);

    expect(screen.getByText('CAGR')).toBeTruthy();
    expect(screen.getByText('+18.2%')).toBeTruthy();
    expect(screen.getByText('Max DD')).toBeTruthy();
    // Max DD は符号付きで出す（従来シナリオ側は絶対値表示だった）
    expect(screen.getByText('-22.1%')).toBeTruthy();
  });

  it('2行目・3行目のメトリクスをラベル付きで出す', () => {
    render(<BacktestStrategyCard {...baseProps} />);

    expect(screen.getByText('Sharpe')).toBeTruthy();
    expect(screen.getByText('0.92')).toBeTruthy();
    expect(screen.getByText('Time in Market')).toBeTruthy();
    expect(screen.getByText('Rebalances')).toBeTruthy();
    expect(screen.getByText('Regime Changes')).toBeTruthy();
  });

  it('末尾に Final Capital と総ゲインを出す', () => {
    render(<BacktestStrategyCard {...baseProps} />);

    expect(screen.getByText('Final Capital')).toBeTruthy();
    expect(screen.getByText('$284,120')).toBeTruthy();
    expect(screen.getByText('(+184.1%)')).toBeTruthy();
  });

  it('値が無い指標は — にフォールバックし、落ちない', () => {
    render(
      <BacktestStrategyCard
        {...baseProps}
        cagrPct={null}
        maxDrawdownPct={null}
        finalCapital={null}
        rows={[[{ label: 'Avg Trade', value: '—' }, { label: 'Win Rate', value: '—' }]]}
      />
    );

    expect(screen.queryAllByText('—').length).toBeGreaterThan(0);
    // finalCapital が無ければ末尾ブロックごと出さない
    expect(screen.queryByText('Final Capital')).toBeNull();
  });

  it('cagrRangePct を渡すと Monte Carlo の best / worst を添える', () => {
    render(<BacktestStrategyCard {...baseProps} cagrRangePct={{ max: 25.5, min: 4.1 }} />);

    expect(screen.getByText('+25.5%')).toBeTruthy();
    expect(screen.getByText('+4.1%')).toBeTruthy();
  });

  it('onClick を渡すとカード全体がクリック可能になる', () => {
    const onClick = vi.fn();
    render(<BacktestStrategyCard {...baseProps} icon="💎" onClick={onClick} />);

    fireEvent.click(screen.getByText(/VXV ratio ema/));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('badge を渡すと右上に表示する', () => {
    render(<BacktestStrategyCard {...baseProps} badge="BEST CAGR" />);
    expect(screen.getByText('BEST CAGR')).toBeTruthy();
  });
});
