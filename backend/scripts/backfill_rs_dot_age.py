"""Parquet マスターの indicators を rs_blue_dot_age / rs_red_dot_age へ移行する。

## なぜ専用スクリプトが要るのか

`tools/deploy_after_merge.ps1 -RebuildFrom T3` は **ホット期間（730日）しか再計算しない**
（`backend/scripts/deploy_after_merge.py` L113 のコメント）。既存カラムの値を直すだけなら
それで足りるが、**新規カラムでは Parquet の残り5年分が NULL のまま残る**。
バックテスト期間は5年あるため、そのままでは大半が欠損して使い物にならない。

## なぜ「全指標の再計算」ではなく「2列の入れ替え」なのか

経過日数カウンタは **既存の is_rs_blue_dot / is_rs_red_dot だけ**から決まる
（点灯の事実は既に Parquet に記録されている）。`pipeline.parquet_recompute` は全 63 列を
作り直すため、過去に指標ロジックが変わっていた場合に**既存カラムの値まで動く**危険がある。

本スクリプトは **既存カラムを一切再計算せず、arrow の Table のまま素通しする**。
pandas を経由しないので dtype も変わらない。旧2列を drop し、新2列を append するだけ。

## 生成規則（doc/completed/rs_dot_age_plan.md §3.1）

    0=当日点灯 / n=n営業日前に点灯 / 999=未点灯・無効
      - 再点灯で 0 に戻す / 上限 60 超で 999 / 反対ドット点灯で即 999
      - 履歴 252 本未満は 999

規則の実装は `indicators.relative_strength.compute_rs_dot_age` **1箇所だけ**にしてある。
T3（前方計算）と本スクリプト（過去データ）で規則がずれないようにするため。

## 使い方

    # Sandbox 検証（本番を読み、Sandbox へ新世代を書く。本番は無変更）
    python backend/scripts/backfill_rs_dot_age.py \
        --source-dir "data/parquet_master" --out-dir "data/sandbox/parquet_master"

    # 本番へ適用（ユーザー実施。API サーバーと日次更新を止めてから）
    python backend/scripts/backfill_rs_dot_age.py \
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators.relative_strength import (  # noqa: E402
    RS_DOT_AGE_MAX, RS_DOT_AGE_NONE, RS_DOT_WARMUP_BARS, compute_rs_dot_age,
)

OLD_COLUMNS = ('is_rs_blue_dot', 'is_rs_red_dot')
NEW_COLUMNS = ('rs_blue_dot_age', 'rs_red_dot_age')


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def compute_age_columns(indicators_path: str) -> pd.DataFrame:
    """既存の点灯フラグから全銘柄の (symbol_id, date, *NEW_COLUMNS) を作る。"""
    df = pd.read_parquet(indicators_path,
                         columns=['symbol_id', 'date', *OLD_COLUMNS])
    df['date'] = df['date'].astype(str)
    df = df.sort_values(['symbol_id', 'date'], kind='stable').reset_index(drop=True)

    sid = df['symbol_id'].to_numpy()
    blue = np.nan_to_num(df[OLD_COLUMNS[0]].to_numpy(dtype=np.float64), nan=0.0) > 0.5
    red = np.nan_to_num(df[OLD_COLUMNS[1]].to_numpy(dtype=np.float64), nan=0.0) > 0.5

    n = len(df)
    blue_age = np.full(n, RS_DOT_AGE_NONE, np.int32)
    red_age = np.full(n, RS_DOT_AGE_NONE, np.int32)
    bounds = np.flatnonzero(np.r_[True, sid[1:] != sid[:-1], True])
    for a, z in zip(bounds[:-1], bounds[1:]):
        ba, ra = compute_rs_dot_age(blue[a:z], red[a:z])
        blue_age[a:z], red_age[a:z] = ba, ra

    out = df[['symbol_id', 'date']].copy()
    out[NEW_COLUMNS[0]] = blue_age
    out[NEW_COLUMNS[1]] = red_age

    lit = int((blue_age == 0).sum())
    within = int((blue_age <= 10).sum())
    _log(f'経過日数算出: {n:,} 行 / 銘柄 {len(bounds) - 1:,}')
    _log(f'  ブルー: 当日点灯 {lit:,} 行 / 10日以内 {within:,} 行 '
         f'({within / max(n, 1) * 100:.2f}%)')
    _log(f'  レッド: 当日点灯 {int((red_age == 0).sum()):,} 行')
    return out


def backfill_indicators(indicators_path: str, age_df: pd.DataFrame, out_path: str) -> dict:
    """indicators を row group ごとに読み、旧2列を落として新2列を足す。

    **他の既存カラムは arrow の Table のまま素通しする。** pandas に変換しないので
    dtype も値も変わらない。
    """
    pf = pq.ParquetFile(indicators_path)
    existing = set(pf.schema_arrow.names)
    for col in OLD_COLUMNS:
        if col not in existing:
            raise SystemExit(f'{col} が見つかりません。既に移行済みの世代の可能性があります。')
    for col in NEW_COLUMNS:
        if col in existing:
            raise SystemExit(f'既に {col} が存在します。作り直す場合は元世代から実行してください。')

    lookup = age_df.set_index(['symbol_id', 'date'])
    writer = None
    total, unmatched = 0, 0
    try:
        for i in range(pf.metadata.num_row_groups):
            table = pf.read_row_group(i)
            keys = pd.DataFrame({
                'symbol_id': table.column('symbol_id').to_numpy(zero_copy_only=False),
                'date': table.column('date').to_pylist(),
            })
            keys['date'] = keys['date'].astype(str)
            joined = keys.join(lookup, on=['symbol_id', 'date'])
            unmatched += int(joined[NEW_COLUMNS[0]].isna().sum())

            table = table.drop_columns(list(OLD_COLUMNS))
            for col in NEW_COLUMNS:
                # 未マッチは起こらないはずだが、起きても NULL にせず未点灯に倒す
                vals = joined[col].fillna(RS_DOT_AGE_NONE).to_numpy(np.int16)
                table = table.append_column(col, pa.array(vals, pa.int16()))

            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema, compression='snappy')
            writer.write_table(table)

            total += table.num_rows
            _log(f'  row group {i + 1}/{pf.metadata.num_row_groups}: {table.num_rows:,} 行')
    finally:
        if writer is not None:
            writer.close()

    if unmatched:
        raise SystemExit(f'突合できない行が {unmatched:,} 件ありました。中断します。')
    return {'rows': total}


def verify(original_path: str, new_path: str) -> None:
    """旧2列以外が1つも変わっていないことを検査する。

    構造的に変わらない作りではあるが、**「変わらないはず」で済ませない**。
    行数・スキーマ・先頭 row group の値を実際に突き合わせる。
    """
    old_pf, new_pf = pq.ParquetFile(original_path), pq.ParquetFile(new_path)
    assert old_pf.metadata.num_rows == new_pf.metadata.num_rows, (
        f'行数が変わった: {old_pf.metadata.num_rows:,} -> {new_pf.metadata.num_rows:,}')

    kept = [n for n in old_pf.schema_arrow.names if n not in OLD_COLUMNS]
    assert new_pf.schema_arrow.names == kept + list(NEW_COLUMNS), (
        f'カラム構成が想定と違う: {new_pf.schema_arrow.names[-4:]}')
    for name in kept:
        assert old_pf.schema_arrow.field(name).type == new_pf.schema_arrow.field(name).type, (
            f'{name} の型が変わった')

    old_t = old_pf.read_row_group(0).select(kept)
    new_t = new_pf.read_row_group(0).select(kept)
    assert old_t.equals(new_t), '先頭 row group の既存カラムに差分がある'

    # 新カラムの値域（NULL が無いこと・0..MAX か番兵のみ）
    for col in NEW_COLUMNS:
        arr = new_pf.read(columns=[col]).column(col).to_numpy(zero_copy_only=False)
        assert not pd.isna(arr).any(), f'{col} に NULL がある'
        bad = arr[(arr < 0) | ((arr > RS_DOT_AGE_MAX) & (arr != RS_DOT_AGE_NONE))]
        assert bad.size == 0, f'{col} に想定外の値: {np.unique(bad)[:10]}'

    _log(f'検査 OK: {old_pf.metadata.num_rows:,} 行 / 既存 {len(kept)} 列は不変 '
         f'/ {list(OLD_COLUMNS)} -> {list(NEW_COLUMNS)}')


def verify_against_old_flags(original_path: str, new_path: str) -> None:
    """旧フラグとの整合を検査する（計画書 §6.1）。

    旧 `is_rs_blue_dot == 1` の行は、履歴 252 本ガードで落とした分を除き
    新 `rs_blue_dot_age == 0` と一致しなければならない。
    """
    old = pd.read_parquet(original_path, columns=['symbol_id', 'date', *OLD_COLUMNS])
    new = pd.read_parquet(new_path, columns=['symbol_id', 'date', *NEW_COLUMNS])
    old['date'] = old['date'].astype(str)
    new['date'] = new['date'].astype(str)
    m = old.merge(new, on=['symbol_id', 'date'], how='inner')
    assert len(m) == len(old), '突合で行が落ちた'

    for old_col, new_col, label in ((OLD_COLUMNS[0], NEW_COLUMNS[0], 'ブルー'),
                                    (OLD_COLUMNS[1], NEW_COLUMNS[1], 'レッド')):
        lit_old = np.nan_to_num(m[old_col].to_numpy(dtype=np.float64), nan=0.0) > 0.5
        lit_new = m[new_col].to_numpy() == 0
        dropped = int((lit_old & ~lit_new).sum())
        added = int((~lit_old & lit_new).sum())
        _log(f'  {label}: 旧点灯 {int(lit_old.sum()):,} / 新 age==0 {int(lit_new.sum()):,} '
             f'/ ガードで落ちた {dropped:,} / **増えた {added:,}（0 でなければならない）**')
        assert added == 0, f'{label}: 旧フラグで点灯していない行が age==0 になっている'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source-dir', required=True, help='読み込む parquet_master ディレクトリ')
    ap.add_argument('--out-dir', required=True, help='新しい indicators を書くディレクトリ')
    ap.add_argument('--apply', action='store_true',
                    help='latest_master.json を新世代へ差し替える（付けなければポインタは動かさない）')
    args = ap.parse_args()

    t0 = time.perf_counter()
    pointer = os.path.join(args.source_dir, 'latest_master.json')
    with open(pointer, encoding='utf-8') as f:
        latest = json.load(f)
    _log(f'元世代: {os.path.basename(latest["indicators"])}')
    _log(f'規則: cap={RS_DOT_AGE_MAX} / sentinel={RS_DOT_AGE_NONE} / '
         f'warmup={RS_DOT_WARMUP_BARS}')

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(args.out_dir, f'indicators_{stamp}.parquet')

    age_df = compute_age_columns(latest['indicators'])
    stats = backfill_indicators(latest['indicators'], age_df, out_path)
    _log(f'書き出し: {out_path}（{stats["rows"]:,} 行）')

    verify(latest['indicators'], out_path)
    verify_against_old_flags(latest['indicators'], out_path)

    new_latest = dict(latest)
    new_latest['indicators'] = os.path.abspath(out_path)
    if args.apply:
        tmp = pointer + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(new_latest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, pointer)
        _log('latest_master.json を差し替えました（旧世代のファイルは削除していません）')
    elif os.path.abspath(args.out_dir) != os.path.abspath(args.source_dir):
        # Sandbox から読めるよう out-dir 側にポインタを作る（本番のポインタは動かさない）
        with open(os.path.join(args.out_dir, 'latest_master.json'), 'w', encoding='utf-8') as f:
            json.dump(new_latest, f, ensure_ascii=False, indent=2)
        _log('out-dir 側に latest_master.json を書きました（本番のポインタは無変更）')
    else:
        _log('--apply が無いのでポインタは動かしていません')

    _log(f'所要 {time.perf_counter() - t0:.1f} 秒')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
