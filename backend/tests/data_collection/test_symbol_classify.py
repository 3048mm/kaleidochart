"""symbol_classify.derive_theme_type の単体テスト。

判定ロジックが3箇所に分散して IBIT / CPER が 'theme' ではなく 'etf' になる不整合が
発生したため（universe.db 移行計画 W4b-C）、一元化した関数の挙動を固定する。
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_collection.symbol_classify import derive_theme_type, looks_like_virtual_ticker


class TestDeriveThemeType:
    @pytest.mark.parametrize(
        "exchange, category, ticker, expected",
        [
            # --- 仮想指数: exchange が最優先 ---
            ("VIRTUAL", "テーマ", "_PHNC_", "virtual"),
            ("VIRTUAL", "市場", "_X_", "virtual"),
            ("virtual", "テーマ", "_PHNC_", "virtual"),  # 小文字も許容
            (" VIRTUAL ", "テーマ", "_PHNC_", "virtual"),  # 前後空白も許容
            # --- カテゴリ別 ---
            ("NYSEARCA", "セクタ", "XLK", "sector"),
            ("NYSEARCA", "テーマ", "COPX", "theme"),
            ("NYSEARCA", "市場", "SPY", "etf"),
            ("NYSEARCA", "指標", "USO", "etf"),
            ("NASDAQ", "個別", "AAPL", None),
            ("NYSEARCA", "レバレッジ", "SOXL", None),
        ],
    )
    def test_derivation(self, exchange, category, ticker, expected):
        assert derive_theme_type(exchange, category, ticker) == expected

    def test_ibit_and_cper_are_theme_not_etf(self):
        """回帰: 旧 import_universe._detect_theme_type は既定値 'etf' を返していた。

        IBIT / CPER は category='テーマ' なので 'theme' でなければならない。
        （移行後 CPER は category='指標' になり 'etf' が正となる — W2b)
        """
        assert derive_theme_type("NASDAQ", "テーマ", "IBIT") == "theme"
        assert derive_theme_type("NYSEARCA", "テーマ", "CPER") == "theme"
        # W2b 後の姿
        assert derive_theme_type("NYSEARCA", "指標", "CPER") == "etf"

    def test_underscore_ticker_is_virtual_even_without_virtual_exchange(self):
        """回帰: exchange 設定漏れの仮想テーマを救済する。

        `_MRAD_` は exchange='NYSE' で登録されていたため theme_type='theme' となり、
        T2 が yfinance から取得を試みて失敗し、仮想指数合成の対象にもならず、
        12銘柄の構成銘柄を持ちながら価格系列が空のまま放置されていた。
        """
        assert derive_theme_type("NYSE", "テーマ", "_MRAD_") == "virtual"

    def test_no_ticker_falls_back_to_exchange_and_category(self):
        assert derive_theme_type("NYSEARCA", "テーマ") == "theme"
        assert derive_theme_type("VIRTUAL", "テーマ") == "virtual"

    @pytest.mark.parametrize("exchange", [None, ""])
    def test_missing_exchange_is_tolerated(self, exchange):
        assert derive_theme_type(exchange, "個別", "AAPL") is None
        assert derive_theme_type(exchange, "セクタ", "XLK") == "sector"

    def test_unknown_category_returns_none(self):
        assert derive_theme_type("NYSE", "未知カテゴリ", "FOO") is None
        assert derive_theme_type("NYSE", None, "FOO") is None


class TestLooksLikeVirtualTicker:
    @pytest.mark.parametrize("ticker", ["_PHNC_", "_MRAD_", "_CMMM0F_", "_A_"])
    def test_virtual_patterns(self, ticker):
        assert looks_like_virtual_ticker(ticker) is True

    @pytest.mark.parametrize("ticker", ["AAPL", "BRK-B", "_", "__", "", None, "_LEAD", "TRAIL_"])
    def test_non_virtual_patterns(self, ticker):
        assert looks_like_virtual_ticker(ticker) is False
