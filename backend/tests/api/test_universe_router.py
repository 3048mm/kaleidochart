"""Universe Manager API（`api/universe_router.py`）のテスト。

## この機能が絶対に守るべきこと

`theme_members` は文字列参照で `symbols_master` への DB 制約が効かない。
実在しないティッカーを構成銘柄として登録できてしまうと、翌日の T1 同期
（`universe_sync._check_integrity`）が `UniverseIntegrityError` で落ち、
**日次パイプライン全体が止まる**（2026-09-10 に `_DFNS7A_` へ `HARK`
（`HAWK` のタイポ）、`_AIPHY_` へ `CCXI`（未登録）を手動追加して実際に発生）。

「登録した本人が、登録した瞬間に」気づけるよう、追加時点で弾く。

DB は共有インメモリ SQLite に差し替える（`test_universe_candidates.py` と同じ方式）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models_universe import BaseUniverse, SymbolMaster, ThemeMember

_engine = create_engine(
    "sqlite:///file:memuniv_router?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
BaseUniverse.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def _get_test_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()


def _get_test_write_db():
    """本番の `get_universe_write_db()` と同じく**抜けるときに commit する**。"""
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pytest.fixture()
def client():
    from api import universe_router

    app = FastAPI()
    app.include_router(universe_router.router, prefix="/api")
    app.dependency_overrides[universe_router._get_read_db] = _get_test_db
    app.dependency_overrides[universe_router._get_write_db] = _get_test_write_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    db = _Session()
    try:
        db.query(ThemeMember).delete()
        db.query(SymbolMaster).delete()
        db.commit()
    finally:
        db.close()


def _seed_symbol(ticker, category="個別", **kw):
    base = dict(ticker=ticker, exchange="NASDAQ", name=ticker, category=category,
                active=1, source="manual")
    base.update(kw)
    db = _Session()
    try:
        row = SymbolMaster(**base)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


class TestAddThemeMember:
    def test_既存の銘柄同士なら追加できる(self, client):
        _seed_symbol("_DFNS7A_", category="テーマ")
        _seed_symbol("HAWK")

        r = client.post("/api/universe/themes/_DFNS7A_/members",
                        json={"member_ticker": "HAWK"})
        assert r.status_code == 201
        assert r.json()["member_ticker"] == "HAWK"

    def test_構成銘柄がsymbols_masterに無ければ422(self, client):
        """タイポで登録漏れの銘柄を弾く（`HARK` を `HAWK` と間違えた実例）。

        ここで弾かれないと、翌日の T1 同期が `UniverseIntegrityError` で
        落ちて日次パイプライン全体が止まる。
        """
        _seed_symbol("_DFNS7A_", category="テーマ")
        # HAWK を登録し忘れたまま HARK（タイポ）を追加しようとする

        r = client.post("/api/universe/themes/_DFNS7A_/members",
                        json={"member_ticker": "HARK"})
        assert r.status_code == 422
        assert "HARK" in r.json()["detail"]

        # DB に孤児行が残っていないこと
        db = _Session()
        try:
            assert db.query(ThemeMember).filter(
                ThemeMember.member_ticker == "HARK").first() is None
        finally:
            db.close()

    def test_テーマ親がsymbols_masterに無ければ422(self, client):
        """テーマ自体が未登録なら、構成銘柄側が実在していても弾く。"""
        _seed_symbol("HAWK")
        # _NOSUCH_ というテーマは symbols_master に存在しない

        r = client.post("/api/universe/themes/_NOSUCH_/members",
                        json={"member_ticker": "HAWK"})
        assert r.status_code == 422
        assert "_NOSUCH_" in r.json()["detail"]

    def test_両方存在しなくても422(self, client):
        r = client.post("/api/universe/themes/_NOSUCH_/members",
                        json={"member_ticker": "ALSONOPE"})
        assert r.status_code == 422

    def test_重複追加は409のまま(self, client):
        """実在チェックの追加で既存の重複検出（409）が壊れていないこと。"""
        _seed_symbol("_DFNS7A_", category="テーマ")
        _seed_symbol("HAWK")
        client.post("/api/universe/themes/_DFNS7A_/members",
                   json={"member_ticker": "HAWK"})

        r = client.post("/api/universe/themes/_DFNS7A_/members",
                        json={"member_ticker": "HAWK"})
        assert r.status_code == 409
