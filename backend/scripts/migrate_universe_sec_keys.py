"""universe.db に SEC の安定キー（`cik` / `sec_class_id`）を追加し、初回解決する。

## なぜ必要か

ティッカーは変わる。2026年6〜7月だけで**15件**（改称6・上場廃止9）が発生し、
2ヶ月放置された挙句、原因の誤診に丸一日を費やした。

**CIK と classId は変わらない。** これを軸にすれば、週次でマスタを1回取得するだけで
全銘柄のコーポレートアクションを検知できる。

## 対象は universe.db（ユーザー資産）

`agent_execution_rules.md` §10.1 の通り、swap・再構築は禁止。
**バックアップ取得 → in-place マイグレーションのみ**。冪等（再実行可能）。

## キーの選び方

    個別銘柄        … cik（company_tickers.json）
    ETF・ファンド   … sec_class_id（company_tickers_mf.json）
                      CIK はトラスト単位で粗すぎる。RSHO の CIK 1944285 は
                      Tema ETF Trust の13ファンドを含むため、ファンド単位で追えない
    指数・仮想テーマ … 両方 NULL（SEC に実体が無い。追跡対象外）

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_sec_keys.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_sec_keys.py --apply
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from data_collection.sec_client import SecClient  # noqa: E402
from db.database_universe import get_universe_write_db, init_universe_db  # noqa: E402
from db.models_universe import SymbolMaster  # noqa: E402

NEW_COLUMNS = {
    "cik": "INTEGER",
    "sec_class_id": "VARCHAR",
    "sec_checked_at": "DATETIME",
}


def add_columns_if_missing(db) -> list[str]:
    """列を追加する（既にあれば何もしない＝冪等）。"""
    from sqlalchemy import text

    existing = {r[1] for r in db.execute(text("PRAGMA table_info(symbols_master)"))}
    added = []
    for col, typ in NEW_COLUMNS.items():
        if col not in existing:
            db.execute(text(f"ALTER TABLE symbols_master ADD COLUMN {col} {typ}"))
            added.append(col)
    for col in ("cik", "sec_class_id"):
        db.execute(text(
            f"CREATE INDEX IF NOT EXISTS ix_symbols_master_{col} ON symbols_master ({col})"))
    return added


def is_sec_trackable(ticker: str) -> bool:
    """SEC に実体があるか（仮想テーマ・指数は追跡対象外）。

    `_DRON_` は構成銘柄から合成した内部指数、`^VIX` は取引所の指数で、
    どちらも SEC への登録主体が存在しない。個別に問い合わせても無駄なので弾く。
    """
    t = (ticker or "").strip()
    if not t:
        return False
    return not (t.startswith("^") or (t.startswith("_") and t.endswith("_")))


def resolve_keys(tickers, ct: dict, mf: dict) -> dict:
    """ticker → (cik, sec_class_id) を解決する。

    ETF は classId を優先する。両方に無い（指数・仮想テーマ）は対象外。
    """
    out = {}
    for ticker in tickers:
        t = (ticker or "").upper()
        if t in mf:
            cik, _series, class_id = mf[t]
            out[ticker] = (cik, class_id)
        elif t in ct:
            out[ticker] = (ct[t], None)
    return out


def resolve_leftovers_individually(client, tickers, keys: dict) -> dict:
    """一括マスタで解決できなかった分だけ1件ずつ引く。

    `company_tickers.json` は全登録企業を網羅していない（`AEP` が典型）。
    ここを埋めないと該当銘柄が「キー無し＝追跡対象外」に静かに落ちる。

    Returns:
        新たに解決できた ticker → (cik, None) の辞書。
    """
    todo = [t for t in tickers if t not in keys and is_sec_trackable(t)]
    found = {}
    for i, t in enumerate(todo, 1):
        cik = client.lookup_cik_by_ticker(t)
        if cik is not None:
            found[t] = (cik, None)
        if i % 20 == 0 or i == len(todo):
            print(f"    個別照会 {i}/{len(todo)} … 解決 {len(found)}件")
    return found


def run(dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    udb_path = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"))

    print("=" * 74)
    print(f"universe.db に SEC キーを追加  (dry-run={dry_run})")
    print("=" * 74)
    print(f"対象: {udb_path}")

    # ユーザー資産なので必ずバックアップを取る
    if not dry_run:
        bk = f"{udb_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(udb_path, bk)
        print(f"バックアップ: {os.path.basename(bk)}")

    client = SecClient()
    print(f"\n[1] SEC マスタを取得中... (連絡先: {client.contact})")
    ct = client.company_tickers()
    mf = client.company_tickers_mf()
    print(f"    company_tickers {len(ct):,}件 / company_tickers_mf {len(mf):,}件")

    init_universe_db(udb_path)
    with get_universe_write_db() as db:
        added = [] if dry_run else add_columns_if_missing(db)
        if added:
            print(f"\n[2] 列を追加: {added}")
        elif not dry_run:
            print(f"\n[2] 列は既に存在（冪等スキップ）")

        # dry-run では列がまだ存在しないため ORM を使わない（生 SQL で必要な列だけ読む）
        from sqlalchemy import text as _text
        rows = [(r[0], r[1]) for r in
                db.execute(_text("SELECT ticker, category FROM symbols_master"))]
        tickers = [r[0] for r in rows]
        keys = resolve_keys(tickers, ct, mf)

        n_bulk = len(keys)
        leftovers = resolve_leftovers_individually(client, tickers, keys)
        keys.update(leftovers)
        print(f"\n[2b] 一括マスタ {n_bulk:,}件 + 個別照会 {len(leftovers)}件 = {len(keys):,}件")

        from collections import defaultdict
        stat = defaultdict(lambda: [0, 0, 0])   # category -> [cik, class_id, 未解決]
        for ticker, category in rows:
            k = keys.get(ticker)
            if k is None:
                stat[category][2] += 1
            elif k[1]:
                stat[category][1] += 1
            else:
                stat[category][0] += 1

        print(f"\n[3] キーの解決状況（全 {len(rows):,} 件）")
        print(f"    {'カテゴリ':<10}{'cik':>7}{'classId':>9}{'未解決':>8}{'解決率':>8}")
        for cat, (a, b, c) in sorted(stat.items(), key=lambda x: -sum(x[1])):
            tot = a + b + c
            print(f"    {cat:<10}{a:>7}{b:>9}{c:>8}{(a+b)/tot*100:>7.1f}%")

        unresolved = [t for t in tickers if t not in keys]
        virtual = [t for t in unresolved if t.startswith("_") and t.endswith("_")]
        index_like = [t for t in unresolved if t.startswith("^")]
        other = [t for t in unresolved if t not in virtual and t not in index_like]
        print(f"\n    未解決の内訳: 仮想テーマ {len(virtual)} / 指数 {len(index_like)} / その他 {len(other)}")
        if other:
            print(f"      その他: {sorted(other)[:20]}")
            print(f"      ※ 上場廃止済み・OTC・米国外などが該当しうる")

        if dry_run:
            print("\n[DRY-RUN] 書き込んでいません。")
            return

        now = datetime.utcnow()
        n = 0
        for s in db.query(SymbolMaster).all():
            k = keys.get(s.ticker)
            if k is None:
                continue
            cik, class_id = k
            if s.cik != cik or s.sec_class_id != class_id:
                s.cik, s.sec_class_id = cik, class_id
                n += 1
            s.sec_checked_at = now
        db.flush()
        print(f"\n[4] {n:,} 件のキーを更新しました（全 {len(keys):,} 件に sec_checked_at を記録）")

    print("\n=== Done ===")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="universe.db に SEC の安定キーを追加する")
    p.add_argument("--dry-run", action="store_true", help="変更せず解決状況だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    run(dry_run=not a.apply)
