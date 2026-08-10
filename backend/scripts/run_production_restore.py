# -*- coding: utf-8 -*-
import os
import sys
import time
import shutil
import logging
import gc

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Add project root and backend to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from backend.db.database import init_db, get_db
from backend.pipeline.parquet_cache_manager import restore_sqlite_cache_from_parquet

# ファイル削除を諦めるまでの試行回数と間隔。
# 一過性のハンドル（終了直前のリクエスト等）は数秒で解放されるが、
# 無限に粘ると復旧作業そのものが止まるので上限を切る。
DELETE_RETRY_ATTEMPTS = 5
DELETE_RETRY_DELAY_SEC = 2.0


def delete_with_retry(paths, remove=None, sleep=None,
                      attempts: int = DELETE_RETRY_ATTEMPTS,
                      delay: float = DELETE_RETRY_DELAY_SEC):
    """ファイルを消す。掴まれていたら少し粘る。

    **1回失敗しただけで破壊的フォールバックへ落とさない。**
    終了直前のリクエストがハンドルを握っている程度のことは普通に起きる。

    Returns:
        (全部消せたか, 消せなかったパスのリスト)
    """
    remove = remove or os.remove
    sleep = sleep or time.sleep

    stuck = []
    for attempt in range(attempts):
        stuck = []
        for p in paths:
            if not os.path.exists(p):
                continue
            try:
                remove(p)
                logger.info(f"  Deleted: {os.path.basename(p)}")
            except Exception as e:
                stuck.append(p)
                if attempt == attempts - 1:
                    logger.warning(f"  Could not delete {os.path.basename(p)}: {e}")
        if not stuck:
            return True, []
        if attempt < attempts - 1:
            logger.info(f"  ロックされています。{delay}秒待って再試行します"
                        f"（{attempt + 1}/{attempts}）")
            sleep(delay)
    return False, stuck


def describe_lock_holders(path: str) -> list:
    """ロックしていそうなプロセスを列挙する（診断用・best-effort）。

    **何を止めればよいかを名指しする。** 2026-08-03 の事故では uvicorn の
    **孤児の子プロセス**がハンドルを握っており、「サーバーを止めたのに解消しない」で
    原因特定に時間を要した。厄介なのは、**ファイルハンドルは掴んでいるが
    SQLite のロックは持っていない**中間状態になること
    （`BEGIN IMMEDIATE` が通るので「DB は空いている」と誤認できてしまう）。

    > [!WARNING]
    > **`psutil.Process.open_files()` を使ってはいけない。**
    > Windows で `access violation` を起こし、**インタプリタごと落ちる**
    > （`except` では捕捉できない。2026-08-09 にテスト実行時に実測）。
    > よって「そのファイルを開いているプロセス」を厳密には特定せず、
    > **同じプロジェクトを触っている Python プロセス**を候補として挙げる。
    > 運用者が知りたいのは「何を止めればよいか」なので、これで足りる。

    `psutil` が無ければ空リストを返す（診断で復旧を止めない）。
    """
    holders = []
    try:
        import psutil
    except ImportError:
        logger.debug("psutil が無いためロック保持者を推定できません")
        return holders

    # DB が置かれたディレクトリ（data/）ではなく**プロジェクトルート**で照合する。
    # uvicorn は `...\stocktool\venv\Scripts\uvicorn.exe` として起動するため、
    # data/ で照合すると本命の API サーバーが候補に挙がらない。
    project = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(path))))
    me = os.getpid()
    # 自分自身を候補に挙げない。venv の python.exe はラッパーと実体で PID が分かれるため、
    # `os.getpid()` だけでは足りずスクリプト名でも除外する
    self_marker = os.path.splitext(os.path.basename(__file__))[0].lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            if info["pid"] == me or not (info.get("name") or "").lower().startswith("python"):
                continue
            cmd = " ".join(info.get("cmdline") or [])
            if self_marker in cmd.lower():
                continue
            if project and os.path.normcase(cmd).find(project) >= 0:
                holders.append(f"PID {info['pid']} ({info['name']}): {cmd[:90]}")
        except Exception:  # noqa: BLE001 — 消えた・アクセス不可のプロセスは飛ばす
            continue
    return holders


