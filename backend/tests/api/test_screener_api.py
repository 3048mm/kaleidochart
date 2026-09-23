import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from api.routers import router, get_api_db
from api.screener_router import router as screener_api_router

# Create shared in-memory SQLite DB
_engine = create_engine(
    "sqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def _get_test_db():
    db = _TestSession()
    try:
        yield db
    finally:
        db.close()


def _create_app():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(screener_api_router, prefix="/api")
    app.dependency_overrides[get_api_db] = _get_test_db
    return app


_app = _create_app()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset DB for each test and seed base data."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    db = _TestSession()
    
    # Add symbols
    # Stocks
    aapl = Symbol(id=1, ticker="AAPL", name="Apple", category="個別", active=1)
    msft = Symbol(id=4, ticker="MSFT", name="Microsoft", category="個別", active=1)
    
    # Themes
    theme1 = Symbol(id=2, ticker="THEME1", name="最強テーマ::半導体", category="テーマ", active=1)
    theme2 = Symbol(id=3, ticker="THEME2", name="最弱テーマ::航空", category="テーマ", active=1)
    
    db.add_all([aapl, msft, theme1, theme2])

    from datetime import date
    d = date(2026, 5, 20)
    
    # 特殊フィルタ（pandas 側, screener_cross_section.build_cross_section が merge する列）が
    # 全銘柄同一列で None を混在させると "float > None" の TypeError になる
    # （本番では T3/T4 が一括で全列を埋めるため起きないが、この最小フィクスチャでは
    # 明示的に埋めておかないと再現してしまう。2026-08-13、screener_dashboard の
    # 全プリセット error=None 回帰テストの追加で判明）。
    _SPECIAL_FILTER_DEFAULTS = dict(rs_trend_s14=1.0, rs_trend_s21=1.0, rs_macd_hist_21=0.1)

    # 1. Stocks data
    # AAPL (Rise candidate: +1.5%)
    dp1 = DailyPrice(symbol_id=1, date=d, open=180, high=185, low=178, close=183, volume=1000)
    ind1 = Indicator(symbol_id=1, date=d, change_1d_pct=1.5, sma50_atr_mult=4.0, avg_dollar_volume_21=5e6,
                      **_SPECIAL_FILTER_DEFAULTS)

    # MSFT (Fall candidate: -3.0%)
    dp4 = DailyPrice(symbol_id=4, date=d, open=400, high=402, low=385, close=388, volume=1200)
    ind4 = Indicator(symbol_id=4, date=d, change_1d_pct=-3.0, sma50_atr_mult=4.0, vol_surge_21=2.0, avg_dollar_volume_21=5e6,
                      **_SPECIAL_FILTER_DEFAULTS)

    db.add_all([dp1, ind1, dp4, ind4])

    # 2. Themes data & RelativeRank (T4)
    # THEME1 (Strong theme: rs_ratio_rank_e21 = 0.9)
    dp2 = DailyPrice(symbol_id=2, date=d, open=100, high=105, low=98, close=102, volume=500)
    ind2 = Indicator(symbol_id=2, date=d, change_1d_pct=2.0, sma50_atr_mult=1.0, **_SPECIAL_FILTER_DEFAULTS)
    rr1 = RelativeRank(symbol_id=2, date=d, group_name="theme", rs_ratio_rank_e21=0.9,
                        rs_ratio_rank_e14=0.5, rs_ratio_rank_e63=0.5,
                        rs_trend_rank_s14=0.5, rs_trend_rank_s21=0.5, rs_trend_rank_s63=0.5)

    # THEME2 (Weak theme: rs_ratio_rank_e21 = 0.3)
    dp3 = DailyPrice(symbol_id=3, date=d, open=100, high=102, low=95, close=97, volume=300)
    ind3 = Indicator(symbol_id=3, date=d, change_1d_pct=-1.5, sma50_atr_mult=1.0, **_SPECIAL_FILTER_DEFAULTS)
    rr2 = RelativeRank(symbol_id=3, date=d, group_name="theme", rs_ratio_rank_e21=0.3,
                        rs_ratio_rank_e14=0.5, rs_ratio_rank_e63=0.5,
                        rs_trend_rank_s14=0.5, rs_trend_rank_s21=0.5, rs_trend_rank_s63=0.5)

    db.add_all([dp2, ind2, rr1, dp3, ind3, rr2])

    # 3. Mappings (ThemeConstituent)
    # Both AAPL and MSFT are in THEME1 (0.9) and THEME2 (0.3)
    tc1 = ThemeConstituent(theme_id=2, symbol_id=1, weight=1.0)
    tc2 = ThemeConstituent(theme_id=3, symbol_id=1, weight=1.0)
    tc3 = ThemeConstituent(theme_id=2, symbol_id=4, weight=1.0)
    tc4 = ThemeConstituent(theme_id=3, symbol_id=4, weight=1.0)
    
    db.add_all([tc1, tc2, tc3, tc4])

    db.commit()
    db.close()
    yield


@pytest.fixture
def client():
    with TestClient(_app) as c:
        yield c


def test_screener_dashboard_returns_all_presets(client):
    """Verify that all presets defined in TOML are returned."""
    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "rise" in data
    assert "fall" in data


def test_screener_dashboard_theme_rank_assignment(client):
    """
    TDD Test: Verify that the screener dashboard assigns the strongest theme for Rise categories,
    and the weakest theme for Fall categories to each stock item.
    """
    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    # 1. Verify Rise (Strongest Theme: THEME1, rs_ratio_21 = 0.9)
    # Find AAPL in 'rise' categories (e.g. check_1d_gain)
    aapl_item = None
    for category in data["rise"]:
        for item in category["items"]:
            if item["ticker"] == "AAPL":
                aapl_item = item
                break
        if aapl_item:
            break
            
    assert aapl_item is not None, "AAPL should be found in Rise category items"
    # Should select THEME1 (0.9) because it's in Rise category and THEME1 is stronger than THEME2 (0.3)
    assert aapl_item.get("theme_ticker") == "THEME1"
    assert aapl_item.get("theme_name") == "半導体"
    assert aapl_item.get("theme_rs_ratio") == 0.9

    # 2. Verify Fall (Weakest Theme: THEME2, rs_ratio_21 = 0.3)
    # Find MSFT in 'fall' categories (e.g. Warning or Rebound sign)
    msft_item = None
    for category in data["fall"]:
        for item in category["items"]:
            if item["ticker"] == "MSFT":
                msft_item = item
                break
        if msft_item:
            break

    assert msft_item is not None, "MSFT should be found in Fall category items"
    assert msft_item.get("theme_ticker") == "THEME2"
    assert msft_item.get("theme_name") == "航空"
    assert msft_item.get("theme_rs_ratio") == 0.3


def test_screener_api_legacy_alias_normalization(client):
    """Verify that legacy parameter names are normalized and correctly filtered in API."""
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)
    
    # Seed specific RelativeRank values for the stocks
    # AAPL (1): rs_ratio_rank_e21 = 0.8
    # MSFT (4): rs_ratio_rank_e21 = 0.4
    rr_aapl = RelativeRank(symbol_id=1, date=d, group_name="stock", rs_ratio_rank_e21=0.8)
    rr_msft = RelativeRank(symbol_id=4, date=d, group_name="stock", rs_ratio_rank_e21=0.4)
    
    # Seed intraday close/open values for change_oc_pct normalization check
    # AAPL (1): close=183, open=180 -> change_oc_pct = (183-180)/180 * 100 = 1.66%
    # MSFT (4): close=388, open=400 -> change_oc_pct = (388-400)/400 * 100 = -3.00%
    
    db.add_all([rr_aapl, rr_msft])
    db.commit()
    db.close()
    
    # 1. Test legacy 'min_rs_ratio_21_rank' (should normalize to 'min_rs_ratio_rank_e21')
    # Filter: min_rs_ratio_21_rank = 0.6. Only AAPL (0.8) should pass.
    resp = client.get("/api/screener?target_date=2026-05-20&min_rs_ratio_21_rank=0.6")
    assert resp.status_code == 200
    items = resp.json()
    tickers = [x["ticker"] for x in items]
    assert "AAPL" in tickers
    assert "MSFT" not in tickers

    # 2. Test legacy 'min_change_oc_pct' (should normalize to 'min_change_intraday_pct')
    # Filter: min_change_oc_pct = 0.0. AAPL (1.66%) should pass, MSFT (-3.00%) should fail.
    resp2 = client.get("/api/screener?target_date=2026-05-20&min_change_oc_pct=0.0")
    assert resp2.status_code == 200
    items2 = resp2.json()
    tickers2 = [x["ticker"] for x in items2]
    assert "AAPL" in tickers2
    assert "MSFT" not in tickers2


