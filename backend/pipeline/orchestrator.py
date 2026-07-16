import logging
import pandas as pd
import time
import os
import json
import hashlib
from typing import Optional, List
from sqlalchemy import func, text

from db.database import init_db, get_db, get_write_db, get_active_db_path
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent
from data_collection.spreadsheet_sync import fetch_symbols_from_sheet

from .phases.t2_prices import sync_phase_t2_prices, sync_fx_rates
from .phases.t3_indicators import sync_phase_t3_indicators
from .phases.t4_ranks import sync_phase_t4_ranks
from .phases.t5_signals import sync_phase_t5_signals

logger = logging.getLogger(__name__)

def sync_symbols_to_db(db, credentials_path, spreadsheet_url, extra_symbols=None):
    sheet_data = fetch_symbols_from_sheet(credentials_path, spreadsheet_url)
    
    if extra_symbols:
        existing_tickers = {item['ticker'] for item in sheet_data}
        for ticker in extra_symbols:
            if ticker not in existing_tickers:
                sheet_data.append({
                    "ticker": ticker,
                    "exchange": "US",
                    "name": ticker,
                    "category": "市場",
                    "asset_class": "System",
                    "tags": None,
                    "theme_type": "etf"
                })
    
    db.query(Symbol).update({"active": 0})
    symbol_ids = {}
    for item in sheet_data:
        sym = db.query(Symbol).filter(Symbol.ticker == item['ticker'], Symbol.exchange == item['exchange']).first()
        if sym:
            sym.name, sym.category, sym.asset_class, sym.theme_type, sym.tags, sym.active = item['name'], item['category'], item['asset_class'], item['theme_type'], item['tags'], 1
        else:
            sym = Symbol(ticker=item['ticker'], exchange=item['exchange'], name=item['name'], category=item['category'], asset_class=item['asset_class'], theme_type=item['theme_type'], tags=item['tags'], active=1)
            db.add(sym)
        db.flush()
        symbol_ids[(sym.ticker, sym.exchange)] = sym.id
    db.commit()
    db.query(ThemeConstituent).delete()
    db.commit()
    themes_and_virtuals, constituents_to_add = [s for s in sheet_data if s['category'] == 'テーマ'], []
    for t_item in themes_and_virtuals:
        t_id = symbol_ids[(t_item['ticker'], t_item['exchange'])]
        target_tag = t_item['ticker'].strip()
        
        if not target_tag:
            continue
            
        matching_symbols = db.query(Symbol).filter(Symbol.active == 1, Symbol.tags.like(f"%{target_tag}%"), (Symbol.theme_type != 'virtual') | (Symbol.theme_type.is_(None))).all()
        if matching_symbols:
            weight = 1.0 / len(matching_symbols)
            for m_sym in matching_symbols:
                constituents_to_add.append(ThemeConstituent(theme_id=t_id, symbol_id=m_sym.id, weight=weight))
    if constituents_to_add:
        db.bulk_save_objects(constituents_to_add)
        db.commit()
    return sheet_data, symbol_ids

