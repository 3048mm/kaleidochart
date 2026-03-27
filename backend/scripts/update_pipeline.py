import os
import sys
import time
import logging
import tomli
import pandas as pd
import argparse
from datetime import datetime, timedelta, date
from typing import List, Optional
from sqlalchemy import func

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import init_db, get_db
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning
from data_collection.fetcher import fetch_daily_data, fetch_multiple_daily_data, fetch_fundamentals
from data_collection.spreadsheet_sync import fetch_symbols_from_sheet
from indicators.calculator import calculate_indicators, calculate_relative_ranks, calculate_market_signals

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomli.load(f)

def _to_val(row, key):
    v = row.get(key)
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
    except Exception:
        pass
    return v

def sync_symbols_to_db(db, credentials_path, spreadsheet_url):
    """
    Fetch from spreadsheet and upsert T1 symbols and ThemeConstituents mapping.
    """
    sheet_data = fetch_symbols_from_sheet(credentials_path, spreadsheet_url)
    
    # 1. Upsert Symbols
    logger.info("Upserting symbols to database...")
    
    # Mark all existing as inactive first (soft delete)
    db.query(Symbol).update({"active": 0})
    
    symbol_ids = {} # (ticker, exchange) -> id
    
    for item in sheet_data:
        sym = db.query(Symbol).filter(Symbol.ticker == item['ticker'], Symbol.exchange == item['exchange']).first()
        if sym:
            sym.name = item['name']
            sym.category = item['category']
            sym.asset_class = item['asset_class']
            sym.theme_type = item['theme_type']
            sym.tags = item['tags']
            sym.active = 1
        else:
            sym = Symbol(
                ticker=item['ticker'],
                exchange=item['exchange'],
                name=item['name'],
                category=item['category'],
                asset_class=item['asset_class'],
                theme_type=item['theme_type'],
                tags=item['tags'],
                active=1
            )
            db.add(sym)
        
        db.flush()
        symbol_ids[(sym.ticker, sym.exchange)] = sym.id
    
    db.commit()
    logger.info(f"Updated {len(sheet_data)} active symbols.")
    
    # 2. Rebuild ThemeConstituents (VIRTUAL mappings)
    logger.info("Rebuilding Theme Constituents mapping...")
    db.query(ThemeConstituent).delete()
    db.commit()
    
    virtuals = [s for s in sheet_data if s['theme_type'] == 'virtual']
    constituents_to_add = []
    
    for v_item in virtuals:
        v_id = symbol_ids[(v_item['ticker'], v_item['exchange'])]
        
        # We need to find stocks that have tags loosely matching the virtual name
        # Ex: if virtual name is "フォトニクス", find stocks with "フォトニクス" in tags
        # In a real scenario, you can define a more rigorous mapping rule (e.g. tag == v_item.ticker)
        # For now, we will map by checking if the VIRTUAL ticker name (striped of __) or Name is in tags
        target_tag = v_item['name'].strip()
        if not target_tag:
            # Fallback to Ticker if Name is empty
            target_tag = v_item['ticker'].strip('_')
            
        # Find active symbols containing this tag
        matching_symbols = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.tags.like(f"%{target_tag}%"),
            (Symbol.theme_type != 'virtual') | (Symbol.theme_type.is_(None))
        ).all()
        
        if matching_symbols:
            logger.info(f"Virtual theme '{v_item['ticker']}' ({target_tag}) matched {len(matching_symbols)} constituents.")
            weight = 1.0 / len(matching_symbols) # Equal weighting
            for m_sym in matching_symbols:
                constituents_to_add.append(
                    ThemeConstituent(
                        theme_id=v_id,
                        symbol_id=m_sym.id,
                        weight=weight
                    )
                )
        else:
            logger.warning(f"Virtual theme '{v_item['ticker']}' ({target_tag}) has no matching constituent symbols.")
            
    if constituents_to_add:
        db.bulk_save_objects(constituents_to_add)
        db.commit()
        
    return sheet_data, symbol_ids

