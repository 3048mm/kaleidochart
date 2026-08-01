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
