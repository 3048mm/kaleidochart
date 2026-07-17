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


def test_clean_old_parquet_versions_grace_period(tmp_path):
    """15分の猶予期間（grace period）が適用され、最近更新されたバージョンは削除されないこと、
    および十分に古いバージョンは削除されることをテストする。"""
    import json
    import os
    import time
    from pipeline.parquet_cache_manager import clean_old_parquet_versions

    parquet_dir = tmp_path / "parquet_master"
    os.makedirs(parquet_dir, exist_ok=True)

    # 1. 3つのバージョン用 JSON ポインタと関連ダミーファイルを作成する
    # v1 (非常に古い: 30分前)
    v1_json = parquet_dir / "data_version_20260717_060000.json"
    v1_file = parquet_dir / "v1_prices.parquet"
    v1_file.write_text("dummy")
    with open(v1_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v1_file)}, f)
    # mtime を30分前（1800秒前）に設定
    t_v1 = time.time() - 1800
    os.utime(v1_json, (t_v1, t_v1))
    os.utime(v1_file, (t_v1, t_v1))

    # v2 (猶予期間内: 5分前)
    v2_json = parquet_dir / "data_version_20260717_062500.json"
    v2_file = parquet_dir / "v2_prices.parquet"
    v2_file.write_text("dummy")
    with open(v2_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v2_file)}, f)
    # mtime を5分前（300秒前）に設定
    t_v2 = time.time() - 300
    os.utime(v2_json, (t_v2, t_v2))
    os.utime(v2_file, (t_v2, t_v2))

    # v3 (最新: 今作成)
    v3_json = parquet_dir / "data_version_20260717_063000.json"
    v3_file = parquet_dir / "v3_prices.parquet"
    v3_file.write_text("dummy")
    with open(v3_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v3_file)}, f)
    # mtime は現在時刻

    # 2. クリーンアップを実行する（keep_count=2 で実行するが、v2は猶予期間内のためスキップされるはず）
    clean_old_parquet_versions(str(parquet_dir), logger, keep_count=2)

    # 3. アサーション
    # v1（非常に古い）は削除されているはず
    assert not os.path.exists(v1_json)
    assert not os.path.exists(v1_file)

    # v2（猶予期間内）は keep_count=2 枠の「外側」（pointers_to_delete に入る）だが、
    # 5分前作成で猶予期間（15分）内なので、削除されずに残っているはず！
    assert os.path.exists(v2_json)
    assert os.path.exists(v2_file)

    # v3（最新）は keep_count=2 内なので当然残る
    assert os.path.exists(v3_json)
    assert os.path.exists(v3_file)

