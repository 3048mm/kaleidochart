"""panel_builders のバルクプリロード関数のテスト。

Plan B（doc/in_progress/dashboard_performance_plan.md）:
per-symbol の 3 クエリ（sparkline / 価格履歴 / indicator）を
一括ロードに置き換えても、**結果が per-symbol 実装と厳密に等価**であることを検証する。
"""
import os
import sys
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker

test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(test_dir)
sys.path.insert(0, backend_dir)

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank

TARGET_DATE = date(2026, 7, 1)
# limit=30 を超える数の営業日を用意して LIMIT の挙動を検証する
DATES = [TARGET_DATE - timedelta(days=i) for i in range(39, -1, -1)]


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    session.add_all([
        # sym 1: 全日付にデータあり（標準ケース）
        Symbol(id=1, ticker="FULL", exchange="X", name="Full", category="テーマ", active=1),
        # sym 2: 一部の日付でランクが NULL（非NULLフィルタの等価性）
        Symbol(id=2, ticker="NULLS", exchange="X", name="Nulls", category="テーマ", active=1),
        # sym 3: limit 未満の行数しかない
        Symbol(id=3, ticker="SHORT", exchange="X", name="Short", category="テーマ", active=1),
        # sym 4: データなし
        Symbol(id=4, ticker="EMPTY", exchange="X", name="Empty", category="テーマ", active=1),
        # sym 5: target_date より新しいデータも持つ（日付上限フィルタの等価性）
        Symbol(id=5, ticker="FUTURE", exchange="X", name="Future", category="テーマ", active=1),
        # sym 6: 直近データなし（プリロードの日付窓より古いデータのみ → フォールバック検証）
        Symbol(id=6, ticker="STALE", exchange="X", name="Stale", category="テーマ", active=1),
    ])
    session.flush()

    for j, d in enumerate(DATES):
        session.add(DailyPrice(symbol_id=1, date=d, open=1, high=2, low=0.5,
                               close=100.0 + j, volume=100))
        session.add(RelativeRank(symbol_id=1, date=d, group_name="テーマ",
                                 rs_ratio_rank_e21=j / 100.0))
        # sym 2: 偶数番目の日付はランク NULL
        session.add(RelativeRank(symbol_id=2, date=d, group_name="テーマ",
                                 rs_ratio_rank_e21=(j / 100.0) if j % 2 else None))
        session.add(DailyPrice(symbol_id=2, date=d, open=1, high=2, low=0.5,
                               close=200.0 + j, volume=100))

    for j, d in enumerate(DATES[-5:]):
        session.add(DailyPrice(symbol_id=3, date=d, open=1, high=2, low=0.5,
                               close=300.0 + j, volume=100))
        session.add(RelativeRank(symbol_id=3, date=d, group_name="テーマ",
                                 rs_ratio_rank_e21=0.3 + j / 100.0))

    future = TARGET_DATE + timedelta(days=3)
    session.add(DailyPrice(symbol_id=5, date=TARGET_DATE, open=1, high=2, low=0.5,
                           close=500.0, volume=100))
    session.add(DailyPrice(symbol_id=5, date=future, open=1, high=2, low=0.5,
                           close=555.0, volume=100))
    session.add(RelativeRank(symbol_id=5, date=TARGET_DATE, group_name="テーマ",
                             rs_ratio_rank_e21=0.5))
    session.add(RelativeRank(symbol_id=5, date=future, group_name="テーマ",
                             rs_ratio_rank_e21=0.9))

    # sym 6: 150〜190日前のデータのみ（日付窓の外）
    for j in range(10):
        stale_d = TARGET_DATE - timedelta(days=150 + j * 4)
        session.add(DailyPrice(symbol_id=6, date=stale_d, open=1, high=2, low=0.5,
                               close=600.0 + j, volume=100))
        session.add(RelativeRank(symbol_id=6, date=stale_d, group_name="テーマ",
                                 rs_ratio_rank_e21=0.6 + j / 100.0))

    session.add(Indicator(symbol_id=1, date=TARGET_DATE, ema_21=100.0,
                          rs_ratio_e21=1.1, rs_ratio_e63=1.2, rs_momentum_e21=1.3))
    session.add(Indicator(symbol_id=2, date=TARGET_DATE, ema_21=200.0))

    session.commit()
    yield session
    session.close()


ALL_IDS = [1, 2, 3, 4, 5, 6]


def test_preload_sparklines_matches_per_symbol_queries(db):
    """バルク版 sparkline が per-symbol 版と全銘柄で厳密に一致すること。"""
    from api.panel_builders import _get_sparkline_data, preload_sparklines

    pre = preload_sparklines(db, ALL_IDS, str(TARGET_DATE))
    for sid in ALL_IDS:
        expected = _get_sparkline_data(db, sid, str(TARGET_DATE))
        actual = _get_sparkline_data(db, sid, str(TARGET_DATE), preloaded=pre)
        assert actual == expected, f"symbol_id={sid} の sparkline が per-symbol 版と不一致"


