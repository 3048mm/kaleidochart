/**
 * IPO 追加候補のレビュー画面。
 *
 * 週次スキャン（`backend/scripts/scan_ipo_candidates.py`）が SEC マスタとの差分から
 * 検知した新規上場銘柄を、人間が採用/却下する。採用すると `symbols_master` に入り、
 * 翌日の T1 同期で `stocktool.db` へ伝わる。
 *
 * `UniversePage.tsx` は既に 1,600 行あるので**別ファイルにしている**。
 * 両画面はトップレベルタブで切り替える。
 */

import { useCallback, useEffect, useState } from 'react';
import {
  acceptCandidate,
  bulkReviewCandidates,
  fetchCandidateStats,
  fetchCandidates,
  rejectCandidate,
  type CandidateStats,
  type IpoCandidate,
} from '../api/universe';

const PAGE_SIZE = 100;

/** status ごとの表示名。既定は未レビュー（pending）だけを見せる。 */
const STATUS_TABS: { key: string; label: string; hint: string }[] = [
  { key: 'pending', label: '未レビュー', hint: 'まだ判断していない候補' },
  { key: 'auto_excluded', label: '自動除外', hint: 'SPAC・ファンド・ADR と判定したもの' },
  { key: 'rejected', label: '却下済み', hint: '見送った候補。ここから復活できる' },
  { key: 'accepted', label: '採用済み', hint: 'symbols_master に追加済み' },
];

const FLAG_LABEL: Record<string, string> = {
  spac: 'SPAC',
  fund: 'ファンド',
  adr: 'ADR',
};

