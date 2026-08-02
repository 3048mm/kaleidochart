"""再構築前の Parquet 退避のテスト（scripts/archive_parquet_master.py）

この処理は完全再構築の直前に走る「最後の砦」。ここが黙って失敗すると、
上流（Yahoo）が系列を切り落とした銘柄の履歴が復元不能になる
（2026-08-02 に17銘柄で発生。詳細は doc/issue_list.md）。
"""

import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.archive_parquet_master import ARCHIVE_PREFIX, archive  # noqa: E402


def _make_master(data_dir, files=("prices_1.parquet", "latest_master.json")):
    d = data_dir / "parquet_master"
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("dummy", encoding="utf-8")
    return d


def test_moves_instead_of_deleting(tmp_path):
    """退避後、元の場所は消え、中身は退避先に残っていること。

    コピーではなく move。「退避したつもりで元が残っている」と再構築が
    旧世代を拾ってしまうため。
    """
    src = _make_master(tmp_path)
    dst = archive(str(tmp_path))

    assert dst is not None
    assert not src.exists(), "元ディレクトリが残っている（move ではなく copy になっている）"
    assert os.path.basename(dst).startswith(ARCHIVE_PREFIX)
    assert sorted(os.listdir(dst)) == ["latest_master.json", "prices_1.parquet"]


def test_dry_run_changes_nothing(tmp_path):
    src = _make_master(tmp_path)
    assert archive(str(tmp_path), dry_run=True) is None
    assert src.exists()
    assert list(tmp_path.iterdir()) == [src]


def test_missing_master_is_not_an_error(tmp_path):
    """対象が無いときは黙ってスキップする（再実行可能であること）。

    ここで例外を投げると、退避済みの状態から再構築をやり直せなくなる。
    """
    assert archive(str(tmp_path)) is None


def test_archive_name_does_not_collide_with_existing(tmp_path):
    """既存の退避ディレクトリがあっても上書きしないこと。

    上書きすると「前回の再構築前の状態」を失う。
    """
    (tmp_path / f"{ARCHIVE_PREFIX}20260101_000000").mkdir(parents=True)
    _make_master(tmp_path)

    dst = archive(str(tmp_path))
    assert dst is not None
    assert (tmp_path / f"{ARCHIVE_PREFIX}20260101_000000").exists(), "既存の退避先が消えている"
    assert os.path.basename(dst) != f"{ARCHIVE_PREFIX}20260101_000000"


def test_failure_propagates(tmp_path, monkeypatch):
    """移動に失敗したら例外を伝播すること（呼び出し元が再構築を中止できるように）。

    ここを握り潰すと、退避できていないのに再構築が進んでしまう。
    """
    _make_master(tmp_path)

    import scripts.archive_parquet_master as mod

    def boom(*a, **kw):
        raise PermissionError("locked by another process")

    monkeypatch.setattr(mod.shutil, "move", boom)
    with pytest.raises(PermissionError):
        archive(str(tmp_path))
