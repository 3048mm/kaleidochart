import { useEffect, useState, useMemo } from 'react'
import { Routes, Route, Link, useNavigate, useLocation } from 'react-router-dom'
import { Symbol, SystemInfo, SystemHealthResponse } from './types'
import { DashboardPage } from './pages/DashboardPage'
import { ChartPage } from './pages/ChartPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { ScreenerResultPage } from './pages/ScreenerResultPage'
import { WatchlistPage } from './pages/WatchlistPage'
import { GroupPage } from './pages/GroupPage'
import { TotalPortfolioPage } from './pages/TotalPortfolioPage'
import { PortfolioDetailPage } from './pages/PortfolioDetailPage'
import { BacktestPage } from './pages/BacktestPage'
import { UniversePage } from './pages/UniversePage'


const API = '/api'

export default function App() {
    const [symbols, setSymbols] = useState<Symbol[]>([])
    const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)
    const [systemHealth, setSystemHealth] = useState<SystemHealthResponse | null>(null)
    const [healthPopoverOpen, setHealthPopoverOpen] = useState(false)
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

    const fetchSystemHealth = () => {
        fetch(`${API}/system/health`)
            .then(r => {
                if (!r.ok) throw new Error("API error");
                return r.json();
            })
            .then((data: SystemHealthResponse) => {
                setSystemHealth(data);
            })
            .catch(err => {
                console.error("Failed to fetch system health:", err);
            });
    }

    // Load symbols on mount
    useEffect(() => {
        fetch(`${API}/symbols`).then(r => r.json()).then((data: Symbol[]) => {
            setSymbols(data)
        })
        fetch(`${API}/system/info`).then(r => r.json()).then((data: SystemInfo) => {
            setSystemInfo(data)
        })
        
        fetchSystemHealth();
        const interval = setInterval(fetchSystemHealth, 60000); // Poll every 60s
        return () => clearInterval(interval);
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
                        <span>KaleidoChart</span>
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
                <nav className="pc-only-nav" style={{ display: 'flex', gap: '20px', marginRight: '20px' }}>
                    <Link to="/" style={{ color: location.pathname === '/' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Dashboard</Link>
                    <Link to="/screener" style={{ color: location.pathname === '/screener' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Screener</Link>
                    <Link to="/watchlist" style={{ color: location.pathname === '/watchlist' ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Watchlist</Link>
                    <Link to="/portfolio" style={{ color: location.pathname.startsWith('/portfolio') ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Portfolio</Link>
                    <Link to="/backtest" style={{ color: location.pathname.startsWith('/backtest') || location.pathname.startsWith('/scenariotest') || location.pathname.startsWith('/etf-backtest') ? '#00ff88' : '#d1d4dc', textDecoration: 'none' }}>Backtest</Link>
                    <Link to="/universe" style={{ color: location.pathname === '/universe' ? '#00ff88' : '#d1d4dc', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                        Universe
                        {/* IPO 追加候補の未レビュー件数。ステータス詳細はクリックしないと
                            見えないので、気づける場所にも出す */}
                        {!!systemHealth?.universe?.ipo_candidates_pending && (
                            <span
                                title={`IPO 追加候補が ${systemHealth.universe.ipo_candidates_pending} 件レビュー待ちです`}
                                style={{
                                    padding: '1px 6px',
                                    background: '#ff9800',
                                    color: '#1a1a1a',
                                    borderRadius: '9px',
                                    fontSize: '10px',
                                    fontWeight: 700,
                                    lineHeight: 1.5,
                                }}
                            >
                                {systemHealth.universe.ipo_candidates_pending}
                            </span>
                        )}
                    </Link>
                </nav>
                
                {systemHealth && (
                    <div style={{ position: 'relative', display: 'inline-block', marginRight: '20px' }}>
                        <button
                            onClick={() => setHealthPopoverOpen(prev => !prev)}
                            style={{
                                padding: '4px 10px',
                                backgroundColor: 'rgba(21, 26, 38, 0.6)',
                                backdropFilter: 'blur(4px)',
                                border: `1px solid ${
                                    systemHealth.overall_status === 'healthy' ? '#00ff88' :
                                    systemHealth.overall_status === 'warning' ? '#ff9800' : '#ff4444'
                                }`,
                                color: '#fff',
                                borderRadius: '6px',
                                fontSize: '11px',
                                fontWeight: 600,
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '6px',
                                transition: 'all 0.2s',
                                boxShadow: `0 0 10px ${
                                    systemHealth.overall_status === 'healthy' ? 'rgba(0, 255, 136, 0.15)' :
                                    systemHealth.overall_status === 'warning' ? 'rgba(255, 152, 0, 0.15)' : 'rgba(255, 68, 68, 0.15)'
                                }`
                            }}
                        >
                            <span style={{
                                width: '8px',
                                height: '8px',
                                borderRadius: '50%',
                                backgroundColor: 
                                    systemHealth.overall_status === 'healthy' ? '#00ff88' :
                                    systemHealth.overall_status === 'warning' ? '#ff9800' : '#ff4444',
                                display: 'inline-block',
                                boxShadow: `0 0 6px ${
                                    systemHealth.overall_status === 'healthy' ? '#00ff88' :
                                    systemHealth.overall_status === 'warning' ? '#ff9800' : '#ff4444'
                                }`
                            }} />
                            <span>
                                {systemHealth.pipeline_status.is_running ? 'Updating' : 
                                 systemHealth.overall_status === 'healthy' ? 'Live' : 
                                 systemHealth.overall_status === 'warning' ? 'Delayed' : 'Error'}
                            </span>
                        </button>
                        
                        {healthPopoverOpen && (
                            <div style={{
                                position: 'absolute',
                                top: '100%',
                                right: 0,
                                marginTop: '8px',
                                width: '320px',
                                backgroundColor: 'rgba(21, 26, 38, 0.95)',
                                backdropFilter: 'blur(10px)',
                                border: '1px solid rgba(255, 255, 255, 0.1)',
                                borderRadius: '8px',
                                boxShadow: '0 10px 25px rgba(0, 0, 0, 0.5)',
                                padding: '16px',
                                zIndex: 1000,
                                color: '#d1d4dc',
                                fontSize: '12px',
                                textAlign: 'left'
                            }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px', borderBottom: '1px solid rgba(255, 255, 255, 0.1)', paddingBottom: '8px' }}>
                                    <span style={{ fontWeight: 'bold', color: '#fff' }}>System Status</span>
                                    <button 
                                        onClick={() => setHealthPopoverOpen(false)}
                                        style={{ background: 'none', border: 'none', color: '#d1d4dc', cursor: 'pointer', fontSize: '14px' }}
                                    >
                                        ×
                                    </button>
                                </div>
                                
                                {/* Freshness */}
                                <div style={{ marginBottom: '12px' }}>
                                    <div style={{ fontWeight: 'bold', color: '#00ff88', marginBottom: '4px' }}>Data Freshness</div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '4px' }}>
                                        <div>T2 (Prices):</div><div>{systemHealth.data_freshness.daily_prices || 'N/A'}</div>
                                        <div>T3 (Indicators):</div><div>{systemHealth.data_freshness.indicators || 'N/A'}</div>
                                        <div>T4 (Ranks):</div><div>{systemHealth.data_freshness.relative_ranks || 'N/A'}</div>
                                        <div>T5 (Signals):</div><div>{systemHealth.data_freshness.market_signals || 'N/A'}</div>
                                        <div>SPY Latest Date:</div><div>{systemHealth.data_freshness.spy_latest || 'N/A'}</div>
                                        {systemHealth.data_freshness.delay_days !== null && (
                                            <>
                                                <div>Delay Days:</div><div>{systemHealth.data_freshness.delay_days} day(s)</div>
                                            </>
                                        )}
                                    </div>
                                </div>

                                {/* Integrity */}
                                <div style={{ marginBottom: '12px' }}>
                                    <div style={{ fontWeight: 'bold', color: '#00ff88', marginBottom: '4px' }}>Data Integrity</div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '4px' }}>
                                        <div>Latest Date Count:</div><div>T2: {systemHealth.data_integrity.daily_prices_count} / T3: {systemHealth.data_integrity.indicators_count}</div>
                                        <div>Integrity Check:</div>
                                        <div style={{ color: systemHealth.data_integrity.is_consistent ? '#00ff88' : '#ff4444' }}>
                                            {systemHealth.data_integrity.is_consistent ? 'Matched (OK)' : 'Mismatched (NG)'}
                                        </div>
                                    </div>
                                </div>

                                {/* Pipeline */}
                                <div style={{ marginBottom: '12px' }}>
                                    <div style={{ fontWeight: 'bold', color: '#00ff88', marginBottom: '4px' }}>Pipeline Status</div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '4px' }}>
                                        <div>Execution:</div>
                                        <div style={{ color: systemHealth.pipeline_status.is_running ? '#ff9800' : '#d1d4dc' }}>
                                            {systemHealth.pipeline_status.is_running ? 'Running' : 'Idle'}
                                        </div>
                                        <div>Last Completed:</div>
                                        <div>
                                            {systemHealth.pipeline_status.last_completed_at
                                                ? new Date(systemHealth.pipeline_status.last_completed_at).toLocaleString('ja-JP')
                                                : 'N/A'}
                                        </div>
                                    </div>
                                </div>

                                {/* Universe */}
                                <div style={{ marginBottom: '12px' }}>
                                    <div style={{ fontWeight: 'bold', color: '#00ff88', marginBottom: '4px' }}>Universe</div>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '4px' }}>
                                        <div>IPO Candidates:</div>
                                        <div style={{ color: systemHealth.universe?.ipo_candidates_pending ? '#ff9800' : '#d1d4dc' }}>
                                            {systemHealth.universe?.ipo_candidates_pending ?? 0} pending
                                        </div>
                                    </div>
                                </div>

                                {/* Validation Warnings */}
                                {systemHealth.validation.warnings.length > 0 && (
                                    <div>
                                        <div style={{ fontWeight: 'bold', color: '#ff4444', marginBottom: '4px' }}>Preset Warnings</div>
                                        <div style={{ 
                                            maxHeight: '100px', 
                                            overflowY: 'auto', 
                                            backgroundColor: 'rgba(255, 68, 68, 0.1)', 
                                            padding: '8px', 
                                            borderRadius: '4px',
                                            border: '1px solid rgba(255, 68, 68, 0.2)',
                                            fontSize: '11px',
                                            color: '#ff8888'
                                        }}>
                                            {systemHealth.validation.warnings.map((w, idx) => (
                                                <div key={idx} style={{ marginBottom: '4px' }}>• {w}</div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </header>

            {/* Sidebar Backdrop (mobile) */}
            <div
                className={`sidebar-backdrop ${sidebarOpen ? 'open' : ''}`}
                onClick={() => setSidebarOpen(false)}
            />

            <aside className={`app-sidebar ${sidebarOpen ? 'open' : ''}`}>
                <div className="mobile-only-nav" style={{ 
                    flexDirection: 'column', 
                    gap: '12px', 
                    padding: '16px 14px', 
                    borderBottom: '1px solid var(--border)',
                    background: 'rgba(30, 40, 70, 0.15)'
                }}>
                    <Link to="/" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname === '/' ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px' }}>Dashboard</Link>
                    <Link to="/screener" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname === '/screener' ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px' }}>Screener</Link>
                    <Link to="/watchlist" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname === '/watchlist' ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px' }}>Watchlist</Link>
                    <Link to="/portfolio" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname.startsWith('/portfolio') ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px' }}>Portfolio</Link>
                    <Link to="/backtest" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname.startsWith('/backtest') || location.pathname.startsWith('/scenariotest') || location.pathname.startsWith('/etf-backtest') ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px' }}>Backtest</Link>
                    <Link to="/universe" onClick={() => setSidebarOpen(false)} style={{ color: location.pathname === '/universe' ? '#00ff88' : '#d1d4dc', textDecoration: 'none', fontWeight: 600, fontSize: '13px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        Universe
                        {!!systemHealth?.universe?.ipo_candidates_pending && (
                            <span
                                style={{
                                    padding: '1px 6px',
                                    background: '#ff9800',
                                    color: '#1a1a1a',
                                    borderRadius: '9px',
                                    fontSize: '10px',
                                    fontWeight: 700,
                                    lineHeight: 1.5,
                                }}
                            >
                                {systemHealth.universe.ipo_candidates_pending}
                            </span>
                        )}
                    </Link>
                </div>
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
                    <Route path="/universe" element={<UniversePage />} />
                </Routes>
            </main>
        </div>
    )
}
