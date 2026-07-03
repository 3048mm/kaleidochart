"""
database.py の並行動作テスト。

背景: T4 更新（長時間の書き込みトランザクション）中に API の読み取りが
ブロックされ、ダッシュボードが表示できなくなる問題の回帰テスト。
WAL モードでは「読み取りは書き込みをブロックしない」が保証されるべきであり、
API の読み取りセッションが書き込みロックを要求してはならない。

テストは pytest の一時ディレクトリに作る使い捨てサンドボックス DB を使用し、
本番 DB (data/stocktool.db) には一切触れない。
"""
import sqlite3
import threading
import time

import pytest
from sqlalchemy import text

import db.database as database_module


@pytest.fixture()
def sandbox_db(tmp_path, monkeypatch):
    """使い捨てサンドボックス DB を初期化し、テスト後にモジュール状態を復元する。"""
    monkeypatch.delenv("STOCKTOOL_DB_PATH", raising=False)

    saved_state = {
        name: getattr(database_module, name, None)
        for name in ("engine", "SessionLocal", "_active_db_path",
                     "write_engine", "SessionLocalWrite")
    }

    db_path = str(tmp_path / "stocktool_sandbox_test.db")
    database_module.init_db(db_path)

    # テスト専用のスクラッチテーブル（モデルスキーマに依存しない）
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS t_lock_test (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t_lock_test (v) VALUES ('baseline')")
    conn.commit()
    conn.close()

    yield db_path

    # エンジンを破棄してファイルハンドルを解放（Windows のロック回避）
    for name in ("engine", "write_engine"):
        eng = getattr(database_module, name, None)
        if eng is not None:
            eng.dispose()
    for name, value in saved_state.items():
        setattr(database_module, name, value)


def test_read_not_blocked_by_long_write_transaction(sandbox_db):
    """パイプライン相当の書き込みトランザクション保持中でも、読み取りは即座に完了する。

    T4 フェーズは Delete-Insert を単一トランザクションで数分間保持する。
    その間、API の SELECT は WAL スナップショットにより更新前のデータを
    ブロックなしで返さなければならない。
    """
    # Arrange: 別コネクションが書き込みロックを保持（T4 の Delete-Insert を模倣）
    writer = sqlite3.connect(sandbox_db, timeout=5)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE t_lock_test SET v = 'modified-uncommitted'")

    result = {}

    def read_via_api_session():
        start = time.monotonic()
        with database_module.get_db() as db:
            rows = db.execute(text("SELECT v FROM t_lock_test")).fetchall()
        result["rows"] = rows
        result["elapsed"] = time.monotonic() - start

    try:
        # Act: API と同じ経路 (get_db) で読み取り
        reader = threading.Thread(target=read_via_api_session, daemon=True)
        reader.start()
        reader.join(timeout=8)

        # Assert: ブロックされずに完了し、更新前のスナップショットが見える
        assert not reader.is_alive(), (
            "読み取りが書き込みロックにブロックされた: "
            "API セッションが SELECT で書き込みロックを要求している"
        )
        assert result["elapsed"] < 5, f"読み取りに {result['elapsed']:.1f}s かかった"
        assert result["rows"] == [("baseline",)], "未コミットの書き込みが見えてはならない"
    finally:
        writer.rollback()
        writer.close()


def test_write_session_acquires_write_lock_at_begin(sandbox_db):
    """書き込みセッションはトランザクション開始時点で書き込みロックを確保する。

    パイプライン（唯一の書き込み主体）は BEGIN IMMEDIATE で開始することで、
    「読み取り→書き込み」のロック昇格デッドロックや BUSY_SNAPSHOT を防ぐ。
    ロックを取れていれば、他の書き込み希望者は即座に locked になるはず。
    """
    with database_module.get_write_db() as db:
        # SELECT だけでトランザクションが開始され、書き込みロックが確保される
        db.execute(text("SELECT count(*) FROM t_lock_test"))

        rival = sqlite3.connect(sandbox_db, timeout=0.5)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                rival.execute("BEGIN IMMEDIATE")
        finally:
            rival.close()


def test_api_read_not_blocked_while_write_session_active(sandbox_db):
    """本番の実組み合わせ: get_write_db(パイプライン) 実行中も get_db(API) は読める。"""
    with database_module.get_write_db() as write_db:
        write_db.execute(text("UPDATE t_lock_test SET v = 'pipeline-writing'"))
        # コミット前 = 書き込みトランザクション保持中

        result = {}

        def read_via_api_session():
            with database_module.get_db() as db:
                result["rows"] = db.execute(text("SELECT v FROM t_lock_test")).fetchall()

        reader = threading.Thread(target=read_via_api_session, daemon=True)
        reader.start()
        reader.join(timeout=8)

        assert not reader.is_alive(), "API 読み取りがパイプライン書き込みにブロックされた"
        assert result["rows"] == [("baseline",)], "未コミットの書き込みが見えてはならない"

        write_db.rollback()


def test_wal_mode_enabled_for_both_engines(sandbox_db):
    """読み取り・書き込み両エンジンで WAL モードが有効であること（architecture.md §10）。"""
    with database_module.get_db() as db:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    with database_module.get_write_db() as db:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_busy_timeout_contract(sandbox_db):
    """読み取りは短い busy_timeout（障害を即検知）、書き込みは長い busy_timeout（バッチ耐性）。"""
    with database_module.get_db() as db:
        read_timeout_ms = db.execute(text("PRAGMA busy_timeout")).scalar()
        assert read_timeout_ms <= 60_000, (
            f"読み取りの busy_timeout が {read_timeout_ms}ms — "
            "長すぎるとロック競合時に無限ローディングに見える"
        )
    with database_module.get_write_db() as db:
        write_timeout_ms = db.execute(text("PRAGMA busy_timeout")).scalar()
        assert write_timeout_ms >= 600_000, (
            f"書き込みの busy_timeout が {write_timeout_ms}ms — "
            "長時間バッチ同士の競合を待機できない"
        )
