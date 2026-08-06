"""universe.db のティッカーを改称する（コーポレートアクション対応）。

## 背景

ティッカー変更は珍しくない（2026年6月だけで FDP→DMC / IAC→PPLI / VSCO→VSXY が発生）。
旧ティッカーは Yahoo 側にも数行の残骸だけが残るため、週次メンテの鮮度監査では
「履歴がほぼ無い銘柄」として上場廃止候補に見えるが、実際には**改称なので新ティッカーへ
付け替えるのが正しい**。

## 処理

    1. 新ティッカーが未使用であることを確認
    2. symbols_master.ticker を更新（必要なら exchange も）
    3. theme_members の theme_ticker / member_ticker を連動更新
    4. ticker_history に旧ティッカーを記録

`symbols_master.id` は変わらない。`stocktool.db` 側は次回パイプライン実行の T1 同期で
新ティッカーの行が作られ、旧ティッカーの行は universe.db に存在しなくなるため
`active=0` に落ちる（価格履歴は旧 id に温存される）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\rename_symbol.py --from FDP --to DMC --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\rename_symbol.py --from FDP --to DMC --reason "2026-06-29 ticker change"
    .\\venv\\Scripts\\python.exe backend\\scripts\\rename_symbol.py --batch renames.csv
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import date

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from data_collection.symbol_classify import derive_theme_type
from db.database_universe import init_universe_db, get_universe_write_db
from db.models_universe import SymbolMaster, ThemeMember, TickerHistory


# 改称に追随させる `user_data.db` のテーブル。
# **`position_history` は含めない。** あれは「その時どの銘柄を売買したか」の記録で、
# 現行ティッカーへ書き換えると取引履歴として不正確になる
# （`symbol_id` は `api/symbol_heal.py` が ticker_history 経由で現行へ解決する）。
USER_DATA_RENAME_TABLES = ("watchlist", "portfolio_positions")


def _user_db_path() -> str:
    """`config.toml` から `user_data.db` の場所を解決する。"""
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    return config["system"].get(
        "user_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "user_data.db"))


def rename_user_data_references(user_db_path: str, old: str, new: str,
                                exchange: str | None, dry_run: bool) -> dict[str, int | None]:
    """`user_data.db` のティッカー参照を改称に追随させる。

    ウォッチリストは `ticker` を耐久キーにして「DB を作り直しても追随できる」設計だが、
    **ticker 自体が変わると宙に浮く**（2026-08-06 に `ATLN`→`CIRC` で実際に発生）。
    改称を適用したその場で書き換えておけば、次の画面表示から正しく出る。

    `symbol_id` はここでは触らない。改称直後の `stocktool.db` にはまだ新ティッカーの
    行が無い（T1 同期は次回パイプライン実行）ため、解決は heal に任せる。

    Returns:
        テーブル名 → 更新件数。テーブルが無ければ ``None``。
        `user_data.db` 自体が無ければ空辞書（改称を止めない）。
    """
    if not os.path.exists(user_db_path):
        return {}

    con = sqlite3.connect(user_db_path, timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=NORMAL")
        out: dict[str, int | None] = {}
        con.execute("BEGIN IMMEDIATE")
        for table in USER_DATA_RENAME_TABLES:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker = ?",
                                (old,)).fetchone()[0]
            except sqlite3.OperationalError:
                out[table] = None      # 旧スキーマ・テスト環境など
                continue
            if not dry_run and n:
                if exchange:
                    con.execute(f"UPDATE {table} SET ticker = ?, exchange = ? WHERE ticker = ?",
                                (new, exchange, old))
                else:
                    # 取引所が変わらない改称で exchange を潰さない
                    con.execute(f"UPDATE {table} SET ticker = ? WHERE ticker = ?", (new, old))
            out[table] = n
        con.commit() if not dry_run else con.rollback()
        return out
    finally:
        con.close()


def rename_one(udb, old: str, new: str, reason: str, exchange: str | None, dry_run: bool) -> bool:
    """1銘柄を改称する。実施したら True、スキップしたら False。"""
    sym = udb.query(SymbolMaster).filter(SymbolMaster.ticker == old).first()
    if sym is None:
        # 既に改称済みなら冪等にスキップ
        if udb.query(SymbolMaster).filter(SymbolMaster.ticker == new).first():
            print(f"  {old:<8} → {new:<8} 既に改称済み（冪等スキップ）")
        else:
            print(f"  {old:<8} → {new:<8} [SKIP] 旧ティッカーが universe.db にありません")
        return False

    clash = udb.query(SymbolMaster).filter(SymbolMaster.ticker == new).first()
    if clash:
        print(f"  {old:<8} → {new:<8} [SKIP] 新ティッカーが既に存在します "
              f"(id={clash.id}, active={clash.active})。手動で統合してください")
        return False

    members = udb.query(ThemeMember).filter(ThemeMember.member_ticker == old).all()
    parents = udb.query(ThemeMember).filter(ThemeMember.theme_ticker == old).all()
    ex_note = f" / exchange {sym.exchange!r}→{exchange!r}" if exchange else ""
    print(f"  {old:<8} → {new:<8} id={sym.id} category={sym.category}{ex_note}"
          f"  所属テーマ {len(members)} 件 / 親としての構成 {len(parents)} 件")

    if dry_run:
        return True

    old_exchange = sym.exchange
    sym.ticker = new
    if exchange:
        sym.exchange = exchange

    # ticker / exchange が変われば theme_type も変わりうる
    # （例: SIXG(NYSEARCA, theme) → _SIXG_(VIRTUAL, virtual) の仮想テーマ化）
    new_type = derive_theme_type(sym.exchange, sym.category, sym.ticker)
    if (sym.theme_type or None) != new_type:
        print(f"  {'':<8}   theme_type {sym.theme_type!r} → {new_type!r}")
        sym.theme_type = new_type

    for m in members:
        m.member_ticker = new
    for p in parents:
        p.theme_ticker = new

    # 同一の旧ティッカーを二重記録しない（uq_ticker_history_old 制約がある）
    exists = udb.query(TickerHistory).filter(
        TickerHistory.old_ticker == old,
        TickerHistory.old_exchange == old_exchange,
    ).first()
    if not exists:
        udb.add(TickerHistory(
            current_ticker=new,
            old_ticker=old,
            old_exchange=old_exchange,
            changed_at=str(date.today()),
            reason=reason,
        ))
    udb.flush()

    # ウォッチリスト・保有ポジションを改称に追随させる（取引履歴の ticker は残す）
    try:
        touched = rename_user_data_references(_user_db_path(), old, new, exchange, dry_run=False)
        moved = {t: n for t, n in touched.items() if n}
        if moved:
            print(f"  {'':<8}   user_data: " +
                  " / ".join(f"{t} {n}件" for t, n in moved.items()))
    except Exception as e:  # noqa: BLE001 — 追随の失敗で改称そのものを巻き戻さない
        print(f"  {'':<8}   [WARN] user_data.db の追随に失敗しました: {e}")
        print(f"  {'':<8}          起動時の heal が ticker_history から自動修復します")
    return True


def run(pairs: list[tuple[str, str, str, str | None]], dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    universe_db_path = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"),
    )
    init_universe_db(universe_db_path)

    print("=" * 72)
    print(f"ティッカー改称  (dry-run={dry_run})")
    print("=" * 72)
    print()

    done = 0
    with get_universe_write_db() as udb:
        for old, new, reason, exchange in pairs:
            if rename_one(udb, old, new, reason, exchange, dry_run):
                done += 1

        if not dry_run:
            udb.flush()
            print(f"\n→ {done} 件を改称しました。")
            print("  次回パイプライン実行時に stocktool.db へ反映されます"
                  "（新ティッカーの行が作られ、旧ティッカーは active=0 になります）。")
        else:
            print(f"\n[DRY-RUN] 変更していません（対象 {done} 件）。")

    if not dry_run:
        with get_universe_write_db() as udb:
            print("\n--- ticker_history ---")
            for h in udb.query(TickerHistory).order_by(TickerHistory.changed_at).all():
                print(f"  {h.old_ticker:<8} → {h.current_ticker:<8} "
                      f"({h.old_exchange}) {h.changed_at}  {h.reason}")

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="universe.db のティッカーを改称する")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from", dest="old", type=str, help="旧ティッカー")
    src.add_argument("--batch", type=str,
                     help="CSV から一括（列: old,new,reason[,exchange]）")
    parser.add_argument("--to", dest="new", type=str, help="新ティッカー")
    parser.add_argument("--reason", type=str, default="ticker change", help="改称理由")
    parser.add_argument("--exchange", type=str, default=None, help="取引所も変える場合に指定")
    parser.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    args = parser.parse_args()

    if args.batch:
        entries = []
        with open(args.batch, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("old") or "").strip():
                    entries.append((
                        r["old"].strip(), r["new"].strip(),
                        (r.get("reason") or "ticker change").strip(),
                        (r.get("exchange") or "").strip() or None,
                    ))
    else:
        if not args.new:
            parser.error("--from を使う場合は --to も必要です")
        entries = [(args.old, args.new, args.reason, args.exchange)]

    run(entries, dry_run=args.dry_run)
