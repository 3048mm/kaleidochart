"""Parquet の T5（market_signals）を全期間再計算して新世代を書き出す。

## なぜ必要か

T5 を全日付で再計算すると、SQLite の `daily_prices` にある SPY だけ
（ホット期間 約503本）で計算するため、`sma_200` の遡りが足りず、
MTS の SPY 由来列が誤った値で保存される。SPY は 2010-04-01 から Parquet に
あり正解の値は存在するのに、再計算のたびに誤った値で上書きされる。

2026-09-04 に T3/T4 は Parquet 基点に強制された（`ae77596`）が、同じ再構築手順の
最後の T5 だけが SQLite 基点のまま残っていた。`recompute_parquet_ranks.py`
（T4）・`recompute_parquet_indicators.py`（T3）と同じ考え方・同じ手順に揃える。

詳細: `doc/in_progress/t5_parquet_rebuild_plan.md`

## パス解決

Parquet ディレクトリの解決は `paths.resolve_db_path_for_init()` を経由する
（環境変数 `STOCKTOOL_DB_PATH` やワークツリーの `config.local.toml` を尊重する）。
`--apply` の書き込み前には `paths.ensure_writable()` を掛け、ワークツリーから
本番 Parquet へ書き込もうとした場合は `ProductionWriteError` で拒否する。

## 副作用（実行前に理解すること）

**現在の `active` フラグ・`個別` カテゴリで breadth を再計算する。** T4 と同じ慣習
（`recompute_parquet_ranks.py` の副作用の節を参照）。

## 使い方

    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_signals.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\recompute_parquet_signals.py --apply
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
import paths  # noqa: E402
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)
from indicators.market_signals import (  # noqa: E402
    calculate_market_signals,
    compute_breadth_momentum,
)

# `market_signals` の出力列（Parquet の既存スキーマと一致させること）
OUTPUT_COLUMNS = [
    "id", "date", "spy_above_sma200", "spy_sma200_rising", "distribution_days",
    "is_distribution_day", "follow_through_day", "market_phase",
    "market_trend_score", "vxv_vix_ratio", "breadth_sma50", "created_at",
]

# dry-run 差分の期間区分。計画書 §1.2(a) と同じ4区分。
PERIODS = [
    ("P1 2010-04-01〜2018-03-31", "2010-04-01", "2018-03-31"),
    ("P2 2018-04-01〜2024-09-02", "2018-04-01", "2024-09-02"),
    ("P3 2024-09-03〜2025-06-30", "2024-09-03", "2025-06-30"),
    ("P4 2025-07-01〜", "2025-07-01", "2099-12-31"),
]

NUMERIC_DIFF_COLS = [
    "spy_above_sma200", "spy_sma200_rising", "distribution_days",
    "is_distribution_day", "follow_through_day", "market_trend_score",
    "vxv_vix_ratio", "breadth_sma50",
]
STRING_DIFF_COLS = ["market_phase"]
DIFF_TOLERANCE = 1e-6


def build_market_signals_frame(
    spy_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    vxv_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    created_at: datetime,
) -> pd.DataFrame:
    """Parquet の入力から T5（market_signals）の全期間フレームを組み立てる。

    `calculate_market_signals()`（SQLite 経路の `t5_signals.py` と共通）で
    SPY 由来の列を計算し、`breadth_sma50` を日付で結合したうえで、Parquet の
    既存スキーマ（列名・dtype・`id`/`created_at` の作り方）に合わせる。

    Args:
        spy_df:     `date`/`close`/`high`/`low`/`volume` を持つ SPY の価格
        vix_df:     `date`/`close` を持つ ^VIX の価格（無ければ空 DataFrame）
        vxv_df:     `date`/`close` を持つ ^VIX3M の価格（無ければ空 DataFrame）
        metrics_df: `compute_breadth_momentum()` の戻り値
                    （`date`/`breadth_sma50`/`momentum_ratio`）
        created_at: この世代の全行に共通で入れるタイムスタンプ
                    （SQLite 経路の `bulk_save_objects` が1回のコミットで
                    ほぼ同一の `datetime.utcnow()` を刻む挙動に合わせ、単一値にする）

    Returns:
        `OUTPUT_COLUMNS` の列を持つ DataFrame。
    """
    ms = calculate_market_signals(spy_df, vix_df, vxv_df, metrics_df)
    if ms.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    breadth_cols = metrics_df[["date", "breadth_sma50"]] if metrics_df is not None and not metrics_df.empty \
        else pd.DataFrame(columns=["date", "breadth_sma50"])
    ms = ms.merge(breadth_cols, on="date", how="left")

    out = pd.DataFrame({
        "id": range(1, len(ms) + 1),
        "date": ms["date"].dt.strftime("%Y-%m-%d"),
        "spy_above_sma200": ms["spy_above_sma200"].astype("int64"),
        "spy_sma200_rising": pd.to_numeric(ms["spy_sma200_rising"], errors="coerce").astype("float64"),
        "distribution_days": ms["distribution_days"].astype("int64"),
        "is_distribution_day": ms["is_distribution_day"].astype("int64"),
        "follow_through_day": ms["follow_through_day"].astype("int64"),
        "market_phase": ms["market_phase"],
        "market_trend_score": ms["market_trend_score"].astype("float64"),
        "vxv_vix_ratio": ms["vxv_vix_ratio"].astype("float64"),
        "breadth_sma50": ms["breadth_sma50"].astype("float64"),
        "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
    })
    return out[OUTPUT_COLUMNS]


def compare_by_period(old_df: pd.DataFrame, new_df: pd.DataFrame,
                       periods=PERIODS, tolerance: float = DIFF_TOLERANCE) -> dict:
    """現行世代と再計算結果を期間別・列別に比較する（計画書 §1.2(a) と同じ形式）。

    数値列は |差|>tolerance を不一致として扱う。整数列は Parquet 世代間で
    float/int が混在しうるため `pd.to_numeric` で数値化してから比較する
    （型差だけで全不一致に見える罠を避ける。計画書 §1.2 の注記）。
    `market_phase` は文字列比較。

    Returns:
        期間ラベル -> {"days": int, "columns": {列名 -> {"mismatch_days", "max_abs_diff"}}}
    """
    old = old_df.copy()
    new = new_df.copy()
    old["date"] = pd.to_datetime(old["date"])
    new["date"] = pd.to_datetime(new["date"])
    merged = old.merge(new, on="date", how="inner", suffixes=("_old", "_new"))

    result = {}
    for label, start, end in periods:
        mask = (merged["date"] >= start) & (merged["date"] <= end)
        sub = merged[mask]
        columns = {}
        for col in STRING_DIFF_COLS:
            a = sub[f"{col}_old"].astype(str)
            b = sub[f"{col}_new"].astype(str)
            n = int((a != b).sum())
            columns[col] = {"mismatch_days": n, "max_abs_diff": None}
        for col in NUMERIC_DIFF_COLS:
            a = pd.to_numeric(sub[f"{col}_old"], errors="coerce")
            b = pd.to_numeric(sub[f"{col}_new"], errors="coerce")
            both_na = a.isna() & b.isna()
            diff = (a - b).abs()
            bad = ~both_na & ((diff > tolerance) | (a.isna() != b.isna()))
            mx = float(diff[bad].max()) if bad.any() else 0.0
            columns[col] = {"mismatch_days": int(bad.sum()), "max_abs_diff": mx}
        result[label] = {"days": int(mask.sum()), "columns": columns}
    return result


def _print_diff(diff: dict) -> None:
    print("\n[3] 期間別・列別の差分（現行世代 vs 再計算）")
    cols = STRING_DIFF_COLS + NUMERIC_DIFF_COLS
    header = f"{'列':<22}" + "".join(f"{label[:2]:>18}" for label, *_ in PERIODS)
    print(header + "   ※ 不一致日数（数値列は |差|>1e-6、括弧内は最大|差|）")
    for col in cols:
        cells = []
        for label, *_ in PERIODS:
            c = diff[label]["columns"][col]
            if c["max_abs_diff"] is None:
                cells.append(f"{c['mismatch_days']:>18}")
            else:
                cells.append(f"{c['mismatch_days']:>6} ({c['max_abs_diff']:>7.3g})")
        print(f"{col:<22}" + "".join(cells))
    print("期間の日数:", {label[:2]: diff[label]["days"] for label, *_ in PERIODS})


def _load_prices(prices_path: str, symbol_id: int | None, cols: list[str]) -> pd.DataFrame:
    if symbol_id is None:
        return pd.DataFrame(columns=["date"] + cols)
    d = pd.read_parquet(
        prices_path, columns=["symbol_id", "date"] + cols,
        filters=[("symbol_id", "==", symbol_id)],
    )
    d["date"] = pd.to_datetime(d["date"])
    return d.drop(columns=["symbol_id"]).sort_values("date").reset_index(drop=True)


def run(dry_run: bool):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = paths.resolve_db_path_for_init("stocktool", config["system"]["db_path"])
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"Parquet T5 全期間再計算  (dry-run={dry_run})")
    print("=" * 74)
    print(f"現行世代: {os.path.basename(cur['signals'])}")

    t0 = time.time()
    sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker", "category", "active"])

    def _symbol_id(ticker: str):
        rows = sym.loc[sym["ticker"] == ticker, "id"]
        return int(rows.iloc[0]) if not rows.empty else None

    spy_id = _symbol_id("SPY")
    if spy_id is None:
        print("[ERROR] SPY の symbol_id が見つかりません。")
        sys.exit(1)
    vix_id = _symbol_id("^VIX")
    vxv_id = _symbol_id("^VIX3M")

    spy_df = _load_prices(cur["prices"], spy_id, ["close", "high", "low", "volume"])
    vix_df = _load_prices(cur["prices"], vix_id, ["close"])
    vxv_df = _load_prices(cur["prices"], vxv_id, ["close"])

    # breadth の母集団: active=1 かつ 個別（t5_signals.py の SQL と同じ条件）
    active_ids = sym.loc[(sym["active"] == 1) & (sym["category"] == "個別"), "id"].tolist()
    if active_ids:
        px = pd.read_parquet(
            cur["prices"], columns=["symbol_id", "date", "close"],
            filters=[("symbol_id", "in", active_ids)],
        )
        ind = pd.read_parquet(
            cur["indicators"], columns=["symbol_id", "date", "sma_50"],
            filters=[("symbol_id", "in", active_ids)],
        )
        px["date"] = pd.to_datetime(px["date"])
        ind["date"] = pd.to_datetime(ind["date"])
        raw = px.merge(ind, on=["symbol_id", "date"], how="inner")
    else:
        raw = pd.DataFrame(columns=["symbol_id", "date", "close", "sma_50"])
    metrics_df = compute_breadth_momentum(raw)
    print(f"\n[1] 読み込み {time.time() - t0:.1f}s  "
          f"SPY {len(spy_df):,}行 / breadth 対象 {len(active_ids):,}銘柄")

    t1 = time.time()
    created_at = datetime.utcnow()
    new_ms = build_market_signals_frame(spy_df, vix_df, vxv_df, metrics_df, created_at)
    print(f"[2] 再計算 {time.time() - t1:.0f}s  → {len(new_ms):,}行")

    old_ms = pd.read_parquet(cur["signals"])
    diff = compare_by_period(old_ms, new_ms)
    _print_diff(diff)

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    # ワークツリーから本番 Parquet へ書こうとしていないか（FS では防げないのでここで検査）
    paths.ensure_writable(parquet_dir)

    # --- 新世代の書き出し ---
    print("\n[4] 新世代の書き出し...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    # signals 以外は同世代へコピーする。pointer が複数世代を混ぜて参照すると
    # prune で参照中のファイルが消える。
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "signals":
            new_ms.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("recompute")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Parquet の T5 を全期間再計算する")
    p.add_argument("--dry-run", action="store_true", help="差分だけ表示して書き込まない")
    p.add_argument("--apply", action="store_true", help="実際に新世代を書き出す")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 日次更新・週次メンテとの同時実行を防ぐ
    with pipeline_lock("recompute_parquet_signals"):
        run(dry_run=not a.apply)
