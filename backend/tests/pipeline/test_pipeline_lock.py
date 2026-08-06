"""パイプライン排他ロックのテスト（pipeline/pipeline_lock.py）

## なぜこの部品を作ったか

Parquet マスタを書き換える処理は6つあるのに、**ロックを取っていたのは
`update_pipeline.py` と `weekly_maintenance.py` の2つだけ**だった。

2026-08-06 に実際に衝突した:

    13:00:14  スケジューラの日次更新が開始
    13:03〜   手動で履歴復元 → fx 復元 → T4 再計算（新 id 体系の世代を作る）
    13:10:02  日次更新が完了し、**旧 id 体系の SQLite をマージした世代**で
              latest_master.json を奪う

結果、symbols は旧 id・prices は新 id という世代が本番ポインタになり、
`CAT` の終値が 871.08 → 64.13 になるなど全銘柄がずれた。

`latest_master.json` の差し替え自体はアトミックだが、
**「読んで・作って・差し替える」の一連が排他されていない**ため後勝ちで壊れる。
"""

import os
import subprocess
import sys
import textwrap

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline.pipeline_lock import PipelineLockBusy, pipeline_lock  # noqa: E402


def test_lock_can_be_acquired_and_released(tmp_path):
    lock = str(tmp_path / "test.lock")

    with pipeline_lock("first", lock):
        pass
    with pipeline_lock("second", lock):   # 解放されていれば再取得できる
        pass


def test_second_holder_is_rejected_immediately(tmp_path):
    """**待たない。** 数時間かかる再構築の裏で黙って待つより、理由を見せて落とす。"""
    lock = str(tmp_path / "test.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, r"{backend_dir}")
            from pipeline.pipeline_lock import pipeline_lock
            with pipeline_lock("holder", r"{lock}"):
                print("locked", flush=True)
                time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked", "先行プロセスがロックを取れていない"

        with pytest.raises(PipelineLockBusy, match="他のパイプライン処理"):
            with pipeline_lock("second", lock):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_lock_is_released_even_when_the_body_raises(tmp_path):
    """処理が例外で落ちてもロックを残さない（残すと以後すべて弾かれる）。"""
    lock = str(tmp_path / "test.lock")

    with pytest.raises(ValueError):
        with pipeline_lock("boom", lock):
            raise ValueError("boom")

    with pipeline_lock("after", lock):
        pass


def test_error_message_names_the_owner(tmp_path):
    """どの処理が弾かれたのかログで分かること。"""
    lock = str(tmp_path / "test.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, r"{backend_dir}")
            from pipeline.pipeline_lock import pipeline_lock
            with pipeline_lock("holder", r"{lock}"):
                print("locked", flush=True)
                time.sleep(30)
        """)],
        stdout=subprocess.PIPE, text=True)
    try:
        holder.stdout.readline()
        with pytest.raises(PipelineLockBusy, match="recompute_parquet_ranks"):
            with pipeline_lock("recompute_parquet_ranks", lock):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_default_lock_file_is_shared_with_the_pipeline():
    """既定のロックが `update_pipeline.lock` であること。

    別ファイルにすると日次更新と排他できず、この仕組みの意味が無くなる。
    """
    from pipeline.pipeline_lock import DEFAULT_LOCK_FILE

    assert os.path.basename(DEFAULT_LOCK_FILE) == "update_pipeline.lock"
