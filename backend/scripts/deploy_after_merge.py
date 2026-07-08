# -*- coding: utf-8 -*-
"""
merge 後のデータ昇格ランナー（種別 B: スキーマ・indicator・パイプライン変更）。

処理フロー（doc/in_progress/deploy_after_merge_plan.md §3）:
  1. 前提チェック（lock なし / ソース Parquet あり / ディスク空き）
  2. 現行本番世代の記録（ロールバック用）
  3. 本番 Parquet → ワークスペース（data/tmp/deploy/）へコピーし、
     ワークスペース SQLite を復元 → merge 済みコードでパイプライン再実行
  4. ワークスペースの health check
  5. 新世代を本番へ swap（ポインタ差し替え。旧世代は保持）
  6. 本番 SQLite を restore → 本番 health check
  7. NG ならポインタを旧世代へロールバックして restore し直し、exit code 2 で終了

exit code: 0=成功 / 1=昇格前の失敗（本番無変更） / 2=ロールバック発生
通常は tools/deploy_after_merge.ps1 から起動する。
"""
import os
import sys
import time
import shutil
import logging
import argparse
import subprocess

# backend / project root を sys.path へ（update_pipeline.py と同じ流儀）
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)
project_root = os.path.dirname(backend_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deploy_after_merge")

EXIT_OK = 0
EXIT_FAILED_BEFORE_PROMOTE = 1
EXIT_ROLLED_BACK = 2

MIN_FREE_GB = 5


def _venv_python() -> str:
    """本体リポジトリの venv python を返す（ワークツリーには venv が無い前提）"""
    cand = os.path.join(project_root, "venv", "Scripts", "python.exe")
    return cand if os.path.exists(cand) else sys.executable


def _run(cmd: list[str], env: dict | None = None, cwd: str | None = None) -> int:
    logger.info("実行: " + " ".join(cmd))
    proc = subprocess.run(cmd, env=env, cwd=cwd or project_root)
    return proc.returncode


def preflight(prod_parquet_dir: str, workspace: str) -> str | None:
    """前提チェック。NG 理由を返す（None なら OK）"""
    from pipeline.deploy_promotion import read_generation

    lock_file = os.path.join(project_root, "update_pipeline.lock")
    if os.path.exists(lock_file):
        # msvcrt ロックの残骸の可能性もあるが、安全側に倒して中断する
        return f"update_pipeline.lock が存在します（daily update 実行中の可能性）: {lock_file}"

    if read_generation(prod_parquet_dir) is None:
        return f"本番 Parquet の世代ポインタが見つかりません: {prod_parquet_dir}"

    free_gb = shutil.disk_usage(os.path.dirname(workspace) or ".").free / (1024 ** 3)
    if free_gb < MIN_FREE_GB:
        return f"ディスク空き容量不足: {free_gb:.1f} GB（{MIN_FREE_GB} GB 以上必要）"
    return None


def regenerate_in_workspace(workspace: str, prod_parquet_dir: str, rebuild_from: str) -> bool:
    """本番世代をワークスペースへコピーし、SQLite 復元 → パイプライン再実行する"""
    from pipeline.deploy_promotion import copy_generation

    ws_db = os.path.join(workspace, "stocktool_deploy.db")
    ws_user_db = os.path.join(workspace, "user_data_deploy.db")
    ws_parquet = os.path.join(workspace, "parquet_master")

    # ワークスペースを毎回作り直す（使い捨て）
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    os.makedirs(workspace, exist_ok=True)

    logger.info("Step 3a: 本番 Parquet をワークスペースへコピー...")
    copy_generation(prod_parquet_dir, ws_parquet, logger)

    # 子プロセス用環境: ワークスペースの DB を指す（user 側も必ずセット — heal_*_ids 事故防止）
    env = os.environ.copy()
    env["STOCKTOOL_DB_PATH"] = ws_db
    env["STOCKTOOL_USER_DB_PATH"] = ws_user_db
    env["PYTHONPATH"] = backend_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    py = _venv_python()

    logger.info("Step 3b: ワークスペース SQLite を Parquet から復元...")
    # run_production_restore は内部で STOCKTOOL_DB_PATH を解除し --db-path を優先するため env はそのままで安全
    rc = _run([py, os.path.join(backend_dir, "scripts", "run_production_restore.py"),
               "--db-path", ws_db], env=env)
    if rc != 0:
        logger.error("ワークスペース SQLite の復元に失敗しました")
        return False

    logger.info(f"Step 3c: merge 済みコードでパイプライン再実行（範囲: {rebuild_from}）...")
    pipeline_cmd = [py, os.path.join(backend_dir, "scripts", "update_pipeline.py"), "--skip-sync"]
    if rebuild_from.upper() == "ALL":
        # 全履歴の再構築 = 全データ再取得 + 全指標再計算（yfinance アクセスあり・長時間）
        pipeline_cmd.append("--re-calculate")
    else:
        # ホット期間（730日）のみの再計算。それ以前の Parquet 履歴は旧値のまま残る点に注意
        pipeline_cmd += ["--rebuild-from", rebuild_from.upper(), "--skip-fetch"]
    rc = _run(pipeline_cmd, env=env)
    if rc != 0:
        logger.error("パイプライン再実行に失敗しました")
        return False
    return True


def health_check(db_path: str) -> bool:
    """db_health_check.py を実行し、exit code で合否を返す"""
    py = _venv_python()
    env = {k: v for k, v in os.environ.items() if k not in ("STOCKTOOL_DB_PATH", "STOCKTOOL_USER_DB_PATH")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    rc = _run([py, os.path.join(project_root, "tools", "db_health_check.py"),
               "--all", "--check-nulls", "--db-path", db_path], env=env)
    return rc == 0


def restore_prod_sqlite(prod_db_path: str) -> bool:
    py = _venv_python()
    env = {k: v for k, v in os.environ.items() if k not in ("STOCKTOOL_DB_PATH", "STOCKTOOL_USER_DB_PATH")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    rc = _run([py, os.path.join(backend_dir, "scripts", "run_production_restore.py"),
               "--db-path", prod_db_path], env=env)
    return rc == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="merge 後のデータ昇格ランナー（種別 B）")
    parser.add_argument("--rebuild-from", type=str, default="T3",
                        choices=["T2", "T3", "T4", "T5", "All", "ALL", "all"],
                        help="再計算の起点（既定 T3）。All は全データ再取得込みの全履歴再構築（長時間）")
    parser.add_argument("--workspace", type=str, default=os.path.join(project_root, "data", "tmp", "deploy"),
                        help="再生成ワークスペース（既定 data/tmp/deploy）")
    parser.add_argument("--prod-db-path", type=str, default=os.path.join(project_root, "data", "stocktool.db"),
                        help="本番 SQLite（模擬テストで差し替え可）")
    parser.add_argument("--dry-run", action="store_true",
                        help="再生成とワークスペース検証まで実施し、本番への swap/restore を行わない")
    parser.add_argument("--keep-workspace", action="store_true", help="終了時にワークスペースを残す")
    args = parser.parse_args()

    from pipeline.deploy_promotion import read_generation, promote_generation, rollback_generation, generation_timestamp

    prod_db_path = os.path.abspath(args.prod_db_path)
    prod_parquet_dir = os.path.join(os.path.dirname(prod_db_path), "parquet_master")
    workspace = os.path.abspath(args.workspace)
    ws_db = os.path.join(workspace, "stocktool_deploy.db")
    ws_parquet = os.path.join(workspace, "parquet_master")
    t_start = time.time()

    logger.info("=" * 65)
    logger.info("  DEPLOY AFTER MERGE - データ昇格ランナー")
    logger.info(f"  本番: {prod_parquet_dir}")
    logger.info(f"  範囲: {args.rebuild_from} / dry-run: {args.dry_run}")
    logger.info("=" * 65)

    # 1. 前提チェック
    ng = preflight(prod_parquet_dir, workspace)
    if ng:
        logger.error(f"前提チェック NG: {ng}")
        return EXIT_FAILED_BEFORE_PROMOTE

    # 2. 現行世代の記録
    old_pointer = read_generation(prod_parquet_dir)
    logger.info(f"現行本番世代: {generation_timestamp(old_pointer)}（ロールバック用に記録）")

    # 3. ワークスペースで再生成
    if not regenerate_in_workspace(workspace, prod_parquet_dir, args.rebuild_from):
        return EXIT_FAILED_BEFORE_PROMOTE

    # 4. ワークスペース検証
    logger.info("Step 4: ワークスペースの health check...")
    if not health_check(ws_db):
        logger.error("ワークスペースの health check NG。本番は無変更のまま中断します。")
        return EXIT_FAILED_BEFORE_PROMOTE

    if args.dry_run:
        logger.info("dry-run 指定のためここで終了します（本番は無変更）。")
        logger.info(f"所要時間: {time.time() - t_start:.0f} 秒")
        return EXIT_OK

    # 5. swap
    logger.info("Step 5: 新世代を本番へ昇格（ポインタ差し替え）...")
    old_pointer, new_pointer = promote_generation(ws_parquet, prod_parquet_dir, logger)

    # 6. 本番 SQLite restore + health check
    logger.info("Step 6: 本番 SQLite を新世代から restore...")
    ok = restore_prod_sqlite(prod_db_path) and health_check(prod_db_path)

    if not ok:
        # 7. ロールバック
        logger.error("=" * 65)
        logger.error("  !! HEALTH CHECK NG -> ロールバックを実行します !!")
        logger.error("=" * 65)
        if rollback_generation(prod_parquet_dir, old_pointer, logger) and restore_prod_sqlite(prod_db_path):
            logger.error(f"ロールバック完了: 本番は昇格前の世代 {generation_timestamp(old_pointer)} に戻りました。")
        else:
            logger.error("ロールバックにも失敗しました。手動復旧が必要です（旧世代ファイルは parquet_master/ に残っています）。")
        return EXIT_ROLLED_BACK

    if not args.keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)

    logger.info("=" * 65)
    logger.info(f"  昇格成功: 新世代 {generation_timestamp(new_pointer)}")
    logger.info(f"  所要時間: {time.time() - t_start:.0f} 秒")
    logger.info("  API サーバを再起動できます。旧世代の prune は次回 daily update に任せます。")
    logger.info("=" * 65)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
