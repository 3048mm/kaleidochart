"""シナリオテストの戦略スキャン範囲の検証（backtest/scenario_runner.py）

## 背景（2026-07-20 発見、2026-09-03 Pickup/Check/Common 再編で対象グループ変更）

`run_scenario_test` は `strat_name.startswith(SCENARIO_TARGET_PREFIX)`（既定 `'Rise - Pickup'`）
の戦略だけをスキャンする。戦略名は `f"{section.capitalize()} - {group} - {name}"` で
組み立てられるため、preset TOML の `group` がこの値と揃っていないと**警告なく完全に
スキャン対象外**になる。

2026-09-03 の再編で「バックテスト実績のある戦略」の group は `"Pickup"` に統一された
（新設の `"Check"` は観察用途で edge 未検証のため対象外のままにする、意図的な変更）。
防御が無いままでは新しい preset を足したときに黙って無視される
（D-2/I-6 と同型のサイレント失敗パターン）。

最悪ケースは「全戦略が除外され、シグナル0件の結果が正常な結果として扱われる」こと。
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from backtest.scenario_runner import (  # noqa: E402
    SCENARIO_TARGET_PREFIX,
    report_strategy_scan_coverage,
    split_scannable_strategies,
)


def _strats(*names):
    return {n: {} for n in names}


# ---------------------------------------------------------------------------
# split_scannable_strategies
# ---------------------------------------------------------------------------
def test_pickup_group_is_scanned():
    scanned, skipped = split_scannable_strategies(_strats("Rise - Pickup - A_opt"))
    assert scanned == ["Rise - Pickup - A_opt"]
    assert skipped == []


def test_other_group_is_excluded():
    """group が Pickup 以外だと対象外になる（これが元の落とし穴）"""
    scanned, skipped = split_scannable_strategies(
        _strats("Rise - Pickup - A_opt", "Rise - Momentum - B_opt")
    )
    assert scanned == ["Rise - Pickup - A_opt"]
    assert skipped == ["Rise - Momentum - B_opt"]


def test_fall_side_is_excluded():
    """Fall 側はロング専用の設計により対象外"""
    scanned, skipped = split_scannable_strategies(
        _strats("Rise - Pickup - A_opt", "Fall - Check - Short1")
    )
    assert scanned == ["Rise - Pickup - A_opt"]
    assert skipped == ["Fall - Check - Short1"]


# ---------------------------------------------------------------------------
# report_strategy_scan_coverage
# ---------------------------------------------------------------------------
def test_excluded_strategies_are_reported():
    """除外された戦略が黙って消えず、理由つきで報告されること"""
    lines = []
    report_strategy_scan_coverage(
        _strats("Rise - Pickup - A_opt", "Rise - Momentum - B_opt"),
        logger_fn=lines.append,
    )
    out = "\n".join(lines)

    assert "Rise - Momentum - B_opt" in out
    assert "group を 'Pickup' に" in out, "対処方法が示されていない"


def test_fall_exclusion_reports_long_only_reason():
    """Fall の除外は「設定ミス」ではなく仕様である旨が伝わること"""
    lines = []
    report_strategy_scan_coverage(
        _strats("Rise - Pickup - A_opt", "Fall - Check - Short1"),
        logger_fn=lines.append,
    )
    out = "\n".join(lines)

    assert "ロング専用" in out


def test_nothing_reported_when_all_scannable():
    """全戦略が対象なら何も出さない（ノイズにしない）"""
    lines = []
    report_strategy_scan_coverage(
        _strats("Rise - Pickup - A_opt", "Rise - Pickup - B_opt"), logger_fn=lines.append
    )
    assert lines == []


def test_raises_when_all_strategies_excluded():
    """全滅時は例外。

    ここを警告で済ませると「実行できたがシグナル0件」を正常な結果として
    受け取ってしまい、最も気づきにくい失敗になる。
    """
    with pytest.raises(ValueError, match="1件もありません"):
        report_strategy_scan_coverage(
            _strats("Rise - Momentum - B_opt", "Fall - Check - Short1"),
            logger_fn=lambda *_: None,
        )


def test_returns_scanned_list():
    scanned = report_strategy_scan_coverage(
        _strats("Rise - Pickup - A_opt", "Rise - Other - C"), logger_fn=lambda *_: None
    )
    assert scanned == ["Rise - Pickup - A_opt"]


def test_prefix_constant_matches_scorer_default():
    """runner と scorer で判定が分裂しないこと（分裂すると再発する）"""
    from backtest.scenario_scorer import ScenarioScorer

    assert ScenarioScorer().target_group_prefix == SCENARIO_TARGET_PREFIX
