import pytest
import pandas as pd
from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig
from backend.backtest.scenario_market_score import MarketPhase

def test_portfolio_config_has_regime_model():
    # PortfolioConfig must support regime_model attribute
    config = PortfolioConfig(regime_model="spy_sma200")
    assert config.regime_model == "spy_sma200"

def test_regime_spy_sma200():
    # Test SPY SMA200 regime allocation switches
    # Buffer rules: buy at > 1.05 * SMA200, sell at < 0.97 * SMA200, otherwise maintain previous
    config = PortfolioConfig(regime_model="spy_sma200", max_positions=8)
    portfolio = ScenarioPortfolio(config)
    portfolio.use_hysteresis = True
    
    # 1. Start: Initial default max_positions is config.max_positions (8)
    assert portfolio.dynamic_max_positions == 8
    
    # 2. BEAR Trigger: SPY = 90, SMA200 = 100 (90 < 97) -> should cash out (0)
    portfolio.update_regime(
        date="2026-01-01",
        spy_close=90.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 0
    
    # 3. NEUTRAL: SPY = 100, SMA200 = 100 (97 <= 100 <= 105) -> should maintain previous (0)
    portfolio.update_regime(
        date="2026-01-02",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 0
    
    # 4. BULL Trigger: SPY = 106, SMA200 = 100 (106 > 105) -> should invest (8)
    portfolio.update_regime(
        date="2026-01-03",
        spy_close=106.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 8

    # 5. NEUTRAL (downward): SPY = 100, SMA200 = 100 -> should maintain previous (8)
    portfolio.update_regime(
        date="2026-01-04",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 8

def test_regime_spy_sma63():
    # Test SPY SMA63 regime allocation switches
    # Buffer rules: buy at > 1.05 * SMA63, sell at < 0.97 * SMA63, otherwise maintain previous
    config = PortfolioConfig(regime_model="spy_sma63", max_positions=8)
    portfolio = ScenarioPortfolio(config)
    portfolio.use_hysteresis = True
    
    assert portfolio.dynamic_max_positions == 8
    
    # BEAR Trigger
    portfolio.update_regime(
        date="2026-01-01",
        spy_close=90.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 0
    
    # NEUTRAL (maintain BEAR)
    portfolio.update_regime(
        date="2026-01-02",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 0
    
    # BULL Trigger
    portfolio.update_regime(
        date="2026-01-03",
        spy_close=106.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 8

def test_regime_vxv_vix_ema():
    # Test VXV/VIX ratio EMA (4-regime model)
    # ema5 < 1.00 -> BOTTOM (2 slots)
    # ema5 > 1.20 -> OVERHEAT (4 slots)
    # ema5 > ema21 -> BULL (8 slots)
    # otherwise -> BEAR (0 slots)
    config = PortfolioConfig(regime_model="vxv_vix_ema", max_positions=8)
    portfolio = ScenarioPortfolio(config)
    portfolio.use_hysteresis = True
    
    # Needs 21 days of history to calculate EMA.
    # We populate the history to simulate this.
    portfolio.vxv_vix_history = [1.10] * 21
    
    # 1. VXV/VIX = 1.10 (stable BULL)
    portfolio.update_regime(
        date="2026-01-01",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 8
    
    # 2. VXV/VIX drops below 1.00 (BOTTOM)
    # We shift history to force a low EMA5
    portfolio.vxv_vix_history = [1.10] * 15 + [0.90] * 6
    portfolio.update_regime(
        date="2026-01-02",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=0.90,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 2
    
    # 3. VXV/VIX climbs to OVERHEAT (> 1.20)
    portfolio.vxv_vix_history = [1.10] * 15 + [1.25] * 6
    portfolio.update_regime(
        date="2026-01-03",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.25,
        mts_score=50.0
    )
    assert portfolio.dynamic_max_positions == 4

def test_regime_mts_raw():
    # Test MTS raw regime model (default multi-stage hysteresis)
    config = PortfolioConfig(regime_model="mts_raw", max_positions=8)
    portfolio = ScenarioPortfolio(config)
    portfolio.use_hysteresis = True
    
    # Initial score
    portfolio.current_market_score = 50.0
    
    # score going downward to 15.0 -> BEAR (0 slots)
    portfolio.update_regime(
        date="2026-01-01",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=15.0
    )
    assert portfolio.dynamic_max_positions == 0
    
    # score going upward to 85.0 -> BULL (8 slots)
    portfolio.update_regime(
        date="2026-01-02",
        spy_close=100.0,
        spy_sma200=100.0,
        spy_sma63=100.0,
        vxv_vix_ratio=1.10,
        mts_score=85.0
    )
    assert portfolio.dynamic_max_positions == 8
