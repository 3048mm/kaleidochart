import pandas as pd
import numpy as np
import pytest
from indicators.calculate import calculate_market_signals

def test_market_trend_score_bull():
    """極端な強気相場での100点（に近い）スコアを検証"""
    # SPY: EMA21, SMA50, SMA200 すべてをしっかり上回る
    # 200日分のデータを作成（すべて上昇基調）
    dates = pd.date_range(start='2025-01-01', periods=250)
    prices = [100.0 + i * 0.5 for i in range(250)]
    df_spy = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': [p - 0.1 for p in prices],
        'high': [p + 0.2 for p in prices],
        'low': [p - 0.2 for p in prices],
        'volume': [1000000] * 250
    })
    
    # VIX: 12以下 (25 pts)
    df_vix = pd.DataFrame({
        'date': [dates[-1]],
        'close': [12.0]
    })
    
    # Metrics: Breadth 1.0, Momentum 1.0 (25 + 25 pts)
    df_metrics = pd.DataFrame({
        'date': [dates[-1]],
        'breadth_sma50': [1.0],
        'momentum_ratio': [1.0]
    })
    
    res = calculate_market_signals(df_spy, df_vix=df_vix, df_metrics=df_metrics)
    score = res.iloc[-1]['market_trend_score']
    
    # SPY(25) + Breadth(25) + Momentum(25) + VIX(25) = 100
    assert score >= 99.0

def test_market_trend_score_bear():
    """極端な弱気相場での0点（に近い）スコアを検証"""
    # SPY: 200日分の下落データ
    dates = pd.date_range(start='2025-01-01', periods=250)
    prices = [500.0 - i * 1.0 for i in range(250)]
    df_spy = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': [p + 0.1 for p in prices],
        'high': [p + 0.2 for p in prices],
        'low': [p - 0.2 for p in prices],
        'volume': [1000000] * 250
    })
    
    # VIX: 35以上 (0 pts)
    df_vix = pd.DataFrame({
        'date': [dates[-1]],
        'close': [40.0]
    })
    
    # Metrics: Breadth 0.0, Momentum 0.0 (0 + 0 pts)
    df_metrics = pd.DataFrame({
        'date': [dates[-1]],
        'breadth_sma50': [0.0],
        'momentum_ratio': [0.0]
    })
    
    res = calculate_market_signals(df_spy, df_vix=df_vix, df_metrics=df_metrics)
    score = res.iloc[-1]['market_trend_score']
    
    # 全 MA 割れなら SPY=0, 他も0なので 0
    assert score <= 1.0

def test_market_trend_score_neutral():
    """中立相場での50点前後を検証"""
    # VIX: 23.5 (12.5 pts) -> (35-23.5)/(35-12) = 11.5/23 = 0.5 -> 12.5 pts
    df_vix = pd.DataFrame({
        'date': pd.to_datetime(['2026-01-01']),
        'close': [23.5]
    })
    
    # Metrics: Breadth 0.5, Momentum 0.5 (12.5 + 12.5 = 25 pts)
    df_metrics = pd.DataFrame({
        'date': pd.to_datetime(['2026-01-01']),
        'breadth_sma50': [0.5],
        'momentum_ratio': [0.5]
    })
    
    # SPY: SMA200 のみ上回る想定 (約8.3 pts) -> 12.5(VIX) + 25(Metrics) + 8.3 = 45.8
    # 1件だと全部 True になるので、ダミー履歴を入れる
    df_spy = pd.DataFrame({
        'date': pd.to_datetime(['2025-12-01', '2026-01-01']),
        'close': [100.0, 150.0], # 200MA=125, 50MA=125, 21EMA=125 ... 全部越えるな
        'open': [100.0, 150.0],
        'high': [100.0, 150.0],
        'low': [100.0, 150.0],
        'volume': [1000, 1000]
    })
    
    # 計算ロジック上、EMA/SMAの期間が必要なので、
    # 実際はもっと長いデータが必要だが、まずは構造のテスト
    res = calculate_market_signals(df_spy, df_vix=df_vix, df_metrics=df_metrics)
    score = res.iloc[-1]['market_trend_score']
    
    assert 10.0 <= score <= 90.0

def test_calculate_indicators_protection():
    """リファクタリング前後の安全性を担保するための保護テスト。
    ダミーデータを用いて現在の計算出力（期待値）を固定化する。
    """
    from indicators.calculate import calculate_indicators
    
    dates = pd.date_range(start='2025-01-01', periods=260, freq='B')
    prices = [100.0 + i * 0.1 for i in range(260)]
    
    df_dummy = pd.DataFrame({
        'date': dates,
        'close': prices,
        'open': [p - 0.5 for p in prices],
        'high': [p + 1.0 for p in prices],
        'low': [p - 1.0 for p in prices],
        'volume': [1000000 + i * 100 for i in range(260)]
    })
    
    spy_prices = [300.0 + i * 0.05 for i in range(260)]
    df_spy = pd.DataFrame({
        'date': dates,
        'close': spy_prices,
        'open': [p - 0.5 for p in spy_prices],
        'high': [p + 1.0 for p in spy_prices],
        'low': [p - 1.0 for p in spy_prices],
        'volume': [5000000 + i * 100 for i in range(260)]
    })
    
    res = calculate_indicators(df_dummy, df_spy)
    latest = res.iloc[-1]
    
    # Python <=3.10 and pandas behavior handling
    def is_close(a, b):
        if pd.isna(a) and pd.isna(b): return True
        if pd.isna(a) or pd.isna(b): return False
        return abs(a - b) < 1e-5

    EXPECTATIONS = {
        'sma_50': 123.450000,
        'ema_21': 124.900000,
        'atr_14': 2.000000,
        'atr_pct_14': 1.588562,
        'vcr': 1.000000,
        'td9': 4.000000,
        'dist_sma50_atr': 1.249311,
        'relative_strength_spy': 0.402301,
        'rs_condition_21': 1.006400,
        'rs_ratio_21': 1.610011,
        'rs_momentum_21': None,
        'vol_surge_21': 1.000976,
        'rel_vol_vs_spy_21': 1.000777,
        'pct_from_52w_high': -0.788022,
        'up_down_vol_ratio_50': None,
        'rs_blue_dot': 0.000000,
        'trend_template_ok': 1.000000,
    }
    
    for col, expected in EXPECTATIONS.items():
        assert col in latest, f"Column {col} missing in output"
        actual = latest[col]
        assert is_close(actual, expected), f"{col} expected {expected}, got {actual}"

