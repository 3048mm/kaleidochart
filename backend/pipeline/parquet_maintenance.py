"""Parquet マスターを直接手当てするスクリプトの共通部品。

`backend/scripts/` の補修スクリプト（分割の遡及補正・仮想指数の再合成・履歴の
切り詰めなど）は、どれも同じ形をしている:

    Parquet を読む → 一部を作り直す → **新世代として書き出す** →
    ポインタを差し替える → **SQLite ホットキャッシュにも反映する**

このうち後半2つは間違えると被害が大きいわりに毎回同じなので、ここに集約する。

> [!IMPORTANT]
> **SQLite への反映を省くと翌日のデイリー更新で修正が元に戻る。**
> `daily_prices` / `indicators` / `relative_ranks` は Parquet と SQLite の
> **マージ**（`drop_duplicates(keep='last')`・SQL 側優先）で伝播するため、
> ホットキャッシュ（直近730日）に古い値が残っているとそのまま Parquet に復活する。
> 2026-08-06 に `JBIO` の切り詰めがこれで無効化された。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3

import pandas as pd

from pipeline.parquet_cache_manager import update_pointer_with_retry

# マージで SQLite → Parquet に戻る階層。**ここを漏らすと修正が翌日で無効化される。**
MERGED_TABLES = ("daily_prices", "indicators", "relative_ranks")

# `latest_master.json` のキー → ファイル名の接頭辞
MASTER_NAME_MAP = {
    "symbols": "symbols", "prices": "prices", "indicators": "indicators",
    "ranks": "ranks", "tc": "theme_constituents",
    "signals": "market_signals", "fx": "fx_rates",
}


def resolve_db_path(config: dict, explicit: str | None = None) -> str:
    """書き込み先の SQLite を決める。**環境変数を無視しないこと。**

    環境変数の解釈は `db.database.init_db()` の中にしか無いため、それを import
    しないスクリプト（`truncate_symbol_history.py` など）は
    **`STOCKTOOL_DB_PATH` を設定しても本番 DB を掴む**。Sandbox で検証したつもりが
    本番を書き換える事故になるので、ここで明示的に解決する。

    優先順位は `init_db()` と揃えること（ズレると API が見ている DB と
    スクリプトが書く DB が食い違う）:

        explicit(--db-path) > STOCKTOOL_ENV=sandbox|test > STOCKTOOL_DB_PATH > config.toml
    """
    if explicit:
        return explicit
    env_name = os.getenv("STOCKTOOL_ENV")
    if env_name == "sandbox":
        return "data/sandbox/stocktool.db"
    if env_name == "test":
        return "data/test/stocktool.db"
    return os.getenv("STOCKTOOL_DB_PATH") or config["system"]["db_path"]


def write_master_generation(parquet_dir: str, pointer_file: str, current: dict,
                            overrides: dict[str, pd.DataFrame],
                            label: str = "maintenance",
                            verbose: bool = True) -> dict[str, str]:
    """新しい世代を書き出して `latest_master.json` を差し替える。

    上書きは絶対にしない（Windows のファイル共有ロック回避のため MVCC 世代管理）。
    `overrides` に無いテーブルは現行世代からコピーする。

    Args:
        current: `get_latest_master_files()` の戻り（キー → 現行ファイルパス）
        overrides: 差し替えるテーブル（`MASTER_NAME_MAP` のキー → DataFrame）

    Returns:
        新世代のキー → ファイルパス。

    Raises:
        RuntimeError: ポインタの更新に失敗した場合（**世代ファイルは残る**ので
            `data_version_<ts>.json` を手で反映すれば復旧できる）。

    Note:
        **旧世代は prune しない。** 検証に合格するまで MVCC 旧世代がロールバック用の
        バックアップを兼ねる（`doc/agent_execution_rules.md` §10.1）。
    """
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    files: dict[str, str] = {}
    for key, base in MASTER_NAME_MAP.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key in overrides:
            overrides[key].to_parquet(dst, index=False)
        else:
            shutil.copy2(current[key], dst)
        files[key] = dst
        if verbose:
            print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w",
              encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, logging.getLogger(label)):
        raise RuntimeError(
            f"pointer の更新に失敗しました。data_version_{ts}.json を手動で反映してください。")
    if verbose:
        print(f"\n    pointer を data_version_{ts} に更新しました")
        print("    ※ 旧世代は prune していません（検証合格までバックアップを兼ねる）")
    return files


def connect_hot_cache(db_path: str) -> sqlite3.Connection:
    """ホットキャッシュへの接続。WAL 前提の PRAGMA を必ず入れる。"""
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def assert_ticker(con: sqlite3.Connection, symbol_id: int, expect_ticker: str | None):
    """`symbol_id` が期待どおりのティッカーを指していることを確認する。

    **全期間再構築は `symbols.id` を再採番する**（2026-08-06 に 2,378件が変化）。
    Parquet で解決した id を照合せずに SQLite へ流すと**別銘柄を壊す**。

    Raises:
        ValueError: 食い違った場合（**書き込まない**）。
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