def build_virtual_index_prices(db, virtual_id: int):
    mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == virtual_id).all()
    if not mappings: return pd.DataFrame()
    c_ids = [m.symbol_id for m in mappings]
    all_prices = []
    for i in range(0, len(c_ids), 900):
        all_prices.extend(db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(c_ids[i:i + 900])).all())
    if not all_prices: return pd.DataFrame()
    # closeに加えてvolumeも取得
    df = pd.DataFrame([{'date': p.date, 'symbol_id': p.symbol_id, 'close': p.close, 'volume': p.volume} for p in all_prices if p.close is not None and p.close > 0])
    if df.empty: return pd.DataFrame()
    df = df.sort_values('date')
    
    import numpy as np
    
    # 騰落率の算出
    df['ret'] = df.groupby('symbol_id')['close'].pct_change()
    daily_avg_ret = df.dropna(subset=['ret']).groupby('date')['ret'].mean().reset_index().sort_values('date')
    if daily_avg_ret.empty: return pd.DataFrame()
    
    # 売買代金の算出 (Dollar Volume = close * volume)
    df['dollar_volume'] = df['close'] * df['volume'].fillna(0)
    
    # 21日平均売買代金の算出
    df['dollar_volume_ma21'] = df.groupby('symbol_id')['dollar_volume'].transform(
        lambda x: x.rolling(window=21, min_periods=1).mean()
    )
    
    # 出来高急増倍率 (Surge) の算出
    df['surge'] = np.where(
        df['dollar_volume_ma21'] == 0,
        1.0,
        df['dollar_volume'] / df['dollar_volume_ma21']
    )
    df['surge'] = df['surge'].fillna(1.0)
    
    # 日付ごとの平均出来高急増倍率を算出
    daily_avg_surge = df.groupby('date')['surge'].mean().reset_index()
    
    current_val, synth_closes = 1000.0, []
    for _, row in daily_avg_ret.iterrows():
        current_val *= (1 + row['ret'])
        synth_closes.append(current_val)
    daily_avg_ret['close'] = synth_closes
    daily_avg_ret['open'] = daily_avg_ret['high'] = daily_avg_ret['low'] = daily_avg_ret['close']
    
    # 平均出来高急増倍率を volume カラムに結合
    daily_avg_ret = pd.merge(daily_avg_ret, daily_avg_surge, on='date', how='left')
    daily_avg_ret['volume'] = (daily_avg_ret['surge'].fillna(1.0) * 1000000.0).astype(float)
    daily_avg_ret.drop(columns=['surge'], inplace=True)
    
    daily_avg_ret['symbol_id'] = virtual_id
    return daily_avg_ret