def build_virtual_index_prices(db, virtual_id: int):
    """
    Idea C: Synthesize DailyPrice for a virtual index based on equally weighted returns of constituents.
    Base value is 1000.
    """
    # Get constituents
    mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == virtual_id).all()
    if not mappings:
        return pd.DataFrame()
        
    c_ids = [m.symbol_id for m in mappings]
    
    # Query all prices for these constituents - Chunked to avoid SQL variable limit (SQLite default 999)
    all_prices = []
    chunk_size = 900
    for i in range(0, len(c_ids), chunk_size):
        chunk = c_ids[i:i + chunk_size]
        prices = db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(chunk)).all()
        all_prices.extend(prices)
        
    if not all_prices:
        return pd.DataFrame()
        
    # Build dataframe: date, symbol_id, close
    df = pd.DataFrame([{
        'date': p.date,
        'symbol_id': p.symbol_id,
        'close': p.close
    } for p in all_prices if p.close is not None and p.close > 0])
    
    if df.empty:
        return pd.DataFrame()
        
    # Calculate daily returns for each symbol
    df = df.sort_values('date')
    df['ret'] = df.groupby('symbol_id')['close'].pct_change()
    
    # Average the returns cross-sectionally per day
    daily_avg_ret = df.dropna(subset=['ret']).groupby('date')['ret'].mean().reset_index()
    daily_avg_ret = daily_avg_ret.sort_values('date')
    
    if daily_avg_ret.empty:
        return pd.DataFrame()
        
    # Compound returns starting from 1000
    base_value = 1000.0
    synth_closes = []
    
    current_val = base_value
    for _, row in daily_avg_ret.iterrows():
        current_val *= (1 + row['ret'])
        synth_closes.append(current_val)
        
    daily_avg_ret['close'] = synth_closes
    daily_avg_ret['open'] = daily_avg_ret['close']
    daily_avg_ret['high'] = daily_avg_ret['close']
    daily_avg_ret['low'] = daily_avg_ret['close']
    daily_avg_ret['volume'] = 0
    daily_avg_ret['symbol_id'] = virtual_id
    
    return daily_avg_ret


