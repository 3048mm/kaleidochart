"""Parquet の T3（indicators）全期間再計算（`recompute_parquet_indicators.py`）の
パス解決・書き込みガードのテスト（§3.6・5-6b）。

`run()` の再計算ロジック自体（`parquet_recompute_chunked`）は別テストの対象。
ここではワークツリー隔離の配線のみを検証する。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.6
"""
import json
import os

import pandas as pd
import pytest

import paths
from scripts import recompute_parquet_indicators as rpi

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
    """dry-run 早期リターン直後の書き込みガードまで到達させる最小限の Parquet 世代。

    `run()` はガード前に symbols / prices / indicators を読む
    （`ranks` / `tc` / `signals` / `fx` はコピーのみで、ガード後にしか使わない）。
    """
    os.makedirs(parquet_dir, exist_ok=True)
    # SPY（指数）と個別銘柄1件。`--verify` の標本抽出（個別のみ）にも使う。
    sym = pd.DataFrame({
        "id": [1, 2], "ticker": ["SPY", "AAA"],
        "category": ["指数", "個別"], "active": [1, 1],
    })
    dates = pd.date_range("2020-01-01", periods=5).strftime("%Y-%m-%d")
    prices = pd.DataFrame({
        "symbol_id": [1] * 5 + [2] * 5,
        "date": list(dates) * 2,
        "open": [100.0] * 10,
        "high": [101.0] * 10,
        "low": [99.0] * 10,
        "close": [100.0, 101.0, 102.0, 101.0, 103.0] * 2,
        "volume": [1_000_000] * 10,
    })
    # 実データと同じ型で1行だけ持たせる（0行の object 列は Parquet 書き出し時に
    # null 型へ推論され、再計算結果の string 列と cast できず落ちるため）。
    indicators = pd.DataFrame({
        "symbol_id": [999],
        "date": ["2019-01-01"],
        "sma_50": [0.0],
    })

    ts = "20200101_000000"
    files = {}
    for key, df in (("symbols", sym), ("prices", prices), ("indicators", indicators)):
        path = os.path.join(parquet_dir, f"{key}_{ts}.parquet")
        df.to_parquet(path, index=False)
        files[key] = path
    for key, base in (("ranks", "ranks"), ("tc", "theme_constituents"),
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
            rpi.run(dry_run=False, chunk_size=rpi.DEFAULT_CHUNK_SIZE)

        assert len(list(parquet_dir.glob("indicators_*.parquet"))) == 1  # 元の1件のみ

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

        rpi.run(dry_run=False, chunk_size=rpi.DEFAULT_CHUNK_SIZE)  # 例外を投げずに完走する

        assert len(list(parquet_dir.glob("indicators_*.parquet"))) == 2  # 元の1件 + 新世代
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

        rpi.run(dry_run=True, chunk_size=rpi.DEFAULT_CHUNK_SIZE)  # 例外にならない（読み取りのみ）

        after = set(os.listdir(parquet_dir))
        assert after == before

    def test_verify_uses_resolved_parquet_dir_not_raw_config_path(
            self, tmp_path, monkeypatch):
        """`--verify`（読み取り専用）もパス解決を尊重すること。"""
        work = tmp_path / "work"
        work.mkdir()
        repo = _make_worktree_repo(tmp_path)
        parquet_dir = work / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))
        monkeypatch.setenv("STOCKTOOL_DB_PATH", str(work / "stocktool.db"))

        # 解決先が work_dir であることが確認できれば十分（例外にならないこと）。
        # 既存 indicators のダミー行とは日付が噛み合わないため突合0件で rc=1 になる。
        rc = rpi.verify(sample=1, chunk_size=rpi.DEFAULT_CHUNK_SIZE)
        assert rc == 1
