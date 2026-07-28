"""Universe Manager – TSV Import Script

スプレッドシートからエクスポートされた TSV ファイル (Themes.txt / Stocks.txt) を
universe.db へインポートする。

Usage:
    python backend/scripts/import_universe.py --from-tsv
    python backend/scripts/import_universe.py --from-tsv --include-finviz
    python backend/scripts/import_universe.py --dry-run
"""

import argparse
import csv
import os
import sys

# --- path setup -----------------------------------------------------------
_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from data_collection.symbol_classify import derive_theme_type
from db.database_universe import init_universe_db, get_universe_write_db
from db.models_universe import SymbolMaster, ThemeMember


# ---------------------------------------------------------------------------
# TSV parsing helpers
# ---------------------------------------------------------------------------

def _parse_tsv(filepath: str) -> list[dict]:
    """Read a TSV file and return rows as dicts (skip empty rows)."""
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            # Skip blank separator rows
            ticker = (row.get("Ticker") or "").strip()
            if not ticker:
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def _detect_theme_type(exchange: str, ticker: str, category: str | None = None) -> str | None:
    """theme_type の判定は data_collection.symbol_classify に一元化している。

    旧実装は ticker パターンと既知セクタETF集合で判定し、既定値が 'etf' だったため、
    本番 T1 経路 (spreadsheet_sync) と食い違って IBIT / CPER が 'theme' ではなく
    'etf' になる不整合を生んでいた。後方互換のため薄いラッパーとして残す。
    """
    return derive_theme_type(exchange, category, ticker)


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def import_themes(rows: list[dict], session, *, source: str = "spreadsheet") -> int:
    """Import Themes.txt rows into symbols_master."""
    count = 0
    for row in rows:
        ticker = row["Ticker"]
        exchange = row.get("Exchange", "")
        existing = (
            session.query(SymbolMaster)
            .filter(SymbolMaster.ticker == ticker, SymbolMaster.exchange == exchange)
            .first()
        )
        category = row.get("category", "") or (existing.category if existing else "") or "テーマ"
        theme_type = _detect_theme_type(exchange, ticker, category)
        if existing:
            existing.name = row.get("Name", existing.name)
            existing.category = category
            existing.industry = row.get("Industry", existing.industry)
            existing.theme_type = theme_type
            existing.sector_etf = row.get("Tags", existing.sector_etf)
            existing.source = source
        else:
            sym = SymbolMaster(
                ticker=ticker,
                exchange=exchange,
                name=row.get("Name", ""),
                category=category,
                industry=row.get("Industry", ""),
                theme_type=theme_type,
                sector_etf=row.get("Tags", ""),
                active=1,
                source=source,
            )
            session.add(sym)
        count += 1
    return count


