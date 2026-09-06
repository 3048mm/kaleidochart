"""VIX 系指数の欠損を CBOE の公式配信で埋める（追記専用）。

## なぜ必要か（2026-09-04 発見）

Yahoo は VIX の**期間構造**（`^VIX3M` / `^VIX9D` / `^VIX6M`）を落とすことがある。
`^VIX` 本体は無傷なので、パイプライン全体の異常としては現れない。

    ティッカー   2026年の行数   欠損日の回収
    ^VIX3M          136          0/14
    ^VIX9D          136          0/14
    ^VIX6M          136          0/14
    ^VIX             —          12/14   ← 本体だけ無傷

`^VIX3M` は MTS（Market Trend Score）の入力なので、欠けると
`market_signals.py` が直前値を持ち越す。2026-07-20〜08-04 の **12営業日**が
持ち越しになり、パイプライン自身が「10営業日以上の持ち越しは信頼できません」と
警告を出していた。

## 上流から取り直しても直らない（むしろ悪化する）

`upstream-data-diagnosis` §2 の決定論テストを5通り実施して全て 0/14 だった:

    range 2026-07-15..08-12      3行  欠損日を回収: 0/14
    range 2026-01-01..now      136行  欠損日を回収: 0/14
    range 2024-01-01..now      638行  欠損日を回収: 0/14
    period=1y                  219行  欠損日を回収: 0/14
    period=max                5033行  欠損日を回収: 0/14   ← 2006年まで遡っても無い

旧ティッカー `^VXV` は廃止済み（`possibly delisted`）、`VIX3M`（^なし）は 404。

> [!CAUTION]
> **`^VIX3M` を全期間取り直してはいけない。** 手元 155日 / 上流 136日 で、
> **こちらの方が20日ぶん完全**。過去に取れた日を Yahoo が後から落としている。
> 取り直すと欠損が 14日 → 34日 に悪化する（2026-08-02 に17銘柄・8年分を失った
> 事故と同じ経路）。だから本スクリプトは**追記専用**で、既存行に触れない。

## CBOE を使う根拠

VIX は CBOE が算出しており、Yahoo はその再配信にすぎない。**一次情報に近い。**
重複 4,118行での実測一致度:

    close  最大差 1.0500  相対 p50=2.06e-08 p99=5.47e-08  0.5%超の行 4 / 4,118

浮動小数点精度で同一。別系列を混ぜる心配は無い。

## やること

  1. CBOE から日次履歴 CSV を取得
  2. **重複期間で既存行と一致するかを検算**（一致しなければ中断＝唯一の歯止め）
  3. SPY の営業日カレンダーに存在し、かつ手元に無い日だけを追記
  4. Parquet の新世代を書き出してポインタを更新

T3 / T5 / SQLite への反映は**行わない**。`update_pipeline.py --rebuild-from T3`
を続けて実行すること（MTS は T5 なので T3→T4→T5 が再計算される）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_vix_from_cboe.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\backfill_vix_from_cboe.py --apply
"""

import argparse
import io
import os
import sys
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
for _p in (_project_root, _backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests  # noqa: E402
import tomli  # noqa: E402

from data_collection.tls_trust import ensure_ca_bundle  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)
from pipeline.parquet_maintenance import (  # noqa: E402
    resolve_db_path,
    write_master_generation,
)
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402

CBOE_URL = ("https://cdn.cboe.com/api/global/us_indices/daily_prices/"
            "{name}_History.csv")

# 当プロジェクトのティッカー → CBOE のファイル名
TICKER_TO_CBOE = {"^VIX3M": "VIX3M", "^VIX": "VIX", "^VIX9D": "VIX9D",
                  "^VIX6M": "VIX6M"}

# 重複期間の一致検証。これを超える相対差の行が許容割合を上回れば中断する。
MATCH_TOLERANCE = 0.005
MAX_MISMATCH_RATIO = 0.01

# 検証に使う重複行の下限。これ未満だと一致を判断できない。
MIN_OVERLAP_ROWS = 200

# カレンダーの基準（この銘柄が取引した日だけを営業日とみなす）
CALENDAR_TICKER = "SPY"


