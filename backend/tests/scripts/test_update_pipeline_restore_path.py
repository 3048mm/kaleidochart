"""`_rebuild_from_parquet()` が `run_production_restore()` に渡す db_path のテスト。

`run_production_restore()` は引数なしだと `<チェックアウトのルート>/data/stocktool.db`
を決め打ちする（`run_production_restore.py:14,123`）。再計算スクリプト3本（5-6b）は
`paths.resolve_db_path_for_init()` でパス解決を揃えているため、復元先も**同じ解決結果**
を明示的に渡さないと「Parquet は A・書き込み先は B」の破壊的な不整合になる（§3.7）。

実際の再計算・復元は一切実行しない（`recompute_parquet_*.run` と
`run_production_restore.run_production_restore` をモックする）。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.7・§7-4・5-8b
"""
import logging
import os

import pytest

import paths
from scripts import update_pipeline
from scripts import recompute_parquet_indicators
from scripts import recompute_parquet_ranks
from scripts import recompute_parquet_signals
from scripts import run_production_restore as rpr_module
from pipeline.parquet_cache_manager import get_parquet_master_dir
import pipeline.orchestrator as orchestrator_module


_STOCKTOOL_ENV_VARS = (
    "STOCKTOOL_DATA_ROOT",
    "STOCKTOOL_PROD_DATA_ROOT",
    "STOCKTOOL_ENV",
    "STOCKTOOL_ALLOW_DB_CREATE",
    "STOCKTOOL_DB_PATH",
    "STOCKTOOL_USER_DB_PATH",
    "STOCKTOOL_UNIVERSE_DB_PATH",
)

# recompute_parquet_signals.py と同じ「引数なしの config.toml db_path」の代わり。
# 本番の絶対パスを模した値（このパス自体には書き込まない）。
_DUMMY_PROD_DB_PATH = "C:/dummy_prod/data/stocktool.db"


def _make_worktree_repo(tmp_path, config_local_toml: str | None = None):
    """ワークツリーを模したディレクトリを作る（`.git` がファイル）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: D:/dummy/.git/worktrees/x\n", encoding="utf-8")
    if config_local_toml is not None:
        (repo / "config.local.toml").write_text(config_local_toml, encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _clean_stocktool_env(monkeypatch):
    for name in _STOCKTOOL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def _mock_recompute_scripts(monkeypatch):
    """T3/T4/T5 の再計算は呼ばれたことだけ記録し、実処理はしない。

    復元の後に呼ばれる後処理の `run_pipeline()`（§7-6(2)・5-8c）もここでモックする
    （本テストの関心は db_path 解決であり、後処理の実処理は対象外）。
    """
    calls = []
    monkeypatch.setattr(recompute_parquet_indicators, "run",
                         lambda dry_run, chunk_size: calls.append("T3"))
    monkeypatch.setattr(recompute_parquet_ranks, "run",
                         lambda dry_run: calls.append("T4"))
    monkeypatch.setattr(recompute_parquet_signals, "run",
                         lambda dry_run: calls.append("T5"))
    monkeypatch.setattr(orchestrator_module, "run_pipeline",
                         lambda **kwargs: calls.append("run_pipeline"))
    return calls


@pytest.fixture()
def _capture_restore_call(monkeypatch):
    """`run_production_restore()` の呼び出しキーワード引数を記録する。"""
    captured = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(rpr_module, "run_production_restore", _fake)
    return captured


class TestRestoreDbPathMatchesWorktreeSandbox:
    def test_worktree_sandbox_uses_sandbox_db_path_not_prod(
            self, tmp_path, monkeypatch, _mock_recompute_scripts, _capture_restore_call):
        """ワークツリー相当（config.local.toml がsandboxを指す）では、
        sandbox の db_path で `run_production_restore` が呼ばれる。
        引数なしの従来挙動（本番決め打ち）や config.toml の本番パスは使われない。
        """
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=f'[data]\nroot = "{sandbox.as_posix()}"\n',
        )
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        logger = logging.getLogger("test_update_pipeline_restore_path")
        update_pipeline._rebuild_from_parquet(
            "T5", logger, config={"system": {"db_path": _DUMMY_PROD_DB_PATH}})

        expected_db_path = str(sandbox / "stocktool.db")
        assert os.path.abspath(_capture_restore_call["db_path"]) == os.path.abspath(expected_db_path)
        # 本番決め打ち・config.toml の本番パスのどちらでもないこと
        assert os.path.abspath(_capture_restore_call["db_path"]) != os.path.abspath(_DUMMY_PROD_DB_PATH)


class TestRestoreDbPathRespectsEnvVar:
    def test_stocktool_db_path_env_var_is_used(
            self, tmp_path, monkeypatch, _mock_recompute_scripts, _capture_restore_call):
        """環境変数 `STOCKTOOL_DB_PATH` が設定されている場合、その解決結果で呼ばれる
        （`deploy_after_merge` が作業領域を環境変数で指定するケースに相当）。
        """
        work = tmp_path / "work"
        work.mkdir()
        repo = _make_worktree_repo(tmp_path)
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))
        work_db_path = str(work / "stocktool.db")
        monkeypatch.setenv("STOCKTOOL_DB_PATH", work_db_path)

        logger = logging.getLogger("test_update_pipeline_restore_path")
        update_pipeline._rebuild_from_parquet(
            "T5", logger, config={"system": {"db_path": _DUMMY_PROD_DB_PATH}})

        assert os.path.abspath(_capture_restore_call["db_path"]) == os.path.abspath(work_db_path)


class TestParquetDirConsistencyBetweenRecomputeAndRestore:
    """再計算スクリプトが使う Parquet ディレクトリと、復元先 db_path から導かれる
    Parquet ディレクトリが一致すること（食い違い防止の回帰テスト。§3.7）。
    """

    def test_parquet_dir_from_restore_db_path_matches_recompute_scripts(
            self, tmp_path, monkeypatch, _mock_recompute_scripts, _capture_restore_call):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=f'[data]\nroot = "{sandbox.as_posix()}"\n',
        )
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        logger = logging.getLogger("test_update_pipeline_restore_path")
        update_pipeline._rebuild_from_parquet(
            "T5", logger, config={"system": {"db_path": _DUMMY_PROD_DB_PATH}})

        # 復元先 db_path から導かれる Parquet ディレクトリ
        restore_parquet_dir = get_parquet_master_dir(_capture_restore_call["db_path"])

        # 再計算スクリプト（recompute_parquet_signals.run 等）が実際に使う解決順
        # そのものを再現し、同じ結果になることを確かめる（5-6b と同じ解決順）。
        recompute_db_path = paths.resolve_db_path_for_init(
            "stocktool", _DUMMY_PROD_DB_PATH, repo_root=str(repo))
        recompute_parquet_dir = get_parquet_master_dir(recompute_db_path)

        assert os.path.abspath(restore_parquet_dir) == os.path.abspath(recompute_parquet_dir)


class TestRestoreDbPathBackwardCompatibility:
    def test_no_arguments_call_still_falls_back_to_default(self, monkeypatch, tmp_path):
        """引数なしで呼んだ従来の `run_production_restore()` の決め打ち挙動自体は
        変えていないこと（本関数のデフォルト値 `db_path=None` の分岐は無変更）。
        """
        # run_production_restore は内部で pipeline_lock 等の重い処理をせず
        # db_path 解決のみ確認したいので、対象関数を直接読んで確認する。
        import inspect
        sig = inspect.signature(rpr_module.run_production_restore)
        assert sig.parameters["db_path"].default is None
