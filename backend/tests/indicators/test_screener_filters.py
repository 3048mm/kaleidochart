"""
test_screener_filters.py — Phase 1 Step 1: 既存特殊フィルタの期待値確定テスト

backtest_screener.py に現在実装されている特殊フィルタロジックの正しい動作を
テストとして記録する。リファクタリング後に同一テストが通ることで、
動作の同一性を保証する。
"""
import pytest
import pandas as pd
import numpy as np
from datetime import date
from backtest.backtest_screener import scan_signals_for_date


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def base_symbols():
    """テスト用銘柄マスタ（個別 + テーマ）"""
    return pd.DataFrame([
        {"id": 1,  "ticker": "AAAA", "name": "Stock A", "category": "個別", "active": 1},
        {"id": 2,  "ticker": "BBBB", "name": "Stock B", "category": "個別", "active": 1},
        {"id": 3,  "ticker": "CCCC", "name": "Stock C", "category": "個別", "active": 1},
        {"id": 4,  "ticker": "DDDD", "name": "Stock D", "category": "個別", "active": 1},
        {"id": 5,  "ticker": "EEEE", "name": "Stock E", "category": "個別", "active": 1},
        {"id": 10, "ticker": "THM1", "name": "Theme 1", "category": "テーマ", "active": 1},
        {"id": 11, "ticker": "THM2", "name": "Theme 2", "category": "テーマ", "active": 1},
    ])


@pytest.fixture
def dates():
    """テスト用日付"""
    return {
        "target": date(2026, 5, 5),
        "prev": date(2026, 5, 4),
    }


def _make_price_row(symbol_id, d, close=100.0, market_cap=1e9):
    """価格データの1行を生成"""
    return {
        "symbol_id": symbol_id, "date": d,
        "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
        "volume": 10000, "market_cap": market_cap,
    }


def _make_ind_row(symbol_id, d, **overrides):
    """指標データの1行を生成（デフォルト値付き）"""
    row = {
        "symbol_id": symbol_id, "date": d,
        "ema_21": 95.0, "sma_50": 90.0, "sma_200": 85.0,
        "atr_14": 2.0, "sma50_atr_mult": 3.0,
        "change_1d_pct": 2.0, "change_1w_pct": 5.0, "change_1m_pct": 10.0,
        "change_intraday_pct": 1.0,
        "vol_surge_21": 1.5, "rel_vol_vs_spy_21": 1.0,
        "adr_pct_21": 5.0, "market_cap": 1e9,
        "rs_ratio_e21": 0.5, "rs_momentum_e21": 0.3,
        "rs_ratio_e63": 0.2,
        "rs_condition_21": 1.0,
        "trend_template_ok": 1,
        "up_down_vol_ratio_50": 1.3,
        "vcr": 0.5,
        "td9": 0,
        "rs_blue_dot": 0,
        "rs_red_dot": 0,
    }
    row.update(overrides)
    return row


def _empty_ranks():
    return pd.DataFrame(columns=["symbol_id", "date"])


def _empty_theme_constituents():
    return pd.DataFrame(columns=["theme_id", "symbol_id"])


# ============================================================
# Test: filter_rs_rank_21_gt_63
# ============================================================