def run_step3_pipeline(recalculate_all: bool = False, categories: Optional[List[str]] = None, skip_fetch: bool = False, skip_sync: bool = False, skip_t3: bool = False):
    logger.info(f"Starting Step 3 Data Pipeline (Sheet Sync + Virtual Index) - recalculate_all={recalculate_all}, categories={categories}")
    
    config = load_config()
    db_path = config["system"]["db_path"]
    spreadsheet_url = config["system"].get("spreadsheet_url", "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0")
    credentials_path = config["system"].get("credentials_path", "credentials.json")
    initial_fetch_days = config["data_collection"]["initial_fetch_days"]
    sleep_seconds = config["data_collection"]["sleep_seconds"]
    
    init_db(db_path)
    
    with get_db() as db:
        # 1. Sync from Spreadsheets
        if not skip_sync:
            logger.info("Using Google Spreadsheet for symbol sync...")
            sheet_data, symbol_id_map = sync_symbols_to_db(db, credentials_path, spreadsheet_url)
        else:
            logger.info("Skipping Google Spreadsheet sync. Using existing symbols in DB.")
            symbols = db.query(Symbol).filter(Symbol.active == True).all()
            sheet_data = []
            symbol_id_map = {}
            for s in symbols:
                # Mock sheet_data format for downstream
                sheet_data.append({
                    'ticker': s.ticker,
                    'exchange': s.exchange,
                    'category': s.category,
                    'theme_type': s.theme_type
                })
                symbol_id_map[(s.ticker, s.exchange)] = s.id
        
        # --- Category Filter ---
        if categories:
            original_count = len(sheet_data)
            sheet_data = [s for s in sheet_data if s['category'] in categories]
            logger.info(f"Filtered symbols by categories {categories}: {len(sheet_data)} / {original_count}")
        
        # Split into real vs virtual
        real_tickers = [d['ticker'] for d in sheet_data if d['theme_type'] != 'virtual']
        virtual_items = [d for d in sheet_data if d['theme_type'] == 'virtual']
        
        # 2. Sync from Price Data
        data_dict = {}
        
        # 2. Fetch SPY first to determine the target (latest) date
        logger.info("Fetching SPY data first to determine target date...")
        spy_full_start = (datetime.now() - timedelta(days=initial_fetch_days)).strftime('%Y-%m-%d')
        
        spy_item = next((d for d in sheet_data if d['ticker'] == 'SPY'), None)
        spy_sym_id = symbol_id_map.get(('SPY', spy_item['exchange'])) if spy_item else None
        
        # Get SPY's current latest date in DB
        spy_latest_in_db = None
        if spy_sym_id:
            row = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
            spy_latest_in_db = row
        
        # Determine SPY fetch start (incremental if exists, full otherwise)
        if spy_latest_in_db:
            spy_fetch_start = (datetime.combine(spy_latest_in_db, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"SPY exists in DB up to {spy_latest_in_db}. Fetching incremental from {spy_fetch_start}...")
        else:
            spy_fetch_start = spy_full_start
            logger.info(f"SPY not in DB. Fetching full history from {spy_fetch_start}...")
        
        spy_new_df = pd.DataFrame()
        if not skip_fetch:
            spy_new_df = fetch_daily_data("SPY", spy_fetch_start)
        else:
            logger.info("Skipping SPY fetch due to --skip-fetch.")
        
        # Save new SPY rows (append only - do not delete)
        if not spy_new_df.empty:
            existing_spy_dates = set(
                str(r[0]) for r in db.query(DailyPrice.date)
                .filter(DailyPrice.symbol_id == spy_sym_id).all()
            ) if spy_sym_id and spy_latest_in_db else set()
            
            new_spy_records = [
                DailyPrice(
                    symbol_id=spy_sym_id,
                    date=row['date'],
                    open=_to_val(row, 'open'),
                    high=_to_val(row, 'high'),
                    low=_to_val(row, 'low'),
                    close=_to_val(row, 'close'),
                    volume=int(row['volume']) if _to_val(row, 'volume') is not None else 0,
                )
                for _, row in spy_new_df.iterrows()
                if str(row['date']) not in existing_spy_dates
            ]
            if new_spy_records:
                db.bulk_save_objects(new_spy_records)
                db.commit()
                logger.info(f"SPY: inserted {len(new_spy_records)} new rows.")
                data_dict["SPY"] = spy_new_df # Track so T3 calculation is triggered
            else:
                logger.info("SPY: no new rows to insert.")
                data_dict["SPY"] = None # Track so T3 calculation logic can still check it
        
        # Get SPY's latest date (now updated)
        spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar() if spy_sym_id else None
        logger.info(f"SPY target date (latest): {spy_latest_date}")
        
        # Load full SPY for indicator calculations
        spy_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()
        spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in spy_all])
        
        # 3. Get latest date in DB for each symbol (bulk query)
        latest_date_rows = db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()
        sym_latest_date_map = {sym_id: latest for sym_id, latest in latest_date_rows}
        
        logger.info("Fetching/updating price data for real symbols (incremental)...")
        for item in sheet_data:
            if item['theme_type'] == 'virtual':
                continue
            if item['ticker'] == 'SPY':
                continue  # Already handled above
                
            ticker = item['ticker']
            sym_id = symbol_id_map[(ticker, item['exchange'])]
            sym_latest = sym_latest_date_map.get(sym_id)
            
            # Skip fetch logic
            if skip_fetch:
                logger.debug(f"[{ticker}] --skip-fetch is set. Using existing DB data.")
                data_dict[ticker] = None # Signal to Use existing T2
                continue

            # Skip if already up to date
            if sym_latest and spy_latest_date and str(sym_latest) >= str(spy_latest_date):
                logger.debug(f"[{ticker}] Already up to date ({sym_latest}). Skipping.")
                data_dict[ticker] = None  # Signal: skip indicator recalc
                continue
            
            # Determine fetch start date
            if isinstance(sym_latest, date):
                fetch_start = (datetime.combine(sym_latest, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
                logger.info(f"[{ticker}] Incremental fetch from {fetch_start} (latest in DB: {sym_latest}).")
            else:
                fetch_start = spy_full_start
                logger.info(f"[{ticker}] Full fetch from {fetch_start} (new symbol).")
            
            df_new = fetch_daily_data(ticker, fetch_start)
            time.sleep(sleep_seconds)
            
            if df_new is None or df_new.empty:
                data_dict[ticker] = None
                continue
            
            # Append only new rows (avoid duplicates)
            existing_dates = set(
                str(r[0]) for r in db.query(DailyPrice.date)
                .filter(DailyPrice.symbol_id == sym_id).all()
            ) if sym_latest else set()
            
            new_rows = [
                DailyPrice(
                    symbol_id=sym_id,
                    date=row['date'],
                    open=_to_val(row, 'open'),
                    high=_to_val(row, 'high'),
                    low=_to_val(row, 'low'),
                    close=_to_val(row, 'close'),
                    volume=int(row['volume']) if _to_val(row, 'volume') is not None else 0,
                )
                for _, row in df_new.iterrows()
                if str(row['date']) not in existing_dates
            ]
            if new_rows:
                db.bulk_save_objects(new_rows)
                db.commit()
                data_dict[ticker] = df_new  # Has new data → needs indicator recalc
            else:
                data_dict[ticker] = None
        
        db.commit()
        
        # 4. Synthesize T2 for Virtual Themes (always recalculate since constituents may have changed)
        logger.info("Synthesizing T2 DailyPrice records for virtual themes...")
        for v_item in virtual_items:
            v_ticker = v_item['ticker']
            v_id = symbol_id_map[(v_ticker, v_item['exchange'])]
            
            synth_df = build_virtual_index_prices(db, v_id)
            if not synth_df.empty:
                db.query(DailyPrice).filter(DailyPrice.symbol_id == v_id).delete()
                db.query(Indicator).filter(Indicator.symbol_id == v_id).delete()
                db.commit()
                
                t2_records = [
                    DailyPrice(
                        symbol_id=v_id,
                        date=row['date'],
                        open=row['open'],
                        high=row['high'],
                        low=row['low'],
                        close=row['close'],
                        volume=0
                    )
                    for _, row in synth_df.iterrows()
                ]
                db.bulk_save_objects(t2_records)
                data_dict[v_ticker] = synth_df  # Always recalc virtual
        db.commit()

        
        # --- Step 5: Calculate T3 Indicators for ALL symbols ---
        all_indicators_dfs = []
        if skip_t3:
            logger.info("Skipping T3 Indicator calculation (--skip-t3). Loading existing indicators from DB for T4 ranking...")
            indicators_to_rank = [
                'relative_strength_spy', 
                'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
                'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
                'rs_condition_14', 'rs_condition_21', 'rs_condition_63'
            ]
            # Join with Symbol to get 'category' group and only select required columns to save RAM
            query = db.query(
                Indicator.symbol_id,
                Indicator.date,
                Symbol.category,
                *[getattr(Indicator, col) for col in indicators_to_rank if hasattr(Indicator, col)]
            ).join(Symbol, Symbol.id == Indicator.symbol_id).filter(Symbol.active == 1)
            
            # Load into a single DataFrame for T4
            combined_df = pd.read_sql(query.statement, db.bind)
            logger.info(f"Loaded {len(combined_df)} indicators for ranking.")
        else:
            logger.info("Calculating T3 Indicators for all symbols...")
            processed_t3_sym_ids = set()
            for item in sheet_data:
                ticker = item['ticker']
                sym_id = symbol_id_map[(ticker, item['exchange'])]
                
                # Prevent duplicate processing if a ticker is in multiple sheets
                if sym_id in processed_t3_sym_ids:
                    continue
                processed_t3_sym_ids.add(sym_id)
                
                try:
                    has_new_data = ticker in data_dict and data_dict.get(ticker) is not None

                    # Check latest indicator date vs price date to ensure no gaps
                    latest_ind_date = db.query(func.max(Indicator.date)).filter(Indicator.symbol_id == sym_id).scalar()
                    latest_price_date = sym_latest_date_map.get(sym_id)
                    
                    is_up_to_date = (latest_ind_date and latest_price_date and latest_ind_date >= latest_price_date)

                    # recalculate_all が False かつ価格更新がない場合、かつインジケーターが既に最新の場合のみスキップ
                    if not recalculate_all and not has_new_data and ticker in data_dict and is_up_to_date:
                        # Symbol is up to date — load only the latest indicator row from DB for RS Rank
                        latest_ind = db.query(Indicator).filter(
                            Indicator.symbol_id == sym_id,
                            Indicator.date == latest_ind_date
                        ).first()
                        if latest_ind:
                            rs21 = latest_ind.rs_ratio_21
                            rs63 = latest_ind.rs_ratio_63
                            rs_mom21 = latest_ind.rs_momentum_21
                            if rs21 is not None or rs63 is not None:
                                mini = pd.DataFrame([{
                                    'symbol_id': sym_id,
                                    'category': item['category'],
                                    'date': latest_ind.date,
                                    'rs_ratio_21': rs21,
                                    'rs_ratio_63': rs63,
                                    'rs_momentum_21': rs_mom21,
                                }])
                                all_indicators_dfs.append(mini)
                        continue  # Skip full recalculation

                    if not has_new_data and ticker not in data_dict:
                        # Brand new ticker with no price data fetched, skip entirely
                        continue

                    # Has new data — load full history from DB and recalculate all indicators
                    all_prices = db.query(DailyPrice).filter(
                        DailyPrice.symbol_id == sym_id
                    ).order_by(DailyPrice.date).all()
                    if not all_prices:
                        continue
                    df = pd.DataFrame([{
                        'date': p.date, 'open': p.open, 'high': p.high,
                        'low': p.low, 'close': p.close, 'volume': p.volume
                    } for p in all_prices])

                    df_ind = calculate_indicators(df, spy_df if ticker != "SPY" else None)

                    # Fetch fundamentals for market_cap calculation
                    fund_res = {}
                    if not skip_fetch:
                        logger.debug(f"[{ticker}] Fetching fundamentals for market cap...")
                        fund_res = fetch_fundamentals(ticker)
                    
                    shares_df = fund_res.get("shares")
                    info = fund_res.get("info")
                    
                    df_ind['market_cap'] = None
                    if shares_df is not None and not shares_df.empty:
                        try:
                            shares_series = shares_df.iloc[:, 0]
                            shares_series.index = pd.to_datetime(shares_series.index).tz_localize(None).normalize()
                            shares_df_temp = shares_series.reset_index()
                            shares_df_temp.columns = ['date', 'shares']
                            shares_df_temp = shares_df_temp.sort_values('date')
                            
                            df_temp = pd.DataFrame({'date': pd.to_datetime(df['date'])})
                            merged_shares = pd.merge_asof(df_temp, shares_df_temp, on='date', direction='backward')
                            df_ind['market_cap'] = df['close'].values * merged_shares['shares'].values
                        except Exception as e:
                            logger.error(f"[{ticker}] Error calculating market cap from shares: {e}")
                    
                    # Fallback to info['marketCap'] for the most recent data if calculation failed or shares missing
                    if info and 'marketCap' in info:
                        latest_mcap = info['marketCap']
                        # If market_cap column is still all None or has NaNs at the end, fill with static info
                        if df_ind['market_cap'].isnull().all():
                            df_ind['market_cap'] = latest_mcap
                        else:
                            # Fill only the last row or latest missing ones
                            df_ind['market_cap'] = df_ind['market_cap'].fillna(latest_mcap)
                            
                    income_stmt = fund_res.get("income_stmt")
                    if income_stmt is not None and not income_stmt.empty:
                        try:
                            db.query(Earning).filter(Earning.symbol_id == sym_id).delete()
                            earning_records = []
                            for period_dt in income_stmt.columns:
                                if pd.isna(period_dt): continue
                                col_data = income_stmt[period_dt]
                                
                                def get_val(key):
                                    if key in col_data.index and pd.notna(col_data[key]):
                                        return float(col_data[key])
                                    return None
                                    
                                earning_records.append(Earning(
                                    symbol_id=sym_id,
                                    period_date=period_dt.date() if isinstance(period_dt, datetime) else period_dt,
                                    eps_basic=get_val('Basic EPS'),
                                    eps_diluted=get_val('Diluted EPS'),
                                    revenue=get_val('Total Revenue') or get_val('Operating Revenue'),
                                    net_income=get_val('Net Income')
                                ))
                            
                            if earning_records:
                                db.bulk_save_objects(earning_records)
                                # Commit happens below with T3 records
                        except Exception as e:
                            logger.error(f"[{ticker}] Error saving earnings: {e}")

                    # Save indicators (T3)
                    if recalculate_all:
                        # Clear existing history if requested
                        db.query(Indicator).filter(Indicator.symbol_id == sym_id).delete()
                        db.commit()
                        dates_to_save = df_ind['date'].tolist()
                    else:
                        # Only save rows that don't exist in DB (Incremental Save)
                        existing_ind_dates = set(
                            str(r[0]) for r in db.query(Indicator.date)
                            .filter(Indicator.symbol_id == sym_id).all()
                        )
                        dates_to_save = [
                            d for d in df_ind['date'].tolist()
                            if str(d) not in existing_ind_dates
                        ]

                    if dates_to_save:
                        # Filter df_ind to only new dates
                        df_to_save = df_ind[df_ind['date'].isin(dates_to_save)]
                        
                        t3_records = [
                            Indicator(
                                symbol_id=sym_id,
                                date=row['date'],
                                sma_5=_to_val(row, 'sma_5'),
                                sma_21=_to_val(row, 'sma_21'),
                                sma_50=_to_val(row, 'sma_50'),
                                sma_63=_to_val(row, 'sma_63'),
                                sma_150=_to_val(row, 'sma_150'),
                                sma_200=_to_val(row, 'sma_200'),
                                ema_5=_to_val(row, 'ema_5'),
                                ema_21=_to_val(row, 'ema_21'),
                                ema_50=_to_val(row, 'ema_50'),
                                ema_63=_to_val(row, 'ema_63'),
                                ema_150=_to_val(row, 'ema_150'), # Match calculator.py
                                ema_200=_to_val(row, 'ema_200'),
                                td9=int(row['td9']) if _to_val(row, 'td9') is not None else 0,
                                atr_14=_to_val(row, 'atr_14'),
                                atr_pct_14=_to_val(row, 'atr_pct_14'),
                                adr_pct_21=_to_val(row, 'adr_pct_21'),
                                dist_sma50_atr=_to_val(row, 'dist_sma50_atr'),
                                market_cap=_to_val(row, 'market_cap'),
                                relative_strength_spy=_to_val(row, 'relative_strength_spy'),
                                rs_condition_14=_to_val(row, 'rs_condition_14'),
                                rs_condition_21=_to_val(row, 'rs_condition_21'),
                                rs_condition_63=_to_val(row, 'rs_condition_63'),
                                rs_momentum_14=_to_val(row, 'rs_momentum_14'),
                                rs_momentum_21=_to_val(row, 'rs_momentum_21'),
                                rs_momentum_63=_to_val(row, 'rs_momentum_63'),
                                rs_ratio_14=_to_val(row, 'rs_ratio_14'),
                                rs_ratio_21=_to_val(row, 'rs_ratio_21'),
                                rs_ratio_63=_to_val(row, 'rs_ratio_63'),
                                vol_surge_21=_to_val(row, 'vol_surge_21'),
                                rel_vol_vs_spy_21=_to_val(row, 'rel_vol_vs_spy_21'),
                                pct_from_63d_high=_to_val(row, 'pct_from_63d_high'),
                                pct_from_52w_high=_to_val(row, 'pct_from_52w_high'),
                                trend_template_ok=int(row['trend_template_ok']) if _to_val(row, 'trend_template_ok') is not None else None,
                            )
                            for _, row in df_to_save.iterrows()
                        ]
                        db.bulk_save_objects(t3_records)
                        db.commit()
                        logger.debug(f"[{ticker}] Saved {len(t3_records)} indicator rows.")

                    df_ind['symbol_id'] = sym_id
                    df_ind['category'] = item['category']
                    all_indicators_dfs.append(df_ind)

                except Exception as e:
                    logger.error(f"[{ticker}] Critical error in indicator calculation: {e}")
                    db.rollback()
                    continue
            
        # 6. Relative Ranks (T4)
        if skip_t3 or all_indicators_dfs:
            logger.info("Calculating relative ranks (T4)...")
            if not skip_t3:
                combined_df = pd.concat(all_indicators_dfs, ignore_index=True)
                # Filter to only recent dates to avoid re-calculating/saving entire history
                threshold_date = datetime.strptime("2026-03-24", "%Y-%m-%d").date()
                combined_df = combined_df[combined_df['date'] >= threshold_date]
            
            # Clear existing ranks for full recalculation
            if recalculate_all:
                logger.info("Clearing ALL T4 RelativeRank records for full recalculation.")
                db.query(RelativeRank).delete()
                db.commit()
            
            t4_records = []
            indicators_to_rank = [
                'relative_strength_spy', 
                'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
                'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
                'rs_condition_14', 'rs_condition_21', 'rs_condition_63'
            ]
            for ind_col in indicators_to_rank:
                if ind_col not in combined_df.columns:
                    continue
                ranked_df = calculate_relative_ranks(combined_df, group_col='category', indicator_col=ind_col)
                
                # Optimized: avoid iterrows() and create a list of dicts for bulk insert
                # Filtering out NaNs first to reduce data volume
                valid_ranks = ranked_df[ranked_df['percent_rank'].notna()]
                
                new_records = [
                    RelativeRank(
                        symbol_id=int(row['symbol_id']),
                        date=row['date'],
                        group_name=row['group_name'],
                        indicator_name=row['indicator_name'],
                        percent_rank=float(row['percent_rank'])
                    )
                    for row in valid_ranks.to_dict('records')
                ]
                t4_records.extend(new_records)
                
                # Commit in chunks if very large
                if len(t4_records) > 100000:
                    logger.info(f"Saving chunk of {len(t4_records)} T4 records...")
                    db.bulk_save_objects(t4_records)
                    db.commit()
                    t4_records = []

            if t4_records:
                logger.info(f"Saving final chunk of {len(t4_records)} T4 records...")
                db.bulk_save_objects(t4_records)
                db.commit()
            
        # 7. Market Signals (T5)
        if not spy_df.empty:
            logger.info("Calculating market signals (T5)...")
            db.query(MarketSignal).delete()
            db.commit()
            
            ms_df = calculate_market_signals(spy_df)
            t5_records = [
                MarketSignal(
                    date=row['date'],
                    spy_above_sma200=int(row['spy_above_sma200']),
                    spy_sma200_rising=int(row['spy_sma200_rising']) if _to_val(row, 'spy_sma200_rising') is not None else None,
                    distribution_days=int(row['distribution_days']),
                    follow_through_day=int(row['follow_through_day']),
                    market_phase=row['market_phase'],
                )
                for _, row in ms_df.iterrows()
            ]
            db.bulk_save_objects(t5_records)
            db.commit()

    logger.info("Step 3 Pipeline completed successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Step 3 Data Pipeline.")
    parser.add_argument("--re-calculate", action="store_true", help="Redownload all data and recalculate all indicators (clean start for T3/T4).")
    parser.add_argument("--category", type=str, help="Comma-separated categories to process (e.g. '市場,指標,セクタ,テーマ').")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip yfinance price fetching, use existing DB data")
    parser.add_argument("--skip-sync", action="store_true", help="Skip Google Spreadsheet sync, use existing symbols in DB")
    parser.add_argument("--skip-t3", action="store_true", help="Skip T3 indicator calculation, jump to T4 ranking")
    args = parser.parse_args()
    
    selected_categories = None
    if args.category:
        selected_categories = [c.strip() for c in args.category.split(",")]
    
    run_step3_pipeline(
        recalculate_all=args.re_calculate, 
        categories=selected_categories,
        skip_fetch=args.skip_fetch,
        skip_sync=args.skip_sync,
        skip_t3=args.skip_t3
    )
