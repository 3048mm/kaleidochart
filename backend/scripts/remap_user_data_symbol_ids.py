"""`user_data.db` の `symbol_id` を現在の `stocktool.db` に合わせて振り直す。

## いつ必要か

**全期間再構築（`refresh_All.bat`）は `symbols.id` を再採番する。**
再構築はサンドボックスの空 DB から始まるため、T1 が「既存 id を温存する upsert」を
働かせる相手が居ない。退役済み銘柄は新 DB に作られないので、その分だけ後続の id が
前へ詰まる。

    2026-08-06 の再構築: 共通3,220ティッカーのうち **2,378件で id が変化**
      CAT 845→844 / CATY 846→845 / CAVA 847→846 ...

`user_data.db`（ウォッチリスト・ポートフォリオ）は `symbol_id` で `stocktool.db` を
参照するため、放置すると**別の銘柄を指したまま**になる。

## 直せる理由

`symbol_id` を持つテーブルは **`ticker` と `exchange` も併せて保持している**。
これが正であり、`symbol_id` は導出値として振り直せる。

## Usage

    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\remap_user_data_symbol_ids.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\remap_user_data_symbol_ids.py --apply
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402

# `symbol_id` と `ticker` を併せ持つテーブル
TARGET_TABLES = ("watchlist", "portfolio_positions", "position_history")


def load_symbol_map(db_path: str) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {t: i for i, t in con.execute("SELECT id, ticker FROM symbols")}
    finally:
        con.close()


def plan_remap(con: sqlite3.Connection, table: str,
               symbol_map: dict[str, int]) -> tuple[list, list]:
    """(振り直す行, ティッカーが解決できない行) を返す。

    解決できない行は**触らない**。退役・改称でマスタから消えた銘柄を
    黙って別 id に付け替えるより、そのまま残して人が判断する方が安全。
    """
    try:
        rows = list(con.execute(f"SELECT id, symbol_id, ticker FROM {table}"))
    except sqlite3.OperationalError:
        return [], []
    fix, unresolved = [], []
    for rid, sid, ticker in rows:
        new = symbol_map.get(ticker)
        if new is None:
            unresolved.append((rid, sid, ticker))
        elif new != sid:
            fix.append((rid, sid, new, ticker))
    return fix, unresolved


def run(dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    sys_db = config["system"]["db_path"]
    user_db = config["system"].get(
        "user_db_path", os.path.join(os.path.dirname(sys_db), "user_data.db"))

    print("=" * 74)
    print(f"user_data.db の symbol_id 振り直し  (dry-run={dry_run})")
    print("=" * 74)
    print(f"  参照元: {sys_db}")
    print(f"  対象  : {user_db}")

    symbol_map = load_symbol_map(sys_db)
    print(f"  symbols: {len(symbol_map):,} 件")

    if not dry_run:
        bk = f"{user_db}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(user_db, bk)
        print(f"  バックアップ: {os.path.basename(bk)}")

    con = sqlite3.connect(user_db, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
        total = 0
        con.execute("BEGIN IMMEDIATE")
        for table in TARGET_TABLES:
            fix, unresolved = plan_remap(con, table, symbol_map)
            print(f"\n  {table}: 振り直し {len(fix)} 件 / 解決不能 {len(unresolved)} 件")
            for rid, old, new, ticker in fix[:10]:
                print(f"      {ticker:<8} {old} → {new}")
            if len(fix) > 10:
                print(f"      ... 他 {len(fix)-10} 件")
            for rid, sid, ticker in unresolved[:10]:
                print(f"      [未解決] {ticker:<8} symbol_id={sid} は symbols に存在しない"
                      f" — 退役・改称の可能性。手動で確認してください")
            if not dry_run:
                con.executemany(f"UPDATE {table} SET symbol_id = ? WHERE id = ?",
                                [(new, rid) for rid, _old, new, _t in fix])
            total += len(fix)
        con.commit() if not dry_run else con.rollback()
    finally:
        con.close()

    print(f"\n合計 {total} 件" + ("（dry-run のため未適用）" if dry_run else " を振り直しました"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="user_data.db の symbol_id を振り直す")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    run(dry_run=not a.apply)
