"""上流の欠損を前日値で埋めてしまった「捏造行」を削除する。

## いつ使うか

`data_collection/fetcher.py` の `fetch_daily_data` は取得直後に無条件で
`df.ffill()` する（upstream-data-diagnosis SKILL 限界I）。上流が欠損を返すと、
**前日値がそのまま「取引があったこと」として保存される**。

指紋は **OHLC が全部同値かつ出来高が 0**。実在しない値動きなので、
バックテストが「その期間まったく動かなかった」ものとして扱ってしまう。

## 実例（2026-08-25 / `AVB`）

```
2026-08-14  O=184.55 H=185.62 L=182.74 C=184.06  vol=2,484,400   ← 実データ
2026-08-17  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-18  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-19  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-20  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-21  O=65.90  H=65.90  L=65.90  C=65.90   vol=0           ← 捏造（別スケール）
```

> [!WARNING]
> **これは上流の異常そのものを直すものではない。** `AVB` は Yahoo の chart 系列が
> 実勢価格（$184）と桁の違う $65 帯を返しており、`2.793:1` という実在しない
> 分割記録まで付いている。捏造行を消しても**残った実データの段差は残る**。
> 上流の状態は `.claude/skills/upstream-data-diagnosis/SKILL.md` §2 の決定論テストで
> 確認し、推定値を書き込まないこと（SKILL §7）。

> [!IMPORTANT]
> **削除は SQLite にも反映しないと翌日のデイリー更新で戻る。**
> `daily_prices` は Parquet と SQLite の**マージ**（`drop_duplicates(keep='last')`）で
> 伝播し、**マージは行を消さない**（2026-08-06 に `JBIO` で実証済み）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\delete_symbol_rows.py \\
        --ticker AVB --auto-fabricated --from 2026-08-17 --to 2026-08-21 --dry-run
    ... --apply

    日付を明示する場合:
    ... --ticker AVB --dates 2026-08-17,2026-08-18 --apply
"""

import argparse
import os
import sys

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from scripts.scan_price_anomalies import use_utf8_stdout  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
)
from pipeline.parquet_maintenance import (  # noqa: E402
    connect_hot_cache,
    recompute_and_publish,
    resolve_db_path,
)
from pipeline.parquet_recompute import find_affected_virtual_themes  # noqa: E402

VIRTUAL_THEME_HASH_FILE = os.path.join("data", "virtual_theme_hashes.json")

# OHLC が同値とみなす相対誤差。ffill は完全一致でコピーするので厳しくてよい。
FLAT_TOLERANCE = 1e-9


def find_fabricated_rows(prices: pd.DataFrame, symbol_id: int,
                         date_from: str | None = None,
                         date_to: str | None = None) -> pd.DataFrame:
    """`ffill` で捏造された行を挙げる。

    指紋は **OHLC が全部同値 かつ 出来高が 0** の両方。

    > [!CAUTION]
    > **片方だけで消してはいけない。** 出来高0でも日中値があれば板が薄いだけの
    > 実在する日かもしれないし、値幅ゼロでも出来高があれば実際に約定している。

    Returns:
        `symbol_id` / `date` / OHLCV の DataFrame（削除候補）。
    """
    df = prices[prices["symbol_id"] == symbol_id].copy()
    if date_from:
        df = df[df["date"] >= date_from]
    if date_to:
        df = df[df["date"] <= date_to]
    if df.empty:
        return df

    hi = df[["open", "high", "low", "close"]].max(axis=1)
    lo = df[["open", "high", "low", "close"]].min(axis=1)
    flat = (hi - lo).abs() <= (hi.abs() * FLAT_TOLERANCE)
    no_volume = df["volume"].fillna(0) == 0
    return df[flat & no_volume].sort_values("date")


def drop_rows(prices: pd.DataFrame, symbol_id: int,
              dates: list[str]) -> pd.DataFrame:
    """指定銘柄の指定日の行を落とした新しい DataFrame を返す。"""
    if not dates:
        return prices
    mask = (prices["symbol_id"] == symbol_id) & (prices["date"].isin(dates))
    return prices[~mask].reset_index(drop=True)


def delete_sqlite_rows(db_path: str, symbol_id: int, dates: list[str],
                       expect_ticker: str, dry_run: bool) -> int:
    """ホットキャッシュからも同じ行を消す。

    **Parquet だけ消しても翌日のデイリー更新で戻る**（マージは行を消さない）。
    """
    from pipeline.parquet_maintenance import assert_ticker

    con = connect_hot_cache(db_path)
    try:
        assert_ticker(con, symbol_id, expect_ticker)
        if not dates:
            return 0
        marks = ",".join("?" * len(dates))
        # スコープの無い DELETE を撃たないよう、必ず symbol_id と date で絞る
        params = [symbol_id, *dates]
        n = con.execute(
            f"SELECT COUNT(*) FROM daily_prices WHERE symbol_id = ? AND date IN ({marks})",
            params).fetchone()[0]
        if not dry_run and n:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                f"DELETE FROM daily_prices WHERE symbol_id = ? AND date IN ({marks})",
                params)
            con.commit()
        return n
    finally:
        con.close()


