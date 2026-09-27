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
    enqueue_baseline_trial,
)


# ============================================================
# Fixtures: mock TOML config dicts
# ============================================================

@pytest.fixture
def sample_config():
    """Minimal config dict simulating parsed backtest_config.toml with embedded optimization."""
    return {
        "strategy": [
            {
                "name": "B_theme_momentum",
                "description": "Strong theme momentum",
                "min_1d_gain_pct": 2.0,
                "min_market_cap": 1e8,
                "optimization": {
                    "min_1d_gain_pct": {"type": "float", "min": 1.0, "max": 5.0, "step": 0.5},
                    "min_market_cap": {"type": "categorical", "choices": [1e7, 1e8, 5e8]},
                }
            },
            {
                "name": "D_ema21_pullback",
                "description": "EMA21 pullback",
                "min_dist_21ema_pct": -2.0,
                "max_dist_21ema_pct": 2.0,
                "min_market_cap": 1e9,
                "trend_template_ok": 1,
                "optimization": {
                    "min_dist_21ema_pct": {"type": "float", "min": -4.0, "max": -1.0, "step": 0.5},
                    "max_dist_21ema_pct": {"type": "float", "min": 0.5, "max": 4.0, "step": 0.5},
                    "min_market_cap": {"type": "categorical", "choices": [1e8, 5e8, 1e9]},
                    "trend_template_ok": {"type": "categorical", "choices": [1]},
                }
            },
        ],
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
            {
                "name": "X_test",
                "some_flag": 0,
                "optimization": {
                    "some_flag": {"type": "int", "min": 0, "max": 10, "step": 2},
                }
            },
        ],
    }


