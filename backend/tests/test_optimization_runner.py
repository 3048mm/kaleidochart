"""
test_optimization_runner.py — TDD tests for TOML-based optimization parameter parsing.

These tests verify that optimization_runner.py correctly reads search space
definitions from backtest_config.toml and converts them to Optuna trial parameters.
"""

import pytest
from unittest.mock import MagicMock

# Functions to be implemented in optimization_runner.py
from optimization_runner import (
    parse_optimization_params,
    apply_trial_params,
    parse_optimization_periods,
    calculate_prune_penalty,
)


# ============================================================
# Fixtures: mock TOML config dicts
# ============================================================

@pytest.fixture
def sample_config():
    """Minimal config dict simulating parsed backtest_config.toml."""
    return {
        "strategy": [
            {
                "name": "B_theme_momentum",
                "description": "Strong theme momentum",
                "min_1d_gain_pct": 2.0,
                "min_market_cap": 1e8,
            },
            {
                "name": "D_ema21_pullback",
                "description": "EMA21 pullback",
                "min_dist_21ema_pct": -2.0,
                "max_dist_21ema_pct": 2.0,
                "min_market_cap": 1e9,
                "trend_template_ok": 1,
            },
        ],
        "optimization": {
            "B": {
                "min_1d_gain_pct": {"type": "float", "min": 1.0, "max": 5.0, "step": 0.5},
                "min_market_cap": {"type": "categorical", "choices": [1e7, 1e8, 5e8]},
            },
            "D": {
                "min_dist_21ema_pct": {"type": "float", "min": -4.0, "max": -1.0, "step": 0.5},
                "max_dist_21ema_pct": {"type": "float", "min": 0.5, "max": 4.0, "step": 0.5},
                "min_market_cap": {"type": "categorical", "choices": [1e8, 5e8, 1e9]},
                "trend_template_ok": {"type": "categorical", "choices": [1]},
            },
        },
        "optimization_periods": {
            "periods": [
                {"start": "2022-01-01", "end": "2022-12-31", "label": "Bear 2022"},
                {"start": "2024-06-01", "end": "2025-12-31", "label": "Bull 2024-25"},
            ]
        },
    }


@pytest.fixture
def config_with_int():
    """Config with an int-type parameter for testing."""
    return {
        "strategy": [
            {"name": "X_test", "some_flag": 0},
        ],
        "optimization": {
            "X": {
                "some_flag": {"type": "int", "min": 0, "max": 10, "step": 2},
            },
        },
    }


@pytest.fixture
def config_with_none():
    """Config with categorical containing 'none' (maps to Python None)."""
    return {
        "strategy": [
            {"name": "F_elite_momentum97", "trend_template_ok": 1},
        ],
        "optimization": {
            "F": {
                "trend_template_ok": {"type": "categorical", "choices": [1, "none"]},
            },
        },
    }


# ============================================================
# Tests for parse_optimization_params()
# ============================================================

class TestParseOptimizationParams:
    """parse_optimization_params(config, strategy_short) should return
    a list of parameter definitions extracted from the TOML config."""

    def test_parse_float_param(self, sample_config):
        """Float type TOML definition is correctly parsed."""
        params = parse_optimization_params(sample_config, "B")
        # Find the float param
        float_param = next(p for p in params if p["name"] == "min_1d_gain_pct")
        assert float_param["type"] == "float"
        assert float_param["min"] == 1.0
        assert float_param["max"] == 5.0
        assert float_param["step"] == 0.5

    def test_parse_categorical_param(self, sample_config):
        """Categorical type TOML definition is correctly parsed."""
        params = parse_optimization_params(sample_config, "B")
        cat_param = next(p for p in params if p["name"] == "min_market_cap")
        assert cat_param["type"] == "categorical"
        assert cat_param["choices"] == [1e7, 1e8, 5e8]

    def test_parse_int_param(self, config_with_int):
        """Int type TOML definition is correctly parsed."""
        params = parse_optimization_params(config_with_int, "X")
        int_param = next(p for p in params if p["name"] == "some_flag")
        assert int_param["type"] == "int"
        assert int_param["min"] == 0
        assert int_param["max"] == 10
        assert int_param["step"] == 2

    def test_undefined_strategy_raises_error(self, sample_config):
        """Requesting an undefined strategy raises ValueError."""
        with pytest.raises(ValueError, match="optimization"):
            parse_optimization_params(sample_config, "Z")

    def test_returns_all_params_for_strategy(self, sample_config):
        """All parameters defined for a strategy are returned."""
        params = parse_optimization_params(sample_config, "D")
        param_names = {p["name"] for p in params}
        assert param_names == {
            "min_dist_21ema_pct", "max_dist_21ema_pct",
            "min_market_cap", "trend_template_ok",
        }

    def test_none_string_converted(self, config_with_none):
        """The string 'none' in choices is converted to Python None."""
        params = parse_optimization_params(config_with_none, "F")
        cat_param = next(p for p in params if p["name"] == "trend_template_ok")
        assert None in cat_param["choices"]
        assert "none" not in cat_param["choices"]


# ============================================================
# Tests for apply_trial_params()
# ============================================================

