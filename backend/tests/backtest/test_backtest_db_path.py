"""resolve_backtest_db_path() の回帰テスト。

2026-08-26 の事故: `STOCKTOOL_DB_PATH` を Sandbox に向けて最適化を回したのに、
バックテストは **本番 Parquet を読んでいた**。

原因は import 形式の混在（CLAUDE.md「import 規約」）。
  - backtest_runner は `backend.db.database` を import
  - optimization_runner は `db.database`（PYTHONPATH=backend 形式）で init_db
Python はこの2つを別モジュール実体として扱うため `get_active_db_path()` が
None を返し、環境変数を無視して config.toml の本番 DB へ落ちていた。

**新カラムが無ければ黙って本番データで最適化が回る**状態だったので、
「環境変数が最優先で勝つ」ことをテストで固定する。
"""
import os

import pytest

from backend.backtest.backtest_runner import resolve_backtest_db_path

SANDBOX = os.path.join('data', 'sandbox', 'stocktool_sandbox.db')
PROD = os.path.join('data', 'stocktool.db')


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv('STOCKTOOL_DB_PATH', raising=False)


def test_env_var_wins_over_uninitialised_module(monkeypatch):
    """init_db が別モジュール実体で呼ばれていても（=None）、環境変数で Sandbox を掴む。

    これが事故の本体。ここが破れると Sandbox 指定が無言で無効になる。
    """
    monkeypatch.setenv('STOCKTOOL_DB_PATH', SANDBOX)

    assert resolve_backtest_db_path(None) == SANDBOX


def test_env_var_wins_over_initialised_production_path(monkeypatch):
    """init_db 済みでも、環境変数と食い違うなら環境変数を優先する。

    「初期化はしたが本番を指していた」も隔離が破れている状態なので、
    黙って本番を読ませない。
    """
    monkeypatch.setenv('STOCKTOOL_DB_PATH', SANDBOX)

    assert resolve_backtest_db_path(PROD) == SANDBOX


def test_warns_when_initialised_path_disagrees(monkeypatch, caplog):
    """食い違いは警告として見えること（黙って優先しない）。"""
    monkeypatch.setenv('STOCKTOOL_DB_PATH', SANDBOX)

    with caplog.at_level('WARNING'):
        resolve_backtest_db_path(PROD)

    assert any('STOCKTOOL_DB_PATH' in r.getMessage() for r in caplog.records), \
        '食い違いの警告が出ていない'


def test_no_warning_when_paths_agree(monkeypatch, caplog):
    monkeypatch.setenv('STOCKTOOL_DB_PATH', SANDBOX)

    with caplog.at_level('WARNING'):
        resolve_backtest_db_path(SANDBOX)

    assert not caplog.records


def test_falls_back_to_initialised_path_without_env():
    """環境変数が無ければ init_db の値を使う（従来どおり）。"""
    assert resolve_backtest_db_path(PROD) == PROD


def test_falls_back_to_config_when_nothing_set():
    """何も無ければ config.toml。少なくとも .db を指す文字列が返る。"""
    resolved = resolve_backtest_db_path(None)

    assert isinstance(resolved, str) and resolved.endswith('.db')
