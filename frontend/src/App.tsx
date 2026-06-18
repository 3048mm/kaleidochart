import { useEffect, useState, useMemo } from 'react'
import { Routes, Route, Link, useNavigate, useLocation } from 'react-router-dom'
import { Symbol, SystemInfo } from './types'
import { DashboardPage } from './pages/DashboardPage'
import { ChartPage } from './pages/ChartPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { ScreenerResultPage } from './pages/ScreenerResultPage'
import { WatchlistPage } from './pages/WatchlistPage'
import { GroupPage } from './pages/GroupPage'
import { TotalPortfolioPage } from './pages/TotalPortfolioPage'
import { PortfolioDetailPage } from './pages/PortfolioDetailPage'
import { BacktestPage } from './pages/BacktestPage'


const API = '/api'

export default function App() {
    const [symbols, setSymbols] = useState<Symbol[]>([])
    const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
    const [search, setSearch] = useState('')
    const [sidebarOpen, setSidebarOpen] = useState(
        () => !window.matchMedia('(max-width: 768px)').matches
    )
    const navigate = useNavigate()
    const location = useLocation()
    
    // Accordion State for Sidebar Categories (Sectors & Themes collapsed by default)
    const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({
        '市場': true,
        '指標': true,
        'セクタ': true,
        'テーマ': true,
        'レバレッジ': true,
        '個別': false,
    })
    
    const toggleCategory = (cat: string) => {
        setCollapsedCategories(prev => ({
            ...prev,
            [cat]: !prev[cat]
        }))
    }

    const isCategoryCollapsed = (cat: string) => {
        if (search !== '') return false; // Force expand during search
        return collapsedCategories[cat] ?? false;
    }

    // Load symbols on mount
    useEffect(() => {
        fetch(`${API}/symbols`).then(r => r.json()).then((data: Symbol[]) => {
            setSymbols(data)
        })
        fetch(`${API}/system/info`).then(r => r.json()).then((data: SystemInfo) => {
            setSystemInfo(data)
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
        const groups = filteredSymbols.reduce<Record<string, Symbol[]>>((acc, s) => {
            const cat = s.category || '未分類'
            ;(acc[cat] ||= []).push(s)
            return acc
        }, {})

        // Sort 'テーマ' category by ThemeGroup > Theme (locale-aware for Japanese/English)
        if (groups['テーマ']) {
            groups['テーマ'].sort((a, b) => {
                const aName = a.name || '';
                const bName = b.name || '';
                
                const aHasDelim = aName.includes('::');
                const bHasDelim = bName.includes('::');

                const aGroup = aHasDelim ? aName.split('::')[0] : aName;
                const bGroup = bHasDelim ? bName.split('::')[0] : bName;

                const aTheme = aHasDelim ? aName.split('::')[1] : aName;
                const bTheme = bHasDelim ? bName.split('::')[1] : bName;

                // Compare ThemeGroup first
                const groupComp = aGroup.localeCompare(bGroup, 'ja');
                if (groupComp !== 0) return groupComp;

                // Compare Theme next
                return aTheme.localeCompare(bTheme, 'ja');
            });
        }

        return groups;
    }, [filteredSymbols])

    // Navigation handler for sidebar items
    const handleSymbolClick = (s: Symbol) => {
        if (s.category === 'テーマ' || s.category === 'セクタ') {
            navigate(`/group/${s.ticker}`);
        } else {
            const symIdentifier = s.exchange ? `${s.exchange}:${s.ticker}` : s.ticker;
            navigate(`/chart/${symIdentifier}`);
        }
        setSidebarOpen(false);
    }

    return (
        <div className={`app-shell ${!sidebarOpen ? 'sidebar-collapsed' : ''}`}>
            {/* Header */}
            <header className="app-header">
                <button
                    className="sidebar-toggle"
                    onClick={() => setSidebarOpen(prev => !prev)}
                    aria-label="Toggle sidebar"
                >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        {sidebarOpen
                            ? <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>
                            : <><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></>
                        }
                    </svg>
                </button>
                <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
                    <div className="header-logo">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
                            <polyline points="16 7 22 7 22 13" />
                        </svg>
                        <span>Stock Analyzer</span>
                    </div>
                </Link>

                {systemInfo && !systemInfo.is_production && (
                    <div style={{
                        marginLeft: '12px',
                        padding: '2px 8px',
                        backgroundColor: '#ff9800',
                        color: '#000',
                        borderRadius: '4px',
                        fontSize: '10px',
                        fontWeight: 'bold',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        boxShadow: '0 0 10px rgba(255, 152, 0, 0.3)'
                    }}>
                        <span style={{ fontSize: '12px' }}>⚠️</span>
                        <span>DB: {systemInfo.db_name}</span>
                    </div>
                )}

                <div className="header-spacer" />
                <nav style={{ display: 'flex', gap: '20px', marginRight: '20px' }}>
                    <Link to="/" style={{ color: location.pathname === '/' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Dashboard</Link>
                    <Link to="/screener" style={{ color: location.pathname === '/screener' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Screener</Link>
                    <Link to="/watchlist" style={{ color: location.pathname === '/watchlist' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Watchlist</Link>
                    <Link to="/portfolio" style={{ color: location.pathname.startsWith('/portfolio') ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Portfolio</Link>
                    <Link to="/backtest" style={{ color: location.pathname.startsWith('/backtest') || location.pathname.startsWith('/scenariotest') || location.pathname.startsWith('/etf-backtest') ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Backtest</Link>
                </nav>
                <div className="header-status">
                    <div className="status-dot" />
                    <span>Live</span>
                </div>
            </header>

            {/* Sidebar Backdrop (mobile) */}
            <div
                className={`sidebar-backdrop ${sidebarOpen ? 'open' : ''}`}
                onClick={() => setSidebarOpen(false)}
            />

            <aside className={`app-sidebar ${sidebarOpen ? 'open' : ''}`}>
                <div className="sidebar-search" style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                    <input
                        className="search-input"
                        type="text"
                        placeholder="セクタ・テーマ・銘柄を検索..."
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        id="symbol-search"
                        style={{ paddingRight: search ? '30px' : '10px' }}
                    />
                    {search && (
                        <button
                            onClick={() => setSearch('')}
                            style={{
                                position: 'absolute',
                                right: '20px',
                                background: 'transparent',
                                border: 'none',
                                color: '#8b9cc8',
                                cursor: 'pointer',
                                fontSize: '14px',
                                display: 'flex',
                                alignItems: 'center',
                                padding: '4px'
                            }}
                            title="クリア"
                        >
                            ✕
                        </button>
                    )}
                </div>
                
                {search && (
                    <div style={{ fontSize: '10px', color: '#666', padding: '2px 14px 6px', borderBottom: '1px solid var(--border)' }}>
                        {filteredSymbols.length} items found
                    </div>
                )}

                <div className="symbol-list">
                    {Object.entries(grouped).map(([cat, syms]) => {
                        const isCollapsed = isCategoryCollapsed(cat);
                        return (
                            <div key={cat} style={{ marginBottom: '8px' }}>
                                <div 
                                    className="symbol-group-label" 
                                    onClick={() => toggleCategory(cat)}
                                    style={{ 
                                        cursor: 'pointer', 
                                        display: 'flex', 
                                        justifyContent: 'space-between', 
                                        alignItems: 'center',
                                        userSelect: 'none',
                                        padding: '8px 14px 4px'
                                    }}
                                >
                                    <span>{cat}</span>
                                    <span style={{ 
                                        fontSize: '9px', 
                                        color: '#666', 
                                        transition: 'transform 0.2s cubic-bezier(0.4, 0, 0.2, 1)', 
                                        transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
                                        display: 'inline-block'
                                    }}>
                                        ▼
                                    </span>
                                </div>
                                <div 
                                    className={`symbol-group-content ${isCollapsed ? 'collapsed' : ''}`}
                                    style={{
                                        overflow: 'hidden',
                                        transition: 'max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease',
                                        maxHeight: isCollapsed ? '0px' : '15000px', // Large enough height to handle 300+ theme items comfortably
                                        opacity: isCollapsed ? 0 : 1
                                    }}
                                >
                                    {syms.map(s => {
                                        const isActive = location.pathname.includes(encodeURIComponent(s.ticker)) || location.pathname.endsWith(`/${s.ticker}`);
                                        
                                        // Tag helper for Asset badge
                                        const getAssetTag = () => {
                                            if (s.category === 'テーマ') return { label: 'THEME', className: 'badge-theme' };
                                            if (s.category === 'セクタ') return { label: 'SECTOR', className: 'badge-sector' };
                                            if (s.asset_class === 'Equity') return { label: 'STOCK', className: 'badge-equity' };
                                            return { label: s.asset_class || 'ETF', className: 'badge-etf' };
                                        };
                                        const tag = getAssetTag();

                                        return (
                                            <div
                                                key={s.id}
                                                id={`symbol-item-${s.id}`}
                                                className={`symbol-item ${isActive ? 'active' : ''}`}
                                                onClick={() => handleSymbolClick(s)}
                                                style={{
                                                    display: 'flex',
                                                    flexDirection: 'column',
                                                    alignItems: 'stretch',
                                                    gap: '3px',
                                                    padding: '8px 14px',
                                                    margin: '4px 10px',
                                                    borderRadius: '6px',
                                                    transition: 'all 0.2s',
                                                    cursor: 'pointer',
                                                    position: 'relative'
                                                }}
                                            >
                                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                                                    <span className="symbol-ticker" style={{ fontSize: '13px', fontWeight: 'bold' }}>{s.ticker}</span>
                                                    <span className={`symbol-badge ${tag.className}`} style={{ fontSize: '8px', padding: '2px 4px', borderRadius: '3px', fontWeight: 'bold' }}>
                                                        {tag.label}
                                                    </span>
                                                </div>
                                                
                                                {/* ThemeGroup::Theme splitting with delimiter checking */}
                                                {s.name && s.name.includes('::') ? (
                                                    <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
                                                        {/* ThemeGroup (upper) and Theme (lower) */}
                                                        <span style={{ fontSize: '9px', color: '#475685', fontWeight: 600, letterSpacing: '0.02em' }}>{s.name.split('::')[0]}</span>
                                                        <span style={{ fontSize: '10px', color: '#8b9cc8', fontWeight: 500 }}>{s.name.split('::')[1]}</span>
                                                    </div>
                                                ) : (
                                                    s.name && <span style={{ fontSize: '10px', color: '#8b9cc8', lineHeight: 1.2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </aside>

            {/* Main Panel Routing */}
            <main className="app-main" style={{ overflowY: 'auto', overflowX: 'hidden' }}>
                <Routes>
                    <Route path="/" element={<DashboardPage />} />
                    <Route path="/chart/:ticker" element={<ChartPage symbols={symbols} />} />
                    <Route path="/screener" element={<ScreenerPage />} />
                    <Route path="/screener/result/:presetId" element={<ScreenerResultPage />} />
                    <Route path="/watchlist" element={<WatchlistPage />} />
                    <Route path="/group/:ticker" element={<GroupPage />} />
                    <Route path="/portfolio" element={<TotalPortfolioPage />} />
                    <Route path="/portfolio/:portfolioId" element={<PortfolioDetailPage />} />
                    <Route path="/backtest" element={<BacktestPage />} />
                    <Route path="/scenariotest" element={<BacktestPage />} />
                    <Route path="/etf-backtest" element={<BacktestPage />} />
                </Routes>
            </main>
        </div>
    )
}
