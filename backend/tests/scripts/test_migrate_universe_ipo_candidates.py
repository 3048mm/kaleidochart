"""`ipo_candidates` マイグレーションのテスト。

`universe.db` は**ユーザー資産**（`agent_execution_rules.md` §10.1）で、
採用/却下という再生成不可能な人間の判断を保持する。したがって:

  - バックアップを取らずに書き換えない
  - 冪等（何度実行しても壊れない）
  - 既存の `symbols_master` / `theme_members` / `ticker_history` に触れない

を機械的に担保する。
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

from db.database_universe import get_universe_write_db, init_universe_db  # noqa: E402
from db.models_universe import IpoCandidate, SymbolMaster  # noqa: E402
from scripts import migrate_universe_ipo_candidates as mig  # noqa: E402

TABLE = "ipo_candidates"


@pytest.fixture()
def universe_db(tmp_path, monkeypatch):
    """一時的な universe.db を作り、スクリプトがそこを向くようにする。"""
    path = str(tmp_path / "universe.db")
    monkeypatch.setattr(mig, "resolve_universe_db_path", lambda: path)
    return path


def _columns(path):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
    finally:
        con.close()


class TestApply:

    def test_テーブルが作成される(self, universe_db):
        mig.run(dry_run=False)
        assert _columns(universe_db) == {
            c.name for c in IpoCandidate.__table__.columns}

    def test_冪等_二回実行しても壊れない(self, universe_db):
        mig.run(dry_run=False)
        with get_universe_write_db() as db:
            db.add(IpoCandidate(ticker="EROC", exchange="NYSE", status="pending"))
            db.commit()

        mig.run(dry_run=False)      # 2回目

        with get_universe_write_db() as db:
            # セッションを抜けると DetachedInstanceError になるため中で取り出す
            tickers = [r.ticker for r in db.query(IpoCandidate).all()]
        assert tickers == ["EROC"], "既存行が消えてはいけない"

    def test_既存テーブルに触れない(self, universe_db):
        init_universe_db(universe_db)
        with get_universe_write_db() as db:
            db.add(SymbolMaster(ticker="AAPL", exchange="NASDAQ",
                                category="個別", active=1))
            db.commit()

        mig.run(dry_run=False)

        with get_universe_write_db() as db:
            assert db.query(SymbolMaster).count() == 1

    def test_既存DBがあればバックアップを取る(self, universe_db, tmp_path):
        init_universe_db(universe_db)          # DB を実体化させる
        assert os.path.exists(universe_db)

        mig.run(dry_run=False)

        baks = [f for f in os.listdir(tmp_path) if ".bak_" in f]
        assert baks, "ユーザー資産の書き換え前にバックアップが必要"

    def test_statusの既定はpending(self, universe_db):
        mig.run(dry_run=False)
        with get_universe_write_db() as db:
            db.add(IpoCandidate(ticker="FDXF", exchange="NYSE"))
            db.commit()
            assert db.query(IpoCandidate).one().status == "pending"

    def test_ticker_exchangeは一意(self, universe_db):
        from sqlalchemy.exc import IntegrityError

        mig.run(dry_run=False)
        with get_universe_write_db() as db:
            db.add(IpoCandidate(ticker="EROC", exchange="NYSE"))
            db.commit()
        with pytest.raises(IntegrityError):
            with get_universe_write_db() as db:
                db.add(IpoCandidate(ticker="EROC", exchange="NYSE"))
                db.commit()


class TestDryRun:

    def test_DBを作らない(self, universe_db):
        mig.run(dry_run=True)
        assert not os.path.exists(universe_db), "dry-run は DB を作ってはいけない"

    def test_既存DBを変更しない(self, universe_db, tmp_path):
        init_universe_db(universe_db)
        before = os.path.getsize(universe_db)

        r = mig.run(dry_run=True)

        assert r["dry_run"] is True
        assert os.path.getsize(universe_db) == before
        assert not [f for f in os.listdir(tmp_path) if ".bak_" in f], \
            "dry-run はバックアップも作らない"
