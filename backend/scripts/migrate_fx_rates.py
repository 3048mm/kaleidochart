# -*- coding: utf-8 -*-
"""
為替データ（JPY=X）を daily_prices から fx_rates 専用テーブルへ移行するスクリプト。
"""
import logging
from sqlalchemy.orm import Session
from db.models import Symbol, DailyPrice, FxRate

logger = logging.getLogger(__name__)

def migrate_fx_rates(db: Session) -> bool:
    """
    daily_prices テーブル内の JPY=X レコードを fx_rates テーブルに移行します。
    移行完了後、daily_prices 内の JPY=X レコードを削除します。
    """
    try:
        # 1. JPY=X の Symbol を取得
        jpy_symbol = db.query(Symbol).filter(Symbol.ticker == "JPY=X").first()
        if not jpy_symbol:
            logger.info("Symbol 'JPY=X' not found. Migration skipped.")
            return True

        # 2. daily_prices から JPY=X の価格データを全件取得
        prices = db.query(DailyPrice).filter(DailyPrice.symbol_id == jpy_symbol.id).all()
        if not prices:
            logger.info("No price data for 'JPY=X' found. Migration completed successfully (no data).")
            return True

        logger.info(f"Found {len(prices)} price records for JPY=X. Starting migration to fx_rates...")

        # 3. FxRate テーブルへデータを移行
        count = 0
        for price in prices:
            # 重複登録を防ぐため、既に存在するか確認
            exists = db.query(FxRate).filter(
                FxRate.currency_pair == "USD/JPY",
                FxRate.date == price.date
            ).first()
            
            if not exists:
                fx_rate = FxRate(
                    currency_pair="USD/JPY",
                    date=price.date,
                    rate=price.close  # close 値を為替レートとして使用
                )
                db.add(fx_rate)
                count += 1

        logger.info(f"Inserted {count} records into fx_rates.")

        # 4. daily_prices から移行したレコードを削除
        deleted_count = db.query(DailyPrice).filter(DailyPrice.symbol_id == jpy_symbol.id).delete()
        logger.info(f"Deleted {deleted_count} old JPY=X records from daily_prices.")

        # コミット
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error during FX rates migration: {e}")
        return False
