"""`update_pipeline.py` の再構築手順（`--rebuild-from`）ディスパッチのテスト。

T3/T4/T5 は Parquet 基点で完結する（`_rebuild_from_parquet()`）。
呼び出し元の `_run_rebuild_or_pipeline()` 自身は `run_pipeline()` を呼ばないが、
`_rebuild_from_parquet()` は SQLite 復元の**後**に、後処理
（T1/FX/仮想指数/rotate/purge/整合監査）のため `run_pipeline()` を1回呼ぶ
（§7-6(2)・5-8c）。呼び出し順序が T3→T4→T5→SQLite復元→後処理 になることを検証する。

実際のパイプライン・再計算は走らせず、`recompute_parquet_*` / `run_production_restore`
/ `run_pipeline` をモックして呼び出しの有無・順序・引数だけを確認する。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.3・5-8・§7-6(2)・5-8c
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
    """3本の再計算 + SQLite復元 + run_pipeline の呼び出し順を記録するモック。

    `run_pipeline` は `_rebuild_from_parquet()` の後処理（5-8c）と、
    `_run_rebuild_or_pipeline()` の通常経路（T2 / rebuild_from 無し）の
    どちらからも呼ばれうる。呼び出しごとの kwargs は `run_pipeline_calls` に、
    呼び出し順は他の呼び出しと合わせて `order` に "run_pipeline" として記録する。
    """
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

    def _fake_run_pipeline(**kwargs):
        order.append("run_pipeline")
        run_pipeline_calls.append(kwargs)

    monkeypatch.setattr(orchestrator_module, "run_pipeline", _fake_run_pipeline)

    return order, run_pipeline_calls


def _call(rebuild_from, categories=None, skip_sync=False):
    logger = logging.getLogger("test_update_pipeline_rebuild")
    update_pipeline._run_rebuild_or_pipeline(
        rebuild_from=rebuild_from,
        selected_categories=categories,
        config={"system": {"db_path": "dummy_config_db.db"}},
        db_path="dummy.db",
        logger=logger,
        skip_fetch=False,
        skip_sync=skip_sync,
        skip_t3=False,
        recalculate_all=False,
    )


class TestRebuildFromT3:
    def test_calls_t3_t4_t5_restore_then_post_processing_in_order(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T3")

        assert order == ["T3", "T4", "T5", "restore", "run_pipeline"]

    def test_post_processing_run_pipeline_uses_none_rebuild_and_skip_fetch(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T3")

        assert len(run_pipeline_calls) == 1
        call = run_pipeline_calls[0]
        assert call["rebuild_from"] is None
        assert call["categories"] is None
        assert call["skip_fetch"] is True
        assert call["recalculate_all"] is False


class TestRebuildFromT4:
    def test_skips_t3_calls_t4_t5_restore_then_post_processing(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T4")

        assert order == ["T4", "T5", "restore", "run_pipeline"]
        assert len(run_pipeline_calls) == 1


class TestRebuildFromT5:
    def test_skips_t3_and_t4_calls_t5_restore_then_post_processing(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T5")

        assert order == ["T5", "restore", "run_pipeline"]
        assert len(run_pipeline_calls) == 1


class TestPostProcessingSkipSyncPropagation:
    """後処理の `run_pipeline` に呼び出し元の `--skip-sync` がそのまま渡ること
    （`refresh_T3Table.bat` が `--skip-sync` を渡さない＝T1 を走らせる想定と対称。
    §7-6(2)）。
    """

    def test_skip_sync_false_is_passed_through(self, call_order):
        _, run_pipeline_calls = call_order

        _call("T3", skip_sync=False)

        assert run_pipeline_calls[-1]["skip_sync"] is False

    def test_skip_sync_true_is_passed_through(self, call_order):
        _, run_pipeline_calls = call_order

        _call("T3", skip_sync=True)

        assert run_pipeline_calls[-1]["skip_sync"] is True


class TestRebuildFromOtherOrNone:
    """T2 指定や通常実行（rebuild_from 無し）は、`_run_rebuild_or_pipeline` 自身が
    従来どおり `run_pipeline()` を呼ぶ（Parquet 再計算・復元は経由しない）。
    """

    def test_rebuild_from_t2_calls_run_pipeline_and_not_parquet_recompute(self, call_order):
        order, run_pipeline_calls = call_order

        _call("T2", categories=["AI"])

        assert order == ["run_pipeline"]
        assert len(run_pipeline_calls) == 1
        assert run_pipeline_calls[0]["rebuild_from"] == "T2"
        assert run_pipeline_calls[0]["categories"] == ["AI"]

    def test_no_rebuild_from_calls_run_pipeline(self, call_order):
        order, run_pipeline_calls = call_order

        _call(None)

        assert order == ["run_pipeline"]
        assert len(run_pipeline_calls) == 1
        assert run_pipeline_calls[0]["rebuild_from"] is None


class TestCategoryIgnoredForParquetRebuild:
    def test_warns_and_ignores_category_for_t3(self, call_order, caplog):
        order, _ = call_order
        with caplog.at_level(logging.WARNING, logger="test_update_pipeline_rebuild"):
            _call("T3", categories=["AI"])

        assert order == ["T3", "T4", "T5", "restore", "run_pipeline"]
        assert any("--category" in r.message for r in caplog.records)
