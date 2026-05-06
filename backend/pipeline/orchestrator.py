import logging
import pandas as pd
from typing import Optional, List
from sqlalchemy import func

from db.database import init_db, get_db
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent
from data_collection.spreadsheet_sync import fetch_symbols_from_sheet

from .phases.t2_prices import sync_phase_t2_prices
from .phases.t3_indicators import sync_phase_t3_indicators
from .phases.t4_ranks import sync_phase_t4_ranks
from .phases.t5_signals import sync_phase_t5_signals

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
        if t_item.get('theme_type') == 'virtual':
            target_tag = t_item['name'].strip() or t_item['ticker'].strip('_')
        else:
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
    df = pd.DataFrame([{'date': p.date, 'symbol_id': p.symbol_id, 'close': p.close} for p in all_prices if p.close is not None and p.close > 0])
    if df.empty: return pd.DataFrame()
    df = df.sort_values('date')
    df['ret'] = df.groupby('symbol_id')['close'].pct_change()
    daily_avg_ret = df.dropna(subset=['ret']).groupby('date')['ret'].mean().reset_index().sort_values('date')
    if daily_avg_ret.empty: return pd.DataFrame()
    current_val, synth_closes = 1000.0, []
    for _, row in daily_avg_ret.iterrows():
        current_val *= (1 + row['ret'])
        synth_closes.append(current_val)
    daily_avg_ret['close'] = synth_closes
    daily_avg_ret['open'] = daily_avg_ret['high'] = daily_avg_ret['low'] = daily_avg_ret['close']
    daily_avg_ret['volume'], daily_avg_ret['symbol_id'] = 0, virtual_id
    return daily_avg_ret

def run_pipeline(config, db_path, logger: logging.Logger, rebuild_from: Optional[str] = None, categories: Optional[List[str]] = None, skip_fetch: bool = False, skip_sync: bool = False, skip_t3: bool = False, recalculate_all: bool = False):
    """
    Main Orchestrator for Step 3 Pipeline.
    """
    logger.info(f"Starting Step 3 Pipeline Orchestrator - rebuild_from={rebuild_from}, categories={categories}, re-calculate={recalculate_all}")
    init_db(db_path)
    
    lvl_map = {'T2': 2, 'T3': 3, 'T4': 4, 'T5': 5}
    active_lvl = lvl_map.get(str(rebuild_from).upper(), 0)
    if recalculate_all: active_lvl = 2

    try:
        with get_db() as db:
            if not skip_sync:
                extra_symbols = config.get("data_collection", {}).get("symbols", [])
                sheet_data, symbol_id_map = sync_symbols_to_db(
                    db, 
                    config["system"].get("credentials_path", "credentials.json"), 
                    config["system"].get("spreadsheet_url", "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0"),
                    extra_symbols=extra_symbols
                )
            else:
                symbols = db.query(Symbol).filter(Symbol.active == True).all()
                sheet_data = [{'ticker': s.ticker, 'exchange': s.exchange, 'category': s.category, 'theme_type': s.theme_type} for s in symbols]
                symbol_id_map = {(s.ticker, s.exchange): s.id for s in symbols}
            
            if categories:
                sheet_data = [s for s in sheet_data if s['category'] in categories or s['ticker'] == 'SPY']
            
            target_ids = [symbol_id_map.get((s['ticker'], s['exchange'])) for s in sheet_data]

            if active_lvl > 0:
                logger.info(f"Cascaded Rebuild Triggered: Clearing downstream from level {active_lvl}")
                if active_lvl <= 5: db.query(MarketSignal).delete()
                if active_lvl <= 4:
                    if categories: db.query(RelativeRank).filter(RelativeRank.symbol_id.in_(target_ids)).delete()
                    else: db.query(RelativeRank).delete()
                if active_lvl <= 3:
                    if categories: db.query(Indicator).filter(Indicator.symbol_id.in_(target_ids)).delete()
                    else: db.query(Indicator).delete()
                if active_lvl <= 2:
                    if categories:
                        spy_id = db.query(Symbol.id).filter(Symbol.ticker == 'SPY').scalar()
                        q = db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(target_ids))
                        if spy_id: q = q.filter(DailyPrice.symbol_id != spy_id)
                        q.delete(synchronize_session=False)
                db.commit()

            spy_latest_date = sync_phase_t2_prices(db, sheet_data, symbol_id_map, config["data_collection"]["initial_fetch_days"], skip_fetch, logger)
            
            virtual_items = [d for d in sheet_data if d['theme_type'] == 'virtual']
            for v_item in virtual_items:
                v_id = symbol_id_map.get((v_item['ticker'], v_item['exchange']))
                synth_df = build_virtual_index_prices(db, v_id)
                if not synth_df.empty:
                    db.query(DailyPrice).filter(DailyPrice.symbol_id == v_id).delete()
                    db.query(Indicator).filter(Indicator.symbol_id == v_id).delete()
                    db.bulk_save_objects([DailyPrice(symbol_id=v_id, date=row['date'], open=row['open'], high=row['high'], low=row['low'], close=row['close'], volume=0) for _, row in synth_df.iterrows()])
                    db.commit()

            if not skip_t3:
                sync_phase_t3_indicators(db, sheet_data, symbol_id_map, spy_latest_date, skip_fetch, db_path, logger)
            
            sync_phase_t4_ranks(db, spy_latest_date, logger)
            sync_phase_t5_signals(db, logger)

            logger.info("--- Step 3 Pipeline COMPLETED SUCCESSFULLY ---")
    except Exception as e:
        logger.error(f"--- Step 3 Pipeline FAILURE --- Type: {type(e).__name__}, Message: {str(e)}")
        raise e