def run(ticker: str, dates: list[str] | None, auto: bool, date_from: str | None,
        date_to: str | None, reason: str, dry_run: bool,
        db_path: str | None = None):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = resolve_db_path(config, db_path)
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"捏造行の削除  {ticker}  (dry-run={dry_run})")
    print(f"理由: {reason}")
    print("=" * 74)
    print(f"[環境] SQLite  : {os.path.abspath(db_path)}")
    print(f"[環境] Parquet : {os.path.abspath(parquet_dir)}")

    sym = pd.read_parquet(cur["symbols"])
    hit = sym[sym["ticker"] == ticker]
    if hit.empty:
        print(f"[ERROR] {ticker} が symbols にありません。")
        sys.exit(1)
    sid = int(hit["id"].iloc[0])
    spy_id = int(sym[sym["ticker"] == "SPY"]["id"].iloc[0])

    px = pd.read_parquet(cur["prices"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    if auto:
        cand = find_fabricated_rows(px, sid, date_from, date_to)
        dates = cand["date"].tolist()
        print(f"\n[1] 捏造行の自動検出（OHLC 全同値 かつ 出来高0）: {len(dates)}件")
        if not cand.empty:
            print(cand[["date", "open", "high", "low", "close", "volume"]]
                  .to_string(index=False))
    else:
        dates = dates or []
        print(f"\n[1] 指定された削除対象: {len(dates)}件  {dates}")

    if not dates:
        print("\n削除対象がありません。終了します。")
        # 呼び出し元（`reapply_corrections.py`）が「適用した」と誤って数えないよう、
        # 何もしなかったことを戻り値で伝える
        return "noop"

    target = px[px["symbol_id"] == sid].sort_values("date")
    print(f"\n[2] 対象: {ticker} (symbol_id={sid})")
    print(f"    現在: {len(target):,}行  {target['date'].min()} 〜 {target['date'].max()}")
    print(f"    削除: {len(dates)}行 → 残り {len(target) - len(dates):,}行")

    # 削除後の接合部を見せる（削除しても段差が残るなら別途の判断が要る）
    left = target[~target["date"].isin(dates)]
    prev = left[left["date"] < min(dates)]
    nxt = left[left["date"] > max(dates)]
    if not prev.empty and not nxt.empty:
        a, b = prev.iloc[-1], nxt.iloc[0]
        print(f"\n[3] 削除後の接合部")
        print(f"    {a['date']}  close={a['close']:>10.4f}")
        print(f"    {b['date']}  close={b['close']:>10.4f}   比率 {b['close']/a['close']:.4f}")
        if not (0.6 < b["close"] / a["close"] < 1.8):
            print("    [WARN] 削除しても段差が残ります。上流の異常が別にあります"
                  "（推定値で埋めないこと）。")

    theme_ids = find_affected_virtual_themes([sid], pd.read_parquet(cur["tc"]), sym)
    if theme_ids:
        print(f"\n[4] 再合成が必要な仮想テーマ: {len(theme_ids)}件")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    new_px = drop_rows(px, sid, dates)
    if theme_ids:
        from pipeline.parquet_recompute import rebuild_virtual_index_prices
        rebuilt = rebuild_virtual_index_prices(
            theme_ids, new_px, pd.read_parquet(cur["tc"]))
        keep = new_px[~new_px["symbol_id"].isin(theme_ids)]
        new_px = pd.concat([keep, rebuilt.reindex(columns=new_px.columns)],
                           ignore_index=True)
        new_px.sort_values(["symbol_id", "date"], inplace=True, ignore_index=True)
        print(f"    仮想テーマ {len(theme_ids)}件を再合成")

    recompute_and_publish(
        cur, parquet_dir, pointer_file, db_path, new_px, sym,
        recompute_ids=[sid] + theme_ids, spy_id=spy_id, label="delete_symbol_rows",
        theme_ids=theme_ids,
        hash_path=os.path.join(_project_root, VIRTUAL_THEME_HASH_FILE))

    # 価格の行は recompute_and_publish の差し替えで消えるが、
    # 明示的に DELETE も撃って「消えたこと」を確定させる
    n = delete_sqlite_rows(db_path, sid, dates, ticker, dry_run=False)
    print(f"\n[+] SQLite の残存確認: {n}行を削除")


if __name__ == "__main__":
    use_utf8_stdout()
    p = argparse.ArgumentParser(
        description="ffill で捏造された行（OHLC 全同値・出来高0）を削除する")
    p.add_argument("--ticker", required=True, help="対象ティッカー")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--auto-fabricated", action="store_true",
                   help="OHLC 全同値かつ出来高0 の行を自動検出する")
    g.add_argument("--dates", type=str, help="削除する日付（カンマ区切り）")
    p.add_argument("--from", dest="date_from", default=None,
                   help="自動検出の開始日（YYYY-MM-DD）")
    p.add_argument("--to", dest="date_to", default=None,
                   help="自動検出の終了日（YYYY-MM-DD）")
    p.add_argument("--reason", default="", help="削除の理由（記録用）")
    p.add_argument("--db-path", default=None,
                   help="書き込み先 SQLite を明示指定")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    dates = a.dates.split(",") if a.dates else None
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損）
    with pipeline_lock("delete_symbol_rows"):
        run(a.ticker, dates, a.auto_fabricated, a.date_from, a.date_to,
            a.reason, dry_run=not a.apply, db_path=a.db_path)
