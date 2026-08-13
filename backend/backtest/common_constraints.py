"""
common_constraints.py — 全戦略共通のハード制約（流動性床）の単一実装。

`doc/in_progress/screener_filter_unification_plan.md` §5 Phase 1「流動性床
（min_avg_dollar_volume_21）の注入を単一の純関数へ集約」に対応する。

従来、以下の4モジュール5箇所に独立実装（コピペ）されていた:
  1. backend/api/screener_router.py::_load_min_avg_dollar_volume_21
  2. backend/backtest/backtest_runner.py::run_backtest（ループ内）
  3. backend/optimization_runner.py（run_holdout_validation / objective の2箇所）
  4. backend/backtest/scenario_runner.py::inject_liquidity_floor

このモジュールが値の解決（TOML 読み込み）と注入（戦略 dict への設定）の
唯一の実装を提供し、上記5箇所はここへ委譲する。
"""
import os
from typing import Any, Dict, List, Mapping, Optional, Union

import tomli

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKTEST_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "backend", "backtest", "backtest_config.toml")


def load_min_avg_dollar_volume_21(config: Optional[Mapping[str, Any]] = None) -> Optional[float]:
    """全戦略共通の流動性ハード制約の値を backtest_config.toml [general] から読む。

    config を渡した場合はそれを使う（既にロード済みの呼び出し側用。バックテスト／
    最適化／シナリオテストは起動時に TOML を1度読み込んでいるため、二重に
    ファイルを開かない）。渡さなければ backtest_config.toml を直接読み込む
    （screener_router.py 等、TOML を未ロードの呼び出し側用）。
    """
    if config is None:
        try:
            with open(_BACKTEST_CONFIG_PATH, "rb") as f:
                config = tomli.load(f)
        except OSError:
            return None
    val = config.get("general", {}).get("min_avg_dollar_volume_21")
    return float(val) if val is not None else None


def inject_liquidity_floor(filters: Dict[str, Any], floor: Optional[float]) -> Dict[str, Any]:
    """1戦略の filters に流動性床を注入する。

    戦略側の明示指定があれば尊重（上書きしない）。入力を破壊せず新しい dict を返す。
    """
    result = dict(filters)
    if floor is None:
        return result
    if 'min_avg_dollar_volume_21' not in result:
        result['min_avg_dollar_volume_21'] = float(floor)
    return result


StrategiesType = Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]


def inject_liquidity_floor_all(strategies: StrategiesType, floor: Optional[float]) -> StrategiesType:
    """dict[str, dict] / list[dict] のどちらでも受けて一括注入する。"""
    if isinstance(strategies, dict):
        return {name: inject_liquidity_floor(filters, floor) for name, filters in strategies.items()}
    return [inject_liquidity_floor(strat, floor) for strat in strategies]