def replace_sqlite_rows(db_path: str, table: str, symbol_ids: list[int],
                        rows: pd.DataFrame, dry_run: bool) -> int | None:
    """指定銘柄の行を再計算値で**差し替える**（delete → insert）。

    指標は比率・偏差・フラグが混在しており、`×factor` のような一律変換で意味が
    通るのは価格系の一部だけ。価格を直したら再計算した値で丸ごと置き換えるのが
    唯一正しい。

    SQLite に無い列は黙って捨てる（指標列は 50 → 63 と増え続けているため）。

    Returns:
        挿入した行数。テーブルが無ければ ``None``。
    """
    con = connect_hot_cache(db_path)
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

        # **`itertuples()` を使ってはいけない。** ID 列は Parquet の規約で `Int64`
        # （pandas nullable）なので `itertuples()` は `numpy.int64` を返す。
        # numpy スカラはバッファプロトコルを持つため **sqlite3 が BLOB として束縛し**、
        # 挿入は成功して件数も合うのに `WHERE symbol_id = ?` が1件も返らなくなる。
        #
        # 2026-08-25 に Sandbox で発生（仮想テーマ3本の価格が丸ごと参照不能になった）:
        #     (b'=\x01\x00\x00\x00\x00\x00\x00', 2110, '2018-04-03', '2026-08-24')
        #
        # `to_numpy()` は Python の int / float に落とすので安全。
        # `restore_sqlite_cache_from_parquet` も同じ形にしてある。
        records = [tuple(x) for x in payload.to_numpy()]

        con.execute("BEGIN IMMEDIATE")
        for i in range(0, len(symbol_ids), 900):
            chunk = symbol_ids[i:i + 900]
            marks = ",".join("?" * len(chunk))
            con.execute(f"DELETE FROM {table} WHERE symbol_id IN ({marks})", chunk)
        if records:
            marks = ",".join("?" * len(use))
            con.executemany(
                f"INSERT INTO {table} ({','.join(use)}) VALUES ({marks})", records)
        con.commit()
        return len(records)
    finally:
        con.close()