def test_screener_api_excludes_illiquid_symbols(client):
    """2026-07-27: 全戦略共通の流動性ハード制約(min_avg_dollar_volume_21)が
    生スクリーナーAPIでも常時適用され、クエリパラメータに関わらず低流動性銘柄が
    除外されること（doc/issue_list.md P0 対応）。
    """
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)

    # AAPL(symbol_id=1)は基本fixtureで avg_dollar_volume_21=5e6 (閾値$2M超で通過)。
    # MSFT(symbol_id=4)の avg_dollar_volume_21 を閾値未満まで下げて低流動性化する。
    ind4 = db.query(Indicator).filter(Indicator.symbol_id == 4, Indicator.date == d).first()
    ind4.avg_dollar_volume_21 = 500_000.0
    db.commit()
    db.close()

    # フィルタ無し（全銘柄対象のはず）でも、低流動性銘柄は常に除外される
    resp = client.get("/api/screener?target_date=2026-05-20")
    assert resp.status_code == 200
    tickers = [x["ticker"] for x in resp.json()]
    assert "AAPL" in tickers
    assert "MSFT" not in tickers


def test_screener_api_excludes_theme_rows_from_results(client):
    """2026-08-05: category='テーマ'（実在ETF・仮想合成指数）は実売買不可能なため、
    `/api/screener` の結果行には常に含めない（backtest側の apply_filters_to_df と
    同じ扱い。doc/issue_list.md 対応、フロントの「RS MACD and Theme」等で
    仮想テーマティッカーが結果に混入していた実害を受けて修正）。
    """
    # THEME1/THEME2 に流動性ハード制約(avg_dollar_volume_21)を満たす値を与える
    # （テーマの base fixture は未設定=NULLのため、流動性フィルタ自体で偶然除外されて
    # しまい、テーマ除外ロジックを検証できなくなるのを防ぐ）。
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)
    for sid in (2, 3):
        ind = db.query(Indicator).filter(Indicator.symbol_id == sid, Indicator.date == d).first()
        ind.avg_dollar_volume_21 = 5e6
    db.commit()
    db.close()

    # THEME1 (symbol_id=2) は change_1d_pct=2.0 で min_change_1d_pct=1.0 を満たすが、
    # テーマ自体は結果から除外され、条件を満たす個別銘柄(AAPL)だけが残ること。
    resp = client.get("/api/screener?target_date=2026-05-20&min_change_1d_pct=1.0")
    assert resp.status_code == 200
    tickers = [x["ticker"] for x in resp.json()]
    assert "AAPL" in tickers
    assert "THEME1" not in tickers
    assert "THEME2" not in tickers