def build_all_virtual_indexes_prices(db, virtual_items: list[dict], symbol_id_map: dict, hash_file_path: str = "data/virtual_theme_hashes.json"):
    if not virtual_items:
        return
        
    v_ids = []
    v_id_to_item = {}
    for item in virtual_items:
        v_id = symbol_id_map.get((item['ticker'], item['exchange']))
        if v_id:
            v_ids.append(v_id)
            v_id_to_item[v_id] = item

    if not v_ids:
        return

    constituents = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id.in_(v_ids)).all()
    if not constituents:
        return

    theme_to_symbols = {}
    all_constituent_symbol_ids = set()
    for c in constituents:
        theme_to_symbols.setdefault(c.theme_id, []).append(c.symbol_id)
        all_constituent_symbol_ids.add(c.symbol_id)

    saved_hashes = {}
    if os.path.exists(hash_file_path):
        try:
            with open(hash_file_path, 'r', encoding='utf-8') as f:
                saved_hashes = json.load(f)
        except Exception:
            saved_hashes = {}

    new_hashes = {}
    rebuild_ranks = False
    theme_modes = {}
    
    for v_id in v_ids:
        c_syms = sorted(theme_to_symbols.get(v_id, []))
        if not c_syms:
            continue
        
        hash_key = ",".join(map(str, c_syms))
        current_hash = hashlib.sha256(hash_key.encode('utf-8')).hexdigest()
        new_hashes[str(v_id)] = current_hash
        
        prev_hash = saved_hashes.get(str(v_id))
        is_incremental = False
        last_date = None
        
        if prev_hash == current_hash:
            last_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == v_id).scalar()
            if last_date:
                prev_date_query = db.query(DailyPrice.date, DailyPrice.close)\
                                    .filter(DailyPrice.symbol_id == v_id, DailyPrice.date < last_date)\
                                    .order_by(DailyPrice.date.desc()).first()
                if prev_date_query:
                    is_incremental = True
        
        if is_incremental:
            theme_modes[v_id] = ('incremental', current_hash, last_date)
        else:
            theme_modes[v_id] = ('rebuild', current_hash, None)
            rebuild_ranks = True

    c_ids_list = list(all_constituent_symbol_ids)
    if not c_ids_list:
        return
        
    # チャンク化してクエリを発行しパラメータ制限を回避＆メモリを保護
    db.commit()
    from db.database import get_read_engine_for
    read_engine = get_read_engine_for(db)
    
    chunk_size = 500
    dfs = []
    for i in range(0, len(c_ids_list), chunk_size):
        chunk = c_ids_list[i:i + chunk_size]
        chunk_str = ",".join(map(str, chunk))
        query = f"SELECT symbol_id, date, open, high, low, close, volume FROM daily_prices WHERE symbol_id IN ({chunk_str}) AND close IS NOT NULL AND close > 0"
        chunk_df = pd.read_sql_query(query, read_engine)
        if not chunk_df.empty:
            dfs.append(chunk_df)
            
    if not dfs:
        return
        
    all_prices_df = pd.concat(dfs, ignore_index=True)
    all_prices_df['date'] = pd.to_datetime(all_prices_df['date']).dt.date
    all_prices_df = all_prices_df.sort_values(['symbol_id', 'date'])

    to_delete_vids = []
    to_delete_indicators = []
    objects_to_insert = []
    
    total_modes = len(theme_modes)
    for index, (v_id, mode_info) in enumerate(theme_modes.items(), 1):
        mode, current_hash, last_date = mode_info
        item = v_id_to_item.get(v_id)
        ticker = item['ticker'] if item else f"ID {v_id}"
        
        # 進行ログの出力
        if index % 10 == 0 or index == total_modes:
            logger.info(f"Virtual Index Progress: {index}/{total_modes} - Processing {ticker} ({mode})")
            
        c_syms = theme_to_symbols.get(v_id, [])
        
        theme_prices_df = all_prices_df[all_prices_df['symbol_id'].isin(c_syms)]
        if theme_prices_df.empty:
            continue
            
        if mode == 'rebuild':
            to_delete_vids.append(v_id)
            to_delete_indicators.append(v_id)
            
            df = theme_prices_df.copy()
            df['close_prev'] = df.groupby('symbol_id')['close'].shift(1)
            
            # 先に売買代金と21日平均およびSurgeの計算を df 全体（Day 1含む）で行う
            df['dollar_volume'] = df['close'] * df['volume'].fillna(0)
            df['dollar_volume_ma21'] = df.groupby('symbol_id')['dollar_volume'].transform(
                lambda x: x.rolling(window=21, min_periods=1).mean()
            )
            import numpy as np
            df['surge'] = np.where(
                df['dollar_volume_ma21'] == 0,
                1.0,
                df['dollar_volume'] / df['dollar_volume_ma21']
            )
            df['surge'] = df['surge'].fillna(1.0)
            
            df_clean = df.dropna(subset=['close_prev']).copy()
            
            if df_clean.empty:
                continue
                
            df_clean['ret'] = df_clean['close'] / df_clean['close_prev'] - 1
            df_clean['open_ratio'] = df_clean['open'] / df_clean['close_prev']
            df_clean['high_ratio'] = df_clean['high'] / df_clean['close_prev']
            df_clean['low_ratio'] = df_clean['low'] / df_clean['close_prev']
            
            daily_avg = df_clean.groupby('date').agg({
                'ret': 'mean',
                'open_ratio': 'mean',
                'high_ratio': 'mean',
                'low_ratio': 'mean',
                'surge': 'mean'
            }).reset_index().sort_values('date')
            
            if daily_avg.empty:
                continue
                
            current_val = 1000.0
            for _, row in daily_avg.iterrows():
                prev_val = current_val
                current_val *= (1 + row['ret'])
                
                o_val = prev_val * row['open_ratio']
                h_val = prev_val * row['high_ratio']
                l_val = prev_val * row['low_ratio']
                c_val = current_val
                
                h_final = max(o_val, h_val, l_val, c_val)
                l_final = min(o_val, h_val, l_val, c_val)
                
                objects_to_insert.append(DailyPrice(
                    symbol_id=v_id,
                    date=row['date'],
                    open=o_val,
                    high=h_final,
                    low=l_final,
                    close=c_val,
                    volume=float(row['surge'] * 1000000.0)
                ))
                
        elif mode == 'incremental':
            prev_row = db.query(DailyPrice)\
                         .filter(DailyPrice.symbol_id == v_id, DailyPrice.date < last_date)\
                         .order_by(DailyPrice.date.desc()).first()
            if not prev_row:
                to_delete_vids.append(v_id)
                to_delete_indicators.append(v_id)
                df = theme_prices_df.copy()
                df['close_prev'] = df.groupby('symbol_id')['close'].shift(1)
                
                # 先に売買代金と21日平均およびSurgeの計算を df 全体（Day 1含む）で行う
                df['dollar_volume'] = df['close'] * df['volume'].fillna(0)
                df['dollar_volume_ma21'] = df.groupby('symbol_id')['dollar_volume'].transform(
                    lambda x: x.rolling(window=21, min_periods=1).mean()
                )
                import numpy as np
                df['surge'] = np.where(
                    df['dollar_volume_ma21'] == 0,
                    1.0,
                    df['dollar_volume'] / df['dollar_volume_ma21']
                )
                df['surge'] = df['surge'].fillna(1.0)
                
                df_clean = df.dropna(subset=['close_prev']).copy()
                if not df_clean.empty:
                    df_clean['ret'] = df_clean['close'] / df_clean['close_prev'] - 1
                    df_clean['open_ratio'] = df_clean['open'] / df_clean['close_prev']
                    df_clean['high_ratio'] = df_clean['high'] / df_clean['close_prev']
                    df_clean['low_ratio'] = df_clean['low'] / df_clean['close_prev']
                    
                    daily_avg = df_clean.groupby('date').agg({
                        'ret': 'mean',
                        'open_ratio': 'mean',
                        'high_ratio': 'mean',
                        'low_ratio': 'mean',
                        'surge': 'mean'
                    }).reset_index().sort_values('date')
                    
                    if not daily_avg.empty:
                        current_val = 1000.0
                        for _, row in daily_avg.iterrows():
                            prev_val = current_val
                            current_val *= (1 + row['ret'])
                            
                            o_val = prev_val * row['open_ratio']
                            h_val = prev_val * row['high_ratio']
                            l_val = prev_val * row['low_ratio']
                            c_val = current_val
                            
                            h_final = max(o_val, h_val, l_val, c_val)
                            l_final = min(o_val, h_val, l_val, c_val)
                            
                            objects_to_insert.append(DailyPrice(
                                symbol_id=v_id,
                                date=row['date'],
                                open=o_val,
                                high=h_final,
                                low=l_final,
                                close=c_val,
                                volume=float(row['surge'] * 1000000.0)
                            ))
                continue
                
            seed_date = prev_row.date
            seed_price = prev_row.close
            
            df = theme_prices_df[theme_prices_df['date'] >= seed_date].copy()
            df['close_prev'] = df.groupby('symbol_id')['close'].shift(1)
            
            # 先に売買代金と21日平均およびSurgeの計算を df 全体（Day 1含む）で行う
            df['dollar_volume'] = df['close'] * df['volume'].fillna(0)
            df['dollar_volume_ma21'] = df.groupby('symbol_id')['dollar_volume'].transform(
                lambda x: x.rolling(window=21, min_periods=1).mean()
            )
            import numpy as np
            df['surge'] = np.where(
                df['dollar_volume_ma21'] == 0,
                1.0,
                df['dollar_volume'] / df['dollar_volume_ma21']
            )
            df['surge'] = df['surge'].fillna(1.0)
            
            df_clean = df.dropna(subset=['close_prev']).copy()
            
            db.query(DailyPrice).filter(DailyPrice.symbol_id == v_id, DailyPrice.date >= last_date).delete()
            
            if not df_clean.empty:
                df_clean['ret'] = df_clean['close'] / df_clean['close_prev'] - 1
                df_clean['open_ratio'] = df_clean['open'] / df_clean['close_prev']
                df_clean['high_ratio'] = df_clean['high'] / df_clean['close_prev']
                df_clean['low_ratio'] = df_clean['low'] / df_clean['close_prev']
                
                daily_avg = df_clean.groupby('date').agg({
                    'ret': 'mean',
                    'open_ratio': 'mean',
                    'high_ratio': 'mean',
                    'low_ratio': 'mean',
                    'surge': 'mean'
                }).reset_index().sort_values('date')
                
                current_val = seed_price
                for _, row in daily_avg.iterrows():
                    prev_val = current_val
                    current_val *= (1 + row['ret'])
                    if row['date'] >= last_date:
                        o_val = prev_val * row['open_ratio']
                        h_val = prev_val * row['high_ratio']
                        l_val = prev_val * row['low_ratio']
                        c_val = current_val
                        
                        h_final = max(o_val, h_val, l_val, c_val)
                        l_final = min(o_val, h_val, l_val, c_val)
                        
                        objects_to_insert.append(DailyPrice(
                            symbol_id=v_id,
                            date=row['date'],
                            open=o_val,
                            high=h_final,
                            low=l_final,
                            close=c_val,
                            volume=float(row['surge'] * 1000000.0)
                        ))

    if to_delete_vids:
        db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(to_delete_vids)).delete(synchronize_session=False)
    if to_delete_indicators:
        db.query(Indicator).filter(Indicator.symbol_id.in_(to_delete_indicators)).delete(synchronize_session=False)
        
    if objects_to_insert:
        db.bulk_save_objects(objects_to_insert)
        
    db.commit()

    hash_dir = os.path.dirname(hash_file_path)
    if hash_dir and not os.path.exists(hash_dir):
        os.makedirs(hash_dir, exist_ok=True)

    with open(hash_file_path, 'w', encoding='utf-8') as f:
        json.dump(new_hashes, f, ensure_ascii=False, indent=2)

    if rebuild_ranks:
        db.query(RelativeRank).filter(RelativeRank.group_name == "テーマ").delete(synchronize_session=False)
        db.commit()