def import_stocks(rows: list[dict], session, *, source: str = "spreadsheet") -> tuple[int, int]:
    """Import Stocks.txt rows into symbols_master + theme_members.

    Returns (symbols_count, theme_members_count).
    """
    sym_count = 0
    tm_count = 0

    for row in rows:
        ticker = row["Ticker"]
        exchange = row.get("Exchange", "")
        tags_raw = row.get("Tags", "")

        # --- symbols_master ---
        existing = (
            session.query(SymbolMaster)
            .filter(SymbolMaster.ticker == ticker, SymbolMaster.exchange == exchange)
            .first()
        )
        if existing:
            existing.name = row.get("Name", existing.name)
            existing.category = row.get("category", "") or "個別"
            existing.industry = row.get("Industry", existing.industry)
            existing.source = source
        else:
            sym = SymbolMaster(
                ticker=ticker,
                exchange=exchange,
                name=row.get("Name", ""),
                category=row.get("category", "") or "個別",
                industry=row.get("Industry", ""),
                theme_type=None,
                sector_etf=None,
                active=1,
                source=source,
            )
            session.add(sym)
        sym_count += 1

        # --- theme_members ---
        if tags_raw:
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            for theme_ticker in tags:
                existing_tm = (
                    session.query(ThemeMember)
                    .filter(
                        ThemeMember.theme_ticker == theme_ticker,
                        ThemeMember.member_ticker == ticker,
                    )
                    .first()
                )
                if not existing_tm:
                    tm = ThemeMember(
                        theme_ticker=theme_ticker,
                        member_ticker=ticker,
                        weight=1.0,
                        source=source,
                    )
                    session.add(tm)
                    tm_count += 1

    return sym_count, tm_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_tsv_import(
    tsv_dir: str | None = None,
    include_finviz: bool = False,
    dry_run: bool = False,
    db_path: str | None = None,
):
    """Execute TSV → universe.db import."""
    if tsv_dir is None:
        tsv_dir = os.path.join(_project_root, "data", "ThemeStockList", "spreadsheet")

    if db_path is None:
        db_path = os.path.join(_project_root, "data", "universe.db")

    print(f"=== Universe Import ===")
    print(f"  TSV dir : {tsv_dir}")
    print(f"  DB path : {db_path}")
    print(f"  finviz  : {'YES' if include_finviz else 'NO'}")
    print(f"  dry-run : {'YES' if dry_run else 'NO'}")
    print()

    init_universe_db(db_path)

    themes_file = os.path.join(tsv_dir, "Themes.txt")
    stocks_file = os.path.join(tsv_dir, "Stocks.txt")

    if not os.path.exists(themes_file):
        print(f"[ERROR] Themes.txt not found: {themes_file}")
        sys.exit(1)
    if not os.path.exists(stocks_file):
        print(f"[ERROR] Stocks.txt not found: {stocks_file}")
        sys.exit(1)

    theme_rows = _parse_tsv(themes_file)
    stock_rows = _parse_tsv(stocks_file)

    print(f"  Parsed Themes.txt : {len(theme_rows)} rows")
    print(f"  Parsed Stocks.txt : {len(stock_rows)} rows")

    # Optionally include finviz merged data
    finviz_sym_count = 0
    finviz_tm_count = 0
    finviz_theme_rows = []
    finviz_stock_rows = []
    if include_finviz:
        merged_dir = os.path.join(_project_root, "data", "ThemeStockList", "merged")
        merged_themes = os.path.join(merged_dir, "Themes.txt")
        merged_stocks = os.path.join(merged_dir, "Stocks.txt")
        if os.path.exists(merged_themes):
            finviz_theme_rows = _parse_tsv(merged_themes)
            print(f"  Parsed merged/Themes.txt : {len(finviz_theme_rows)} rows")
        if os.path.exists(merged_stocks):
            finviz_stock_rows = _parse_tsv(merged_stocks)
            print(f"  Parsed merged/Stocks.txt : {len(finviz_stock_rows)} rows")

    if dry_run:
        print("\n[DRY-RUN] No data written. Exiting.")
        return

    with get_universe_write_db() as session:
        # 1) Import themes
        t_count = import_themes(theme_rows, session, source="spreadsheet")
        print(f"\n  → symbols_master (themes)  : {t_count}")

        # 2) Import stocks + theme_members
        s_count, tm_count = import_stocks(stock_rows, session, source="spreadsheet")
        print(f"  → symbols_master (stocks)  : {s_count}")
        print(f"  → theme_members            : {tm_count}")

        # 3) Optional finviz
        if include_finviz and (finviz_theme_rows or finviz_stock_rows):
            ft_count = import_themes(finviz_theme_rows, session, source="finviz")
            fs_count, ftm_count = import_stocks(finviz_stock_rows, session, source="finviz")
            finviz_sym_count = ft_count + fs_count
            finviz_tm_count = ftm_count
            print(f"  → symbols_master (finviz)  : {finviz_sym_count}")
            print(f"  → theme_members (finviz)   : {finviz_tm_count}")

        session.flush()

        # Summary counts from DB
        total_symbols = session.query(SymbolMaster).count()
        total_themes = session.query(SymbolMaster).filter(SymbolMaster.category == "テーマ").count()
        total_members = session.query(ThemeMember).count()

        print(f"\n=== Summary ===")
        print(f"  symbols_master  : {total_symbols} 件")
        print(f"  テーマ          : {total_themes} 件")
        print(f"  theme_members   : {total_members} 件")
        print(f"=== Done ===")


