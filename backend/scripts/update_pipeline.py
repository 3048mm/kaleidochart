import os
import sys
import logging
import tomli
import pandas as pd
from datetime import datetime, timedelta

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

# Add project root for config.toml access
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from db.database import init_db, get_db
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent
from data_collection.fetcher import fetch_multiple_daily_data, fetch_fundamentals
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
    
    # Query all prices for these constituents
    prices = db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(c_ids)).all()
    if not prices:
        return pd.DataFrame()
        
    # Build dataframe: date, symbol_id, close
    df = pd.DataFrame([{
        'date': p.date,
        'symbol_id': p.symbol_id,
        'close': p.close
    } for p in prices if p.close is not None and p.close > 0])
    
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


def run_step3_pipeline():
    logger.info("Starting Step 3 Data Pipeline (Sheet Sync + Virtual Index)...")
    
    config = load_config()
    db_path = config["system"]["db_path"]
    spreadsheet_url = config["system"].get("spreadsheet_url", "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0")
    credentials_path = config["system"].get("credentials_path", "credentials.json")
    initial_fetch_days = config["data_collection"]["initial_fetch_days"]
    sleep_seconds = config["data_collection"]["sleep_seconds"]
    
    init_db(db_path)
    
    with get_db() as db:
        # 1. Sync from Spreadsheets
        logger.info("Using Google Spreadsheet for symbol sync...")
        sheet_data, symbol_id_map = sync_symbols_to_db(db, credentials_path, spreadsheet_url)
        
        # Split into real vs virtual
        real_tickers = [d['ticker'] for d in sheet_data if d['theme_type'] != 'virtual']
        virtual_items = [d for d in sheet_data if d['theme_type'] == 'virtual']
        
        # 2. Fetch real data
        start_date = (datetime.now() - timedelta(days=initial_fetch_days)).strftime('%Y-%m-%d')
        logger.info(f"Fetching historical data from {start_date} for {len(real_tickers)} real symbols...")
        data_dict = fetch_multiple_daily_data(real_tickers, start_date=start_date, sleep_seconds=sleep_seconds)
        
        spy_df = data_dict.get("SPY", pd.DataFrame())
        
        # 3. Save T2 for real symbols
        logger.info("Saving T2 DailyPrice records for real symbols...")
        
        for item in sheet_data:
            if item['theme_type'] == 'virtual':
                continue
                
            ticker = item['ticker']
            sym_id = symbol_id_map[(ticker, item['exchange'])]
            
            df = data_dict.get(ticker)
            if df is None or df.empty:
                continue
                
            db.query(DailyPrice).filter(DailyPrice.symbol_id == sym_id).delete()
            db.query(Indicator).filter(Indicator.symbol_id == sym_id).delete()
            db.commit()
            
            t2_records = [
                DailyPrice(
                    symbol_id=sym_id,
                    date=row['date'],
                    open=_to_val(row, 'open'),
                    high=_to_val(row, 'high'),
                    low=_to_val(row, 'low'),
                    close=_to_val(row, 'close'),
                    volume=int(row['volume']) if _to_val(row, 'volume') is not None else 0,
                )
                for _, row in df.iterrows()
            ]
            db.bulk_save_objects(t2_records)
        db.commit()
        
        # 4. Synthesize T2 for Virtual Themes
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
                data_dict[v_ticker] = synth_df  # Add to dictionary so indicators get calculated
        db.commit()
        
        # 5. Calculate T3 Indicators for ALL symbols
        logger.info("Calculating T3 Indicators for all symbols...")
        all_indicators_dfs = []
        processed_t3_sym_ids = set()
        
        for item in sheet_data:
            ticker = item['ticker']
            sym_id = symbol_id_map[(ticker, item['exchange'])]
            
            # Prevent duplicate processing if a ticker is in multiple sheets
            if sym_id in processed_t3_sym_ids:
                continue
            processed_t3_sym_ids.add(sym_id)
            
            df = data_dict.get(ticker)
            
            if df is None or df.empty:
                continue
                
            df_ind = calculate_indicators(df, spy_df if ticker != "SPY" else None)
            
            # Fetch fundamentals for market_cap calculation
            logger.info(f"[{ticker}] Fetching fundamentals for market cap...")
            fund_res = fetch_fundamentals(ticker)
            shares_df = fund_res.get("shares")
            
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
                    logger.error(f"[{ticker}] Error calculating market cap: {e}")

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
                for _, row in df_ind.iterrows()
            ]
            db.bulk_save_objects(t3_records)
            db.commit()
            
            df_ind['symbol_id'] = sym_id
            df_ind['category'] = item['category']
            all_indicators_dfs.append(df_ind)
            
        # 6. Relative Ranks (T4)
        if all_indicators_dfs:
            logger.info("Calculating relative ranks (T4)...")
            combined_df = pd.concat(all_indicators_dfs, ignore_index=True)
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
                for _, row in ranked_df.iterrows():
                    if pd.notna(row['percent_rank']):
                        t4_records.append(RelativeRank(
                            symbol_id=int(row['symbol_id']),
                            date=row['date'],
                            group_name=row['group_name'],
                            indicator_name=row['indicator_name'],
                            percent_rank=row['percent_rank']
                        ))
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
    run_step3_pipeline()
