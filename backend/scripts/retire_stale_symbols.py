"""上場廃止候補を universe.db で退役させる（universe.db 移行 W8）。

## 背景

移行前の T1 は「全件 active=0 → スプレッドシート掲載分だけ active=1」という同期だったため、
シートから消した銘柄は自動的に失効した。universe.db はソフトデリートのみで、一度立った
`active=1` は勝手には落ちない。**この退役ループが切替と同時に失われる**ため、
`weekly_maintenance.py` が出す上場廃止候補を universe.db へ反映する導線を用意する。

## 使い方

    # 週次メンテのレポートを読んで反映（対話確認あり）
    python backend/scripts/retire_stale_symbols.py --from-report

    # 内容だけ確認
    python backend/scripts/retire_stale_symbols.py --from-report --dry-run

    # ティッカーを直接指定
    python backend/scripts/retire_stale_symbols.py --tickers CNCR,LUX --dry-run

退役しても `stocktool.db` の行は削除されない。次回 T1 同期で `active=0` になるだけで、
`symbols.id` と価格履歴は温存される（履歴の孤児化を防ぐため）。
"""

import argparse
import csv
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from db.database_universe import init_universe_db, get_universe_write_db
from db.models_universe import SymbolMaster, ThemeMember

REPORT_PATH = os.path.join(
    _project_root, "data", "maintenance_reports", "delisting_recommendations.csv"
)


def _load_report(path: str) -> list[tuple[str, str]]:
    if not os.path.exists(path):
        print(f"[ERROR] レポートが見つかりません: {path}")
        print("  先に weekly_maintenance.py を実行してください。")
        sys.exit(1)
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ticker = (r.get("ticker") or "").strip()
            if ticker:
                rows.append((ticker, (r.get("reason") or "").strip()))
    return rows


def run(tickers: list[tuple[str, str]], dry_run: bool, assume_yes: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    universe_db_path = config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"),
    )
    init_universe_db(universe_db_path)

    print("=" * 70)
    print(f"上場廃止候補の退役  (dry-run={dry_run})")
    print("=" * 70)

    with get_universe_write_db() as udb:
        targets, skipped = [], []
        for ticker, reason in tickers:
            sym = udb.query(SymbolMaster).filter(SymbolMaster.ticker == ticker).first()
            if sym is None:
                skipped.append((ticker, "universe.db に存在しない"))
            elif sym.active == 0:
                skipped.append((ticker, "既に active=0"))
            else:
                # テーマの親になっている銘柄を落とすと構成が壊れるので保護する
                as_parent = udb.query(ThemeMember).filter(
                    ThemeMember.theme_ticker == ticker
                ).count()
                if as_parent:
                    skipped.append((ticker, f"テーマの親（構成銘柄 {as_parent} 件）のため要手動判断"))
                else:
                    targets.append((sym, reason))

        print(f"\n退役対象: {len(targets)} 件")
        for sym, reason in targets:
            print(f"  {sym.ticker:<10} {sym.category:<8} {reason}")
        print(f"\nスキップ: {len(skipped)} 件")
        for ticker, why in skipped:
            print(f"  {ticker:<10} {why}")

        if dry_run or not targets:
            print("\n[DRY-RUN] 変更していません。" if dry_run else "\n退役対象がありません。")
            return

        if not assume_yes:
            answer = input(f"\n{len(targets)} 件を active=0 にします。続行しますか? [y/N]: ")
            if answer.strip().lower() != "y":
                print("中止しました。")
                return

        for sym, _reason in targets:
            sym.active = 0
        udb.flush()
        print(f"\n→ {len(targets)} 件を active=0 にしました。")
        print("  次回パイプライン実行時に stocktool.db へ反映されます"
              "（symbols.id と価格履歴は温存されます）。")

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="上場廃止候補を universe.db で退役させる")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-report", action="store_true",
                     help=f"週次メンテのレポートを読む ({REPORT_PATH})")
    src.add_argument("--tickers", type=str, help="カンマ区切りのティッカー")
    parser.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    parser.add_argument("--yes", action="store_true", help="確認プロンプトを出さない")
    args = parser.parse_args()

    if args.from_report:
        entries = _load_report(REPORT_PATH)
    else:
        entries = [(t.strip(), "manual") for t in args.tickers.split(",") if t.strip()]

    run(entries, dry_run=args.dry_run, assume_yes=args.yes)