from datetime import datetime, date

def should_run_pipeline_for_date(db, current_time: datetime, spy_latest_date: date) -> tuple[bool, str]:
    """
    Determines whether the pipeline should execute for the given SPY date based on metadata.
    Handles 2-phase updates (morning unconfirmed prompt vs afternoon confirmed confirm).
    """
    import pytz
    from datetime import datetime as dt
    from pipeline.utils import get_pipeline_meta
    
    est_tz = pytz.timezone('America/New_York')
    # Make sure current_time has tzinfo, default to UTC if missing
    if current_time.tzinfo is None:
        current_time = pytz.utc.localize(current_time)
    current_time_est = current_time.astimezone(est_tz)
    
    last_completed_utc, last_spy_date = get_pipeline_meta(db)
    
    if last_spy_date is None or last_completed_utc is None:
        # First execution or empty metadata
        return True, "prompt"
        
    if spy_latest_date > last_spy_date:
        # New trading day has started
        return True, "prompt"
        
    if spy_latest_date < last_spy_date:
        # DB date is somehow ahead (rollback safety)
        return False, "skip"
        
    # Same trading day (spy_latest_date == last_spy_date)
    # Check if we need volume confirmation (EST 22:00 = UTC next day 02:00/03:00)
    # Ensure last_completed_utc has tzinfo
    if last_completed_utc.tzinfo is None:
        last_completed_utc = pytz.utc.localize(last_completed_utc)
    last_completed_est = last_completed_utc.astimezone(est_tz)
    
    # 22:00 America/New_York on the SPY trading date is the limit for unconfirmed data
    confirmation_limit = est_tz.localize(dt.combine(spy_latest_date, dt.min.time().replace(hour=22)))

    
    is_last_run_unconfirmed = last_completed_est < confirmation_limit
    is_now_confirmed_window = current_time_est >= confirmation_limit
    
    if is_last_run_unconfirmed and is_now_confirmed_window:
        return True, "confirm"
        
    return False, "skip"

