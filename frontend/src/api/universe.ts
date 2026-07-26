/**
 * Universe Manager – API Client
 *
 * universe_router.py が公開する CRUD エンドポイントへの薄いラッパー。
 */

const API = '/api/universe';

/* ---------- Types ---------- */

export interface SymbolMaster {
  id: number;
  ticker: string;
  exchange: string | null;
  name: string | null;
  category: string;
  industry: string | null;
  theme_type: string | null;
  sector_etf: string | null;
  active: number;
  source: string | null;
  created_at: string | null;
  updated_at: string | null;
  member_count?: number;
}

export interface PaginatedSymbols {
  items: SymbolMaster[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ThemeMember {
  id: number;
  theme_ticker: string;
  member_ticker: string;
  weight: number;
  source: string | null;
}

export interface UniverseStats {
  total_symbols: number;
  by_category: Record<string, number>;
  total_themes: number;
  total_theme_members: number;
  last_updated: string | null;
}

export interface SymbolCreatePayload {
  ticker: string;
  exchange?: string | null;
  name?: string | null;
  category?: string;
  industry?: string | null;
  theme_type?: string | null;
  sector_etf?: string | null;
  active?: number;
}

export interface SymbolUpdatePayload {
  ticker?: string;
  exchange?: string;
  name?: string;
  category?: string;
  industry?: string;
  theme_type?: string | null;
  sector_etf?: string | null;
  active?: number;
}

/* ---------- API Functions ---------- */

export async function fetchSymbols(params: {
  category?: string;
  search?: string;
  include_inactive?: boolean;
  sort_by?: string;
  sort_dir?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedSymbols> {
  const qs = new URLSearchParams();
  if (params.category) qs.set('category', params.category);
  if (params.search) qs.set('search', params.search);
  if (params.include_inactive) qs.set('include_inactive', 'true');
  if (params.sort_by) qs.set('sort_by', params.sort_by);
  if (params.sort_dir) qs.set('sort_dir', params.sort_dir);
  if (params.page) qs.set('page', String(params.page));
  if (params.page_size) qs.set('page_size', String(params.page_size));

  const res = await fetch(`${API}/symbols?${qs.toString()}`);
  if (!res.ok) throw new Error(`fetchSymbols failed: ${res.status}`);
  return res.json();
}

export async function createSymbol(payload: SymbolCreatePayload): Promise<SymbolMaster> {
  const res = await fetch(`${API}/symbols`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `createSymbol failed: ${res.status}`);
  }
  return res.json();
}

export async function updateSymbol(id: number, payload: SymbolUpdatePayload): Promise<SymbolMaster> {
  const res = await fetch(`${API}/symbols/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `updateSymbol failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteSymbol(id: number): Promise<void> {
  const res = await fetch(`${API}/symbols/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`deleteSymbol failed: ${res.status}`);
}

export async function fetchThemeMembers(ticker: string): Promise<ThemeMember[]> {
  const res = await fetch(`${API}/themes/${encodeURIComponent(ticker)}/members`);
  if (!res.ok) throw new Error(`fetchThemeMembers failed: ${res.status}`);
  return res.json();
}

export async function addThemeMember(
  themeTicker: string,
  memberTicker: string,
  weight: number = 1.0,
): Promise<ThemeMember> {
  const res = await fetch(`${API}/themes/${encodeURIComponent(themeTicker)}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_ticker: memberTicker, weight }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `addThemeMember failed: ${res.status}`);
  }
  return res.json();
}

export async function removeThemeMember(themeTicker: string, memberTicker: string): Promise<void> {
  const res = await fetch(
    `${API}/themes/${encodeURIComponent(themeTicker)}/members/${encodeURIComponent(memberTicker)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) throw new Error(`removeThemeMember failed: ${res.status}`);
}

export async function fetchStats(): Promise<UniverseStats> {
  const res = await fetch(`${API}/stats`);
  if (!res.ok) throw new Error(`fetchStats failed: ${res.status}`);
  return res.json();
}

export async function fetchSymbolThemes(ticker: string): Promise<ThemeMember[]> {
  const res = await fetch(`${API}/symbols/${encodeURIComponent(ticker)}/themes`);
  if (!res.ok) throw new Error(`fetchSymbolThemes failed: ${res.status}`);
  return res.json();
}

/** Batch fetch: { ticker: [theme_ticker, ...] } */
export async function fetchThemesBatch(tickers: string[]): Promise<Record<string, string[]>> {
  if (tickers.length === 0) return {};
  const res = await fetch(`${API}/themes-batch?tickers=${tickers.map(encodeURIComponent).join(',')}`);
  if (!res.ok) throw new Error(`fetchThemesBatch failed: ${res.status}`);
  return res.json();
}

/* ---------- Spreadsheet Import Types & APIs ---------- */

export interface ImportDiffSummary {
  added: number;
  updated: number;
  deleted: number;
  members_added: number;
}

export interface ImportDiffItemAdded {
  ticker: string;
  exchange: string;
  name: string;
  category: string;
  industry: string;
  sector_etf: string;
}

export interface ImportDiffItemUpdated {
  id: number;
  ticker: string;
  changes: Record<string, { old: string; new: string }>;
}

export interface ImportDiffItemDeleted {
  id: number;
  ticker: string;
  name: string;
  category: string;
}

export interface ImportDiff {
  summary: ImportDiffSummary;
  added: ImportDiffItemAdded[];
  updated: ImportDiffItemUpdated[];
  deleted: ImportDiffItemDeleted[];
  members_added: { theme_ticker: string; member_ticker: string }[];
}

export interface ImportResult {
  status: string;
  added: number;
  updated: number;
  members_added: number;
  mode: string;
}

export async function previewImport(spreadsheetUrl: string, mode: 'upsert' | 'replace' = 'upsert'): Promise<ImportDiff> {
  const res = await fetch(`${API}/import/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spreadsheet_url: spreadsheetUrl, mode }),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({}));
    throw new Error(errorBody.detail || `Preview import failed: ${res.status}`);
  }
  return res.json();
}

export async function executeImport(spreadsheetUrl: string, mode: 'upsert' | 'replace' = 'upsert'): Promise<ImportResult> {
  const res = await fetch(`${API}/import/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spreadsheet_url: spreadsheetUrl, mode }),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({}));
    throw new Error(errorBody.detail || `Execute import failed: ${res.status}`);
  }
  return res.json();
}

export interface ExportResult {
  status: string;
  total_exported: number;
  details: Record<string, number>;
}

export async function exportToSpreadsheet(spreadsheetUrl: string): Promise<ExportResult> {
  const res = await fetch(`${API}/export/spreadsheet`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spreadsheet_url: spreadsheetUrl }),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({}));
    throw new Error(errorBody.detail || `Export failed: ${res.status}`);
  }
  return res.json();
}
