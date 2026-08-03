"""健全性チェックの NG / STALE 分離のテスト（tools/db_health_check.py）

## 背景（2026-08-02）

上流(Yahoo)が銘柄レコードを作り直して系列を切り落とすと、その銘柄は
**恒久的に SPY へ追いつけない**。17銘柄で発生し、旧実装ではこれらが全て NG となり
常時 21件の NG が出続ける状態になった。

その状態では本当に対応が要る「T2/T3 件数不一致」「NULL 混入」が埋もれる。
また `--ng-out` は deploy_after_merge のベースライン比較に使われるため、
恒久 NG が混ざると「昇格で新たに壊れた銘柄」の差分が読めなくなる。

判定は「こちらで直せるか」で切る:
  NG    … 件数不一致・NULL 混入・履歴不足 → こちら側の問題
  STALE … 完全な履歴があり最新日だけ遅れている → 上流の供給状況
"""

import os
import sqlite3
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import tools.db_health_check as hc  # noqa: E402


@pytest.fixture
def health_db(tmp_path, monkeypatch):
    """SPY 2026-07-31 を基準にした最小 DB を作る"""
    db = tmp_path / "health_test.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT, active INTEGER);
        CREATE TABLE daily_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol_id INTEGER, date TEXT);
        CREATE TABLE indicators (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol_id INTEGER, date TEXT);
    """)
    con.execute("INSERT INTO symbols VALUES (1, 'SPY', 1)")
    con.executemany("INSERT INTO daily_prices (symbol_id, date) VALUES (1, ?)",
                    [(f"2026-07-{d:02d}",) for d in range(1, 32)])
    con.executemany("INSERT INTO indicators (symbol_id, date) VALUES (1, ?)",
                    [(f"2026-07-{d:02d}",) for d in range(1, 32)])
    con.commit()
    monkeypatch.setattr(hc, "DB_PATH", str(db))

    def add(sym_id, ticker, n_t2, n_t3, latest_day):
        con.execute("INSERT INTO symbols VALUES (?, ?, 1)", (sym_id, ticker))
        # 最終日が latest_day になるよう、そこから遡って n 件入れる
        dates = [f"2026-{6 if latest_day - i <= 0 else 7:02d}-{(latest_day - i) if latest_day - i > 0 else 30 + (latest_day - i):02d}"
                 for i in range(n_t2)]
        con.executemany("INSERT INTO daily_prices (symbol_id, date) VALUES (?, ?)",
                        [(sym_id, d) for d in dates])
        con.executemany("INSERT INTO indicators (symbol_id, date) VALUES (?, ?)",
                        [(sym_id, d) for d in dates[:n_t3]])
        con.commit()

    yield add, con
    con.close()


def _status(ticker):
    """--all で走らせて対象銘柄の Status を取る"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        ng = hc.check_symbol_health(all_active=True)
    return ng, buf.getvalue()


def test_lagging_symbol_with_full_history_is_stale(health_db):
    """完全な履歴があり最新日だけ遅れている → STALE。NG リストに載せない。

    これが本丸。上流切断銘柄が恒久 NG として居座るのを防ぐ。
    """
    add, _ = health_db
    add(2, "BLD", n_t2=25, n_t3=25, latest_day=2)   # 最終 2026-07-02、SPY は 07-31

    ng, out = _status("BLD")

    assert "BLD" not in ng, "STALE が NG リストに入っている"
    assert "[BLD] Status: STALE" in out


def test_count_mismatch_is_ng(health_db):
    """T2/T3 の件数不一致は STALE ではなく NG（こちら側で直せる問題）"""
    add, _ = health_db
    add(2, "BROKEN", n_t2=25, n_t3=20, latest_day=2)

    ng, out = _status("BROKEN")

    assert "BROKEN" in ng
    assert "[BROKEN] Status: NG" in out


def test_short_history_is_ng_not_stale(health_db):
    """履歴が極端に短い銘柄は STALE に逃がさない（要調査のまま残す）。

    上流切断の復元前はこの状態になるので、ここを STALE にすると復元漏れに気づけない。
    """
    add, _ = health_db
    add(2, "TINY", n_t2=5, n_t3=5, latest_day=2)

    ng, out = _status("TINY")

    assert "TINY" in ng
    assert "[TINY] Status: NG" in out


def test_synced_symbol_is_ok(health_db):
    add, _ = health_db
    add(2, "AAPL", n_t2=25, n_t3=25, latest_day=31)

    ng, out = _status("AAPL")

    assert "AAPL" not in ng
    assert "[AAPL] Status:" not in out, "OK 銘柄が個別出力されている（ノイズ）"


def test_summary_separates_ng_and_stale(health_db):
    add, _ = health_db
    add(2, "BLD", n_t2=25, n_t3=25, latest_day=2)      # STALE
    add(3, "BROKEN", n_t2=25, n_t3=20, latest_day=2)   # NG

    ng, out = _status("BLD")

    assert ng == ["BROKEN"]
    assert "要対応 1銘柄" in out
    assert "上流待ち 1銘柄" in out