def clear_pipeline_data_for_date(db, target_date: date):
    """
    Clears all downstream pipeline data (T2 to T5) for a specific date
    to prepare for a clean rebuild/overwrite of that day's data.
    """
    db.query(MarketSignal).filter(MarketSignal.date == target_date).delete(synchronize_session=False)
    db.query(RelativeRank).filter(RelativeRank.date == target_date).delete(synchronize_session=False)
    db.query(Indicator).filter(Indicator.date == target_date).delete(synchronize_session=False)
    db.query(DailyPrice).filter(DailyPrice.date == target_date).delete(synchronize_session=False)
    db.commit()

def safe_wal_checkpoint(db, logger: logging.Logger):
    """
    Safely commits the transaction and runs SQLite WAL checkpoint (TRUNCATE).
    Does not crash the pipeline if checkpoint fails due to concurrent read locks.
    """
    try:
        db.commit()
        db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    except Exception as e:
        logger.warning(f"WAL checkpoint (TRUNCATE) skipped/failed (likely active read locks): {e}")

def verify_pipeline_integrity(db, categories: Optional[List[str]] = None):
    """
    Validates data integrity of the pipeline output:
    1. Ensures SPY latest date is aligned with the overall max price date in the database.
    2. Ensures DailyPrice count and Indicator count match exactly on the latest date.
    
    Raises ValueError if any integrity check fails, triggering transaction rollback.
    """
    from db.models import Symbol, DailyPrice, Indicator
    from sqlalchemy import func
    
    # 1. Check SPY date alignment
    spy = db.query(Symbol).filter(Symbol.ticker == "SPY", Symbol.active == 1).first()
    if not spy:
        # If SPY is not registered, we can't align it. (This should only happen in bare tests)
        return
        
    spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy.id).scalar()
    overall_latest_date = db.query(func.max(DailyPrice.date)).scalar()
    
    if overall_latest_date and spy_latest_date != overall_latest_date:
        raise ValueError(
            f"SPY date is not aligned with the latest market data. "
            f"SPY: {spy_latest_date}, Overall Max: {overall_latest_date}"
        )
        
    # 2. Check DailyPrice vs Indicator count matching on latest date
    # Only perform count audit if we ran a full execution (categories is None)
    if categories is None and spy_latest_date:
        t2_count = db.query(func.count(DailyPrice.id)).filter(DailyPrice.date == spy_latest_date).scalar() or 0
        t3_count = db.query(func.count(Indicator.id)).filter(Indicator.date == spy_latest_date).scalar() or 0
        
        if t2_count != t3_count:
            # Find symbols having DailyPrice on spy_latest_date but lacking Indicator for troubleshooting
            prices_query = db.query(DailyPrice.symbol_id).filter(DailyPrice.date == spy_latest_date)
            missing_indicators = db.query(Symbol.ticker)\
                .filter(Symbol.id.in_(prices_query), Symbol.active == 1)\
                .filter(~Symbol.id.in_(
                    db.query(Indicator.symbol_id).filter(Indicator.date == spy_latest_date)
                )).all()
            missing_tickers = [row[0] for row in missing_indicators]
            
            raise ValueError(
                f"Mismatched record counts on latest date {spy_latest_date}. "
                f"DailyPrices: {t2_count}, Indicators: {t3_count}. "
                f"Missing indicators for tickers: {missing_tickers[:20]}"
            )

