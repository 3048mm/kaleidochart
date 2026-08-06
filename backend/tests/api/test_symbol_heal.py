"""symbol_heal.py（heal_*_ids 共通コア）のユニットテスト。

検証する振る舞い:
1. 正常修復: stale な symbol_id を ticker/exchange から再解決する
2. 単発の解決不能: 該当項目のみ NULL 化し、他は無傷
3. 安全弁: 解決不能率が閾値超なら一切書き込まない（I-7 事故の再発防止）
4. スロットル: 前回 clean なら TTL 内はスキップ。修復が発生した回は記録しない
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol
from db.models_user import BaseUser, Watchlist
from api.symbol_heal import (
    heal_symbol_references,
    reset_heal_throttle,
    HEAL_MAX_UNRESOLVED_RATIO,
)

# stocktool 側（symbols）と user 側（watchlist）の独立インメモリ DB
_engine = create_engine("sqlite:///file:heal_mem?mode=memory&cache=shared&uri=true",
                        connect_args={"check_same_thread": False})
_user_engine = create_engine("sqlite:///file:heal_user_mem?mode=memory&cache=shared&uri=true",
                             connect_args={"check_same_thread": False})
_Session = sessionmaker(bind=_engine)
_UserSession = sessionmaker(bind=_user_engine)


def _make_watchlist_item(user_db, ticker, exchange="NASDAQ", symbol_id=None):
    wl = Watchlist(symbol_id=symbol_id, ticker=ticker, exchange=exchange,
                   entry_date=date(2026, 7, 1), entry_price=100.0,
                   status="active", added_at=datetime(2026, 7, 1))
    user_db.add(wl)
    return wl


@pytest.fixture()
def dbs():
    reset_heal_throttle()
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    BaseUser.metadata.drop_all(_user_engine)
    BaseUser.metadata.create_all(_user_engine)

    db = _Session()
    user_db = _UserSession()

    # symbols: id=1 AAPL, id=2 NVDA, id=3 MSFT
    db.add_all([
        Symbol(id=1, ticker="AAPL", exchange="NASDAQ", name="Apple", category="個別", active=1),
        Symbol(id=2, ticker="NVDA", exchange="NASDAQ", name="Nvidia", category="個別", active=1),
        Symbol(id=3, ticker="MSFT", exchange="NASDAQ", name="Microsoft", category="個別", active=1),
    ])
    db.commit()

    yield db, user_db
    db.close()
    user_db.close()


def _heal(db, user_db, kind="watchlist"):
    items = user_db.query(Watchlist).all()
    return heal_symbol_references(db, user_db, items, kind=kind)


def test_remaps_stale_symbol_id(dbs):
    """T1 再同期で id が変わった項目を ticker/exchange から再解決する。"""
    db, user_db = dbs
    _make_watchlist_item(user_db, "AAPL", symbol_id=999)  # stale
    _make_watchlist_item(user_db, "NVDA", symbol_id=2)    # 正常
    user_db.commit()

    result = _heal(db, user_db)

    assert result.skipped is None
    assert result.healed == 1
    ids = {wl.ticker: wl.symbol_id for wl in user_db.query(Watchlist).all()}
    assert ids == {"AAPL": 1, "NVDA": 2}


def test_nulls_single_unresolvable(dbs):
    """正当な上場廃止相当（少数の解決不能）は従来通り NULL 化し、他は無傷。"""
    db, user_db = dbs
    _make_watchlist_item(user_db, "AAPL", symbol_id=1)
    _make_watchlist_item(user_db, "NVDA", symbol_id=2)
    _make_watchlist_item(user_db, "MSFT", symbol_id=3)
    _make_watchlist_item(user_db, "GHOST", symbol_id=888)  # 1/4 = 25% <= 30%
    user_db.commit()

    result = _heal(db, user_db)

    assert result.skipped is None
    assert result.nulled == 1
    ids = {wl.ticker: wl.symbol_id for wl in user_db.query(Watchlist).all()}
    assert ids == {"AAPL": 1, "NVDA": 2, "MSFT": 3, "GHOST": None}


def test_safety_valve_blocks_mass_nulling(dbs):
    """I-7 再発防止: 解決不能率が閾値超なら一切書き込まない。"""
    db, user_db = dbs
    # 4/5 = 80% が解決不能（Sandbox 誤接続を模倣）
    _make_watchlist_item(user_db, "AAPL", symbol_id=1)
    for i, t in enumerate(["GHOST1", "GHOST2", "GHOST3", "GHOST4"]):
        _make_watchlist_item(user_db, t, symbol_id=100 + i)
    user_db.commit()

    result = _heal(db, user_db)

    assert result.skipped == "safety_valve"
    assert result.unresolved == 4
    # symbol_id は一切変更されていない（NULL 化も再マッピングもなし）
    ids = {wl.ticker: wl.symbol_id for wl in user_db.query(Watchlist).all()}
    assert ids == {"AAPL": 1, "GHOST1": 100, "GHOST2": 101, "GHOST3": 102, "GHOST4": 103}


def test_safety_valve_threshold_boundary(dbs):
    """境界値: ちょうど閾値（30%）は発動しない。超えたら発動する。"""
    db, user_db = dbs
    # 3/10 = 30% → 発動しない（> で判定）
    for t, sid in [("AAPL", 1), ("NVDA", 2), ("MSFT", 3)]:
        _make_watchlist_item(user_db, t, symbol_id=sid)
    for i in range(4):  # 正常なダミー（AAPL の重複登録は不可のため別ticker + 解決可能に）
        _make_watchlist_item(user_db, "AAPL", exchange=f"EX{i}", symbol_id=1)
    for i in range(3):
        _make_watchlist_item(user_db, f"GHOST{i}", symbol_id=200 + i)
    user_db.commit()

    assert HEAL_MAX_UNRESOLVED_RATIO == 0.3
    result = _heal(db, user_db)
    assert result.skipped is None  # 3/10 == 30% は発動しない
    assert result.nulled == 3


def test_throttle_skips_when_previous_heal_was_clean(dbs):
    """前回 clean（修復ゼロ）なら TTL 内の再実行はスキップされる。"""
    db, user_db = dbs
    wl = _make_watchlist_item(user_db, "AAPL", symbol_id=1)
    user_db.commit()

    r1 = _heal(db, user_db)
    assert r1.skipped is None and r1.healed == 0  # clean → スロットル記録

    # その後データを壊しても、TTL 内はスキップされ修復されない
    wl.symbol_id = 999
    user_db.commit()
    r2 = _heal(db, user_db)
    assert r2.skipped == "throttled"
    assert user_db.query(Watchlist).first().symbol_id == 999

    # リセット後は修復される
    reset_heal_throttle()
    r3 = _heal(db, user_db)
    assert r3.skipped is None and r3.healed == 1
    assert user_db.query(Watchlist).first().symbol_id == 1


def test_throttle_not_recorded_after_modification(dbs):
    """修復が発生した回はスロットル記録されず、直後の再実行も有効（既存テスト互換）。"""
    db, user_db = dbs
    wl1 = _make_watchlist_item(user_db, "AAPL", symbol_id=999)
    wl2 = _make_watchlist_item(user_db, "NVDA", symbol_id=2)
    user_db.commit()

    r1 = _heal(db, user_db)
    assert r1.healed == 1  # 修復発生 → スロットル記録しない

    # 直後に別の破損を作っても、次の heal で修復される
    wl2.symbol_id = 777
    user_db.commit()
    r2 = _heal(db, user_db)
    assert r2.skipped is None
    assert r2.healed == 1
    assert user_db.query(Watchlist).filter_by(ticker="NVDA").first().symbol_id == 2


def test_throttle_key_distinguishes_kind(dbs):
    """スロットルは種別（watchlist/portfolio）ごとに独立している。"""
    db, user_db = dbs
    wl = _make_watchlist_item(user_db, "AAPL", symbol_id=1)
    user_db.commit()

    r1 = _heal(db, user_db, kind="watchlist")
    assert r1.skipped is None  # clean → watchlist キーで記録

    # 別種別は独立に実行される
    r2 = _heal(db, user_db, kind="portfolio")
    assert r2.skipped is None


def test_empty_items_is_noop(dbs):
    db, user_db = dbs
    result = _heal(db, user_db)
    assert result.total == 0
    assert result.skipped is None


# ---------------------------------------------------------------------------
# 改称への追随（ticker_history 経由）
# ---------------------------------------------------------------------------
class TestFollowsTickerRenames:
    """**ticker が変わると heal の全段が外れて symbol_id が NULL になる。**

    ウォッチリストは ticker を耐久キーにして「DB を作り直しても追随できる」設計だが、
    ticker 自体が変わると `symbol_id` → `(ticker, exchange)` → `ticker` の
    どれも当たらない。2026-08-06 に `ATLN`（→ `CIRC`）が実際にこれで宙に浮いた。

        watchlist  ticker='ATLN'  symbol_id=581
        symbols    ATLN は存在しない / 581 は 'CIRC'（同一企業・SEC CIK 1605888）

    `universe.db.ticker_history` に改称は必ず記録されている（`rename_symbol.py` と
    Universe Manager の両方が書く）ので、**NULL 化する前にそこを引く**。
    """

    def test_renamed_ticker_is_resolved_via_history(self, dbs):
        """`ATLN` → `CIRC` の実測ケース。"""
        db, user_db = dbs
        db.add(Symbol(id=581, ticker="CIRC", exchange="NASDAQ",
                      name="Atlantic International", category="個別", active=1))
        db.commit()
        _make_watchlist_item(user_db, "ATLN", symbol_id=581)
        user_db.commit()

        result = heal_symbol_references(db, user_db, user_db.query(Watchlist).all(),
                                        kind="watchlist", rename_map={"ATLN": "CIRC"})

        assert result.nulled == 0, "改称を追えず NULL 化している"
        wl = user_db.query(Watchlist).filter_by(symbol_id=581).one()
        assert wl.ticker == "CIRC", "表示用の ticker が現行へ更新されていない"

    def test_multi_hop_rename_is_followed(self):
        """週を跨いだ多段改称（A→B→C）も辿れること。"""
        from api.symbol_heal import resolve_current_ticker

        assert resolve_current_ticker("A", {"A": "B", "B": "C"}) == "C"

    def test_cyclic_history_does_not_hang(self):
        """壊れた履歴（循環）で無限ループしない。"""
        from api.symbol_heal import resolve_current_ticker

        assert resolve_current_ticker("A", {"A": "B", "B": "A"}) in {"A", "B"}

    def test_unknown_ticker_returns_none(self):
        from api.symbol_heal import resolve_current_ticker

        assert resolve_current_ticker("AAPL", {"ATLN": "CIRC"}) is None

    def test_still_nulls_when_history_has_nothing(self, dbs):
        """履歴にも無ければ従来どおり NULL 化する（推測で別銘柄に付けない）。"""
        db, user_db = dbs
        _make_watchlist_item(user_db, "DEADCO", symbol_id=999)
        # 解決不能率が閾値(30%)を超えると安全弁で一切書き込まれないため、正常な行を添える
        _make_watchlist_item(user_db, "AAPL", symbol_id=1)
        _make_watchlist_item(user_db, "NVDA", symbol_id=2)
        _make_watchlist_item(user_db, "MSFT", symbol_id=3)
        user_db.commit()

        result = heal_symbol_references(db, user_db, user_db.query(Watchlist).all(),
                                        kind="watchlist", rename_map={"ATLN": "CIRC"})

        assert result.nulled == 1
        assert user_db.query(Watchlist).filter_by(ticker="DEADCO").one().symbol_id is None

    def test_history_is_only_consulted_when_normal_resolution_fails(self, dbs):
        """正常な行のために universe.db を引かない（リクエストパスを重くしない）。"""
        db, user_db = dbs
        _make_watchlist_item(user_db, "AAPL", symbol_id=1)
        user_db.commit()
        calls = []

        def _loader():
            calls.append(1)
            return {}

        heal_symbol_references(db, user_db, user_db.query(Watchlist).all(),
                               kind="watchlist", rename_map=_loader)

        assert calls == [], "解決できているのに ticker_history を読んでいる"

    def test_position_history_keeps_its_original_ticker(self, dbs):
        """**取引記録の ticker は書き換えない。**

        `position_history` は「その時どの銘柄を売買したか」の記録。
        後から現行ティッカーに書き換えると取引履歴として不正確になる。
        `symbol_id` だけ現行へ解決する。
        """
        from db.models_user import PositionHistory

        db, user_db = dbs
        db.add(Symbol(id=581, ticker="CIRC", exchange="NASDAQ",
                      name="Atlantic International", category="個別", active=1))
        db.commit()
        ph = PositionHistory(portfolio_id=1, symbol_id=581, ticker="ATLN", exchange="NASDAQ",
                             entry_date=date(2026, 6, 23), entry_price=1.33, entry_shares=100,
                             exit_date=date(2026, 7, 10), exit_price=0.60, exit_shares=100,
                             exit_reason="stop_loss")
        user_db.add(ph)
        user_db.commit()

        heal_symbol_references(db, user_db, [ph], kind="portfolio",
                               rename_map={"ATLN": "CIRC"})

        assert ph.symbol_id == 581, "symbol_id が現行へ解決されていない"
        assert ph.ticker == "ATLN", "取引記録の ticker を書き換えてしまっている"
