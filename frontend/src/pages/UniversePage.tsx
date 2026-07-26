/**
 * Universe Manager – Main Page (v2)
 *
 * 銘柄マスタの CRUD + テーマ構成銘柄の管理を行う。
 * v2 改善:
 *   - テーマ行の展開ボタンを視認性の高い「Members」ボタンに変更
 *   - 個別銘柄にも展開パネルを追加（所属テーマの表示・追加・削除）
 *   - sector_etf 列を追加（インライン編集可能）
 */

import { useEffect, useState, useCallback, useRef } from 'react';
import {
  fetchSymbols,
  createSymbol,
  updateSymbol,
  deleteSymbol,
  fetchThemeMembers,
  addThemeMember,
  removeThemeMember,
  fetchSymbolThemes,
  fetchThemesBatch,
  fetchStats,
  previewImport,
  executeImport,
  exportToSpreadsheet,
  type SymbolMaster,
  type PaginatedSymbols,
  type ThemeMember,
  type UniverseStats,
  type ImportDiff,
  type ImportResult,
  type ExportResult,
} from '../api/universe';

/* ---------- Constants ---------- */

const CATEGORIES = ['全て', '市場', '指標', 'セクタ', 'テーマ', '個別', 'レバレッジ'] as const;
const PAGE_SIZE = 50;

const SORTABLE_COLUMNS: { key: string; label: string; width?: string }[] = [
  { key: 'ticker', label: 'Ticker', width: '120px' },
  { key: 'exchange', label: 'Exchange', width: '110px' },
  { key: 'name', label: 'Name' },
  { key: 'industry', label: 'Industry', width: '120px' },
  { key: 'sector_etf', label: 'Sector ETF', width: '100px' },
  { key: 'category', label: 'Category', width: '90px' },
  { key: 'active', label: 'Active', width: '60px' },
];

/* ---------- Component ---------- */

