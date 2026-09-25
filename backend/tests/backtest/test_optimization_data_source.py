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


def test_resolve_all_child_data_source_returns_backup_name(monkeypatch):
    """--strategy all: 参照先が backup に解決されたら、確定したバックアップ名を子へ渡す（R4）。"""
    monkeypatch.setattr(
        "pipeline.parquet_cache_manager.resolve_backtest_data_source",
        lambda data_source, active_db_path=None: (
            {"prices": "x.parquet"},
            {"data_source": "backup", "backup_name": "_bk_20260925_173413", "parquet_generation": "g"},
        ),
    )

    child_data_source = opt_runner.resolve_all_child_data_source(None, active_db_path="dummy")

    assert child_data_source == "_bk_20260925_173413"


def test_resolve_all_child_data_source_returns_latest_and_warns(monkeypatch, caplog):
    """参照先が latest に解決されたら 'latest' をそのまま返し、世代が変わりうる旨を警告する（R4）。"""
    monkeypatch.setattr(
        "pipeline.parquet_cache_manager.resolve_backtest_data_source",
        lambda data_source, active_db_path=None: (
            {"prices": "x.parquet"},
            {"data_source": "latest", "backup_name": None, "parquet_generation": "g2"},
        ),
    )

    with caplog.at_level("WARNING"):
        child_data_source = opt_runner.resolve_all_child_data_source(None, active_db_path="dummy")

    assert child_data_source == "latest"
    assert any("latest" in rec.message for rec in caplog.records)


def test_main_resolves_data_source_via_resolve_backtest_db_path(monkeypatch):
    """main() は config.toml の DB パスを直接渡さず、resolve_backtest_db_path 経由で
    Sandbox 隔離（STOCKTOOL_DB_PATH 等）を反映した値を active_db_path として渡す（R1）。"""
    seen_active_db_paths = []

    def fake_resolve_data_source(data_source, active_db_path=None):
        seen_active_db_paths.append(active_db_path)
        raise RuntimeError("stop before actual optimization run")

    monkeypatch.setattr(opt_runner, "resolve_data_source", fake_resolve_data_source)
    monkeypatch.setattr(opt_runner, "resolve_backtest_db_path", lambda *a, **k: "isolated_db_path")
    monkeypatch.setattr(opt_runner, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(
        opt_runner.sys, "argv",
        ["optimization_runner.py", "--strategy", "F_elite_momentum97", "--trials", "1"],
    )

    with pytest.raises(RuntimeError):
        opt_runner.main()

    # config.toml の DB パス（db_path_main）そのものではなく、resolve_backtest_db_path の
    # 戻り値（Sandbox 隔離を反映済み）が active_db_path として渡っていること。
    assert seen_active_db_paths == ["isolated_db_path"]


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
