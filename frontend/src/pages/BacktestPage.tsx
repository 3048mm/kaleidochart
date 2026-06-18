import React, { useState } from 'react';
import { EtfSingleBacktestPage } from './EtfSingleBacktestPage';
import { BacktestResultPage } from './BacktestResultPage';
import { appConfig } from '../config';

export const BacktestPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'etf' | 'scenario'>('etf');

  const containerStyle: React.CSSProperties = {
    padding: '20px',
    width: '100%',
    maxWidth: '1200px',
    margin: '0 auto',
    boxSizing: 'border-box',
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
  };

  const headerStyle: React.CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '10px',
    flexWrap: 'wrap',
    gap: '15px',
  };

  const tabsStyle: React.CSSProperties = {
    display: 'flex',
    borderBottom: `1px solid ${appConfig.colors.glassBorder}`,
    marginBottom: '10px',
  };

  return (
    <div className="dashboard-page" style={containerStyle}>
      {/* Header */}
      <div style={headerStyle}>
        <div>
          <h1 style={{ 
            margin: 0, 
            background: 'linear-gradient(135deg, #3b82f6, #22d3a0)', 
            WebkitBackgroundClip: 'text', 
            WebkitTextFillColor: 'transparent', 
            backgroundClip: 'text', 
            fontWeight: 'bold', 
            fontSize: '24px' 
          }}>
            📊 Backtest Dashboard
          </h1>
          <span style={{ fontSize: '11px', color: '#8b9cc8' }}>
            Regime-based ETF single strategy vs Broad scenario-based stock strategy
          </span>
        </div>
      </div>

      {/* Tabs */}
      <div className="dashboard-tabs" style={tabsStyle}>
        {[
          { id: 'etf', label: '📈 ETF Backtest' },
          { id: 'scenario', label: '🧭 Scenario Test' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as 'etf' | 'scenario')}
            style={{
              flex: 1,
              padding: '12px 20px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === tab.id ? `3px solid ${appConfig.colors.accent}` : '3px solid transparent',
              color: activeTab === tab.id ? '#fff' : '#aaa',
              fontSize: '15px',
              fontWeight: 'bold',
              cursor: 'pointer',
              transition: 'all 0.2s',
              outline: 'none',
            }}
            onMouseEnter={(e) => {
              if (activeTab !== tab.id) {
                (e.target as HTMLButtonElement).style.color = '#fff';
                (e.target as HTMLButtonElement).style.backgroundColor = 'rgba(255,255,255,0.05)';
              }
            }}
            onMouseLeave={(e) => {
              if (activeTab !== tab.id) {
                (e.target as HTMLButtonElement).style.color = '#aaa';
                (e.target as HTMLButtonElement).style.backgroundColor = 'transparent';
              }
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content Rendering */}
      <div>
        {activeTab === 'etf' && <EtfSingleBacktestPage hideHeader={true} />}
        {activeTab === 'scenario' && <BacktestResultPage hideHeader={true} />}
      </div>
    </div>
  );
};
