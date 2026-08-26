"""補正の再適用のテスト（scripts/reapply_corrections.py）。

実際の Parquet を触る `run()` は差し替えて、**どの補正がどのスクリプトへ
どの引数で渡るか**と、**結果の集計**だけを固定する。
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline.price_corrections import Correction  # noqa: E402
from scripts.reapply_corrections import (  # noqa: E402
    APPLIED,
    FAILED,
    SKIPPED,
    apply_one,
    summarize,
)

SPLIT = Correction(ticker="BYND", kind="split", reason="1:30 併合",
                   before="2026-08-13", factor=30.0)
FAB = Correction(ticker="AVB", kind="fabricated_rows", reason="ffill 捏造行",
                 date_from="2026-08-15", date_to="2026-08-23")


class _Spy:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        if self.exc:
            raise self.exc


# ---------------------------------------------------------------------------
# ディスパッチ — 引数の取り違えは本番データを壊す
# ---------------------------------------------------------------------------
def test_split_is_dispatched_with_the_ledger_values(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr("scripts.reapply_corrections._run_split", spy)
    assert apply_one(SPLIT, dry_run=True) == APPLIED

    (_, kw), = spy.calls
    assert kw["ticker"] == "BYND"
    assert kw["before"] == "2026-08-13"
    assert kw["factor"] == 30.0
    assert kw["dry_run"] is True
    assert "1:30 併合" in kw["reason"]


def test_fabricated_rows_is_dispatched_as_auto_detection(monkeypatch):
    """**日付を明示せず自動検出させる。**

    再構築後に上流が返す欠損の位置は前回と同じとは限らない。
    範囲だけ渡して「OHLC 全同値かつ出来高0」を探させる方が正しい。
    """
    spy = _Spy()
    monkeypatch.setattr("scripts.reapply_corrections._run_delete", spy)
    assert apply_one(FAB, dry_run=False) == APPLIED

    (_, kw), = spy.calls
    assert kw["ticker"] == "AVB"
    assert kw["auto"] is True
    assert kw["dates"] is None
    assert kw["date_from"] == "2026-08-15"
    assert kw["date_to"] == "2026-08-23"


def test_dry_run_is_passed_through(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr("scripts.reapply_corrections._run_split", spy)
    apply_one(SPLIT, dry_run=True)
    assert spy.calls[0][1]["dry_run"] is True


# ---------------------------------------------------------------------------
# 「既に正しい」を失敗と区別する
# ---------------------------------------------------------------------------
def test_seam_check_failure_counts_as_skipped(monkeypatch):
    """接合部の検算に落ちる＝**既に補正済み**か、比率が違う。

    再適用は「全部試して、当たらないものは弾かれる」運用なので、
    これを失敗として数えると毎回ノイズになる。
    """
    from scripts.adjust_symbol_split import SeamCheckError

    monkeypatch.setattr("scripts.reapply_corrections._run_split",
                        _Spy(SeamCheckError("比率が一致しません")))
    assert apply_one(SPLIT, dry_run=False) == SKIPPED


def test_other_errors_count_as_failed(monkeypatch):
    monkeypatch.setattr("scripts.reapply_corrections._run_split",
                        _Spy(RuntimeError("Parquet が壊れている")))
    assert apply_one(SPLIT, dry_run=False) == FAILED


def test_system_exit_counts_as_failed(monkeypatch):
    """スクリプトは異常時に `sys.exit(1)` する。プロセスごと落とさず拾う。"""
    monkeypatch.setattr("scripts.reapply_corrections._run_split",
                        _Spy(SystemExit(1)))
    assert apply_one(SPLIT, dry_run=False) == FAILED


def test_clean_system_exit_is_not_a_failure(monkeypatch):
    monkeypatch.setattr("scripts.reapply_corrections._run_split",
                        _Spy(SystemExit(0)))
    assert apply_one(SPLIT, dry_run=False) == APPLIED


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def test_summary_counts_each_outcome():
    s = summarize([APPLIED, APPLIED, SKIPPED, FAILED])
    assert s == {APPLIED: 2, SKIPPED: 1, FAILED: 1}


def test_summary_of_nothing_is_empty():
    assert summarize([]) == {}


def test_failure_makes_the_run_unsuccessful():
    from scripts.reapply_corrections import exit_code_for

    assert exit_code_for({APPLIED: 3}) == 0
    assert exit_code_for({APPLIED: 1, SKIPPED: 2}) == 0
    assert exit_code_for({}) == 0
    assert exit_code_for({APPLIED: 1, FAILED: 1}) == 1, \
        "1件でも失敗したら検収で気付けるように非ゼロで終わること"


# ---------------------------------------------------------------------------
# 未知の種別は台帳側で弾かれるが、念のため
# ---------------------------------------------------------------------------
def test_unknown_kind_raises():
    bogus = Correction(ticker="X", kind="magic", reason="?")
    with pytest.raises(ValueError, match="magic"):
        apply_one(bogus, dry_run=True)


# ---------------------------------------------------------------------------
# 「何もしなかった」を「適用した」と報告しない
#
# 2026-08-26 の実行で AVB が「適用」と出たが、実際は捏造行が0件で何もしていなかった。
# 集計が嘘をつくと、再構築後の検収で「3件とも当たった」と誤読する。
# ---------------------------------------------------------------------------
class _NoopSpy(_Spy):
    def __call__(self, *a, **kw):
        super().__call__(*a, **kw)
        return "noop"


def test_noop_from_the_runner_counts_as_skipped(monkeypatch):
    monkeypatch.setattr("scripts.reapply_corrections._run_delete", _NoopSpy())
    assert apply_one(FAB, dry_run=False) == SKIPPED


def test_actual_work_still_counts_as_applied(monkeypatch):
    class _Did(_Spy):
        def __call__(self, *a, **kw):
            super().__call__(*a, **kw)
            return "applied"

    monkeypatch.setattr("scripts.reapply_corrections._run_delete", _Did())
    assert apply_one(FAB, dry_run=False) == APPLIED


def test_runner_returning_none_is_treated_as_applied(monkeypatch):
    """戻り値を返さない古い形の `run()` でも壊れないこと。"""
    monkeypatch.setattr("scripts.reapply_corrections._run_split", _Spy())
    assert apply_one(SPLIT, dry_run=True) == APPLIED
