"""rs_blue_dot_age / rs_red_dot_age を本番へ昇格する（Parquet 差し替え + SQLite 更新）。

## なぜ run_production_restore.py を使わないのか

標準の昇格手順は `stocktool.db` を**ファイルごと削除**して Parquet から作り直す。
Parquet に無いデータは消えるため過去に事故が起きている
（`.claude/skills/parquet-data-quality/SKILL.md` §8: fx_rates 7,715行の喪失）。

本タスクは **indicators の2列を入れ替えるだけ**なので、削除は過剰。
`ALTER TABLE ADD COLUMN` + 該当行の `UPDATE` で足りる。WAL なので読み取り接続と
共存できる。

## 旧列（is_rs_blue_dot / is_rs_red_dot）の扱い

**DROP せずに残す**（計画書 §4 #4）。SQLite の DROP COLUMN はロールバックを難しくし、
値が残っていても新コードは参照しない。次回のフル復元で自然に消える。
Parquet 側は `backfill_rs_dot_age.py` が drop するので、**両者で列構成が一時的に
食い違う**が、`db/models.py` が新列しか宣言していないため実害は無い。

## 手順

1. `VACUUM INTO` で stocktool.db の一貫したバックアップを取る
2. Parquet に新世代を publish（`backfill_rs_dot_age.py --apply`）
3. SQLite に2列を追加し、新 Parquet から**ホット期間の行だけ** UPDATE
4. 検証（列の存在・NULL が無いこと・値域・Parquet との値一致）

旧 Parquet 世代は prune しないので、失敗時はポインタを戻せば復旧できる。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.relative_strength import RS_DOT_AGE_MAX, RS_DOT_AGE_NONE  # noqa: E402

NEW_COLUMNS = ('rs_blue_dot_age', 'rs_red_dot_age')


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=60.0)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=60000')
    con.execute('PRAGMA synchronous=NORMAL')
    return con


def backup(db_path: str) -> str:
    """VACUUM INTO で一貫したスナップショットを取る（読み取り接続があっても可）。"""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = f'{os.path.splitext(db_path)[0]}.pre_dotage_{stamp}.db'
    con = _connect(db_path)
    try:
        con.execute('VACUUM INTO ?', (dest,))
    finally:
        con.close()
    _log(f'バックアップ: {dest} ({os.path.getsize(dest) / 1e9:.2f} GB)')
    return dest


def add_columns(db_path: str) -> None:
    con = _connect(db_path)
    try:
        existing = {r[1] for r in con.execute('PRAGMA table_info(indicators)')}
        for col in NEW_COLUMNS:
            if col in existing:
                _log(f'  {col} は既に存在（スキップ）')
                continue
            # 既存行は未点灯扱いで埋めてから UPDATE する（NULL を残さない）
            con.execute(
                f'ALTER TABLE indicators ADD COLUMN {col} SMALLINT DEFAULT {RS_DOT_AGE_NONE}')
            _log(f'  {col} を追加（既定 {RS_DOT_AGE_NONE}）')
        con.commit()
    finally:
        con.close()


def update_hot_cache(db_path: str, indicators_path: str) -> dict:
    """ホット期間（SQLite が持っている日付範囲）の行だけ Parquet から埋める。"""
    con = _connect(db_path)
    try:
        lo, hi = con.execute('SELECT MIN(date), MAX(date) FROM indicators').fetchone()
        _log(f'  ホット期間: {lo} 〜 {hi}')
        total = con.execute('SELECT COUNT(*) FROM indicators').fetchone()[0]

        df = pd.read_parquet(indicators_path,
                             columns=['symbol_id', 'date', *NEW_COLUMNS],
                             filters=[('date', '>=', str(lo)), ('date', '<=', str(hi))])
        df['date'] = df['date'].astype(str)
        _log(f'  Parquet から {len(df):,} 行を読み込み')

        rows = [(int(b), int(r), int(s), d)
                for s, d, b, r in df.itertuples(index=False, name=None)]

        con.execute('BEGIN IMMEDIATE')
        con.executemany(
            f'UPDATE indicators SET {NEW_COLUMNS[0]} = ?, {NEW_COLUMNS[1]} = ? '
            'WHERE symbol_id = ? AND date = ?', rows)
        con.commit()
        lit = con.execute(
            f'SELECT COUNT(*) FROM indicators WHERE {NEW_COLUMNS[0]} = 0').fetchone()[0]
        return {'total': total, 'parquet_rows': len(df), 'lit': lit}
    finally:
        con.close()


def verify(db_path: str, indicators_path: str) -> None:
    con = _connect(db_path)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info(indicators)')}
        for col in NEW_COLUMNS:
            assert col in cols, f'{col} が追加されていない'

            nulls = con.execute(
                f'SELECT COUNT(*) FROM indicators WHERE {col} IS NULL').fetchone()[0]
            assert nulls == 0, f'{col} に NULL が {nulls:,} 件（numeric フィルタが NaN 落ちする）'

            bad = con.execute(
                f'SELECT COUNT(*) FROM indicators WHERE {col} < 0 '
                f'OR ({col} > ? AND {col} != ?)', (RS_DOT_AGE_MAX, RS_DOT_AGE_NONE)).fetchone()[0]
            assert bad == 0, f'{col} に想定外の値が {bad:,} 件'

        # Parquet と値を突合（最新日のサンプル）
        latest_date = con.execute('SELECT MAX(date) FROM indicators').fetchone()[0]
        sq = pd.read_sql_query(
            f'SELECT symbol_id, {NEW_COLUMNS[0]} FROM indicators WHERE date = ? '
            'ORDER BY symbol_id LIMIT 500', con, params=(latest_date,))
        pq_df = pd.read_parquet(indicators_path, columns=['symbol_id', 'date', NEW_COLUMNS[0]],
                                filters=[('date', '==', str(latest_date))])
        merged = sq.merge(pq_df[['symbol_id', NEW_COLUMNS[0]]], on='symbol_id',
                          suffixes=('_sqlite', '_parquet'))
        diff = int((merged[f'{NEW_COLUMNS[0]}_sqlite']
                    != merged[f'{NEW_COLUMNS[0]}_parquet']).sum())
        assert diff == 0, f'SQLite と Parquet で {diff} 件の値が不一致'
        _log(f'  検証 OK: {latest_date} の {len(merged)} 件で Parquet と一致')
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-path', default='data/stocktool.db')
    ap.add_argument('--parquet-dir', default='data/parquet_master')
    ap.add_argument('--skip-backfill', action='store_true',
                    help='Parquet は既に昇格済みで、SQLite 側だけ入れたい場合')
    args = ap.parse_args()

    t0 = time.perf_counter()
    _log('=== 1. SQLite のバックアップ ===')
    backup(args.db_path)

    if not args.skip_backfill:
        _log('=== 2. Parquet に新世代を publish ===')
        from scripts.backfill_rs_dot_age import main as backfill_main
        sys.argv = ['backfill', '--source-dir', args.parquet_dir,
                    '--out-dir', args.parquet_dir, '--apply']
        backfill_main()

    with open(os.path.join(args.parquet_dir, 'latest_master.json'), encoding='utf-8') as f:
        latest = json.load(f)
    _log(f'現行世代の indicators: {os.path.basename(latest["indicators"])}')

    _log('=== 3. SQLite に列を追加 ===')
    add_columns(args.db_path)

    _log('=== 4. ホットキャッシュを更新 ===')
    stats = update_hot_cache(args.db_path, latest['indicators'])
    _log(f'  indicators {stats["total"]:,} 行 / 当日点灯 {stats["lit"]:,} 行')

    _log('=== 5. 検証 ===')
    verify(args.db_path, latest['indicators'])
    _log(f'昇格完了 {time.perf_counter() - t0:.1f}s')
    _log('注意: 旧 is_rs_blue_dot / is_rs_red_dot 列は SQLite に残置している（意図的）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
