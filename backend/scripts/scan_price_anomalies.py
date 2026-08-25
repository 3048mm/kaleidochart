"""Parquet 全期間の価格アノマリーを分類して報告する。

## なぜ Parquet を直接読むか

週次メンテの監査は SQLite（直近730日）しか見ておらず、**アノマリーの72%が射程外**
だった（実測: 全1,081件のうち775件）。バックテストが読むのは Parquet の全期間なので、
実態を把握できていなかった。

**SQLite には一切接続しない。** symbols も Parquet から読むため、
API サーバー・パイプラインとのロック競合は原理的に起きない
（バックテストが Parquet を直読みするのと同じ構造）。

実測コスト（2026-08-05）: 読み込み 0.6秒 / 全処理 4.7秒 / ピークメモリ 約2GB。
**パイプラインとの同時実行は避ける**（メモリのため）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_price_anomalies.py
    .\\venv\\Scripts\\python.exe backend\\scripts\\scan_price_anomalies.py --out data/maintenance_reports
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from indicators.price_anomaly import (  # noqa: E402
    MARKET_WIDE_MIN_SYMBOLS,
    classify_price_jump,
    count_real_symbols_per_day,
    is_anomalous_ratio,
)
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)

# 報告の並び順。上から「対応が要る順」。
CLASS_ORDER = ["split_suspect", "undecided", "real_move",
               "low_liquidity", "market_wide", "virtual"]

CLASS_LABEL = {
    "split_suspect": "未調整の分割の疑い（要対応）",
    "undecided": "判定不能（出来高0等）",
    "real_move": "実際の値動き（対応不要）",
    "low_liquidity": "低位株・薄商い（戦略が触らない）",
    "market_wide": "市場全体の急落日",
    "virtual": "仮想テーマ指数の合成値",
}


def use_utf8_stdout() -> None:
    """コンソール出力を UTF-8 にする。**`__main__` からだけ呼ぶこと。**

    Windows のコンソールは既定が cp932 で、`—`(U+2014) のような文字を出せない。
    日本語レポートを PowerShell から直接実行すると途中で落ちる:

        UnicodeEncodeError: 'cp932' codec can't encode character '\\u2014'

    `run/*.bat` は `PYTHONIOENCODING=utf-8` を立てているが、手で実行する経路には無い。

    > [!CAUTION]
    > **モジュールの import 時に実行してはいけない。** リポジトリ内の既存スクリプト
    > （`daily_sync_job.py` / `theme_report.py`）は
    > `sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())` を先頭で呼ぶが、
    > この形はテストがモジュールを import した瞬間に **pytest の stdout を
    > detach してしまう**:
    >
    >     ValueError: underlying buffer has been detached
    >
    > `reconfigure()` は既存のラッパを壊さないので、こちらを使う。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")      # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass      # reconfigure できない相手（pytest の capture 等）には何もしない


def find_anomalies(prices: pd.DataFrame, symbols: pd.DataFrame) -> pd.DataFrame:
    """全期間の段差を抽出し、分類に必要な特徴量を付ける。"""
    px = prices[prices["close"].notna() & (prices["close"] > 0)]
    px = px.sort_values(["symbol_id", "date"]).copy()

    g = px.groupby("symbol_id", sort=False)
    px["prev_close"] = g["close"].shift(1)
    px["prev_volume"] = g["volume"].shift(1)
    px["dv"] = px["close"] * px["volume"]
    # ジャンプ当日を含めない21日平均売買代金
    px["adv21"] = g["dv"].transform(lambda s: s.shift(1).rolling(21, min_periods=5).mean())

    d = px.dropna(subset=["prev_close"])
    d = d[d["prev_close"] > 0].copy()
    d["ratio"] = d["close"] / d["prev_close"]
    a = d[d["ratio"].map(is_anomalous_ratio)].copy()

    prev_dv = a["prev_close"] * a["prev_volume"]
    a["dv_ratio"] = np.where(prev_dv > 0, a["dv"] / prev_dv, np.nan)
    # 前日が取引停止だと代金比が取れない。直前21日平均との比で代替する
    a["dv_vs_adv"] = np.where(a["adv21"] > 0, a["dv"] / a["adv21"], np.nan)
    # ticker を先に付ける。**同日件数の算出には ticker が要る**（下のコメント参照）
    a = a.merge(symbols[["id", "ticker", "category", "active"]],
                left_on="symbol_id", right_on="id", how="left")
    a = a[a["active"] == 1].copy()

    # 仮想テーマは数えない（犯人が自分の作った波及に隠れる）。
    # 母集団は active のみ — `weekly_maintenance.py` 側の SQL が
    # `Symbol.active == 1` で絞っているので、そちらと数を揃える。
    a["same_day_count"] = count_real_symbols_per_day(a)
    return a


