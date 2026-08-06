"""パイプラインの排他ロック（`update_pipeline.lock`）を共有するための小さな部品。

## なぜ要るか

Parquet マスタを書き換える処理は複数ある。

    update_pipeline.py            日次・再構築
    weekly_maintenance.py         週次
    truncate_symbol_history.py    逆さ合併の切り詰め
    restore_truncated_symbol_history.py / restore_fx_from_generation.py
    recompute_parquet_ranks.py    T4 全期間再計算

このうち**ロックを取っていたのは前2つだけ**だった。手動スクリプトは無防備で、
2026-08-06 に実際に衝突した:

    13:00:14  スケジューラの日次更新が開始（こちらは知らない）
    13:03〜   手動で履歴復元 → fx 復元 → T4 再計算（新しい id 体系の世代を作る）
    13:10:02  日次更新が完了し、**旧 id 体系の SQLite をマージした世代**で
              latest_master.json を奪う

結果、**symbols は旧 id・prices は新 id** という世代が本番ポインタになり、
`CAT` の終値が 64.13（実際は 871.08）になるなど全銘柄がずれた。

`latest_master.json` の更新自体はアトミックだが、**「読んで・作って・差し替える」の
一連が排他されていない**ため、後勝ちで壊れる。

## 使い方

```python
from pipeline.pipeline_lock import pipeline_lock

with pipeline_lock("recompute_parquet_ranks"):
    ...  # Parquet を書き換える処理
```

取得できなければ `PipelineLockBusy` を送出する。**待たない。**
数時間かかる再構築の裏で黙って待ち続けるより、すぐ落として理由を見せる方がよい。
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_LOCK_FILE = os.path.join(_project_root, "update_pipeline.lock")


class PipelineLockBusy(RuntimeError):
    """他のパイプライン処理が実行中。"""


def _lock_impl():
    """Windows は msvcrt、その他は fcntl。テストと CI の両方で動くように分ける。"""
    if sys.platform == "win32":
        import msvcrt

        def acquire(fd):
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

        def release(fd):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire(fd):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release(fd):
            fcntl.flock(fd, fcntl.LOCK_UN)

    return acquire, release


@contextmanager
def pipeline_lock(owner: str, lock_file: str | None = None):
    """パイプラインの排他ロックを取る。取れなければ `PipelineLockBusy`。

    Args:
        owner: ログに出す実行主体の名前（どの処理が握ったか分かるように）。
        lock_file: 既定は `update_pipeline.lock`。テストは一時ファイルを渡す。
    """
    path = lock_file or DEFAULT_LOCK_FILE
    acquire, release = _lock_impl()
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            acquire(fd)
        except (IOError, OSError) as e:
            raise PipelineLockBusy(
                f"他のパイプライン処理が実行中のため {owner} を中止しました"
                f"（{os.path.basename(path)}）。"
                f" 日次更新・週次メンテ・再構築の完了を待ってから再実行してください。"
            ) from e
        yield
    finally:
        try:
            release(fd)
        except Exception:  # noqa: BLE001 — 解放失敗でプロセスを落とさない
            pass
        os.close(fd)
