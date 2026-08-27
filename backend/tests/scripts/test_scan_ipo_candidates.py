"""IPO 候補スキャンのテスト（scripts/scan_ipo_candidates.py）。

## テスト方針

**ネットワークには一切出ない。** Yahoo / SEC の応答は実測したペイロードを
そのまま固定し、`opener` を差し替えて注入する。

Yahoo chart API の `meta` は 2026-08-27 に `EROC` から実取得したもの
（不要フィールドは削ってある）。
"""

import json
import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_collection.ipo_discovery import parse_chart_meta  # noqa: E402
from db.database_universe import get_universe_write_db  # noqa: E402
from db.models_universe import IpoCandidate  # noqa: E402
from scripts import scan_ipo_candidates as scan  # noqa: E402


# `EROC` の実応答（2026-08-27 取得）
EROC_META = {
    "currency": "USD",
    "symbol": "EROC",
    "exchangeName": "NYQ",
    "fullExchangeName": "NYSE",
    "instrumentType": "EQUITY",
    "firstTradeDate": 1781098200,          # 2026-06-10
    "regularMarketPrice": 13.23,
    "regularMarketVolume": 1716508,
    "longName": "ERock, Inc.",
    "shortName": "ERock, Inc.",
}


def _payload(meta):
    return {"chart": {"result": [{"meta": meta}], "error": None}}


class TestParseChartMeta:
    """Yahoo の応答を候補判定に使える形へ正規化する（純粋関数）。"""

    def test_主要フィールドを取り出す(self):
        q = parse_chart_meta(_payload(EROC_META))
        assert q["fullExchangeName"] == "NYSE"
        assert q["instrumentType"] == "EQUITY"
        assert q["name"] == "ERock, Inc."
        assert q["last_price"] == 13.23

    def test_firstTradeDateをISO日付に変換する(self):
        """エポック秒のまま比較すると `since` の日付文字列と型が合わない。"""
        q = parse_chart_meta(_payload(EROC_META))
        assert q["first_trade_date"] == "2026-06-10"

    def test_firstTradeDateが無ければNone(self):
        meta = {k: v for k, v in EROC_META.items() if k != "firstTradeDate"}
        assert parse_chart_meta(_payload(meta))["first_trade_date"] is None

    @pytest.mark.parametrize("payload", [
        {"chart": {"result": None, "error": {"code": "Not Found"}}},
        {"chart": {"result": []}},
        {},
    ])
    def test_空応答はNoneを返す(self, payload):
        assert parse_chart_meta(payload) is None


class TestProbeQuote:
    """HTTP は注入した opener 経由。404 とレート制限を混同しない。"""

    def test_正常応答をパースして返す(self):
        def opener(url):
            assert "EROC" in url
            return json.dumps(_payload(EROC_META))

        q = scan.probe_quote("EROC", opener=opener)
        assert q["first_trade_date"] == "2026-06-10"

    def test_404はNoneを返す(self):
        """ティッカーが Yahoo に無いだけ。**候補から静かに落とす**。"""
        def opener(url):
            raise scan.NotFound()

        assert scan.probe_quote("NOPE", opener=opener) is None

    def test_その他の例外もNoneにするが件数を数える(self):
        """通信エラーで**スキャン全体を落とさない**。ただし黙って握り潰さない。"""
        def opener(url):
            raise RuntimeError("boom")

        stats = {}
        assert scan.probe_quote("X", opener=opener, stats=stats) is None
        assert stats.get("error") == 1


class TestUpsertCandidates:

    @pytest.fixture(autouse=True)
    def _db(self, tmp_path, monkeypatch):
        from db.database_universe import init_universe_db
        init_universe_db(str(tmp_path / "universe.db"))

    def _row(self, ticker="EROC", **kw):
        base = dict(
            ticker=ticker, exchange="NYSE", name="ERock, Inc.", cik=2110029,
            first_trade_date="2026-06-10", last_price=13.23,
            avg_volume=1716508, flags=set(), status="pending")
        base.update(kw)
        return base

    def test_新規行を挿入する(self):
        with get_universe_write_db() as db:
            n = scan.upsert_candidates(db, [self._row()])
            db.commit()
        assert n == 1
        with get_universe_write_db() as db:
            assert db.query(IpoCandidate).count() == 1

    def test_二回目は重複させない(self):
        for _ in range(2):
            with get_universe_write_db() as db:
                scan.upsert_candidates(db, [self._row()])
                db.commit()
        with get_universe_write_db() as db:
            assert db.query(IpoCandidate).count() == 1

    def test_レビュー済みの判断を上書きしない(self):
        """**却下/採用は人間の判断**。再スキャンで pending に戻してはいけない。"""
        with get_universe_write_db() as db:
            scan.upsert_candidates(db, [self._row()])
            db.commit()
        with get_universe_write_db() as db:
            db.query(IpoCandidate).filter_by(ticker="EROC").update(
                {"status": "rejected", "status_note": "興味なし"})
            db.commit()

        with get_universe_write_db() as db:
            scan.upsert_candidates(db, [self._row()])
            db.commit()

        with get_universe_write_db() as db:
            row = db.query(IpoCandidate).one()
            assert (row.status, row.status_note) == ("rejected", "興味なし")

    def test_pending行のスナップショットは更新する(self):
        # 未レビューなら最新の株価・出来高を見せたい
        with get_universe_write_db() as db:
            scan.upsert_candidates(db, [self._row()])
            db.commit()
        with get_universe_write_db() as db:
            scan.upsert_candidates(db, [self._row(last_price=99.0)])
            db.commit()
        with get_universe_write_db() as db:
            assert db.query(IpoCandidate).one().last_price == 99.0

    def test_flagsはカンマ区切りで保存する(self):
        with get_universe_write_db() as db:
            scan.upsert_candidates(
                db, [self._row(ticker="ALPX", flags={"spac"},
                               status="auto_excluded")])
            db.commit()
        with get_universe_write_db() as db:
            row = db.query(IpoCandidate).filter_by(ticker="ALPX").one()
            assert row.flags == "spac"
            assert row.status == "auto_excluded"


class TestDecideStatus:
    """フラグの有無で pending / auto_excluded を振り分ける。"""

    def test_フラグ無しはpending(self):
        assert scan.decide_status(set()) == ("pending", None)

    def test_SPACはauto_excluded(self):
        st, note = scan.decide_status({"spac"})
        assert st == "auto_excluded" and "SPAC" in note

    def test_ETFはauto_excluded(self):
        st, note = scan.decide_status({"fund"})
        assert st == "auto_excluded" and note


class TestRateLimiter:
    """Yahoo は SEC のような公開上限が無い。自主的に絞る。"""

    def test_指定レートを超えない(self):
        t = [0.0]
        slept = []
        rl = scan.RateLimiter(per_sec=4, now=lambda: t[0],
                              sleep=lambda s: (slept.append(s),
                                               t.__setitem__(0, t[0] + s)))
        for _ in range(3):
            rl.acquire()
            t[0] += 0.01
        assert slept, "間隔が足りなければ待つ必要がある"
        assert all(s <= 0.25 for s in slept)

    def test_十分に間隔が空いていれば待たない(self):
        t = [0.0]
        slept = []
        rl = scan.RateLimiter(per_sec=4, now=lambda: t[0],
                              sleep=lambda s: slept.append(s))
        rl.acquire()
        t[0] += 10.0
        rl.acquire()
        assert not slept
