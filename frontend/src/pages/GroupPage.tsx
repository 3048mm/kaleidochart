/* Integrated GroupPage: Features from ThemePage ported here */
import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { GroupDataResponse, ThemeDetailResponse, ThemeConstituentItem } from '../types';
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

    const rrgSeries: RrgSeries[] = useMemo(() => {
        if (!data) return [];
        const result: RrgSeries[] = [];
        let colorIdx = 0;
        if (selectedTickers.has(data.ticker)) {
            result.push({ ticker: data.ticker, data: data.chart_data, color: PALETTE[colorIdx++] });
        }
        (data.constituents || [])
            .filter(c => selectedTickers.has(c.ticker))
            .forEach(c => {
                result.push({ ticker: c.ticker, data: c.chart_data || [], color: PALETTE[colorIdx % PALETTE.length] });
                colorIdx++;
            });
        return result;
    }, [data, selectedTickers]);

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
                 <h1 style={{ margin: 0 }}>{data.ticker} <span style={{ fontSize: '18px', color: '#aaa', fontWeight: 'normal' }}>{data.name}</span></h1>
            </div>

            <EtfFeaturePanel feature={data as any} titleSuffix="ETF" />

            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                <RsLineChart data={data.chart_data} />
            </div>

            <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px' }}>
                    <h3 style={{ margin: 0 }}>📋 構成銘柄 ({data.constituents?.length || 0})</h3>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <button onClick={() => setSelectedTickers(new Set([data.ticker, ...(data.constituents || []).map(c => c.ticker)]))} style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}>全て選択</button>
                        <button onClick={() => setSelectedTickers(new Set([data.ticker]))} style={{ padding: '4px 10px', background: 'rgba(255,255,255,0.07)', border: `1px solid ${appConfig.colors.glassBorder}`, borderRadius: '5px', color: '#ccc', cursor: 'pointer', fontSize: '11px' }}>テーマのみ</button>
                    </div>
                </div>

                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                        <thead>
                            <tr style={{ color: '#aaa', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, textAlign: 'left' }}>
                                <th style={{ padding: '8px', width: '30px' }}>RRG</th>
                                <th style={{ padding: '8px' }}>Ticker</th>
                                <th style={{ padding: '8px' }}>Name</th>
                                <th style={{ padding: '8px', textAlign: 'right' }}>Close</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1D%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1W%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>1M%</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RS14</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RS21</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RS63</th>
                                <th style={{ padding: '8px', textAlign: 'center' }}>RS Mom</th>
                                <th style={{ padding: '8px', textAlign: 'center', width: '80px' }}>Trend</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(data.constituents || []).map((c: any) => {
                                const isSelected = selectedTickers.has(c.ticker);
                                const dotColor = colorMap[c.ticker];
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
                                        {[c.change_1d_pct, c.change_1w_pct, c.change_1m_pct].map((v, i) => (
                                            <td key={i} style={{ padding: '8px', textAlign: 'center', color: v > 0 ? appConfig.colors.good : v < 0 ? appConfig.colors.bad : '#fff' }}>
                                                {v > 0 ? '+' : ''}{v?.toFixed(2)}%
                                            </td>
                                        ))}
                                        {[c.rs_ratio_14, c.rs_ratio_21, c.rs_ratio_63].map((v, i) => (
                                            <td key={i} style={{ padding: '8px', textAlign: 'center', color: v > 100 ? appConfig.colors.good : v < 100 ? appConfig.colors.bad : '#fff' }}>
                                                {v?.toFixed(2)}
                                            </td>
                                        ))}
                                        <td style={{ padding: '8px', textAlign: 'center', color: (c.rs_momentum_21 || 0) > 0 ? appConfig.colors.good : appConfig.colors.bad }}>
                                            {(c.rs_momentum_21 || 0).toFixed(2)}
                                        </td>
                                        <td style={{ padding: '8px' }}>
                                            <Sparkline data={c.rs_sparkline || []} width={80} height={20} color={(c.rs_sparkline?.[c.rs_sparkline.length-1] || 0) > 0 ? appConfig.colors.good : appConfig.colors.bad} />
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
                    <div style={{ height: '500px' }}>
                        <RrgChart series={rrgSeries} />
                    </div>
                ) : (
                    <div style={{ padding: '40px', textAlign: 'center', color: '#666' }}>銘柄を選択してください</div>
                )}
            </div>
        </div>
    );
};
