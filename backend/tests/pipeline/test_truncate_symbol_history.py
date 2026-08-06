"""履歴切り詰めのテスト（scripts/truncate_symbol_history.py）

## なぜ SQLite も消さないといけないか

`JBIO` の切り詰めは 2026-08-06 01:43 時点では効いていたのに、
**同日 06:49 のデイリー更新で元に戻った**。

    Parquet  2025-04-28  91.40  →  2025-04-29  10.14   ← 逆さ合併の段差が復活

原因は切り詰めが **Parquet しか消していなかった**こと。
`daily_prices` は Parquet と SQLite の**マージ**（`drop_duplicates(keep='last')`・SQL 側優先）で
伝播するため、SQLite のホットキャッシュ（直近730日）に合併前の行が残っていると
翌日のデイリーでそのまま Parquet に戻る。**マージは行を消さない。**

「次回の `restore_sqlite_cache_from_parquet` で追随する」という前提が誤りだった。
あれは再構築時にしか走らず、日常の伝播はマージ方向（SQLite → Parquet）が優先される。
"""

import os
import sqlite3
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.truncate_symbol_history import (  # noqa: E402
    TRUNCATE_TABLES,
    purge_sqlite_history,
)

KEEP_FROM = "2025-04-29"


@pytest.fixture
def hot_db(tmp_path):
    """`JBIO`(id=1721) と巻き込んではいけない別銘柄(id=99) を持つホットキャッシュ。"""
    path = str(tmp_path / "stocktool_test.db")
    con = sqlite3.connect(path)
    for t in TRUNCATE_TABLES:
        con.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, symbol_id INT, date TEXT)")
        rows = [
            (1721, "2024-08-05"), (1721, "2025-04-28"),   # 合併前（別会社）＝消す
            (1721, "2025-04-29"), (1721, "2026-08-04"),   # 合併後＝残す
            (99, "2024-08-05"), (99, "2025-04-28"),       # 他銘柄＝触らない
        ]
        con.executemany(f"INSERT INTO {t} (symbol_id, date) VALUES (?, ?)", rows)
    con.commit()
    con.close()
    return path


def _dates(path, table, symbol_id):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute(
            f"SELECT date FROM {table} WHERE symbol_id=? ORDER BY date", (symbol_id,))]
    finally:
        con.close()


def test_purges_pre_merger_rows_from_every_table(hot_db):
    """価格だけ消しても指標・順位が残ればマージで戻る。3テーブルすべて消す。"""
    got = purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=False)

    for t in TRUNCATE_TABLES:
        assert _dates(hot_db, t, 1721) == ["2025-04-29", "2026-08-04"], t
        assert got[t] == 2


def test_other_symbols_are_untouched(hot_db):
    """スコープの無い DELETE を絶対に撃たない。"""
    purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=False)

    for t in TRUNCATE_TABLES:
        assert _dates(hot_db, t, 99) == ["2024-08-05", "2025-04-28"], t


def test_the_boundary_date_itself_is_kept(hot_db):
    """`--from` の当日は「残す最初の行」。境界を1日ずらすと接合部が壊れる。"""
    purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=False)

    assert "2025-04-29" in _dates(hot_db, "daily_prices", 1721)


def test_dry_run_reports_without_deleting(hot_db):
    got = purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=True)

    assert got["daily_prices"] == 2, "件数を報告していない"
    assert len(_dates(hot_db, "daily_prices", 1721)) == 4, "dry-run なのに消えている"


def test_rerun_is_idempotent(hot_db):
    """再実行しても壊れない（切り詰めは再構築のたびに再適用する運用のため）。"""
    purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=False)
    got = purge_sqlite_history(hot_db, 1721, KEEP_FROM, dry_run=False)

    assert all(v == 0 for v in got.values())
    assert _dates(hot_db, "daily_prices", 1721) == ["2025-04-29", "2026-08-04"]


def test_missing_table_does_not_abort_the_purge(tmp_path):
    """テーブルが無い環境（サンドボックス等）でも他のテーブルの掃除は続ける。"""
    path = str(tmp_path / "partial.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_prices (id INTEGER PRIMARY KEY, symbol_id INT, date TEXT)")
    con.execute("INSERT INTO daily_prices (symbol_id, date) VALUES (1721, '2024-08-05')")
    con.commit()
    con.close()

    got = purge_sqlite_history(path, 1721, KEEP_FROM, dry_run=False)

    assert got["daily_prices"] == 1
    assert got["indicators"] is None, "存在しないテーブルは None で区別できること"


def test_covers_all_three_merged_tables():
    """マージで Parquet へ戻る T2/T3/T4 を漏れなく対象にしていること。"""
    assert set(TRUNCATE_TABLES) == {"daily_prices", "indicators", "relative_ranks"}


# ---------------------------------------------------------------------------
# ティッカー照合（世代間で symbol_id が振り直される事故への防御）
# ---------------------------------------------------------------------------
class TestSymbolIdIsVerifiedAgainstTicker:
    """**symbol_id は Parquet と SQLite で一致しているとは限らない。**

    全期間再構築は `symbols.id` を再採番する（サンドボックスの空 DB から始まるため
    T1 の「既存 id を温存する upsert」が働かない）。2026-08-06 の再構築では
    共通3,220ティッカーのうち 2,378件で id が変わった。

    切り詰めは Parquet 側で解決した `symbol_id` をそのまま SQLite の DELETE に使うため、
    両者がずれていると**まったく別の銘柄の履歴を消す**。実際にこの状況で
    961行が別銘柄から削除された（直後に SQLite を再構築したため実害は無かった）。
    """

    def _db(self, tmp_path, rows):
        path = str(tmp_path / "hot.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT)")
        con.executemany("INSERT INTO symbols (id, ticker) VALUES (?, ?)", rows)
        for t in TRUNCATE_TABLES:
            con.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, symbol_id INT, date TEXT)")
            con.execute(f"INSERT INTO {t} (symbol_id, date) VALUES (1716, '2024-01-01')")
        con.commit()
        con.close()
        return path

    def test_refuses_when_the_id_points_at_another_symbol(self, tmp_path):
        """id 先のティッカーが違えば**消さずに例外**。黙って別銘柄を削らない。"""
        path = self._db(tmp_path, [(1716, "SOMEONE_ELSE")])

        with pytest.raises(ValueError, match="SOMEONE_ELSE"):
            purge_sqlite_history(path, 1716, "2025-04-29", dry_run=False, expect_ticker="JBIO")

        con = sqlite3.connect(path)
        assert con.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 1
        con.close()

    def test_proceeds_when_the_ticker_matches(self, tmp_path):
        path = self._db(tmp_path, [(1716, "JBIO")])

        got = purge_sqlite_history(path, 1716, "2025-04-29", dry_run=False, expect_ticker="JBIO")

        assert got["daily_prices"] == 1

    def test_without_expect_ticker_it_still_works(self, tmp_path):
        """照合対象を渡さない呼び出し（既存テスト・他用途）は従来どおり動く。"""
        path = self._db(tmp_path, [(1716, "JBIO")])

        got = purge_sqlite_history(path, 1716, "2025-04-29", dry_run=False)

        assert got["daily_prices"] == 1
