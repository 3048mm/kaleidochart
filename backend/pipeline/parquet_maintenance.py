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
