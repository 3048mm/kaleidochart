"""universe.db に `ipo_candidates` テーブルを追加する。

## なぜ必要か

`universe.db` の個別銘柄は 2026-04 頃のスプレッドシート由来で、**それ以降に IPO した
銘柄が一切入っていない**。既存の SEC 週次同期は universe.db → SEC の方向にしか
走査しないため、「改称」「上場廃止」は検知できても「新規上場」は構造的に検知できない。

検知した候補を人間がレビューして採用するまでの待機場所が要る。

## 対象は universe.db（ユーザー資産）

`agent_execution_rules.md` §10.1 の通り、swap・再構築は禁止。
**バックアップ取得 → in-place マイグレーションのみ**。冪等（再実行可能）。

採用/却下は**再生成不可能な人間の判断**なので、この DB のバックアップ規律に乗せる。

## 実装メモ

`init_universe_db()` が `BaseUniverse.metadata.create_all()` を呼ぶため、
モデルを追加した時点でテーブルは自動生成される。本スクリプトは
**バックアップの取得と作成結果の検証**を明示的に行うためのもの。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_ipo_candidates.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\migrate_universe_ipo_candidates.py --apply

詳細: `doc/in_progress/ipo_candidates_plan.md` §3.2
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from sqlalchemy import text  # noqa: E402

from db.database_universe import get_universe_write_db, init_universe_db  # noqa: E402
from db.models_universe import IpoCandidate  # noqa: E402

TABLE = IpoCandidate.__tablename__


def table_exists(db) -> bool:
    row = db.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": TABLE}).fetchone()
    return row is not None


def existing_columns(db) -> set:
    return {r[1] for r in db.execute(text(f"PRAGMA table_info({TABLE})"))}


def resolve_universe_db_path() -> str:
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    return config["system"].get(
        "universe_db_path",
        os.path.join(os.path.dirname(config["system"]["db_path"]), "universe.db"))


def run(dry_run: bool) -> dict:
    udb_path = resolve_universe_db_path()

    print("=" * 74)
    print(f"universe.db に {TABLE} を追加  (dry-run={dry_run})")
    print("=" * 74)
    print(f"対象: {udb_path}")

    already = os.path.exists(udb_path)
    if dry_run:
        # dry-run では create_all を走らせたくないので生の接続で覗くだけにする
        import sqlite3
        if not already:
            print("\n[!] DB が存在しません。--apply で新規作成されます。")
            return {"created": False, "dry_run": True}
        con = sqlite3.connect(f"file:{udb_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (TABLE,)).fetchone()
            if row:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")]
                n = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
                print(f"\n[=] {TABLE} は既に存在します（{len(cols)}列 / {n}行）")
                print(f"    列: {cols}")
                return {"created": False, "dry_run": True, "rows": n}
            print(f"\n[+] {TABLE} は未作成。--apply で作成されます。")
            print(f"    列: {[c.name for c in IpoCandidate.__table__.columns]}")
            return {"created": True, "dry_run": True}
        finally:
            con.close()

    # ユーザー資産なので必ずバックアップを取る（既存 DB がある場合のみ）
    if already:
        bk = f"{udb_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(udb_path, bk)
        print(f"バックアップ: {os.path.basename(bk)}")

    # init_universe_db() の create_all がモデル定義からテーブルを作る（冪等）
    init_universe_db(udb_path)
    with get_universe_write_db() as db:
        if not table_exists(db):
            raise RuntimeError(
                f"{TABLE} が作成されませんでした。モデルの登録漏れを疑ってください。")
        cols = existing_columns(db)
        expected = {c.name for c in IpoCandidate.__table__.columns}
        missing = expected - cols
        if missing:
            # SQLite の create_all は**既存テーブルに列を足さない**。
            # 後から列を増やしたときはここで検出して ALTER する必要がある
            raise RuntimeError(
                f"{TABLE} に列が不足しています: {sorted(missing)}\n"
                f"  create_all は既存テーブルへ列を追加しません。"
                f"ALTER TABLE で個別に追加してください。")
        n = db.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar()
        print(f"\n[OK] {TABLE}: {len(cols)}列 / {n}行")
        print(f"     列: {sorted(cols)}")
    return {"created": True, "columns": len(cols), "rows": n}


def main():
    ap = argparse.ArgumentParser(
        description=f"universe.db に {TABLE} を追加する（冪等）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="変更せずに現状を表示する")
    g.add_argument("--apply", action="store_true", help="実際に作成する")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
