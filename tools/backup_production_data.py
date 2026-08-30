"""本番の DB と Parquet マスターを `data/_bk_<timestamp>/` へ退避する。

## なぜ必要か

- **DB / Parquet は git に乗らない**（`agent_execution_rules.md` §10）。コードは
  ブランチで戻せるが、データは退避しておかないと戻せない。
- Parquet の MVCC 旧世代がバックアップを兼ねているが、**世代を prune すると
  その保険が消える**。prune の前には必ずこのスクリプトを流すこと。
- 2026-08-02 に 17銘柄・8年分の履歴を失った前例がある（`data/_bk/` に偶然
  残っていた旧世代で復旧できた）。詳細は
  `backend/scripts/archive_parquet_master.py` の docstring。

## 何を退避するか

| 対象 | 方式 | 理由 |
| :--- | :--- | :--- |
| `stocktool.db` | `VACUUM INTO` | 再生成可能だが再構築に時間がかかる。**API サーバー稼働中でも安全に一貫コピーが取れる** |
| `user_data.db` | `VACUUM INTO` | **ユーザー資産**（ウォッチリスト・ポートフォリオ）。再生成不可 |
| `universe.db` | `VACUUM INTO` | **ユーザー資産**（銘柄定義の手動編集・改称履歴）。再生成不可 |
| `optimization_trials.db` | `VACUUM INTO` | **ユーザー資産**（Optuna の試行履歴。約12時間の計算結果） |
| Parquet | ファイルコピー | `latest_master.json` が参照している**現行世代のみ**。旧世代は prune 対象なので退避しない |

`VACUUM INTO` を使うのは、単純なファイルコピーだと WAL と本体が食い違った
不整合なスナップショットになりうるため。読み取り接続と共存できる。

## 使い方

    python tools/backup_production_data.py            # ドライラン（対象と容量の確認）
    python tools/backup_production_data.py --apply    # 実行

退避先は `data/_bk_<YYYYMMDD_HHMMSS>/`。既存の `data/_bk_*` と同じ命名。
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

DATA_DIR = 'data'
PARQUET_DIR = os.path.join(DATA_DIR, 'parquet_master')
DATABASES = ('stocktool.db', 'user_data.db', 'universe.db', 'optimization_trials.db')


def _log(msg: str) -> None:
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def _mb(n: int) -> str:
    return f'{n / 1024 / 1024:,.0f} MB'


def collect_targets() -> tuple:
    """(DB のリスト, Parquet のリスト, 合計バイト数) を返す。"""
    dbs = []
    for name in DATABASES:
        p = os.path.join(DATA_DIR, name)
        if os.path.exists(p):
            dbs.append((p, os.path.getsize(p)))
        else:
            _log(f'  (見つからないのでスキップ: {p})')

    pointer = os.path.join(PARQUET_DIR, 'latest_master.json')
    if not os.path.exists(pointer):
        raise SystemExit(f'{pointer} が見つかりません。中断します。')
    with open(pointer, encoding='utf-8') as f:
        latest = json.load(f)

    parquets = [(pointer, os.path.getsize(pointer))]
    for key, path in latest.items():
        abs_path = os.path.join(PARQUET_DIR, os.path.basename(path))
        if not os.path.exists(abs_path):
            raise SystemExit(f'ポインタが指すファイルがありません: {abs_path}')
        parquets.append((abs_path, os.path.getsize(abs_path)))

    total = sum(s for _, s in dbs) + sum(s for _, s in parquets)
    return dbs, parquets, total


def backup_sqlite(src: str, dst: str) -> None:
    """VACUUM INTO で一貫したスナップショットを取る（読み取り接続と共存可）。"""
    con = sqlite3.connect(src, timeout=120.0)
    try:
        con.execute('PRAGMA busy_timeout=120000')
        con.execute('VACUUM INTO ?', (dst,))
    finally:
        con.close()


def main(apply: bool) -> int:
    dbs, parquets, total = collect_targets()

    print()
    print('=== 退避対象 ===')
    for p, s in dbs:
        print(f'  [DB]      {os.path.basename(p):32s} {_mb(s):>12s}')
    for p, s in parquets:
        print(f'  [Parquet] {os.path.basename(p):32s} {_mb(s):>12s}')
    print(f'  {"合計":38s} {_mb(total):>12s}')

    free = shutil.disk_usage(os.path.abspath(DATA_DIR)).free
    print(f'\n  空きディスク: {free / 1e9:,.0f} GB')
    if free < total * 1.2:
        raise SystemExit('空き容量が不足しています（必要量の1.2倍を確保してください）。中断します。')

    if not apply:
        print('\n--apply が無いのでドライランです。実行するには --apply を付けてください。')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(DATA_DIR, f'_bk_{stamp}')
    os.makedirs(dest, exist_ok=False)
    _log(f'退避先: {dest}')

    t0 = time.perf_counter()
    for p, s in dbs:
        _log(f'  VACUUM INTO {os.path.basename(p)} ({_mb(s)}) ...')
        backup_sqlite(p, os.path.join(dest, os.path.basename(p)))
    for p, s in parquets:
        _log(f'  copy {os.path.basename(p)} ({_mb(s)}) ...')
        shutil.copy2(p, os.path.join(dest, os.path.basename(p)))

    # 何を退避したかを残す（後から「どの世代か」を追えるようにする）
    manifest = {
        'created_at': datetime.now().isoformat(),
        'databases': [os.path.basename(p) for p, _ in dbs],
        'parquet_generation': {k: os.path.basename(v) for k, v in
                               json.load(open(os.path.join(PARQUET_DIR, 'latest_master.json'),
                                              encoding='utf-8')).items()},
    }
    with open(os.path.join(dest, 'BACKUP_MANIFEST.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    actual = sum(os.path.getsize(os.path.join(dest, n)) for n in os.listdir(dest))
    _log(f'完了: {len(dbs)} DB + {len(parquets)} ファイル / 実サイズ {_mb(actual)} / '
         f'{time.perf_counter() - t0:.1f} 秒')
    print(f'\n退避先: {os.path.abspath(dest)}')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='実際に退避する（無ければドライラン）')
    raise SystemExit(main(ap.parse_args().apply))
