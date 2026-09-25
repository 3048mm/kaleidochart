"""シナリオバッチの子プロセスが master_files を受け取り、
探索・ポインタ読みを一切しないことのテスト（backtest_stable_data_plan.md §3-C）。
"""
import os
import sys

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest.run_scenario_batch as rsb  # noqa: E402


def test_run_single_mc_scenario_passes_master_files_to_preload_without_lookup(monkeypatch):
    """親から渡された master_files を preload_data に渡し、
    resolve_backtest_data_source（探索・ポインタ読み）は呼ばれないこと。"""
    rsb._child_preloaded_data = None  # モジュールグローバルをテスト前にリセット

    calls = []

    def fake_resolve(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("子プロセスで resolve_backtest_data_source が呼ばれてはいけない")

    monkeypatch.setattr(
        "pipeline.parquet_cache_manager.resolve_backtest_data_source", fake_resolve
    )

    seen_master_files = []

    def fake_preload_data(engine, start_date, end_date, refresh_cache=False,
                           data_source="backup", master_files=None):
        seen_master_files.append(master_files)
        return ("symbols", "prices", "indicators", "ranks", "tc", ["date1"])

    monkeypatch.setattr(rsb, "preload_data", fake_preload_data)

    def fake_run_scenario_test(**kwargs):
        return {"summary": {}}

    monkeypatch.setattr(rsb, "run_scenario_test", fake_run_scenario_test)

    sentinel_files = {"prices": "x.parquet"}
    result = rsb.run_single_mc_scenario(
        "A_test", "full_position", 0,
        "2022-01-01", "2022-06-30",
        "dummy_preset.toml", project_root,
        master_files=sentinel_files,
    )

    assert seen_master_files == [sentinel_files]
    assert calls == []
    assert result is not None

    rsb._child_preloaded_data = None  # 後始末（他テストへ漏れないように）