def run_production_restore(db_path: str = None, force: bool = False):
    logger.info("=========================================================")
    logger.info("    🏛️ PRODUCTION DATABASE REBUILD & RESTORE RUNNER 🏛️")
    logger.info("=========================================================")

    # db_path 指定時はそのDB（と隣の parquet_master/）を対象にする
    # （deploy_after_merge の模擬昇格テスト・ワークスペース復元で使用）
    prod_db_path = os.path.abspath(db_path) if db_path else os.path.join(project_root, "data", "stocktool.db")

    # init_db は STOCKTOOL_DB_PATH を優先するため、restore 対象と食い違うと
    # 「parquet は A、書き込み先は B」という破壊的な不整合になる。ここで必ず解除する。
    env_db = os.environ.pop("STOCKTOOL_DB_PATH", None)
    if env_db and os.path.abspath(env_db) != prod_db_path:
        logger.warning(f"STOCKTOOL_DB_PATH ({env_db}) を解除しました。restore 対象: {prod_db_path}")
    
    # 1. Close any potential open engine/sessions in this process
    import backend.db.database as db_module
    if db_module.engine:
        db_module.engine.dispose()
    db_module.engine = None
    db_module.SessionLocal = None
    
    gc.collect()
    time.sleep(0.5) # Wait for OS handles
    
    # 2. Delete the corrupted / bloated SQLite file and its journals (Non-fatal fallback)
    logger.info("1. Attempting to delete bloated SQLite database files (will fallback to truncation if locked)...")
    target_files = [
        prod_db_path,
        prod_db_path + "-journal",
        prod_db_path + "-shm",
        prod_db_path + "-wal"
    ]
    
    file_deleted, stuck = delete_with_retry(target_files)

    if not file_deleted:
        # **ロックの主体を特定せずに破壊的操作へ進まない。**
        # 2026-08-06 の実測では、掴んでいたのは uvicorn で、そのまま DROP ALL に進んだ結果
        # 読み取りロックに阻まれて5分以上ハングし、プロセスを落として再実行する羽目になった。
        logger.error("  以下のファイルを削除できませんでした（他プロセスが掴んでいます）:")
        for p in stuck:
            logger.error(f"    - {os.path.basename(p)}")
        holders = describe_lock_holders(prod_db_path)
        if holders:
            logger.error("  同じプロジェクトを触っている Python プロセス（これらを止めてください）:")
            for h in holders:
                logger.error(f"    - {h}")
        logger.error("  ヒント: API サーバー(uvicorn)を停止し、**子プロセスが孤児として"
                     "残っていないか**も確認してください（残るとハンドルを掴み続けます）。")

        if not force:
            logger.error("  中断します。ロックを解消してから再実行するか、"
                         "どうしても続けるなら --force を付けてください"
                         "（DROP ALL にフォールバックしますが、読み取りロックがあると長時間停止します）。")
            return False

    # 3. Create fresh database with correct schemas
    logger.info("2. Re-creating fresh SQLite database schemas from models...")
    try:
        init_db(prod_db_path)
        if not file_deleted:
            logger.warning("  --force が指定されたため DROP ALL にフォールバックします"
                           "（読み取りロックがあると長時間停止する可能性があります）")
            from backend.db.models import Base
            Base.metadata.drop_all(bind=db_module.engine)
            Base.metadata.create_all(bind=db_module.engine)
        logger.info("  SQLite schema recreation completed successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False
        
    # 4. Import the historical cache from Parquet Master
    logger.info("3. Bulk-importing the historical cache from Parquet masters...")
    try:
        # Re-import db module to get fresh sessionlocal
        import backend.db.database as db_module
        with db_module.get_write_db() as db:
            restore_sqlite_cache_from_parquet(db, prod_db_path, logger)
        logger.info("  SQLite Cache successfully restored from Parquet master!")
    except Exception as e:
        logger.error(f"Failed to restore SQLite Cache from Parquet master: {e}")
        return False
        
    # 5. Restore metadata if needed (so update_pipeline knows where it left off)
    try:
        from backend.pipeline.utils import update_pipeline_meta
        from datetime import datetime
        # Fetch max date in DailyPrice to align last SPY date
        with db_module.get_write_db() as db:
            from backend.db.models import DailyPrice
            from sqlalchemy import func
            max_date_val = db.query(func.max(DailyPrice.date)).scalar()
            if max_date_val:
                from datetime import date
                if isinstance(max_date_val, str):
                    spy_date = datetime.strptime(max_date_val, "%Y-%m-%d").date()
                else:
                    spy_date = max_date_val
                update_pipeline_meta(db, datetime.utcnow(), spy_date)
                logger.info(f"  Successfully restored pipeline metadata to last_spy_date={spy_date}")
    except Exception as me:
        logger.warning(f"  Failed to restore pipeline metadata: {me}")
        
    logger.info("\n🎉 PRODUCTION DATABASE MIGRATION & RESTORE COMPLETED SUCCESSFULLY!")
    logger.info(f"  Bloated DB (4.7 GB) has been successfully rebuilt into optimized historical cache!")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild & restore a SQLite hot cache from its Parquet master.")
    parser.add_argument("--db-path", type=str, default=None,
                        help="対象 SQLite パス（省略時は data/stocktool.db。parquet_master/ は DB と同じディレクトリから解決）")
    parser.add_argument("--force", action="store_true",
                        help="ファイルを削除できなくても DROP ALL で強行する"
                             "（読み取りロックがあると長時間停止する。通常は使わない）")
    args = parser.parse_args()
    # run_production_restore 内で STOCKTOOL_DB_PATH は解除され --db-path が優先される
    if args.db_path and os.getenv("STOCKTOOL_DB_PATH"):
        logger.info("STOCKTOOL_DB_PATH はプロセス内で解除され、--db-path を restore 対象として使用します。")
    # 日次更新・週次メンテとの同時実行を防ぐ。**この処理は SQLite を全消しして
    # Parquet から作り直す**ため、裏でパイプラインが走ると両方が壊れる。
    from backend.pipeline.pipeline_lock import pipeline_lock
    with pipeline_lock("run_production_restore"):
        success = run_production_restore(args.db_path, force=args.force)
    sys.exit(0 if success else 1)
