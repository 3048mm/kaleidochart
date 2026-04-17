import React from 'react';
import { appConfig } from '../config';

interface WatchlistButtonProps {
    isActive: boolean;
    onClick: (e: React.MouseEvent) => void;
    size?: number;
    loading?: boolean;
}

export const WatchlistButton: React.FC<WatchlistButtonProps> = ({ 
    isActive, 
    onClick, 
    size = 20,
    loading = false 
}) => {
    return (
        <button
            onClick={onClick}
            disabled={loading}
            style={{
                background: 'transparent',
                border: 'none',
                cursor: loading ? 'default' : 'pointer',
                padding: '4px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: isActive ? appConfig.colors.star : 'rgba(255,255,255,0.2)',
                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                outline: 'none',
                transform: isActive ? 'scale(1.1)' : 'scale(1)',
            }}
            title={isActive ? "Remove from watchlist" : "Add to watchlist"}
            onMouseEnter={e => {
                if (!isActive && !loading) {
                    e.currentTarget.style.color = 'rgba(255,255,255,0.4)';
                    e.currentTarget.style.transform = 'scale(1.1)';
                }
            }}
            onMouseLeave={e => {
                if (!isActive) {
                    e.currentTarget.style.color = 'rgba(255,255,255,0.2)';
                    e.currentTarget.style.transform = 'scale(1)';
                } else {
                    e.currentTarget.style.transform = 'scale(1.1)';
                }
            }}
        >
            <svg
                width={size}
                height={size}
                viewBox="0 0 24 24"
                fill={isActive ? "currentColor" : "none"}
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
            >
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
        </button>
    );
};
