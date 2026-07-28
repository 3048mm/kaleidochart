"""universe.db 移行 Phase 1: 整合性確立（W1 / W2b / W3 / W4b-C / W4b-G）

実施内容:
  W1     GBTC を 指標(LeadingList) へ昇格し sector_etf='Crypto'
  W2b    CPER を 指標 へ移し、テーマ構成(FCX/IE/SCCO)を解消（COPX がスーパーセット）
  W2b'   IBIT はテーマ専任のまま sector_etf を親セクタETF 'BLOK' に戻す
  W3     DX-Y.NYB を universe.db から削除、stocktool.symbols は active=0
  W4b-G  親が symbols_master に存在しない孤児 theme_members を削除
  W4b-C  全行の theme_type を symbol_classify.derive_theme_type で再導出

冪等（再実行可能）。既に目的の状態なら何もしない。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_phase1.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_phase1.py
"""

import argparse
import os
import sys

# --- path setup -----------------------------------------------------------
_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from data_collection.symbol_classify import derive_theme_type
from db.database import init_db, get_write_db
from db.database_universe import init_universe_db, get_universe_write_db
from db.models import Symbol
from db.models_universe import SymbolMaster, ThemeMember

# --- 期待する最終状態（冪等判定にも使う） ---------------------------------
# exchange は (ticker, exchange) が symbols の自然キーであるため、
# stocktool 側の実際の値と一致させること。ズレると同期時に exchange の
# 書き換えが走る（W5 の救済で id は温存されるが、無用な差分になる）。
SYMBOL_UPDATES = [
    # ticker,   category, sector_etf,   name,                  industry, exchange
    ("GBTC",    "指標",    "Crypto",     "ビットコイン (GBTC)",   "",       "NASDAQ"),
    ("CPER",    "指標",    "Commodity",  "銅 (Copper)",          "",       None),
    # IBIT はテーマ専任のまま。sector_etf は親セクタETF（分類ラベルではない）
    ("IBIT",    "テーマ",   "BLOK",       None,                   None,     None),
]

# テーマとしての CPER は COPX の真部分集合（CPER固有メンバー0件）のため解消する
THEME_LINKS_TO_REMOVE = ["CPER"]

RETIRE_FROM_UNIVERSE = ["DX-Y.NYB"]
RETIRE_IN_STOCKTOOL = ["DX-Y.NYB"]