def classify(anomalies: pd.DataFrame) -> pd.DataFrame:
    out = anomalies.copy()
    out["dv_ratio"] = out["dv_ratio"].where(out["dv_ratio"].notna(), None)
    out["classification"] = [
        classify_price_jump({
            "ticker": r.ticker, "ratio": r.ratio, "prev_close": r.prev_close,
            "adv21": None if pd.isna(r.adv21) else r.adv21,
            "dv_ratio": None if r.dv_ratio is None or pd.isna(r.dv_ratio) else r.dv_ratio,
            "dv_vs_adv": None if pd.isna(r.dv_vs_adv) else r.dv_vs_adv,
            "same_day_count": r.same_day_count,
        })
        for r in out.itertuples()
    ]
    return out


def report(df: pd.DataFrame, out_dir: str) -> None:
    total = len(df)
    counts = df["classification"].value_counts()

    print("=" * 74)
    print(f"価格アノマリーの分類  （全期間 / active 銘柄）")
    print("=" * 74)
    print(f"検出総数: {total:,} 件 / {df['ticker'].nunique():,} 銘柄")
    print(f"期間: {df['date'].min()} 〜 {df['date'].max()}\n")

    print(f"{'分類':<28}{'件数':>7}{'銘柄':>7}  比率")
    print("-" * 60)
    for c in CLASS_ORDER:
        sub = df[df["classification"] == c]
        if sub.empty and c not in counts:
            continue
        print(f"{CLASS_LABEL[c]:<28}{len(sub):>7}{sub['ticker'].nunique():>7}"
              f"  {len(sub)/max(total,1)*100:>5.1f}%")

    for c in ("split_suspect", "undecided"):
        sub = df[df["classification"] == c].sort_values("date", ascending=False)
        if sub.empty:
            continue
        print(f"\n=== {CLASS_LABEL[c]} — 全 {len(sub)} 件 ===")
        print(f"{'ticker':<8}{'date':<12}{'前終値':>10}{'終値':>10}{'比率':>8}{'代金比':>9}")
        for r in sub.head(40).itertuples():
            dv = "-" if r.dv_ratio is None or pd.isna(r.dv_ratio) else f"{r.dv_ratio:.2f}"
            print(f"{r.ticker:<8}{r.date:<12}{r.prev_close:>10.2f}{r.close:>10.2f}"
                  f"{r.ratio:>8.3f}{dv:>9}")
        if len(sub) > 40:
            print(f"  ... 他 {len(sub)-40} 件")

    os.makedirs(out_dir, exist_ok=True)
    cols = ["ticker", "date", "category", "prev_close", "close", "ratio",
            "dv_ratio", "dv_vs_adv", "adv21", "same_day_count", "classification"]
    csv_path = os.path.join(out_dir, "price_anomalies.csv")
    df.sort_values(["classification", "date"])[cols].to_csv(
        csv_path, index=False, encoding="utf-8", lineterminator="\n")
    print(f"\n→ {csv_path}")

    # 前回との差分（再構築や補正でアノマリー集合は変わる）
    snap_path = os.path.join(out_dir, "price_anomalies_snapshot.json")
    now = {f"{r.ticker}|{r.date}": r.classification for r in df.itertuples()}
    if os.path.exists(snap_path):
        prev = json.load(open(snap_path, encoding="utf-8"))
        added = set(now) - set(prev)
        removed = set(prev) - set(now)
        changed = {k for k in set(now) & set(prev) if now[k] != prev[k]}
        print(f"\n=== 前回との差分 ===")
        print(f"  新規 {len(added)} / 消滅 {len(removed)} / 分類変更 {len(changed)}")
        for k in sorted(added)[:10]:
            print(f"    + {k}  {now[k]}")
        for k in sorted(removed)[:10]:
            print(f"    - {k}  {prev[k]}")
    else:
        print("\n（前回のスナップショットが無いため差分なし。次回から比較します）")
    json.dump(now, open(snap_path, "w", encoding="utf-8"), ensure_ascii=False)


def run(out_dir: str | None):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]
    parquet_dir = get_parquet_master_dir(db_path)
    cur = get_latest_master_files(get_pointer_file_path(parquet_dir))
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    # symbols も Parquet から読む → SQLite への接続はゼロ
    symbols = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    prices = pd.read_parquet(cur["prices"], columns=["symbol_id", "date", "close", "volume"])

    a = find_anomalies(prices, symbols)
    df = classify(a)
    report(df, out_dir or os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                       "maintenance_reports"))


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(description="Parquet 全期間の価格アノマリーを分類する")
    p.add_argument("--out", type=str, default=None, help="レポートの出力先ディレクトリ")
    a = p.parse_args()
    run(a.out)
