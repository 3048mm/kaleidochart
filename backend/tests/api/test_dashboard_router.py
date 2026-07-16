"""dashboard_router のパフォーマンス回帰テスト。

背景: GET /api/dashboard が全アクティブ銘柄（個別株 約2,800件含む）に対して
panel item を生成してからカテゴリ分岐で破棄しており、1銘柄あたり3クエリの
N+1 と合わせて 9,685 クエリ / 4.66秒 かかっていた
（doc/in_progress/dashboard_performance_plan.md）。

このテストは「クエリ数が表示対象外の銘柄数に比例しない」ことを検証する。
"""
import os
import sys
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(test_dir)
sys.path.insert(0, backend_dir)

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent

TARGET_DATE = date(2026, 7, 1)
# sparkline(30件) / 価格履歴(22件) の LIMIT を満たす日数を用意する
DATES = [TARGET_DATE - timedelta(days=i) for i in range(34, -1, -1)]


def _seed(session, n_individual: int, n_sectors: int = 2, n_themes: int = 3):
    """ダッシュボードが参照する全テーブルへ最小限のデータを投入する。"""
    symbols = [
        Symbol(id=1, ticker="SPY", exchange="NYSEARCA", name="SPDR S&P500", category="指標", active=1),
        Symbol(id=2, ticker="^VIX", exchange="INDEX", name="VIX", category="指標", active=1),
        Symbol(id=3, ticker="^VIX3M", exchange="INDEX", name="VIX3M", category="指標", active=1),
        Symbol(id=4, ticker="^GSPC", exchange="INDEX", name="S&P500", category="市場", active=1),
    ]
    next_id = 10
    for i in range(n_sectors):
        symbols.append(Symbol(id=next_id, ticker=f"SEC{i}", exchange="NYSEARCA",
                              name=f"Sector {i}", category="セクタ", active=1))
        next_id += 1
    
    themes_to_test = []
    for i in range(n_themes):
        t_sym = Symbol(id=next_id, ticker=f"THM{i}", exchange="VIRTUAL",
                              name=f"Theme {i}", category="テーマ", active=1)
        symbols.append(t_sym)
        themes_to_test.append(t_sym)
        next_id += 1
        
    stocks_to_test = []
    for i in range(n_individual):
        s_sym = Symbol(id=next_id, ticker=f"ST{i:04d}", exchange="NASDAQ",
                              name=f"Stock {i}", category="個別", active=1)
        symbols.append(s_sym)
        stocks_to_test.append(s_sym)
        next_id += 1
        
    session.add_all(symbols)
    session.flush()

    # Link stocks to themes
    for t in themes_to_test:
        for s in stocks_to_test:
            session.add(ThemeConstituent(theme_id=t.id, symbol_id=s.id, weight=1.0))

    for s in symbols:
        for j, d in enumerate(DATES):
            px = 100.0 + s.id * 0.1 + j
            session.add(DailyPrice(symbol_id=s.id, date=d,
                                   open=px, high=px + 1, low=px - 1, close=px, volume=1000))
            session.add(RelativeRank(symbol_id=s.id, date=d, group_name=s.category,
                                     rs_ratio_rank_e14=0.4, rs_ratio_rank_e21=0.5,
                                     rs_ratio_rank_e63=0.6,
                                     rs_trend_rank_s14=0.4, rs_trend_rank_s21=0.5,
                                     rs_trend_rank_s63=0.6))
        session.add(Indicator(symbol_id=s.id, date=TARGET_DATE,
                              sma_5=100.0, sma_21=100.0, sma_63=100.0, ema_21=100.0,
                              rs_ratio_e21=1.0, rs_momentum_e21=1.0,
                              rs_value=1.0, rs_value_e14=1.0, rs_value_e21=1.0, rs_value_e63=1.0, rs_value_e200=1.0,
                              rs_ratio_e5=1.0, rs_ratio_e14=1.0, rs_ratio_e63=1.0, rs_ratio_e200=1.0,
                              rs_momentum_e5=1.0, rs_momentum_e14=1.0, rs_momentum_e63=1.0, rs_momentum_e200=1.0,
                              rs_trend_s5=1.0, rs_trend_s14=1.0, rs_trend_s21=1.0, rs_trend_s63=1.0, rs_trend_s200=1.0,
                              vol_surge_rel_spy_21=1.0))

    session.add(MarketSignal(date=TARGET_DATE, market_phase="BULL",
                             distribution_days=1, market_trend_score=75.0,
                             vxv_vix_ratio=1.2, is_distribution_day=0, follow_through_day=0))
    session.commit()


