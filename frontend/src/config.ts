// frontend/src/config.ts

export const appConfig = {
    // Colors matching the dark glassmorphism glass UI
    colors: {
        good: '#00ff88',       // Bright cyan/green for positive performance
        bad: '#ff4444',        // Bright red for negative performance
        neutral: '#aaaaaa',    // Grey for neutral
        chartText: '#d1d4dc',  // Base text color for charts
        chartLine: '#2962FF',  // Base line color
        glassBorder: 'rgba(255, 255, 255, 0.1)',
        glassBg: 'rgba(25, 25, 30, 0.65)',
        accent: '#2962FF',
        star: '#FFC107'
    },

    // Thresholds for alerts and rendering
    thresholds: {
        atr_multiple_yellow: 8,       // sma50_atr_mult >= 8 (Caution)
        atr_multiple_red: 10,         // sma50_atr_mult >= 10 (Danger)
        sparkline_max_pct_sector: 4,  // Maximum absolute 1D% for full color intensity in Sector List
        sparkline_max_pct_theme: 8,   // Maximum absolute 1D% for full color intensity in Theme List
        sparkline_max_pct_index: 4,   // Maximum absolute 1D% for Indices List
    }
};

/**
 * Utility to calculate rgba color with opacity based on a percentage map
 * @param percent The absolute percentage variation
 * @param maxPercent The threshold for max opacity (1.0)
 * @param baseColor Hex color string (e.g. '#00ff88')
 * @param minOpacity Minimum opacity when percent is near 0
 * @returns rgba string
 */
export function getIntensityColor(percent: number | null | undefined, maxPercent: number, baseColor: string, minOpacity: number = 0.2): string {
    if (percent == null) return 'rgba(128, 128, 128, 0.3)';

    // Parse Hex to RGB
    const hex = baseColor.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);

    // Calculate Opacity
    let opacity = (Math.abs(percent) / maxPercent);
    if (opacity > 1) opacity = 1;
    if (opacity < minOpacity) opacity = minOpacity;

    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}
