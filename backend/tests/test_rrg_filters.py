import pytest
import pandas as pd
import numpy as np
from datetime import date
from backend.backtest.backtest_screener import scan_signals_for_date

@pytest.fixture
def base_data():
    """RRGテスト用の共通モックデータを作成"""
    target_date = date(2026, 5, 4)
    prev_date = date(2026, 5, 3)
    
    # Symbols: 1: Leading Success, 2: Low Intensity, 3: Decelerating, 4: Improving Success, 5: Falling from Leading
    df_symbols = pd.DataFrame([
        {"id": 1, "ticker": "LEAD", "name": "Leading", "category": "個別", "active": 1},
        {"id": 2, "ticker": "WEAK_INT", "name": "Weak Intensity", "category": "個別", "active": 1},
        {"id": 3, "ticker": "DECEL", "name": "Decelerating", "category": "個別", "active": 1},
        {"id": 4, "ticker": "IMPROV", "name": "Improving", "category": "個別", "active": 1},
        {"id": 5, "ticker": "FALL", "name": "Falling", "category": "個別", "active": 1},
    ])
    
    # Prices: 最小限
    price_data = []
    for sid in df_symbols["id"]:
        for d in [prev_date, target_date]:
            price_data.append({"symbol_id": sid, "date": d, "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000, "market_cap": 1e9})
    df_price = pd.DataFrame(price_data)
    
    # Indicators: RRG値のメイン
    ind_data = [
        # 1: Leading Success (Prev: Improving, Today: Leading + Accel + Intense)
        {"symbol_id": 1, "date": prev_date,   "rs_ratio_21": -0.1, "rs_momentum_21": 0.6, "ema_21": 90},
        {"symbol_id": 1, "date": target_date, "rs_ratio_21": 0.3,  "rs_momentum_21": 0.7, "ema_21": 90},
        
        # 2: Low Intensity (Prev: Improving, Today: Leading + Accel + BUT LOW INTENSITY 0.2 < 0.5)
        {"symbol_id": 2, "date": prev_date,   "rs_ratio_21": -0.1, "rs_momentum_21": 0.1, "ema_21": 90},
        {"symbol_id": 2, "date": target_date, "rs_ratio_21": 0.1,  "rs_momentum_21": 0.15, "ema_21": 90}, # dist = sqrt(0.1^2 + 0.15^2) = 0.18
        
        # 3: Decelerating (Prev: Improving, Today: Leading + Intense BUT MOMENTUM FALLING)
        {"symbol_id": 3, "date": prev_date,   "rs_ratio_21": -0.1, "rs_momentum_21": 1.0, "ema_21": 90},
        {"symbol_id": 3, "date": target_date, "rs_ratio_21": 0.5,  "rs_momentum_21": 0.9, "ema_21": 90},
        
        # 4: Improving Success (Prev: Lagging, Today: Improving + Accel + Intense)
        {"symbol_id": 4, "date": prev_date,   "rs_ratio_21": -0.5, "rs_momentum_21": -0.1, "ema_21": 90},
        {"symbol_id": 4, "date": target_date, "rs_ratio_21": -0.3, "rs_momentum_21": 0.6, "ema_21": 90},
        
        # 5: Falling from Leading (Prev: Leading, Today: Improving) -> SHOULD BE REJECTED for improving_in
        {"symbol_id": 5, "date": prev_date,   "rs_ratio_21": 0.5,  "rs_momentum_21": 0.6, "ema_21": 90},
        {"symbol_id": 5, "date": target_date, "rs_ratio_21": -0.1, "rs_momentum_21": 0.7, "ema_21": 90},
    ]
    df_ind = pd.DataFrame(ind_data)
    # 欠損カラム補完
    for col in ['change_1d_pct', 'change_1w_pct', 'change_1m_pct', 'dist_sma50_atr', 'vol_surge_21', 'rel_vol_vs_spy_21', 'rs_condition_21', 'trend_template_ok']:
        df_ind[col] = 1.0
        
    df_ranks = pd.DataFrame(columns=["symbol_id", "date", "indicator_name", "percent_rank"])
    df_theme_constituents = pd.DataFrame(columns=["theme_id", "symbol_id"])
    
    return {
        "target_date": target_date,
        "prev_date": prev_date,
        "df_ind": df_ind,
        "df_price": df_price,
        "df_ranks": df_ranks,
        "df_symbols": df_symbols,
        "df_theme_constituents": df_theme_constituents
    }

def test_rrg_leading_in_refined(base_data):
    """改良版 rrg_leading_in のテスト"""
    strategy = {
        "name": "test",
        "rrg_leading_in": True,
        "rrg_intensity_threshold": 0.5
    }
    
    signals = scan_signals_for_date(
        base_data["target_date"],
        base_data["df_ind"],
        base_data["df_price"],
        base_data["df_ranks"],
        base_data["df_symbols"],
        base_data["df_theme_constituents"],
        strategy,
        prev_date=base_data["prev_date"]
    )
    
    tickers = [s.ticker for s in signals]
    # LEAD (id: 1) だけが合格するはず
    assert "LEAD" in tickers
    assert "WEAK_INT" not in tickers
    assert "DECEL" not in tickers
    assert "IMPROV" not in tickers
    assert "FALL" not in tickers

def test_rrg_improving_in_refined(base_data):
    """改良版 rrg_improving_in のテスト"""
    strategy = {
        "name": "test",
        "rrg_improving_in": True,
        "rrg_intensity_threshold": 0.5
    }
    
    signals = scan_signals_for_date(
        base_data["target_date"],
        base_data["df_ind"],
        base_data["df_price"],
        base_data["df_ranks"],
        base_data["df_symbols"],
        base_data["df_theme_constituents"],
        strategy,
        prev_date=base_data["prev_date"]
    )
    
    tickers = [s.ticker for s in signals]
    # IMPROV (id: 4) だけが合格するはず
    # FALL (id: 5) は Leading から落ちてきたので不合格
    assert "IMPROV" in tickers
    assert "FALL" not in tickers
    assert "LEAD" not in tickers