def test_preload_sparklines_without_date_bound(db):
    """target_date=None（上限なし）は「各銘柄の全期間から最新30件」と等価であること。"""
    from api.panel_builders import _get_sparkline_data, preload_sparklines

    pre = preload_sparklines(db, ALL_IDS, None)
    # sym 5 は target_date より新しい行も含めて取得される
    far_future = str(TARGET_DATE + timedelta(days=365))
    for sid in ALL_IDS:
        expected = _get_sparkline_data(db, sid, far_future)
        actual = _get_sparkline_data(db, sid, far_future, preloaded=pre)
        assert actual == expected, f"symbol_id={sid} の sparkline（上限なし）が不一致"


def test_preload_close_histories_matches_per_symbol_queries(db):
    """バルク版の価格履歴（降順 close リスト）が per-symbol 版と厳密に一致すること。"""
    from api.panel_builders import preload_close_histories

    pre = preload_close_histories(db, ALL_IDS, str(TARGET_DATE), limit=22)
    for sid in ALL_IDS:
        rows = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sid,
            DailyPrice.date <= str(TARGET_DATE)
        ).order_by(desc(DailyPrice.date)).limit(22).all()
        expected = [r[0] for r in rows]
        assert pre.get(sid, []) == expected, f"symbol_id={sid} の価格履歴が不一致"


def test_preload_indicators_matches_per_symbol_queries(db):
    """バルク版 indicator が per-symbol 版（対象日1行）と一致すること。"""
    from api.panel_builders import preload_indicators

    pre = preload_indicators(db, ALL_IDS, str(TARGET_DATE))
    for sid in ALL_IDS:
        expected = db.query(Indicator).filter(
            Indicator.symbol_id == sid,
            Indicator.date == str(TARGET_DATE)
        ).first()
        assert pre.get(sid) is expected or (
            expected is not None and pre.get(sid) is not None
            and pre[sid].id == expected.id
        ), f"symbol_id={sid} の indicator が不一致"
    assert 4 not in pre  # データなし銘柄は含まれない


def test_build_panel_item_with_preload_equals_without(db):
    """preload あり/なしで _build_panel_item の出力が完全一致すること。"""
    from api.panel_builders import _build_panel_item, build_panel_preload

    target = str(TARGET_DATE)
    preload = build_panel_preload(db, ALL_IDS, target)
    for sid in [1, 2, 3, 5]:  # 4 は価格なしのため dp が存在しない
        sym = db.query(Symbol).get(sid)
        dp = db.query(DailyPrice).filter(
            DailyPrice.symbol_id == sid, DailyPrice.date == target).first()
        if dp is None:
            continue
        without = _build_panel_item(db, sym, dp, 0.5, 0.6, target, rank_val_14=0.4)
        with_pre = _build_panel_item(db, sym, dp, 0.5, 0.6, target, rank_val_14=0.4,
                                     preload=preload)
        assert with_pre == without, f"symbol_id={sid} の panel item が preload 有無で不一致"


def test_build_leading_item_with_preload_equals_without(db):
    """preload あり/なしで _build_leading_item の出力が完全一致すること。"""
    from api.panel_builders import _build_leading_item, build_panel_preload

    target = str(TARGET_DATE)
    preload = build_panel_preload(db, ALL_IDS, target)
    for sid in [1, 2, 3]:
        sym = db.query(Symbol).get(sid)
        dp = db.query(DailyPrice).filter(
            DailyPrice.symbol_id == sid, DailyPrice.date == target).first()
        if dp is None:
            continue
        without = _build_leading_item(db, sym, dp, target)
        with_pre = _build_leading_item(db, sym, dp, target, preload=preload)
        assert with_pre == without, f"symbol_id={sid} の leading item が preload 有無で不一致"


def test_preload_constituent_details_populates_latest_rank(db):
    """テーマ構成銘柄のプリロードが latest_rank を埋めること。

    回帰テスト: ranks_by_pair を組み立てながら preload["latest_rank"] へ
    代入していなかったため、/api/theme/{id} の構成銘柄ランクが全て 0% に
    なっていた（テーマ詳細画面の RSR21% / RSM21% がゼロ）。
    """
    from api.panel_builders import preload_constituent_details

    pre = preload_constituent_details(db, ALL_IDS)

    # sym 1: 各銘柄の最新価格日（DATES 末尾 = TARGET_DATE）のランクが入る
    r1 = pre["latest_rank"].get(1)
    assert r1 is not None, "sym 1 の latest_rank が未設定"
    assert r1.date == TARGET_DATE
    assert r1.rs_ratio_rank_e21 == pytest.approx(39 / 100.0)

    # sym 5: target_date より新しい行を持つ銘柄は、その最新日のランクを引く
    r5 = pre["latest_rank"].get(5)
    assert r5 is not None, "sym 5 の latest_rank が未設定"
    assert r5.date == TARGET_DATE + timedelta(days=3)
    assert r5.rs_ratio_rank_e21 == pytest.approx(0.9)

    # sym 4: 価格データなし → キー自体が存在しない（latest_price と同じ扱い）
    assert 4 not in pre["latest_rank"]
    assert 4 not in pre["latest_price"]

    # latest_rank の日付は latest_price の日付と一致する
    for sid, dp in pre["latest_price"].items():
        r = pre["latest_rank"].get(sid)
        if r is not None:
            assert r.date == dp.date, f"symbol_id={sid} のランク日付が価格日付と不一致"


