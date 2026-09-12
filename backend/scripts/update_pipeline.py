import os
import sys
import tomllib
import argparse
import traceback
import msvcrt

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import get_active_db_path
import logging
from logging.handlers import RotatingFileHandler

import multiprocessing

def _rebuild_from_parquet(level: str, logger) -> None:
    """T3 / T4 / T5 の作り直しを **Parquet 基点**で行う。

    ## なぜ SQLite 基点ではいけないか

    T3 は SQLite の `daily_prices` の全行を入力にする（`t3_indicators.py` L22-24。
    日付の絞り込みが無い）。SQLite はホット期間(730日)しか持たないため、
    そのまま `--rebuild-from T3` を回すと**窓の先頭で遡り履歴が足りず、
    `min_periods=1` の指標が「それらしい誤った値」を出す**。

        2026-08-29  730日窓で --rebuild-from T3 を実行
                    → 2024-09〜2025-08 の 772,526行が誤った値で上書きされた
                       sma_200 の 77.7% が食い違い、ブルードットは 4,983件 → 0件

    T4 も同じ理由で「SQLite にある日付しか順位を作れない」制約があり、
    `recompute_parquet_ranks.py` が先に用意されていた（2026-08-05）。

    T5（market_signals）も同じ構図で、SQLite の SPY（ホット期間 約503本）だけで
    全日付を再計算すると `sma_200` の遡りが足りず、MTS の SPY 由来列が誤った値で
    保存される（2026-09-04〜09-06 に複数回発生。詳細は
    `doc/in_progress/t5_parquet_rebuild_plan.md` §1）。`recompute_parquet_signals.py`
    で T3/T4 と同じ考え方に揃えた。

    ## 手順

    Parquet には各銘柄の全履歴がある（ETF は 2010、個別は 2018）。そこで:

        1. Parquet で T3 を全期間再計算し、新世代を publish（T3 指定時のみ）
        2. Parquet で T4 を全期間再計算し、新世代を publish（T3/T4 指定時のみ）
        3. Parquet で T5（market_signals）を全期間再計算し、新世代を publish
        4. SQLite をホット期間ぶん復元（market_signals も Parquet から入る）

    T3〜T5 すべて Parquet で完結し SQLite も復元済みになるため、この関数の
    呼び出し元は `run_pipeline()` を呼ばない（rotate＝SQLite→Parquet の
    マージが不要なため）。

    `--category` は無視する（全銘柄を作り直す方が安全で、Parquet 基点なら
    銘柄を絞る利点も無いため）。

    Args:
        level: "T3" / "T4" / "T5"。T4 指定時は T3 を、T5 指定時は T3・T4 を
               それぞれスキップする（依存元は既に作り直し済みという前提）。
    """
    from scripts import recompute_parquet_indicators, recompute_parquet_ranks, recompute_parquet_signals
    from scripts.run_production_restore import run_production_restore

    if level == "T3":
        logger.info("=== [1/4] Parquet の T3 を全期間再計算 ===")
        recompute_parquet_indicators.run(dry_run=False,
                                         chunk_size=recompute_parquet_indicators.DEFAULT_CHUNK_SIZE)
    else:
        logger.info("=== [1/4] T3 は指定されていないためスキップ ===")

    if level in ("T3", "T4"):
        logger.info("=== [2/4] Parquet の T4 を全期間再計算 ===")
        recompute_parquet_ranks.run(dry_run=False)
    else:
        logger.info("=== [2/4] T4 は指定されていないためスキップ（T5 単独） ===")

    logger.info("=== [3/4] Parquet の T5 を全期間再計算 ===")
    recompute_parquet_signals.run(dry_run=False)

    logger.info("=== [4/4] SQLite をホット期間ぶん復元 ===")
    if not run_production_restore():
        raise RuntimeError("SQLite の復元に失敗しました。Parquet は更新済みなので、"
                           " 復元だけやり直してください。")


