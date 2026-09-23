import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { SummaryTable } from '../SummaryTable';

// min_periods_warmup 計画 5-23:
// NULL ランク（判定不能）を 0（最悪バケット）として表示・整列しない。
//   表示: '-'（色は中立）
//   ソート: 昇順・降順のどちらでも末尾

const base = {
  name: '', close: 100, change_pct: 1, change_1w_pct: 1, change_1m_pct: 1,
  dist_21ema_pct: 1, sparkline: [] as number[], intensity_score: null,
};

const items = [
  { ...base, id: 1, ticker: 'HIGH', rs_ratio_rank_e14: 0.9, rs_ratio_rank_e21: 0.9, rs_ratio_rank_e63: 0.9 },
  { ...base, id: 2, ticker: 'NULLED', rs_ratio_rank_e14: null, rs_ratio_rank_e21: null, rs_ratio_rank_e63: null },
  { ...base, id: 3, ticker: 'LOW', rs_ratio_rank_e14: 0.1, rs_ratio_rank_e21: 0.1, rs_ratio_rank_e63: 0.1 },
];

const renderTable = () =>
  render(
    <BrowserRouter>
      <SummaryTable
        items={items as any}
        maxPct={10}
        rankType="rs_ratio"
        defaultSortKey="rs_ratio_rank_e21"
        defaultSortDirection="desc"
      />
    </BrowserRouter>
  );

const tickerOrder = () =>
  screen.getAllByRole('link').map(l => l.textContent);

describe('SummaryTable: NULL ランクの扱い', () => {
  it('降順ソートで NULL は末尾になる', () => {
    renderTable();
    expect(tickerOrder()).toEqual(['HIGH', 'LOW', 'NULLED']);
  });

  it('昇順ソートでも NULL は末尾になる（0 として先頭に来ない）', () => {
    renderTable();
    // 同じ列のヘッダを再クリックすると desc -> asc
    // （ミニマップ見出し 'RSR21% (30d)' は並び替え対象ではないので除く）
    const header = screen.getAllByText(/RSR21%/).find(el => !el.textContent?.includes('(30d)'))!;
    fireEvent.click(header);
    expect(tickerOrder()).toEqual(['LOW', 'HIGH', 'NULLED']);
  });

  it('NULL ランクは 0 ではなく - と表示され、色は中立', () => {
    renderTable();
    const dashes = screen.getAllByText('-');
    // NULLED 行の 14 / 21 / 63 の3列
    expect(dashes).toHaveLength(3);
    for (const el of dashes) {
      // 中立色（#aaa）。good/bad の色分けを付けない
      expect(el.style.color).toBe('rgb(170, 170, 170)');
    }
    // 0 として表示されていない（HIGH=90 / LOW=10 は表示される）
    expect(screen.queryByText('0')).toBeNull();
    expect(screen.getAllByText('90')).toHaveLength(3);
    expect(screen.getAllByText('10')).toHaveLength(3);
  });
});
