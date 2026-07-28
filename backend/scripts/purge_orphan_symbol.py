"""重複採番された銘柄行とその派生データを一括削除する（universe.db 移行 §7.6）。

背景:
    `(ticker, exchange)` が symbols の自然キーであるため、exchange が変わると
    新しい id が採番され、旧 id に価格履歴が取り残される。
    2026-07-28 に GBTC が (GBTC,'US') → (GBTC,'NASDAQ') となり、
    id=3256（旧・active=0）と id=3258（新・active=1）の重複が発生した。

    旧 id は `active=0` だが `t4_ranks.py` は active でフィルタしないため、
    無効化された重複銘柄が相対ランクの母集団に混入し続ける。

削除対象:
    SQLite : symbols / daily_prices / indicators / relative_ranks / theme_constituents
    Parquet: symbols / prices / indicators / ranks / theme_constituents

Parquet は MVCC 世代管理に従い、**上書きせず新しいタイムスタンプ付きファイルとして書き出し**、
`latest_master.json` をアトミックに差し替える。旧世代はロールバック用に残す。
2.6GB の indicators を扱うため、PyArrow の行グループ単位ストリーミングで書く（OOM 回避）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\purge_orphan_symbol.py --symbol-id 3256 --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\purge_orphan_symbol.py --symbol-id 3256
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli
from pipeline.parquet_cache_manager import (
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# SQLite: テーブル名 → symbol_id を指す列（複数可）
SQLITE_TARGETS = {
    "daily_prices": ["symbol_id"],
    "indicators": ["symbol_id"],
    "relative_ranks": ["symbol_id"],
    "theme_constituents": ["theme_id", "symbol_id"],
}

# Parquet: latest_master.json のキー → symbol_id を指す列
PARQUET_TARGETS = {
    "prices": ["symbol_id"],
    "indicators": ["symbol_id"],
    "ranks": ["symbol_id"],
    "tc": ["theme_id", "symbol_id"],
    "symbols": ["id"],
}


def _filter_parquet(src_path: str, dst_path: str, columns: list, symbol_id: int) -> tuple:
    """行グループ単位でストリーミングしながら symbol_id 該当行を除外して書き出す。"""
    pf = pq.ParquetFile(src_path)
    schema = pf.schema_arrow
    total = removed = 0
    writer = None
    try:
        for batch in pf.iter_batches(batch_size=200_000):
            table = pa.Table.from_batches([batch], schema=schema)
            total += table.num_rows

            mask = None
            for col in columns:
                if col not in table.column_names:
                    continue
                cond = pc.not_equal(table[col], symbol_id)
                # NULL は残す（NULL != x は NULL になるため明示的に埋める）
                cond = pc.fill_null(cond, True)
                mask = cond if mask is None else pc.and_(mask, cond)

            if mask is not None:
                kept = table.filter(mask)
                removed += table.num_rows - kept.num_rows
                table = kept

            if table.num_rows == 0:
                continue
            if writer is None:
                writer = pq.ParquetWriter(dst_path, schema, compression="snappy")
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    return total, removed


def run(symbol_id: int, dry_run: bool = False):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]

    print("=" * 72)
    print(f"重複銘柄行の削除  symbol_id={symbol_id}  (dry-run={dry_run})")
    print("=" * 72)

    # ------------------------------------------------------------------
    # 0) 安全確認: 対象が本当に「無効化された重複」であること
    # ------------------------------------------------------------------
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=30000")
    row = conn.execute(
        "SELECT id, ticker, exchange, category, active FROM symbols WHERE id=?", (symbol_id,)
    ).fetchone()
    if row is None:
        print(f"[ERROR] symbol_id={symbol_id} が存在しません。中断します。")
        sys.exit(1)
    print(f"\n[0] 対象: id={row[0]} ticker={row[1]} exchange={row[2]} category={row[3]} active={row[4]}")

    if row[4] != 0:
        print("[ERROR] active=1 の銘柄は削除できません（無効化されたものだけが対象）。中断します。")
        sys.exit(1)

    siblings = conn.execute(
        "SELECT id, exchange, active FROM symbols WHERE ticker=? AND id<>?", (row[1], symbol_id)
    ).fetchall()
    live = [s for s in siblings if s[2] == 1]
    print(f"    同一 ticker の他行: {siblings}")
    if not live:
        print("[ERROR] 同一 ticker に active=1 の代替行がありません。"
              "履歴が完全に失われるため中断します。")
        sys.exit(1)
    print(f"    → active な代替行 id={live[0][0]} (exchange={live[0][1]}) が存在するため削除可能")

    # user_data.db が参照していないか
    user_db_path = config["system"].get(
        "user_db_path", os.path.join(os.path.dirname(db_path), "user_data.db"))
    if os.path.exists(user_db_path):
        udb = sqlite3.connect(user_db_path)
        refs = []
        for t in ("watchlist", "portfolio_positions", "position_history"):
            try:
                n = udb.execute(f"SELECT COUNT(*) FROM {t} WHERE symbol_id=?", (symbol_id,)).fetchone()[0]
                if n:
                    refs.append((t, n))
            except sqlite3.OperationalError:
                pass
        udb.close()
        print(f"    user_data.db からの参照: {refs if refs else 'なし'}")
        if refs:
            print("    （heal_*_ids が ticker 経由で再解決するため削除自体は安全）")

    # ------------------------------------------------------------------
    # 1) SQLite
    # ------------------------------------------------------------------
    print("\n[1] SQLite の削除対象")
    counts = {}
    for table, cols in SQLITE_TARGETS.items():
        where = " OR ".join(f"{c}=?" for c in cols)
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}",
                         tuple([symbol_id] * len(cols))).fetchone()[0]
        counts[table] = n
        print(f"    {table:<22} {n:>8,} 行")
    print(f"    {'symbols':<22} {1:>8,} 行")

    if not dry_run:
        conn.execute("BEGIN IMMEDIATE")
        for table, cols in SQLITE_TARGETS.items():
            where = " OR ".join(f"{c}=?" for c in cols)
            conn.execute(f"DELETE FROM {table} WHERE {where}", tuple([symbol_id] * len(cols)))
        conn.execute("DELETE FROM symbols WHERE id=?", (symbol_id,))
        conn.commit()
        print("    → 削除完了")
    conn.close()

    # ------------------------------------------------------------------
    # 2) Parquet（MVCC: 新世代を書いてポインタを差し替え）
    # ------------------------------------------------------------------
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    latest = get_latest_master_files(pointer_file)
    if not latest:
        print("\n[ERROR] latest_master.json を解決できません。中断します。")
        sys.exit(1)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n[2] Parquet の書き換え（新世代 {stamp}）")

    new_files = dict(latest)
    for key, cols in PARQUET_TARGETS.items():
        src = latest.get(key)
        if not src or not os.path.exists(src):
            print(f"    {key:<10} ソース不明のためスキップ")
            continue

        base = os.path.basename(src)
        prefix = base.rsplit("_", 2)[0]
        dst = os.path.join(parquet_dir, f"{prefix}_{stamp}.parquet")

        if dry_run:
            pf = pq.ParquetFile(src)
            tbl = pf.read(columns=[c for c in cols if c in pf.schema_arrow.names])
            hit = 0
            for c in cols:
                if c in tbl.column_names:
                    hit += pc.sum(pc.fill_null(pc.equal(tbl[c], symbol_id), False)).as_py() or 0
            print(f"    {key:<10} {pf.metadata.num_rows:>10,} 行中 {hit:>6,} 行が該当")
            continue

        total, removed = _filter_parquet(src, dst, cols, symbol_id)
        new_files[key] = dst
        print(f"    {key:<10} {total:>10,} 行 → {total - removed:>10,} 行  (削除 {removed:,})")

    if not dry_run:
        if update_pointer_with_retry(pointer_file, new_files, logger):
            print("    → latest_master.json を更新しました（旧世代はロールバック用に保持）")
        else:
            print("    [ERROR] ポインタ更新に失敗しました。旧世代のままです。")
            sys.exit(1)

    print("\n=== Done ===")
    if not dry_run:
        print("検証: .\\venv\\Scripts\\python.exe tools\\db_health_check.py --parquet")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重複採番された銘柄行と派生データを削除する")
    parser.add_argument("--symbol-id", type=int, required=True, help="削除する symbols.id")
    parser.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    args = parser.parse_args()
    run(symbol_id=args.symbol_id, dry_run=args.dry_run)
