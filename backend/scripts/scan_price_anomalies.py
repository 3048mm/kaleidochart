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
from data_collection.split_records import (  # noqa: E402
    load_split_records,
    resolve_default_path,
    split_map,
)
from indicators.price_anomaly import (  # noqa: E402
    MARKET_WIDE_MIN_SYMBOLS,
    classify_price_jump,
    count_real_symbols_per_day,
    find_matching_split,
    is_anomalous_ratio,
    is_below_liquidity_floor,
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


def classify(anomalies: pd.DataFrame, splits: dict | None = None) -> pd.DataFrame:
    """段差を分類する。

    Args:
        splits: `{ticker: [(split_date, factor)]}`。**省略すると分割メタデータを
            見ない従来の分類**になる（分割日と一致する段差が `market_wide` /
            `real_move` に化ける。`doc/issue_list.md` P1）。
    """
    out = anomalies.copy()
    out["dv_ratio"] = out["dv_ratio"].where(out["dv_ratio"].notna(), None)

    matches, classes = [], []
    for r in out.itertuples():
        m = find_matching_split(r.ticker, str(r.date)[:10], r.ratio, splits)
        matches.append(m)
        classes.append(classify_price_jump({
            "ticker": r.ticker, "ratio": r.ratio, "prev_close": r.prev_close,
            "adv21": None if pd.isna(r.adv21) else r.adv21,
            "dv_ratio": None if r.dv_ratio is None or pd.isna(r.dv_ratio) else r.dv_ratio,
            "dv_vs_adv": None if pd.isna(r.dv_vs_adv) else r.dv_vs_adv,
            "same_day_count": r.same_day_count,
            "split_match": m,
        }))

    out["classification"] = classes
    out["split_date"] = [m["split_date"] if m else "" for m in matches]
    out["split_factor"] = [m["factor"] if m else None for m in matches]
    out["split_days_off"] = [m["days_off"] if m else None for m in matches]
    # 分類は変えないが、要対応の読む順を決めるのに使う（§4-3）
    out["below_liquidity_floor"] = [
        is_below_liquidity_floor(r.prev_close, r.ratio,
                                 None if pd.isna(r.adv21) else r.adv21)
        for r in out.itertuples()
    ]
    return out


def _print_rows(sub: pd.DataFrame, limit: int) -> None:
    """要対応の明細。分割記録と一致したものは分割日・比率も出す。"""
    if sub.empty:
        return
    print(f"{'ticker':<8}{'date':<12}{'前終値':>10}{'終値':>10}{'比率':>8}"
          f"{'代金比':>9}  {'分割記録'}")
    for r in sub.head(limit).itertuples():
        dv = "-" if r.dv_ratio is None or pd.isna(r.dv_ratio) else f"{r.dv_ratio:.2f}"
        sp = ""
        if getattr(r, "split_date", "") :
            off = getattr(r, "split_days_off", 0) or 0
            sp = (f"{r.split_date} ×{r.split_factor:g}"
                  + (f"（{off:+d}日）" if off else ""))
        print(f"{r.ticker:<8}{r.date:<12}{r.prev_close:>10.2f}{r.close:>10.2f}"
              f"{r.ratio:>8.3f}{dv:>9}  {sp}")
    if len(sub) > limit:
        print(f"  ... 他 {len(sub)-limit} 件")


def report_split_coverage(df: pd.DataFrame, records: dict | None,
                          records_path: str) -> None:
    """分割記録のカバー範囲を出す。**沈黙しないための段。**

    記録が古い / 無い状態で従来分類を返すと、ゲートがあるように見えて
    何も見ていない状態になる。「見ていない件数」を必ず数字で出す。
    """
    print("--- 分割記録 ---")
    if not records:
        print(f"  **無し**（{records_path}）")
        print("  → 分割メタデータ**なし**で分類した。分割日と一致する段差が")
        print("     market_wide / real_move に化けている可能性がある。")
        print("     生成: backend/scripts/scan_split_consistency.py --years 2\n")
        return

    n_tickers = len(records.get("splits") or {})
    n_pairs = sum(len(v) for v in (records.get("splits") or {}).values())
    start = records.get("start") or ""
    print(f"  取得: {records.get('generated_at', '?')}"
          f" / 検査 {records.get('tickers_fetched', 0):,}銘柄")
    print(f"  記録: {n_tickers:,}銘柄 / {n_pairs:,}件")
    print(f"  カバー期間: {start} 以降")

    if start:
        outside = int((df["date"].astype(str) < start).sum())
        print(f"  **カバー期間外のアノマリー: {outside:,} 件**"
              f"（{len(df):,}件中）— この範囲は分割記録で照合していない")
    failed = records.get("failed") or []
    if failed:
        print(f"  取得できなかった銘柄: {len(failed):,} 件"
              f"（例: {', '.join(map(str, failed[:5]))}）")
    print()


def report(df: pd.DataFrame, out_dir: str, records: dict | None = None,
           records_path: str = "") -> None:
    total = len(df)
    counts = df["classification"].value_counts()

    print("=" * 74)
    print(f"価格アノマリーの分類  （全期間 / active 銘柄）")
    print("=" * 74)
    print(f"検出総数: {total:,} 件 / {df['ticker'].nunique():,} 銘柄")
    print(f"期間: {df['date'].min()} 〜 {df['date'].max()}\n")
    report_split_coverage(df, records, records_path)

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
        # 要対応は「実売買しうる水準」から先に読ませる。**低流動ぶんも隠さない**
        # （分類では握り潰さず、読む順だけを変える。§4-3）
        thin = sub["below_liquidity_floor"] if "below_liquidity_floor" in sub else False
        main, low = sub[~thin], sub[thin]
        head = f"\n=== {CLASS_LABEL[c]} — 全 {len(sub)} 件"
        print(head + (f"（うち低流動 {len(low)} 件）===" if len(low) else " ==="))
        _print_rows(main, limit=40)
        if len(low):
            print(f"  --- 低流動（$5 / $1M日 未満。破損ではあるが戦略は触らない）"
                  f" {len(low)} 件 ---")
            _print_rows(low, limit=20)

    os.makedirs(out_dir, exist_ok=True)
    cols = ["ticker", "date", "category", "prev_close", "close", "ratio",
            "dv_ratio", "dv_vs_adv", "adv21", "same_day_count", "classification",
            "split_date", "split_factor", "split_days_off",
            "below_liquidity_floor"]
    cols = [c for c in cols if c in df.columns]
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


def run(out_dir: str | None, splits_path: str | None = None):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]

    # 分割記録は「無ければ従来分類」。**ただし黙って通さない**（report が出す）。
    # 壊れていれば load_split_records が例外を投げる（握り潰さない）。
    records_path = splits_path or resolve_default_path(db_path)
    records = load_split_records(records_path)
    splits = split_map(records)
    parquet_dir = get_parquet_master_dir(db_path)
    cur = get_latest_master_files(get_pointer_file_path(parquet_dir))
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    # symbols も Parquet から読む → SQLite への接続はゼロ
    symbols = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])
    prices = pd.read_parquet(cur["prices"], columns=["symbol_id", "date", "close", "volume"])

    a = find_anomalies(prices, symbols)
    df = classify(a, splits)
    report(df, out_dir or os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                       "maintenance_reports"),
           records=records, records_path=records_path)


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(description="Parquet 全期間の価格アノマリーを分類する")
    p.add_argument("--out", type=str, default=None, help="レポートの出力先ディレクトリ")
    p.add_argument("--splits", type=str, default=None,
                   help="分割記録 JSON（既定は data/maintenance_reports/split_records.json）")
    a = p.parse_args()
    run(a.out, a.splits)
