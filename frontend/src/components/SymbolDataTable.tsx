import React, { useMemo } from 'react';
import { ChartDataPoint } from '../types';
import { appConfig } from '../config';

interface SymbolDataTableProps {
    data: ChartDataPoint[];
}

export const SymbolDataTable: React.FC<SymbolDataTableProps> = ({ data }) => {
    // 降順ソート & 1年分 (約252日) に制限
    const tableData = useMemo(() => {
        return [...data]
            .sort((a, b) => (a.time > b.time ? -1 : a.time < b.time ? 1 : 0))
            .slice(0, 252);
    }, [data]);

    const formatN = (v: number | null | undefined, dec = 2) => {
        if (v == null) return '-';
        return v.toFixed(dec);
    };

    const getTd9Color = (val: number | null | undefined) => {
        if (!val) return 'transparent';
        return val > 0 ? 'rgba(239, 83, 80, 0.2)' : 'rgba(102, 187, 106, 0.2)';
    };

    const getChgColor = (val: number | null | undefined) => {
        if (!val) return 'inherit';
        return val > 0 ? appConfig.colors.good : val < 0 ? appConfig.colors.bad : 'inherit';
    };

    const formatCap = (val: number | null | undefined) => {
        if (val == null || val === 0) return '-';
        if (val >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
        if (val >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
        if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
        return val.toLocaleString();
    };

    const getConditionColor = (cond: number | null | undefined) => {
        if (cond == null) return 'inherit';
        if (cond >= 2) return appConfig.colors.good; 
        if (cond <= -2) return appConfig.colors.bad;
        return 'inherit';
    };

    return (
        <div style={{ 
            flex: 1, 
            overflow: 'auto', 
            background: 'rgba(0,0,0,0.2)', 
            borderRadius: '8px',
            border: `1px solid ${appConfig.colors.glassBorder}`,
            margin: '0 20px 20px 20px'
        }}>
            <table style={{ 
                width: 'max-content', // Allow table to be wide
                minWidth: '100%',
                borderCollapse: 'collapse', 
                fontSize: '11px', 
                color: '#ccc'
            }}>
                <thead style={{ position: 'sticky', top: 0, zIndex: 10, background: '#1a1a1a' }}>
                    <tr style={{ borderBottom: `2px solid ${appConfig.colors.glassBorder}` }}>
                        <th style={{ width: '85px', padding: '8px', textAlign: 'left' }}>Date</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Close</th>
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right' }}>Open</th>
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right' }}>High</th>
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right' }}>Low</th>
                        <th style={{ width: '80px', padding: '8px', textAlign: 'right' }}>Volume</th>
                        <th style={{ width: '80px', padding: '8px', textAlign: 'right' }}>Mkt Cap</th>
                        
                        {/* Technicals */}
                        <th style={{ width: '40px', padding: '8px', textAlign: 'center', borderLeft: '1px solid #333' }}>TD9</th>
                        <th style={{ width: '55px', padding: '8px', textAlign: 'right' }}>ADR%</th>
                        <th style={{ width: '55px', padding: '8px', textAlign: 'right' }}>ATR%</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>50/ATR</th>
                        <th style={{ width: '55px', padding: '8px', textAlign: 'right' }}>VCR</th>

                        {/* Moving Averages */}
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>SMA5</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>SMA21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>SMA50</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>SMA63</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>SMA150</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>SMA200</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>EMA5</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA50</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA63</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA150</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA200</th>

                        {/* RS EMA (Smoothed) */}
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>EMA RS14</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA RS21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>EMA RS63</th>

                        {/* RS Ratio */}
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>Rat 14</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Rat 21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Rat 63</th>
                        
                        {/* RS Momentum */}
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>Mom 14</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Mom 21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Mom 63</th>

                        {/* RS Condition */}
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>Cnd 14</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Cnd 21</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>Cnd 63</th>
                        <th style={{ width: '45px', padding: '8px', textAlign: 'center', color: '#00ff88' }}>Blue</th>
                        <th style={{ width: '45px', padding: '8px', textAlign: 'center', color: '#ff4444' }}>Red</th>

                        {/* Volume Analysis */}
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>Surge21</th>
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right' }}>RelV/SPY</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>UDV50</th>
                    </tr>
                </thead>
                <tbody>
                    {tableData.map((d, idx) => {
                        const prev = tableData[idx + 1];
                        const dailyChg = prev ? ((d.close - prev.close) / prev.close) * 100 : null;
                        
                        return (
                            <tr key={d.time} style={{ 
                                borderBottom: '1px solid rgba(255,255,255,0.03)',
                                background: d.td9 && Math.abs(d.td9) === 9 ? 'rgba(255,255,255,0.05)' : 'transparent'
                            }}>
                                <td style={{ padding: '6px 8px', color: '#888' }}>{d.time}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 'bold', color: getChgColor(dailyChg) }}>
                                    {formatN(d.close)}
                                </td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.open)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.high)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.low)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: '#666' }}>{d.volume?.toLocaleString()}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: '#888' }}>{formatCap(d.market_cap)}</td>
                                
                                {/* Technicals */}
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'center', fontWeight: 'bold', borderLeft: '1px solid rgba(255,255,255,0.03)',
                                    color: d.td9 && d.td9 > 0 ? appConfig.colors.bad : appConfig.colors.good,
                                    background: getTd9Color(d.td9)
                                }}>{d.td9 || ''}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.adr_pct_21, 2)}%</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.atr_pct_14, 2)}%</td>
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'right',
                                    fontWeight: (d.dist_sma50_atr || 0) >= 8 ? 'bold' : 'normal',
                                    color: (d.dist_sma50_atr || 0) >= 10 ? '#ff0000' : (d.dist_sma50_atr || 0) >= 8 ? '#ffff00' : '#aaa'
                                }}>{formatN(d.dist_sma50_atr, 1)}</td>
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'right',
                                    color: (d.vcr || 1) < 0.5 ? '#00ff88' : '#aaa'
                                }}>{formatN(d.vcr, 2)}</td>

                                {/* MAs */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)' }}>{formatN(d.sma_5)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.sma_21)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.sma_50)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.sma_63)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.sma_150)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.sma_200)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)' }}>{formatN(d.ema_5)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.ema_21)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.ema_50)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.ema_63)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.ema_150)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatN(d.ema_200)}</td>

                                {/* RS EMA (Smoothed) */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: '#888' }}>{formatN(d.rs_ema_14, 4)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: '#888' }}>{formatN(d.rs_ema_21, 4)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: '#888' }}>{formatN(d.rs_ema_63, 4)}</td>

                                {/* RS Ratio */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: getChgColor((d.rs_ratio_14 || 0) - 100) }}>{formatN(d.rs_ratio_14)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getChgColor((d.rs_ratio_21 || 0) - 100) }}>{formatN(d.rs_ratio_21)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getChgColor((d.rs_ratio_63 || 0) - 100) }}>{formatN(d.rs_ratio_63)}</td>

                                {/* RS Momentum */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: getChgColor(d.rs_momentum_14) }}>{formatN(d.rs_momentum_14)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getChgColor(d.rs_momentum_21) }}>{formatN(d.rs_momentum_21)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getChgColor(d.rs_momentum_63) }}>{formatN(d.rs_momentum_63)}</td>

                                {/* RS Condition */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: getConditionColor(d.rs_condition_14) }}>{formatN(d.rs_condition_14, 2)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getConditionColor(d.rs_condition_21) }}>{formatN(d.rs_condition_21, 2)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: getConditionColor(d.rs_condition_63) }}>{formatN(d.rs_condition_63, 2)}</td>
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'center', 
                                    background: d.rs_blue_dot === 1 ? 'rgba(0, 255, 136, 0.15)' : 'transparent',
                                    color: '#00ff88', fontSize: '14px'
                                }}>{d.rs_blue_dot === 1 ? '●' : ''}</td>
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'center', 
                                    background: d.rs_red_dot === 1 ? 'rgba(255, 68, 68, 0.15)' : 'transparent',
                                    color: '#ff4444', fontSize: '14px'
                                }}>{d.rs_red_dot === 1 ? '●' : ''}</td>

                                {/* Volume */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: (d.vol_surge_21 || 0) > 2 ? appConfig.colors.good : 'inherit' }}>{formatN(d.vol_surge_21, 2)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: (d.rel_vol_vs_spy_21 || 0) > 1.2 ? appConfig.colors.good : 'inherit' }}>{formatN(d.rel_vol_vs_spy_21, 2)}</td>
                                <td style={{ 
                                    padding: '6px 8px', textAlign: 'right', 
                                    color: (d.up_down_vol_ratio_50 || 0) > 1.5 ? appConfig.colors.good : (d.up_down_vol_ratio_50 || 0) < 0.7 ? appConfig.colors.bad : 'inherit'
                                }}>{formatN(d.up_down_vol_ratio_50, 2)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};