class CboeBackfillError(RuntimeError):
    """検算に失敗した。**迂回できるようにしないこと。**"""


def fetch_cboe(name: str) -> pd.DataFrame:
    """CBOE の日次履歴を取得して `date/open/high/low/close` に正規化する。"""
    url = CBOE_URL.format(name=name)
    r = requests.get(url, timeout=60)
    if not r.ok:
        raise CboeBackfillError(f"{name}: CBOE が HTTP {r.status_code} を返した（{url}）")
    df = pd.read_csv(io.StringIO(r.text))
    need = {"DATE", "OPEN", "HIGH", "LOW", "CLOSE"}
    if not need.issubset(df.columns):
        raise CboeBackfillError(
            f"{name}: CSV の列が想定と違う（{list(df.columns)}）。"
            f"CBOE 側の書式変更を疑うこと。")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["DATE"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
        "open": df["OPEN"].astype(float),
        "high": df["HIGH"].astype(float),
        "low": df["LOW"].astype(float),
        "close": df["CLOSE"].astype(float),
    })
    return out.sort_values("date").reset_index(drop=True)


def verify_overlap(mine: pd.DataFrame, cboe: pd.DataFrame, ticker: str,
                   tolerance: float = MATCH_TOLERANCE) -> dict:
    """重複期間で同じ系列かを検算する。**別物を混ぜないための唯一の歯止め。**"""
    m = mine[["date", "close"]].merge(
        cboe[["date", "close"]].rename(columns={"close": "cboe"}), on="date")
    m = m[(m["close"] > 0) & (m["cboe"] > 0)]
    if len(m) < MIN_OVERLAP_ROWS:
        raise CboeBackfillError(
            f"{ticker}: 重複が {len(m)} 行しかない（最低 {MIN_OVERLAP_ROWS} 行）。"
            f"同じ系列か判断できないので中断する。")
    rel = ((m["close"] - m["cboe"]).abs() / m["cboe"])
    off = int((rel > tolerance).sum())
    ratio = off / len(m)
    if ratio > MAX_MISMATCH_RATIO:
        raise CboeBackfillError(
            f"{ticker}: 重複 {len(m):,}行のうち {off}行 ({ratio:.2%}) が "
            f"{tolerance:.1%} を超えてズレている。別系列の可能性があるので中断する。")
    return {"rows": len(m), "off": off, "p50": float(rel.median()),
            "max": float(rel.max())}


def select_fill_dates(mine: pd.DataFrame, cboe: pd.DataFrame,
                      calendar: set, start: str) -> list[str]:
    """追記する日付を選ぶ。

    **カレンダーに無い日は足さない。** CBOE は独自の営業日を持つため、
    そのまま入れると他銘柄に無い日付の行ができて T4 の母集団がズレる。
    """
    have = set(mine["date"])
    return sorted(d for d in set(cboe["date"])
                  if d >= start and d in calendar and d not in have)


