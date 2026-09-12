"""`update_pipeline.py` の再構築手順（`--rebuild-from`）ディスパッチのテスト。

T3/T4/T5 は Parquet 基点で完結する（`_rebuild_from_parquet()`）ため、
呼び出し元の `_run_rebuild_or_pipeline()` が `run_pipeline()` を呼ばないこと、
呼び出し順序が T3→T4→T5→SQLite復元 になることを検証する。

実際のパイプライン・再計算は走らせず、`recompute_parquet_*` / `run_production_restore`
/ `run_pipeline` をモックして呼び出しの有無・順序だけを確認する。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.3・5-8
"""
import logging

import pytest

from scripts import update_pipeline
from scripts import recompute_parquet_indicators
from scripts import recompute_parquet_ranks
from scripts import recompute_parquet_signals
from scripts import run_production_restore as rpr_module
import pipeline.orchestrator as orchestrator_module


@pytest.fixture()
def call_order(monkeypatch):
    """3本の再計算 + SQLite復元 + run_pipeline の呼び出し順を記録するモック。"""
    order = []

    monkeypatch.setattr(recompute_parquet_indicators, "run",
                         lambda dry_run, chunk_size: order.append("T3"))
    monkeypatch.setattr(recompute_parquet_ranks, "run",
                         lambda dry_run: order.append("T4"))
    monkeypatch.setattr(recompute_parquet_signals, "run",
                         lambda dry_run: order.append("T5"))
    monkeypatch.setattr(rpr_module, "run_production_restore",
                         lambda *a, **k: (order.append("restore") or True))

    run_pipeline_calls = []
    monkeypatch.setattr(orchestrator_module, "run_pipeline",
                         lambda **kwargs: run_pipeline_calls.append(kwargs))

    return order, run_pipeline_calls


def _call(rebuild_from, categories=None, caplog_level=logging.INFO):
    logger = logging.getLogger("test_update_pipeline_rebuild")
    update_pipeline._run_rebuild_or_pipeline(
        rebuild_from=rebuild_from,
        selected_categories=categories,
        config={"system": {}},
        db_path="dummy.db",
        logger=logger,
        skip_fetch=False,
        skip_sync=False,
        skip_t3=False,
        recalculate_all=False,
    )


class TestRebuildFromT3:
    def test_calls_t3_t4_t5_restore_in_order(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T3")

        assert order == ["T3", "T4", "T5", "restore"]

    def test_does_not_call_run_pipeline(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T3")

        assert run_pipeline_calls == []


class TestRebuildFromT4:
    def test_skips_t3_calls_t4_t5_restore_in_order(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T4")

        assert order == ["T4", "T5", "restore"]
        assert run_pipeline_calls == []


class TestRebuildFromT5:
    def test_skips_t3_and_t4_calls_t5_restore_only(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T5")

        assert order == ["T5", "restore"]
        assert run_pipeline_calls == []


class TestRebuildFromOtherOrNone:
    """T2 指定や通常実行（rebuild_from 無し）は従来どおり run_pipeline() を呼ぶ。"""

    def test_rebuild_from_t2_calls_run_pipeline_and_not_parquet_recompute(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T2", categories=["AI"])

        assert order == []
        assert len(run_pipeline_calls) == 1
        assert run_pipeline_calls[0]["rebuild_from"] == "T2"
        assert run_pipeline_calls[0]["categories"] == ["AI"]

    def test_no_rebuild_from_calls_run_pipeline(self, call_order):
        order, run_pipeline_calls = call_order

        _call(None)

        assert order == []
        assert len(run_pipeline_calls) == 1
        assert run_pipeline_calls[0]["rebuild_from"] is None


class TestCategoryIgnoredForParquetRebuild:
    def test_warns_and_ignores_category_for_t3(self, call_order, caplog):
        order, _ = call_order
        with caplog.at_level(logging.WARNING, logger="test_update_pipeline_rebuild"):
            _call("T3", categories=["AI"])

        assert order == ["T3", "T4", "T5", "restore"]
        assert any("--category" in r.message for r in caplog.records)