def test_screener_dashboard_unknown_filter_key_isolated_as_error(client, monkeypatch):
    """2026-08-13: 未知キーを含むプリセットは items=[] かつ error 付きで返り、
    他のプリセットは正常表示を続けること（U-1 (b)。
    doc/completed/screener_filter_unification_plan.md §3 Phase 1）。
    """
    import api.screener_router as router_module

    fake_presets = {
        "rise": [
            {"id": "broken_preset", "name": "Broken", "group": "Check",
             "filters": {"min_totally_unknown_key_xyz": 1.0}},
            {"id": "ok_preset", "name": "OK", "group": "Check",
             "filters": {"min_change_1d_pct": 0.0}},
        ],
        "fall": [],
    }
    monkeypatch.setattr(router_module, "_load_presets", lambda: fake_presets)

    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    broken = next(c for c in data["rise"] if c["id"] == "broken_preset")
    ok = next(c for c in data["rise"] if c["id"] == "ok_preset")

    assert broken["items"] == []
    assert broken["error"] is not None
    assert "min_totally_unknown_key_xyz" in broken["error"]

    assert ok["error"] is None


def test_screener_dashboard_current_presets_have_no_errors(client):
    """現行の data/screener_presets.toml の全プリセットで error が None であること
    （fail-loud 化によって既存プリセットの解釈が変わっていないことの固定）。
    """
    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    for cat in data.get("rise", []) + data.get("fall", []):
        assert cat.get("error") is None, f"preset '{cat['id']}' unexpectedly errored: {cat.get('error')}"


def test_screener_dashboard_excludes_theme_rows_from_items(client):
    """2026-08-05: ダッシュボードの各プリセットカードの items にもテーマ自体は含めない。

    THEME1 に実在プリセット `check_1d_gain`（min_change_1d_pct=4.0 /
    min_vol_surge_rel_spy_21=1.0 / max_sma50_atr_mult=6.0 / min_adr_pct_21=4.0 /
    min_market_cap=1e9）を満たす値を与える（market_cap はテーマ免除のため未設定でよい）。
    """
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)
    ind2 = db.query(Indicator).filter(Indicator.symbol_id == 2, Indicator.date == d).first()
    ind2.change_1d_pct = 10.0
    ind2.vol_surge_rel_spy_21 = 2.0
    ind2.sma50_atr_mult = 1.0
    ind2.adr_pct_21 = 10.0
    db.commit()
    db.close()

    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    for cat in data.get("rise", []) + data.get("fall", []):
        for item in cat.get("items", []):
            assert item["ticker"] not in ("THEME1", "THEME2")


