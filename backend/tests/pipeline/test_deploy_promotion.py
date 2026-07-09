# deploy_promotion（昇格用の Parquet 世代コピー・swap・ロールバック）のテスト
import json
import os

import pytest

from pipeline.deploy_promotion import (
    copy_generation,
    generation_timestamp,
    promote_generation,
    read_generation,
    rollback_generation,
)

import logging

logger = logging.getLogger(__name__)

TS = "20260623_124055"


def _make_generation(parquet_dir, ts=TS, content=b"dummy"):
    """ダミーの Parquet 世代（ポインタ + 実体ファイル）を作る"""
    os.makedirs(parquet_dir, exist_ok=True)
    pointer = {}
    for key in ("symbols", "prices", "indicators", "ranks", "tc", "signals"):
        name = f"{key}_{ts}.parquet"
        path = os.path.join(parquet_dir, name)
        with open(path, "wb") as f:
            f.write(content)
        pointer[key] = path
    pointer_file = os.path.join(parquet_dir, "latest_master.json")
    with open(pointer_file, "w", encoding="utf-8") as f:
        json.dump(pointer, f)
    return pointer


def test_read_generation_returns_pointer_dict(tmp_path):
    """latest_master.json の内容が dict で読める"""
    src = str(tmp_path / "src")
    made = _make_generation(src)
    got = read_generation(src)
    assert got == made


def test_read_generation_missing_pointer_returns_none(tmp_path):
    """ポインタが無いディレクトリでは None"""
    assert read_generation(str(tmp_path / "empty")) is None


def test_generation_timestamp_extracts_ts():
    """ポインタのファイル名から世代タイムスタンプを抽出できる"""
    pointer = {"prices": f"D:/x/prices_{TS}.parquet"}
    assert generation_timestamp(pointer) == TS


def test_copy_generation_copies_files_and_writes_pointer(tmp_path):
    """コピー先に実体ファイルとポインタが揃い、ポインタはコピー先パスを指す"""
    src = str(tmp_path / "src")
    dst = str(tmp_path / "dst")
    _make_generation(src, content=b"source-data")

    new_pointer = copy_generation(src, dst, logger)

    assert set(new_pointer.keys()) == {"symbols", "prices", "indicators", "ranks", "tc", "signals"}
    for key, path in new_pointer.items():
        assert os.path.dirname(path) == dst
        with open(path, "rb") as f:
            assert f.read() == b"source-data"
    stored = read_generation(dst)
    assert stored == new_pointer


def test_copy_generation_missing_source_raises(tmp_path):
    """コピー元にポインタが無い場合は FileNotFoundError"""
    with pytest.raises(FileNotFoundError):
        copy_generation(str(tmp_path / "no_src"), str(tmp_path / "dst"), logger)


def test_promote_generation_updates_prod_pointer(tmp_path):
    """昇格でワークスペースの世代が本番へコピーされ、ポインタが新世代を指す。旧世代の実体は残る"""
    ws = str(tmp_path / "workspace")
    prod = str(tmp_path / "prod")
    old_ts = "20260101_000000"
    new_ts = "20260709_120000"
    _make_generation(prod, ts=old_ts, content=b"old")
    _make_generation(ws, ts=new_ts, content=b"new")

    old_pointer, new_pointer = promote_generation(ws, prod, logger)

    assert generation_timestamp(old_pointer) == old_ts
    assert generation_timestamp(new_pointer) == new_ts
    # 本番ポインタは新世代を指す
    assert read_generation(prod) == new_pointer
    # 新世代の実体が本番ディレクトリに存在する
    for path in new_pointer.values():
        assert os.path.dirname(path) == prod
        assert os.path.exists(path)
    # 旧世代の実体は削除されない（ロールバック用に保持）
    for path in old_pointer.values():
        assert os.path.exists(os.path.join(prod, os.path.basename(path)))


def test_promote_generation_writes_version_json(tmp_path):
    """昇格時に世代別 data_version_*.json も書かれる（prune 互換）"""
    ws = str(tmp_path / "workspace")
    prod = str(tmp_path / "prod")
    new_ts = "20260709_120000"
    _make_generation(prod, ts="20260101_000000")
    _make_generation(ws, ts=new_ts)

    _, new_pointer = promote_generation(ws, prod, logger)

    version_file = os.path.join(prod, f"data_version_{new_ts}.json")
    assert os.path.exists(version_file)
    with open(version_file, "r", encoding="utf-8") as f:
        assert json.load(f) == new_pointer


def test_promote_generation_without_workspace_raises(tmp_path):
    """ワークスペースに世代が無ければ FileNotFoundError"""
    prod = str(tmp_path / "prod")
    _make_generation(prod)
    with pytest.raises(FileNotFoundError):
        promote_generation(str(tmp_path / "no_ws"), prod, logger)


def test_rollback_generation_restores_old_pointer(tmp_path):
    """ロールバックで本番ポインタが旧世代へ戻る"""
    ws = str(tmp_path / "workspace")
    prod = str(tmp_path / "prod")
    old_ts = "20260101_000000"
    _make_generation(prod, ts=old_ts, content=b"old")
    _make_generation(ws, ts="20260709_120000", content=b"new")

    old_pointer, _ = promote_generation(ws, prod, logger)
    ok = rollback_generation(prod, old_pointer, logger)

    assert ok is True
    assert read_generation(prod) == old_pointer


def test_rollback_generation_with_none_pointer_returns_false(tmp_path):
    """旧ポインタが None（初回昇格等）ならロールバック不能として False"""
    prod = str(tmp_path / "prod")
    _make_generation(prod)
    assert rollback_generation(prod, None, logger) is False
