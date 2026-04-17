import { useState, useEffect, useCallback } from 'react';

/**
 * Hook to manage watchlist state globally (active tickers).
 */
export function useWatchlist() {
    const [activeTickers, setActiveTickers] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(true);

    const refreshActiveTickers = useCallback(async () => {
        try {
            const res = await fetch('/api/watchlist/tickers');
            if (res.ok) {
                const data = await res.json();
                setActiveTickers(new Set(data.tickers));
            }
        } catch (err) {
            console.error('Failed to fetch watchlist tickers:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        refreshActiveTickers();
    }, [refreshActiveTickers]);

    const toggleWatchlist = async (ticker: string, entry_date?: string) => {
        const isActive = activeTickers.has(ticker);

        try {
            if (isActive) {
                // Remove
                const res = await fetch(`/api/watchlist/${ticker}`, { method: 'DELETE' });
                if (res.ok) {
                    const next = new Set(activeTickers);
                    next.delete(ticker);
                    setActiveTickers(next);
                }
            } else {
                // Add
                // Default to today if no date provided
                const date = entry_date || new Date().toISOString().split('T')[0];
                const res = await fetch('/api/watchlist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker, entry_date: date })
                });
                if (res.ok) {
                    const next = new Set(activeTickers);
                    next.add(ticker);
                    setActiveTickers(next);
                }
            }
        } catch (err) {
            console.error('Failed to toggle watchlist:', err);
        }
    };

    const isTickerActive = (ticker: string) => activeTickers.has(ticker);

    return {
        activeTickers,
        isTickerActive,
        toggleWatchlist,
        refreshActiveTickers,
        loading
    };
}
