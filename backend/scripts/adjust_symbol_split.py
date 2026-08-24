"""上流が調整しなかった株式分割・併合を、手元で遡及補正する。

## いつ使うか

`auto_adjust=True` は Yahoo 自身の分割記録に依存する。記録が無い（限界1）か、
**記録はあるのに適用が歯抜け**（2026-08-25 に `BYND` で確認した新種）だと、
分割日に生の価格ジャンプが残る。**何度取り直しても直らない。**

差分取得の継ぎ接ぎ（限界3）なら全期間取り直しで解消するので、**まずそちらを疑うこと**。
本スクリプトが要るのは「上流の系列自体が壊れている」と確認できた場合だけ。
切り分けは `.claude/skills/upstream-data-diagnosis/SKILL.md` §2 の決定論テストで行う。

## 実例（2026-08-25）

`BYND`（Beyond Meat）が 2026-08-14 09:30 ET 付で 1:30 併合。

```
2026-08-12   close  0.4141   volume 108,607,358    ← 併合前スケール
2026-08-13   close 12.4650   volume   2,574,106    ← 併合後スケール（段差 ×30.10）
```

Yahoo の全期間 1,838 行のうち **7営業日だけが ×30 調整済み**で残りは生値。
`query1`/`query2` × 3周で完全に決定論的（AAPL は毎回正常）＝供給側の欠陥。
**この状態で全期間取り直すと歯抜けの ×30 が混じってさらに悪化する。**

## やること

  1. 接合部を検算（比率が `--factor` と一致しなければ**中断**）
  2. `--before` より前の OHLC を `×factor`、volume を `÷factor`（market_cap は触らない）
  3. 接合日の market_cap スパイクを前後から補間
  4. 対象銘柄の T3 を全期間再計算
  5. 所属する**仮想テーマ**を全期間再合成し、そのテーマの T3 も再計算
  6. T4 を全期間再計算（横断的なので母集団が変わった日付は全銘柄が影響を受ける）
  7. Parquet の新世代を書き出してポインタを更新
  8. **SQLite ホットキャッシュにも同じ補正を反映**
  9. `virtual_theme_hashes.json` から対象テーマを落として次回 T2 に再合成させる

> [!IMPORTANT]
> **8 を省くと翌日のデイリー更新で補正が元に戻る。**
> `daily_prices` などは Parquet と SQLite の**マージ**（`drop_duplicates(keep='last')`・
> SQL 側優先）で伝播し、**マージは行を書き換えない**。直近730日に併合前スケールの行が
> 残っていればそのまま Parquet に復活する（2026-08-06 に `JBIO` で実際に発生）。

> [!IMPORTANT]
> **9 を省くと差分モードのまま過去が再計算されない。** 仮想テーマの再合成判定は
> **構成銘柄集合のハッシュ**で決まるため、構成銘柄の「価格」が変わってもハッシュは
> 変わらない（`parquet_recompute.find_affected_virtual_themes` の docstring 参照）。

> [!CAUTION]
> **補正は冪等ではない。** 2回かければ ×900 になる。唯一の歯止めが接合部の検算なので、
> `--force-seam` のような迂回路は**足さないこと**。

> [!WARNING]
> **全期間再構築（`run/tool/refresh_All.bat`）を実行したら、補正は再適用が必要。**
> 再構築は Yahoo から取り直すため、上流に残っている段差がそのまま戻ってくる。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\adjust_symbol_split.py \\
        --ticker BYND --before 2026-08-13 --factor 30 \\
        --reason "1:30 併合 (2026-08-14 ET)" --dry-run
    ... --apply
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from pipeline.pipeline_lock import pipeline_lock  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)
from pipeline.parquet_recompute import (  # noqa: E402
    find_affected_virtual_themes,
    rebuild_virtual_index_prices,
    recompute_indicators,
    recompute_ranks,
)

# マージで SQLite → Parquet に戻る階層。**ここを漏らすと補正が翌日で無効化される。**
# `daily_prices` はスケール、残り2つは再計算値で差し替える（指標は ×factor しても
# 意味が通らない — 比率・偏差・フラグが混在しているため）。
MERGED_TABLES = ("daily_prices", "indicators", "relative_ranks")

# 価格として扱う列。volume だけ逆方向にスケールする。
_PRICE_COLUMNS = ("open", "high", "low", "close")

# 接合部の比率が factor からどれだけ外れてよいか。実データは当日の値動きが乗るので
# ぴったりにはならない（`BYND` は 30.10 / factor 30 = +0.3%）。
DEFAULT_SEAM_TOLERANCE = 0.05

# 仮想テーマの再合成判定に使うハッシュの保存先（プロジェクトルートからの相対）
VIRTUAL_THEME_HASH_FILE = os.path.join("data", "virtual_theme_hashes.json")


def resolve_db_path(config: dict, explicit: str | None = None) -> str:
    """書き込み先の SQLite を決める。**環境変数を無視しないこと。**

    環境変数の解釈は `db.database.init_db()` の中にしか無いため、それを import
    しないスクリプト（`truncate_symbol_history.py` など）は
    **`STOCKTOOL_DB_PATH` を設定しても本番 DB を掴む**。Sandbox で検証したつもりが
    本番を書き換える事故になるので、ここで明示的に解決する。

    優先順位は `init_db()` と揃えること（ズレると API が見ている DB と
    スクリプトが書く DB が食い違う）:

        --db-path > STOCKTOOL_ENV=sandbox|test > STOCKTOOL_DB_PATH > config.toml
    """
    if explicit:
        return explicit
    env_name = os.getenv("STOCKTOOL_ENV")
    if env_name == "sandbox":
        return "data/sandbox/stocktool.db"
    if env_name == "test":
        return "data/test/stocktool.db"
    return os.getenv("STOCKTOOL_DB_PATH") or config["system"]["db_path"]


class SeamCheckError(RuntimeError):
    """接合部の比率が申告された `factor` と一致しない。

    **二重適用と比率間違いに対する唯一の歯止め。** 迂回できるようにしないこと。
    """


def verify_seam(prices: pd.DataFrame, symbol_id: int, before: str, factor: float,
                tolerance: float = DEFAULT_SEAM_TOLERANCE) -> dict:
    """`before` の直前と直後の終値の比が `factor` と一致するかを検算する。

    Raises:
        SeamCheckError: 境界にデータが無い、または比率が `factor` から
            `tolerance`（相対）を超えて外れている場合。
    """
    px = prices[prices["symbol_id"] == symbol_id].sort_values("date")
    pre = px[px["date"] < before]
    post = px[px["date"] >= before]
    if pre.empty or post.empty:
        raise SeamCheckError(
            f"symbol_id={symbol_id} は {before} の前後どちらかにデータがありません"
            f"（前 {len(pre)}行 / 後 {len(post)}行）。--before を確認してください。"
        )

    last_pre = pre.iloc[-1]
    first_post = post.iloc[0]
    if not last_pre["close"] or last_pre["close"] <= 0:
        raise SeamCheckError(f"接合直前の終値が不正です: {last_pre['close']!r}")

    ratio = float(first_post["close"]) / float(last_pre["close"])
    if abs(ratio - factor) > factor * tolerance:
        raise SeamCheckError(
            f"接合部の比率が申告値と一致しません。\n"
            f"    {last_pre['date']}  close={last_pre['close']:.6g}\n"
            f"    {first_post['date']}  close={first_post['close']:.6g}\n"
            f"    実測比率 {ratio:.4f} / 申告 factor {factor} "
            f"(許容 ±{tolerance:.1%})\n"
            f"  補正済みの系列に再適用しようとしていないか、"
            f"--factor / --before が正しいかを確認してください。"
        )
    return {"ok": True, "ratio": ratio,
            "last_pre_date": last_pre["date"], "last_pre_close": float(last_pre["close"]),
            "first_post_date": first_post["date"],
            "first_post_close": float(first_post["close"])}


def backadjust_prices(prices: pd.DataFrame, symbol_id: int, before: str,
                      factor: float) -> pd.DataFrame:
    """`before` より前の価格を `×factor`、volume を `÷factor` した新しい DataFrame を返す。

    `market_cap` は**触らない**。併合は株数÷factor・株価×factor なので時価総額は不変で、
    実データでも併合前の market_cap は正しい値が入っている。
    """
    out = prices.copy()
    mask = (out["symbol_id"] == symbol_id) & (out["date"] < before)
    for col in _PRICE_COLUMNS:
        if col in out.columns:
            out.loc[mask, col] = out.loc[mask, col] * factor
    if "volume" in out.columns:
        out.loc[mask, "volume"] = out.loc[mask, "volume"] / factor
    return out


def interpolate_market_cap(prices: pd.DataFrame, symbol_id: int,
                           date: str) -> pd.DataFrame:
    """接合日の `market_cap` スパイクを前後の平均で埋める。

    株数の更新が価格の分割適用に1日遅れると、その日だけ時価総額が `factor` 倍になる
    （`BYND` は 2026-08-13 に 6.43e9。前後は 2.1e8〜2.3e8）。mcap フィルタを使う
    スクリーナー・バックテストが接合日だけ別の母集団にならないように埋める。

    前後どちらかが欠けている場合は**何もしない**（推定値を作らない）。
    """
    out = prices.copy()
    if "market_cap" not in out.columns:
        return out

    px = out[out["symbol_id"] == symbol_id].sort_values("date")
    prev = px[px["date"] < date]
    nxt = px[px["date"] > date]
    if prev.empty or nxt.empty:
        return out

    lo = prev.iloc[-1]["market_cap"]
    hi = nxt.iloc[0]["market_cap"]
    if pd.isna(lo) or pd.isna(hi):
        return out

    out.loc[(out["symbol_id"] == symbol_id) & (out["date"] == date),
            "market_cap"] = (float(lo) + float(hi)) / 2.0
    return out


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _assert_ticker(con: sqlite3.Connection, symbol_id: int, expect_ticker: str | None):
    """`symbol_id` が期待どおりのティッカーを指していることを確認する。

    **全期間再構築は `symbols.id` を再採番する**（2026-08-06 に 2,378件が変化）。
    Parquet で解決した id を照合せずに SQLite へ流すと**別銘柄を壊す**。
    """
    if not expect_ticker:
        return
    row = con.execute("SELECT ticker FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
    actual = row[0] if row else None
    if actual != expect_ticker:
        raise ValueError(
            f"symbol_id={symbol_id} は SQLite では {actual!r} を指しており "
            f"{expect_ticker!r} と一致しません。Parquet と SQLite で id 体系が"
            f"ずれている可能性があります（全期間再構築の直後など）。"
        )


def scale_sqlite_history(db_path: str, symbol_id: int, before: str, factor: float,
                         dry_run: bool, expect_ticker: str | None = None
                         ) -> dict[str, int | None]:
    """ホットキャッシュ（SQLite）の `daily_prices` にも同じスケーリングをかける。

    **Parquet だけ直しても翌日のデイリー更新で戻る**（マージが SQL 側を優先するため）。

    Returns:
        `{"daily_prices": 対象行数}`。テーブルが無ければ値は ``None``。
    """
    con = _connect(db_path)
    try:
        _assert_ticker(con, symbol_id, expect_ticker)

        # スコープの無い UPDATE を撃たないよう、必ず symbol_id と date で絞る
        where = "FROM daily_prices WHERE symbol_id = ? AND date < ?"
        try:
            n = con.execute(f"SELECT COUNT(*) {where}", (symbol_id, before)).fetchone()[0]
        except sqlite3.OperationalError:
            return {"daily_prices": None}

        if not dry_run and n:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE daily_prices SET open = open * ?, high = high * ?,"
                " low = low * ?, close = close * ?, volume = volume / ?"
                " WHERE symbol_id = ? AND date < ?",
                (factor, factor, factor, factor, factor, symbol_id, before),
            )
            con.commit()
        return {"daily_prices": n}
    finally:
        con.close()


def replace_sqlite_rows(db_path: str, table: str, symbol_ids: list[int],
                        rows: pd.DataFrame, dry_run: bool) -> int | None:
    """`indicators` / `relative_ranks` を再計算値で**差し替える**（スケールしない）。

    指標は比率・偏差・フラグが混在しており、`×factor` して意味が通るのは価格系の
    一部だけ。価格を補正したら再計算した値で丸ごと置き換えるのが唯一正しい。

    SQLite に無い列は黙って捨てる（指標列は増え続けているため）。
    """
    con = _connect(db_path)
    try:
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
        except sqlite3.OperationalError:
            return None
        if not cols:
            return None
        if dry_run:
            return len(rows)

        use = [c for c in rows.columns if c in cols and c != "id"]
        payload = rows[use].where(pd.notna(rows[use]), None)

        con.execute("BEGIN IMMEDIATE")
        for i in range(0, len(symbol_ids), 900):
            chunk = symbol_ids[i:i + 900]
            marks = ",".join("?" * len(chunk))
            con.execute(f"DELETE FROM {table} WHERE symbol_id IN ({marks})", chunk)
        if len(payload):
            marks = ",".join("?" * len(use))
            con.executemany(
                f"INSERT INTO {table} ({','.join(use)}) VALUES ({marks})",
                list(payload.itertuples(index=False, name=None)),
            )
        con.commit()
        return len(payload)
    finally:
        con.close()


def drop_virtual_theme_hashes(hash_path: str, theme_ids: list[int], dry_run: bool) -> int:
    """再合成させたいテーマのハッシュを落とす。

    再合成判定は**構成銘柄集合のハッシュ**なので、構成銘柄の価格が変わっても
    ハッシュは変わらず差分モードのままになる。落としておくと次回 T2 が全期間を作り直す。
    """
    if not os.path.exists(hash_path):
        return 0
    with open(hash_path, "r", encoding="utf-8") as f:
        saved = json.load(f)
    hit = [str(t) for t in theme_ids if str(t) in saved]
    if not dry_run and hit:
        for k in hit:
            saved.pop(k, None)
        with open(hash_path, "w", encoding="utf-8") as f:
            json.dump(saved, f, ensure_ascii=False, indent=2)
    return len(hit)


def run(ticker: str, before: str, factor: float, reason: str, dry_run: bool,
        tolerance: float = DEFAULT_SEAM_TOLERANCE, db_path: str | None = None):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = resolve_db_path(config, db_path)
    # Parquet ディレクトリは DB パスから解決されるので、ここが本番/Sandbox の分岐点
    parquet_dir = get_parquet_master_dir(db_path)
    print(f"[環境] SQLite  : {os.path.abspath(db_path)}")
    print(f"[環境] Parquet : {os.path.abspath(parquet_dir)}")
    pointer_file = get_pointer_file_path(parquet_dir)
    cur = get_latest_master_files(pointer_file)
    if not cur:
        print("[ERROR] latest_master.json を解決できません。")
        sys.exit(1)

    print("=" * 74)
    print(f"分割・併合の遡及補正  {ticker}  {before} より前を ×{factor}  (dry-run={dry_run})")
    print(f"理由: {reason}")
    print("=" * 74)

    sym = pd.read_parquet(cur["symbols"])
    hit = sym[sym["ticker"] == ticker]
    if hit.empty:
        print(f"[ERROR] {ticker} が symbols にありません。")
        sys.exit(1)
    sid = int(hit["id"].iloc[0])
    spy_id = int(sym[sym["ticker"] == "SPY"]["id"].iloc[0])

    px = pd.read_parquet(cur["prices"])
    px["date"] = pd.to_datetime(px["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    # --- [1] 接合部の検算（唯一の歯止め。失敗したら何も書かずに落ちる） ---
    seam = verify_seam(px, sid, before, factor, tolerance)
    target = px[px["symbol_id"] == sid]
    n_pre = int((target["date"] < before).sum())
    print(f"\n[1] 対象: {ticker} (symbol_id={sid})")
    print(f"    現在: {len(target):,}行  {target['date'].min()} 〜 {target['date'].max()}")
    print(f"    補正: {n_pre:,}行  （{before} より前）")
    print(f"\n[2] 接合部の検算")
    print(f"    {seam['last_pre_date']}  {seam['last_pre_close']:>12.6g}   ← 補正する最後の行")
    print(f"    {seam['first_post_date']}  {seam['first_post_close']:>12.6g}   ← 補正しない最初の行")
    print(f"    実測比率 {seam['ratio']:.4f}  /  申告 factor {factor}  → OK")

    tc = pd.read_parquet(cur["tc"])
    theme_ids = find_affected_virtual_themes([sid], tc, sym)
    if theme_ids:
        names = sym[sym["id"].isin(theme_ids)]
        print(f"\n[3] 再合成が必要な仮想テーマ {len(theme_ids)}件")
        for _, t in names.iterrows():
            print(f"    {t['id']:>5}  {t['ticker']:<12} {t.get('name', '')}")

    if dry_run:
        print("\n[DRY-RUN] 書き込んでいません。")
        return

    # --- [4] 価格の補正 ---
    print("\n[4] 価格の補正と再計算...")
    new_px = backadjust_prices(px, sid, before, factor)
    new_px = interpolate_market_cap(new_px, sid, before)

    # --- [5] 仮想テーマの再合成（補正後の構成銘柄価格で全期間） ---
    if theme_ids:
        rebuilt = rebuild_virtual_index_prices(theme_ids, new_px, tc)
        keep = new_px[~new_px["symbol_id"].isin(theme_ids)]
        # 既存のテーマ行の列（id / market_cap 等）に合わせてから連結する
        rebuilt = rebuilt.reindex(columns=new_px.columns)
        new_px = pd.concat([keep, rebuilt], ignore_index=True)
        new_px = new_px.sort_values(["symbol_id", "date"]).reset_index(drop=True)
        print(f"    仮想テーマ {len(theme_ids)}件を再合成 → {len(rebuilt):,}行")

    # --- [6] T3 を対象銘柄＋テーマで再計算 ---
    ind = pd.read_parquet(cur["indicators"])
    ind["date"] = pd.to_datetime(ind["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    ind_cols = [c for c in ind.columns if c not in ("id", "symbol_id", "date")]
    recompute_ids = [sid] + theme_ids

    keep_ind = ind[~ind["symbol_id"].isin(recompute_ids)]
    new_ind_rows = recompute_indicators(recompute_ids, new_px, ind_cols, spy_id)
    next_id = int(pd.to_numeric(ind["id"], errors="coerce").max()) + 1
    new_ind_rows = new_ind_rows.copy()
    new_ind_rows["id"] = range(next_id, next_id + len(new_ind_rows))
    new_ind_rows["symbol_id"] = new_ind_rows["symbol_id"].astype(ind["symbol_id"].dtype)
    merged_ind = pd.concat([keep_ind, new_ind_rows[ind.columns]], ignore_index=True)
    merged_ind = merged_ind.sort_values(["symbol_id", "date"]).reset_index(drop=True)
    print(f"    indicators {len(ind):,} → {len(merged_ind):,}行")

    # --- [7] T4 は横断的なので全期間を作り直す ---
    new_ranks = recompute_ranks(merged_ind, sym).reset_index(drop=True)
    new_ranks.insert(0, "id", range(1, len(new_ranks) + 1))
    print(f"    ranks {len(pd.read_parquet(cur['ranks'])):,} → {len(new_ranks):,}行")

    # --- [8] 新世代の書き出し ---
    print("\n[5] 新世代の書き出し...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    files = {}
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "prices":
            new_px.to_parquet(dst, index=False)
        elif key == "indicators":
            merged_ind.to_parquet(dst, index=False)
        elif key == "ranks":
            new_ranks.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w",
              encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    import logging
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger("adjust_split")):
        print(f"[ERROR] pointer の更新に失敗。data_version_{ts}.json を手動で反映してください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")

    # --- [9] ホットキャッシュの同期（これを忘れると翌日のマージで戻る） ---
    print("\n[6] SQLite ホットキャッシュの同期...")
    def _n(v: int | None, unit: str) -> str:
        return "テーブルなし" if v is None else f"{v:,}行 {unit}"

    scaled = scale_sqlite_history(db_path, sid, before, factor,
                                  dry_run=False, expect_ticker=ticker)
    print(f"    daily_prices         {_n(scaled['daily_prices'], 'スケール')}")

    n_ind = replace_sqlite_rows(db_path, "indicators", recompute_ids,
                                merged_ind[merged_ind["symbol_id"].isin(recompute_ids)],
                                dry_run=False)
    print(f"    indicators           {_n(n_ind, '差し替え')}")

    rk = new_ranks[new_ranks["symbol_id"].isin(recompute_ids)]
    n_rk = replace_sqlite_rows(db_path, "relative_ranks", recompute_ids, rk, dry_run=False)
    print(f"    relative_ranks       {_n(n_rk, '差し替え')}")

    # 仮想テーマ指数そのものの価格は「スケール」ではなく再合成値で差し替える
    # （指数はリターンの連鎖なので、構成銘柄と同じ倍率で動くわけではない）
    if theme_ids:
        tpx = new_px[new_px["symbol_id"].isin(theme_ids)]
        n_tp = replace_sqlite_rows(db_path, "daily_prices", theme_ids, tpx, dry_run=False)
        print(f"    daily_prices(テーマ) {_n(n_tp, '差し替え')}")

    # --- [10] 次回 T2 に再合成させる ---
    n_hash = drop_virtual_theme_hashes(
        os.path.join(_project_root, VIRTUAL_THEME_HASH_FILE), theme_ids, dry_run=False)
    print(f"\n[7] virtual_theme_hashes.json から {n_hash}件を削除（次回 T2 で再合成）")


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="上流が調整しなかった株式分割・併合を手元で遡及補正する")
    p.add_argument("--ticker", required=True, help="対象ティッカー")
    p.add_argument("--before", required=True,
                   help="この日より前を補正する（YYYY-MM-DD。分割適用後の最初の日）")
    p.add_argument("--factor", required=True, type=float,
                   help="補正倍率。1:30 併合なら 30、1:4 分割なら 0.25")
    p.add_argument("--reason", default="", help="補正の理由（記録用）")
    p.add_argument("--tolerance", type=float, default=DEFAULT_SEAM_TOLERANCE,
                   help=f"接合部比率の許容誤差（相対、既定 {DEFAULT_SEAM_TOLERANCE}）")
    p.add_argument("--db-path", default=None,
                   help="書き込み先 SQLite を明示指定（省略時は STOCKTOOL_ENV / "
                        "STOCKTOOL_DB_PATH / config.toml の順で解決）")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    # 日次更新・週次メンテとの同時実行を防ぐ（2026-08-06 に世代破損）
    with pipeline_lock("adjust_symbol_split"):
        run(a.ticker, a.before, a.factor, a.reason, dry_run=not a.apply,
            tolerance=a.tolerance, db_path=a.db_path)