@pytest.fixture
def config_with_none():
    """Config with categorical containing 'none' (maps to Python None)."""
    return {
        "strategy": [
            {
                "name": "F_elite_momentum97",
                "trend_template_ok": 1,
                "optimization": {
                    "trend_template_ok": {"type": "categorical", "choices": [1, "none"]},
                }
            },
        ],
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
        with pytest.raises(ValueError, match="not found"):
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


# ============================================================
# Tests for enqueue_baseline_trial()
# ============================================================

class TestEnqueueBaselineTrial:
    """enqueue_baseline_trial(study, config, strategy_short) should extract
    the strategy's default parameters and enqueue them as a trial."""

    def test_enqueue_baseline_trial_success(self, sample_config):
        mock_study = MagicMock()
        
        # 'B' strategy matches 'B_theme_momentum'
        # Default params in B_theme_momentum:
        # - min_1d_gain_pct = 2.0
        # - min_market_cap = 1e8
        
        from optimization_runner import enqueue_baseline_trial
        
        success = enqueue_baseline_trial(mock_study, sample_config, "B")
        
        assert success is True
        mock_study.enqueue_trial.assert_called_once_with({
            "min_1d_gain_pct": 2.0,
            "min_market_cap": 1e8,
        })

    def test_enqueue_baseline_trial_strategy_not_found(self, sample_config):
        mock_study = MagicMock()
        from optimization_runner import enqueue_baseline_trial

        # Strategy 'Z' is not in sample_config
        with pytest.raises(ValueError, match="Strategy"):
            enqueue_baseline_trial(mock_study, sample_config, "Z")


# ============================================================
# Tests for holdout validation（修正6）
# ============================================================

@pytest.fixture
def config_with_validation(sample_config):
    sample_config["optimization_validation"] = {
        "sets": [
            {
                "label": "stress_bear",
                "windows": [
                    {"start": "2018-10-01", "end": "2018-12-31"},
                    {"start": "2020-02-01", "end": "2020-05-31"},
                ],
            },
            {
                "label": "calm_recent",
                "windows": [
                    {"start": "2023-01-01", "end": "2023-12-31"},
                ],
            },
        ]
    }
    return sample_config


class TestParseValidationSets:
    def test_parse_sets(self, config_with_validation):
        from optimization_runner import parse_validation_sets

        sets = parse_validation_sets(config_with_validation)
        assert len(sets) == 2
        assert sets[0]["label"] == "stress_bear"
        assert sets[0]["windows"] == [("2018-10-01", "2018-12-31"),
                                      ("2020-02-01", "2020-05-31")]
        assert sets[1]["windows"] == [("2023-01-01", "2023-12-31")]

    def test_missing_section_returns_empty(self, sample_config):
        from optimization_runner import parse_validation_sets

        assert parse_validation_sets(sample_config) == []


class TestEvaluateHoldout:
    """ホールドアウト評価はセット内の全窓のトレードをプールしてから指標計算すること。

    3ヶ月窓単体に活動量ゲートやトレード数最少要件を当てると、静かなスクリーンが
    不当に沈むため（doc/completed/backtest_optimization_hardening_plan.md 修正6）。
    """

    def _stub_trade(self, pnl, day):
        from datetime import date, timedelta
        from backend.backtest.backtest_simulator import TradeResult
        d = date(2020, 3, 2) + timedelta(days=day)
        return TradeResult(symbol_id=1, ticker="TST", entry_date=d,
                           exit_date=d + timedelta(days=3),
                           entry_price=100.0, exit_price=100.0 * (1 + pnl / 100),
                           pnl_pct=pnl, holding_days=3, exit_reason="test")

    def test_pools_trades_across_windows(self, config_with_validation, monkeypatch):
        import optimization_runner as opt
        from optimization_runner import parse_validation_sets, evaluate_holdout_for_params

        # 窓ごとに 2 トレード / 3 トレードを返すスタブ
        trades_per_window = [
            [self._stub_trade(5.0, 0), self._stub_trade(-2.0, 10)],
            [self._stub_trade(3.0, 20), self._stub_trade(1.0, 30), self._stub_trade(-1.0, 40)],
            [self._stub_trade(2.0, 50)],
        ]
        calls = []

        def fake_get_cached_data(config_app, start, end):
            calls.append((start, end))
            return (None, None, None, None, None, [1, 2, 3])  # trading_dates はダミー

        def fake_run_single_strategy(*args, **kwargs):
            return {}, trades_per_window[len(calls) - 1]

        monkeypatch.setattr(opt, "get_cached_data", fake_get_cached_data)
        monkeypatch.setattr(opt, "run_single_strategy", fake_run_single_strategy)

        sets = parse_validation_sets(config_with_validation)
        result = evaluate_holdout_for_params(
            {"name": "T"}, config_app={}, exit_rules=None,
            validation_sets=sets, entry_mode="close", consider_tax=0.0)

        # stress_bear: 2 窓のトレードがプールされて 5 件
        stress = result["stress_bear"]
        assert stress["pooled"]["total_trades"] == 5
        assert len(stress["windows"]) == 2
        # calm_recent: 1 窓 1 件
        assert result["calm_recent"]["pooled"]["total_trades"] == 1
        # 評価に使った窓は定義順どおり
        assert calls == [("2018-10-01", "2018-12-31"),
                         ("2020-02-01", "2020-05-31"),
                         ("2023-01-01", "2023-12-31")]


# calculate_custom_score() のスコアリング・テストは 2026-07-06 の目的関数再設計
# （主指標: expectancy_lcb → 期間CAGR × DDペナルティ × 検出件数帯）に伴い
# test_optimization_score.py へ移設した。


# ============================================================
# verify_and_register_objective_id (目的関数の混入防止)
# ============================================================
from optimization_runner import (
    CURRENT_OBJECTIVE_ID,
    CURRENT_OBJECTIVE_FORMULA,
    IncompatibleObjectiveError,
    verify_and_register_objective_id,
)


def test_verify_and_register_objective_id_new_study():
    """新規 study（trials=0, user_attrs={}）に現在の ID と formula を記録する。"""
    mock_study = MagicMock()
    mock_study.study_name = "test_study"
    mock_study.user_attrs = {}
    mock_study.trials = []

    verify_and_register_objective_id(mock_study)

    mock_study.set_user_attr.assert_any_call("objective_id", CURRENT_OBJECTIVE_ID)
    mock_study.set_user_attr.assert_any_call("objective_formula", CURRENT_OBJECTIVE_FORMULA)


def test_verify_and_register_objective_id_matching():
    """既存 study の objective_id が一致する場合、正常に通過する。"""
    mock_study = MagicMock()
    mock_study.study_name = "test_study"
    mock_study.user_attrs = {"objective_id": CURRENT_OBJECTIVE_ID}
    mock_study.trials = [MagicMock()]

    # 例外が送出されないこと
    verify_and_register_objective_id(mock_study)


def test_verify_and_register_objective_id_mismatch():
    """既存 study の objective_id が異なる場合、IncompatibleObjectiveError で停止する。"""
    mock_study = MagicMock()
    mock_study.study_name = "A_momentum_breakout"
    mock_study.user_attrs = {"objective_id": "cagr_v1"}
    mock_study.trials = [MagicMock()]

    with pytest.raises(IncompatibleObjectiveError) as exc_info:
        verify_and_register_objective_id(mock_study)

    err = str(exc_info.value)
    assert "A_momentum_breakout" in err
    assert "cagr_v1" in err
    assert CURRENT_OBJECTIVE_ID in err
    assert "退避" in err


def test_verify_and_register_objective_id_legacy_with_trials():
    """trial を持つが objective_id が未記録のレガシー study は混入防止のためエラーにする。"""
    mock_study = MagicMock()
    mock_study.study_name = "B_legacy_study"
    mock_study.user_attrs = {}  # objective_id なし
    mock_study.trials = [MagicMock(), MagicMock()]  # 2 trials 存在

    with pytest.raises(IncompatibleObjectiveError) as exc_info:
        verify_and_register_objective_id(mock_study)

    err = str(exc_info.value)
    assert "B_legacy_study" in err
    assert "2 件の trial" in err
    assert "レガシー study" in err
    assert "退避" in err


def test_verify_and_register_objective_id_empty_trials_unregistered():
    """trial が 0 件で未記録の場合は新規 study と同様に登録して通過する。"""
    mock_study = MagicMock()
    mock_study.study_name = "empty_study"
    mock_study.user_attrs = {}
    mock_study.trials = []

    verify_and_register_objective_id(mock_study)

    mock_study.set_user_attr.assert_any_call("objective_id", CURRENT_OBJECTIVE_ID)
    mock_study.set_user_attr.assert_any_call("objective_formula", CURRENT_OBJECTIVE_FORMULA)


def test_main_aborts_on_incompatible_objective(capsys, monkeypatch):
    """main() 実行時に既存 study の objective_id が不一致なら sys.exit(1) で即座に終了する。"""
    import optimization_runner as opt

    mock_study = MagicMock()
    mock_study.study_name = "A_momentum_breakout"
    mock_study.user_attrs = {"objective_id": "old_objective_v0"}
    mock_study.trials = [MagicMock()]

    monkeypatch.setattr(opt, "init_db", lambda *a, **kw: None)
    monkeypatch.setattr(opt, "get_cached_data", lambda *a, **kw: None)
    monkeypatch.setattr("optuna.create_study", lambda **kw: mock_study)

    with pytest.raises(SystemExit) as exc_info:
        opt.main(["--strategy", "A", "--trials", "1"])
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "[ERROR] 目的関数の不一致を検知しました" in captured.out
    assert "old_objective_v0" in captured.out



