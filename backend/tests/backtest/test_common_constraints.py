"""test_common_constraints.py — 流動性床（min_avg_dollar_volume_21）の単一実装のテスト。

従来 4モジュール5箇所（screener_router.py / backtest_runner.py / optimization_runner.py(2箇所) /
scenario_runner.py）に独立実装されていた注入ロジックを
backend/backtest/common_constraints.py へ集約する（doc/completed/screener_filter_unification_plan.md
§5 Phase 1）。
"""
import pytest

from backend.backtest.common_constraints import (
    InvalidTaxRateError,
    inject_liquidity_floor,
    inject_liquidity_floor_all,
    load_min_avg_dollar_volume_21,
    load_tax_rate,
)


class TestInjectLiquidityFloor:
    def test_injects_into_strategy_without_it(self):
        filters = {"min_change_1d_pct": 5.0}
        result = inject_liquidity_floor(filters, 2e6)
        assert result["min_avg_dollar_volume_21"] == 2e6

    def test_respects_explicit_override(self):
        """戦略側の明示指定があれば上書きしない。"""
        filters = {"min_avg_dollar_volume_21": 5e6}
        result = inject_liquidity_floor(filters, 2e6)
        assert result["min_avg_dollar_volume_21"] == 5e6

    def test_noop_when_floor_is_none(self):
        filters = {"min_change_1d_pct": 5.0}
        result = inject_liquidity_floor(filters, None)
        assert "min_avg_dollar_volume_21" not in result

    def test_does_not_mutate_input(self):
        """入力 dict を破壊せず、新しい dict を返すこと。"""
        filters = {"min_change_1d_pct": 5.0}
        result = inject_liquidity_floor(filters, 2e6)
        assert "min_avg_dollar_volume_21" not in filters
        assert result is not filters


class TestInjectLiquidityFloorAll:
    def test_accepts_dict_of_strategies(self):
        strategies = {
            "A": {"min_change_1d_pct": 5.0},
            "B": {"min_avg_dollar_volume_21": 9e6},
        }
        result = inject_liquidity_floor_all(strategies, 2e6)
        assert result["A"]["min_avg_dollar_volume_21"] == 2e6
        assert result["B"]["min_avg_dollar_volume_21"] == 9e6

    def test_accepts_list_of_strategies(self):
        strategies = [
            {"name": "A", "min_change_1d_pct": 5.0},
            {"name": "B", "min_avg_dollar_volume_21": 9e6},
        ]
        result = inject_liquidity_floor_all(strategies, 2e6)
        by_name = {s["name"]: s for s in result}
        assert by_name["A"]["min_avg_dollar_volume_21"] == 2e6
        assert by_name["B"]["min_avg_dollar_volume_21"] == 9e6

    def test_noop_when_floor_is_none_for_dict(self):
        strategies = {"A": {"min_change_1d_pct": 5.0}}
        result = inject_liquidity_floor_all(strategies, None)
        assert "min_avg_dollar_volume_21" not in result["A"]

    def test_noop_when_floor_is_none_for_list(self):
        strategies = [{"name": "A", "min_change_1d_pct": 5.0}]
        result = inject_liquidity_floor_all(strategies, None)
        assert "min_avg_dollar_volume_21" not in result[0]


class TestLoadMinAvgDollarVolume21:
    def test_reads_from_passed_config(self):
        config = {"general": {"min_avg_dollar_volume_21": 3e6}}
        assert load_min_avg_dollar_volume_21(config) == 3e6

    def test_returns_none_when_absent_in_config(self):
        config = {"general": {}}
        assert load_min_avg_dollar_volume_21(config) is None

    def test_reads_from_backtest_config_toml_when_no_config_passed(self):
        """config を渡さない場合、backtest_config.toml [general] を直接読む
        （screener_router.py 等、TOML を未ロードの呼び出し側用）。
        """
        val = load_min_avg_dollar_volume_21()
        assert val == 2e6  # backend/backtest/backtest_config.toml の現行値


class TestLoadTaxRate:
    """税率（consider_tax）の解決と検証。単位は率（0.2=20%）。

    `> 1.0` の値は「20% のつもりで 20.0 と書く事故」を検出してエラーにする
    （doc/in_progress/tax_rate_wiring_plan.md §3.1）。
    """

    def test_zero_is_allowed(self):
        """0.0（税なし）は正当な設定として許可する。"""
        config = {"general": {"consider_tax": 0.0}}
        assert load_tax_rate(config) == 0.0

    def test_valid_rate_passes(self):
        config = {"general": {"consider_tax": 0.2}}
        assert load_tax_rate(config) == 0.2

    def test_percent_style_value_raises(self):
        """20.0（%のつもり）は率の範囲を超えるためエラー。"""
        config = {"general": {"consider_tax": 20.0}}
        with pytest.raises(InvalidTaxRateError):
            load_tax_rate(config)

    def test_negative_rate_raises(self):
        config = {"general": {"consider_tax": -0.1}}
        with pytest.raises(InvalidTaxRateError):
            load_tax_rate(config)

    def test_missing_key_defaults_to_zero(self):
        config = {"general": {}}
        assert load_tax_rate(config) == 0.0

    def test_invalid_tax_rate_error_is_value_error_subclass(self):
        assert issubclass(InvalidTaxRateError, ValueError)


class TestLoadTaxRateForOptimization:
    """税の経路分離（doc/in_progress/objective_quality_first_plan.md §3.2）。

    型1（最適化バックテスト）は `consider_tax_optimization` を、
    型2・型3（実運用シミュレーション）は従来どおり `consider_tax` を読む。
    """

    def test_default_reads_consider_tax(self):
        """for_optimization を指定しない（既定 False）場合は consider_tax を読む。"""
        config = {"general": {"consider_tax": 0.2, "consider_tax_optimization": 0.0}}
        assert load_tax_rate(config) == 0.2

    def test_for_optimization_reads_separate_key(self):
        """for_optimization=True は consider_tax_optimization を読み、consider_tax とは独立。"""
        config = {"general": {"consider_tax": 0.2, "consider_tax_optimization": 0.0}}
        assert load_tax_rate(config, for_optimization=True) == 0.0

    def test_for_optimization_missing_key_defaults_to_zero(self):
        """consider_tax_optimization が未設定なら 0.0（税なし）が既定。"""
        config = {"general": {"consider_tax": 0.2}}
        assert load_tax_rate(config, for_optimization=True) == 0.0

    def test_types_return_independently_different_values(self):
        """型1経路と型2/型3経路が異なる税率を返すこと（回帰: 型3側が巻き込まれない）。"""
        config = {"general": {"consider_tax": 0.2, "consider_tax_optimization": 0.05}}
        rate_type1 = load_tax_rate(config, for_optimization=True)
        rate_type23 = load_tax_rate(config, for_optimization=False)
        assert rate_type1 == 0.05
        assert rate_type23 == 0.2
        assert rate_type1 != rate_type23

    def test_unit_validation_applies_to_optimization_path_too(self):
        """単位検証（%表記の取り違え検出）は for_optimization=True 側にも効くこと。"""
        config = {"general": {"consider_tax_optimization": 20.0}}
        with pytest.raises(InvalidTaxRateError):
            load_tax_rate(config, for_optimization=True)

    def test_unit_validation_applies_to_default_path(self):
        """単位検証は for_optimization=False（既定）側にも引き続き効くこと。"""
        config = {"general": {"consider_tax": 20.0}}
        with pytest.raises(InvalidTaxRateError):
            load_tax_rate(config, for_optimization=False)
