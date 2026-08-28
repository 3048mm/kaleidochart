import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UniverseCandidatesPage } from '../UniverseCandidatesPage';

/**
 * IPO 追加候補レビュー画面のテスト。
 *
 * データは E1（実データ 4,009 件のフルスケール実行）で実際に pending になった銘柄を使う。
 */

const EROC = {
  id: 1, ticker: 'EROC', exchange: 'NYSE', name: 'ERock, Inc.', cik: 2110029,
  first_trade_date: '2026-06-10', market_cap: 850000000, avg_volume: 1716508,
  last_price: 13.23, sector: 'Industrials', industry: 'Engineering',
  summary: '再生可能エネルギー関連の事業を行う。', website: 'https://example.com',
  flags: [] as string[], status: 'pending', status_note: null,
  detected_at: '2026-08-28T00:00:00', reviewed_at: null,
};
const ALPX = {
  ...EROC, id: 2, ticker: 'ALPX', name: 'Alpex Acquisition Corp',
  flags: ['spac'], status: 'auto_excluded', summary: null, website: null,
};

const STATS = { pending: 111, accepted: 0, rejected: 3, auto_excluded: 91 };

function mockApi(items = [EROC]) {
  return vi.fn((url: string, init?: RequestInit) => {
    if (url.includes('/candidates/stats')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(STATS) } as Response);
    }
    if (url.includes('/candidates/bulk')) {
      return Promise.resolve({
        ok: true, json: () => Promise.resolve({ updated: 2, skipped: [] }),
      } as Response);
    }
    if (/\/candidates\/\d+\/(accept|reject)/.test(url)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(EROC) } as Response);
    }
    if (url.includes('/candidates')) {
      const status = new URL(url, 'http://x').searchParams.get('status');
      const filtered = status === 'auto_excluded' ? [ALPX] : items;
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ items: filtered, total: filtered.length, page: 1, page_size: 100 }),
      } as Response);
    }
    return Promise.reject(new Error(`unexpected fetch: ${url} ${init?.method ?? ''}`));
  });
}

describe('UniverseCandidatesPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockApi());
  });

  it('未レビュー候補を一覧表示する', async () => {
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('ERock, Inc.')).toBeInTheDocument());
    expect(screen.getByText('2026-06-10')).toBeInTheDocument();
    expect(screen.getByText('NYSE')).toBeInTheDocument();
  });

  it('status タブに件数が出る', async () => {
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('111')).toBeInTheDocument());
    // 却下済みも件数が見え、非表示にされていないこと（トグルで復活できる前提）
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('91')).toBeInTheDocument();
  });

  it('自動除外タブに切り替えると SPAC フラグが見える', async () => {
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('ERock, Inc.')).toBeInTheDocument());

    fireEvent.click(screen.getByText('自動除外'));
    await waitFor(() => expect(screen.getByText('Alpex Acquisition Corp')).toBeInTheDocument());
    expect(screen.getByText('SPAC')).toBeInTheDocument();
  });

  it('ティッカーを押すと企業概要と外部リンクが開く', async () => {
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText(/EROC/)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/EROC/));
    await waitFor(() =>
      expect(screen.getByText('再生可能エネルギー関連の事業を行う。')).toBeInTheDocument());

    // テーマのタグ付け判断に使うので、SEC と Yahoo への導線が要る
    expect(screen.getByText(/SEC EDGAR \(CIK 2110029\)/)).toBeInTheDocument();
    expect(screen.getByText(/Yahoo Finance/)).toBeInTheDocument();
    expect(screen.getByText(/公式サイト/)).toBeInTheDocument();
  });

  it('追加ボタンで accept を呼ぶ', async () => {
    const f = mockApi();
    vi.stubGlobal('fetch', f);
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('ERock, Inc.')).toBeInTheDocument());

    fireEvent.click(screen.getByText('追加'));
    await waitFor(() =>
      expect(f.mock.calls.some(([u]) => String(u).includes('/candidates/1/accept'))).toBe(true));
  });

  it('却下ボタンで reject を呼び、復活できる旨を伝える', async () => {
    const f = mockApi();
    vi.stubGlobal('fetch', f);
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('ERock, Inc.')).toBeInTheDocument());

    fireEvent.click(screen.getByText('却下'));
    await waitFor(() =>
      expect(f.mock.calls.some(([u]) => String(u).includes('/candidates/1/reject'))).toBe(true));
    await waitFor(() =>
      expect(screen.getByText(/却下済み.*タブから戻せます/)).toBeInTheDocument());
  });

  it('チェックボックスで選ぶと一括操作が出る', async () => {
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText('ERock, Inc.')).toBeInTheDocument());

    const boxes = screen.getAllByRole('checkbox');
    fireEvent.click(boxes[1]);          // 0 番目はヘッダの全選択
    await waitFor(() => expect(screen.getByText('1 件選択中')).toBeInTheDocument());
    expect(screen.getByText('まとめて追加')).toBeInTheDocument();
  });

  it('候補が無ければ週次で追加される旨を案内する', async () => {
    vi.stubGlobal('fetch', mockApi([]));
    render(<UniverseCandidatesPage />);
    await waitFor(() =>
      expect(screen.getByText(/該当する候補はありません/)).toBeInTheDocument());
    expect(screen.getByText(/週次メンテナンス/)).toBeInTheDocument();
  });

  it('API が失敗したらエラーを表示する', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)));
    render(<UniverseCandidatesPage />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
  });
});
