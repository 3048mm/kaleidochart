/* Integrated GroupPage: Features from ThemePage ported here */
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { GroupDataResponse, ThemeDetailResponse } from '../types';
import { EtfFeaturePanel } from '../components/EtfFeaturePanel';
import { Sparkline } from '../components/Sparkline';
import { RrgChart, RrgSeries } from '../components/RrgChart';
import RsLineChart from '../components/RsLineChart';
import { appConfig } from '../config';

const PALETTE = [
    '#26a69a', '#ab47bc', '#ffa726', '#42a5f5', '#ef5350',
    '#66bb6a', '#ec407a', '#ffee58', '#26c6da', '#ff7043',
    '#8d6e63', '#78909c', '#d4e157', '#29b6f6', '#ff5252',
    '#69f0ae', '#e040fb', '#ffcc02', '#00bcd4', '#ff6d00',
];

export const GroupPage: React.FC = () => {
    const { ticker } = useParams<{ ticker: string }>();
    const [searchParams] = useSearchParams();
    const dateParam = searchParams.get('date');

    const [data, setData] = useState<ThemeDetailResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selectedTickers, setSelectedTickers] = useState<Set<string>>(new Set());

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        setError('');

        const basicUrl = dateParam 
            ? `/api/group_data/${ticker}?date=${dateParam}`
            : `/api/group_data/${ticker}`;

        fetch(basicUrl)
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch group data');
                return res.json();
            })
            .then((groupJson: GroupDataResponse) => {
                if (groupJson.group_type === 'theme') {
                    return fetch(`/api/theme/${groupJson.feature.id}`)
                        .then(res => res.json())
                        .then((themeJson: ThemeDetailResponse) => {
                            setData(themeJson);
                            setSelectedTickers(new Set([themeJson.ticker]));
                        });
                } else {
                    setData(groupJson as any); 
                    setSelectedTickers(new Set([groupJson.ticker]));
                }
            })
            .catch(err => {
                console.error(err);
                setError(err.message);
            })
            .finally(() => setLoading(false));
    }, [ticker, dateParam]);

    const chartData = useMemo(() => {
        if (!data) return [];
        const d = data as any;
        if (d.chart_data) return d.chart_data;
        if (d.feature && d.feature.chart_data) return d.feature.chart_data;
        return [];
    }, [data]);

    const rrgSeries: RrgSeries[] = useMemo(() => {
        if (!data) return [];
        const result: RrgSeries[] = [];
        let colorIdx = 0;
        if (selectedTickers.has(data.ticker)) {
            result.push({ ticker: data.ticker, data: chartData, color: PALETTE[colorIdx++] });
        }
        (data.constituents || [])
            .filter(c => selectedTickers.has(c.ticker))
            .forEach(c => {
                result.push({ ticker: c.ticker, data: c.chart_data || [], color: PALETTE[colorIdx % PALETTE.length] });
                colorIdx++;
            });
        return result;
    }, [data, selectedTickers, chartData]);

    const colorMap = useMemo(() => {
        const m: Record<string, string> = {};
        rrgSeries.forEach(s => { m[s.ticker] = s.color; });
        return m;
    }, [rrgSeries]);

    const toggleTicker = (t: string) => {
        setSelectedTickers(prev => {
            const next = new Set(prev);
            next.has(t) ? next.delete(t) : next.add(t);
            return next;
        });
    };

    if (loading && !data) return <div style={{ padding: '50px', textAlign: 'center', color: '#aaa' }}>Loading Group Details...</div>;
    if (error || !data) return <div style={{ padding: '50px', textAlign: 'center', color: appConfig.colors.bad }}>{error || 'Data not found'}</div>;

    return (
        <div className="group-page" style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto' }}>
            <div style={{ marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '15px' }}>
                 <button onClick={() => window.history.back()} style={{ background: 'transparent', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '20px' }}>⬅</button>
                 <h1 style={{ margin: 0, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                     <span>{data.ticker}</span>
                     {data.name && data.name.includes('::') ? (
                         <div style={{ display: 'inline-flex', flexDirection: 'column', marginLeft: '4px', lineHeight: 1.2, textAlign: 'left' }}>
                             <span style={{ fontSize: '11px', color: '#8b9cc8', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase' }}>{data.name.split('::')[0]}</span>
                             <span style={{ fontSize: '20px', color: '#fff', fontWeight: 'bold' }}>{data.name.split('::')[1]}</span>
                         </div>
                     ) : (
                         <span style={{ fontSize: '18px', color: '#aaa', fontWeight: 'normal', marginLeft: '10px' }}>{data.name}</span>
                     )}
                 </h1>
            </div>

            <EtfFeaturePanel 
                feature={('feature' in data && data.feature) ? (data.feature as any) : data} 
                titleSuffix="ETF" 
            />

            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                <RsLineChart data={chartData} />
            </div>

            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    <h3 style={{ margin: 0 }}>📋 構成銘柄 ({data.constituents?.length || 0})</h3>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <button onClick={() => setSelectedTickers(new Set([data.ticker, ...(data.constituents || []).map(c => c.ticker)]))} style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}>全て選択</button>
                        <button onClick={() => setSelectedTickers(new Set([data.ticker]))} style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}>テーマのみ</button>
                    </div>
                </div>

                <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                    <table style={{ width: 'max-content', minWidth: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                        <thead>
                            <tr style={{ color: '#aaa', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, textAlign: 'left' }}>
                                <th style={{ padding: '8px', width: '30px' }}>RRG</th>
                                <th style={{ padding: '8px' }}>Ticker</th>
                                <th style={{ padding: '8px' }}>Name</th>
                                <th style={{ padding: '8px', textAlign: 'right' }}>Close</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1D%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1W%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1M%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RSR21%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RSR63%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RSM21%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RSM63%</th>
                                <th style={{ padding: '8px', textAlign: 'center', width: '80px' }}>RSR21% (30d)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(data.constituents || []).map((c: any) => {
                                const isSelected = selectedTickers.has(c.ticker);
                                const dotColor = colorMap[c.ticker];
                                
                                // Helper to get rank from either ThemeConstituentItem or DashboardPanelItem
                                const getRank = (type: '21' | '63' | 'mom21' | 'mom63') => {
                                    if (type === '21') return c.rank_rs_ratio_21 ?? c.rs_ratio_21_rank;
                                    if (type === '63') return c.rank_rs_ratio_63 ?? c.rs_ratio_63_rank;
                                    if (type === 'mom21') return c.rank_rs_momentum_21 ?? c.rs_momentum_21_rank;
                                    if (type === 'mom63') return c.rank_rs_momentum_63 ?? c.rs_momentum_63_rank;
                                    return 0;
                                };

                                const ranks = [getRank('21'), getRank('63'), getRank('mom21'), getRank('mom63')];
                                const formatRank = (r: number | undefined) => r != null ? `${(r * 100).toFixed(0)}%` : '-';
                                const colorRank = (r: number | undefined) => {
                                    if (r == null) return '#fff';
                                    return r > 0.8 ? appConfig.colors.good : r < 0.2 ? appConfig.colors.bad : '#fff';
                                };

                                return (
                                    <tr key={c.id} style={{ borderBottom: `1px solid ${appConfig.colors.glassBorder}`, background: isSelected ? 'rgba(255,255,255,0.03)' : 'transparent' }}>
                                        <td style={{ padding: '8px', textAlign: 'center' }}>
                                            <div 
                                                onClick={() => toggleTicker(c.ticker)}
                                                style={{ width: '14px', height: '14px', borderRadius: '2px', border: `2px solid ${isSelected ? (dotColor || appConfig.colors.accent) : '#555'}`, background: isSelected ? (dotColor || appConfig.colors.accent) : 'transparent', cursor: 'pointer', margin: '0 auto' }} 
                                            />
                                        </td>
                                        <td style={{ padding: '8px', fontWeight: 'bold' }}>
                                            <Link to={`/chart/${c.ticker}`} style={{ color: '#fff', textDecoration: 'none' }}>{c.ticker}</Link>
                                        </td>
                                        <td style={{ padding: '8px', color: '#aaa', maxWidth: '150px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</td>
                                        <td style={{ padding: '8px', textAlign: 'right' }}>{c.close?.toFixed(2)}</td>
                                        {[c.change_1d_pct ?? c.change_pct, c.change_1w_pct, c.change_1m_pct].map((v, i) => (
                                            <td key={i} style={{ padding: '8px', textAlign: 'center', color: v > 0 ? appConfig.colors.good : v < 0 ? appConfig.colors.bad : '#fff' }}>
                                                {v > 0 ? '+' : ''}{v?.toFixed(2)}%
                                            </td>
                                        ))}
                                        {ranks.map((r, i) => (
                                            <td key={i} style={{ padding: '8px', textAlign: 'center', fontWeight: 'bold', color: colorRank(r) }}>
                                                {formatRank(r)}
                                            </td>
                                        ))}
                                        <td style={{ padding: '8px' }}>
                                            {(() => {
                                                const spark = c.rs_sparkline || c.sparkline || [];
                                                const lastVal = spark.length > 0 ? spark[spark.length - 1] : 0;
                                                return (
                                                    <Sparkline 
                                                        data={spark} 
                                                        width={80} 
                                                        height={20} 
                                                        color={lastVal > 0 ? appConfig.colors.good : appConfig.colors.bad} 
                                                    />
                                                );
                                            })()}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="glass-panel" style={{ padding: '20px' }}>
                <h3 style={{ marginTop: 0, marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>📡 RRG Chart</h3>
                {rrgSeries.length > 0 ? (
                    <div style={{ height: '750px' }}>
                        <RrgChart series={rrgSeries} />
                    </div>
                ) : (
                    <div style={{ padding: '40px', textAlign: 'center', color: '#666' }}>銘柄を選択してください</div>
                )}
            </div>
        </div>
    );
};