def test_preload_constituent_details_respects_target_date(db):
    """target_date を指定したら、その日以前の最新の価格・ランクを返すこと。

    テーマ詳細（/api/theme/{id}?date=）で過去日を指定しても構成銘柄だけ
    最新日の値が出てしまう不具合の回帰テスト。
    """
    from api.panel_builders import preload_constituent_details

    past = TARGET_DATE - timedelta(days=5)  # DATES の j=34
    pre = preload_constituent_details(db, ALL_IDS, target_date=past)

    # sym 1: 指定日ちょうどの行
    assert pre["latest_price"][1].date == past
    assert pre["latest_price"][1].close == pytest.approx(100.0 + 34)
    r1 = pre["latest_rank"][1]
    assert r1 is not None and r1.date == past
    assert r1.rs_ratio_rank_e21 == pytest.approx(34 / 100.0)

    # 指定日以前に遡って履歴が並ぶ（未来の行が混ざらない）
    assert pre["recent_closes_21"][1][0] == pytest.approx(100.0 + 34)
    assert all(p.date <= past for p in pre["price_history_126"][1])

    # sym 5: 指定日より前にデータが無い銘柄はキーごと落ちる
    assert 5 not in pre["latest_price"]
    assert 5 not in pre["latest_rank"]

    # target_date=TARGET_DATE なら sym 5 は TARGET_DATE の行（future の行ではない）
    pre_t = preload_constituent_details(db, ALL_IDS, target_date=TARGET_DATE)
    assert pre_t["latest_price"][5].date == TARGET_DATE
    assert pre_t["latest_price"][5].close == pytest.approx(500.0)
    assert pre_t["latest_rank"][5].rs_ratio_rank_e21 == pytest.approx(0.5)


def test_build_panel_item_with_none_ranks_returns_none_not_zero(db):
    """判定不能（None）のランクは 0.0 ではなく None のまま DashboardPanelItem に反映されること（5-9b）。

    T4 の相対ランクが正しく NULL（判定不能）を返すようになった（5-8c）のに、
    _build_panel_item の `float(rank_val_xx or 0.0)` がそれを「最下位」という
    偽の値に潰している回帰テスト。
    """
    from api.panel_builders import _build_panel_item

    target = str(TARGET_DATE)
    sym = db.query(Symbol).get(1)
    dp = db.query(DailyPrice).filter(
        DailyPrice.symbol_id == 1, DailyPrice.date == target).first()

    item = _build_panel_item(
        db, sym, dp, None, None, target,
        rank_val_14=None, rank_val_mom=None, rank_val_mom63=None,
        rank_val_trend_14=None, rank_val_trend_21=None, rank_val_trend_63=None,
    )

    assert item.intensity_score is None
    assert item.rs_ratio_rank_e21 is None
    assert item.rs_ratio_rank_e63 is None
    assert item.rs_ratio_rank_e14 is None
    assert item.rs_momentum_rank_e21 is None
    assert item.rs_momentum_rank_e63 is None
    assert item.rs_trend_rank_s14 is None
    assert item.rs_trend_rank_s21 is None
    assert item.rs_trend_rank_s63 is None
    # レガシーフィールド（フロントエンド互換用）も同様に None を維持すべき
    assert item.rs_ratio_21_rank is None
    assert item.rs_ratio_63_rank is None
    assert item.rs_ratio_14_rank is None
    assert item.rs_momentum_21_rank is None
    assert item.rs_momentum_63_rank is None


def test_preload_constituent_details_target_date_accepts_str(db):
    """target_date は文字列（API のクエリパラメータ）でも date と同じ結果になること。"""
    from api.panel_builders import preload_constituent_details

    past = TARGET_DATE - timedelta(days=5)
    pre_date = preload_constituent_details(db, ALL_IDS, target_date=past)
    pre_str = preload_constituent_details(db, ALL_IDS, target_date=str(past))

    assert pre_str["latest_price"].keys() == pre_date["latest_price"].keys()
    for sid in pre_date["latest_price"]:
        assert pre_str["latest_price"][sid].date == pre_date["latest_price"][sid].date
        assert pre_str["recent_closes_21"][sid] == pre_date["recent_closes_21"][sid]