def _make_client(tmp_path, name: str, n_individual: int, n_sectors: int = 2, n_themes: int = 3):
    """使い捨てファイル DB でシード済みの TestClient とクエリカウンタを返す。"""
    db_file = tmp_path / f"dash_{name}.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        _seed(session, n_individual, n_sectors=n_sectors, n_themes=n_themes)
    finally:
        session.close()

    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    from api.dashboard_router import router
    from api.deps import get_api_db

    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_api_db] = override_db
    return TestClient(app), counter


def test_dashboard_query_count_does_not_scale_with_individual_stocks(tmp_path):
    """クエリ数が「個別」銘柄数に比例してはならない（Plan A）。"""
    client_small, counter_small = _make_client(tmp_path, "small", n_individual=3)
    client_large, counter_large = _make_client(tmp_path, "large", n_individual=30)

    r1 = client_small.get("/api/dashboard")
    q_small = counter_small["n"]
    r2 = client_large.get("/api/dashboard")
    q_large = counter_large["n"]

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert q_small == q_large


def test_dashboard_query_count_does_not_scale_with_panel_items(tmp_path):
    """クエリ数がパネルアイテム数（セクタ/テーマ数）にも比例しないこと（Plan B）。"""
    client_small, counter_small = _make_client(tmp_path, "psmall", n_individual=0,
                                               n_sectors=2, n_themes=3)
    client_large, counter_large = _make_client(tmp_path, "plarge", n_individual=0,
                                               n_sectors=10, n_themes=25)

    r1 = client_small.get("/api/dashboard")
    q_small = counter_small["n"]
    r2 = client_large.get("/api/dashboard")
    q_large = counter_large["n"]

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert q_small == q_large


def test_dashboard_returns_expected_categories(tmp_path):
    """表示対象カテゴリのアイテムが欠けないこと（Plan A の同値性ガード）。"""
    client, _ = _make_client(tmp_path, "content", n_individual=5)
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()

    assert data["date"] == str(TARGET_DATE)
    assert [it["ticker"] for it in data["indices"]] == ["^GSPC"]
    assert {it["ticker"] for it in data["leading"]} == {"^VIX", "^VIX3M"}
    assert data["spy_feature"]["ticker"] == "SPY"
    assert {it["ticker"] for it in data["sectors"]} == {"SEC0", "SEC1"}
    assert {it["ticker"] for it in data["themes_top"]} == {"THM0", "THM1", "THM2"}


def test_theme_detail_query_count_does_not_scale_with_constituents(tmp_path):
    """構成銘柄数が 3 件の場合と 30 件の場合で、/theme/{id} のクエリ数が比例して増加してはならない（N+1問題の排除）。"""
    # Theme 0 has ID = 12
    client_small, counter_small = _make_client(tmp_path, "tsmall", n_individual=3, n_themes=1)
    client_large, counter_large = _make_client(tmp_path, "tlarge", n_individual=30, n_themes=1)

    # Trigger request to Theme 0 (id=12)
    # Clear query counters before call
    counter_small["n"] = 0
    r1 = client_small.get("/api/theme/12")
    q_small = counter_small["n"]

    counter_large["n"] = 0
    r2 = client_large.get("/api/theme/12")
    q_large = counter_large["n"]

    assert r1.status_code == 200
    assert r2.status_code == 200
    
    # Assert that the query counts are equal (meaning bulk preloading works and scales at O(1) queries)
    assert q_small == q_large, (
        f"構成銘柄3件で {q_small} クエリ、30件で {q_large} クエリ — "
        "テーマ詳細画面で構成銘柄ごとの N+1 クエリが発生している"
    )
