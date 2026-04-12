// frontend/src/components/Sparkline.tsx
import React from 'react';

interface SparklineProps {
    data: number[]; // Array of values
    width?: number;
    height?: number;
    color?: string;
    strokeWidth?: number;
    fixedRange?: boolean; // If true, use 0-1 fixed range (no auto-scale)
}

export const Sparkline: React.FC<SparklineProps> = ({
    data,
    width = 100,
    height = 30,
    color = '#2962FF',
    strokeWidth = 1.5,
    fixedRange = false,
}) => {
    if (!data || data.length === 0) {
        return <svg width={width} height={height} />;
    }

    const maxVal = fixedRange ? 1 : Math.max(...data);
    const minVal = fixedRange ? 0 : Math.min(...data);
    const range = maxVal - minVal;

    // Build SVG path
    const pathData = data.map((val, i) => {
        const x = (i / (data.length - 1)) * width;
        // SVG y-axis is inverted (0 is top)
        const clamped = fixedRange ? Math.max(0, Math.min(1, val)) : val;
        const normalizedY = range === 0 ? 0.5 : (clamped - minVal) / range;
        const y = height - (normalizedY * height);

        return `${i === 0 ? 'M' : 'L'} ${x} ${y}`;
    }).join(' ');

    return (
        <svg width={width} height={height} style={{ overflow: 'visible' }}>
            {/* Center reference line */}
            <line 
                x1={0} 
                y1={height / 2} 
                x2={width} 
                y2={height / 2} 
                stroke="rgba(255, 255, 255, 0.6)" 
                strokeWidth={1} 
                strokeDasharray="3,3"
            />
            <path
                d={pathData}
                fill="none"
                stroke={color}
                strokeWidth={strokeWidth}
                strokeLinecap="round"
                strokeLinejoin="round"
            />
        </svg>
    );
};
