"""backtest_runner.py の CLI main(): --db-path 明示時の data_source 既定（backtest_stable_data_plan.md §6.3 R3）。

本番で --db-path を明示しても data_source が既定の 'backup'（常に本番のバックアップ）に
なると、指定した DB の内容が黙って無視され本番を読む。--db-path 明示かつ --data-source
未指定なら 'latest' を既定にすることを固定する。
"""
import sys

import pytest

import backend.backtest.backtest_runner as br


@pytest.fixture(autouse=True)
def _stub_run_backtest(monkeypatch, tmp_path):
    """main() を実データに触れさせず、run_backtest への引数だけを観測する。"""
    calls = []

    def fake_run_backtest(config, strategy_filter=None, refresh_cache=False,
                           db_path_override=None, data_source=None):
        calls.append({
            "strategy_filter": strategy_filter,
            "refresh_cache": refresh_cache,
            "db_path_override": db_path_override,
            "data_source": data_source,
        })

    monkeypatch.setattr(br, "run_backtest", fake_run_backtest)
    return calls


def _run_main_with_argv(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    br.main()


def test_db_path_explicit_defaults_data_source_to_latest(monkeypatch, _stub_run_backtest, tmp_path):
    dummy_db = str(tmp_path / "dummy.db")
    _run_main_with_argv(monkeypatch, [
        "backtest_runner.py", "--db-path", dummy_db,
    ])

    assert len(_stub_run_backtest) == 1
    call = _stub_run_backtest[0]
    assert call["db_path_override"] == dummy_db
    assert call["data_source"] == "latest"


def test_db_path_explicit_does_not_override_explicit_data_source(monkeypatch, _stub_run_backtest, tmp_path):
    dummy_db = str(tmp_path / "dummy.db")
    _run_main_with_argv(monkeypatch, [
        "backtest_runner.py", "--db-path", dummy_db, "--data-source", "backup",
    ])

    assert _stub_run_backtest[0]["data_source"] == "backup"


def test_no_db_path_leaves_data_source_unset(monkeypatch, _stub_run_backtest):
    _run_main_with_argv(monkeypatch, ["backtest_runner.py"])

    assert _stub_run_backtest[0]["db_path_override"] is None
    assert _stub_run_backtest[0]["data_source"] is None
