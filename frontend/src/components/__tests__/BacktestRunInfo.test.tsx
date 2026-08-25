import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { BacktestRunInfo } from '../BacktestRunInfo';

// 実行条件バー（ETF タブ / シナリオタブ共通）。
// 毎回参照する値ではないため「デフォルト閉」を仕様として固定する。

const items = [
  { label: 'Period', value: '2022-01-01 → 2026-03-26' },
  { label: 'Trading Days', value: '1,062' },
  { label: 'Initial Cap', value: '$100,000' },
  { label: 'Tax Rate', value: '20%' },
];

describe('BacktestRunInfo', () => {
  it('デフォルトは閉じていて、中身を出さない', () => {
    render(<BacktestRunInfo items={items} />);

    expect(screen.getByText('Run Info')).toBeTruthy();
    expect(screen.queryByText('Period')).toBeNull();
    expect(screen.queryByText('$100,000')).toBeNull();
  });

  it('ヘッダのタップで 4 項目を展開し、もう一度で閉じる', () => {
    render(<BacktestRunInfo items={items} />);
    const header = screen.getByRole('button');

    fireEvent.click(header);
    expect(screen.getByText('Period')).toBeTruthy();
    expect(screen.getByText('Trading Days')).toBeTruthy();
    expect(screen.getByText('Initial Cap')).toBeTruthy();
    expect(screen.getByText('Tax Rate')).toBeTruthy();
    expect(screen.getByText('20%')).toBeTruthy();

    fireEvent.click(header);
    expect(screen.queryByText('Period')).toBeNull();
  });

  it('開閉状態を aria-expanded に反映する', () => {
    render(<BacktestRunInfo items={items} />);
    const header = screen.getByRole('button');

    expect(header.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(header);
    expect(header.getAttribute('aria-expanded')).toBe('true');
  });

  it('note を渡すとヘッダに補足を添える', () => {
    render(<BacktestRunInfo items={items} note="10 Monte Carlo runs" />);
    expect(screen.getByText('10 Monte Carlo runs')).toBeTruthy();
  });

  it('items が空なら何も描画しない（API が実行条件を返さない場合）', () => {
    const { container } = render(<BacktestRunInfo items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