export function UniversePage() {
  // Data state
  const [data, setData] = useState<PaginatedSymbols | null>(null);
  const [stats, setStats] = useState<UniverseStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [activeCategory, setActiveCategory] = useState<string>('全て');
  const [searchQuery, setSearchQuery] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);
  const [sortBy, setSortBy] = useState('ticker');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [currentPage, setCurrentPage] = useState(1);

  // Inline editing
  const [editingCell, setEditingCell] = useState<{ id: number; field: string; originalValue: string } | null>(null);
  const [editValue, setEditValue] = useState('');
  const editInputRef = useRef<HTMLInputElement>(null);

  // Add symbol modal
  const [showAddModal, setShowAddModal] = useState(false);
  const [newSymbol, setNewSymbol] = useState({
    ticker: '', exchange: '', name: '', category: '個別', industry: '', sector_etf: '',
  });

  // Expansion panel (shared for both themes and stocks)
  const [expandedRow, setExpandedRow] = useState<{ ticker: string; type: 'theme' | 'stock' } | null>(null);
  const [panelMembers, setPanelMembers] = useState<ThemeMember[]>([]);
  const [panelLoading, setPanelLoading] = useState(false);
  const [newPanelTicker, setNewPanelTicker] = useState('');

  // Inline theme tags for stocks (batch loaded)
  const [inlineThemes, setInlineThemes] = useState<Record<string, string[]>>({});

  // Import modal states
  const [showImportModal, setShowImportModal] = useState(false);
  const [importStep, setImportStep] = useState<1 | 2 | 3>(1);
  const [importUrl, setImportUrl] = useState('');
  const [importMode, setImportMode] = useState<'upsert' | 'replace'>('upsert');
  const [importLoading, setImportLoading] = useState(false);
  const [importModalError, setImportModalError] = useState<string | null>(null);
  const [importDiff, setImportDiff] = useState<ImportDiff | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  // Debounced search
  const searchTimerRef = useRef<ReturnType<typeof setTimeout>>();

  /* ---------- Data fetching ---------- */

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchSymbols({
        category: activeCategory === '全て' ? undefined : activeCategory,
        search: searchQuery || undefined,
        include_inactive: includeInactive,
        sort_by: sortBy,
        sort_dir: sortDir,
        page: currentPage,
        page_size: PAGE_SIZE,
      });
      setData(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [activeCategory, searchQuery, includeInactive, sortBy, sortDir, currentPage]);

  useEffect(() => { loadData(); }, [loadData]);

  // Batch-load inline theme tags whenever data changes
  useEffect(() => {
    if (!data) return;
    const stockTickers = data.items
      .filter(s => s.category === '個別')
      .map(s => s.ticker);
    if (stockTickers.length === 0) {
      setInlineThemes({});
      return;
    }
    fetchThemesBatch(stockTickers)
      .then(setInlineThemes)
      .catch(() => setInlineThemes({}));
  }, [data]);

  useEffect(() => {
    fetchStats().then(setStats).catch(() => {});
  }, []);

  /* ---------- Handlers ---------- */

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setCurrentPage(1);
    }, 300);
  };

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortDir(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(column);
      setSortDir('asc');
    }
    setCurrentPage(1);
  };

  const handleCategoryChange = (cat: string) => {
    setActiveCategory(cat);
    setCurrentPage(1);
    setExpandedRow(null);
  };

  // Inline editing
  const startEdit = (id: number, field: string, currentValue: string) => {
    setEditingCell({ id, field, originalValue: currentValue });
    setEditValue(currentValue);
    setTimeout(() => editInputRef.current?.focus(), 50);
  };

  const saveEdit = async () => {
    if (!editingCell) return;
    
    // 変更がない場合は API を叩かずそのまま編集終了
    if (editValue.trim() === editingCell.originalValue.trim()) {
      setEditingCell(null);
      return;
    }

    try {
      const updatedSym = await updateSymbol(editingCell.id, { [editingCell.field]: editValue });
      
      // 全画面リロード(loadData)を行わず、対象行のみローカルStateを更新
      setData(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map(item => (item.id === editingCell.id ? updatedSym : item)),
        };
      });

      setEditingCell(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed');
    }
  };

  const cancelEdit = () => setEditingCell(null);

  // Add symbol
  const handleAddSymbol = async () => {
    if (!newSymbol.ticker.trim()) return;
    try {
      await createSymbol({
        ticker: newSymbol.ticker.trim().toUpperCase(),
        exchange: newSymbol.exchange.trim() || null,
        name: newSymbol.name.trim() || null,
        category: newSymbol.category,
        industry: newSymbol.industry.trim() || null,
        sector_etf: newSymbol.sector_etf.trim() || null,
      });
      setShowAddModal(false);
      setNewSymbol({ ticker: '', exchange: '', name: '', category: '個別', industry: '', sector_etf: '' });
      loadData();
      fetchStats().then(setStats).catch(() => {});
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Add failed');
    }
  };

  // Soft delete
  const handleDelete = async (sym: SymbolMaster) => {
    if (!confirm(`${sym.ticker} を非アクティブにしますか？`)) return;
    try {
      await deleteSymbol(sym.id);
      loadData();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  // Expansion panel – unified for themes and stocks
  const toggleExpansion = async (ticker: string, type: 'theme' | 'stock') => {
    if (expandedRow?.ticker === ticker && expandedRow?.type === type) {
      setExpandedRow(null);
      return;
    }
    setExpandedRow({ ticker, type });
    setPanelLoading(true);
    setNewPanelTicker('');
    try {
      const members = type === 'theme'
        ? await fetchThemeMembers(ticker)
        : await fetchSymbolThemes(ticker);
      setPanelMembers(members);
    } catch {
      setPanelMembers([]);
    } finally {
      setPanelLoading(false);
    }
  };

  /** Refresh inline theme chips for a specific stock ticker */
  const refreshInlineTheme = useCallback(async (ticker: string) => {
    try {
      const updated = await fetchThemesBatch([ticker]);
      setInlineThemes(prev => ({
        ...prev,
        [ticker]: updated[ticker] || [],
      }));
    } catch { /* ignore */ }
  }, []);

  // Import Handlers
  const handlePreviewImport = async () => {
    if (!importUrl.trim()) return;
    setImportLoading(true);
    setImportModalError(null);
    try {
      const diff = await previewImport(importUrl.trim(), importMode);
      setImportDiff(diff);
      setImportStep(2);
    } catch (e: unknown) {
      setImportModalError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setImportLoading(false);
    }
  };

  const handleExecuteImport = async () => {
    if (!importUrl.trim()) return;
    setImportLoading(true);
    setImportModalError(null);
    try {
      const res = await executeImport(importUrl.trim(), importMode);
      setImportResult(res);
      setImportStep(3);
      loadData();
      fetchStats().then(setStats).catch(() => {});
    } catch (e: unknown) {
      setImportModalError(e instanceof Error ? e.message : 'Execution failed');
    } finally {
      setImportLoading(false);
    }
  };

  const resetImportModal = () => {
    setShowImportModal(false);
    setImportStep(1);
    setImportUrl('');
    setImportMode('upsert');
    setImportLoading(false);
    setImportModalError(null);
    setImportDiff(null);
    setImportResult(null);
  };

  // Export modal states
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportUrl, setExportUrl] = useState('');
  const [exportLoading, setExportLoading] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);

  const handleExecuteExport = async () => {
    if (!exportUrl.trim()) return;
    setExportLoading(true);
    setExportError(null);
    try {
      const res = await exportToSpreadsheet(exportUrl.trim());
      setExportResult(res);
    } catch (e: unknown) {
      setExportError(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExportLoading(false);
    }
  };

  const resetExportModal = () => {
    setShowExportModal(false);
    setExportUrl('');
    setExportLoading(false);
    setExportError(null);
    setExportResult(null);
  };

  const handlePanelAdd = async () => {
    if (!expandedRow || !newPanelTicker.trim()) return;
    const val = newPanelTicker.trim().toUpperCase();
    try {
      if (expandedRow.type === 'theme') {
        // Theme: add member stock
        await addThemeMember(expandedRow.ticker, val);
      } else {
        // Stock: add to theme (theme_ticker = val, member_ticker = this stock)
        await addThemeMember(val, expandedRow.ticker);
      }
      setNewPanelTicker('');
      // Refresh panel
      const members = expandedRow.type === 'theme'
        ? await fetchThemeMembers(expandedRow.ticker)
        : await fetchSymbolThemes(expandedRow.ticker);
      setPanelMembers(members);
      // Refresh inline chips
      if (expandedRow.type === 'stock') {
        refreshInlineTheme(expandedRow.ticker);
      } else {
        refreshInlineTheme(val);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Add failed');
    }
  };

  const handlePanelRemove = async (themeTicker: string, memberTicker: string) => {
    try {
      await removeThemeMember(themeTicker, memberTicker);
      if (expandedRow) {
        const members = expandedRow.type === 'theme'
          ? await fetchThemeMembers(expandedRow.ticker)
          : await fetchSymbolThemes(expandedRow.ticker);
        setPanelMembers(members);
        // Refresh inline chips
        if (expandedRow.type === 'stock') {
          refreshInlineTheme(expandedRow.ticker);
        } else {
          refreshInlineTheme(memberTicker);
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Remove failed');
    }
  };

  /* ---------- Render helpers ---------- */

  const renderSortIcon = (column: string) => {
    if (sortBy !== column) return <span style={{ opacity: 0.3, fontSize: '10px' }}>⇅</span>;
    return <span style={{ fontSize: '10px', color: 'var(--accent)' }}>{sortDir === 'asc' ? '▲' : '▼'}</span>;
  };

  const renderEditableCell = (sym: SymbolMaster, field: keyof SymbolMaster, value: string) => {
    if (editingCell?.id === sym.id && editingCell?.field === field) {
      if (field === 'category') {
        return (
          <select
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={saveEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') saveEdit();
              if (e.key === 'Escape') cancelEdit();
            }}
            autoFocus
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--accent)',
              borderRadius: '4px',
              color: 'var(--text-primary)',
              padding: '2px 4px',
              fontSize: '11px',
              outline: 'none',
            }}
          >
            {CATEGORIES.filter(c => c !== '全て').map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        );
      }

      return (
        <input
          ref={editInputRef}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={saveEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') saveEdit();
            if (e.key === 'Escape') cancelEdit();
          }}
          style={{
            width: '100%',
            background: 'rgba(59, 130, 246, 0.15)',
            border: '1px solid var(--accent)',
            borderRadius: '4px',
            color: 'var(--text-primary)',
            padding: '2px 6px',
            fontSize: '12px',
            outline: 'none',
          }}
        />
      );
    }

    if (field === 'category') {
      return (
        <span
          onDoubleClick={() => startEdit(sym.id, field, value)}
          title="ダブルクリックで編集"
          style={{
            padding: '2px 8px',
            borderRadius: '10px',
            fontSize: '10px',
            fontWeight: 600,
            background: categoryColor(value),
            color: '#fff',
            cursor: 'pointer',
            display: 'inline-block',
            userSelect: 'none',
          }}
        >
          {value}
        </span>
      );
    }

    return (
      <span
        onDoubleClick={() => startEdit(sym.id, field, value)}
        style={{ cursor: 'text', display: 'block', minHeight: '18px' }}
        title="ダブルクリックで編集"
      >
        {value || <span style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: '11px' }}>—</span>}
      </span>
    );
  };

  /** Expansion panel rendered below the row */
  const renderExpansionPanel = (sym: SymbolMaster) => {
    const isTheme = expandedRow?.type === 'theme';
    const panelLabel = isTheme ? 'Constituents' : 'Theme Memberships';
    const addPlaceholder = isTheme ? 'Add member ticker…' : 'Add theme ticker…';

    return (
      <tr key={`panel-${sym.id}`}>
        <td colSpan={SORTABLE_COLUMNS.length + 2} style={{ padding: 0, border: 'none' }}>
          <div style={{
            margin: '0 12px 8px',
            padding: '14px 16px',
            background: isTheme
              ? 'linear-gradient(135deg, rgba(34, 211, 160, 0.06), rgba(59, 130, 246, 0.06))'
              : 'linear-gradient(135deg, rgba(245, 158, 11, 0.06), rgba(59, 130, 246, 0.06))',
            border: `1px solid ${isTheme ? 'rgba(34, 211, 160, 0.2)' : 'rgba(245, 158, 11, 0.2)'}`,
            borderRadius: 'var(--radius-sm)',
            fontSize: '12px',
          }}>
            {/* Panel header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{
                  fontWeight: 700,
                  fontSize: '13px',
                  color: isTheme ? 'var(--accent-green)' : 'var(--accent-yellow)',
                }}>
                  {isTheme ? '🏷' : '📂'} {panelLabel}
                </span>
                <span style={{
                  padding: '2px 8px',
                  borderRadius: '10px',
                  fontSize: '11px',
                  fontWeight: 600,
                  background: isTheme ? 'rgba(34, 211, 160, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                  color: isTheme ? 'var(--accent-green)' : 'var(--accent-yellow)',
                }}>
                  {panelMembers.length}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                <input
                  type="text"
                  value={newPanelTicker}
                  onChange={(e) => setNewPanelTicker(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handlePanelAdd(); }}
                  placeholder={addPlaceholder}
                  style={{
                    width: '160px',
                    padding: '5px 10px',
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    color: 'var(--text-primary)',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
                <button
                  onClick={handlePanelAdd}
                  disabled={!newPanelTicker.trim()}
                  style={{
                    padding: '5px 12px',
                    background: newPanelTicker.trim() ? 'var(--accent)' : 'var(--bg-glass)',
                    border: 'none',
                    borderRadius: '4px',
                    color: '#fff',
                    fontSize: '12px',
                    fontWeight: 600,
                    cursor: newPanelTicker.trim() ? 'pointer' : 'default',
                    opacity: newPanelTicker.trim() ? 1 : 0.4,
                    transition: 'all 0.15s',
                  }}
                >
                  + Add
                </button>
              </div>
            </div>

            {/* Panel body */}
            {panelLoading ? (
              <span style={{ color: 'var(--text-muted)' }}>Loading…</span>
            ) : panelMembers.length === 0 ? (
              <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                {isTheme ? 'No constituent members' : 'Not assigned to any theme'}
              </span>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                {panelMembers.map((m) => {
                  const displayTicker = isTheme ? m.member_ticker : m.theme_ticker;
                  const chipColor = isTheme ? 'rgba(34, 211, 160, 0.12)' : 'rgba(245, 158, 11, 0.12)';
                  const chipBorder = isTheme ? 'rgba(34, 211, 160, 0.3)' : 'rgba(245, 158, 11, 0.3)';
                  return (
                    <span
                      key={m.id}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '5px',
                        padding: '3px 10px',
                        background: chipColor,
                        border: `1px solid ${chipBorder}`,
                        borderRadius: '14px',
                        fontSize: '12px',
                        fontWeight: 500,
                        color: 'var(--text-primary)',
                        transition: 'all 0.15s',
                      }}
                    >
                      {displayTicker}
                      <button
                        onClick={() => handlePanelRemove(m.theme_ticker, m.member_ticker)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--accent-red)',
                          cursor: 'pointer',
                          fontSize: '14px',
                          lineHeight: 1,
                          padding: 0,
                          opacity: 0.7,
                          transition: 'opacity 0.15s',
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
                        onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.7')}
                        title="Remove"
                      >
                        ×
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        </td>
      </tr>
    );
  };

  const renderPagination = () => {
    if (!data || data.total_pages <= 1) return null;
    const pages: number[] = [];
    const tp = data.total_pages;
    const cp = data.page;

    const addPage = (p: number) => { if (p >= 1 && p <= tp && !pages.includes(p)) pages.push(p); };
    addPage(1);
    for (let i = Math.max(2, cp - 2); i <= Math.min(tp - 1, cp + 2); i++) addPage(i);
    addPage(tp);
    pages.sort((a, b) => a - b);

    const elements: JSX.Element[] = [];
    elements.push(
      <button key="prev" disabled={cp === 1} onClick={() => setCurrentPage(cp - 1)}
        style={paginationBtnStyle(false)}>‹ Prev</button>
    );
    let prev = 0;
    for (const p of pages) {
      if (p - prev > 1) elements.push(<span key={`gap-${p}`} style={{ color: 'var(--text-muted)', padding: '0 4px' }}>…</span>);
      elements.push(
        <button key={p} onClick={() => setCurrentPage(p)}
          style={paginationBtnStyle(p === cp)}>{p}</button>
      );
      prev = p;
    }
    elements.push(
      <button key="next" disabled={cp === tp} onClick={() => setCurrentPage(cp + 1)}
        style={paginationBtnStyle(false)}>Next ›</button>
    );

    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '4px' }}>{elements}</div>;
  };

  /* ---------- Render ---------- */

  return (
    <div style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            Universe Manager
          </h1>
          {stats && (
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
              {stats.total_symbols.toLocaleString()} symbols · {stats.total_themes} themes · {stats.total_theme_members.toLocaleString()} members
            </p>
          )}
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={() => {
              resetImportModal();
              setShowImportModal(true);
            }}
            style={{
              padding: '8px 16px',
              background: 'var(--bg-glass)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.2s',
            }}
          >
            📥 シートから同期
          </button>
          <button
            onClick={() => {
              resetExportModal();
              setShowExportModal(true);
            }}
            style={{
              padding: '8px 16px',
              background: 'var(--bg-glass)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.2s',
            }}
          >
            📤 シートへ書き出し
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            style={{
              padding: '8px 18px',
              background: 'linear-gradient(135deg, var(--accent), #6366f1)',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              color: '#fff',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer',
              transition: 'all 0.2s',
              boxShadow: '0 2px 12px var(--accent-glow)',
            }}
          >
            + 銘柄追加
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div style={{
          padding: '10px 16px',
          background: 'rgba(244, 63, 94, 0.15)',
          border: '1px solid var(--accent-red)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--accent-red)',
          fontSize: '12px',
          marginBottom: '16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <span>{error}</span>
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', color: 'var(--accent-red)', cursor: 'pointer', fontSize: '16px' }}>×</button>
        </div>
      )}

      {/* Category tabs */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '14px', flexWrap: 'wrap' }}>
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            onClick={() => handleCategoryChange(cat)}
            style={{
              padding: '6px 14px',
              background: activeCategory === cat
                ? 'linear-gradient(135deg, var(--accent), #6366f1)'
                : 'var(--bg-glass)',
              border: activeCategory === cat ? 'none' : '1px solid var(--border)',
              borderRadius: '20px',
              color: activeCategory === cat ? '#fff' : 'var(--text-secondary)',
              fontSize: '12px',
              fontWeight: activeCategory === cat ? 600 : 400,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }}
          >
            {cat}
            {stats && cat !== '全て' && stats.by_category[cat] != null && (
              <span style={{ marginLeft: '6px', opacity: 0.7, fontSize: '10px' }}>
                ({stats.by_category[cat]})
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Search + options row */}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1 1 250px', maxWidth: '400px' }}>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search ticker, name, industry…"
            style={{
              width: '100%',
              padding: '8px 14px 8px 34px',
              background: 'var(--bg-glass)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-primary)',
              fontSize: '12px',
              outline: 'none',
              transition: 'border-color 0.2s',
            }}
            onFocus={(e) => (e.target.style.borderColor = 'var(--accent)')}
            onBlur={(e) => (e.target.style.borderColor = 'var(--border)')}
          />
          <span style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', fontSize: '14px', color: 'var(--text-muted)' }}>
            🔍
          </span>
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => { setIncludeInactive(e.target.checked); setCurrentPage(1); }}
            style={{ accentColor: 'var(--accent)' }}
          />
          非アクティブも表示
        </label>
      </div>

      {/* Table */}
      <div style={{
        background: 'var(--bg-glass)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
      }}>
        {loading && (
          <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            Loading…
          </div>
        )}

        {!loading && data && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ ...thStyle, width: '40px' }}>#</th>
                  {SORTABLE_COLUMNS.map((col) => (
                    <th
                      key={col.key}
                      onClick={() => handleSort(col.key)}
                      style={{ ...thStyle, cursor: 'pointer', userSelect: 'none', width: col.width }}
                    >
                      {col.label} {renderSortIcon(col.key)}
                    </th>
                  ))}
                  <th
                    onClick={() => handleSort('member_count')}
                    style={{ ...thStyle, minWidth: '160px', cursor: 'pointer', userSelect: 'none' }}
                    title="クリックで構成銘柄数（タグ数）順にソート"
                  >
                    Tags / Members {renderSortIcon('member_count')}
                  </th>
                  <th style={{ ...thStyle, width: '50px' }}></th>
                </tr>
              </thead>
              <tbody>
                {data.items.flatMap((sym, idx) => {
                  const rowNum = (data.page - 1) * PAGE_SIZE + idx + 1;
                  const isTheme = sym.category === 'テーマ';
                  const isStock = sym.category === '個別';
                  const canExpand = isTheme || isStock;
                  const isExpanded = expandedRow?.ticker === sym.ticker &&
                    ((isTheme && expandedRow?.type === 'theme') || (isStock && expandedRow?.type === 'stock'));
                  const isInactive = sym.active === 0;
                  const expandType = isTheme ? 'theme' as const : 'stock' as const;

                  const rows: JSX.Element[] = [];

                  rows.push(
                    <tr
                      key={sym.id}
                      style={{
                        opacity: isInactive ? 0.5 : 1,
                        borderBottom: isExpanded ? 'none' : undefined,
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(59, 130, 246, 0.04)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = '')}
                    >
                      <td style={tdStyle}>
                        <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{rowNum}</span>
                      </td>
                      {/* Ticker */}
                      <td style={tdStyle}>
                        {renderEditableCell(sym, 'ticker', sym.ticker)}
                      </td>
                      {/* Exchange (with virtual badge) */}
                      <td style={tdStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                          {renderEditableCell(sym, 'exchange', sym.exchange || '')}
                          {sym.theme_type === 'virtual' && (
                            <span style={{
                              padding: '1px 5px',
                              borderRadius: '8px',
                              fontSize: '9px',
                              fontWeight: 600,
                              background: 'rgba(168, 85, 247, 0.15)',
                              border: '1px solid rgba(168, 85, 247, 0.3)',
                              color: '#a855f7',
                              whiteSpace: 'nowrap',
                              flexShrink: 0,
                            }}>
                              virtual
                            </span>
                          )}
                        </div>
                      </td>
                      {/* Name */}
                      <td style={{ ...tdStyle, maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {renderEditableCell(sym, 'name', sym.name || '')}
                      </td>
                      {/* Industry */}
                      <td style={tdStyle}>{renderEditableCell(sym, 'industry', sym.industry || '')}</td>
                      {/* Sector ETF */}
                      <td style={tdStyle}>{renderEditableCell(sym, 'sector_etf', sym.sector_etf || '')}</td>
                      {/* Category */}
                      <td style={tdStyle}>
                        {renderEditableCell(sym, 'category', sym.category)}
                      </td>
                      {/* Active */}
                      <td style={{ ...tdStyle, textAlign: 'center' }}>
                        <span style={{
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          display: 'inline-block',
                          background: sym.active === 1 ? 'var(--accent-green)' : 'var(--accent-red)',
                          boxShadow: `0 0 6px ${sym.active === 1 ? 'var(--accent-green)' : 'var(--accent-red)'}`,
                        }} />
                      </td>
                      {/* Tags column: inline chips + edit button */}
                      <td style={{ ...tdStyle, minWidth: '160px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                          {/* Inline theme chips for stocks */}
                          {isStock && inlineThemes[sym.ticker] && inlineThemes[sym.ticker].map(tag => (
                            <span key={tag} style={{
                              padding: '1px 7px',
                              borderRadius: '10px',
                              fontSize: '10px',
                              fontWeight: 500,
                              background: 'rgba(245, 158, 11, 0.12)',
                              border: '1px solid rgba(245, 158, 11, 0.25)',
                              color: 'var(--accent-yellow)',
                              whiteSpace: 'nowrap',
                            }}>
                              {tag}
                            </span>
                          ))}
                          {isStock && (!inlineThemes[sym.ticker] || inlineThemes[sym.ticker].length === 0) && (
                            <span style={{ color: 'var(--text-muted)', fontSize: '10px', fontStyle: 'italic' }}>—</span>
                          )}
                          {/* Edit button */}
                          {canExpand && (
                            <button
                              onClick={() => toggleExpansion(sym.ticker, expandType)}
                              title={isTheme ? 'Manage constituents' : 'Edit theme tags'}
                              style={{
                                padding: '2px 8px',
                                background: isExpanded
                                  ? (isTheme ? 'var(--accent-green)' : 'var(--accent-yellow)')
                                  : 'transparent',
                                border: `1px solid ${isExpanded
                                  ? 'transparent'
                                  : (isTheme ? 'rgba(34, 211, 160, 0.4)' : 'rgba(245, 158, 11, 0.3)')}`,
                                borderRadius: '10px',
                                color: isExpanded
                                  ? '#fff'
                                  : (isTheme ? 'var(--accent-green)' : 'var(--accent-yellow)'),
                                cursor: 'pointer',
                                fontSize: '10px',
                                fontWeight: 600,
                                transition: 'all 0.2s',
                                whiteSpace: 'nowrap',
                                flexShrink: 0,
                              }}
                            >
                              {isTheme
                                ? `🏷 Members (${sym.member_count ?? 0})`
                                : (sym.member_count && sym.member_count > 0)
                                  ? `🏷 Members (${sym.member_count})`
                                  : '✏'}
                            </button>
                          )}
                        </div>
                      </td>
                      {/* Delete */}
                      <td style={tdStyle}>
                        <button
                          onClick={() => handleDelete(sym)}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: 'var(--text-muted)',
                            cursor: 'pointer',
                            fontSize: '14px',
                            transition: 'color 0.2s',
                          }}
                          title="非アクティブ化"
                          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--accent-red)')}
                          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-muted)')}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );

                  // Expansion panel row
                  if (isExpanded) {
                    rows.push(renderExpansionPanel(sym));
                  }

                  return rows;
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination footer */}
        {data && (
          <div style={{
            padding: '12px 16px',
            borderTop: '1px solid var(--border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: '12px',
            color: 'var(--text-muted)',
          }}>
            <span>
              Showing {((data.page - 1) * PAGE_SIZE) + 1}–{Math.min(data.page * PAGE_SIZE, data.total)} of {data.total.toLocaleString()}
            </span>
            {renderPagination()}
          </div>
        )}
      </div>

      {/* Add Symbol Modal */}
      {showAddModal && (
        <div style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.6)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999,
        }}
          onClick={() => setShowAddModal(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              padding: '28px',
              width: '420px',
              maxWidth: '90vw',
              boxShadow: '0 20px 60px rgba(0, 0, 0, 0.5)',
            }}
          >
            <h2 style={{ fontSize: '18px', fontWeight: 700, marginBottom: '20px', color: 'var(--text-primary)' }}>
              銘柄を追加
            </h2>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={labelStyle}>Ticker *</label>
                <input type="text" value={newSymbol.ticker}
                  onChange={(e) => setNewSymbol({ ...newSymbol, ticker: e.target.value })}
                  placeholder="NVDA" style={modalInputStyle} autoFocus />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={labelStyle}>Exchange</label>
                  <input type="text" value={newSymbol.exchange}
                    onChange={(e) => setNewSymbol({ ...newSymbol, exchange: e.target.value })}
                    placeholder="NASDAQ" style={modalInputStyle} />
                </div>
                <div>
                  <label style={labelStyle}>Category</label>
                  <select value={newSymbol.category}
                    onChange={(e) => setNewSymbol({ ...newSymbol, category: e.target.value })}
                    style={{ ...modalInputStyle, cursor: 'pointer' }}>
                    {CATEGORIES.filter(c => c !== '全て').map(c => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label style={labelStyle}>Name</label>
                <input type="text" value={newSymbol.name}
                  onChange={(e) => setNewSymbol({ ...newSymbol, name: e.target.value })}
                  placeholder="NVIDIA Corporation" style={modalInputStyle} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={labelStyle}>Industry</label>
                  <input type="text" value={newSymbol.industry}
                    onChange={(e) => setNewSymbol({ ...newSymbol, industry: e.target.value })}
                    placeholder="半導体" style={modalInputStyle} />
                </div>
                <div>
                  <label style={labelStyle}>Sector ETF</label>
                  <input type="text" value={newSymbol.sector_etf}
                    onChange={(e) => setNewSymbol({ ...newSymbol, sector_etf: e.target.value })}
                    placeholder="XLK" style={modalInputStyle} />
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '24px' }}>
              <button
                onClick={() => setShowAddModal(false)}
                style={{
                  padding: '8px 18px',
                  background: 'var(--bg-glass)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--text-secondary)',
                  fontSize: '13px',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleAddSymbol}
                disabled={!newSymbol.ticker.trim()}
                style={{
                  padding: '8px 18px',
                  background: newSymbol.ticker.trim() ? 'linear-gradient(135deg, var(--accent), #6366f1)' : 'var(--bg-glass)',
                  border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  color: '#fff',
                  fontSize: '13px',
                  fontWeight: 600,
                  cursor: newSymbol.ticker.trim() ? 'pointer' : 'not-allowed',
                  opacity: newSymbol.ticker.trim() ? 1 : 0.5,
                }}
              >
                追加
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Import Modal */}
      {showImportModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={resetImportModal}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
              width: '640px',
              maxWidth: '92vw',
              maxHeight: '85vh',
              overflowY: 'auto',
              boxShadow: '0 20px 60px rgba(0, 0, 0, 0.6)',
            }}
          >
            {/* Step Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h2 style={{ fontSize: '18px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
                📥 Google スプレッドシート同期
              </h2>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                Step {importStep} / 3
              </span>
            </div>

            {importModalError && (
              <div style={{
                padding: '8px 12px',
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid var(--accent-red)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--accent-red)',
                fontSize: '12px',
                marginBottom: '16px',
              }}>
                {importModalError}
              </div>
            )}

            {/* STEP 1: 入力 & モード選択 */}
            {importStep === 1 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <label style={labelStyle}>Google スプレッドシート URL *</label>
                  <input
                    type="text"
                    value={importUrl}
                    onChange={(e) => setImportUrl(e.target.value)}
                    placeholder="https://docs.google.com/spreadsheets/d/..."
                    style={modalInputStyle}
                    autoFocus
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', display: 'block' }}>
                    ※ 共有設定が「リンクを知っている全員（閲覧）」になっている必要があります。
                  </span>
                </div>

                <div>
                  <label style={labelStyle}>同期モード</label>
                  <div style={{ display: 'flex', gap: '16px', marginTop: '6px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', cursor: 'pointer', color: 'var(--text-primary)' }}>
                      <input
                        type="radio"
                        name="importMode"
                        value="upsert"
                        checked={importMode === 'upsert'}
                        onChange={() => setImportMode('upsert')}
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      <b>差分更新 (UPSERT)</b>
                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>— 追加・編集のみ（推論手動データ維持）</span>
                    </label>
                  </div>
                  <div style={{ display: 'flex', gap: '16px', marginTop: '6px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', cursor: 'pointer', color: 'var(--text-primary)' }}>
                      <input
                        type="radio"
                        name="importMode"
                        value="replace"
                        checked={importMode === 'replace'}
                        onChange={() => setImportMode('replace')}
                        style={{ accentColor: 'var(--accent-red)' }}
                      />
                      <b>完全置換 (REPLACE)</b>
                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>— 一旦全削除してシートの通りに初期化</span>
                    </label>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '16px' }}>
                  <button onClick={resetImportModal} style={modalCancelBtnStyle}>
                    キャンセル
                  </button>
                  <button
                    onClick={handlePreviewImport}
                    disabled={!importUrl.trim() || importLoading}
                    style={{
                      ...modalPrimaryBtnStyle,
                      opacity: (!importUrl.trim() || importLoading) ? 0.5 : 1,
                      cursor: (!importUrl.trim() || importLoading) ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {importLoading ? '解析中…' : '差分を解析 ➔'}
                  </button>
                </div>
              </div>
            )}

            {/* STEP 2: 差分プレビュー */}
            {importStep === 2 && importDiff && (
              <div>
                {/* Summary badges */}
                <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap' }}>
                  <span style={summaryBadgeStyle('#22d3a0')}>+ 新規追加: {importDiff.summary.added} 件</span>
                  <span style={summaryBadgeStyle('#f59e0b')}>~ 内容更新: {importDiff.summary.updated} 件</span>
                  <span style={summaryBadgeStyle('#6366f1')}>+ 構成銘柄追加: {importDiff.summary.members_added} 件</span>
                  {importMode === 'replace' && (
                    <span style={summaryBadgeStyle('#ef4444')}>- 削除対象: {importDiff.summary.deleted} 件</span>
                  )}
                </div>

                {/* Diff Preview Content */}
                <div style={{ maxHeight: '350px', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '10px', background: 'rgba(15, 23, 42, 0.4)', fontSize: '12px' }}>
                  {importDiff.summary.added === 0 && importDiff.summary.updated === 0 && importDiff.summary.members_added === 0 && (
                    <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '20px' }}>
                      変更点はありません。DBの最新データと一致しています。
                    </div>
                  )}

                  {/* Added Items */}
                  {importDiff.added.length > 0 && (
                    <div style={{ marginBottom: '14px' }}>
                      <h4 style={{ color: '#22d3a0', margin: '0 0 6px 0', fontSize: '12px' }}>新規追加 ({importDiff.added.length} 件)</h4>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                        {importDiff.added.map(item => (
                          <span key={item.ticker} style={{ padding: '2px 8px', background: 'rgba(34, 211, 160, 0.12)', border: '1px solid rgba(34, 211, 160, 0.3)', borderRadius: '10px', color: '#22d3a0', fontSize: '11px' }}>
                            {item.ticker} ({item.category})
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Updated Items */}
                  {importDiff.updated.length > 0 && (
                    <div style={{ marginBottom: '14px' }}>
                      <h4 style={{ color: '#f59e0b', margin: '0 0 6px 0', fontSize: '12px' }}>内容更新 ({importDiff.updated.length} 件)</h4>
                      {importDiff.updated.map(item => (
                        <div key={item.id} style={{ marginBottom: '4px', background: 'rgba(245, 158, 11, 0.08)', padding: '4px 8px', borderRadius: '4px' }}>
                          <b>{item.ticker}</b>: {Object.entries(item.changes).map(([field, change]) => `${field}: "${change.old || ''}" ➔ "${change.new || ''}"`).join(', ')}
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Theme Members Added */}
                  {importDiff.members_added.length > 0 && (
                    <div>
                      <h4 style={{ color: '#6366f1', margin: '0 0 6px 0', fontSize: '12px' }}>テーマ構成銘柄追加 ({importDiff.members_added.length} 件)</h4>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                        {importDiff.members_added.map((m, i) => (
                          <span key={i} style={{ padding: '2px 8px', background: 'rgba(99, 102, 241, 0.12)', border: '1px solid rgba(99, 102, 241, 0.3)', borderRadius: '10px', color: '#6366f1', fontSize: '11px' }}>
                            {m.theme_ticker} ➔ {m.member_ticker}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div style={{ display: 'flex', gap: '10px', justifyContent: 'space-between', marginTop: '16px' }}>
                  <button onClick={() => setImportStep(1)} style={modalCancelBtnStyle}>
                    ‹ 戻る
                  </button>
                  <button
                    onClick={handleExecuteImport}
                    disabled={importLoading}
                    style={{
                      ...modalPrimaryBtnStyle,
                      background: 'linear-gradient(135deg, #22d3a0, #0ea5e9)',
                      opacity: importLoading ? 0.5 : 1,
                      cursor: importLoading ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {importLoading ? '実行中…' : '確定して本反映 ➔'}
                  </button>
                </div>
              </div>
            )}

            {/* STEP 3: 完了 */}
            {importStep === 3 && importResult && (
              <div style={{ textAlign: 'center', padding: '16px 0' }}>
                <div style={{ fontSize: '42px', marginBottom: '10px' }}>✅</div>
                <h3 style={{ fontSize: '16px', color: 'var(--text-primary)', marginBottom: '8px' }}>
                  インポートが完了しました
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '20px' }}>
                  追加: {importResult.added} 件 / 更新: {importResult.updated} 件 / 構成追加: {importResult.members_added} 件 ({importResult.mode} モード)
                </p>
                <button onClick={resetImportModal} style={{ ...modalPrimaryBtnStyle, width: '120px', margin: '0 auto' }}>
                  閉じる
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Export Modal */}
      {showExportModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.65)',
            backdropFilter: 'blur(4px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
          }}
          onClick={resetExportModal}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              padding: '24px',
              width: '540px',
              maxWidth: '92vw',
              boxShadow: '0 20px 60px rgba(0, 0, 0, 0.6)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h2 style={{ fontSize: '18px', fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
                📤 Google スプレッドシートへ書き出し
              </h2>
            </div>

            {exportError && (
              <div style={{
                padding: '8px 12px',
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid var(--accent-red)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--accent-red)',
                fontSize: '12px',
                marginBottom: '16px',
              }}>
                {exportError}
              </div>
            )}

            {!exportResult ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div>
                  <label style={labelStyle}>書き出し先 Google スプレッドシート URL *</label>
                  <input
                    type="text"
                    value={exportUrl}
                    onChange={(e) => setExportUrl(e.target.value)}
                    placeholder="https://docs.google.com/spreadsheets/d/..."
                    style={modalInputStyle}
                    autoFocus
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', display: 'block' }}>
                    ※ サービスアカウントメールへの「編集者」アクセス権限が必要です。
                  </span>
                </div>

                <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '16px' }}>
                  <button onClick={resetExportModal} style={modalCancelBtnStyle}>
                    キャンセル
                  </button>
                  <button
                    onClick={handleExecuteExport}
                    disabled={!exportUrl.trim() || exportLoading}
                    style={{
                      ...modalPrimaryBtnStyle,
                      opacity: (!exportUrl.trim() || exportLoading) ? 0.5 : 1,
                      cursor: (!exportUrl.trim() || exportLoading) ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {exportLoading ? '書き出し中…' : 'シートへ上書き出力 ➔'}
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '16px 0' }}>
                <div style={{ fontSize: '42px', marginBottom: '10px' }}>✅</div>
                <h3 style={{ fontSize: '16px', color: 'var(--text-primary)', marginBottom: '8px' }}>
                  書き出しが完了しました！
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '16px' }}>
                  合計 {exportResult.total_exported} 件のデータをスプレッドシートへ反映しました。
                </p>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', background: 'rgba(15, 23, 42, 0.4)', padding: '10px', borderRadius: '4px', marginBottom: '20px', textAlign: 'left' }}>
                  {Object.entries(exportResult.details).map(([sheet, count]) => (
                    <div key={sheet}>• {sheet}: {count} 件</div>
                  ))}
                </div>
                <button onClick={resetExportModal} style={{ ...modalPrimaryBtnStyle, width: '120px', margin: '0 auto' }}>
                  閉じる
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- Style constants ---------- */

const thStyle: React.CSSProperties = {
  padding: '10px 12px',
  textAlign: 'left',
  fontWeight: 600,
  fontSize: '11px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  whiteSpace: 'nowrap',
  background: 'rgba(15, 23, 42, 0.5)',
};

const tdStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--border)',
  verticalAlign: 'top',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '11px',
  fontWeight: 600,
  color: 'var(--text-muted)',
  marginBottom: '4px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};

const modalInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-glass)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-primary)',
  fontSize: '13px',
  outline: 'none',
};

function paginationBtnStyle(isActive: boolean): React.CSSProperties {
  return {
    padding: '4px 10px',
    background: isActive ? 'var(--accent)' : 'transparent',
    border: isActive ? 'none' : '1px solid var(--border)',
    borderRadius: '4px',
    color: isActive ? '#fff' : 'var(--text-secondary)',
    fontSize: '12px',
    fontWeight: isActive ? 600 : 400,
    cursor: 'pointer',
    transition: 'all 0.15s',
  };
}

function categoryColor(category: string): string {
  const map: Record<string, string> = {
    '市場': '#6366f1',
    '指標': '#8b5cf6',
    'セクタ': '#0ea5e9',
    'テーマ': '#22d3a0',
    '個別': '#f59e0b',
    'レバレッジ': '#ef4444',
  };
  return map[category] || '#64748b';
}

const modalCancelBtnStyle: React.CSSProperties = {
  padding: '8px 18px',
  background: 'var(--bg-glass)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-secondary)',
  fontSize: '13px',
  cursor: 'pointer',
};

const modalPrimaryBtnStyle: React.CSSProperties = {
  padding: '8px 18px',
  background: 'linear-gradient(135deg, var(--accent), #6366f1)',
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  color: '#fff',
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  transition: 'all 0.2s',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
};

function summaryBadgeStyle(color: string): React.CSSProperties {
  return {
    padding: '4px 10px',
    borderRadius: '12px',
    fontSize: '11px',
    fontWeight: 600,
    background: `${color}18`,
    border: `1px solid ${color}40`,
    color,
  };
}
