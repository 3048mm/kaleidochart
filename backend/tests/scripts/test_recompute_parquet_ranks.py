"""Parquet の T4（relative_ranks）全期間再計算（`recompute_parquet_ranks.py`）の
パス解決・書き込みガードのテスト（§3.6・5-6b）。

`run()` のランク計算ロジック自体（`parquet_recompute.recompute_ranks`）は
別テストの対象。ここではワークツリー隔離の配線のみを検証する。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.6
"""
import json
import os

import pandas as pd
import pytest

import paths
from pipeline.parquet_recompute import INDICATORS_TO_RANK
from scripts import recompute_parquet_ranks as rpr

_STOCKTOOL_ENV_VARS = (
    "STOCKTOOL_DATA_ROOT",
    "STOCKTOOL_PROD_DATA_ROOT",
    "STOCKTOOL_ENV",
    "STOCKTOOL_ALLOW_DB_CREATE",
    "STOCKTOOL_DB_PATH",
    "STOCKTOOL_USER_DB_PATH",
    "STOCKTOOL_UNIVERSE_DB_PATH",
)


def _make_worktree_repo(tmp_path, config_local_toml: str | None = None):
    """ワークツリーを模したディレクトリを作る（`.git` がファイル）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: D:/dummy/.git/worktrees/x\n", encoding="utf-8")
    if config_local_toml is not None:
        (repo / "config.local.toml").write_text(config_local_toml, encoding="utf-8")
    return repo


def _make_minimal_parquet_master(parquet_dir):
    """guard 到達までに読まれる最小限の Parquet 世代を作る。

    `run()` はガード前に symbols / indicators / ranks を読む
    （`prices` / `tc` / `signals` / `fx` はコピーのみで、ガード後にしか使わない）。
    0行の indicators/ranks でも `recompute_ranks` は空フレームを返すだけで
    実データは不要（`pipeline/parquet_recompute.py::recompute_ranks` 参照）。
    """
    os.makedirs(parquet_dir, exist_ok=True)
    sym = pd.DataFrame({"id": [1], "ticker": ["SPY"], "category": ["指数"], "active": [1]})

    ind = pd.DataFrame({"symbol_id": pd.Series([], dtype="int64"),
                         "date": pd.Series([], dtype="object")})
    for col, _ in INDICATORS_TO_RANK:
        ind[col] = pd.Series([], dtype="float64")

    old = pd.DataFrame({"id": pd.Series([], dtype="int64"),
                         "symbol_id": pd.Series([], dtype="int64"),
                         "date": pd.Series([], dtype="object")})
    for _, col in INDICATORS_TO_RANK:
        old[col] = pd.Series([], dtype="float64")

    ts = "20200101_000000"
    files = {}
    for key, df in (("symbols", sym), ("indicators", ind), ("ranks", old)):
        path = os.path.join(parquet_dir, f"{key}_{ts}.parquet")
        df.to_parquet(path, index=False)
        files[key] = path
    for key, base in (("prices", "prices"), ("tc", "theme_constituents"),
                       ("signals", "market_signals"), ("fx", "fx_rates")):
        path = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        with open(path, "wb") as f:
            f.write(b"placeholder")
        files[key] = path

    pointer = os.path.join(parquet_dir, "latest_master.json")
    with open(pointer, "w", encoding="utf-8") as f:
        json.dump(files, f)
    return files


class TestRunPathIsolation:
    """`run()` のパス解決が worktree / 環境変数を尊重すること（§3.6・5-6b）。

    実行環境の実際の worktree/本体判定に依存しないよう、`paths.get_repo_root`
    を偽の worktree に差し替えて検証する（main へ merge 後の実行でも決定的）。
    """

    @pytest.fixture(autouse=True)
    def _clean_stocktool_env(self, monkeypatch):
        for name in _STOCKTOOL_ENV_VARS:
            monkeypatch.delenv(name, raising=False)

    def test_apply_from_worktree_pointing_at_prod_raises_production_write_error(
            self, tmp_path, monkeypatch):
        """本番データを指すワークツリーから書き込み経路に入ると拒否される。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=(
                f'[data]\nroot = "{prod.as_posix()}"\nprod_root = "{prod.as_posix()}"\n'
            ),
        )
        parquet_dir = prod / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        with pytest.raises(paths.ProductionWriteError):
            rpr.run(dry_run=False)

        assert len(list(parquet_dir.glob("ranks_*.parquet"))) == 1  # 元の1件のみ

    def test_apply_with_stocktool_db_path_env_uses_work_dir_not_prod(
            self, tmp_path, monkeypatch):
        """`STOCKTOOL_DB_PATH` を設定すると、本番ではなく作業領域が使われる。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        repo = _make_worktree_repo(tmp_path)
        parquet_dir = work / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))
        monkeypatch.setenv("STOCKTOOL_DB_PATH", str(work / "stocktool.db"))
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))

        rpr.run(dry_run=False)  # 例外を投げずに完走する

        assert len(list(parquet_dir.glob("ranks_*.parquet"))) == 2  # 元の1件 + 新世代
        assert list(prod.iterdir()) == []

    def test_dry_run_does_not_write_even_when_pointed_at_prod(
            self, tmp_path, monkeypatch):
        """`--dry-run` は書き込みガードの有無に関係なく書き込まない。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=(
                f'[data]\nroot = "{prod.as_posix()}"\nprod_root = "{prod.as_posix()}"\n'
            ),
        )
        parquet_dir = prod / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        before = set(os.listdir(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        rpr.run(dry_run=True)  # 例外にならない（読み取りのみ）

        after = set(os.listdir(parquet_dir))
        assert after == before