def run_stocktool_backfill(
    stocktool_db_path: str | None = None,
    universe_db_path: str | None = None,
    categories: list[str] | None = None,
):
    """stocktool.db から不足カテゴリ（市場・指標・セクタ・レバレッジ）を補完インポート。

    TSV ファイルにはテーマと個別しかないため、stocktool.db の symbols テーブルから
    残りのカテゴリを引き抜いて universe.db に投入する。
    """
    if stocktool_db_path is None:
        stocktool_db_path = os.path.join(_project_root, "data", "stocktool.db")
    if universe_db_path is None:
        universe_db_path = os.path.join(_project_root, "data", "universe.db")
    if categories is None:
        categories = ["市場", "指標", "セクタ", "レバレッジ"]

    print(f"=== Backfill from stocktool.db ===")
    print(f"  stocktool.db : {stocktool_db_path}")
    print(f"  universe.db  : {universe_db_path}")
    print(f"  categories   : {', '.join(categories)}")
    print()

    # Import stocktool DB modules
    from db.database import init_db, get_db
    from db.models import Symbol
    from sqlalchemy import func

    init_db(stocktool_db_path)
    init_universe_db(universe_db_path)

    # Read missing symbols from stocktool.db
    with get_db() as sdb:
        source_symbols = (
            sdb.query(Symbol)
            .filter(Symbol.category.in_(categories), Symbol.active == 1)
            .all()
        )
        rows = []
        for s in source_symbols:
            rows.append({
                "ticker": s.ticker,
                "exchange": s.exchange or "",
                "name": s.name or "",
                "category": s.category,
                "industry": s.asset_class or "",
                "theme_type": s.theme_type,
                "sector_etf": s.tags or "",
            })

    print(f"  Found {len(rows)} symbols in stocktool.db")

    # Write to universe.db
    with get_universe_write_db() as udb:
        added = 0
        updated = 0
        for r in rows:
            existing = (
                udb.query(SymbolMaster)
                .filter(SymbolMaster.ticker == r["ticker"], SymbolMaster.exchange == r["exchange"])
                .first()
            )
            if existing:
                existing.name = r["name"]
                existing.category = r["category"]
                existing.industry = r["industry"]
                existing.theme_type = r["theme_type"]
                existing.sector_etf = r["sector_etf"]
                existing.source = "stocktool_db"
                updated += 1
            else:
                sym = SymbolMaster(
                    ticker=r["ticker"],
                    exchange=r["exchange"],
                    name=r["name"],
                    category=r["category"],
                    industry=r["industry"],
                    theme_type=r["theme_type"],
                    sector_etf=r["sector_etf"],
                    active=1,
                    source="stocktool_db",
                )
                udb.add(sym)
                added += 1

        udb.flush()

        # Summary
        total = udb.query(SymbolMaster).count()
        cat_rows = (
            udb.query(SymbolMaster.category, func.count(SymbolMaster.id))
            .group_by(SymbolMaster.category)
            .all()
        )

        print(f"\n  Added: {added}, Updated: {updated}")
        print(f"\n=== universe.db totals ===")
        for cat, cnt in sorted(cat_rows, key=lambda x: -x[1]):
            print(f"  {cat}: {cnt}")
        print(f"  Total: {total}")
        print(f"=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import data into universe.db")
    sub = parser.add_subparsers(dest="command")

    # Subcommand: tsv
    p_tsv = sub.add_parser("tsv", help="Import from local TSV files (Themes.txt / Stocks.txt)")
    p_tsv.add_argument("--tsv-dir", type=str, default=None, help="Path to TSV directory")
    p_tsv.add_argument("--include-finviz", action="store_true", help="Also import merged/finviz data")
    p_tsv.add_argument("--db-path", type=str, default=None, help="Path to universe.db")
    p_tsv.add_argument("--dry-run", action="store_true", help="Parse only, don't write")

    # Subcommand: backfill
    p_bf = sub.add_parser("backfill", help="Backfill 市場/指標/セクタ/レバレッジ from stocktool.db")
    p_bf.add_argument("--stocktool-db", type=str, default=None, help="Path to stocktool.db")
    p_bf.add_argument("--db-path", type=str, default=None, help="Path to universe.db")

    # Legacy: no subcommand = tsv (backward compat)
    parser.add_argument("--from-tsv", action="store_true", default=False, help="(Legacy) Import from TSV")
    parser.add_argument("--tsv-dir", type=str, default=None)
    parser.add_argument("--include-finviz", action="store_true")
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "backfill":
        run_stocktool_backfill(
            stocktool_db_path=args.stocktool_db,
            universe_db_path=args.db_path,
        )
    elif args.command == "tsv" or args.from_tsv or args.command is None:
        # Use subparser args if available, else top-level args
        tsv_dir = getattr(args, "tsv_dir", None)
        include_finviz = getattr(args, "include_finviz", False)
        dry_run = getattr(args, "dry_run", False)
        db_path = getattr(args, "db_path", None)
        run_tsv_import(
            tsv_dir=tsv_dir,
            include_finviz=include_finviz,
            dry_run=dry_run,
            db_path=db_path,
        )

