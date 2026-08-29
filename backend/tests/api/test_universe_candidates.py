"""IPO 候補レビュー API のテスト（api/universe_router.py の candidates 系）。

候補画面から「採用」「却下」を行うエンドポイント。**採用は `symbols_master` への
INSERT を伴う**ため、既存の銘柄 CRUD と同じ経路（`derive_theme_type()`）を通ることと、
レビュー済みの判断を壊さないことを担保する。

DB は共有インメモリ SQLite に差し替える。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models_universe import BaseUniverse, IpoCandidate, SymbolMaster

_engine = create_engine(
    "sqlite:///file:memuniv?mode=memory&cache=shared&uri=true",
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
    """本番の `get_universe_write_db()` と同じく**抜けるときに commit する**。

    commit しないと、エンドポイントが 200 を返しても DB に反映されず
    「実装のバグ」に見えてしまう（エンドポイント側は既存の銘柄 CRUD と同じく
    `db.flush()` までしか行わず、commit は依存側の責務）。
    """
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
        db.query(IpoCandidate).delete()
        db.query(SymbolMaster).delete()
        db.commit()
    finally:
        db.close()


def _seed(**kw):
    base = dict(ticker="EROC", exchange="NYSE", name="ERock, Inc.", cik=2110029,
                first_trade_date="2026-06-10", last_price=13.23,
                status="pending")
    base.update(kw)
    db = _Session()
    try:
        row = IpoCandidate(**base)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


class TestListCandidates:

    def test_既定はpendingのみ返す(self, client):
        _seed()
        _seed(ticker="ALPX", flags="spac", status="auto_excluded")
        _seed(ticker="XXXX", status="rejected")

        r = client.get("/api/universe/candidates")
        assert r.status_code == 200
        assert [i["ticker"] for i in r.json()["items"]] == ["EROC"]

    def test_statusで絞り込める(self, client):
        _seed()
        _seed(ticker="ALPX", flags="spac", status="auto_excluded")

        r = client.get("/api/universe/candidates", params={"status": "auto_excluded"})
        assert [i["ticker"] for i in r.json()["items"]] == ["ALPX"]

    def test_却下済みも表示できる(self, client):
        """永久非表示ではなく**画面トグルで復活できる**ことが設計上の前提。"""
        _seed(ticker="XXXX", status="rejected")
        r = client.get("/api/universe/candidates", params={"status": "rejected"})
        assert [i["ticker"] for i in r.json()["items"]] == ["XXXX"]

    def test_上場日で絞り込める(self, client):
        _seed(ticker="OLD", first_trade_date="2026-04-02")
        _seed(ticker="NEW", first_trade_date="2026-08-01")

        r = client.get("/api/universe/candidates",
                       params={"listed_from": "2026-07-01"})
        assert [i["ticker"] for i in r.json()["items"]] == ["NEW"]

    def test_上場日の新しい順に並ぶ(self, client):
        _seed(ticker="OLD", first_trade_date="2026-04-02")
        _seed(ticker="NEW", first_trade_date="2026-08-01")

        items = client.get("/api/universe/candidates").json()["items"]
        assert [i["ticker"] for i in items] == ["NEW", "OLD"]

    def test_flagsはリストで返す(self, client):
        _seed(ticker="ALPX", flags="spac", status="auto_excluded")
        items = client.get("/api/universe/candidates",
                           params={"status": "auto_excluded"}).json()["items"]
        assert items[0]["flags"] == ["spac"]

    def test_flagsがNULLでも空リスト(self, client):
        _seed()
        assert client.get("/api/universe/candidates").json()["items"][0]["flags"] == []


class TestStats:

    def test_status別の件数を返す(self, client):
        _seed()
        _seed(ticker="FDXF")
        _seed(ticker="ALPX", status="auto_excluded")

        s = client.get("/api/universe/candidates/stats").json()
        assert s["pending"] == 2
        assert s["auto_excluded"] == 1
        assert s["rejected"] == 0


class TestAccept:

    def test_symbols_masterに追加されstatusが変わる(self, client):
        cid = _seed()
        r = client.post(f"/api/universe/candidates/{cid}/accept", json={})
        assert r.status_code == 200

        db = _Session()
        try:
            sym = db.query(SymbolMaster).filter_by(ticker="EROC").one()
            assert sym.category == "個別"
            assert sym.source == "ipo_candidate"
            assert sym.active == 1
            assert db.query(IpoCandidate).get(cid).status == "accepted"
        finally:
            db.close()

    def test_theme_typeは既存の導出関数を通す(self, client):
        """Universe 画面の既存 CRUD と同じ経路を使う（実装の二重化を防ぐ）。"""
        from data_collection.symbol_classify import derive_theme_type

        cid = _seed()
        client.post(f"/api/universe/candidates/{cid}/accept", json={})

        db = _Session()
        try:
            sym = db.query(SymbolMaster).filter_by(ticker="EROC").one()
            assert sym.theme_type == derive_theme_type("NYSE", "個別", "EROC")
        finally:
            db.close()

    def test_テーマを同時に紐付けられる(self, client):
        from db.models_universe import ThemeMember

        cid = _seed()
        r = client.post(f"/api/universe/candidates/{cid}/accept",
                        json={"themes": ["_AIINFRA_"]})
        assert r.status_code == 200

        db = _Session()
        try:
            m = db.query(ThemeMember).filter_by(member_ticker="EROC").one()
            assert m.theme_ticker == "_AIINFRA_"
        finally:
            db.close()

    def test_既にsymbols_masterにあれば409(self, client):
        db = _Session()
        try:
            db.add(SymbolMaster(ticker="EROC", exchange="NYSE",
                                category="個別", active=1))
            db.commit()
        finally:
            db.close()

        cid = _seed()
        assert client.post(f"/api/universe/candidates/{cid}/accept",
                           json={}).status_code == 409

    def test_存在しないidは404(self, client):
        assert client.post("/api/universe/candidates/9999/accept",
                           json={}).status_code == 404


class TestReject:

    def test_statusがrejectedになる(self, client):
        cid = _seed()
        r = client.post(f"/api/universe/candidates/{cid}/reject",
                        json={"note": "興味なし"})
        assert r.status_code == 200

        db = _Session()
        try:
            row = db.query(IpoCandidate).get(cid)
            assert row.status == "rejected"
            assert row.status_note == "興味なし"
            assert row.reviewed_at is not None
        finally:
            db.close()

    def test_symbols_masterには追加しない(self, client):
        cid = _seed()
        client.post(f"/api/universe/candidates/{cid}/reject", json={})

        db = _Session()
        try:
            assert db.query(SymbolMaster).count() == 0
        finally:
            db.close()


class TestBulk:

    def test_複数まとめて却下できる(self, client):
        ids = [_seed(), _seed(ticker="FDXF"), _seed(ticker="REA")]
        r = client.post("/api/universe/candidates/bulk",
                        json={"ids": ids, "action": "reject"})
        assert r.json()["updated"] == 3

        db = _Session()
        try:
            assert db.query(IpoCandidate).filter_by(status="rejected").count() == 3
        finally:
            db.close()

    def test_不正なactionは400(self, client):
        cid = _seed()
        assert client.post("/api/universe/candidates/bulk",
                           json={"ids": [cid], "action": "delete"}).status_code == 400