class TestApplyTrialParams:
    """apply_trial_params(trial, param_defs, strat) should call
    the correct trial.suggest_* methods and update strat dict."""

    def test_float_calls_suggest_float(self, sample_config):
        """Float param triggers trial.suggest_float with correct args."""
        params = parse_optimization_params(sample_config, "B")
        float_param = [p for p in params if p["name"] == "min_1d_gain_pct"]

        mock_trial = MagicMock()
        mock_trial.suggest_float.return_value = 3.0

        strat = {"name": "B_test"}
        apply_trial_params(mock_trial, float_param, strat)

        mock_trial.suggest_float.assert_called_once_with(
            "min_1d_gain_pct", 1.0, 5.0, step=0.5
        )
        assert strat["min_1d_gain_pct"] == 3.0

    def test_categorical_calls_suggest_categorical(self, sample_config):
        """Categorical param triggers trial.suggest_categorical."""
        params = parse_optimization_params(sample_config, "B")
        cat_param = [p for p in params if p["name"] == "min_market_cap"]

        mock_trial = MagicMock()
        mock_trial.suggest_categorical.return_value = 1e8

        strat = {"name": "B_test"}
        apply_trial_params(mock_trial, cat_param, strat)

        mock_trial.suggest_categorical.assert_called_once_with(
            "min_market_cap", [1e7, 1e8, 5e8]
        )
        assert strat["min_market_cap"] == 1e8

    def test_int_calls_suggest_int(self, config_with_int):
        """Int param triggers trial.suggest_int."""
        params = parse_optimization_params(config_with_int, "X")

        mock_trial = MagicMock()
        mock_trial.suggest_int.return_value = 4

        strat = {"name": "X_test"}
        apply_trial_params(mock_trial, params, strat)

        mock_trial.suggest_int.assert_called_once_with(
            "some_flag", 0, 10, step=2
        )
        assert strat["some_flag"] == 4

    def test_base_params_inherited(self, sample_config):
        """Params NOT in optimization section are preserved from base strategy."""
        params = parse_optimization_params(sample_config, "B")
        mock_trial = MagicMock()
        mock_trial.suggest_float.return_value = 3.0
        mock_trial.suggest_categorical.return_value = 1e8

        # Start with a copy of the base strategy
        strat = {
            "name": "B_theme_momentum",
            "description": "Strong theme momentum",
            "min_1d_gain_pct": 2.0,
            "min_market_cap": 1e8,
        }
        apply_trial_params(mock_trial, params, strat)

        # description should be untouched (not in optimization section)
        assert strat["description"] == "Strong theme momentum"
        # optimized params should be overwritten
        assert strat["min_1d_gain_pct"] == 3.0


# ============================================================
# Tests for parse_optimization_periods()
# ============================================================

class TestParseOptimizationPeriods:
    """parse_optimization_periods(config) should return a list of
    (start_date, end_date) tuples."""

    def test_periods_parsed_from_toml(self, sample_config):
        """Periods are correctly extracted as (start, end) tuples."""
        periods = parse_optimization_periods(sample_config)
        assert len(periods) == 2
        assert periods[0] == ("2022-01-01", "2022-12-31")
        assert periods[1] == ("2024-06-01", "2025-12-31")

    def test_missing_periods_raises_error(self):
        """Missing optimization_periods section raises ValueError."""
        config = {"strategy": []}
        with pytest.raises(ValueError, match="optimization_periods"):
            parse_optimization_periods(config)


# ============================================================
# Tests for calculate_prune_penalty()
# ============================================================

class TestCalculatePrunePenalty:
    """TDD tests for penalty score generated when hit counts are out of bounds."""

    def test_penalty_too_few_hits(self):
        """Penalty increases sharply as hits drop towards 0."""
        bounds = (1.0, 15.0, 5.0)
        
        # Severe fail: 0 hits (-100 - (1.0 - 0.0) * 2000 = -2100)
        p_worst = calculate_prune_penalty(0.0, 0.0, bounds)
        assert p_worst == -2100.0
        
        # Mild fail: 0.8 hits. Score should be better than 0 hits, giving a gradient!
        # -100 - (1.0 - 0.8) * 2000 = -500
        p_better = calculate_prune_penalty(0.8, 10.0, bounds)
        assert p_better > p_worst
        assert p_better == pytest.approx(-500.0)

    def test_penalty_too_many_hits(self):
        """Penalty increases smoothly as hits rise above maximum."""
        bounds = (1.0, 15.0, 5.0)
        
        # Mild fail: 16 hits (-100 - (16-15)*50 = -150)
        p_mild = calculate_prune_penalty(16.0, 10.0, bounds)
        assert p_mild == -150.0
        
        # Severe fail: 35 hits (-100 - (35-15)*50 = -1100)
        p_worst = calculate_prune_penalty(35.0, 10.0, bounds)
        assert p_worst < p_mild
        assert p_worst == -1100.0
        
        # Ensure being slightly over (16 hits) is BETTER than being completely dead (0 hits)
        assert p_mild > calculate_prune_penalty(0.0, 0.0, bounds)

    def test_penalty_low_hit_rate(self):
        """Penalty increases as hit rate drops below minimum."""
        bounds = (1.0, 15.0, 5.0)
        
        # Average is fine (e.g. 5.0), but they all happened on a single day (e.g. hit_rate = 1.0)
        p_mild = calculate_prune_penalty(5.0, 4.0, bounds)
        assert p_mild == -100.0 - ((5.0 - 4.0) * 100) # -200.0

        p_worst = calculate_prune_penalty(5.0, 0.0, bounds)
        assert p_worst == -100.0 - (5.0 * 100) # -600.0
        assert p_worst < p_mild