def _run_rebuild_or_pipeline(rebuild_from, selected_categories, config, db_path, logger,
                              skip_fetch, skip_sync, skip_t3, recalculate_all) -> None:
    """`--rebuild-from` の指定に応じて、Parquet 基点の再構築 or 通常のパイプラインを実行する。

    T3/T4/T5 は `_rebuild_from_parquet()` に委譲する。Parquet で全期間の
    再計算 → SQLite 復元まで完結するため、**`run_pipeline()` は呼ばない**
    （以前は `rebuild_from="T5"` に差し替えて `run_pipeline()` を続行し、
    SQLite 基点で T5 を再計算していたが、これが MTS 破損の原因だった）。
    それ以外（`T2` や `rebuild_from` 無しの通常実行）は従来どおり
    `run_pipeline()` を呼ぶ。
    """
    if rebuild_from and rebuild_from.upper() in ("T3", "T4", "T5"):
        if selected_categories:
            logger.warning("--rebuild-from %s では --category を無視し、全銘柄を作り直します"
                           "（Parquet 基点のため銘柄を絞る利点がありません）", rebuild_from)
        _rebuild_from_parquet(rebuild_from.upper(), logger)
        return

    from pipeline.orchestrator import run_pipeline
    run_pipeline(
        config=config,
        db_path=db_path,
        logger=logger,
        rebuild_from=rebuild_from,
        categories=selected_categories,
        skip_fetch=skip_fetch,
        skip_sync=skip_sync,
        skip_t3=skip_t3,
        recalculate_all=recalculate_all,
    )


def setup_pipeline_logging():
    log_dir = os.path.join(project_root, "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "pipeline.log")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(log_format)
    root_logger.addHandler(console_h)
    
    # Only use RotatingFileHandler in the MainProcess to avoid PermissionError on Windows
    if multiprocessing.current_process().name == 'MainProcess':
        file_h = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
        file_h.setFormatter(log_format)
        root_logger.addHandler(file_h)
    
    return logging.getLogger(__name__)

logger = setup_pipeline_logging()

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

LOCK_FILE = os.path.join(project_root, "update_pipeline.lock")
lock_fd = None

def acquire_lock():
    global lock_fd
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False

def release_lock():
    global lock_fd
    if lock_fd:
        try:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
        except:
            pass

if __name__ == "__main__":
    # Check for concurrent execution
    if not acquire_lock():
        print("Another instance of the update script is already running. Exiting.")
        sys.exit(0)

    try:
        parser = argparse.ArgumentParser(description="Run the Step 3 Data Pipeline.")
        parser.add_argument("--rebuild-from", type=str, help="Rebuild from a specific table (T2, T3, T4, T5).")
        parser.add_argument("--re-calculate", action="store_true", help="Redownload all data and recalculate all indicators.")
        parser.add_argument("--category", type=str, help="Comma-separated categories to process.")
        parser.add_argument("--skip-fetch", action="store_true", help="Skip yfinance price fetching.")
        parser.add_argument("--skip-sync", action="store_true", help="Skip Google Spreadsheet sync.")
        parser.add_argument("--skip-t3", action="store_true", help="Skip T3 indicator calculation.")
        args = parser.parse_args()
        selected_categories = [c.strip() for c in args.category.split(",")] if args.category else None

        # Load configuration
        config = load_config()
        
        # Apply logging level from config.toml dynamically
        log_level_str = config.get("system", {}).get("log_level", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        logging.getLogger().setLevel(log_level)
        
        db_path = get_active_db_path() or config["system"]["db_path"]

        # T3 / T4 / T5 の作り直しは Parquet 基点で行う（詳細は関数の docstring）。
        # 旧来の SQLite 基点は 2026-08-29 に 772,526行を壊した経路なので使わない。
        _run_rebuild_or_pipeline(
            rebuild_from=args.rebuild_from,
            selected_categories=selected_categories,
            config=config,
            db_path=db_path,
            logger=logger,
            skip_fetch=args.skip_fetch,
            skip_sync=args.skip_sync,
            skip_t3=args.skip_t3,
            recalculate_all=args.re_calculate,
        )
    except Exception as e:
        logger.error(f"Failed to execute pipeline: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        release_lock()
