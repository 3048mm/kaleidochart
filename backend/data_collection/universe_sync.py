"""universe.db を銘柄マスタのソースとして T1（`symbols` / `theme_constituents`）を同期する。

## 設計上の最重要制約: `symbols.id` の温存

`daily_prices` / `indicators` / `relative_ranks`（各約157万行）と Parquet マスター全期間が
`symbols.id` の整数FKで紐付いている。universe.db は独自の id 空間を持つが、**その id は
一切持ち込まない**。`(ticker, exchange)` を自然キーとして upsert し、既存 id を温存する。

`exchange` がズレると自然キーが外れて新しい id が採番され、価格履歴が孤児化する。

## `tags` の逆生成

`tags` は `category` によって意味が変わる多義カラム（仕様書 §3.1）。

    category == '個別'  → theme_members から所属テーマを昇順ソートで CSV 生成
    それ以外            → universe.sector_etf をそのまま（親セクタETF / 分類ラベル / 原資産ETF）

個別の並びを昇順で固定するのは、順序が不安定だと Parquet の symbols に差分が
毎日発生してしまうため。
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from db.models import Symbol, ThemeConstituent
from db.models_universe import SymbolMaster, ThemeMember

logger = logging.getLogger(__name__)

INDIVIDUAL_CATEGORY = "個別"
TAG_SEPARATOR = ", "


class UniverseIntegrityError(Exception):
    """universe.db の参照整合性が壊れており同期を続行できない場合に送出する。"""


def _check_integrity(universe_db) -> None:
    """`theme_members` の親・子が `symbols_master` に実在することを検証する。

    `theme_members` は文字列参照で DB 制約が効かないため、孤児が静かに混入しうる
    （実際に `_SMRTEE_` / `_TSCR10_` / `_WRBL67_` の3件が発生していた）。
    孤児を抱えたまま同期するとテーマ構成が壊れるので、書き込み前に中断する。
    """
    known = {t[0] for t in universe_db.query(SymbolMaster.ticker).all()}

    orphan_parents = sorted(
        {m.theme_ticker for m in universe_db.query(ThemeMember).all()} - known
    )
    orphan_members = sorted(
        {m.member_ticker for m in universe_db.query(ThemeMember).all()} - known
    )

    problems = []
    if orphan_parents:
        problems.append(f"symbols_master に存在しないテーマ親: {orphan_parents}")
    if orphan_members:
        problems.append(f"symbols_master に存在しない構成銘柄: {orphan_members}")

    if problems:
        raise UniverseIntegrityError(
            "universe.db の整合性チェックに失敗したため T1 同期を中断しました。 "
            + " / ".join(problems)
        )


def _build_tag_map(universe_db, active_tickers: set) -> Dict[str, str]:
    """個別銘柄 → 所属テーマの CSV（昇順・重複排除）を構築する。"""
    membership: Dict[str, set] = {}
    for m in universe_db.query(ThemeMember).all():
        # 無効化された銘柄・テーマは構成から外す
        if m.theme_ticker not in active_tickers or m.member_ticker not in active_tickers:
            continue
        membership.setdefault(m.member_ticker, set()).add(m.theme_ticker)
    return {
        ticker: TAG_SEPARATOR.join(sorted(themes))
        for ticker, themes in membership.items()
    }


def sync_symbols_from_universe(
    db,
    universe_db,
    logger_: Optional[logging.Logger] = None,
) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], int]]:
    """universe.db から `symbols` と `theme_constituents` を同期する。

    Args:
        db: `stocktool.db` の書き込みセッション。
        universe_db: `universe.db` の読み取りセッション。

    Returns:
        `(sheet_data, symbol_ids)` — 既存の txt_sync / spreadsheet 経路と同じ形。
        `sheet_data` は active 銘柄の dict リスト、`symbol_ids` は `(ticker, exchange) -> id`。

    Raises:
        UniverseIntegrityError: 孤児参照を検出した場合（このとき DB は変更されない）。
    """
    log = logger_ or logger

    # --- 1) 書き込み前に整合性ゲートを通す --------------------------------
    _check_integrity(universe_db)

    masters = universe_db.query(SymbolMaster).all()
    active_masters = [m for m in masters if m.active == 1]
    active_tickers = {m.ticker for m in active_masters}
    log.info(
        "T1: universe.db から %d 件（うち active %d 件）を読み込みました",
        len(masters), len(active_masters),
    )

    tag_map = _build_tag_map(universe_db, active_tickers)

    # --- 2) universe に無い銘柄を退役（物理削除はしない） ------------------
    universe_keys = {(m.ticker, m.exchange or "") for m in active_masters}
    deactivated = 0
    for sym in db.query(Symbol).all():
        if (sym.ticker, sym.exchange or "") not in universe_keys and sym.active != 0:
            # id は温存する。物理削除すると daily_prices 等が孤児化するため
            sym.active = 0
            deactivated += 1
    if deactivated:
        log.info("T1: universe.db に存在しない %d 件を active=0 にしました", deactivated)

    # --- 3) (ticker, exchange) 自然キーで upsert（id は温存） --------------
    all_symbols = db.query(Symbol).all()
    existing = {(s.ticker, s.exchange or ""): s for s in all_symbols}

    # ticker 単位の索引。exchange が変わったときに既存行を引き当てるために使う
    by_ticker: Dict[str, List[Symbol]] = {}
    for s in all_symbols:
        by_ticker.setdefault(s.ticker, []).append(s)

    # universe 側が同一 ticker を複数 exchange で持つ場合、下記の救済は
    # どの行に寄せるべきか決められないので無効化する
    ambiguous_tickers = {
        t for t in
        [m.ticker for m in active_masters]
        if [m.ticker for m in active_masters].count(t) > 1
    }

    sheet_data: List[Dict[str, Any]] = []
    symbol_ids: Dict[Tuple[str, str], int] = {}

    for m in active_masters:
        exchange = m.exchange or ""
        tags = (
            tag_map.get(m.ticker, "")
            if m.category == INDIVIDUAL_CATEGORY
            else (m.sector_etf or "")
        )

        sym = existing.get((m.ticker, exchange))

        if sym is None and m.ticker not in ambiguous_tickers:
            # exchange の修正（例: 'US' → 'NASDAQ'）を新規採番ではなく
            # 既存行の更新として扱う。新 id を採番すると価格履歴が孤児化するため。
            # 本番で GBTC が (GBTC,'US') → (GBTC,'NASDAQ') に変わった際、
            # 実際に id 3256 と 3258 の重複行が生まれた（2026-07-28）。
            candidates = by_ticker.get(m.ticker, [])
            if len(candidates) == 1:
                sym = candidates[0]
                log.warning(
                    "T1: %s の exchange が %r → %r に変更されました。"
                    "新規採番せず既存 id=%d を維持します",
                    m.ticker, sym.exchange, exchange, sym.id,
                )
                sym.exchange = exchange
                existing.pop((sym.ticker, sym.exchange or ""), None)
                existing[(m.ticker, exchange)] = sym

        if sym is None:
            sym = Symbol(ticker=m.ticker, exchange=exchange)
            db.add(sym)

        sym.name = m.name
        sym.category = m.category
        sym.asset_class = m.industry          # universe.industry → stocktool.asset_class
        sym.theme_type = m.theme_type
        sym.tags = tags
        sym.active = 1
        # next_earnings_date は T2/T3 が書く列。universe は保持しないので触らない

        db.flush()
        symbol_ids[(m.ticker, exchange)] = sym.id
        sheet_data.append({
            "ticker": m.ticker,
            "exchange": exchange,
            "name": m.name,
            "category": m.category,
            "asset_class": m.industry,
            "theme_type": m.theme_type,
            "tags": tags,
        })

    db.flush()

    # --- 4) theme_constituents を theme_members から直接再構築 -------------
    #     旧実装は `symbols.tags LIKE '%TAG%'` で解決していたが、
    #     `_CMMM0F_` と `_CMMM30_` のような部分一致事故のリスクがあった
    db.query(ThemeConstituent).delete(synchronize_session=False)
    db.flush()

    by_theme: Dict[str, List[str]] = {}
    for tm in universe_db.query(ThemeMember).all():
        if tm.theme_ticker not in active_tickers or tm.member_ticker not in active_tickers:
            continue
        by_theme.setdefault(tm.theme_ticker, []).append(tm.member_ticker)

    ticker_to_id = {t: i for (t, _ex), i in symbol_ids.items()}
    constituents = []
    for theme_ticker, members in by_theme.items():
        theme_id = ticker_to_id.get(theme_ticker)
        if theme_id is None:
            continue
        weight = 1.0 / len(members)
        for member_ticker in sorted(members):
            member_id = ticker_to_id.get(member_ticker)
            if member_id is not None:
                constituents.append(
                    ThemeConstituent(theme_id=theme_id, symbol_id=member_id, weight=weight)
                )

    if constituents:
        db.bulk_save_objects(constituents)
    db.flush()

    log.info(
        "T1: symbols %d 件 / theme_constituents %d 件を同期しました",
        len(sheet_data), len(constituents),
    )
    return sheet_data, symbol_ids
