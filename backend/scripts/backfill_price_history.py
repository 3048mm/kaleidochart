"""価格履歴を過去方向へ**追記のみ**で充足する（Parquet マスターに直接書く）。

## なぜ必要か

個別銘柄の価格は `config.toml` の `default_start_date`（2018-04-01）からしか
取得していない。そのため `optimization_validation` の `stress_bear` 窓
（**2018-10-01 〜 12-31**）は遡りが約125営業日しかなく:

| 指標 | 必要な遡り | 2018-10-01 時点 |
| :--- | ---: | ---: |
| `sma_200` | 200本 | 125本 → 成立しない |
| `dist_52w_high_pct` | 252本 | 125本 → 成立しない |
| `is_trend_template` | 上記に依存 | **判定そのものが無意味** |
| `ema_200` | 収束に ~750本 | 初期値の残存 **28.7%** |

2017年まで遡れば約440営業日になり、ローリング窓は充足、EMA の残存も約1.25% になる。

## なぜ「追記のみ」なのか

全期間再構築（既存を消して yfinance から取り直す）は**不可逆**で、
上流が系列を切り落とした銘柄の履歴を永久に失う。

    2026-08-02 の再構築で 17銘柄・8年分が 1〜11行まで消えた
    （BLD / LC / SCVL など。旧世代が data/_bk/ に偶然残っていて復旧できた）

本スクリプトは**既存行を1行も書き換えない**。取得するのは
`[start-date, 各銘柄の現在の最古日 - 1]` の区間だけで、同一キーは常に既存が勝つ。

## なぜ SQLite ではなく Parquet に書くのか

2017年の行はホット期間（730日）の外なので、SQLite に入れると
`purge_sqlite_cache_older_than_2_years` に削除される。充足と rotate が別実行に
なると間に purge が入って消えるため、Parquet 直書きにする
（`backfill_structure_pivot.py` と同じ方針）。

## 429 の握り潰しに注意

yfinance はレート制限を `possibly delisted; no price data found` として返すため、
**部分的にしか取れなくても成功に見える**（`backfill_symbol_history.py` の docstring が
警告している既知の罠）。取得後に銘柄ごとの到達日を突合し、不足銘柄を必ず報告する。

## 使い方

    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_price_history.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_price_history.py --apply

実行後は T3 以降を作り直すこと:

    .\\venv\\Scripts\\python.exe backend\\scripts\\update_pipeline.py --rebuild-from T3
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

PRICE_COLS = ["symbol_id", "date", "open", "high", "low", "close", "volume"]

# 収集開始の境界（config.toml の data_collection.default_start_date）。
# **「最古日 = この日」は上場日ではなく収集の境界**なので、遡る余地がある。
DEFAULT_COLLECTION_START = "2018-04-01"

# 収集境界からこの日数以内に始まっていれば「境界で切られている」とみなす。
# それより後に始まる銘柄は**実際にその時期に上場した**ので、遡っても取るものが無い
# （2024年上場の銘柄に 2017年を要求しても毎回「取得できなかった」と報告され続ける）。
DEFAULT_LISTED_GRACE_DAYS = 90

# 到達日の許容。取引所休場や上場直後の薄商いで数日ずれるのは正常。
DEFAULT_TOLERANCE_DAYS = 30


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def select_targets(prices: pd.DataFrame, symbols: pd.DataFrame, start_date: str,
                   categories, collection_start: str = DEFAULT_COLLECTION_START,
                   listed_grace_days: int = DEFAULT_LISTED_GRACE_DAYS) -> list:
    """充足対象の銘柄を選ぶ。

    ## 「最古日 = 2018-04-02」は上場日ではない

    価格データの最古日には2つの意味が混ざっている:

      - **収集の境界**: `default_start_date`(2018-04-01) からしか取っていないだけ。
        上流にはもっと過去がある → **遡る価値がある**
      - **実際の上場日**: 2024年に上場した銘柄など → **遡っても取るものが無い**

    両者は最古日だけでは区別できないので、「収集境界の近傍から始まっているか」で
    判定する。これを入れないと、後から上場した銘柄が毎回対象に入り、毎回
    「取得できなかった」と報告され続ける。

    対象になる条件（すべて満たすこと）:

      1. 指定カテゴリの active 銘柄
      2. 現在の最古日が `start_date` よりあと（＝まだ遡る余地がある。冪等性の担保）
      3. 現在の最古日が `collection_start` + `listed_grace_days` 以内
         （＝収集境界で切られている。それより後なら実際の上場）

    Returns:
        [{'symbol_id', 'ticker', 'current_first'}, ...]
    """
    if prices.empty or symbols.empty:
        return []

    cats = set(categories) if categories else None
    sym = symbols[symbols.get("active", 1) == 1]
    if cats is not None:
        sym = sym[sym["category"].isin(cats)]
    allowed = dict(zip(sym["id"].astype(int), sym["ticker"]))
    if not allowed:
        return []

    px = prices[prices["symbol_id"].isin(allowed)]
    if px.empty:
        return []
    first = px.groupby("symbol_id")["date"].min().astype(str)

    limit = (pd.to_datetime(collection_start)
             + timedelta(days=listed_grace_days)).date().isoformat()
    out = []
    for sid, cur_first in first.items():
        if cur_first <= start_date:
            continue                    # 既に十分遡れている（冪等）
        if cur_first > limit:
            continue                    # 収集境界より後＝実際にその時期に上場した銘柄
        out.append({"symbol_id": int(sid), "ticker": allowed[int(sid)],
                    "current_first": cur_first})
    return out


def merge_append_only(existing: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """取得分を**追記だけ**して返す。同一 (symbol_id, date) は常に既存が勝つ。

    上書きを許すと、上流の再取得結果で既存の価格が書き換わりうる。
    本スクリプトの目的は「過去を足す」ことであって「直す」ことではない。
    """
    if fetched is None or fetched.empty:
        return existing.sort_values(["symbol_id", "date"]).reset_index(drop=True)

    ex = existing.copy()
    fe = fetched.copy()
    for df in (ex, fe):
        df["date"] = df["date"].astype(str)

    keys = set(zip(ex["symbol_id"], ex["date"]))
    mask = [(s, d) not in keys for s, d in zip(fe["symbol_id"], fe["date"])]
    new_rows = fe[mask]

    out = pd.concat([ex, new_rows], ignore_index=True)
    return out.sort_values(["symbol_id", "date"]).reset_index(drop=True)


def verify_coverage(merged: pd.DataFrame, targets: list, start_date: str,
                    tolerance_days: int = DEFAULT_TOLERANCE_DAYS) -> list:
    """要求した開始日まで実際に遡れたかを銘柄ごとに検証する。

    yfinance の 429 は `possibly delisted` として返るため、**取れなくても例外にならない**。
    ここで突合しないと「充足したつもり」で先へ進んでしまう。

    Returns:
        到達できなかった銘柄のリスト [{'ticker', 'symbol_id', 'reached', 'requested'}]
    """
    if not targets:
        return []
    limit = (pd.to_datetime(start_date) + timedelta(days=tolerance_days)).date().isoformat()

    first = {}
    if not merged.empty:
        first = merged.groupby("symbol_id")["date"].min().astype(str).to_dict()

    short = []
    for t in targets:
        reached = first.get(t["symbol_id"])
        if reached is None or reached > limit:
            short.append({"ticker": t["ticker"], "symbol_id": t["symbol_id"],
                          "reached": reached, "requested": start_date})
    return short


def fetch_range(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinance から `[start, end]` を取得する。取れなければ空を返す。

    > [!IMPORTANT]
    > **`ensure_ca_bundle()` を先に呼ぶこと。** TLS 傍受環境では `curl_cffi`
    > （yfinance が使う）が独自の CA バンドルを見るため証明書を検証できず、
    > **全銘柄が失敗する**。しかも yfinance はそれを
    > `possibly delisted; no price data found` に畳むので、上流障害と区別がつかない
    > （`data_collection/tls_trust.py` の docstring / `upstream-data-diagnosis` §2.1）。
    >
    > 2026-09-04 に本スクリプトで実際に踏んだ: 50銘柄すべてが 0 行を返し、
    > GLTR / RING / PICK のような稼働中の ETF まで「上場廃止」と報告された。
    > `data_collection/fetcher.py` は import 時にこれを呼んでいるが、
    > 本スクリプトは fetcher を経由しないため自前で呼ぶ必要がある。
    """
    import yfinance as yf

    # **auto_adjust を指定しない（yfinance の既定 True）。** 既存パイプライン
    # （data_collection/fetcher.py L46）が既定のまま呼んでいるので、そこに揃える。
    #
    # 2026-09-04 に auto_adjust=False（生の終値）で取得して失敗した:
    # 既存は配当・分割を調整した値、充足分は生の値になり、接合部(2018-04-02)で
    # 終値比の中央値が 0.898、409銘柄が±25%超の段差になった。高配当の REIT・BDC
    # ほど乖離が大きく（KDP は特別配当の影響で比 0.13）、配当調整の不一致だった。
    # scan_price_anomalies はこれを split_suspect ではなく market_wide と分類する
    # （2018-04-02 が実際に -2.2% の下落日で紛れる）ため、**接合部の終値比を
    # 直接測らないと見逃す**。
    df = yf.download(ticker, start=start, end=end, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame(columns=PRICE_COLS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["Date"]).dt.date.astype(str)
    return pd.DataFrame({
        "date": df["date"],
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low": df["Low"].astype(float),
        "close": df["Close"].astype(float),
        "volume": df["Volume"].astype(float),
    })


def run(start_date: str, categories, apply: bool, sleep_sec: float) -> int:
    import tomli
    # TLS 傍受環境で curl_cffi が証明書を検証できず全銘柄が失敗するのを防ぐ
    # （fetch_range の docstring 参照）
    from data_collection.tls_trust import ensure_ca_bundle
    ensure_ca_bundle()
    from pipeline.parquet_cache_manager import (
        get_latest_master_files, get_parquet_master_dir, get_pointer_file_path,
        update_pointer_with_retry,
    )

    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    parquet_dir = get_parquet_master_dir(config["system"]["db_path"])
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        return 1

    print("=" * 74)
    print(f"価格履歴の充足  start={start_date} / categories={categories} / apply={apply}")
    print("=" * 74)

    prices = pd.read_parquet(cur["prices"])
    prices["date"] = prices["date"].astype(str)
    symbols = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    _log(f"現行世代 prices {len(prices):,}行 / {os.path.basename(cur['prices'])}")

    targets = select_targets(prices, symbols, start_date, categories)
    _log(f"充足対象: {len(targets):,} 銘柄")
    if targets:
        print("    例:", ", ".join(f"{t['ticker']}({t['current_first']})" for t in targets[:8]))

    if not apply:
        print("\n--apply が無いのでドライランです。取得は行っていません。")
        return 0
    if not targets:
        print("\n充足が必要な銘柄はありません。")
        return 0

    _log("yfinance から取得します（既存行には触れません）")
    fetched = []
    failed = []
    for i, t in enumerate(targets, 1):
        end = (pd.to_datetime(t["current_first"]) - timedelta(days=1)).date().isoformat()
        try:
            df = fetch_range(t["ticker"], start_date, end)
        except Exception as e:
            failed.append({"ticker": t["ticker"], "error": str(e)[:80]})
            df = pd.DataFrame(columns=PRICE_COLS)
        if not df.empty:
            df.insert(0, "symbol_id", t["symbol_id"])
            fetched.append(df)
        if i % 50 == 0:
            _log(f"    {i}/{len(targets)} 取得済み（追加 {sum(len(d) for d in fetched):,} 行）")
        time.sleep(sleep_sec)

    add = pd.concat(fetched, ignore_index=True) if fetched else pd.DataFrame(columns=PRICE_COLS)
    _log(f"取得完了: {len(add):,} 行 / 例外 {len(failed)} 件")

    merged = merge_append_only(prices, add)
    gained = len(merged) - len(prices)
    _log(f"追記後: {len(prices):,} → {len(merged):,} 行（+{gained:,}）")

    # 防護柵: 追記なので行が減ることはありえない
    if len(merged) < len(prices):
        print("[ERROR] 行数が減っています。追記専用のはずなので中断します。")
        return 1

    short = verify_coverage(merged, targets, start_date)
    if short:
        print(f"\n[WARN] 要求開始日に到達できなかった銘柄: {len(short)} / {len(targets)}")
        print("       429 の握り潰しか、上流にその期間のデータが無い可能性があります。")
        for s in short[:15]:
            print(f"         {s['ticker']:<8} 到達 {s['reached']}")
        report = os.path.join(_project_root, "data", "maintenance_reports",
                              f"backfill_shortfall_{datetime.now():%Y%m%d_%H%M%S}.csv")
        os.makedirs(os.path.dirname(report), exist_ok=True)
        pd.DataFrame(short).to_csv(report, index=False, encoding="utf-8")
        print(f"       全件: {report}")

    if gained == 0:
        print("\n追記された行がありません。ポインタは更新しません。")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "prices":
            merged.to_parquet(dst, index=False)
        else:
            # prices 以外は同世代へコピーする。pointer が複数世代を混ぜて参照すると
            # prune で参照中のファイルが消える
            shutil.copy2(cur[key], dst)
        files[key] = dst
        _log(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("backfill")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        return 1
    _log(f"pointer を data_version_{ts} に更新しました")
    print("\n次に T3 以降を作り直してください:")
    print("    python backend/scripts/update_pipeline.py --rebuild-from T3")
    return 0


if __name__ == "__main__":
    from pipeline.pipeline_lock import pipeline_lock

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start-date", default="2017-01-01")
    p.add_argument("--category", default="個別,テーマ,セクタ",
                   help="カンマ区切り。ETF 系は既に index_start_date から取得済み")
    p.add_argument("--sleep", type=float, default=0.4, help="銘柄間の待機秒")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    cats = [c.strip() for c in a.category.split(",") if c.strip()]
    with pipeline_lock("backfill_price_history"):
        raise SystemExit(run(a.start_date, cats, a.apply, a.sleep))
