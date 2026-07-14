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
    
    # 1. Stocks data
    # AAPL (Rise candidate: +1.5%)
    dp1 = DailyPrice(symbol_id=1, date=d, open=180, high=185, low=178, close=183, volume=1000)
    ind1 = Indicator(symbol_id=1, date=d, change_1d_pct=1.5, sma50_atr_mult=4.0)
    
    # MSFT (Fall candidate: -3.0%)
    dp4 = DailyPrice(symbol_id=4, date=d, open=400, high=402, low=385, close=388, volume=1200)
    ind4 = Indicator(symbol_id=4, date=d, change_1d_pct=-3.0, sma50_atr_mult=4.0, vol_surge_21=2.0)
    
    db.add_all([dp1, ind1, dp4, ind4])

    # 2. Themes data & RelativeRank (T4)
    # THEME1 (Strong theme: rs_ratio_rank_e21 = 0.9)
    dp2 = DailyPrice(symbol_id=2, date=d, open=100, high=105, low=98, close=102, volume=500)
    ind2 = Indicator(symbol_id=2, date=d, change_1d_pct=2.0, sma50_atr_mult=1.0)
    rr1 = RelativeRank(symbol_id=2, date=d, group_name="theme", rs_ratio_rank_e21=0.9)
    
    # THEME2 (Weak theme: rs_ratio_rank_e21 = 0.3)
    dp3 = DailyPrice(symbol_id=3, date=d, open=100, high=102, low=95, close=97, volume=300)
    ind3 = Indicator(symbol_id=3, date=d, change_1d_pct=-1.5, sma50_atr_mult=1.0)
    rr2 = RelativeRank(symbol_id=3, date=d, group_name="theme", rs_ratio_rank_e21=0.3)
    
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

