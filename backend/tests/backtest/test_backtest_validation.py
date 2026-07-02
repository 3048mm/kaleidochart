import pytest
import pandas as pd
from backend.backtest.backtest_runner import validate_strategies_config

def test_validate_strategies_config_valid():
    # Valid config
    strategies = [
        {
            "name": "valid_strategy",
            "max_hits_per_day": 10,
            "min_vol_surge_21": 3.0,
            "is_close_gt_ema63": True,
            "close_gt_sma200": True,
        }
    ]
    
    df_ind = pd.DataFrame(columns=['vol_surge_21', 'ema_63', 'sma_200'])
    df_prices = pd.DataFrame(columns=['close'])
    
    warnings = validate_strategies_config(strategies, df_ind, df_prices, None, None)
    assert len(warnings) == 0

def test_validate_strategies_config_invalid():
    # Invalid config with typos
    strategies = [
        {
            "name": "invalid_strategy",
            "min_rs_trend_e21": 0.6, # Invalid, should be rs_trend_s21
            "is_theme_rs_ratio_rank_e14_gt_e21_typo": True, # Invalid typo
            "close_gt_sma999": True, # Invalid SMA column
        }
    ]
    
    df_ind = pd.DataFrame(columns=['rs_trend_s21'])
    df_prices = pd.DataFrame(columns=['close'])
    
    warnings = validate_strategies_config(strategies, df_ind, df_prices, None, None)
    assert len(warnings) == 3
    assert any("min_rs_trend_e21" in w for w in warnings)
    assert any("is_theme_rs_ratio_rank_e14_gt_e21_typo" in w for w in warnings)
    assert any("close_gt_sma999" in w for w in warnings)
