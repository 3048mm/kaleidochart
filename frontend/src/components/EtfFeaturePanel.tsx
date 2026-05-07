import React from 'react';
import { Link } from 'react-router-dom';
import { EtfFeatureItem } from '../types';
import { MiniChart } from './MiniChart';
import { Sparkline } from './Sparkline';
import { appConfig } from '../config';

interface EtfFeaturePanelProps {
    feature: EtfFeatureItem;
    titleSuffix?: string;
    showFullChartLink?: boolean;
}

export const EtfFeaturePanel: React.FC<EtfFeaturePanelProps> = ({ 
    feature, 
    titleSuffix = "ETF", 
    showFullChartLink = true 
}) => {
    return (
        <div className="glass-panel" style={{ padding: '20px', marginBottom: '20px', display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            {/* Left: KPIs */}
            <div style={{ flex: '1', minWidth: '350px', display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${appConfig.colors.glassBorder}`, paddingBottom: '10px', marginBottom: '15px' }}>
                    <h2 style={{ margin: 0, fontSize: '20px' }}>
                        {feature.ticker} <span style={{ fontSize: '14px', color: '#aaa', fontWeight: 'normal' }}>{feature.name || titleSuffix}</span>
                    </h2>
                    <div style={{ fontSize: '20px', fontWeight: 'bold' }}>{(feature.close || 0).toFixed(2)}</div>
                    {showFullChartLink && (
                        <Link to={`/chart/${encodeURIComponent(feature.ticker)}`} style={{ color: appConfig.colors.accent, textDecoration: 'none', fontSize: '13px', fontWeight: 'bold' }}>View Full Chart →</Link>
                    )}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '15px' }}>
                    {[
                        { label: '1D Gain', val: feature.change_1d_pct },
                        { label: '1W Gain', val: feature.change_1w_pct },
                        { label: '1M Gain', val: feature.change_1m_pct },
                        { label: '1Y Gain', val: feature.change_1y_pct },
                    ].map(metric => (
                        <div key={metric.label} style={{ background: 'rgba(255,255,255,0.05)', padding: '10px', borderRadius: '6px', textAlign: 'center' }}>
                            <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '4px' }}>{metric.label}</div>
                            <div style={{ fontSize: '14px', fontWeight: 'bold', color: (metric.val || 0) > 0 ? appConfig.colors.good : (metric.val || 0) < 0 ? appConfig.colors.bad : '#fff' }}>
                                {(metric.val || 0) > 0 ? '+' : ''}{(metric.val || 0).toFixed(2)}%
                            </div>
                        </div>
                    ))}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '15px' }}>
                    {[
                        { label: 'dist SMA5', val: feature.dist_sma5_pct },
                        { label: 'dist SMA21', val: feature.dist_sma21_pct },
                        { label: 'dist SMA63', val: feature.dist_sma63_pct },
                        { label: 'SMA21/63 diff', val: feature.sma21_sma63_pct },
                    ].map(metric => (
                        <div key={metric.label} style={{ background: 'rgba(255,255,255,0.05)', padding: '10px', borderRadius: '6px', textAlign: 'center' }}>
                            <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '4px' }}>{metric.label}</div>
                            <div style={{ fontSize: '14px', fontWeight: 'bold', color: (metric.val || 0) > 0 ? appConfig.colors.good : (metric.val || 0) < 0 ? appConfig.colors.bad : '#fff' }}>
                                {(metric.val || 0) > 0 ? '+' : ''}{(metric.val || 0).toFixed(2)}%
                            </div>
                        </div>
                    ))}
                </div>

                {/* RS Ranks & Sparklines */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
                    {[
                        { label: 'RS Ratio 14', spark: feature.rs14_sparkline, rank: feature.rank_rs_ratio_14 },
                        { label: 'RS Ratio 21', spark: feature.rs21_sparkline, rank: feature.rank_rs_ratio_21 },
                        { label: 'RS Ratio 63', spark: feature.rs63_sparkline, rank: feature.rank_rs_ratio_63 },
                    ].map(s => (
                        <div key={s.label} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            <div style={{ fontSize: '10px', color: '#aaa' }}>{s.label}</div>
                            {s.spark && (
                                <div style={{ height: '24px' }}>
                                    <Sparkline 
                                        data={s.spark} 
                                        width={100} 
                                        height={24} 
                                        color={s.spark[s.spark.length - 1] > (s.spark[0] || 0) ? appConfig.colors.good : appConfig.colors.bad}
                                        fixedRange={true}
                                    />
                                </div>
                            )}
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '2px' }}>
                                <span style={{ fontSize: '9px', color: '#666' }}>Rank</span>
                                <span style={{ fontSize: '12px', fontWeight: 'bold', color: (s.rank || 0) > 0.8 ? appConfig.colors.good : (s.rank || 0) < 0.2 ? appConfig.colors.bad : '#fff' }}>
                                    {s.rank != null ? `${(s.rank * 100).toFixed(0)}%` : '-'}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
            
            {/* Right: Mini Chart */}
            <div style={{ flex: '1', minWidth: '350px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '10px' }}>
                    <div style={{ fontSize: '11px', color: '#aaa', marginBottom: '10px', textAlign: 'center' }}>6-Month Trend</div>
                    <MiniChart data={feature.chart_data} height={160} />
            </div>
        </div>
    );
};
