"""optimization_runner の Parquet 参照先の解決（backtest_stable_data_plan.md §3-C）に関するテスト。

開始時に1回だけ resolve_backtest_data_source を呼び、以降の get_cached_data（期間ごとの
preload_data 呼び出し）は同じ master_files を使い回す（再探索・ポインタ再読みをしない）こと。
"""
import pytest

import optimization_runner as opt_runner


@pytest.fixture(autouse=True)
def _reset_globals():
    """モジュールグローバルをテスト間で漏らさない。"""
    opt_runner._cached_data_dict = {}
    opt_runner._resolved_master_files = None
    opt_runner._resolved_data_source_meta = None
    yield
    opt_runner._cached_data_dict = {}
    opt_runner._resolved_master_files = None
    opt_runner._resolved_data_source_meta = None


def test_resolve_data_source_sets_module_globals(monkeypatch):
    sentinel_files = {"prices": "x.parquet"}
    sentinel_meta = {"data_source": "backup", "backup_name": "_bk_x", "parquet_generation": "g"}

    monkeypatch.setattr(
        "pipeline.parquet_cache_manager.resolve_backtest_data_source",
        lambda data_source, active_db_path=None: (sentinel_files, sentinel_meta),
    )

    files, meta = opt_runner.resolve_data_source("backup")

    assert files is sentinel_files
    assert meta is sentinel_meta
    assert opt_runner._resolved_master_files is sentinel_files
    assert opt_runner._resolved_data_source_meta is sentinel_meta


def test_get_cached_data_reuses_resolved_master_files_across_periods(monkeypatch):
    """resolve_data_source で解決した後は、期間が変わっても同じ master_files を preload_data に渡す。"""
    sentinel_files = {"prices": "x.parquet"}
    monkeypatch.setattr(
        "pipeline.parquet_cache_manager.resolve_backtest_data_source",
        lambda data_source, active_db_path=None: (sentinel_files, {"data_source": data_source, "backup_name": None, "parquet_generation": None}),
    )
    opt_runner.resolve_data_source("backup")

    seen_master_files = []

    def fake_preload_data(engine, start_date, end_date, refresh_cache=False, data_source="backup", master_files=None):
        seen_master_files.append(master_files)
        return ("symbols", "prices", "indicators", "ranks", "tc", ["date1"])

    monkeypatch.setattr(opt_runner, "preload_data", fake_preload_data)

    opt_runner.get_cached_data({}, "2020-01-01", "2020-06-30")
    opt_runner.get_cached_data({}, "2021-01-01", "2021-06-30")

    assert seen_master_files == [sentinel_files, sentinel_files]
