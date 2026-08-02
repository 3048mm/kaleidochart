import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy import func

from db.models import DailyPrice, FxRate
from data_collection.fetcher import fetch_daily_data
from indicators.fx_calendar import is_fx_trading_day
from pipeline.utils import sanitize_numeric, attach_market_cap

def sync_phase_t2_prices(db, sheet_data: List[Dict], symbol_id_map: Dict, index_start_date: str, default_start_date: str, skip_fetch: bool, logger: logging.Logger) -> Optional[datetime]:
    """Phase 2: Prices (T2) - Sync prices from yfinance led by SPY."""
    logger.info("--- Phase 2: Price data sync START ---")
    
    spy_item = next((d for d in sheet_data if d['ticker'] == 'SPY'), None)
    if not spy_item:
        logger.error("SPY not found in sheet_data.")
        return None
        
    spy_sym_id = symbol_id_map.get(("SPY", spy_item['exchange']))
    if not spy_sym_id:
        from db.models import Symbol
        spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
        
    spy_max_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar() if spy_sym_id else None
    
    spy_fetch_start = index_start_date
    if spy_max_date:
        spy_fetch_start = (datetime.combine(spy_max_date, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
        
    if not skip_fetch:
        logger.info(f"Updating SPY from {spy_fetch_start}...")
        spy_df = fetch_daily_data("SPY", spy_fetch_start)
        if spy_df is not None and not spy_df.empty:
            spy_df = attach_market_cap("SPY", spy_df, logger)
            existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == spy_sym_id).all()}
            new_recs = [
                DailyPrice(
                    symbol_id=spy_sym_id, 
                    date=row['date'], 
                    open=sanitize_numeric(row, 'open'), 
                    high=sanitize_numeric(row, 'high'), 
                    low=sanitize_numeric(row, 'low'), 
                    close=sanitize_numeric(row, 'close'), 
                    volume=int(row['volume']) if sanitize_numeric(row, 'volume') is not None else 0,
                    market_cap=sanitize_numeric(row, 'market_cap')
                ) for _, row in spy_df.iterrows() if row['date'] not in existing_dates
            ]
            if new_recs:
                db.bulk_save_objects(new_recs)
                db.commit()
                logger.info(f"SPY updated: +{len(new_recs)} rows.")
            else:
                logger.info("SPY: No new rows to add.")
                
    spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
    logger.info(f"Target Reference Date (SPY): {spy_latest_date}")
    if not spy_latest_date: return None
    
    real_items = [d for d in sheet_data if d['theme_type'] != 'virtual' and d['ticker'] != 'SPY' and d['ticker'] != 'JPY=X']
    latest_rows = db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()
    sym_latest_map = {sid: ldt for sid, ldt in latest_rows}
    
    update_needed = []
    for it in real_items:
        sid = symbol_id_map.get((it['ticker'], it['exchange']))
        current_max = sym_latest_map.get(sid)
        if not current_max or current_max < spy_latest_date:
            update_needed.append((it['ticker'], sid, current_max, it.get('category', '')))
            
    logger.info(f"Found {len(update_needed)} tickers needing price update.")
    total_needed = len(update_needed)
    
    if not skip_fetch and update_needed:
        for i, (ticker, sid, current_max, category) in enumerate(update_needed):
            is_index = category in ('レバレッジ', '市場', 'Market', '指標')
            base_start = index_start_date if is_index else default_start_date
            
            f_start = base_start
            if current_max: f_start = (datetime.combine(current_max, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
            
            progress_str = f"[{i+1}/{total_needed}]"
            df = fetch_daily_data(ticker, f_start, progress=progress_str)
            if df is not None and not df.empty:
                df = attach_market_cap(ticker, df, logger)
                existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == sid).all()}
                new_recs = [
                    DailyPrice(
                        symbol_id=sid, 
                        date=row['date'], 
                        open=sanitize_numeric(row, 'open'), 
                        high=sanitize_numeric(row, 'high'), 
                        low=sanitize_numeric(row, 'low'), 
                        close=sanitize_numeric(row, 'close'), 
                        volume=int(row['volume']) if sanitize_numeric(row, 'volume') is not None else 0,
                        market_cap=sanitize_numeric(row, 'market_cap')
                    ) for _, row in df.iterrows() if row['date'] not in existing_dates and row['date'] <= spy_latest_date
                ]
                if new_recs:
                    db.bulk_save_objects(new_recs)
                    db.commit()
                    logger.debug(f"[{ticker}] Updated +{len(new_recs)} rows.")
                    
    return spy_latest_date

def _is_production_db(db) -> bool:
    """セッションの接続先が本番 DB (stocktool.db) かどうかを判定する。

    グローバルな init_db の状態ではなくセッション自身の bind を見るため、
    テストや sandbox のセッションを渡された場合は必ず False になる。
    """
    try:
        url = db.get_bind().url
        return os.path.basename(str(url.database or "")) == "stocktool.db"
    except Exception:
        # 判定できない場合は安全側（本番扱い）に倒す
        return True


