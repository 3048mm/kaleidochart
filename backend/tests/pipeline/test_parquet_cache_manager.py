# parquet_cache_manager のバルクインサートに関する回帰テスト
#
# 背景（2026-07-09 deploy_after_merge 初回実行で実発生）:
# bulk_insert_df_to_sqlite が PRAGMA journal_mode = MEMORY を実行していたが、
# WAL からの journal_mode 変更は「他の接続が1つでも開いていると」database is locked
# で即失敗する。restore 処理は同一プロセス内で SQLAlchemy セッションと raw 接続の
# 複数接続を持つため、本番/ワークスペースを問わず失敗し得る。
# 対策: バルクインサート中も WAL を維持し、journal_mode を変更しない
# （.claude/skills/sqlite-wal-handling/SKILL.md の規約）。
import logging
import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from pipeline.parquet_cache_manager import bulk_insert_df_to_sqlite

logger = logging.getLogger(__name__)


def _prepare_wal_db(tmp_path):
    """WAL モードの SQLite と symbols 互換の最小テーブルを用意する"""
    db_file = str(tmp_path / "bulk_test.db")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text(
            "CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT, exchange TEXT)"
        ))
    return db_file, engine


def _sample_df():
    return pd.DataFrame({
        "id": [1, 2],
        "ticker": ["AAA", "BBB"],
        "exchange": ["NYSE", "NASDAQ"],
    })


def test_bulk_insert_succeeds_while_another_connection_is_open(tmp_path):
    """他の接続が開いたままでもバルクインサートが成功する（journal_mode 変更禁止の担保）。
    WAL からの journal_mode 変更は他接続が存在するだけで database is locked になるため、
    restore の実行環境（同一プロセス内の複数接続）では必ず他接続が存在する前提で書く"""
    db_file, engine = _prepare_wal_db(tmp_path)
    other = sqlite3.connect(db_file)
    other.execute("SELECT 1").fetchone()  # WAL の共有状態を掴んだ接続を保持
    try:
        bulk_insert_df_to_sqlite(engine, _sample_df(), "symbols", logger)
        count = other.execute("SELECT count(*) FROM symbols").fetchone()[0]
        assert count == 2
    finally:
        other.close()
        engine.dispose()


def test_bulk_insert_keeps_wal_mode(tmp_path):
    """バルクインサート後も DB が WAL モードのままである"""
    db_file, engine = _prepare_wal_db(tmp_path)
    try:
        bulk_insert_df_to_sqlite(engine, _sample_df(), "symbols", logger)
    finally:
        engine.dispose()
    conn = sqlite3.connect(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_bulk_insert_empty_df_is_noop(tmp_path):
    """空 DataFrame は何もしない"""
    db_file, engine = _prepare_wal_db(tmp_path)
    try:
        bulk_insert_df_to_sqlite(engine, pd.DataFrame(), "symbols", logger)
        conn = sqlite3.connect(db_file)
        assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
        conn.close()
    finally:
        engine.dispose()
