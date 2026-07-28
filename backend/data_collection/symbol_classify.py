"""銘柄メタデータの分類ロジック（`theme_type` の導出）を一元管理するモジュール。

## 背景

`theme_type` の判定はかつて3箇所に分散しており、実データに不整合を生んでいた。

| 実装 | category=テーマ | 既定値 | 備考 |
| :--- | :--- | :--- | :--- |
| `spreadsheet_sync.fetch_symbols_from_sheet()` | `theme` | `None` | 本番 T1 経路。**これを正とする** |
| `universe_router._derive_theme_type()` | `theme` | `None` | 上と同等 |
| `import_universe._detect_theme_type()` | `etf` | `etf` | ticker パターンと既知セクタETF集合で判定 |

3つ目が universe.db への TSV 取り込みを行ったため、`IBIT` / `CPER` の `theme_type` が
`theme` ではなく `etf` になっていた。本モジュールに一本化してこれを解消する。

## 判定規則（`spreadsheet_sync` の挙動が正）

    exchange == 'VIRTUAL' もしくは ticker が `_..._` 形式 → 'virtual'
    category == 'セクタ'                                  → 'sector'
    category == 'テーマ'                                  → 'theme'
    category in ('市場', '指標')                          → 'etf'
    それ以外（個別 / レバレッジ）                          → None

ticker パターン（`_..._`）を第2の判定材料に加えているのは、`exchange` の設定漏れで
仮想テーマが実在銘柄として扱われ、yfinance から取得できず価格系列が空のまま放置される
事故を防ぐため（`_MRAD_` で実際に発生した）。
"""

from typing import Optional

VIRTUAL_EXCHANGE = "VIRTUAL"


def looks_like_virtual_ticker(ticker: Optional[str]) -> bool:
    """`_PHNC_` のようなアンダースコア囲みの仮想テーマ命名規則かどうか。"""
    if not ticker:
        return False
    t = ticker.strip()
    return len(t) >= 3 and t.startswith("_") and t.endswith("_")


def derive_theme_type(
    exchange: Optional[str],
    category: Optional[str],
    ticker: Optional[str] = None,
) -> Optional[str]:
    """`exchange` / `category` / `ticker` から `theme_type` を導出する。

    Args:
        exchange: 取引所コード。`'VIRTUAL'` なら仮想指数。
        category: 銘柄分類（市場 / 指標 / セクタ / テーマ / 個別 / レバレッジ）。
        ticker: ティッカー。`exchange` の設定漏れを補うための第2判定材料（任意）。

    Returns:
        'virtual' | 'sector' | 'theme' | 'etf' | None
    """
    if (exchange or "").strip().upper() == VIRTUAL_EXCHANGE:
        return "virtual"
    if looks_like_virtual_ticker(ticker):
        return "virtual"

    if category == "セクタ":
        return "sector"
    if category == "テーマ":
        return "theme"
    if category in ("市場", "指標"):
        return "etf"
    return None