def run_pipeline(config, db_path, logger: logging.Logger, rebuild_from: Optional[str] = None, categories: Optional[List[str]] = None, skip_fetch: bool = False, skip_sync: bool = False, skip_t3: bool = False, recalculate_all: bool = False):
    """
    Main Orchestrator for Step 3 Pipeline.
    """
    logger.info(f"Starting Step 3 Pipeline Orchestrator - rebuild_from={rebuild_from}, categories={categories}, re-calculate={recalculate_all}")
    init_db(db_path)
    db_path = get_active_db_path()
    
    start_time_utc = datetime.utcnow()
    has_explicit_rebuild = (rebuild_from is not None) or recalculate_all
    
    # 1. Autonomous check for 2-phase update determination (if no explicit rebuild is requested)
    spy_latest_date = None
    if not has_explicit_rebuild and not skip_fetch:
        try:
            import yfinance as yf
            logger.info("Performing autonomous pre-check on SPY latest date from yfinance...")
            spy_df = yf.download("SPY", period="1d", progress=False)
            if spy_df is not None and not spy_df.empty:
                # pandas datetime to date object
                spy_latest_date = spy_df.index[-1].date()
                logger.info(f"Latest SPY date check result: {spy_latest_date}")
        except Exception as e:
            logger.warning(f"Could not perform autonomous pre-check of SPY date: {e}")

        if spy_latest_date:
            with get_write_db() as db:
                should_run, run_mode = should_run_pipeline_for_date(db, start_time_utc, spy_latest_date)
                
            if not should_run:
                logger.info(f"Pipeline execution SKIPPED autonomously. Data for SPY date {spy_latest_date} is already confirmed and up to date.")
                return
                
            if run_mode == "confirm":
                logger.info(f"Pipeline triggered in Autonomous VOLUME CONFIRMATION Mode for date: {spy_latest_date}")
                logger.info(f"Clearing old morning data for date {spy_latest_date} to prepare for clean overwrite...")
                with get_write_db() as db:
                    clear_pipeline_data_for_date(db, spy_latest_date)
            else:
                logger.info(f"Pipeline triggered in Autonomous PROMPT Mode for new trading day: {spy_latest_date}")
                
    lvl_map = {'T2': 2, 'T3': 3, 'T4': 4, 'T5': 5}
    active_lvl = lvl_map.get(str(rebuild_from).upper(), 0)
    if recalculate_all: active_lvl = 2

    try:
        with get_write_db() as db:
            if not skip_sync:
                logger.info("Starting T1: Symbol Sync...")
                t_start = time.time()
                extra_symbols = config.get("data_collection", {}).get("symbols", [])
                sheet_data, symbol_id_map = sync_symbols_to_db(
                    db, 
                    config["system"].get("credentials_path", "credentials.json"), 
                    config["system"].get("spreadsheet_url", "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0"),
                    extra_symbols=extra_symbols
                )
                logger.info(f"T1: Symbol Sync completed in {time.time() - t_start:.2f}s")
            else:
                symbols = db.query(Symbol).filter(Symbol.active == True).all()
                sheet_data = [{'ticker': s.ticker, 'exchange': s.exchange, 'category': s.category, 'theme_type': s.theme_type} for s in symbols]
                symbol_id_map = {(s.ticker, s.exchange): s.id for s in symbols}
            
            safe_wal_checkpoint(db, logger)
            
            if categories:
                sheet_data = [s for s in sheet_data if s['category'] in categories or s['ticker'] == 'SPY']
            
            target_ids = [symbol_id_map.get((s['ticker'], s['exchange'])) for s in sheet_data]

            if active_lvl > 0:
                logger.info(f"Cascaded Rebuild Triggered: Clearing downstream from level {active_lvl}")
                if active_lvl <= 5: db.query(MarketSignal).delete(synchronize_session=False)
                if active_lvl <= 4:
                    if categories: db.query(RelativeRank).filter(RelativeRank.symbol_id.in_(target_ids)).delete(synchronize_session=False)
                    else: db.query(RelativeRank).delete(synchronize_session=False)
                if active_lvl <= 3:
                    if categories: db.query(Indicator).filter(Indicator.symbol_id.in_(target_ids)).delete(synchronize_session=False)
                    else: db.query(Indicator).delete(synchronize_session=False)
                if active_lvl <= 2:
                    if categories:
                        spy_id = db.query(Symbol.id).filter(Symbol.ticker == 'SPY').scalar()
                        q = db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(target_ids))
                        if spy_id: q = q.filter(DailyPrice.symbol_id != spy_id)
                        q.delete(synchronize_session=False)
                db.commit()

            logger.info("Starting T2: Prices...")
            t_start = time.time()
            spy_latest_date = sync_phase_t2_prices(
                db, sheet_data, symbol_id_map, 
                config["data_collection"]["index_start_date"], 
                config["data_collection"]["default_start_date"], 
                skip_fetch, logger
            )
            logger.info(f"T2: Prices completed in {time.time() - t_start:.2f}s")
            
            logger.info("Starting FX Rates Sync...")
            t_fx_start = time.time()
            fx_success = sync_fx_rates(db, skip_fetch=skip_fetch, logger=logger)
            if fx_success:
                logger.info(f"FX Rates Sync completed in {time.time() - t_fx_start:.2f}s")
            else:
                logger.warning("FX Rates Sync failed.")
            
            virtual_items = [d for d in sheet_data if d['theme_type'] == 'virtual']
            if virtual_items:
                logger.info(f"Starting Virtual Index Build for {len(virtual_items)} items...")
                t_start = time.time()
                build_all_virtual_indexes_prices(db, virtual_items, symbol_id_map)
                logger.info(f"Virtual Index Build completed in {time.time() - t_start:.2f}s")
            
            safe_wal_checkpoint(db, logger)

            if not skip_t3:
                logger.info("Starting T3: Indicators...")
                t_start = time.time()
                sync_phase_t3_indicators(db, sheet_data, symbol_id_map, spy_latest_date, skip_fetch, db_path, logger)
                logger.info(f"T3: Indicators completed in {time.time() - t_start:.2f}s")
            
            safe_wal_checkpoint(db, logger)
            
            logger.info("Starting T4: Ranks...")
            t_start = time.time()
            default_start_str = config.get("data_collection", {}).get("default_start_date", "2018-04-01")
            default_start_val = datetime.strptime(default_start_str, "%Y-%m-%d").date()
            sync_phase_t4_ranks(db, spy_latest_date, logger, default_start_date=default_start_val)
            logger.info(f"T4: Ranks completed in {time.time() - t_start:.2f}s")
            
            safe_wal_checkpoint(db, logger)
            
            logger.info("Starting T5: Signals...")
            t_start = time.time()
            sync_phase_t5_signals(db, logger)
            logger.info(f"T5: Signals completed in {time.time() - t_start:.2f}s")

            # Hot/Cold Hybrid Data Architecture: Archive to Parquet master & Purge SQLite cache
            try:
                from pipeline.parquet_cache_manager import rotate_and_archive_to_parquet, purge_sqlite_cache_older_than_2_years
                rotate_and_archive_to_parquet(db, db_path, logger)
                purge_sqlite_cache_older_than_2_years(db, db_path, logger)
            except Exception as pe:
                logger.error(f"Failed to run Hot/Cold archiving & purging: {pe}")
                # We do not crash the pipeline if archiving fails to keep daily updates robust

            # Save pipeline execution metadata to allow self-determining updates next run
            if spy_latest_date:
                from pipeline.utils import update_pipeline_meta
                logger.info(f"Saving pipeline metadata: start_time={start_time_utc}, spy_date={spy_latest_date}")
                update_pipeline_meta(db, start_time_utc, spy_latest_date)

            safe_wal_checkpoint(db, logger)

            # Run final data integrity audits before committing the transaction
            logger.info("Running final pipeline data integrity verification...")
            verify_pipeline_integrity(db, categories=categories)

            logger.info("--- Step 3 Pipeline COMPLETED SUCCESSFULLY ---")
    except Exception as e:
        logger.error(f"--- Step 3 Pipeline FAILURE --- Type: {type(e).__name__}, Message: {str(e)}")
        raise e
