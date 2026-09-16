"""Parquet マスターの indicators に Direction via Zone Break の4列を全期間バックフィルする。

`backend/scripts/backfill_structure_pivot.py` と同じ設計（同スクリプトの docstring §「なぜ
専用スクリプトが要るのか」/「なぜ全指標の再計算ではなく列の追記なのか」参照）を踏襲する。

## 使い方

    # Sandbox 検証（本番を読み、Sandbox へ新世代を書く。本番は無変更）
    python backend/scripts/backfill_zone_break.py \
        --source-dir "data/parquet_master" --out-dir "data/sandbox/parquet_master"

    # 本番へ適用（ユーザー実施。API サーバーと日次更新を止めてから）
    python backend/scripts/backfill_zone_break.py \
        --source-dir "data/parquet_master" --out-dir "data/parquet_master" --apply

`--apply` を付けたときだけ `latest_master.json` を差し替える。付けなければ
新しい indicators ファイルを書くだけで、ポインタは動かさない（本番は無傷）。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from indicators.zone_break import zone_break_series  # noqa: E402

#: バックフィルする列の全体（doc/completed/zone_break_plan.md §3.2）
ALL_COLUMNS = ('is_zone_break_bull', 'zb_ssl', 'zb_bsl', 'is_zone_break_weak')
_BOOL_COLUMNS = {'is_zone_break_bull', 'is_zone_break_weak'}


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def compute_zone_break_columns(prices_path: str, columns: tuple) -> pd.DataFrame:
    """価格マスターから全銘柄の (symbol_id, date, <columns>) を作る。

    銘柄ごとに日付昇順で計算する。確定遅延は無い（zone_break_series 側の仕様、
    doc/completed/zone_break_plan.md §3.1 参照）。
    """
    px = pd.read_parquet(prices_path, columns=['symbol_id', 'date', 'high', 'low', 'close'])
    px['date'] = px['date'].astype(str)
    px = px.sort_values(['symbol_id', 'date'], kind='stable').reset_index(drop=True)

    sid = px['symbol_id'].to_numpy()
    high = px['high'].to_numpy(np.float64)
    low = px['low'].to_numpy(np.float64)
    close = px['close'].to_numpy(np.float64)

    buf = {
        'is_zone_break_bull': np.zeros(len(px), dtype=bool),
        'zb_ssl': np.full(len(px), np.nan),
        'zb_bsl': np.full(len(px), np.nan),
        'is_zone_break_weak': np.zeros(len(px), dtype=bool),
    }

    bounds = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1], True])
    for a, z in zip(bounds[:-1], bounds[1:]):
        is_bull, ssl, bsl, is_weak = zone_break_series(high[a:z], low[a:z], close[a:z])
        buf['is_zone_break_bull'][a:z] = is_bull
        buf['zb_ssl'][a:z] = ssl
        buf['zb_bsl'][a:z] = bsl
        buf['is_zone_break_weak'][a:z] = is_weak

    out = px[['symbol_id', 'date']].copy()
    for c in columns:
        out[c] = buf[c]
    # SSL/BSL は未確定の間 0.0（confirmed 状態なので isfinite ではなく >0 で「値が入っている」を測る）
    n_defined = int((buf['zb_ssl'] > 0.0).sum())
    _log(f'zone_break算出: {len(out):,} 行 / 銘柄 {len(bounds) - 1:,} / '
         f'SSL/BSL確定済み行 {n_defined:,} ({n_defined / max(len(out), 1) * 100:.1f}%)')
    return out


def backfill_indicators(indicators_path: str, zb_df: pd.DataFrame, out_path: str,
                        columns: tuple) -> dict:
    """indicators を row group ごとに読み、指定された列を足して書き出す。

    **既存カラムは arrow の Table のまま素通しする。** pandas に変換しないので
    dtype も値も変わらない（`backfill_structure_pivot.py` と同じ安全策）。
    """
    pf = pq.ParquetFile(indicators_path)
    existing = set(pf.schema_arrow.names)
    for col in columns:
        if col in existing:
            raise SystemExit(f'既に {col} が存在します。作り直す場合は元世代から実行してください。')

    lookup = zb_df.set_index(['symbol_id', 'date'])
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
                if col in _BOOL_COLUMNS:
                    arr = pa.array(joined[col].fillna(False).to_numpy(bool), pa.bool_())
                else:
                    arr = pa.array(joined[col].to_numpy(np.float64), pa.float64())
                table = table.append_column(col, arr)

            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema, compression='snappy')
            writer.write_table(table)

            total += table.num_rows
            # columns[0] は --columns で選ばれた列のいずれか。zb_ssl 固定だと --columns で
            # zb_ssl/zb_bsl を除外したときに KeyError になる（2026-09-16 code-review Angle A で検出）
            matched += int(joined[columns[0]].notna().sum())
            _log(f'  row group {i + 1}/{pf.metadata.num_row_groups}: {table.num_rows:,} 行')
    finally:
        if writer is not None:
            writer.close()
    return {'rows': total, 'defined': matched}


def verify(original_path: str, new_path: str, columns: tuple) -> None:
    """既存カラムが1つも変わっていないことを検査する。

    構造的に変わらない作りではあるが、**「変わらないはず」で済ませない**
    （`backfill_structure_pivot.py` と同じ方針）。
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
    zb_df = compute_zone_break_columns(latest['prices'], columns)
    stats = backfill_indicators(latest['indicators'], zb_df, out_path, columns)
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
