"""universe_sync.sync_symbols_from_universe() のテスト（universe.db 移行 W5）。

最重要の不変条件は **既存 symbols.id が変化しないこと**。
`daily_prices` / `indicators` / `relative_ranks`（各約157万行）と Parquet マスター全期間が
`symbols.id` の整数FKで紐付いているため、id が振り直されると価格履歴が孤児化する。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from db.models import Base, Symbol, ThemeConstituent
from db.models_universe import BaseUniverse, SymbolMaster, ThemeMember


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def st_session(tmp_path):
    """stocktool.db 相当（本番と誤認されない名前にする）"""
    engine = create_engine(f"sqlite:///{tmp_path / 'st_test.db'}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def un_session(tmp_path):
    """universe.db 相当"""
    engine = create_engine(f"sqlite:///{tmp_path / 'un_test.db'}")
    BaseUniverse.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_universe(un, rows, members=()):
    for r in rows:
        un.add(SymbolMaster(**r))
    for theme, member in members:
        un.add(ThemeMember(theme_ticker=theme, member_ticker=member, weight=1.0))
    un.commit()


def _seed_stocktool(st, rows):
    for r in rows:
        st.add(Symbol(**r))
    st.commit()


# 標準的な universe 側データ
BASE_UNIVERSE = [
    dict(ticker="SPY", exchange="NYSEARCA", name="S&P", category="市場",
         industry="", theme_type="etf", sector_etf=None, active=1),
    dict(ticker="XLK", exchange="NYSEARCA", name="テクノロジー", category="セクタ",
         industry="", theme_type="sector", sector_etf=None, active=1),
    dict(ticker="COPX", exchange="NYSEARCA", name="銅鉱山", category="テーマ",
         industry="金属", theme_type="theme", sector_etf="GLTR", active=1),
    dict(ticker="_PHNC_", exchange="VIRTUAL", name="仮想テーマ", category="テーマ",
         industry="", theme_type="virtual", sector_etf="XLK", active=1),
    dict(ticker="FCX", exchange="NYSE", name="Freeport", category="個別",
         industry="金属鉱業", theme_type=None, sector_etf=None, active=1),
    dict(ticker="SCCO", exchange="NYSE", name="Southern Copper", category="個別",
         industry="金属鉱業", theme_type=None, sector_etf=None, active=1),
    dict(ticker="AAPL", exchange="NASDAQ", name="Apple", category="個別",
         industry="ハードウェア", theme_type=None, sector_etf=None, active=1),
]
BASE_MEMBERS = [("COPX", "FCX"), ("COPX", "SCCO"), ("_PHNC_", "AAPL"), ("_PHNC_", "FCX")]


class TestIdPreservation:
    """最重要: 既存 symbols.id が1件も変化しないこと"""

    def test_existing_ids_are_preserved(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="SPY", exchange="NYSEARCA", name="S&P", category="市場", active=1),
            dict(ticker="AAPL", exchange="NASDAQ", name="Apple", category="個別", active=1),
            dict(ticker="FCX", exchange="NYSE", name="Freeport", category="個別", active=1),
        ])
        before = {s.ticker: s.id for s in st_session.query(Symbol).all()}
        assert len(before) == 3

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        after = {s.ticker: s.id for s in st_session.query(Symbol).all()}
        for ticker, old_id in before.items():
            assert after[ticker] == old_id, f"{ticker} の id が {old_id} → {after[ticker]} に変化した"

    def test_exchange_mismatch_does_not_create_duplicate_row(self, st_session, un_session):
        """(ticker, exchange) が自然キー。exchange 違いで別 id が採番されないこと。

        本番では extra_symbols 注入が exchange='US' 固定だったため、シートから消えた
        SPY が再注入されると (SPY,'US') という別 id 行が生まれる事故要因があった。
        """
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="SPY", exchange="NYSEARCA", name="S&P", category="市場", active=1),
        ])
        spy_id = st_session.query(Symbol).filter_by(ticker="SPY").one().id

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        spy_rows = st_session.query(Symbol).filter_by(ticker="SPY").all()
        assert len(spy_rows) == 1, "SPY が重複行になった"
        assert spy_rows[0].id == spy_id
        assert spy_rows[0].exchange == "NYSEARCA"

    def test_exchange_change_updates_in_place_instead_of_new_id(self, st_session, un_session):
        """exchange の修正は新規採番ではなく既存行の更新として扱う。

        回帰: 2026-07-28 に本番で GBTC が (GBTC,'US') → (GBTC,'NASDAQ') に変わり、
        自然キーが外れて id=3256 と id=3258 の重複行が生まれた。
        旧 id には価格履歴2,818行が紐付いたまま取り残された。
        """
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="GBTC", exchange="US", name="GBTC", category="市場", active=1),
        ])
        old_id = st_session.query(Symbol).filter_by(ticker="GBTC").one().id

        rows = [dict(r) for r in BASE_UNIVERSE] + [
            dict(ticker="GBTC", exchange="NASDAQ", name="ビットコイン (GBTC)", category="指標",
                 industry="", theme_type="etf", sector_etf="Crypto", active=1),
        ]
        _seed_universe(un_session, rows, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        gbtc = st_session.query(Symbol).filter_by(ticker="GBTC").all()
        assert len(gbtc) == 1, "exchange 変更で重複行が生まれてはいけない"
        assert gbtc[0].id == old_id, "既存 id が温存されるべき（価格履歴が孤児化するため）"
        assert gbtc[0].exchange == "NASDAQ"
        assert gbtc[0].category == "指標"

    def test_ambiguous_ticker_with_multiple_exchanges_does_not_hijack(self, st_session, un_session):
        """universe 側が同一 ticker を複数 exchange で持つ場合は救済を行わない。

        どの既存行に寄せるべきか決められないため、通常の新規採番に任せる。
        """
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="DUP", exchange="NYSE", name="Dup", category="個別", active=1),
        ])
        rows = [dict(r) for r in BASE_UNIVERSE] + [
            dict(ticker="DUP", exchange="NASDAQ", name="Dup A", category="個別",
                 industry="", theme_type=None, sector_etf=None, active=1),
            dict(ticker="DUP", exchange="BATS", name="Dup B", category="個別",
                 industry="", theme_type=None, sector_etf=None, active=1),
        ]
        _seed_universe(un_session, rows, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        exchanges = {s.exchange for s in st_session.query(Symbol).filter_by(ticker="DUP").all()}
        assert exchanges == {"NYSE", "NASDAQ", "BATS"}
        # 元の NYSE 行は universe に無いので退役している
        assert st_session.query(Symbol).filter_by(ticker="DUP", exchange="NYSE").one().active == 0

    def test_returns_symbol_id_map_keyed_by_ticker_exchange(self, st_session, un_session):
        """既存 txt_sync / spreadsheet 経路と同じ戻り値の形であること。"""
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sheet_data, symbol_ids = sync_symbols_from_universe(st_session, un_session)

        assert isinstance(sheet_data, list) and isinstance(symbol_ids, dict)
        assert ("SPY", "NYSEARCA") in symbol_ids
        keys = {"ticker", "exchange", "name", "category", "asset_class", "theme_type", "tags"}
        assert keys <= set(sheet_data[0].keys())
        for item in sheet_data:
            assert symbol_ids[(item["ticker"], item["exchange"])] > 0


class TestSoftDelete:
    def test_symbols_absent_from_universe_are_deactivated_not_deleted(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="OLDCO", exchange="NYSE", name="上場廃止", category="個別", active=1),
        ])
        old_id = st_session.query(Symbol).filter_by(ticker="OLDCO").one().id

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        row = st_session.query(Symbol).filter_by(ticker="OLDCO").one()
        assert row.active == 0, "universe に無い銘柄は active=0 になるべき"
        assert row.id == old_id, "退役時も id は温存されるべき（履歴が孤児化するため）"

    def test_inactive_in_universe_is_deactivated(self, st_session, un_session):
        """universe 側で active=0 にされた銘柄は stocktool でも active=0 になる（id は温存）。"""
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="AAPL", exchange="NASDAQ", name="Apple", category="個別", active=1),
        ])
        aapl_id = st_session.query(Symbol).filter_by(ticker="AAPL").one().id

        rows = [dict(r) for r in BASE_UNIVERSE]
        rows[-1]["active"] = 0  # AAPL を無効化
        _seed_universe(un_session, rows, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        row = st_session.query(Symbol).filter_by(ticker="AAPL").one()
        assert row.active == 0
        assert row.id == aapl_id

    def test_inactive_symbol_is_not_created_in_stocktool(self, st_session, un_session):
        """universe で無効な銘柄を stocktool に新規作成はしない（不要な行を増やさない）。"""
        from data_collection.universe_sync import sync_symbols_from_universe

        rows = [dict(r) for r in BASE_UNIVERSE]
        rows[-1]["active"] = 0  # AAPL を無効化
        _seed_universe(un_session, rows, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        assert st_session.query(Symbol).filter_by(ticker="AAPL").count() == 0


class TestThemeConstituents:
    def test_built_from_theme_members_not_tags(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        id_of = {s.ticker: s.id for s in st_session.query(Symbol).all()}
        pairs = {
            (id_of["COPX"], id_of["FCX"]), (id_of["COPX"], id_of["SCCO"]),
            (id_of["_PHNC_"], id_of["AAPL"]), (id_of["_PHNC_"], id_of["FCX"]),
        }
        actual = {(tc.theme_id, tc.symbol_id) for tc in st_session.query(ThemeConstituent).all()}
        assert actual == pairs

    def test_weight_is_one_over_n(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        id_of = {s.ticker: s.id for s in st_session.query(Symbol).all()}
        for theme in ("COPX", "_PHNC_"):
            rows = st_session.query(ThemeConstituent).filter_by(theme_id=id_of[theme]).all()
            assert len(rows) == 2
            assert all(abs(r.weight - 0.5) < 1e-9 for r in rows)
            assert abs(sum(r.weight for r in rows) - 1.0) < 1e-9

    def test_rebuild_is_idempotent(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)
        first = st_session.query(ThemeConstituent).count()
        sync_symbols_from_universe(st_session, un_session)
        assert st_session.query(ThemeConstituent).count() == first

    def test_inactive_members_are_excluded(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        rows = [dict(r) for r in BASE_UNIVERSE]
        for r in rows:
            if r["ticker"] == "SCCO":
                r["active"] = 0
        _seed_universe(un_session, rows, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        id_of = {s.ticker: s.id for s in st_session.query(Symbol).all()}
        rows_tc = st_session.query(ThemeConstituent).filter_by(theme_id=id_of["COPX"]).all()
        assert {r.symbol_id for r in rows_tc} == {id_of["FCX"]}
        assert abs(rows_tc[0].weight - 1.0) < 1e-9


class TestTagsRegeneration:
    """`tags` はカテゴリで意味が変わる（仕様書 §3.1）。"""

    def test_individual_tags_are_generated_from_theme_members_sorted(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        # FCX は COPX と _PHNC_ に所属 → 昇順ソートで安定した並び
        assert st_session.query(Symbol).filter_by(ticker="FCX").one().tags == "COPX, _PHNC_"
        assert st_session.query(Symbol).filter_by(ticker="AAPL").one().tags == "_PHNC_"
        assert st_session.query(Symbol).filter_by(ticker="SCCO").one().tags == "COPX"

    def test_non_individual_tags_come_from_sector_etf(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        assert st_session.query(Symbol).filter_by(ticker="COPX").one().tags == "GLTR"
        assert st_session.query(Symbol).filter_by(ticker="_PHNC_").one().tags == "XLK"

    def test_tag_order_is_stable_across_runs(self, st_session, un_session):
        """並びが不安定だと Parquet symbols の差分が毎日発生する。"""
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)
        first = {s.ticker: s.tags for s in st_session.query(Symbol).all()}
        sync_symbols_from_universe(st_session, un_session)
        assert {s.ticker: s.tags for s in st_session.query(Symbol).all()} == first

    def test_duplicate_membership_is_deduplicated(self, st_session, un_session):
        """旧 tags には `ANET` の `_HRDW4F_` 重複のようなゴミがあった。"""
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        # theme_members 側に重複を作れないよう UniqueConstraint があるため、
        # 生成結果に重複が出ないことだけ確認する
        sync_symbols_from_universe(st_session, un_session)
        tags = st_session.query(Symbol).filter_by(ticker="FCX").one().tags
        parts = [t.strip() for t in tags.split(",")]
        assert len(parts) == len(set(parts))


class TestOrphanGate:
    def test_orphan_theme_parent_aborts_sync(self, st_session, un_session):
        """親が symbols_master に存在しない theme_members があれば同期を中断する。"""
        from data_collection.universe_sync import sync_symbols_from_universe, UniverseIntegrityError

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS + [("_GHOST_", "AAPL")])
        with pytest.raises(UniverseIntegrityError) as exc:
            sync_symbols_from_universe(st_session, un_session)
        assert "_GHOST_" in str(exc.value)

    def test_orphan_member_aborts_sync(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe, UniverseIntegrityError

        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS + [("COPX", "NOSUCH")])
        with pytest.raises(UniverseIntegrityError) as exc:
            sync_symbols_from_universe(st_session, un_session)
        assert "NOSUCH" in str(exc.value)

    def test_nothing_is_written_when_gate_fails(self, st_session, un_session):
        """中断時に stocktool 側が中途半端に書き換わっていないこと。"""
        from data_collection.universe_sync import sync_symbols_from_universe, UniverseIntegrityError

        _seed_stocktool(st_session, [
            dict(ticker="SPY", exchange="NYSEARCA", name="S&P", category="市場", active=1),
        ])
        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS + [("_GHOST_", "AAPL")])

        with pytest.raises(UniverseIntegrityError):
            sync_symbols_from_universe(st_session, un_session)

        assert st_session.query(Symbol).count() == 1
        assert st_session.query(ThemeConstituent).count() == 0


class TestAttributeSync:
    def test_attributes_are_copied_from_universe(self, st_session, un_session):
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="COPX", exchange="NYSEARCA", name="旧名称", category="個別",
                 asset_class="旧業界", theme_type=None, tags="OLD", active=1),
        ])
        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        s = st_session.query(Symbol).filter_by(ticker="COPX").one()
        assert s.name == "銅鉱山"
        assert s.category == "テーマ"
        assert s.asset_class == "金属"      # universe.industry → stocktool.asset_class
        assert s.theme_type == "theme"
        assert s.tags == "GLTR"

    def test_next_earnings_date_is_not_clobbered(self, st_session, un_session):
        """next_earnings_date は T2/T3 が書く列で universe は持たない。"""
        from datetime import date
        from data_collection.universe_sync import sync_symbols_from_universe

        _seed_stocktool(st_session, [
            dict(ticker="AAPL", exchange="NASDAQ", name="Apple", category="個別",
                 active=1, next_earnings_date=date(2026, 8, 1)),
        ])
        _seed_universe(un_session, BASE_UNIVERSE, BASE_MEMBERS)
        sync_symbols_from_universe(st_session, un_session)

        assert st_session.query(Symbol).filter_by(ticker="AAPL").one().next_earnings_date == date(2026, 8, 1)