def sync_fx_rates(db, skip_fetch: bool = False, logger: Optional[logging.Logger] = None) -> bool:
    """
    為替レート (USD/JPY) を取得し、fx_rates テーブルを同期します。
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        
    logger.info("--- FX Rate Sync START ---")
    try:
        from datetime import date
        
        # 1. 取得開始日の算出（最後の為替レコードの翌日、なければ過去30日前から）
        max_date = db.query(func.max(FxRate.date)).scalar()
        if max_date:
            start_date = (datetime.combine(max_date, datetime.min.time()) + timedelta(days=1)).date()
        else:
            start_date = (datetime.now() - timedelta(days=30)).date()
            
        logger.info(f"Target Sync Start Date for FX: {start_date}")

        if skip_fetch:
            # テストおよびスキップ用：ダミー為替データの登録
            # start_date から今日までの日付に対してダミー値を挿入
            #
            # 【重要】本番 DB にはダミーを書き込まない。fx_rates は
            # (currency_pair, date, rate) しか持たずダミーと実データを後から区別できず、
            # 一度混入すると portfolio の円換算が誤った値で動き続けるため
            # （2026-07-27 に実際に発生し、31行全てがダミーになった）。
            if _is_production_db(db):
                logger.warning(
                    "[Skip Fetch] 本番 DB のためダミー為替の投入をスキップしました "
                    "（fx_rates はダミーと実データを区別できないため）。"
                )
                return True

            today = date.today()
            curr = start_date
            count = 0
            while curr <= today:
                # ダミーであっても土日行は作らない。本番と同じ不変条件
                # （fx_rates に非営業日の行は存在しない）をテスト側でも保つ。
                if not is_fx_trading_day(curr):
                    curr += timedelta(days=1)
                    continue
                exists = db.query(FxRate).filter(FxRate.currency_pair == "USD/JPY", FxRate.date == curr).first()
                if not exists:
                    rate_val = 155.0 + (curr.day % 5) * 0.2  # ダミーレート
                    fx = FxRate(currency_pair="USD/JPY", date=curr, rate=rate_val)
                    db.add(fx)
                    count += 1
                curr += timedelta(days=1)
            db.commit()
            logger.info(f"[Skip Fetch] Inserted {count} dummy fx rates.")
            return True
            
        # 取得済みが最新なら開始日が未来になる。yfinance はこれを
        # "possibly delisted; no price data found" として返すため、
        # 毎回 ERROR がログに出て「本当の障害」が埋もれる（2026-08-01 発見）。
        if start_date > date.today():
            logger.info(
                f"FX Rates: 既に最新です（次の取得開始日 {start_date} は未来）。フェッチをスキップします。"
            )
            return True

        # 2. yfinance から JPY=X をフェッチ
        start_str = start_date.strftime('%Y-%m-%d')
        logger.info(f"Fetching JPY=X from {start_str}...")
        df = fetch_daily_data("JPY=X", start_str)

        if df is not None and not df.empty:
            count = 0
            skipped_non_trading = 0
            for _, row in df.iterrows():
                row_date = row['date']
                # date オブジェクトに変換
                if isinstance(row_date, datetime):
                    row_date = row_date.date()
                elif isinstance(row_date, str):
                    row_date = datetime.strptime(row_date, '%Y-%m-%d').date()

                # 株価の T2 は `row['date'] <= spy_latest_date` という SPY の最新日を
                # 上限にしているため Yahoo の「当日・部分バー」が自動的に落ちるが、
                # fx_rates にはその上限が無い。土曜に実行すると取引していない日の
                # スナップショットが入るので、ここで営業日を上限として弾く。
                if not is_fx_trading_day(row_date):
                    skipped_non_trading += 1
                    continue

                exists = db.query(FxRate).filter(
                    FxRate.currency_pair == "USD/JPY",
                    FxRate.date == row_date
                ).first()

                if not exists:
                    close_val = sanitize_numeric(row, 'close')
                    if close_val is not None:
                        fx = FxRate(
                            currency_pair="USD/JPY",
                            date=row_date,
                            rate=close_val
                        )
                        db.add(fx)
                        count += 1
            if skipped_non_trading:
                logger.info(
                    f"FX Rates: 非営業日（土日）のバー {skipped_non_trading} 件を除外しました。"
                )
            if count > 0:
                db.commit()
                logger.info(f"FX Rates updated: +{count} rows of USD/JPY.")
            else:
                logger.info("FX Rates: No new rows to add.")
        else:
            logger.warning("No FX data fetched from yfinance.")

        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error syncing FX rates: {e}")
        return False

