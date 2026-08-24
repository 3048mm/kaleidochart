"""
common_constraints.py — 全戦略共通のハード制約（流動性床）の単一実装。

`doc/completed/screener_filter_unification_plan.md` §5 Phase 1「流動性床
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


class InvalidTaxRateError(ValueError):
    """税率が率（0.0〜1.0）として不正。20% を 20.0 と書く事故を検出する。"""


def load_tax_rate(config: Optional[Mapping[str, Any]] = None, for_optimization: bool = False) -> float:
    """backtest_config.toml の [general] consider_tax を率として解決する。

    値は**率**（0.2 = 20%）。1.0 を超える値は単位の取り違え（20.0 = 2000%）
    としてエラーにする。0.0（税なし）は正当な設定として許可する。
    キー欠如時は 0.0（税なし）を既定値とする。

    for_optimization=True の場合は `consider_tax_optimization` を読む（型1: 最適化
    バックテスト経路専用。既定 0.0 = 税なし）。False（既定）の場合は従来どおり
    `consider_tax` を読む（型2・型3: 実運用シミュレーション経路。既定 0.2 のまま）。
    型1が税込みで最適化すると取引数の減少がCAGR改善を上回り型3の成績を悪化させる
    実測があったため経路を分離した（doc/completed/objective_quality_first_plan.md §3.2）。
    単位検証（`> 1.0` でエラー）はどちらの経路にも同じく適用する。

    config を渡した場合はそれを使う（既にロード済みの呼び出し側用）。
    渡さなければ backtest_config.toml を直接読み込む。
    """
    if config is None:
        try:
            with open(_BACKTEST_CONFIG_PATH, "rb") as f:
                config = tomli.load(f)
        except OSError:
            return 0.0
    key = "consider_tax_optimization" if for_optimization else "consider_tax"
    rate = float(config.get("general", {}).get(key, 0.0))
    if rate < 0.0 or rate > 1.0:
        raise InvalidTaxRateError(
            f"{key} は率で指定してください（20% なら 0.2）。現在値: {rate}"
        )
    return rate
