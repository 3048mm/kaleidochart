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
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

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
    """weekly_maintenance が出す退役候補 CSV を読む。

    CSV には判定根拠（classification / rows / last_date）が入っている。
    `lagging`（自己回復する一時的な取得漏れ）はそもそも書き出されない。
    """
    if not os.path.exists(path):
        print(f"[ERROR] レポートが見つかりません: {path}")
        print("  先に weekly_maintenance.py を実行してください。")
        sys.exit(1)
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ticker = (r.get("ticker") or "").strip()
            if not ticker:
                continue
            kind = (r.get("classification") or "").strip()
            n = (r.get("rows") or "").strip()
            last = (r.get("last_date") or "").strip()
            if kind:
                label = f"{kind} (rows={n}, last={last or 'none'})"
            else:
                label = (r.get("reason") or "").strip()
            rows.append((ticker, label))
    return rows


_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def probe_supply(ticker: str) -> tuple[str, int, str | None]:
    """供給側（Yahoo）に本当にデータが無いかを確認する。

    週次メンテの判定は自分の DB しか見ていないため、「供給が止まった」のか
    「こちらの取得が失敗し続けている」のかを区別できない。実際 `EDOC` は
    DB が 2026-07-17 で止まっていたが Yahoo には 07-28 まで存在した（2026-07-29 実測）。
    退役は不可逆な運用判断なので、実行前に必ず供給側を照会する。

    yfinance は HTTP 404 も 429 も同じメッセージに畳むため、chart API を直接叩いて
    HTTP ステータスを見る。

    Returns:
        (status, 直近1ヶ月の有効行数, 最終日)
        status は "gone"（404＝存在しない） / "alive"（データあり） /
        "truncated"（上流が系列を切り落とした） /
        "empty"（応答はあるがデータなし） / "error"（判定不能）
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        return ("gone" if e.code == 404 else "error"), 0, None
    except Exception:
        return "error", 0, None

    result = (payload.get("chart") or {}).get("result")
    if not result:
        return "empty", 0, None
    r0 = result[0]
    ts = r0.get("timestamp") or []
    closes = (r0.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    valid = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if not valid:
        return "empty", 0, None
    last = dt.datetime.fromtimestamp(valid[-1][0], dt.timezone.utc).date()

    if _is_truncated(r0.get("meta") or {}):
        return "truncated", len(valid), str(last)
    return "alive", len(valid), str(last)


def _is_truncated(meta: dict) -> bool:
    """上流(Yahoo)が銘柄レコードを作り直して系列を切り落とした状態か判定する。

    2026-08-02 に BLD(TopBuild) など17銘柄で発生。Yahoo は銘柄を認識していて
    正式名称も取引所も返すのに、chart の時系列だけが最近の日付から始まる。

    決め手は **`firstTradeDate` と 52週レンジの自己矛盾**:
        BLD  firstTradeDate=2026-06-30（1ヶ月前）  なのに 52週高値=559.47
    52週高安は1年分のデータが無ければ算出できないので、集計レイヤーには履歴が
    あるのに時系列だけが孤立している証拠になる。

    この状態は上場廃止でも改称でもないため **退役させてはいけない**。取り直しても
    直らないので、旧 Parquet 世代から継ぐ
    （`backend/scripts/restore_truncated_symbol_history.py`）。

    実測: 対象17件を 17/17 で検出、対照群 AAPL/SPY/MSFT は誤検出ゼロ。
    """
    ftd = meta.get("firstTradeDate")
    lo, hi = meta.get("fiftyTwoWeekLow"), meta.get("fiftyTwoWeekHigh")
    if not ftd or lo is None or hi is None or hi <= lo:
        return False
    first_trade = dt.datetime.fromtimestamp(ftd, dt.timezone.utc).date()
    return (dt.date.today() - first_trade).days < 365


def run(tickers: list[tuple[str, str]], dry_run: bool, assume_yes: bool, verify: bool = True):
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

        # --- 供給側の検証（退役は不可逆なので既定で実施） -------------------
        if verify and targets:
            print(f"\n供給側の検証中（{len(targets)} 件を Yahoo に照会）...")
            verified = []
            for sym, reason in targets:
                status, n, last = probe_supply(sym.ticker)
                if status == "truncated":
                    # 「取得側の問題」で片付けると原因不明のまま放置される。
                    # 打つ手（旧 Parquet 世代からの復元）まで示す。
                    skipped.append((
                        sym.ticker,
                        f"上流が系列を切断（firstTradeDate 打ち直し・直近1ヶ月 {n} 行）"
                        f" → 退役不可。restore_truncated_symbol_history.py で復元",
                    ))
                elif status == "alive":
                    skipped.append((
                        sym.ticker,
                        f"供給側にデータあり（直近1ヶ月 {n} 行・最終 {last}）→ 退役ではなく取得側の問題",
                    ))
                elif status == "error":
                    skipped.append((sym.ticker, "供給側を照会できず判定不能（要再実行）"))
                else:
                    verified.append((sym, f"{reason} / 供給側={status}"))
                time.sleep(0.5)
            targets = verified

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
    parser.add_argument("--no-verify", action="store_true",
                        help="供給側（Yahoo）への照会をスキップする（非推奨）")
    args = parser.parse_args()

    if args.from_report:
        entries = _load_report(REPORT_PATH)
    else:
        entries = [(t.strip(), "manual") for t in args.tickers.split(",") if t.strip()]

    run(entries, dry_run=args.dry_run, assume_yes=args.yes, verify=not args.no_verify)
