"""
Tests for strategy_normalizer — filter key alias normalization layer.

Verifies that legacy TOML parameter names are correctly converted to
canonical names before being consumed by backtest_screener / screener_router.
"""
import pytest
from backend.backtest.strategy_normalizer import normalize_strategy_keys


class TestNormalizeStrategyKeys:
    """Phase 1: Core normalization function behavior."""

    def test_legacy_change_oc_pct_is_normalized(self):
        """min_change_oc_pct → min_change_intraday_pct"""
        strategy = {"name": "A", "min_change_oc_pct": 7.0}
        result = normalize_strategy_keys(strategy)
        assert "min_change_intraday_pct" in result
        assert "min_change_oc_pct" not in result
        assert result["min_change_intraday_pct"] == 7.0

    def test_legacy_dist_ema21_pct_is_normalized(self):
        """min_dist_ema21_pct → min_dist_21ema_pct"""
        strategy = {"name": "B3", "min_dist_ema21_pct": 1.0, "max_dist_ema21_pct": 20.0}
        result = normalize_strategy_keys(strategy)
        assert "min_dist_21ema_pct" in result
        assert "max_dist_21ema_pct" in result
        assert "min_dist_ema21_pct" not in result
        assert "max_dist_ema21_pct" not in result
        assert result["min_dist_21ema_pct"] == 1.0
        assert result["max_dist_21ema_pct"] == 20.0

    def test_legacy_rs_ratio_rank_old_naming(self):
        """min_rs_ratio_21_rank → min_rs_ratio_rank_e21"""
        strategy = {"min_rs_ratio_21_rank": 0.5}
        result = normalize_strategy_keys(strategy)
        assert "min_rs_ratio_rank_e21" in result
        assert "min_rs_ratio_21_rank" not in result

    def test_legacy_rs_ratio_63_rank(self):
        """min_rs_ratio_63_rank → min_rs_ratio_rank_e63"""
        strategy = {"min_rs_ratio_63_rank": 0.7}
        result = normalize_strategy_keys(strategy)
        assert "min_rs_ratio_rank_e63" in result
        assert "min_rs_ratio_63_rank" not in result

    def test_legacy_boolean_rs_rank_comparison(self):
        """rs_rank_21_gt_63 → is_rs_ratio_rank_e21_gt_e63"""
        strategy = {"rs_rank_21_gt_63": True}
        result = normalize_strategy_keys(strategy)
        assert "is_rs_ratio_rank_e21_gt_e63" in result
        assert "rs_rank_21_gt_63" not in result
        assert result["is_rs_ratio_rank_e21_gt_e63"] is True

    def test_legacy_rs_rank_14_gt_21(self):
        """rs_rank_14_gt_21 → is_rs_ratio_rank_e14_gt_e21"""
        strategy = {"rs_rank_14_gt_21": True}
        result = normalize_strategy_keys(strategy)
        assert "is_rs_ratio_rank_e14_gt_e21" in result
        assert "rs_rank_14_gt_21" not in result

    def test_legacy_theme_rs_ratio_comparisons(self):
        """theme_rs21_gt_63, theme_rs_rank_21_gt_63, etc."""
        strategy = {
            "theme_rs21_gt_63": True,
            "theme_rs_rank_21_gt_63": True,
            "theme_rs14_gt_21": True,
            "theme_rs_rank_14_gt_21": True,
        }
        result = normalize_strategy_keys(strategy)
        assert "is_theme_rs_ratio_e21_gt_e63" in result
        assert "is_theme_rs_ratio_rank_e21_gt_e63" in result
        assert "is_theme_rs_ratio_e14_gt_e21" in result
        assert "is_theme_rs_ratio_rank_e14_gt_e21" in result
        # All old names gone
        for old_key in strategy:
            assert old_key not in result

    def test_legacy_rs_condition_rank(self):
        """min_rs_condition_14_rank → min_rs_trend_rank_s14, etc."""
        strategy = {
            "min_rs_condition_14_rank": 0.5,
            "min_rs_condition_21_rank": 0.6,
            "min_rs_condition_63_rank": 0.7,
        }
        result = normalize_strategy_keys(strategy)
        assert "min_rs_trend_rank_s14" in result
        assert "min_rs_trend_rank_s21" in result
        assert "min_rs_trend_rank_s63" in result
        for old_key in strategy:
            assert old_key not in result

    def test_legacy_theme_rs_condition_rank(self):
        """min_theme_rs_condition_14_rank → min_theme_rs_trend_rank_s14, etc."""
        strategy = {
            "min_theme_rs_condition_14_rank": 0.5,
            "min_theme_rs_condition_21_rank": 0.6,
            "min_theme_rs_condition_63_rank": 0.7,
        }
        result = normalize_strategy_keys(strategy)
        assert "min_theme_rs_trend_rank_s14" in result
        assert "min_theme_rs_trend_rank_s21" in result
        assert "min_theme_rs_trend_rank_s63" in result

    def test_legacy_condition_gt_comparisons(self):
        """rs_condition_14_gt_21 → is_rs_trend_rank_s14_gt_s21, etc."""
        strategy = {
            "rs_condition_14_gt_21": True,
            "rs_condition_21_gt_63": True,
            "theme_rs_condition_14_gt_21": True,
            "theme_rs_condition_21_gt_63": True,
        }
        result = normalize_strategy_keys(strategy)
        assert "is_rs_trend_rank_s14_gt_s21" in result
        assert "is_rs_trend_rank_s21_gt_s63" in result
        assert "is_theme_rs_trend_rank_s14_gt_s21" in result
        assert "is_theme_rs_trend_rank_s21_gt_s63" in result

    def test_canonical_keys_pass_through(self):
        """New/canonical keys must not be modified."""
        strategy = {
            "name": "B1",
            "min_change_1d_pct": 7.0,
            "is_rs_ratio_rank_e21_gt_e63": True,
            "min_rs_ratio_rank_e21": 0.8,
            "min_theme_rs_ratio_rank_e21": 0.6,
            "is_close_gt_ema63": True,
            "min_vol_surge_21": 2.0,
        }
        result = normalize_strategy_keys(strategy)
        assert result == strategy

    def test_unknown_keys_pass_through(self):
        """Keys not in the alias table must not be changed."""
        strategy = {
            "name": "custom",
            "some_custom_filter": 42,
            "is_trend_template": True,
        }
        result = normalize_strategy_keys(strategy)
        assert result == strategy

    def test_values_are_not_modified(self):
        """Only keys are transformed, values must remain identical."""
        strategy = {"min_change_oc_pct": 7.5, "rs_rank_21_gt_63": True}
        result = normalize_strategy_keys(strategy)
        assert result["min_change_intraday_pct"] == 7.5
        assert result["is_rs_ratio_rank_e21_gt_e63"] is True

    def test_conflict_new_name_wins(self):
        """If both old and new name exist, new name's value wins with a warning."""
        strategy = {
            "min_dist_ema21_pct": 1.0,   # old
            "min_dist_21ema_pct": 5.0,   # new (canonical)
        }
        result = normalize_strategy_keys(strategy)
        assert result["min_dist_21ema_pct"] == 5.0
        assert "min_dist_ema21_pct" not in result

    def test_optimization_section_is_recursively_normalized(self):
        """Keys inside strategy['optimization'] dict must also be normalized."""
        strategy = {
            "name": "B3",
            "min_dist_ema21_pct": 1.0,
            "optimization": {
                "min_dist_ema21_pct": {"type": "float", "min": -1.0, "max": 20.0, "step": 1.0},
                "min_change_oc_pct": {"type": "float", "min": -8.0, "max": 8.0, "step": 1.0},
                "min_vol_surge_21": {"type": "float", "min": 0.5, "max": 3.0, "step": 0.1},
            }
        }
        result = normalize_strategy_keys(strategy)
        opt = result["optimization"]
        assert "min_dist_21ema_pct" in opt
        assert "min_dist_ema21_pct" not in opt
        assert "min_change_intraday_pct" in opt
        assert "min_change_oc_pct" not in opt
        # Non-aliased keys pass through
        assert "min_vol_surge_21" in opt

    def test_does_not_mutate_input(self):
        """The original strategy dict must not be modified."""
        strategy = {"min_change_oc_pct": 7.0}
        original_copy = dict(strategy)
        normalize_strategy_keys(strategy)
        assert strategy == original_copy