function fmtNum(v: number | null, suffix = ''): string {
  if (v == null) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B${suffix}`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M${suffix}`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K${suffix}`;
  return `${v.toLocaleString()}${suffix}`;
}

/** 上場からの経過日数。IPO 直後は指標が揃わないので、その目安として出す。 */
function daysSince(iso: string | null): string {
  if (!iso) return '—';
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  return d < 0 ? '—' : `${d}日`;
}

export function UniverseCandidatesPage() {
  const [items, setItems] = useState<IpoCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<CandidateStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [status, setStatus] = useState('pending');
  const [listedFrom, setListedFrom] = useState('');
  const [minMarketCap, setMinMarketCap] = useState('');
  const [page, setPage] = useState(1);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<number | null>(null);
  const [themeInput, setThemeInput] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [res, st] = await Promise.all([
        fetchCandidates({
          status,
          listed_from: listedFrom || undefined,
          min_market_cap: minMarketCap ? Number(minMarketCap) : undefined,
          page,
          page_size: PAGE_SIZE,
        }),
        fetchCandidateStats(),
      ]);
      setItems(res.items);
      setTotal(res.total);
      setStats(st);
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status, listedFrom, minMarketCap, page]);

  useEffect(() => { void load(); }, [load]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => (prev.size === items.length ? new Set() : new Set(items.map((i) => i.id))));
  };

  const handleAccept = async (c: IpoCandidate) => {
    try {
      const themes = (themeInput[c.id] || '')
        .split(',').map((t) => t.trim()).filter(Boolean);
      await acceptCandidate(c.id, { category: '個別', themes });
      setNotice(`${c.ticker} を universe に追加しました${themes.length ? `（テーマ: ${themes.join(', ')}）` : ''}`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleReject = async (c: IpoCandidate) => {
    try {
      await rejectCandidate(c.id);
      setNotice(`${c.ticker} を却下しました（「却下済み」タブから戻せます）`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleBulk = async (action: 'accept' | 'reject') => {
    if (selected.size === 0) return;
    try {
      const r = await bulkReviewCandidates([...selected], action);
      const verb = action === 'accept' ? '追加' : '却下';
      setNotice(
        `${r.updated} 件を${verb}しました` +
        (r.skipped.length ? `（${r.skipped.length} 件はスキップ: ${r.skipped[0].detail}）` : ''),
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const th: React.CSSProperties = {
    padding: '8px 10px', textAlign: 'left', fontSize: '11px', fontWeight: 600,
    color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
  };
  const td: React.CSSProperties = {
    padding: '8px 10px', fontSize: '12px', color: 'var(--text-primary)',
    borderBottom: '1px solid var(--border)',
  };

  return (
    <div>
      {/* status タブ */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '14px', flexWrap: 'wrap' }}>
        {STATUS_TABS.map((t) => {
          const n = stats ? (stats[t.key as keyof CandidateStats] ?? 0) : 0;
          const on = status === t.key;
          return (
            <button
              key={t.key}
              title={t.hint}
              onClick={() => { setStatus(t.key); setPage(1); }}
              style={{
                padding: '6px 14px',
                background: on ? 'linear-gradient(135deg, var(--accent), #6366f1)' : 'var(--bg-glass)',
                border: on ? 'none' : '1px solid var(--border)',
                borderRadius: '20px',
                color: on ? '#fff' : 'var(--text-secondary)',
                fontSize: '12px', fontWeight: on ? 600 : 400, cursor: 'pointer',
              }}
            >
              {t.label}
              <span style={{ marginLeft: '6px', opacity: 0.75, fontSize: '11px' }}>{n}</span>
            </button>
          );
        })}
      </div>

      {/* フィルタ + 一括操作 */}
      <div style={{
        display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap',
        marginBottom: '12px', fontSize: '12px', color: 'var(--text-secondary)',
      }}>
        <label>
          上場日 ≧{' '}
          <input
            type="date" value={listedFrom}
            onChange={(e) => { setListedFrom(e.target.value); setPage(1); }}
            style={{
              padding: '4px 8px', background: 'var(--bg-glass)', border: '1px solid var(--border)',
              borderRadius: '4px', color: 'var(--text-primary)', fontSize: '12px',
            }}
          />
        </label>
        <label>
          時価総額 ≧{' '}
          <input
            type="number" placeholder="例: 100000000" value={minMarketCap}
            onChange={(e) => { setMinMarketCap(e.target.value); setPage(1); }}
            style={{
              padding: '4px 8px', width: '130px', background: 'var(--bg-glass)',
              border: '1px solid var(--border)', borderRadius: '4px',
              color: 'var(--text-primary)', fontSize: '12px',
            }}
          />
        </label>
        <span style={{ flex: 1 }} />
        {selected.size > 0 && (
          <>
            <span>{selected.size} 件選択中</span>
            <button
              onClick={() => void handleBulk('accept')}
              style={{
                padding: '6px 14px', background: 'linear-gradient(135deg, var(--accent), #6366f1)',
                border: 'none', borderRadius: 'var(--radius-sm)', color: '#fff',
                fontSize: '12px', fontWeight: 600, cursor: 'pointer',
              }}
            >
              まとめて追加
            </button>
            <button
              onClick={() => void handleBulk('reject')}
              style={{
                padding: '6px 14px', background: 'var(--bg-glass)',
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                color: 'var(--text-primary)', fontSize: '12px', cursor: 'pointer',
              }}
            >
              まとめて却下
            </button>
          </>
        )}
      </div>

      {error && (
        <div style={{
          padding: '10px 16px', background: 'rgba(244, 63, 94, 0.15)',
          border: '1px solid var(--accent-red)', borderRadius: 'var(--radius-sm)',
          color: 'var(--accent-red)', fontSize: '12px', marginBottom: '12px',
          display: 'flex', justifyContent: 'space-between',
        }}>
          <span>{error}</span>
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', color: 'var(--accent-red)', cursor: 'pointer' }}>×</button>
        </div>
      )}
      {notice && (
        <div style={{
          padding: '10px 16px', background: 'rgba(16, 185, 129, 0.12)',
          border: '1px solid var(--accent-green, #10b981)', borderRadius: 'var(--radius-sm)',
          color: 'var(--accent-green, #10b981)', fontSize: '12px', marginBottom: '12px',
          display: 'flex', justifyContent: 'space-between',
        }}>
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>×</button>
        </div>
      )}

      {loading && <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)' }}>読み込み中…</div>}

      {!loading && items.length === 0 && (
        <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
          該当する候補はありません。
          <div style={{ marginTop: '8px', fontSize: '11px' }}>
            候補は週次メンテナンス（<code>weekly_maintenance.py</code>）が自動で追加します。
          </div>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr>
                <th style={{ ...th, width: '30px' }}>
                  <input type="checkbox" checked={selected.size === items.length} onChange={toggleAll} />
                </th>
                <th style={th}>ティッカー</th>
                <th style={th}>会社名</th>
                <th style={th}>取引所</th>
                <th style={th}>上場日</th>
                <th style={th}>経過</th>
                <th style={{ ...th, textAlign: 'right' }}>時価総額</th>
                <th style={{ ...th, textAlign: 'right' }}>平均出来高</th>
                <th style={{ ...th, textAlign: 'right' }}>株価</th>
                <th style={th}>セクタ</th>
                <th style={th}>フラグ</th>
                <th style={{ ...th, textAlign: 'center' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <>
                  <tr key={c.id} style={{ background: selected.has(c.id) ? 'rgba(99,102,241,0.08)' : undefined }}>
                    <td style={td}>
                      <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} />
                    </td>
                    <td style={{ ...td, fontWeight: 600 }}>
                      <button
                        onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                        title="企業概要を開く"
                        style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontWeight: 600, padding: 0 }}
                      >
                        {expanded === c.id ? '▾ ' : '▸ '}{c.ticker}
                      </button>
                    </td>
                    <td style={{ ...td, maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.name ?? ''}>
                      {c.name ?? '—'}
                    </td>
                    <td style={td}>{c.exchange ?? '—'}</td>
                    <td style={td}>{c.first_trade_date ?? '—'}</td>
                    <td style={{ ...td, color: 'var(--text-muted)' }}>{daysSince(c.first_trade_date)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(c.market_cap)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{fmtNum(c.avg_volume)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{c.last_price != null ? `$${c.last_price.toFixed(2)}` : '—'}</td>
                    <td style={{ ...td, color: 'var(--text-secondary)' }}>{c.sector ?? '—'}</td>
                    <td style={td}>
                      {c.flags.map((f) => (
                        <span key={f} style={{
                          display: 'inline-block', padding: '1px 7px', marginRight: '4px',
                          background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.4)',
                          borderRadius: '10px', fontSize: '10px', color: '#f59e0b',
                        }}>
                          {FLAG_LABEL[f] ?? f}
                        </span>
                      ))}
                    </td>
                    <td style={{ ...td, textAlign: 'center', whiteSpace: 'nowrap' }}>
                      <button
                        onClick={() => void handleAccept(c)}
                        style={{
                          padding: '3px 10px', marginRight: '4px', background: 'var(--accent)',
                          border: 'none', borderRadius: '4px', color: '#fff', fontSize: '11px', cursor: 'pointer',
                        }}
                      >
                        追加
                      </button>
                      <button
                        onClick={() => void handleReject(c)}
                        style={{
                          padding: '3px 10px', background: 'var(--bg-glass)',
                          border: '1px solid var(--border)', borderRadius: '4px',
                          color: 'var(--text-secondary)', fontSize: '11px', cursor: 'pointer',
                        }}
                      >
                        却下
                      </button>
                    </td>
                  </tr>
                  {expanded === c.id && (
                    <tr key={`${c.id}-detail`}>
                      <td colSpan={12} style={{ ...td, background: 'var(--bg-glass)', padding: '14px 20px' }}>
                        <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap', marginBottom: '10px', fontSize: '11px' }}>
                          <a href={`https://finance.yahoo.com/quote/${c.ticker}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                            Yahoo Finance ↗
                          </a>
                          {c.cik != null && (
                            <a
                              href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${c.cik}&type=&dateb=&owner=include&count=40`}
                              target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}
                            >
                              SEC EDGAR (CIK {c.cik}) ↗
                            </a>
                          )}
                          {c.website && (
                            <a href={c.website} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
                              公式サイト ↗
                            </a>
                          )}
                          {c.industry && <span style={{ color: 'var(--text-muted)' }}>業種: {c.industry}</span>}
                        </div>
                        <p style={{ fontSize: '12px', lineHeight: 1.7, color: 'var(--text-secondary)', margin: '0 0 10px' }}>
                          {c.summary ?? '企業概要は取得できていません。'}
                        </p>
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          <label style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            紐付けるテーマ（カンマ区切り、任意）:
                          </label>
                          <input
                            value={themeInput[c.id] ?? ''}
                            onChange={(e) => setThemeInput((p) => ({ ...p, [c.id]: e.target.value }))}
                            placeholder="_AIINFRA_, _NUCLEAR_"
                            style={{
                              padding: '4px 8px', width: '300px', background: 'var(--bg-base, #0d1117)',
                              border: '1px solid var(--border)', borderRadius: '4px',
                              color: 'var(--text-primary)', fontSize: '11px',
                            }}
                          />
                        </div>
                        {c.status_note && (
                          <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-muted)' }}>
                            メモ: {c.status_note}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: '8px', marginTop: '16px', fontSize: '12px' }}>
          <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
            style={{ padding: '4px 12px', background: 'var(--bg-glass)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--text-primary)', cursor: page <= 1 ? 'default' : 'pointer' }}>
            前へ
          </button>
          <span style={{ padding: '4px 8px', color: 'var(--text-muted)' }}>{page} / {totalPages}（全 {total} 件）</span>
          <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
            style={{ padding: '4px 12px', background: 'var(--bg-glass)', border: '1px solid var(--border)', borderRadius: '4px', color: 'var(--text-primary)', cursor: page >= totalPages ? 'default' : 'pointer' }}>
            次へ
          </button>
        </div>
      )}
    </div>
  );
}

export default UniverseCandidatesPage;