def drop_virtual_theme_hashes(hash_path: str, theme_ids: list[int],
                              dry_run: bool) -> int:
    """再合成させたいテーマのハッシュを落とす。

    再合成判定は**構成銘柄集合のハッシュ**なので、構成銘柄の「価格」が変わっても
    ハッシュは変わらず差分モードのままになる。落としておくと次回 T2 が作り直す。
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


def recompute_and_publish(cur: dict, parquet_dir: str, pointer_file: str,
                          db_path: str, new_px: pd.DataFrame, symbols: pd.DataFrame,
                          recompute_ids: list[int], spy_id: int, label: str,
                          theme_ids: list[int] | None = None,
                          hash_path: str | None = None) -> None:
    """価格を差し替えた後の共通の後半処理。

    補修スクリプト（分割補正・仮想指数の再合成・捏造行の削除）はどれも
    「価格を直す → T3 を作り直す → T4 を作り直す → 世代を公開する →
    SQLite にも反映する」で終わる。**ここを各スクリプトに複製すると必ず片方だけ
    直され、「Parquet は直ったが SQLite は古いまま」という半端な状態を生む。**

    Args:
        cur: 現行世代のファイル辞書（`get_latest_master_files()` の戻り）
        new_px: 差し替え後の全銘柄の価格
        recompute_ids: T3 を作り直す symbol_id（対象銘柄＋再合成した仮想テーマ）
        theme_ids: 再合成した仮想テーマ。ハッシュ削除の対象
        hash_path: `virtual_theme_hashes.json` のパス（None なら削除しない）
    """
    import pyarrow.parquet as pq

    from pipeline.parquet_recompute import recompute_indicators, recompute_ranks

    print("\n[+] 指標と順位の再計算...")
    ind = pd.read_parquet(cur["indicators"])
    ind["date"] = pd.to_datetime(ind["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    ind_cols = [c for c in ind.columns if c not in ("id", "symbol_id", "date")]

    keep_ind = ind[~ind["symbol_id"].isin(recompute_ids)]
    new_rows = recompute_indicators(recompute_ids, new_px, ind_cols, spy_id).copy()
    next_id = int(pd.to_numeric(ind["id"], errors="coerce").max()) + 1
    new_rows["id"] = range(next_id, next_id + len(new_rows))

    # **concat の前に dtype を既存へ揃える。** 1列でも object が混じると
    # 600万行 × 63列がまるごと object に巻き上げられ、sort のコピーで OOM する
    # （2026-08-25 に Sandbox で実際に落ちた）。
    for col in ind.columns:
        want = ind[col].dtype
        if col in new_rows.columns and new_rows[col].dtype != want:
            try:
                new_rows[col] = new_rows[col].astype(want)
            except (TypeError, ValueError):
                new_rows[col] = pd.to_numeric(new_rows[col], errors="coerce")

    merged_ind = pd.concat([keep_ind, new_rows[ind.columns]], ignore_index=True)
    del keep_ind, new_rows
    objs = [c for c in merged_ind.columns
            if c != "date" and merged_ind[c].dtype == object]
    if objs:
        print(f"    [WARN] object 列が残っています（メモリが膨らみます）: {objs[:5]}")
    # reset_index(drop=True) を別に呼ぶとフレーム全体をもう一度コピーする
    merged_ind.sort_values(["symbol_id", "date"], inplace=True, ignore_index=True)
    print(f"    indicators {len(ind):,} → {len(merged_ind):,}行")
    del ind

    # 旧行数は表示用。941MB のフレームを読み込まずメタデータから取る
    old_rank_rows = pq.ParquetFile(cur["ranks"]).metadata.num_rows
    # T4 は横断的なので全期間を作り直す（母集団が変わった日付は全銘柄が影響を受ける）
    new_ranks = recompute_ranks(merged_ind, symbols)
    new_ranks.insert(0, "id", range(1, len(new_ranks) + 1))
    print(f"    ranks {old_rank_rows:,} → {len(new_ranks):,}行")

    print("\n[+] 新世代の書き出し...")
    try:
        write_master_generation(
            parquet_dir, pointer_file, cur,
            {"prices": new_px, "indicators": merged_ind, "ranks": new_ranks},
            label=label)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        raise SystemExit(1)

    print("\n[+] SQLite ホットキャッシュの同期...")

    def _n(v, unit):
        return "テーブルなし" if v is None else f"{v:,}行 {unit}"

    n_px = replace_sqlite_rows(db_path, "daily_prices", recompute_ids,
                               new_px[new_px["symbol_id"].isin(recompute_ids)],
                               dry_run=False)
    print(f"    daily_prices     {_n(n_px, '差し替え')}")
    n_ind = replace_sqlite_rows(db_path, "indicators", recompute_ids,
                                merged_ind[merged_ind["symbol_id"].isin(recompute_ids)],
                                dry_run=False)
    print(f"    indicators       {_n(n_ind, '差し替え')}")
    n_rk = replace_sqlite_rows(db_path, "relative_ranks", recompute_ids,
                               new_ranks[new_ranks["symbol_id"].isin(recompute_ids)],
                               dry_run=False)
    print(f"    relative_ranks   {_n(n_rk, '差し替え')}")

    if theme_ids and hash_path:
        n_hash = drop_virtual_theme_hashes(hash_path, theme_ids, dry_run=False)
        print(f"\n[+] virtual_theme_hashes.json から {n_hash}件を削除（次回 T2 で再合成）")
