"""sp_pivot / sp_hl を本番へ昇格する（Parquet 差し替え + SQLite ホットキャッシュ増分更新）。

## なぜ run_production_restore.py を使わないのか

標準の昇格手順は `stocktool.db` を**ファイルごと削除**して Parquet から作り直す。
Parquet に無いデータは消えるため過去に事故が起きている
（`.claude/skills/parquet-data-quality/SKILL.md` §8: fx_rates 7,715行の喪失）。

本タスクは **indicators に2列足すだけ**なので、削除は過剰。
`ALTER TABLE ADD COLUMN` + 該当行の `UPDATE` で足りる。WAL なので読み取り接続と
共存でき、API サーバーを止められない状況でも安全側に倒せる。

## 手順

1. `VACUUM INTO` で stocktool.db の一貫したバックアップを取る
2. Parquet に新世代を publish（`backfill_structure_pivot.py --apply`）
3. SQLite に2列を追加し、新 Parquet から**ホット期間の行だけ** UPDATE
4. 検証（列の存在・NULL率・Parquet との値一致）

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

#: 昇格対象の既定。--columns で切り替える（sp_pivot / sp_hl は 2026-08-26 に昇格済みなので、
#: sp_counter を足すときは --columns sp_counter を指定する）
DEFAULT_COLUMNS = ('sp_pivot', 'sp_hl')


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
    dest = f'{os.path.splitext(db_path)[0]}.pre_sp_{stamp}.db'
    con = _connect(db_path)
    try:
        con.execute("VACUUM INTO ?", (dest,))
    finally:
        con.close()
    _log(f'バックアップ: {dest} ({os.path.getsize(dest) / 1e9:.2f} GB)')
    return dest


def add_columns(db_path: str, columns: tuple) -> None:
    con = _connect(db_path)
    try:
        existing = {r[1] for r in con.execute('PRAGMA table_info(indicators)')}
        for col in columns:
            if col in existing:
                _log(f'  {col} は既に存在（スキップ）')
                continue
            con.execute(f'ALTER TABLE indicators ADD COLUMN {col} FLOAT')
            _log(f'  {col} を追加')
        con.commit()
    finally:
        con.close()


def update_hot_cache(db_path: str, indicators_path: str, columns: tuple) -> dict:
    """ホット期間（SQLite が持っている日付範囲）の行だけ Parquet から埋める。"""
    con = _connect(db_path)
    try:
        lo, hi = con.execute('SELECT MIN(date), MAX(date) FROM indicators').fetchone()
        _log(f'  ホット期間: {lo} 〜 {hi}')
        total = con.execute('SELECT COUNT(*) FROM indicators').fetchone()[0]

        df = pd.read_parquet(indicators_path,
                             columns=['symbol_id', 'date', *columns],
                             filters=[('date', '>=', str(lo)), ('date', '<=', str(hi))])
        df['date'] = df['date'].astype(str)
        _log(f'  Parquet から {len(df):,} 行を読み込み')

        # 値の並びは columns の順。末尾に WHERE 用の (symbol_id, date) を付ける
        rows = [tuple(None if pd.isna(v) else float(v) for v in rec[2:]) + (int(rec[0]), rec[1])
                for rec in df.itertuples(index=False, name=None)]

        assigns = ', '.join(f'{c} = ?' for c in columns)
        con.execute('BEGIN IMMEDIATE')
        con.executemany(
            f'UPDATE indicators SET {assigns} WHERE symbol_id = ? AND date = ?', rows)
        con.commit()
        filled = con.execute(
            f'SELECT COUNT(*) FROM indicators WHERE {columns[0]} IS NOT NULL').fetchone()[0]
        return {'total': total, 'parquet_rows': len(df), 'filled': filled}
    finally:
        con.close()


def verify(db_path: str, indicators_path: str, columns: tuple) -> None:
    con = _connect(db_path)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info(indicators)')}
        missing = [c for c in columns if c not in cols]
        assert not missing, f'列が追加されていない: {missing}'

        if 'sp_pivot' in columns and 'sp_hl' in columns:
            bad = con.execute(
                'SELECT COUNT(*) FROM indicators WHERE sp_pivot IS NOT NULL AND sp_hl >= sp_pivot'
            ).fetchone()[0]
            assert bad == 0, f'sp_hl >= sp_pivot の違反が {bad} 件'

        if 'sp_counter' in columns and 'sp_pivot' in cols:
            # カウンター線は構造が無い期間にだけ引かれる ＝ sp_pivot とは排他
            both = con.execute(
                'SELECT COUNT(*) FROM indicators '
                'WHERE sp_counter IS NOT NULL AND sp_pivot IS NOT NULL').fetchone()[0]
            assert both == 0, f'sp_counter と sp_pivot が同時に入っている行が {both} 件'

        # Parquet と値を突合（最新日のサンプル）
        latest_date = con.execute('SELECT MAX(date) FROM indicators').fetchone()[0]
        probe = columns[0]
        sq = pd.read_sql_query(
            f'SELECT symbol_id, {probe} FROM indicators WHERE date = ? AND {probe} IS NOT NULL '
            'ORDER BY symbol_id LIMIT 200', con, params=(latest_date,))
        pq_df = pd.read_parquet(indicators_path, columns=['symbol_id', 'date', probe],
                                filters=[('date', '==', str(latest_date))])
        merged = sq.merge(pq_df[['symbol_id', probe]], on='symbol_id',
                          suffixes=('_sqlite', '_parquet'))
        diff = (~merged[f'{probe}_sqlite'].sub(merged[f'{probe}_parquet']).abs().lt(1e-6)).sum()
        assert diff == 0, f'SQLite と Parquet で {diff} 件の値が不一致'
        _log(f'  検証 OK: {latest_date} の {len(merged)} 件で Parquet と一致')
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-path', default='data/stocktool.db')
    ap.add_argument('--parquet-dir', default='data/parquet_master')
    ap.add_argument('--columns', nargs='+', default=list(DEFAULT_COLUMNS),
                    choices=['sp_pivot', 'sp_hl', 'sp_counter'],
                    help='昇格する列。既に本番に入っている列は指定しないこと')
    ap.add_argument('--skip-backfill', action='store_true',
                    help='Parquet は既に昇格済みで、SQLite 側だけ入れたい場合')
    args = ap.parse_args()

    columns = tuple(args.columns)
    t0 = time.perf_counter()
    _log(f'昇格する列: {list(columns)}')
    _log('=== 1. SQLite のバックアップ ===')
    backup(args.db_path)

    if not args.skip_backfill:
        _log('=== 2. Parquet に新世代を publish ===')
        from scripts.backfill_structure_pivot import main as backfill_main
        sys.argv = ['backfill', '--source-dir', args.parquet_dir,
                    '--out-dir', args.parquet_dir, '--apply', '--columns', *columns]
        backfill_main()

    with open(os.path.join(args.parquet_dir, 'latest_master.json'), encoding='utf-8') as f:
        latest = json.load(f)
    _log(f'現行世代の indicators: {os.path.basename(latest["indicators"])}')

    _log('=== 3. SQLite に列を追加 ===')
    add_columns(args.db_path, columns)

    _log('=== 4. ホットキャッシュを更新 ===')
    stats = update_hot_cache(args.db_path, latest['indicators'], columns)
    _log(f'  indicators {stats["total"]:,} 行 / 値が入った行 {stats["filled"]:,} '
         f'({stats["filled"] / max(stats["total"], 1) * 100:.1f}%)')

    _log('=== 5. 検証 ===')
    verify(args.db_path, latest['indicators'], columns)
    _log(f'昇格完了 {time.perf_counter() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
