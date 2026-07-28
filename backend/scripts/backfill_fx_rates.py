"""fx_rates の是正（universe.db 移行計画 W9）

実施内容:
  1. skip_fetch 由来のダミー値行を検出して削除
  2. USD/JPY (JPY=X) の全期間実データをバックフィル
  3. JPY=X を銘柄マスタから退役（stocktool.symbols は active=0、universe.db からは削除）

冪等（再実行可能）。既存日付はスキップし、ダミー行が無ければ削除もスキップする。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_fx_rates.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_fx_rates.py
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_fx_rates.py --skip-retire   # 1,2 のみ
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime

import pandas as pd

# --- path setup -----------------------------------------------------------
_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from db.database import init_db, get_write_db
from db.models import FxRate, Symbol

CURRENCY_PAIR = "USD/JPY"
YF_TICKER = "JPY=X"
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _is_dummy_rate(d: date, rate: float) -> bool:
    """t2_prices.sync_fx_rates の skip_fetch 分岐が入れるダミー値かどうか。

    ダミー式: rate_val = 155.0 + (curr.day % 5) * 0.2
    実勢レートが偶然この式に一致する可能性は低いが、判定は「式の一致」と
    「土日を含む」の両面から行い、誤削除を防ぐ。
    """
    return abs(rate - (155.0 + (d.day % 5) * 0.2)) < 1e-9


def fetch_usdjpy_history() -> pd.DataFrame:
    """Yahoo chart API から JPY=X の全期間日足を取得する。

    yfinance (yf.download) はレート制限 (HTTP 429) を "possibly delisted" として
    握り潰すため、一括バックフィルでは chart API を直接叩く。

    日付は meta.exchangeTimezoneName (Europe/London) で変換する。UTC で変換すると
    金曜バーが土日にずれ込み、為替に存在しないはずの土日行が生成される。
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{YF_TICKER}"
        f"?period1=0&period2={int(time.time())}&interval=1d"
    )
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=60) as resp:
        payload = json.load(resp)

    result = payload["chart"]["result"][0]
    tz = result["meta"].get("exchangeTimezoneName") or "UTC"
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    df = pd.DataFrame({"ts": timestamps, "rate": closes})
    df["date"] = (
        pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz).dt.date
    )
    df = df.dropna(subset=["rate"])
    df = df[df["rate"] > 0]
    df = df.drop_duplicates(subset=["date"], keep="last")
    return df[["date", "rate"]].sort_values("date").reset_index(drop=True)


def run(dry_run: bool = False, skip_retire: bool = False):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    init_db(config["system"]["db_path"])

    print("=" * 66)
    print(f"fx_rates 是正  (dry-run={dry_run})")
    print("=" * 66)

    # --- 1) 取得（DB を触る前に確保する。失敗したら何も壊さない） ---
    print("\n[1] Yahoo chart API から USD/JPY 全期間を取得中...")
    hist = fetch_usdjpy_history()
    if hist.empty:
        print("[ERROR] 為替データを取得できませんでした。中断します。")
        sys.exit(1)
    weekend = sum(1 for d in hist["date"] if d.weekday() >= 5)
    print(f"    取得: {len(hist):,} 行  {hist['date'].min()} 〜 {hist['date'].max()}")
    print(f"    土日行: {weekend} 件（0 であること）")
    if weekend:
        print("[ERROR] 土日行が含まれています。タイムゾーン変換を確認してください。中断します。")
        sys.exit(1)

    with get_write_db() as db:
        # --- 2) ダミー行の検出と削除 ---
        existing = db.query(FxRate).filter(FxRate.currency_pair == CURRENCY_PAIR).all()
        dummies = [r for r in existing if _is_dummy_rate(r.date, r.rate)]
        print(f"\n[2] 既存 fx_rates: {len(existing)} 行 / うちダミー判定: {len(dummies)} 行")
        if dummies:
            span = f"{min(r.date for r in dummies)} 〜 {max(r.date for r in dummies)}"
            wk = sum(1 for r in dummies if r.date.weekday() >= 5)
            print(f"    ダミー範囲: {span}  （土日 {wk} 件を含む＝skip_fetch 由来の裏付け）")
        if not dry_run and dummies:
            for r in dummies:
                db.delete(r)
            db.flush()
            print(f"    → {len(dummies)} 行を削除")

        # --- 3) 実データ投入（既存日付はスキップ＝冪等） ---
        remaining = {
            r.date for r in db.query(FxRate).filter(FxRate.currency_pair == CURRENCY_PAIR).all()
        }
        to_add = [row for row in hist.itertuples(index=False) if row.date not in remaining]
        print(f"\n[3] 投入対象: {len(to_add):,} 行（既存 {len(remaining)} 行はスキップ）")
        if not dry_run and to_add:
            db.bulk_save_objects([
                FxRate(currency_pair=CURRENCY_PAIR, date=row.date, rate=float(row.rate))
                for row in to_add
            ])
            db.flush()
            print(f"    → {len(to_add):,} 行を投入")

        # --- 4) JPY=X の銘柄退役 ---
        if not skip_retire:
            sym = db.query(Symbol).filter(Symbol.ticker == YF_TICKER).first()
            print(f"\n[4] stocktool.symbols の {YF_TICKER} 退役")
            if sym is None:
                print("    対象なし（既に削除済み）")
            elif sym.active == 0:
                print("    既に active=0（冪等スキップ）")
            else:
                print(f"    id={sym.id} active=1 → 0")
                if not dry_run:
                    sym.active = 0
                    db.flush()
        else:
            print("\n[4] --skip-retire 指定のため銘柄退役をスキップ")

        # --- 5) 結果サマリ ---
        if not dry_run:
            rows = db.query(FxRate).filter(FxRate.currency_pair == CURRENCY_PAIR).all()
            print("\n--- 結果 ---")
            print(f"  fx_rates ({CURRENCY_PAIR}): {len(rows):,} 行")
            if rows:
                dates = sorted(r.date for r in rows)
                latest = max(rows, key=lambda r: r.date)
                print(f"  期間  : {dates[0]} 〜 {dates[-1]}")
                print(f"  最新値: {latest.rate:.4f} ({latest.date})")
                print(f"  土日行: {sum(1 for r in rows if r.date.weekday() >= 5)} 件")
                print(f"  残ダミー: {sum(1 for r in rows if _is_dummy_rate(r.date, r.rate))} 件")

    # --- 6) universe.db から JPY=X を削除 ---
    if not skip_retire:
        from db.database_universe import init_universe_db, get_universe_write_db
        from db.models_universe import SymbolMaster

        universe_db_path = config["system"].get(
            "universe_db_path",
            os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"),
        )
        init_universe_db(universe_db_path)
        with get_universe_write_db() as udb:
            target = udb.query(SymbolMaster).filter(SymbolMaster.ticker == YF_TICKER).all()
            print(f"\n[5] universe.db の {YF_TICKER}: {len(target)} 件")
            if not dry_run:
                for t in target:
                    udb.delete(t)
                udb.flush()
                if target:
                    print(f"    → {len(target)} 件を削除")

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="fx_rates のダミー除去と全期間バックフィル")
    parser.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    parser.add_argument("--skip-retire", action="store_true", help="JPY=X の銘柄退役を行わない")
    args = parser.parse_args()
    run(dry_run=args.dry_run, skip_retire=args.skip_retire)
