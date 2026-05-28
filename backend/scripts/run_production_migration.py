# -*- coding: utf-8 -*-
"""
本番データベース (data/stocktool.db) に対して為替データ移行マイグレーションを適用するスクリプト。
"""
import os
import sys
import logging

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ルートディレクトリを検索パスに追加 (scripts -> backend -> project_root と3段階上に遡る)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from db.database import init_db
from scripts.migrate_fx_rates import migrate_fx_rates

def run_migration():
    # 本番DBのパス
    prod_db_path = os.path.join(project_root, "data", "stocktool.db")
    if not os.path.exists(prod_db_path):
        logger.error(f"Production database not found at {prod_db_path}")
        return False

    logger.info(f"Connecting to production database: {prod_db_path}")
    
    # 1. 新しいスキーマの適用 (fx_rates テーブルを自動生成、既存テーブルは保持)
    logger.info("Applying schema updates (creating fx_rates table)...")
    init_db(prod_db_path)
    
    # 2. セッションの開始と移行ロジックの実行
    from db.database import SessionLocal
    db = SessionLocal()
    try:
        logger.info("Executing JPY=X data migration to fx_rates...")
        success = migrate_fx_rates(db)
        if success:
            logger.info("Migration COMPLETED SUCCESSFULLY!")
        else:
            logger.error("Migration FAILED during execution.")
        return success
    except Exception as e:
        logger.error(f"Migration crashed: {e}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    run_migration()
