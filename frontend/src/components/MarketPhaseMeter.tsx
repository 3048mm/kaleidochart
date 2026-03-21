import React from 'react';
import { appConfig } from '../config';

interface MarketPhaseMeterProps {
    phase: string;
}

export const MarketPhaseMeter: React.FC<MarketPhaseMeterProps> = ({ phase }) => {
    // Expected phases: "BULL", "CORRECTION", "RALLY_ATTEMPT", "BEAR"
    const phases = [
        { id: 'BEAR', label: 'Bear Market' },
        { id: 'RALLY_ATTEMPT', label: 'Rally Attempt' },
        { id: 'CORRECTION', label: 'Correction' },
        { id: 'BULL', label: 'Confirmed Uptrend' }
    ];

    const currentIndex = phases.findIndex(p => p.id === phase);
    const activeIndex = currentIndex === -1 ? 0 : currentIndex; // Default BEAR if unknown

    // Color gradient representing safety: Red -> Orange -> Yellow -> Green
    const colors = [
        appConfig.colors.bad, // BEAR
        '#f59e0b', // RALLY_ATTEMPT (Amber)
        '#facc15', // CORRECTION (Yellow)
        appConfig.colors.good // BULL
    ];

    return (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            background: 'rgba(0,0,0,0.2)',
            padding: '12px',
            borderRadius: '8px',
            border: `1px solid ${appConfig.colors.glassBorder}`
        }}>
            <div style={{ fontSize: '11px', color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Market Trend Phase
            </div>
            
            <div style={{ display: 'flex', gap: '4px', height: '12px' }}>
                {phases.map((p, index) => {
                    const isActive = index === activeIndex;
                    const isPast = index <= activeIndex;
                    // If past or active, use the color of the *current active phase*, else dim.
                    // Or we could have each segment colored uniquely when reached. Let's color by the active index.
                    const bgColor = isPast ? colors[activeIndex] : 'rgba(255, 255, 255, 0.1)';
                    
                    return (
                        <div
                            key={p.id}
                            style={{
                                flex: 1,
                                backgroundColor: bgColor,
                                borderRadius: '2px',
                                opacity: isActive ? 1 : isPast ? 0.5 : 1,
                                transition: 'all 0.3s ease',
                                boxShadow: isActive ? `0 0 8px ${bgColor}` : 'none'
                            }}
                        />
                    );
                })}
            </div>
            
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                {phases.map((p, index) => {
                    const isActive = index === activeIndex;
                    return (
                        <div
                            key={`lbl-${p.id}`}
                            style={{
                                flex: 1,
                                textAlign: 'center',
                                fontSize: '10px',
                                fontWeight: isActive ? 'bold' : 'normal',
                                color: isActive ? '#fff' : '#666',
                                marginTop: '4px'
                            }}
                        >
                            {p.label}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};
