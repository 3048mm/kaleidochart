"""test_common_constraints.py — 流動性床（min_avg_dollar_volume_21）の単一実装のテスト。

従来 4モジュール5箇所（screener_router.py / backtest_runner.py / optimization_runner.py(2箇所) /
scenario_runner.py）に独立実装されていた注入ロジックを
backend/backtest/common_constraints.py へ集約する（doc/in_progress/screener_filter_unification_plan.md
§5 Phase 1）。
"""
from backend.backtest.common_constraints import (
    inject_liquidity_floor,
    inject_liquidity_floor_all,
    load_min_avg_dollar_volume_21,
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
