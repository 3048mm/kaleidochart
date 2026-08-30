"""Parquet マスターの不要な旧世代を削除する（ポインタ参照を見て安全に）。

## なぜ専用スクリプトが要るのか

既存の `parquet_cache_manager.clean_old_parquet_versions()` は
**`data_version_*.json` 単位でしか掃除しない**（新しい方から `keep_count` 世代を残し、
残りのポインタが列挙するファイルを消す）。

ところが `promote_*.py` 系の昇格スクリプトは、**Parquet ファイルだけを書いて
`data_version_*.json` を作らない**。そのため昇格で生まれた世代は
どのポインタにも属さず、**組み込みの掃除では永久に残る**。

実測（2026-08-30）: `data/parquet_master` が 17 GB。`data_version_*.json` は 2 個しか
無いのに indicators は 6 世代あり、うち 4 世代（`145812` / `151026` / `155707` /
`20260826_180646`）がどのポインタにも属していなかった。

## 安全設計

1. **`latest_master.json` が参照しているファイルは絶対に消さない**（現行世代の保護）。
   ポインタが読めない場合は何もせず中断する
2. `data_version_*.json` が示す世代のうち、**新しい方から `--keep` 個を保護**する
3. 上記のいずれにも属さない孤児ファイルは削除候補にする
4. **更新から `--grace-minutes` 以内のファイルは消さない**（長時間走っている
   バックテストが読んでいる可能性があるため。組み込み実装の15分猶予と同じ思想）
5. 既定はドライラン。`--apply` を付けたときだけ削除する

> [!WARNING]
> **実行前に `tools/backup_production_data.py --apply` を流すこと。**
> Parquet の MVCC 旧世代はバックアップを兼ねている（`agent_execution_rules.md` §10.1）。
> prune はその保険を外す操作なので、退避を取ってからにする。

## 使い方

    python tools/prune_parquet_generations.py                 # ドライラン
    python tools/prune_parquet_generations.py --keep 1        # 保護世代を1つに
    python tools/prune_parquet_generations.py --apply         # 実行
"""
import argparse
import glob
import json
import os
import time
from datetime import datetime

PARQUET_DIR = os.path.join('data', 'parquet_master')


def _mb(n: int) -> str:
    return f'{n / 1024 / 1024:,.0f} MB'


def main(keep: int, grace_minutes: int, apply: bool) -> int:
    pointer = os.path.join(PARQUET_DIR, 'latest_master.json')
    if not os.path.exists(pointer):
        raise SystemExit(f'{pointer} が読めません。安全のため中断します。')
    with open(pointer, encoding='utf-8') as f:
        latest = json.load(f)

    # --- 1. 現行世代（絶対保護） ---
    protected = {os.path.basename(p) for p in latest.values()}
    print('=== 現行世代（latest_master.json・絶対保護）===')
    for k, v in sorted(latest.items()):
        print(f'  {k:11s}: {os.path.basename(v)}')

    # --- 2. data_version ポインタが示す世代のうち新しい方から keep 個 ---
    version_files = sorted(glob.glob(os.path.join(PARQUET_DIR, 'data_version_*.json')))
    kept_versions = version_files[-keep:] if keep > 0 else []
    for vf in kept_versions:
        try:
            with open(vf, encoding='utf-8') as f:
                protected |= {os.path.basename(p) for p in json.load(f).values()}
        except Exception as e:
            raise SystemExit(f'{vf} が読めません（{e}）。安全のため中断します。')
    print(f'\n=== data_version ポインタ {len(version_files)} 個中、新しい {len(kept_versions)} 個を保護 ===')
    for vf in version_files:
        mark = '保護' if vf in kept_versions else '削除候補'
        print(f'  [{mark}] {os.path.basename(vf)}')

    # --- 3. 削除候補の洗い出し ---
    now = time.time()
    grace = grace_minutes * 60
    all_files = sorted(glob.glob(os.path.join(PARQUET_DIR, '*.parquet')))
    delete, skipped_recent = [], []
    for p in all_files:
        if os.path.basename(p) in protected:
            continue
        if now - os.path.getmtime(p) < grace:
            skipped_recent.append(p)
            continue
        delete.append(p)

    # 保護されない data_version ポインタも一緒に消す（対象ファイルが消えるため）
    delete_pointers = [vf for vf in version_files if vf not in kept_versions
                       and now - os.path.getmtime(vf) >= grace]

    print(f'\n=== 削除候補 {len(delete)} ファイル ===')
    freed = 0
    for p in delete:
        s = os.path.getsize(p)
        freed += s
        print(f'  {os.path.basename(p):48s} {_mb(s):>12s}  '
              f'({datetime.fromtimestamp(os.path.getmtime(p)):%m-%d %H:%M})')
    for vf in delete_pointers:
        print(f'  {os.path.basename(vf):48s} {"(pointer)":>12s}')
    print(f'\n  解放される容量: {_mb(freed)}')

    if skipped_recent:
        print(f'\n=== 猶予期間({grace_minutes}分)内のためスキップ {len(skipped_recent)} 件 ===')
        for p in skipped_recent:
            print(f'  {os.path.basename(p)}')

    kept = [p for p in all_files if os.path.basename(p) in protected]
    print(f'\n=== 残すファイル {len(kept)} 件 / 合計 '
          f'{_mb(sum(os.path.getsize(p) for p in kept))} ===')
    for p in kept:
        print(f'  {os.path.basename(p)}')

    if not delete and not delete_pointers:
        print('\n削除対象はありません。')
        return 0
    if not apply:
        print('\n--apply が無いのでドライランです。削除するには --apply を付けてください。')
        print('**実行前に tools/backup_production_data.py --apply を流してください。**')
        return 0

    removed = 0
    for p in delete + delete_pointers:
        try:
            size = os.path.getsize(p)
            os.remove(p)
            removed += size
        except Exception as e:
            # 他プロセスが開いている場合はスキップ（次回の実行で消える）
            print(f'  スキップ（削除できず）: {os.path.basename(p)} — {e}')
    print(f'\n削除しました。解放: {_mb(removed)}')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', type=int, default=1,
                    help='data_version ポインタを新しい方から何世代保護するか（既定 1）')
    ap.add_argument('--grace-minutes', type=int, default=60,
                    help='この分数以内に更新されたファイルは消さない（既定 60）')
    ap.add_argument('--apply', action='store_true', help='実際に削除する（無ければドライラン）')
    a = ap.parse_args()
    raise SystemExit(main(a.keep, a.grace_minutes, a.apply))
