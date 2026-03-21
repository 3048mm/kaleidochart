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
                        
                        {/* Technicals */}
                        <th style={{ width: '40px', padding: '8px', textAlign: 'center', borderLeft: '1px solid #333' }}>TD9</th>
                        <th style={{ width: '55px', padding: '8px', textAlign: 'right' }}>ADR%</th>
                        <th style={{ width: '55px', padding: '8px', textAlign: 'right' }}>ATR%</th>
                        <th style={{ width: '60px', padding: '8px', textAlign: 'right' }}>50/ATR</th>

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

                        {/* Volume Analysis */}
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right', borderLeft: '1px solid #333' }}>Surge21</th>
                        <th style={{ width: '65px', padding: '8px', textAlign: 'right' }}>RelV/SPY</th>
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

                                {/* Volume */}
                                <td style={{ padding: '6px 8px', textAlign: 'right', borderLeft: '1px solid rgba(255,255,255,0.03)', color: (d.vol_surge_21 || 0) > 2 ? appConfig.colors.good : 'inherit' }}>{formatN(d.vol_surge_21, 2)}</td>
                                <td style={{ padding: '6px 8px', textAlign: 'right', color: (d.rel_vol_vs_spy_21 || 0) > 1.2 ? appConfig.colors.good : 'inherit' }}>{formatN(d.rel_vol_vs_spy_21, 2)}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
};
