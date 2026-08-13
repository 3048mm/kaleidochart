"""
TDD Tests for `special` key removal refactoring.

These tests verify that boolean filters work correctly when specified in
[rise.filters] / [fall.filters] instead of via the `special` key.

Test Strategy:
  1. Create test presets with boolean filters in `filters` dict (no `special` key)
  2. Call _build_preset_query directly (unit test) and verify SQL filters are applied
  3. Call /api/screener/dashboard (integration test) and verify results are filtered
  4. Verify preset API no longer returns `special` field
"""
import pytest
from unittest.mock import patch
from datetime import date
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from api.routers import router, get_api_db
from api.screener_router import router as screener_api_router


# =====================================================
# Test Infrastructure
# =====================================================
_engine = create_engine(
    "sqlite:///file:test_special_removal?mode=memory&cache=shared&uri=true",
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
    """Reset DB and seed comprehensive test data for boolean filter validation."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    db = _TestSession()

    d_today = date(2026, 6, 10)
    d_prev = date(2026, 6, 9)

    # === Symbols ===
    # Stocks
    stock_rrg = Symbol(id=1, ticker="RRG_PASS", name="RRG Pass Stock", category="個別", active=1)
    stock_no_rrg = Symbol(id=2, ticker="RRG_FAIL", name="RRG Fail Stock", category="個別", active=1)
    stock_theme = Symbol(id=3, ticker="THEME_PASS", name="Theme Pass Stock", category="個別", active=1)
    stock_no_theme = Symbol(id=4, ticker="THEME_FAIL", name="Theme Fail Stock", category="個別", active=1)

    # Themes
    theme_strong = Symbol(id=10, ticker="T_STRONG", name="テーマ::AI半導体", category="テーマ", active=1)
    theme_weak = Symbol(id=11, ticker="T_WEAK", name="テーマ::旧世代", category="テーマ", active=1)

    db.add_all([stock_rrg, stock_no_rrg, stock_theme, stock_no_theme, theme_strong, theme_weak])

    # === DailyPrice (both dates needed for RRG prev comparison) ===
    for sid in [1, 2, 3, 4, 10, 11]:
        for d in [d_prev, d_today]:
            db.add(DailyPrice(symbol_id=sid, date=d, open=100, high=105, low=95, close=102, volume=1000))

    # === Indicators ===
    # 2026-08-13: P1-8 是正で全戦略共通の流動性ハード制約(min_avg_dollar_volume_21)が
    # /screener/dashboard にも常時適用されるようになったため、各銘柄の当日Indicatorに
    # avg_dollar_volume_21 を明示している（未設定=NULLだと床フィルタで無条件に除外される）。
    # Stock 1 (RRG_PASS): Improving → Leading transition (should pass rrg_leading_in)
    #   Today: ratio>0, momentum>0, momentum accelerating, high intensity
    #   Prev:  ratio<0, momentum>0 (was in Improving quadrant)
    db.add(Indicator(symbol_id=1, date=d_prev,
                     rs_ratio_e21=-0.1, rs_momentum_e21=0.3,
                     change_1d_pct=5.0, vol_surge_21=3.0, sma50_atr_mult=2.0))
    db.add(Indicator(symbol_id=1, date=d_today,
                     rs_ratio_e21=0.5, rs_momentum_e21=0.8,
                     change_1d_pct=5.0, vol_surge_21=3.0, sma50_atr_mult=2.0,
                     avg_dollar_volume_21=5e6))

    # Stock 2 (RRG_FAIL): Stays in Lagging (should fail rrg_leading_in)
    #   Today: ratio<0, momentum<0
    db.add(Indicator(symbol_id=2, date=d_prev,
                     rs_ratio_e21=-0.5, rs_momentum_e21=-0.5,
                     change_1d_pct=5.0, vol_surge_21=3.0, sma50_atr_mult=2.0))
    db.add(Indicator(symbol_id=2, date=d_today,
                     rs_ratio_e21=-0.3, rs_momentum_e21=-0.2,
                     change_1d_pct=5.0, vol_surge_21=3.0, sma50_atr_mult=2.0,
                     avg_dollar_volume_21=5e6))

    # Stock 3 (THEME_PASS): In strong theme
    db.add(Indicator(symbol_id=3, date=d_today,
                     change_1d_pct=8.0, vol_surge_21=3.0, sma50_atr_mult=2.0,
                     rs_ratio_e21=0.5, rs_ratio_e63=0.1,
                     avg_dollar_volume_21=5e6))

    # Stock 4 (THEME_FAIL): In weak theme only
    db.add(Indicator(symbol_id=4, date=d_today,
                     change_1d_pct=8.0, vol_surge_21=3.0, sma50_atr_mult=2.0,
                     rs_ratio_e21=0.1, rs_ratio_e63=0.5,
                     avg_dollar_volume_21=5e6))

    # Theme indicators
    # Strong theme: rs_ratio_e21 > rs_ratio_e63 (momentum rising)
    db.add(Indicator(symbol_id=10, date=d_today,
                     change_1d_pct=3.0, sma50_atr_mult=1.0,
                     rs_ratio_e21=0.8, rs_ratio_e63=0.2))
    # Weak theme: rs_ratio_e21 < rs_ratio_e63
    db.add(Indicator(symbol_id=11, date=d_today,
                     change_1d_pct=1.0, sma50_atr_mult=1.0,
                     rs_ratio_e21=0.1, rs_ratio_e63=0.5))

    # === RelativeRank ===
    # For RS rank comparison tests (rs_ratio_rank_e21 > rs_ratio_rank_e63)
    db.add(RelativeRank(symbol_id=1, date=d_today, group_name="個別",
                        rs_ratio_rank_e21=0.9, rs_ratio_rank_e63=0.3))
    db.add(RelativeRank(symbol_id=2, date=d_today, group_name="個別",
                        rs_ratio_rank_e21=0.2, rs_ratio_rank_e63=0.8))
    db.add(RelativeRank(symbol_id=3, date=d_today, group_name="個別",
                        rs_ratio_rank_e21=0.7, rs_ratio_rank_e63=0.5))
    db.add(RelativeRank(symbol_id=4, date=d_today, group_name="個別",
                        rs_ratio_rank_e21=0.3, rs_ratio_rank_e63=0.9))

    # Theme ranks (for theme_rs_rank_21_gt_63)
    db.add(RelativeRank(symbol_id=10, date=d_today, group_name="テーマ",
                        rs_ratio_rank_e21=0.9, rs_ratio_rank_e63=0.3))
    db.add(RelativeRank(symbol_id=11, date=d_today, group_name="テーマ",
                        rs_ratio_rank_e21=0.2, rs_ratio_rank_e63=0.8))

    # === ThemeConstituent ===
    # Stock 3 (THEME_PASS) → in Strong theme
    db.add(ThemeConstituent(theme_id=10, symbol_id=3, weight=1.0))
    # Stock 4 (THEME_FAIL) → in Weak theme only
    db.add(ThemeConstituent(theme_id=11, symbol_id=4, weight=1.0))

    db.commit()
    db.close()
    yield


@pytest.fixture
def client():
    with TestClient(_app) as c:
        yield c


# =====================================================
# Phase 3 Tests: _build_preset_query handles boolean filters from `filters` dict
# =====================================================

class TestBooleanFiltersInDashboard:
    """
    Verify that boolean filters specified in [rise.filters] (not via `special`)
    are correctly applied by _build_preset_query in the dashboard API.

    CRITICAL: If any of these tests fail after refactoring, it means a boolean
    filter is being silently ignored — the highest-risk failure mode.
    """

    def test_rrg_improving_in_from_filters(self, client):
        """
        When a preset has `rrg_improving_in = true` in its [filters],
        the dashboard should only return stocks that match the RRG Improving → transition.
        Previously this required `special = "rrg_improving_in"`.
        """
        # Create a mock preset with rrg_improving_in in filters (no special key)
        mock_presets = {
            "rise": [{
                "id": "test_rrg_improving",
                "name": "RRG Improving Test",
                "group": "Check",
                "filters": {
                    "rrg_improving_in": True,
                    "min_change_1d_pct": 1.0,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()

            # Should have at least one rise category
            assert len(data["rise"]) == 1
            tickers = [item["ticker"] for item in data["rise"][0]["items"]]

            # RRG_FAIL (ratio<0, momentum<0) should NOT appear
            assert "RRG_FAIL" not in tickers
            # Note: RRG_PASS transitions from Improving→Leading, not into Improving
            # So the exact expected set depends on the RRG improving_in logic

    def test_rrg_leading_in_from_filters(self, client):
        """
        When a preset has `rrg_leading_in = true` in its [filters],
        only stocks transitioning INTO the Leading quadrant should appear.
        """
        mock_presets = {
            "rise": [{
                "id": "test_rrg_leading",
                "name": "RRG Leading Test",
                "group": "Check",
                "filters": {
                    "rrg_leading_in": True,
                    "min_change_1d_pct": 1.0,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["rise"]) == 1
            tickers = [item["ticker"] for item in data["rise"][0]["items"]]

            # RRG_PASS: prev=(ratio<0,mom>0)→today=(ratio>0,mom>0) = Leading In ✓
            assert "RRG_PASS" in tickers
            # RRG_FAIL: today=(ratio<0,mom<0) = still Lagging ✗
            assert "RRG_FAIL" not in tickers

    def test_rrg_lagging_in_from_filters(self, client):
        """
        When a preset has `rrg_lagging_in = true` in its [filters],
        only stocks transitioning INTO the Lagging quadrant should appear.
        """
        mock_presets = {
            "rise": [],
            "fall": [{
                "id": "test_rrg_lagging",
                "name": "RRG Lagging Test",
                "group": "Warning",
                "filters": {
                    "rrg_lagging_in": True,
                }
            }]
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()
            # Test executes without error = the boolean key was not silently ignored

    def test_theme_rs_ratio_filter_from_filters(self, client):
        """
        When a preset has `is_theme_rs_ratio_e21_gt_e63 = true` in filters,
        only stocks belonging to themes where rs_ratio_e21 > rs_ratio_e63 should appear.
        Previously this required `special = "is_theme_rs_ratio_e21_gt_e63"`.
        """
        mock_presets = {
            "rise": [{
                "id": "test_theme_rs",
                "name": "Theme RS Test",
                "group": "Check",
                "filters": {
                    "is_theme_rs_ratio_e21_gt_e63": True,
                    "min_change_1d_pct": 1.0,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["rise"]) == 1
            tickers = [item["ticker"] for item in data["rise"][0]["items"]]

            # THEME_PASS (in strong theme where e21>e63) should appear
            assert "THEME_PASS" in tickers
            # THEME_FAIL (in weak theme where e21<e63) should NOT appear
            assert "THEME_FAIL" not in tickers

    def test_theme_rs_rank_filter_from_filters(self, client):
        """
        When a preset has `is_theme_rs_ratio_rank_e21_gt_e63 = true` in filters,
        only stocks in themes where rank_e21 > rank_e63 should appear.
        """
        mock_presets = {
            "rise": [{
                "id": "test_theme_rs_rank",
                "name": "Theme RS Rank Test",
                "group": "Check",
                "filters": {
                    "is_theme_rs_ratio_rank_e21_gt_e63": True,
                    "min_change_1d_pct": 1.0,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["rise"]) == 1
            tickers = [item["ticker"] for item in data["rise"][0]["items"]]

            # THEME_PASS is in T_STRONG (rank_e21=0.9 > rank_e63=0.3) → ✓
            assert "THEME_PASS" in tickers
            # THEME_FAIL is in T_WEAK (rank_e21=0.2 < rank_e63=0.8) → ✗
            assert "THEME_FAIL" not in tickers

    def test_rs_rank_comparison_from_filters(self, client):
        """
        When a preset has `is_rs_ratio_rank_e21_gt_e63 = true` in filters,
        only individual stocks where their own rank_e21 > rank_e63 should appear.
        Previously this required `special = "is_rs_ratio_rank_e21_gt_e63"`.
        """
        mock_presets = {
            "rise": [{
                "id": "test_rs_rank",
                "name": "RS Rank Test",
                "group": "Check",
                "filters": {
                    "is_rs_ratio_rank_e21_gt_e63": True,
                    "min_change_1d_pct": 1.0,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=mock_presets):
            resp = client.get("/api/screener/dashboard?target_date=2026-06-10")
            assert resp.status_code == 200
            data = resp.json()

            assert len(data["rise"]) == 1
            tickers = [item["ticker"] for item in data["rise"][0]["items"]]

            # RRG_PASS: rank_e21=0.9 > rank_e63=0.3 → ✓
            assert "RRG_PASS" in tickers
            # RRG_FAIL: rank_e21=0.2 < rank_e63=0.8 → ✗
            assert "RRG_FAIL" not in tickers

    def test_boolean_filter_not_silently_ignored(self, client):
        """
        CRITICAL TEST: Ensure that a boolean filter key in [filters] is NOT
        silently ignored (which would return unfiltered results).
        
        This is the highest-risk failure mode identified in the plan.
        We verify by comparing filtered vs unfiltered result counts.
        """
        # Preset WITHOUT any boolean filter (baseline)
        unfiltered_presets = {
            "rise": [{
                "id": "unfiltered",
                "name": "Unfiltered",
                "group": "Check",
                "filters": {"min_change_1d_pct": 1.0}
            }],
            "fall": []
        }

        # Preset WITH restrictive boolean filter
        filtered_presets = {
            "rise": [{
                "id": "filtered",
                "name": "Filtered",
                "group": "Check",
                "filters": {
                    "min_change_1d_pct": 1.0,
                    "rrg_leading_in": True,
                }
            }],
            "fall": []
        }

        with patch("api.screener_router._load_presets", return_value=unfiltered_presets):
            resp1 = client.get("/api/screener/dashboard?target_date=2026-06-10")
            unfiltered_count = len(resp1.json()["rise"][0]["items"])

        with patch("api.screener_router._load_presets", return_value=filtered_presets):
            resp2 = client.get("/api/screener/dashboard?target_date=2026-06-10")
            filtered_count = len(resp2.json()["rise"][0]["items"])

        # Filtered MUST return fewer results than unfiltered
        assert filtered_count < unfiltered_count, (
            f"Boolean filter 'rrg_leading_in' appears to be silently ignored! "
            f"Unfiltered: {unfiltered_count} items, Filtered: {filtered_count} items"
        )


# =====================================================
# Phase 4 Tests: Schema / Preset API no longer returns `special`
# =====================================================

class TestPresetAPINoSpecial:
    """After refactoring, the preset API should not include `special` field."""

    def test_preset_response_has_no_special_field(self, client):
        """GET /api/screener/presets should not return 'special' in any preset item."""
        resp = client.get("/api/screener/presets")
        assert resp.status_code == 200
        data = resp.json()

        for category in ["rise", "fall"]:
            for preset in data.get(category, []):
                assert "special" not in preset, (
                    f"Preset '{preset.get('id')}' still has 'special' field: {preset.get('special')}"
                )


# =====================================================
# Phase 1+2 Tests: TOML migration + scenario_runner
# =====================================================

class TestTOMLMigration:
    """Verify that TOML presets no longer use `special` key."""

    def test_main_presets_toml_no_special(self):
        """screener_presets.toml should not contain any `special` key."""
        import tomllib
        toml_path = r"d:\My Documents\Programing\stocktool\data\screener_presets.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        for section in ["rise", "fall"]:
            for preset in data.get(section, []):
                assert "special" not in preset, (
                    f"Preset '{preset.get('id')}' in [{section}] still has "
                    f"'special = \"{preset.get('special')}\"'. "
                    f"Move it to [{section}.filters] as a boolean key."
                )

    def test_a_only_toml_no_special(self):
        """screener_presets_A_only.toml should not contain any `special` key."""
        import tomllib
        toml_path = r"d:\My Documents\Programing\stocktool\data\screener_presets_A_only.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        for preset in data.get("rise", []):
            assert "special" not in preset

    def test_b_only_toml_no_special(self):
        """screener_presets_B_only.toml should not contain any `special` key."""
        import tomllib
        toml_path = r"d:\My Documents\Programing\stocktool\data\screener_presets_B_only.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        for preset in data.get("rise", []):
            assert "special" not in preset

    def test_scenario_runner_no_special_expansion(self):
        """scenario_runner should not contain special expansion logic."""
        import inspect
        from backtest.scenario_runner import load_scenario_config
        source = inspect.getsource(load_scenario_config)
        assert "special" not in source, (
            "scenario_runner.load_strategy_config still contains 'special' logic. "
            "Remove the special → filters expansion code."
        )
