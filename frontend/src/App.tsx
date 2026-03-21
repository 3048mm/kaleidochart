import { useEffect, useState, useMemo } from 'react'
import { Routes, Route, Link, useNavigate, useLocation } from 'react-router-dom'
import { Symbol } from './types'
import { DashboardPage } from './pages/DashboardPage'
import { ChartPage } from './pages/ChartPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { ScreenerResultPage } from './pages/ScreenerResultPage'
import { ThemePage } from './pages/ThemePage'

const API = '/api'

export default function App() {
    const [symbols, setSymbols] = useState<Symbol[]>([])
    const [search, setSearch] = useState('')
    const navigate = useNavigate()
    const location = useLocation()

    // Load symbols on mount
    useEffect(() => {
        fetch(`${API}/symbols`).then(r => r.json()).then((data: Symbol[]) => {
            setSymbols(data)
        })
    }, [])

    // Filtered symbol list
    const filteredSymbols = useMemo(() => {
        const q = search.toLowerCase()
        if (!q) return symbols;

        // Find all themes that match the query (either by name or ticker)
        const matchingThemes = symbols.filter(s =>
            s.category === 'テーマ' &&
            (s.ticker.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
        );
        const themeTickers = matchingThemes.map(t => t.ticker.toLowerCase());

        return symbols.filter(s => {
            const tickerMatch = s.ticker.toLowerCase().includes(q);
            const nameMatch = s.name.toLowerCase().includes(q);
            const tagsMatch = s.tags && s.tags.toLowerCase().includes(q);

            // If the symbol belongs to any of the matching themes
            const belongsToMatchingTheme = s.tags && themeTickers.some(t => s.tags!.toLowerCase().includes(t));

            return tickerMatch || nameMatch || tagsMatch || belongsToMatchingTheme;
        });
    }, [symbols, search])

    // Group filtered symbols by category
    const grouped = useMemo(() => {
        return filteredSymbols.reduce<Record<string, Symbol[]>>((acc, s) => {
            const cat = s.category || '未分類'
                ; (acc[cat] ||= []).push(s)
            return acc
        }, {})
    }, [filteredSymbols])

    // Navigation handler for sidebar items
    const handleSymbolClick = (s: Symbol) => {
        const symIdentifier = s.exchange ? `${s.exchange}:${s.ticker}` : s.ticker;
        navigate(`/chart/${symIdentifier}`);
    }

    return (
        <div className="app-shell">
            {/* Header */}
            <header className="app-header">
                <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
                    <div className="header-logo">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
                            <polyline points="16 7 22 7 22 13" />
                        </svg>
                        <span>Stock Analyzer</span>
                    </div>
                </Link>
                <div className="header-spacer" />
                <nav style={{ display: 'flex', gap: '20px', marginRight: '20px' }}>
                    <Link to="/" style={{ color: location.pathname === '/' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Dashboard</Link>
                    <Link to="/screener" style={{ color: location.pathname === '/screener' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Screener</Link>
                </nav>
                <div className="header-status">
                    <div className="status-dot" />
                    <span>Live</span>
                </div>
            </header>

            {/* Sidebar */}
            <aside className="app-sidebar">
                <div className="sidebar-search">
                    <input
                        className="search-input"
                        type="text"
                        placeholder="銘柄を検索..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        id="symbol-search"
                    />
                </div>
                <div className="symbol-list">
                    {Object.entries(grouped).map(([cat, syms]) => (
                        <div key={cat}>
                            <div className="symbol-group-label">{cat}</div>
                            {syms.map(s => (
                                <div
                                    key={s.id}
                                    id={`symbol-item-${s.id}`}
                                    className={`symbol-item ${location.pathname.includes(s.ticker) ? 'active' : ''}`}
                                    onClick={() => handleSymbolClick(s)}
                                >
                                    <span className="symbol-ticker">{s.ticker}</span>
                                    <span className={`symbol-badge ${s.asset_class === 'Equity' ? 'badge-equity' : 'badge-etf'}`}>
                                        {s.asset_class}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            </aside>

            {/* Main Panel Routing */}
            <main className="app-main" style={{ overflowY: 'auto' }}>
                <Routes>
                    <Route path="/" element={<DashboardPage />} />
                    <Route path="/chart/:ticker" element={<ChartPage symbols={symbols} />} />
                    <Route path="/theme/:symbolId" element={<ThemePage />} />
                    <Route path="/screener" element={<ScreenerPage />} />
                    <Route path="/screener/result/:presetId" element={<ScreenerResultPage />} />
                </Routes>
            </main>
        </div>
    )
}
