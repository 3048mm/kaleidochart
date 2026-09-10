"""Optuna の study をまとめて削除する（汚染データで回した試行の破棄用）。

## なぜ必要か

バックテストは Parquet マスターを直読みする。マスターが壊れている間に回した
最適化は、**目的関数の値そのものが無意味**になる。それでも Optuna は
`COMPLETE` として記録するため、次回の最適化でサンプラーがその誤った結果を
学習に使い、探索が誤った方向へ誘導される。

    2026-09-01 08:56  rotate のマージが OOM し、indicators が
                      6,076,932行 → 1,594,632行 に切り詰められた
                      （2021〜2024前半の指標が消失）
    → それ以降に回した 9 study / 1,751 trial は全て破棄が必要

## 安全設計

- 実行前に `VACUUM INTO` で `data/optimization_trials_backup_<ts>.db` を作る
- 既定はドライラン。`--apply` を付けたときだけ削除する
- 存在しない study 名は警告するだけで、他の削除は続行する

## 使い方

    python tools/delete_optuna_studies.py --studies A,B,C          # ドライラン
    python tools/delete_optuna_studies.py --studies A,B,C --apply
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.join('data', 'optimization_trials.db')


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def summarize(db_path: str, names: list) -> dict:
    """study ごとの trial 数を返す（存在しないものは None）。"""
    con = sqlite3.connect(db_path)
    try:
        out = {}
        for n in names:
            row = con.execute(
                'SELECT s.study_id, COUNT(t.trial_id) FROM studies s '
                'LEFT JOIN trials t ON t.study_id = s.study_id '
                'WHERE s.study_name = ? GROUP BY s.study_id', (n,)).fetchone()
            out[n] = None if row is None else row[1]
        return out
    finally:
        con.close()


def backup(db_path: str) -> str:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(os.path.dirname(db_path),
                        f'optimization_trials_backup_{stamp}.db')
    con = sqlite3.connect(db_path, timeout=120.0)
    try:
        con.execute('PRAGMA busy_timeout=120000')
        con.execute('VACUUM INTO ?', (dest,))
    finally:
        con.close()
    _log(f'バックアップ: {dest} ({os.path.getsize(dest) / 1e6:.0f} MB)')
    return dest


def main(names: list, apply: bool) -> int:
    if not os.path.exists(DB_PATH):
        print(f'{DB_PATH} が見つかりません。プロジェクトルートで実行してください。')
        return 1

    counts = summarize(DB_PATH, names)
    print('=== 削除対象 ===')
    total = 0
    missing = []
    for n in names:
        c = counts[n]
        if c is None:
            missing.append(n)
            print(f'  {n:38s} （存在しません・スキップ）')
        else:
            total += c
            print(f'  {n:38s} {c:5d} trial')
    print(f'  ---- 合計 {total:,} trial / {len(names) - len(missing)} study')

    if not apply:
        print('\n--apply が無いのでドライランです。削除するには --apply を付けてください。')
        return 0
    if total == 0:
        print('\n削除対象がありません。')
        return 0

    backup(DB_PATH)

    import optuna
    from optuna.storages import RDBStorage
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    storage = RDBStorage(url=f'sqlite:///{os.path.abspath(DB_PATH)}',
                         engine_kwargs={'connect_args': {'timeout': 120.0}})

    deleted = 0
    for n in names:
        if counts[n] is None:
            continue
        try:
            optuna.delete_study(study_name=n, storage=storage)
            deleted += 1
            _log(f'  削除: {n} ({counts[n]} trial)')
        except Exception as e:
            _log(f'  失敗: {n} — {e}')

    after = summarize(DB_PATH, names)
    remain = {k: v for k, v in after.items() if v is not None}
    print()
    _log(f'{deleted} study を削除しました。')
    if remain:
        print('  !! 残存:', remain)
    else:
        print('  対象の study はすべて消えています。')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--studies', required=True,
                    help='カンマ区切りの study 名')
    ap.add_argument('--apply', action='store_true', help='実際に削除する')
    a = ap.parse_args()
    raise SystemExit(main([s.strip() for s in a.studies.split(',') if s.strip()], a.apply))
