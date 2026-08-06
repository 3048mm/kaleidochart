"""改称を `user_data.db` へ伝播させるテスト（scripts/rename_symbol.py）

## なぜ必要か

ウォッチリストは `ticker` を耐久キーにして「DB を作り直しても追随できる」設計だが、
**ticker 自体が変わると宙に浮く**。2026-08-06 に `ATLN`（→ `CIRC`）で実際に起きた。

`api/symbol_heal.py` の `ticker_history` 経由の自己修復（pull）だけでも直るが、
それは API が heal を走らせるまで待つ必要がある。改称を適用した**その場で**
書き換えておけば、次の画面表示から正しいティッカーで出る（push）。

2つは役割が違う:

    push（本モジュール）  改称適用と同時。ユーザーからは即座に正しく見える
    pull（symbol_heal）   取りこぼし・過去分の保険。多段改称も辿る
"""

import datetime as dt
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

from scripts.rename_symbol import (  # noqa: E402
    USER_DATA_RENAME_TABLES,
    rename_user_data_references,
)


@pytest.fixture
def user_db(tmp_path):
    """`ATLN` を持つウォッチリスト・保有ポジション・取引履歴。"""
    path = str(tmp_path / "user_data.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE watchlist
                   (id INTEGER PRIMARY KEY, symbol_id INT, ticker TEXT, exchange TEXT)""")
    con.execute("""CREATE TABLE portfolio_positions
                   (id INTEGER PRIMARY KEY, symbol_id INT, ticker TEXT, exchange TEXT)""")
    con.execute("""CREATE TABLE position_history
                   (id INTEGER PRIMARY KEY, symbol_id INT, ticker TEXT, exchange TEXT)""")
    for t in ("watchlist", "portfolio_positions", "position_history"):
        con.executemany(f"INSERT INTO {t} (symbol_id, ticker, exchange) VALUES (?, ?, ?)",
                        [(581, "ATLN", "NASDAQ"), (512, "AAPL", "NASDAQ")])
    con.commit()
    con.close()
    return path


def _tickers(path, table):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute(f"SELECT ticker FROM {table}"))
    finally:
        con.close()


def test_watchlist_follows_the_rename(user_db):
    """`ATLN` → `CIRC` の実測ケース。"""
    got = rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    assert _tickers(user_db, "watchlist") == ["AAPL", "CIRC"]
    assert got["watchlist"] == 1


def test_open_positions_follow_the_rename(user_db):
    rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    assert _tickers(user_db, "portfolio_positions") == ["AAPL", "CIRC"]


def test_trade_history_keeps_the_original_ticker(user_db):
    """**取引記録は書き換えない。**

    `position_history` は「その時どの銘柄を売買したか」の記録。
    現行ティッカーへ書き換えると取引履歴として不正確になる
    （`symbol_id` は heal が現行へ解決する）。
    """
    got = rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    assert _tickers(user_db, "position_history") == ["AAPL", "ATLN"]
    assert "position_history" not in got


def test_other_tickers_are_untouched(user_db):
    rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    con = sqlite3.connect(user_db)
    assert con.execute("SELECT COUNT(*) FROM watchlist WHERE ticker='AAPL'").fetchone()[0] == 1
    con.close()


def test_exchange_is_updated_only_when_given(user_db):
    """取引所が変わらない改称で `exchange` を NULL に潰さない。"""
    rename_user_data_references(user_db, "ATLN", "CIRC", None, dry_run=False)

    con = sqlite3.connect(user_db)
    ex = con.execute("SELECT exchange FROM watchlist WHERE ticker='CIRC'").fetchone()[0]
    con.close()
    assert ex == "NASDAQ"


def test_dry_run_reports_without_writing(user_db):
    got = rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=True)

    assert got["watchlist"] == 1
    assert _tickers(user_db, "watchlist") == ["AAPL", "ATLN"], "dry-run なのに書き換えている"


def test_rerun_is_idempotent(user_db):
    rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)
    got = rename_user_data_references(user_db, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    assert all(v == 0 for v in got.values())


def test_missing_table_does_not_abort(tmp_path):
    """テーブルが無い user_data.db（旧スキーマ・テスト環境）でも落ちない。"""
    path = str(tmp_path / "partial.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, symbol_id INT, "
                "ticker TEXT, exchange TEXT)")
    con.execute("INSERT INTO watchlist (symbol_id, ticker, exchange) VALUES (1, 'ATLN', 'NASDAQ')")
    con.commit()
    con.close()

    got = rename_user_data_references(path, "ATLN", "CIRC", "NASDAQ", dry_run=False)

    assert got["watchlist"] == 1
    assert got["portfolio_positions"] is None, "存在しないテーブルは None で区別できること"


def test_history_table_is_excluded_by_design():
    """対象テーブルの定義に取引履歴を含めない（設計を定数で固定する）。"""
    assert set(USER_DATA_RENAME_TABLES) == {"watchlist", "portfolio_positions"}


def test_missing_user_db_is_not_an_error(tmp_path):
    """`user_data.db` が無い環境（CI・サンドボックス）で改称を止めない。"""
    got = rename_user_data_references(str(tmp_path / "nope.db"), "ATLN", "CIRC",
                                      "NASDAQ", dry_run=False)

    assert got == {}
