"""preload_data() が暗黙に本番 Parquet を書き換えないことの回帰テスト。

背景（issue_list P1 / 2026-09-09 対応）:
旧実装は「世代ポインタが読めない」というだけで rotate_and_archive_to_parquet() を
自動で呼んでいた。ローテートは旧世代とのマージを伴うため、ポインタが壊れていると
マージ元を見失い、SQLite のホット期間（730日）だけの世代を公開して全期間履歴を失う。

    FileNotFoundError: Parquet master cache files not found at ...\\parquet_master!

つまり **読み取り専用のはずのバックテストを1本走らせるだけで本番 Parquet が
切り詰められうる**状態だった。再生成の入口は明示指定（--refresh-cache）だけに絞る。
"""
import os

import pytest

BOM_BYTES = bytes([0xEF, 0xBB, 0xBF])


@pytest.fixture
def sandbox_paths(tmp_path, monkeypatch):
    """バックテストが掴む DB パスを tmp へ向け、parquet_master を作る。"""
    db_path = tmp_path / "stocktool_sandbox.db"
    db_path.write_bytes(b"")  # 実体は読まない（Parquet 到達前に落ちる想定）
    parquet_dir = tmp_path / "parquet_master"
    parquet_dir.mkdir()
    monkeypatch.setenv("STOCKTOOL_DB_PATH", str(db_path))
    return db_path, parquet_dir


@pytest.fixture
def rotate_spy(monkeypatch):
    """rotate_and_archive_to_parquet の呼び出し回数を数える。"""
    import backend.pipeline.parquet_cache_manager as pcm

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("rotate_and_archive_to_parquet が呼ばれてはいけない")

    monkeypatch.setattr(pcm, "rotate_and_archive_to_parquet", _spy)
    return calls


def test_no_regenerate_when_pointer_absent(sandbox_paths, rotate_spy):
    """ポインタが無いだけで書き込み（ローテート）に入らないこと。

    案内は「パイプラインを回すか --refresh-cache を付ける」でなければならない。
    """
    from backend.backtest.backtest_runner import preload_data

    with pytest.raises(FileNotFoundError) as ei:
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=False)

    assert rotate_spy == [], "読み取り専用のはずの経路が本番 Parquet を書き換えた"
    assert "--refresh-cache" in str(ei.value), "誤誘導のメッセージのままになっている"


def test_no_regenerate_when_pointer_unreadable(sandbox_paths, rotate_spy):
    """ポインタが「存在するのに読めない」ときは、無関係な FileNotFoundError にせず
    読み込み失敗として落とすこと（実体は存在するのに『無い』と言わない）。"""
    from backend.backtest.backtest_runner import preload_data
    from backend.pipeline.parquet_cache_manager import ParquetPointerUnreadableError

    _, parquet_dir = sandbox_paths
    pointer = parquet_dir / "latest_master.json"
    pointer.write_bytes(BOM_BYTES + b'{"prices": "x.parquet"}')

    with pytest.raises(ParquetPointerUnreadableError):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=False)

    assert rotate_spy == [], "壊れたポインタを理由に書き込みへ入った"


def test_explicit_refresh_cache_still_regenerates(sandbox_paths, monkeypatch):
    """明示指定（--refresh-cache）は従来どおり再生成の入口として機能すること。"""
    import contextlib

    import backend.db.database as db_module
    import backend.pipeline.parquet_cache_manager as pcm
    from backend.backtest.backtest_runner import preload_data

    calls = []
    monkeypatch.setattr(pcm, "rotate_and_archive_to_parquet",
                        lambda *a, **k: calls.append(1))
    # 空の DB ファイルではセッションを張れないので、DB 接続自体もモックする
    monkeypatch.setattr(db_module, "get_db",
                        lambda *a, **k: contextlib.nullcontext(object()))

    # 再生成をモックしたのでポインタは作られない。到達点は「呼ばれたか」だけ見る。
    with pytest.raises((FileNotFoundError, Exception)):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True)

    assert calls, "--refresh-cache でも再生成が呼ばれなくなっている"


def test_sandbox_paths_are_isolated(sandbox_paths):
    """本テストが本番 data/ を触っていないことの自己確認（独立経路）。"""
    db_path, parquet_dir = sandbox_paths
    assert "sandbox" in str(db_path)
    assert not os.path.exists(os.path.join(str(parquet_dir), "prices_x.parquet"))