def test_screener_dashboard_excludes_illiquid_symbols(client):
    """P1-8 是正の回帰テスト（2026-08-13）: 全戦略共通の流動性ハード制約
    (min_avg_dollar_volume_21) が `/screener/dashboard` にも適用され、閾値未満の銘柄は
    結果に出ないこと。従来 `/screener` にのみ適用され、dashboard には適用されて
    いなかった（doc/completed/screener_filter_unification_plan.md §7 P1-8）。

    AAPL に実在プリセット `check_1d_gain`（min_change_1d_pct=4.0 /
    min_vol_surge_rel_spy_21=1.0 / max_sma50_atr_mult=6.0 / min_adr_pct_21=4.0 /
    min_market_cap=1e9）を満たす値を与えつつ、avg_dollar_volume_21 だけ閾値($2M)未満
    にする。
    """
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)
    ind1 = db.query(Indicator).filter(Indicator.symbol_id == 1, Indicator.date == d).first()
    ind1.change_1d_pct = 10.0
    ind1.vol_surge_rel_spy_21 = 2.0
    ind1.sma50_atr_mult = 1.0
    ind1.adr_pct_21 = 10.0
    ind1.avg_dollar_volume_21 = 500_000.0  # 閾値 $2M 未満(P1-8実測: 最悪ANPAで$108,939)
    dp1 = db.query(DailyPrice).filter(DailyPrice.symbol_id == 1, DailyPrice.date == d).first()
    dp1.market_cap = 2e9
    db.commit()
    db.close()

    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    check_cat = next(c for c in data["rise"] if c["id"] == "check_1d_gain")
    tickers = [item["ticker"] for item in check_cat["items"]]
    assert "AAPL" not in tickers


def test_screener_dashboard_applied_filters_include_liquidity_floor(client):
    """applied_filters に常時適用の制約(min_avg_dollar_volume_21)が含まれること
    （§5 Phase 1「applied_filters の記録・出力」。流動性床が2度にわたり無効化されていた
    事故は、これがあれば結果を見た瞬間に発覚していた）。
    """
    resp = client.get("/api/screener/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    for cat in data.get("rise", []) + data.get("fall", []):
        assert cat.get("applied_filters") is not None
        assert "min_avg_dollar_volume_21" in cat["applied_filters"]
        assert "exclude_theme_category" in cat["applied_filters"]



def test_screener_api_null_rank_is_returned_as_null(client):
    """min_periods_warmup 計画 5-25: RS ランクが NULL（判定不能）の銘柄は、
    ランク 0.0（最悪）ではなく null で返す。

    旧実装は `_float_or`（NaN→0.0）で NULL を 0.0 に潰していたため、5-9b で
    null 安全にしたフロントが一度も効かず、dashboard API（NULL を返す）とも不整合だった。
    NULL ランクの銘柄は結果の末尾に並ぶ（ソートで落ちないこと）。
    """
    db = _TestSession()
    from datetime import date
    d = date(2026, 5, 20)
    # AAPL だけランクを持つ。MSFT は relative_ranks 行なし＝ランク NULL。
    db.add(RelativeRank(symbol_id=1, date=d, group_name="stock",
                        rs_ratio_rank_e21=0.8, rs_ratio_rank_e63=0.7))
    db.commit()
    db.close()

    resp = client.get("/api/screener?target_date=2026-05-20")
    assert resp.status_code == 200
    items = {x["ticker"]: x for x in resp.json()}

    # 陽性対照: ランクを持つ銘柄は値がそのまま返る（0.0 でも null でもない）
    assert items["AAPL"]["rs_ratio_rank_e21"] == 0.8
    assert items["AAPL"]["rs_ratio_rank_e63"] == 0.7
    assert items["AAPL"]["rs_ratio_21_rank"] == 0.8
    assert items["AAPL"]["rs_ratio_63_rank"] == 0.7

    # NULL ランクは null のまま（0.0 にしない）
    assert "MSFT" in items
    for key in ("rs_ratio_rank_e21", "rs_ratio_rank_e63", "rs_ratio_21_rank", "rs_ratio_63_rank"):
        assert items["MSFT"][key] is None, f"{key} が NULL でなく {items['MSFT'][key]!r}"

    # NULL ランクは末尾（ランクを持つ銘柄より後ろ）
    tickers = [x["ticker"] for x in resp.json()]
    assert tickers.index("AAPL") < tickers.index("MSFT")
