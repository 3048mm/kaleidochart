import React, { useState, useEffect } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { GroupDataResponse } from '../types';
import { EtfFeaturePanel } from '../components/EtfFeaturePanel';
import { SummaryTable } from '../components/SummaryTable';
import { appConfig } from '../config';

export const GroupPage: React.FC = () => {
    const { ticker } = useParams<{ ticker: string }>();
    const [searchParams] = useSearchParams();
    const dateParam = searchParams.get('date');

    const [data, setData] = useState<GroupDataResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!ticker) return;

        setLoading(true);
        setError('');

        const url = dateParam 
            ? `/api/group_data/${ticker}?date=${dateParam}`
            : `/api/group_data/${ticker}`;

        fetch(url)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch group data');
                return res.json();
            })
            .then((json: GroupDataResponse) => {
                setData(json);
            })
            .catch(err => {
                console.error(err);
                setError(err.message);
            })
            .finally(() => setLoading(false));
    }, [ticker, dateParam]);

    if (loading && !data) {
        return (
            <div style={{ padding: '50px', textAlign: 'center', color: '#aaa' }}>
                <div className="loader" style={{ marginBottom: '20px' }}></div>
                Loading Group Details for {ticker}...
            </div>
        );
    }

    if (error || !data) {
        return (
            <div style={{ padding: '50px', textAlign: 'center' }}>
                <div style={{ color: appConfig.colors.bad, marginBottom: '20px' }}>{error || 'Data not found'}</div>
                <button 
                    onClick={() => window.history.back()}
                    style={{ 
                        padding: '10px 20px', 
                        background: 'rgba(255,255,255,0.1)', 
                        border: `1px solid ${appConfig.colors.glassBorder}`,
                        borderRadius: '6px',
                        color: '#fff',
                        cursor: 'pointer'
                    }}
                >
                    Go Back
                </button>
            </div>
        );
    }

    return (
        <div className="group-page" style={{ padding: '20px', maxWidth: '1200px', margin: '0 auto' }}>
            <div style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '15px' }}>
                 <button 
                    onClick={() => window.history.back()}
                    style={{ 
                        background: 'transparent', 
                        border: 'none',
                        color: '#aaa',
                        cursor: 'pointer',
                        fontSize: '20px',
                        padding: '5px'
                    }}
                >
                    ⬅
                </button>
                <h1 style={{ margin: 0 }}>
                    {data.group_type === 'sector' ? 'Sector:' : 'Theme:'} {data.name}
                </h1>
            </div>

            {/* upper panel: ETF feature */}
            <EtfFeaturePanel feature={data.feature} titleSuffix={data.group_type === 'sector' ? 'Sector ETF' : 'Theme ETF'} />

            {/* lower panel: Constituents list */}
            <div className="glass-panel" style={{ padding: '20px' }}>
                <h3 style={{ marginTop: 0, marginBottom: '20px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    {data.group_type === 'sector' ? '📂 Themes in this Sector' : '💎 Member Stocks'}
                </h3>
                
                <SummaryTable 
                    items={data.constituents} 
                    maxPct={data.group_type === 'sector' ? appConfig.thresholds.sparkline_max_pct_theme : appConfig.thresholds.sparkline_max_pct_index}
                    linkTo={(item) => {
                        if (data.group_type === 'sector') {
                            // Link themes to their own GroupPage
                            return `/group/${encodeURIComponent(item.ticker)}${dateParam ? `?date=${dateParam}` : ''}`;
                        }
                        // Stocks go to chart page
                        return `/chart/${encodeURIComponent(item.ticker)}`;
                    }}
                />
            </div>
        </div>
    );
};
