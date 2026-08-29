"""Parquet マスターの indicators に sp_pivot / sp_hl を全期間バックフィルする。

## なぜ専用スクリプトが要るのか

`tools/deploy_after_merge.ps1 -RebuildFrom T3` は **ホット期間（730日）しか再計算しない**
（`backend/scripts/deploy_after_merge.py` L113 のコメント）。既存カラムの値を直すだけなら
それで足りるが、**新規カラムでは Parquet の残り5年分が NULL のまま残る**。
バックテスト期間は5年あるため、そのままでは大半が欠損して使い物にならない。

## なぜ「全指標の再計算」ではなく「2列の追記」なのか

`sp_pivot` / `sp_hl` は **high / low / close だけ**から決まり、他の指標に依存しない。
`pipeline.parquet_recompute.recompute_indicators()` は全63列を作り直すため、
過去に指標ロジックが変わっていた場合に**既存カラムの値まで動く**危険がある
（だから計画書 §3.5 では「既存カラム不変の検査」を安全弁に置いていた）。

本スクリプトは **既存カラムを一切読み書きせず、arrow の Table にそのまま2列を足す**。
pandas を経由しないので dtype も変わらない。**既存カラムは構造的に不変**であり、
検査に頼るのではなく、そもそも壊しようがない形にしてある。

## 使い方

    # Sandbox 検証（本番を読み、Sandbox へ新世代を書く。本番は無変更）
    python backend/scripts/backfill_structure_pivot.py \
        --source-dir "data/parquet_master" --out-dir "data/sandbox/parquet_master"

    # 本番へ適用（ユーザー実施。API サーバーと日次更新を止めてから）
    python backend/scripts/backfill_structure_pivot.py \
        --source-dir "data/parquet_master" --out-dir "data/parquet_master" --apply

`--apply` を付けたときだけ `latest_master.json` を差し替える。付けなければ
新しい indicators ファイルを書くだけで、ポインタは動かさない（本番は無傷）。
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from indicators.structure_pivot import (  # noqa: E402
    DEFAULT_MAX_LEN, DEFAULT_MIN_LEN, counter_trend_series, structure_pivot_series,
)

#: バックフィルできる列の全体。--columns で部分指定する（既に本番に入っている列を
#: 除いて追加するため。sp_pivot / sp_hl は 2026-08-26 に昇格済み）
ALL_COLUMNS = ('sp_pivot', 'sp_hl', 'sp_counter')


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def compute_structure_columns(prices_path: str, min_len: int, max_len: int,
                              columns: tuple) -> pd.DataFrame:
    """価格マスターから全銘柄の (symbol_id, date, sp_pivot, sp_hl) を作る。

    銘柄ごとに日付昇順で計算する。確定遅延は `structure_pivot_series` 側で保たれている。
    """
    px = pd.read_parquet(prices_path, columns=['symbol_id', 'date', 'high', 'low', 'close'])
    px['date'] = px['date'].astype(str)
    px = px.sort_values(['symbol_id', 'date'], kind='stable').reset_index(drop=True)

    sid = px['symbol_id'].to_numpy()
    high = px['high'].to_numpy(np.float64)
    low = px['low'].to_numpy(np.float64)
    close = px['close'].to_numpy(np.float64)

    buf = {c: np.full(len(px), np.nan) for c in columns}
    need_structure = ('sp_pivot' in columns) or ('sp_hl' in columns)

    bounds = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1], True])
    for a, z in zip(bounds[:-1], bounds[1:]):
        if need_structure:
            p, h = structure_pivot_series(high[a:z], low[a:z], close[a:z], min_len, max_len)
            if 'sp_pivot' in buf:
                buf['sp_pivot'][a:z] = p
            if 'sp_hl' in buf:
                buf['sp_hl'][a:z] = h
        if 'sp_counter' in buf:
            buf['sp_counter'][a:z] = counter_trend_series(
                high[a:z], low[a:z], close[a:z], min_len, max_len)

    out = px[['symbol_id', 'date']].copy()
    for c in columns:
        out[c] = buf[c]
    n_defined = int(np.isfinite(buf[columns[0]]).sum())
    _log(f'構造ピボット算出: {len(out):,} 行 / 銘柄 {len(bounds) - 1:,} / '
         f'構造が生きている行 {n_defined:,} ({n_defined / max(len(out), 1) * 100:.1f}%)')
    return out


def backfill_indicators(indicators_path: str, sp_df: pd.DataFrame, out_path: str,
                        columns: tuple) -> dict:
    """indicators を row group ごとに読み、指定された列を足して書き出す。

    **既存カラムは arrow の Table のまま素通しする。** pandas に変換しないので
    dtype も値も変わらない。
    """
    pf = pq.ParquetFile(indicators_path)
    existing = set(pf.schema_arrow.names)
    for col in columns:
        if col in existing:
            raise SystemExit(f'既に {col} が存在します。作り直す場合は元世代から実行してください。')

    lookup = sp_df.set_index(['symbol_id', 'date'])
    writer = None
    total, matched = 0, 0
    try:
        for i in range(pf.metadata.num_row_groups):
            table = pf.read_row_group(i)
            keys = pd.DataFrame({
                'symbol_id': table.column('symbol_id').to_numpy(zero_copy_only=False),
                'date': table.column('date').to_pylist(),
            })
            keys['date'] = keys['date'].astype(str)
            joined = keys.join(lookup, on=['symbol_id', 'date'])

            for col in columns:
                table = table.append_column(
                    col, pa.array(joined[col].to_numpy(np.float64), pa.float64()))

            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema, compression='snappy')
            writer.write_table(table)

            total += table.num_rows
            matched += int(joined[columns[0]].notna().sum())
            _log(f'  row group {i + 1}/{pf.metadata.num_row_groups}: {table.num_rows:,} 行')
    finally:
        if writer is not None:
            writer.close()
    return {'rows': total, 'defined': matched}


def verify(original_path: str, new_path: str, columns: tuple) -> None:
    """既存カラムが1つも変わっていないことを検査する。

    構造的に変わらない作りではあるが、**「変わらないはず」で済ませない**。
    行数・スキーマ・サンプル行の値を実際に突き合わせる。
    """
    old_pf, new_pf = pq.ParquetFile(original_path), pq.ParquetFile(new_path)
    assert old_pf.metadata.num_rows == new_pf.metadata.num_rows, (
        f'行数が変わった: {old_pf.metadata.num_rows:,} -> {new_pf.metadata.num_rows:,}')

    old_names, new_names = old_pf.schema_arrow.names, new_pf.schema_arrow.names
    assert new_names == list(old_names) + list(columns), (
        f'カラム構成が想定と違う: {set(new_names) - set(old_names)}')
    for name in old_names:
        assert old_pf.schema_arrow.field(name).type == new_pf.schema_arrow.field(name).type, (
            f'{name} の型が変わった')

    # 先頭 row group を全カラム突合（値の同一性）
    old_t = old_pf.read_row_group(0)
    new_t = new_pf.read_row_group(0).select(list(old_names))
    assert old_t.equals(new_t), '先頭 row group の既存カラムに差分がある'
    _log(f'検査 OK: {old_pf.metadata.num_rows:,} 行 / 既存 {len(old_names)} 列は不変 '
         f'/ 追加 {list(columns)}')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source-dir', required=True, help='読み込む parquet_master ディレクトリ')
    ap.add_argument('--out-dir', required=True, help='新しい indicators を書くディレクトリ')
    ap.add_argument('--min-len', type=int, default=DEFAULT_MIN_LEN)
    ap.add_argument('--max-len', type=int, default=DEFAULT_MAX_LEN)
    ap.add_argument('--columns', nargs='+', default=list(ALL_COLUMNS), choices=list(ALL_COLUMNS),
                    help='追加する列。既に本番に入っている列は指定しないこと')
    ap.add_argument('--apply', action='store_true',
                    help='latest_master.json を新世代へ差し替える（付けなければポインタは動かさない）')
    args = ap.parse_args()

    t0 = time.perf_counter()
    pointer = os.path.join(args.source_dir, 'latest_master.json')
    with open(pointer, encoding='utf-8') as f:
        latest = json.load(f)
    _log(f'元世代: {os.path.basename(latest["indicators"])}')

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(args.out_dir, f'indicators_{stamp}.parquet')

    columns = tuple(args.columns)
    _log(f'追加する列: {list(columns)}')
    sp_df = compute_structure_columns(latest['prices'], args.min_len, args.max_len, columns)
    stats = backfill_indicators(latest['indicators'], sp_df, out_path, columns)
    _log(f'書き出し: {out_path}')
    _log(f'  {stats["rows"]:,} 行 / 値が入った行 {stats["defined"]:,} '
         f'({stats["defined"] / max(stats["rows"], 1) * 100:.1f}%)')

    verify(latest['indicators'], out_path, columns)

    if args.apply:
        new_latest = dict(latest)
        new_latest['indicators'] = os.path.abspath(out_path)
        tmp = pointer + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(new_latest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, pointer)
        _log('latest_master.json を差し替えました（旧世代のファイルは削除していません）')
    else:
        # ポインタを動かさない場合も、Sandbox で読めるよう out-dir 側に世代を作っておく
        if os.path.abspath(args.out_dir) != os.path.abspath(args.source_dir):
            new_latest = dict(latest)
            new_latest['indicators'] = os.path.abspath(out_path)
            with open(os.path.join(args.out_dir, 'latest_master.json'), 'w', encoding='utf-8') as f:
                json.dump(new_latest, f, ensure_ascii=False, indent=2)
            _log('out-dir に latest_master.json を作成しました（本番ポインタは無変更）')
        else:
            _log('ポインタは変更していません（--apply 未指定）')

    _log(f'完了 {time.perf_counter() - t0:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