def run(tickers: list[str], apply: bool, db_path: str | None = None) -> int:
    ensure_ca_bundle()

    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = resolve_db_path(config, db_path)
    parquet_dir = get_parquet_master_dir(db_path)
    print(f"[環境] Parquet : {os.path.abspath(parquet_dir)}")

    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        raise CboeBackfillError("latest_master.json を解決できません。")

    sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker"])
    prices = pd.read_parquet(cur["prices"])
    prices["date"] = prices["date"].astype(str)

    print("=" * 74)
    print(f"VIX 系指数の CBOE 充足  {tickers}  (apply={apply})")
    print("=" * 74)
    print(f"現行世代 prices {len(prices):,}行 / {os.path.basename(cur['prices'])}")

    cal_hit = sym[sym["ticker"] == CALENDAR_TICKER]
    if cal_hit.empty:
        raise CboeBackfillError(f"{CALENDAR_TICKER} が symbols にありません。")
    cal_id = int(cal_hit["id"].iloc[0])
    calendar = set(prices[prices["symbol_id"] == cal_id]["date"])
    cal_start = min(calendar) if calendar else "1900-01-01"
    print(f"営業日カレンダー({CALENDAR_TICKER}): {len(calendar):,}日 "
          f"{cal_start} 〜 {max(calendar)}")

    added_frames = []
    for ticker in tickers:
        name = TICKER_TO_CBOE.get(ticker)
        if not name:
            raise CboeBackfillError(
                f"{ticker} は CBOE の対応表にありません（{list(TICKER_TO_CBOE)}）。")
        hit = sym[sym["ticker"] == ticker]
        if hit.empty:
            print(f"\n[{ticker}] symbols に無いのでスキップ")
            continue
        sid = int(hit["id"].iloc[0])
        mine = prices[prices["symbol_id"] == sid][["date"] + [
            c for c in ("open", "high", "low", "close") if c in prices.columns]]

        cb = fetch_cboe(name)
        print(f"\n[{ticker}] symbol_id={sid}  手元 {len(mine):,}行 "
              f"{mine['date'].min()} 〜 {mine['date'].max()}")
        print(f"    CBOE {len(cb):,}行 {cb['date'].min()} 〜 {cb['date'].max()}")

        v = verify_overlap(mine, cb, ticker)
        print(f"    重複検算: {v['rows']:,}行  {MATCH_TOLERANCE:.1%}超のズレ "
              f"{v['off']}行  相対差 p50={v['p50']:.2e} 最大={v['max']:.2e} → OK")

        fill = select_fill_dates(mine, cb, calendar, cal_start)
        print(f"    追記する日: {len(fill)}日")
        if not fill:
            continue
        for d in fill[:20]:
            row = cb[cb["date"] == d].iloc[0]
            print(f"      {d}  O={row['open']:.2f} H={row['high']:.2f} "
                  f"L={row['low']:.2f} C={row['close']:.2f}")
        if len(fill) > 20:
            print(f"      …他 {len(fill) - 20}日")

        add = cb[cb["date"].isin(fill)].copy()
        add.insert(0, "symbol_id", sid)
        added_frames.append(add)

    if not added_frames:
        print("\n追記する行がありません。ポインタは更新しません。")
        return 0

    add_all = pd.concat(added_frames, ignore_index=True)
    if not apply:
        print(f"\n[DRY-RUN] {len(add_all):,}行を追記できます。書き込んでいません。")
        return 0

    # 追記専用。既存の (symbol_id, date) は必ず既存が勝つ
    keys = set(zip(prices["symbol_id"], prices["date"]))
    mask = [(s, d) not in keys for s, d in zip(add_all["symbol_id"], add_all["date"])]
    new_rows = add_all[mask].reindex(columns=prices.columns)

    merged = pd.concat([prices, new_rows], ignore_index=True)
    merged = merged.sort_values(["symbol_id", "date"]).reset_index(drop=True)
    gained = len(merged) - len(prices)
    print(f"\n[+] 追記後: {len(prices):,} → {len(merged):,} 行（+{gained:,}）")

    # 防護柵: 追記なので行が減ることはありえない
    if len(merged) < len(prices):
        raise CboeBackfillError(
            f"行数が減っている（{len(prices):,} → {len(merged):,}）。中断する。")

    files = write_master_generation(parquet_dir, pointer_file, cur,
                                    {"prices": merged}, label="cboe_vix_backfill")
    print(f"[+] 新世代を公開: {os.path.basename(files['prices'])}")

    print("\n" + "=" * 74)
    print("価格のみ追記した。**T3 以降は未反映（MTS は T5）。**")
    print("  .\\venv\\Scripts\\python.exe backend\\scripts\\update_pipeline.py"
          " --rebuild-from T3 --skip-fetch")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="VIX 系指数の欠損を CBOE の公式配信で埋める（追記専用）")
    p.add_argument("--tickers", default="^VIX3M,^VIX",
                   help="対象ティッカー（カンマ区切り。既定 ^VIX3M,^VIX）")
    p.add_argument("--db-path", default=None, help="SQLite を明示指定")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    g.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    sys.exit(run([t.strip() for t in a.tickers.split(",") if t.strip()],
                 a.apply, a.db_path))