def run(dry_run: bool = False):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)

    init_db(config["system"]["db_path"])
    universe_db_path = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"),
    )
    init_universe_db(universe_db_path)

    print("=" * 70)
    print(f"universe.db Phase 1 移行  (dry-run={dry_run})")
    print("=" * 70)

    with get_universe_write_db() as udb:
        # ---------------------------------------------------------------
        # W1 / W2b / W2b' : 銘柄属性の更新
        # ---------------------------------------------------------------
        print("\n[W1/W2b] 銘柄属性の更新")
        for ticker, category, sector_etf, name, industry, exchange in SYMBOL_UPDATES:
            sym = udb.query(SymbolMaster).filter(SymbolMaster.ticker == ticker).first()
            if sym is None:
                print(f"  {ticker:<10} 見つかりません（スキップ）")
                continue

            changes = []
            if sym.category != category:
                changes.append(f"category {sym.category}→{category}")
            if (sym.sector_etf or "") != sector_etf:
                changes.append(f"sector_etf {sym.sector_etf!r}→{sector_etf!r}")
            if name is not None and (sym.name or "") != name:
                changes.append(f"name {sym.name!r}→{name!r}")
            if industry is not None and (sym.industry or "") != industry:
                changes.append(f"industry {sym.industry!r}→{industry!r}")
            if exchange is not None and (sym.exchange or "") != exchange:
                changes.append(f"exchange {sym.exchange!r}→{exchange!r}")

            if not changes:
                print(f"  {ticker:<10} 変更なし（冪等スキップ）")
                continue

            print(f"  {ticker:<10} {' / '.join(changes)}")
            if not dry_run:
                sym.category = category
                sym.sector_etf = sector_etf
                if name is not None:
                    sym.name = name
                if industry is not None:
                    sym.industry = industry
                if exchange is not None:
                    sym.exchange = exchange

        # ---------------------------------------------------------------
        # W2b : テーマ構成の解消（CPER）
        # ---------------------------------------------------------------
        print("\n[W2b] テーマ構成の解消")
        for theme in THEME_LINKS_TO_REMOVE:
            links = udb.query(ThemeMember).filter(ThemeMember.theme_ticker == theme).all()
            if not links:
                print(f"  {theme:<10} 構成銘柄なし（冪等スキップ）")
                continue
            members = sorted(m.member_ticker for m in links)
            # 安全確認: COPX がスーパーセットであることを検証してから消す
            copx = {
                m.member_ticker
                for m in udb.query(ThemeMember).filter(ThemeMember.theme_ticker == "COPX").all()
            }
            missing = [m for m in members if m not in copx]
            print(f"  {theme:<10} {len(links)} ペア削除: {members}")
            print(f"  {'':<10} COPX に含まれない銘柄: {missing if missing else 'なし（情報損失なし）'}")
            if missing:
                print(f"  [ERROR] {theme} 固有のメンバーが存在します。中断します。")
                sys.exit(1)
            if not dry_run:
                for m in links:
                    udb.delete(m)

        # ---------------------------------------------------------------
        # W4b-G : 孤児 theme_members の削除
        # ---------------------------------------------------------------
        print("\n[W4b-G] 孤児テーマ親の解消")
        known = {t[0] for t in udb.query(SymbolMaster.ticker).all()}
        orphan_links = [
            m for m in udb.query(ThemeMember).all() if m.theme_ticker not in known
        ]
        if not orphan_links:
            print("  孤児なし（冪等スキップ）")
        else:
            by_parent = {}
            for m in orphan_links:
                by_parent.setdefault(m.theme_ticker, []).append(m.member_ticker)
            for parent, kids in sorted(by_parent.items()):
                print(f"  {parent:<12} 削除 {len(kids)} ペア: {sorted(kids)}")
            if not dry_run:
                for m in orphan_links:
                    udb.delete(m)

        if not dry_run:
            udb.flush()

        # ---------------------------------------------------------------
        # W3 : DX-Y.NYB を universe.db から削除
        # ---------------------------------------------------------------
        print("\n[W3] universe.db からの退役")
        for ticker in RETIRE_FROM_UNIVERSE:
            rows = udb.query(SymbolMaster).filter(SymbolMaster.ticker == ticker).all()
            if not rows:
                print(f"  {ticker:<10} 既に不在（冪等スキップ）")
                continue
            print(f"  {ticker:<10} {len(rows)} 件を削除")
            if not dry_run:
                for r in rows:
                    udb.delete(r)
                udb.flush()

        # ---------------------------------------------------------------
        # W4b-C : theme_type の再導出（カテゴリ変更後に実行すること）
        # ---------------------------------------------------------------
        print("\n[W4b-C] theme_type の再導出")
        changed = []
        for sym in udb.query(SymbolMaster).all():
            expected = derive_theme_type(sym.exchange, sym.category, sym.ticker)
            if (sym.theme_type or None) != expected:
                changed.append((sym.ticker, sym.category, sym.theme_type, expected))
                if not dry_run:
                    sym.theme_type = expected
        if not changed:
            print("  差分なし（冪等スキップ）")
        else:
            print(f"  {len(changed)} 件を更新")
            for t, cat, old, new in changed:
                print(f"    {t:<12} category={cat:<8} {str(old):<8} → {str(new)}")

        if not dry_run:
            udb.flush()

    # -------------------------------------------------------------------
    # W3 : stocktool.symbols の active=0
    # -------------------------------------------------------------------
    print("\n[W3] stocktool.symbols の退役")
    with get_write_db() as db:
        for ticker in RETIRE_IN_STOCKTOOL:
            sym = db.query(Symbol).filter(Symbol.ticker == ticker).first()
            if sym is None:
                print(f"  {ticker:<10} 見つかりません（スキップ）")
            elif sym.active == 0:
                print(f"  {ticker:<10} 既に active=0（冪等スキップ）")
            else:
                print(f"  {ticker:<10} id={sym.id} active=1 → 0")
                if not dry_run:
                    sym.active = 0
                    db.flush()

    # -------------------------------------------------------------------
    # 結果サマリ
    # -------------------------------------------------------------------
    if not dry_run:
        from sqlalchemy import func

        print("\n--- 結果 ---")
        with get_universe_write_db() as udb:
            print("  universe.symbols_master:", udb.query(func.count(SymbolMaster.id)).scalar())
            print("  universe.theme_members :", udb.query(func.count(ThemeMember.id)).scalar())
            print("  category 別:")
            for cat, cnt in sorted(
                udb.query(SymbolMaster.category, func.count(SymbolMaster.id))
                .filter(SymbolMaster.active == 1)
                .group_by(SymbolMaster.category)
                .all(),
                key=lambda x: -x[1],
            ):
                print(f"    {cat:<8} {cnt}")
            print("  theme_type 別:")
            for tt, cnt in sorted(
                udb.query(SymbolMaster.theme_type, func.count(SymbolMaster.id))
                .filter(SymbolMaster.active == 1)
                .group_by(SymbolMaster.theme_type)
                .all(),
                key=lambda x: str(x[0]),
            ):
                print(f"    {str(tt):<8} {cnt}")

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="universe.db Phase 1 整合性確立")
    parser.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