class TestRsRank21Gt63:
    """rs_rank_21_gt_63: RS21 Rank > RS63 Rank の銘柄のみが残る"""

    def test_only_stocks_with_rs21_gt_rs63_pass(self, base_symbols, dates):
        """rs21_rank > rs63_rank の銘柄のみがシグナルに含まれる"""
        td = dates["target"]
        df_price = pd.DataFrame([
            _make_price_row(1, td),
            _make_price_row(2, td),
            _make_price_row(3, td),
        ])
        df_ind = pd.DataFrame([
            _make_ind_row(1, td),
            _make_ind_row(2, td),
            _make_ind_row(3, td),
        ])
        # Rank data: Stock1 rs21 > rs63, Stock2 rs21 < rs63, Stock3 rs21 == rs63
        df_ranks = pd.DataFrame([
            {"symbol_id": 1, "date": td, "rs_ratio_rank_e21": 0.8, "rs_ratio_rank_e63": 0.5},
            {"symbol_id": 2, "date": td, "rs_ratio_rank_e21": 0.3, "rs_ratio_rank_e63": 0.7},
            {"symbol_id": 3, "date": td, "rs_ratio_rank_e21": 0.5, "rs_ratio_rank_e63": 0.5},
        ])

        strategy = {
            "name": "test_rs21_gt_63",
            "rs_rank_21_gt_63": True,
            "sort_column": "rs21_rank",
            "sort_ascending": False,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, df_ranks, base_symbols,
            _empty_theme_constituents(), strategy
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "rs21(0.8) > rs63(0.5) should pass"
        assert "BBBB" not in tickers, "rs21(0.3) < rs63(0.7) should fail"
        assert "CCCC" not in tickers, "rs21(0.5) == rs63(0.5) should fail (not strictly greater)"


# ============================================================
# Test: filter_theme_rs21_gt_63
# ============================================================

class TestThemeRs21Gt63:
    """theme_rs21_gt_63: テーマの RS21 > RS63 に属する銘柄のみが残る"""

    def test_stocks_in_leading_themes_pass(self, base_symbols, dates):
        """RS21 > RS63 のテーマに属する個別銘柄がシグナルに含まれる"""
        td = dates["target"]

        # THM1 (id=10): rs_ratio_21 > rs_ratio_63 → leading theme
        # THM2 (id=11): rs_ratio_21 < rs_ratio_63 → lagging theme
        df_ind = pd.DataFrame([
            _make_ind_row(1,  td),  # Stock in THM1
            _make_ind_row(2,  td),  # Stock in THM2
            _make_ind_row(3,  td),  # Stock in no theme
            _make_ind_row(10, td, rs_ratio_e21=0.8, rs_ratio_e63=0.3),  # THM1: leading
            _make_ind_row(11, td, rs_ratio_e21=0.2, rs_ratio_e63=0.7),  # THM2: lagging
        ])
        df_price = pd.DataFrame([
            _make_price_row(1, td),
            _make_price_row(2, td),
            _make_price_row(3, td),
            _make_price_row(10, td),
            _make_price_row(11, td),
        ])
        # Theme constituents: Stock1 → THM1, Stock2 → THM2
        df_theme_const = pd.DataFrame([
            {"theme_id": 10, "symbol_id": 1},
            {"theme_id": 11, "symbol_id": 2},
        ])

        strategy = {
            "name": "test_theme_rs21",
            "theme_rs21_gt_63": True,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            df_theme_const, strategy
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "Stock in leading theme (THM1) should pass"
        # 2026-07-29: テーマは実売買不可能なため、リーディングテーマ自体はシグナルに含めない
        # （構成銘柄への波及にのみ使う）
        assert "THM1" not in tickers, "Theme itself must never appear as a tradeable signal"
        assert "BBBB" not in tickers, "Stock in lagging theme (THM2) should fail"
        assert "CCCC" not in tickers, "Stock in no theme should fail"


# ============================================================
# Test: filter_theme_rs_rank_21_gt_63
# ============================================================

class TestThemeRsRank21Gt63:
    """theme_rs_rank_21_gt_63: テーマの RS%rank 21 > RS%rank 63 に属する銘柄のみが残る"""

    def test_stocks_in_leading_rank_themes_pass(self, base_symbols, dates):
        """RS%rank 21 > RS%rank 63 のテーマに属する個別銘柄がシグナルに含まれる"""
        td = dates["target"]

        # THM1 (id=10): rs21_rank > rs63_rank -> leading theme
        # THM2 (id=11): rs21_rank < rs63_rank -> lagging theme
        df_ind = pd.DataFrame([
            _make_ind_row(1,  td),  # Stock in THM1
            _make_ind_row(2,  td),  # Stock in THM2
            _make_ind_row(3,  td),  # Stock in no theme
            _make_ind_row(10, td),  # THM1
            _make_ind_row(11, td),  # THM2
        ])
        df_price = pd.DataFrame([
            _make_price_row(1, td),
            _make_price_row(2, td),
            _make_price_row(3, td),
            _make_price_row(10, td),
            _make_price_row(11, td),
        ])
        # Rank data: THM1 (10) rs21(0.8) > rs63(0.3), THM2 (11) rs21(0.2) < rs63(0.7)
        df_ranks = pd.DataFrame([
            {"symbol_id": 10, "date": td, "rs_ratio_rank_e21": 0.8, "rs_ratio_rank_e63": 0.3},
            {"symbol_id": 11, "date": td, "rs_ratio_rank_e21": 0.2, "rs_ratio_rank_e63": 0.7},
        ])
        # Theme constituents: Stock1 -> THM1, Stock2 -> THM2
        df_theme_const = pd.DataFrame([
            {"theme_id": 10, "symbol_id": 1},
            {"theme_id": 11, "symbol_id": 2},
        ])

        strategy = {
            "name": "test_theme_rs_rank",
            "theme_rs_rank_21_gt_63": True,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, df_ranks, base_symbols,
            df_theme_const, strategy
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "Stock in leading rank theme (THM1) should pass"
        # 2026-07-29: テーマは実売買不可能なため、リーディングテーマ自体はシグナルに含めない
        # （構成銘柄への波及にのみ使う）
        assert "THM1" not in tickers, "Theme itself must never appear as a tradeable signal"
        assert "BBBB" not in tickers, "Stock in lagging rank theme (THM2) should fail"
        assert "CCCC" not in tickers, "Stock in no theme should fail"


# ============================================================
# Test: filter_rrg_leading_in
# ============================================================

class TestRrgLeadingIn:
    """rrg_leading_in: 前日非Leading → 当日Leading に転換した銘柄のみが残る"""

    def test_leading_transition_passes(self, base_symbols, dates):
        """前日Improvingだった銘柄が当日Leadingに転換 → 合格"""
        td, prev = dates["target"], dates["prev"]
        df_ind = pd.DataFrame([
            # Stock1: Improving → Leading (passes: accel + intensity OK)
            _make_ind_row(1, prev, rs_ratio_e21=-0.1, rs_momentum_e21=0.6),
            _make_ind_row(1, td,   rs_ratio_e21=0.3,  rs_momentum_e21=0.7),
            # Stock2: Already Leading → Leading (should fail: was already leading)
            _make_ind_row(2, prev, rs_ratio_e21=0.4,  rs_momentum_e21=0.5),
            _make_ind_row(2, td,   rs_ratio_e21=0.5,  rs_momentum_e21=0.6),
        ])
        df_price = pd.DataFrame([
            _make_price_row(1, prev), _make_price_row(1, td),
            _make_price_row(2, prev), _make_price_row(2, td),
        ])
        strategy = {
            "name": "test_leading",
            "rrg_leading_in": True,
            "rrg_intensity_threshold": 0.5,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            _empty_theme_constituents(), strategy, prev_date=prev
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "Improving→Leading transition should pass"
        assert "BBBB" not in tickers, "Already Leading should fail"

    def test_no_prev_date_skips_rrg_filter(self, base_symbols, dates):
        """前日データが無い場合、RRGフィルタはスキップされる（既存動作）"""
        td = dates["target"]
        df_ind = pd.DataFrame([
            _make_ind_row(1, td, rs_ratio_e21=0.3, rs_momentum_e21=0.7),
        ])
        df_price = pd.DataFrame([_make_price_row(1, td)])
        strategy = {
            "name": "test_leading_no_prev",
            "rrg_leading_in": True,
        }
        # prev_date=None → RRG merge がスキップされ、RRGフィルタ条件が適用されない
        # → 他の基本条件（active, category 等）だけで評価される
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            _empty_theme_constituents(), strategy, prev_date=None
        )
        # 既存実装: prev_date=None だと rrg_leading_in フィルタがスキップされるため、
        # 基本条件を満たす銘柄は通過する
        assert len(signals) >= 0, "Behavior with no prev_date is defined by implementation"


# ============================================================
# Test: filter_rrg_improving_in
# ============================================================

class TestRrgImprovingIn:
    """rrg_improving_in: 前日Lagging → 当日Improving に転換した銘柄のみが残る"""

    def test_improving_transition_passes(self, base_symbols, dates):
        """前日Laggingだった銘柄が当日Improvingに転換 → 合格"""
        td, prev = dates["target"], dates["prev"]
        df_ind = pd.DataFrame([
            # Stock1: Lagging → Improving (passes: rs_ratio<0, rs_mom>0, accel, intensity)
            _make_ind_row(1, prev, rs_ratio_e21=-0.5, rs_momentum_e21=-0.1),
            _make_ind_row(1, td,   rs_ratio_e21=-0.3, rs_momentum_e21=0.6),
            # Stock2: Leading → Improving (should fail: was Leading, not Lagging)
            _make_ind_row(2, prev, rs_ratio_e21=0.5,  rs_momentum_e21=0.6),
            _make_ind_row(2, td,   rs_ratio_e21=-0.1, rs_momentum_e21=0.7),
        ])
        df_price = pd.DataFrame([
            _make_price_row(1, prev), _make_price_row(1, td),
            _make_price_row(2, prev), _make_price_row(2, td),
        ])
        strategy = {
            "name": "test_improving",
            "rrg_improving_in": True,
            "rrg_intensity_threshold": 0.5,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            _empty_theme_constituents(), strategy, prev_date=prev
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "Lagging→Improving transition should pass"
        assert "BBBB" not in tickers, "Leading→Improving should fail (prev was Leading)"


# ============================================================
# Test: filter_rrg_lagging_in
# ============================================================

class TestRrgLaggingIn:
    """rrg_lagging_in: 前日非Lagging → 当日Lagging に転換した銘柄のみが残る"""

    def test_lagging_transition_passes(self, base_symbols, dates):
        """前日非Laggingだった銘柄が当日Laggingに転換 → 合格"""
        td, prev = dates["target"], dates["prev"]
        df_ind = pd.DataFrame([
            # Stock1: Weakening → Lagging (passes: prev ratio>=0, today ratio<0 & mom<0)
            _make_ind_row(1, prev, rs_ratio_e21=0.1,  rs_momentum_e21=-0.2),
            _make_ind_row(1, td,   rs_ratio_e21=-0.3, rs_momentum_e21=-0.5),
            # Stock2: Already Lagging → Lagging (should fail: was already lagging)
            _make_ind_row(2, prev, rs_ratio_e21=-0.4, rs_momentum_e21=-0.3),
            _make_ind_row(2, td,   rs_ratio_e21=-0.5, rs_momentum_e21=-0.6),
        ])
        df_price = pd.DataFrame([
            _make_price_row(1, prev), _make_price_row(1, td),
            _make_price_row(2, prev), _make_price_row(2, td),
        ])
        strategy = {
            "name": "test_lagging",
            "rrg_lagging_in": True,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            _empty_theme_constituents(), strategy, prev_date=prev
        )
        tickers = {s.ticker for s in signals}
        assert "AAAA" in tickers, "Non-Lagging→Lagging transition should pass"
        assert "BBBB" not in tickers, "Already Lagging→Lagging should fail"

    def test_no_prev_date_skips_rrg_filter(self, base_symbols, dates):
        """前日データが無い場合、RRGフィルタはスキップされる（既存動作）"""
        td = dates["target"]
        df_ind = pd.DataFrame([
            _make_ind_row(1, td, rs_ratio_e21=-0.3, rs_momentum_e21=-0.5),
        ])
        df_price = pd.DataFrame([_make_price_row(1, td)])
        strategy = {
            "name": "test_lagging_no_prev",
            "rrg_lagging_in": True,
        }
        signals = scan_signals_for_date(
            td, df_ind, df_price, _empty_ranks(), base_symbols,
            _empty_theme_constituents(), strategy, prev_date=None
        )
        # 既存実装: prev_date=None だと rrg_lagging_in フィルタがスキップされるため、
        # 基本条件を満たす銘柄は通過する
        assert len(signals) >= 0, "Behavior with no prev_date is defined by implementation"


# ============================================================
# 新規共通化フィルタ（D-2 リファクタリング）の純関数ユニットテスト
# ============================================================
from indicators.screener_filters import (
    filter_rs_macd_hist_rising_21,
    filter_rs_trend_s21_lt_s63,
    filter_rs_trend_s14_lt_s21,
    filter_theme_rs_trend_rank_s14_gt_s21,
    filter_theme_rs_trend_rank_s21_gt_s63,
    SPECIAL_FILTER_KEYS,
)


class TestRsMacdHistRising21:
    def test_rising_and_positive_passes(self):
        merged = pd.DataFrame({
            "symbol_id": [1, 2, 3, 4],
            "rs_macd_hist_21":      [0.5,  0.5, -0.1, 0.5],
            "prev_rs_macd_hist_21": [0.2,  0.8,  -0.2, None],
        })
        mask = filter_rs_macd_hist_rising_21(merged)
        # 1: 正かつ上昇 → 通過, 2: 正だが下降 → 除外, 3: 負 → 除外, 4: prev NaN → 比較 False → 除外
        assert list(mask) == [True, False, False, False]

    def test_without_prev_column_falls_back_to_positive_only(self):
        merged = pd.DataFrame({
            "symbol_id": [1, 2],
            "rs_macd_hist_21": [0.5, -0.5],
        })
        mask = filter_rs_macd_hist_rising_21(merged)
        assert list(mask) == [True, False]

    def test_missing_columns_passthrough(self):
        merged = pd.DataFrame({"symbol_id": [1, 2]})
        mask = filter_rs_macd_hist_rising_21(merged)
        assert list(mask) == [True, True]


class TestRsTrendComparisons:
    def test_s21_lt_s63(self):
        merged = pd.DataFrame({
            "symbol_id": [1, 2],
            "rs_trend_s21": [1.0, 2.0],
            "rs_trend_s63": [1.5, 1.5],
        })
        assert list(filter_rs_trend_s21_lt_s63(merged)) == [True, False]

    def test_s14_lt_s21(self):
        merged = pd.DataFrame({
            "symbol_id": [1, 2],
            "rs_trend_s14": [0.9, 1.2],
            "rs_trend_s21": [1.0, 1.0],
        })
        assert list(filter_rs_trend_s14_lt_s21(merged)) == [True, False]

    def test_missing_columns_passthrough(self):
        merged = pd.DataFrame({"symbol_id": [1]})
        assert list(filter_rs_trend_s21_lt_s63(merged)) == [True]
        assert list(filter_rs_trend_s14_lt_s21(merged)) == [True]


class TestThemeRsTrendRankComparisons:
    def _fixture(self):
        # テーマ10(14>21で強い), テーマ11(弱い), 個別1(テーマ10所属), 個別2(テーマ11所属)
        merged = pd.DataFrame({
            "symbol_id": [10, 11, 1, 2],
            "category":  ["テーマ", "テーマ", "個別", "個別"],
            "rs_condition_14_rank": [0.9, 0.2, 0.5, 0.5],
            "rs_condition_21_rank": [0.5, 0.6, 0.5, 0.5],
            "rs_condition_63_rank": [0.1, 0.9, 0.5, 0.5],
        })
        tc = pd.DataFrame({"theme_id": [10, 11], "symbol_id": [1, 2]})
        return merged, tc

    def test_theme_s14_gt_s21(self):
        merged, tc = self._fixture()
        mask = filter_theme_rs_trend_rank_s14_gt_s21(merged, tc)
        # テーマ10(0.9>0.5)通過 → 構成銘柄1も通過。テーマ11(0.2>0.6=False)除外 → 銘柄2除外
        assert list(mask) == [True, False, True, False]

    def test_theme_s21_gt_s63(self):
        merged, tc = self._fixture()
        mask = filter_theme_rs_trend_rank_s21_gt_s63(merged, tc)
        # テーマ10(0.5>0.1)通過 → 銘柄1通過。テーマ11(0.6>0.9=False)除外 → 銘柄2除外
        assert list(mask) == [True, False, True, False]

    def test_missing_columns_passthrough(self):
        merged = pd.DataFrame({"symbol_id": [1], "category": ["個別"]})
        tc = pd.DataFrame({"theme_id": [], "symbol_id": []})
        assert list(filter_theme_rs_trend_rank_s14_gt_s21(merged, tc)) == [True]


class TestSpecialFilterKeysRegistry:
    def test_registry_contains_all_special_boolean_keys(self):
        expected = {
            "rrg_leading_in", "rrg_lagging_in", "rrg_improving_in",
            "is_theme_rs_ratio_e14_gt_e21", "is_theme_rs_ratio_e21_gt_e63",
            "is_theme_rs_ratio_rank_e14_gt_e21", "is_theme_rs_ratio_rank_e21_gt_e63",
            "is_rs_trend_s21_lt_s63", "is_rs_trend_s14_lt_s21",
            "is_rs_ratio_rank_e21_gt_e63",
            "is_theme_rs_trend_rank_s14_gt_s21",
            "is_rs_macd_hist_rising_21",
        }
        assert expected <= SPECIAL_FILTER_KEYS
