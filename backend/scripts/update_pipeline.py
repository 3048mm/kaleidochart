import os
import sys
import time
import logging
import tomllib
import pandas as pd
import argparse
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, date
from typing import List, Optional
from sqlalchemy import func, text

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

# Configure comprehensive logging
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
    file_h = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_h.setFormatter(log_format)
    root_logger.addHandler(file_h)
    return logging.getLogger(__name__)

logger = setup_pipeline_logging()

def load_config():
    config_path = os.path.join(project_root, "config.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)

def _to_val(row, key):
    v = row.get(key)
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
    except Exception:
        pass
    return v

# --- Phase Functions (Modular) ---

def sync_phase_t2_prices(db, sheet_data, symbol_id_map, initial_fetch_days, skip_fetch=False):
    """Phase 2: Prices (T2) - Sync prices from yfinance led by SPY."""
    logger.info("--- Phase 2: Price data sync START ---")
    spy_item = next((d for d in sheet_data if d['ticker'] == 'SPY'), None)
    if not spy_item:
        logger.error("SPY not found in sheet_data.")
        return None
    spy_sym_id = symbol_id_map.get(("SPY", spy_item['exchange']))
    spy_max_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
    default_start = (datetime.now() - timedelta(days=initial_fetch_days)).strftime('%Y-%m-%d')
    spy_fetch_start = default_start
    if spy_max_date:
        spy_fetch_start = (datetime.combine(spy_max_date, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
    if not skip_fetch:
        logger.info(f"Updating SPY from {spy_fetch_start}...")
        spy_df = fetch_daily_data("SPY", spy_fetch_start)
        if spy_df is not None and not spy_df.empty:
            existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == spy_sym_id).all()}
            new_recs = [DailyPrice(symbol_id=spy_sym_id, date=row['date'], open=_to_val(row, 'open'), high=_to_val(row, 'high'), low=_to_val(row, 'low'), close=_to_val(row, 'close'), volume=int(row['volume']) if _to_val(row, 'volume') is not None else 0) for _, row in spy_df.iterrows() if row['date'] not in existing_dates]
            if new_recs:
                db.bulk_save_objects(new_recs)
                db.commit()
                logger.info(f"SPY updated: +{len(new_recs)} rows.")
            else:
                logger.info("SPY: No new rows to add.")
    spy_latest_date = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == spy_sym_id).scalar()
    logger.info(f"Target Reference Date (SPY): {spy_latest_date}")
    if not spy_latest_date: return None
    real_items = [d for d in sheet_data if d['theme_type'] != 'virtual' and d['ticker'] != 'SPY']
    latest_rows = db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()
    sym_latest_map = {sid: ldt for sid, ldt in latest_rows}
    update_needed = [(it['ticker'], symbol_id_map.get((it['ticker'], it['exchange'])), sym_latest_map.get(symbol_id_map.get((it['ticker'], it['exchange'])))) for it in real_items if not sym_latest_map.get(symbol_id_map.get((it['ticker'], it['exchange']))) or sym_latest_map.get(symbol_id_map.get((it['ticker'], it['exchange']))) < spy_latest_date]
    logger.info(f"Found {len(update_needed)} tickers needing price update.")
    if not skip_fetch and update_needed:
        for ticker, sid, current_max in update_needed:
            f_start = default_start
            if current_max: f_start = (datetime.combine(current_max, datetime.min.time()) + timedelta(days=1)).strftime('%Y-%m-%d')
            df = fetch_daily_data(ticker, f_start)
            if df is not None and not df.empty:
                existing_dates = {r[0] for r in db.query(DailyPrice.date).filter(DailyPrice.symbol_id == sid).all()}
                new_recs = [DailyPrice(symbol_id=sid, date=row['date'], open=_to_val(row, 'open'), high=_to_val(row, 'high'), low=_to_val(row, 'low'), close=_to_val(row, 'close'), volume=int(row['volume']) if _to_val(row, 'volume') is not None else 0) for _, row in df.iterrows() if row['date'] not in existing_dates]
                if new_recs:
                    db.bulk_save_objects(new_recs)
                    db.commit()
                    logger.debug(f"[{ticker}] Updated +{len(new_recs)} rows.")
    return spy_latest_date

def sync_phase_t3_indicators(db, sheet_data, symbol_id_map, spy_latest_date, skip_fetch=False):
    """Phase 3: Indicators (T3) - Per-ticker catch-up using T2 price data."""
    logger.info("--- Phase 3: Indicator calculation START ---")
    if not spy_latest_date: return
    spy_sym_id = symbol_id_map.get(("SPY", "NYSE" if ("SPY", "NYSE") in symbol_id_map else "AMEX")) or db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    spy_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()
    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in spy_all])
    p_max_map = {sid: mdt for sid, mdt in db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()}
    i_max_map = {sid: mdt for sid, mdt in db.query(Indicator.symbol_id, func.max(Indicator.date)).group_by(Indicator.symbol_id).all()}
    update_count = 0
    for item in sheet_data:
        ticker, sid = item['ticker'], symbol_id_map.get((item['ticker'], item['exchange']))
        if not sid: continue
        t2_max, t3_max = p_max_map.get(sid), i_max_map.get(sid)
        if t2_max and (not t3_max or t3_max < t2_max):
            logger.info(f"[{ticker}] Calculating T3 indicators (Gap: {t3_max} -> {t2_max}).")
            df_price = pd.DataFrame([{'date': p.date, 'open': p.open, 'high': p.high, 'low': p.low, 'close': p.close, 'volume': p.volume} for p in db.query(DailyPrice).filter(DailyPrice.symbol_id == sid).order_by(DailyPrice.date).all()])
            df_ind = calculate_indicators(df_price, spy_df if ticker != "SPY" else None)
            fund_res = {} if skip_fetch else fetch_fundamentals(ticker)
            shares_df, info = fund_res.get("shares"), fund_res.get("info")
            df_ind['market_cap'] = None
            if shares_df is not None and not shares_df.empty:
                try:
                    s_series = shares_df.iloc[:, 0]
                    s_series.index = pd.to_datetime(s_series.index).tz_localize(None).normalize()
                    s_df = s_series.reset_index(); s_df.columns = ['date', 'shares']
                    merged = pd.merge_asof(pd.DataFrame({'date': pd.to_datetime(df_price['date'])}), s_df.sort_values('date'), on='date', direction='backward')
                    df_ind['market_cap'] = df_price['close'].values * merged['shares'].values
                except Exception as e: logger.error(f"[{ticker}] Market cap calc error: {e}")
            if info and 'marketCap' in info: df_ind['market_cap'] = df_ind['market_cap'].fillna(info['marketCap'])
            existing_i_dates = {r[0] for r in db.query(Indicator.date).filter(Indicator.symbol_id == sid).all()}
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1))) & (~df_ind['date'].isin(existing_i_dates))]
            
            if not delta_df.empty:
                t3_recs = [Indicator(symbol_id=sid, date=row['date'], sma_5=_to_val(row, 'sma_5'), sma_21=_to_val(row, 'sma_21'), sma_50=_to_val(row, 'sma_50'), sma_63=_to_val(row, 'sma_63'), sma_150=_to_val(row, 'sma_150'), sma_200=_to_val(row, 'sma_200'), ema_5=_to_val(row, 'ema_5'), ema_21=_to_val(row, 'ema_21'), ema_50=_to_val(row, 'ema_50'), ema_63=_to_val(row, 'ema_63'), ema_150=_to_val(row, 'ema_150'), ema_200=_to_val(row, 'ema_200'), td9=int(row['td9']) if _to_val(row, 'td9') is not None else 0, atr_14=_to_val(row, 'atr_14'), atr_pct_14=_to_val(row, 'atr_pct_14'), adr_pct_21=_to_val(row, 'adr_pct_21'), dist_sma50_atr=_to_val(row, 'dist_sma50_atr'), market_cap=_to_val(row, 'market_cap'), relative_strength_spy=_to_val(row, 'relative_strength_spy'), rs_condition_14=_to_val(row, 'rs_condition_14'), rs_condition_21=_to_val(row, 'rs_condition_21'), rs_condition_63=_to_val(row, 'rs_condition_63'), rs_momentum_14=_to_val(row, 'rs_momentum_14'), rs_momentum_21=_to_val(row, 'rs_momentum_21'), rs_momentum_63=_to_val(row, 'rs_momentum_63'), rs_ratio_14=_to_val(row, 'rs_ratio_14'), rs_ratio_21=_to_val(row, 'rs_ratio_21'), rs_ratio_63=_to_val(row, 'rs_ratio_63'), vol_surge_21=_to_val(row, 'vol_surge_21'), rel_vol_vs_spy_21=_to_val(row, 'rel_vol_vs_spy_21'), pct_from_63d_high=_to_val(row, 'pct_from_63d_high'), pct_from_52w_high=_to_val(row, 'pct_from_52w_high'), trend_template_ok=int(row['trend_template_ok']) if _to_val(row, 'trend_template_ok') is not None else None) for _, row in delta_df.iterrows()]
                db.bulk_save_objects(t3_recs)
                db.commit()
                update_count += 1
    logger.info(f"Phase 3 COMPLETE: Updated {update_count} tickers.")

def sync_phase_t4_ranks(db, spy_latest_date):
    """Phase 4: Relative Ranks (T4) - Idempotent catch-up."""
    logger.info("--- Phase 4: Relative Rank calculation START ---")
    t3_max, t4_max = db.query(func.max(Indicator.date)).scalar(), db.query(func.max(RelativeRank.date)).scalar()
    if not t3_max or (t4_max and t4_max >= t3_max): return
    gap_dates = [r[0] for r in db.query(Indicator.date).distinct().filter(Indicator.date > (t4_max if t4_max else date(2000,1,1))).order_by(Indicator.date).all()]
    if not gap_dates: return
    total_dates = len(gap_dates)
    logger.info(f"Phase 4: Processing relative ranks for {total_dates} dates.")
    indicators_to_rank = ['relative_strength_spy', 'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63', 'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63', 'rs_condition_14', 'rs_condition_21', 'rs_condition_63']
    for i, d in enumerate(gap_dates):
        if i % 10 == 0 or i == total_dates - 1:
            logger.info(f"Phase 4 Progress: {i+1}/{total_dates} (Date: {d})")
        df_date = pd.read_sql(db.query(Indicator.symbol_id, Indicator.date, Symbol.category, *[getattr(Indicator, col) for col in indicators_to_rank]).join(Symbol, Symbol.id == Indicator.symbol_id).filter(Indicator.date == d).statement, db.bind)
        if df_date.empty: continue
        t4_recs = []
        for ind_col in indicators_to_rank:
            if ind_col not in df_date.columns: continue
            df_ranked = calculate_relative_ranks(df_date, group_col='category', indicator_col=ind_col)
            t4_recs.extend([RelativeRank(symbol_id=int(r['symbol_id']), date=r['date'], group_name=r['group_name'], indicator_name=r['indicator_name'], percent_rank=float(r['percent_rank'])) for r in df_ranked[df_ranked['percent_rank'].notna()].to_dict('records')])
        db.query(RelativeRank).filter(RelativeRank.date == d).delete()
        db.bulk_save_objects(t4_recs)
        db.commit()
    logger.info("Phase 4 COMPLETE.")

def sync_phase_t5_signals(db):
    """Phase 5: Market Signals (T5) - Idempotent catch-up."""
    logger.info("--- Phase 5: Market Signal calculation START ---")
    spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    vix_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "^VIX").scalar()
    
    # 1. Determine Gap Dates to process (New dates OR dates with missing score)
    # Strategy: Find all dates where we have SPY metrics, but MarketSignal is either missing or has NULL score
    spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    
    # Dates having SPY indicators (potential signal days)
    t3_dates = {r[0] for r in db.query(Indicator.date).distinct().filter(Indicator.symbol_id == spy_sym_id).all()}
    
    # Dates already having a non-null score
    t5_completed_dates = {r[0] for r in db.query(MarketSignal.date).filter(MarketSignal.market_trend_score.is_not(None)).all()}
    
    # Gap is the set difference
    gap_dates = sorted(list(t3_dates - t5_completed_dates))
    
    if not gap_dates:
        logger.info("No gaps or missing scores detected in Phase 5.")
        return

    # 2. Fetch SPY full history (needed for Distribution Days etc.)
    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()])
    if spy_df.empty:
        logger.error("SPY price data missing.")
        return
    spy_df['date'] = pd.to_datetime(spy_df['date'])

    # 3. Fetch VIX history
    vix_df = pd.DataFrame()
    if vix_sym_id:
        vix_df = pd.DataFrame([{"date": r.date, "close": r.close} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == vix_sym_id).order_by(DailyPrice.date).all()])
        if not vix_df.empty:
            vix_df['date'] = pd.to_datetime(vix_df['date'])
    
    # 4. Calculate Breadth & Momentum for each gap date
    metrics_list = []
    active_stock_ids = [r[0] for r in db.query(Symbol.id).filter(Symbol.active == 1, Symbol.category == '個別').all()]
    
    if not active_stock_ids:
        logger.warning("No active '個別' stocks found for metrics calculation.")
    else:
        total_dates = len(gap_dates)
        logger.info(f"Phase 5: Calculating breadth and momentum for {total_dates} dates.")
        for i, d in enumerate(gap_dates):
            if i % 10 == 0 or i == total_dates - 1:
                logger.info(f"Phase 5 Progress: {i+1}/{total_dates} (Date: {d})")
            # Breadth (% above SMA50)
            # We want: (Count of stocks where close > sma_50) / (Count of stocks)
            # Joining indicators and daily_prices for the same day
            # Revised approach: Use a join on active symbols
            # Breadth
            breadth_val = db.execute(text(f"""
                SELECT CAST(SUM(CASE WHEN dp.close > i.sma_50 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)
                FROM indicators i
                JOIN daily_prices dp ON i.symbol_id = dp.symbol_id AND i.date = dp.date
                JOIN symbols s ON i.symbol_id = s.id
                WHERE i.date = '{d}' AND s.active = 1 AND s.category = '個別'
            """)).scalar() or 0.5
            
            # Momentum (% daily change > 0)
            # We need daily_prices for today (dp) and previous trading day (dp_prev)
            momentum_val = db.execute(text(f"""
                WITH current_prices AS (
                    SELECT symbol_id, close FROM daily_prices WHERE date = '{d}'
                ),
                prev_prices AS (
                    SELECT dp.symbol_id, dp.close
                    FROM daily_prices dp
                    JOIN (
                        SELECT symbol_id, MAX(date) as max_date FROM daily_prices WHERE date < '{d}' GROUP BY symbol_id
                    ) dp_last ON dp.symbol_id = dp_last.symbol_id AND dp.date = dp_last.max_date
                )
                SELECT CAST(SUM(CASE WHEN cp.close > pp.close THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*)
                FROM current_prices cp
                JOIN prev_prices pp ON cp.symbol_id = pp.symbol_id
                JOIN symbols s ON cp.symbol_id = s.id
                WHERE s.active = 1 AND s.category = '個別'
            """)).scalar() or 0.5
            
            metrics_list.append({
                'date': pd.to_datetime(d),
                'breadth_sma50': breadth_val,
                'momentum_ratio': momentum_val
            })
            
    metrics_df = pd.DataFrame(metrics_list) if metrics_list else pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])

    # 5. Execute core calculator
    ms_df = calculate_market_signals(spy_df, vix_df, metrics_df)
    
    # Filter only the gap dates we intended to process
    gap_dt = [pd.to_datetime(d) for d in gap_dates]
    ms_df = ms_df[ms_df['date'].isin(gap_dt)]
    
    if ms_df.empty:
        logger.info("No signals found for the target gap dates.")
        return

    # 6. Upsert records
    t5_recs = []
    for _, row in ms_df.iterrows():
        t5_recs.append(MarketSignal(
            date=row['date'].date(),
            spy_above_sma200=int(row['spy_above_sma200']),
            spy_sma200_rising=int(row['spy_sma200_rising']) if _to_val(row, 'spy_sma200_rising') is not None else None,
            distribution_days=int(row['distribution_days']),
            follow_through_day=int(row['follow_through_day']),
            market_phase=row['market_phase'],
            market_trend_score=float(row['market_trend_score']) if _to_val(row, 'market_trend_score') is not None else None
        ))
    
    # Delete potentially partial entries for the dates we are saving
    db.query(MarketSignal).filter(MarketSignal.date.in_([r.date for r in t5_recs])).delete(synchronize_session=False)
    db.bulk_save_objects(t5_recs)
    db.commit()
    logger.info(f"Phase 5 COMPLETE: Saved {len(t5_recs)} signal records.")

# --- Utility Functions ---

def sync_symbols_to_db(db, credentials_path, spreadsheet_url):
    sheet_data = fetch_symbols_from_sheet(credentials_path, spreadsheet_url)
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

# --- Main Pipeline ---

def run_step3_pipeline(rebuild_from: Optional[str] = None, categories: Optional[List[str]] = None, skip_fetch: bool = False, skip_sync: bool = False, skip_t3: bool = False, recalculate_all: bool = False):
    """
    Main Data Pipeline (Step 3) - Modular and Robust.
    Supports cascaded rebuild via 'rebuild_from' or 'recalculate_all'.
    """
    logger.info(f"Starting Step 3 Pipeline - rebuild_from={rebuild_from}, categories={categories}, re-calculate={recalculate_all}")
    config = load_config()
    init_db(config["system"]["db_path"])
    
    # Map rebuild levels
    lvl_map = {'T2': 2, 'T3': 3, 'T4': 4, 'T5': 5}
    active_lvl = lvl_map.get(str(rebuild_from).upper(), 0)
    if recalculate_all: active_lvl = 2

    try:
        with get_db() as db:
            # 1. Phase 1: Symbol Sync (T1)
            if not skip_sync:
                sheet_data, symbol_id_map = sync_symbols_to_db(db, config["system"].get("credentials_path", "credentials.json"), config["system"].get("spreadsheet_url", "https://docs.google.com/spreadsheets/d/1pkwMVl6FaurinU2Z1Mm_fW_zepMe6YLXE0e-v-vcp1k/edit#gid=0"))
            else:
                symbols = db.query(Symbol).filter(Symbol.active == True).all()
                sheet_data = [{'ticker': s.ticker, 'exchange': s.exchange, 'category': s.category, 'theme_type': s.theme_type} for s in symbols]
                symbol_id_map = {(s.ticker, s.exchange): s.id for s in symbols}
            
            if categories:
                sheet_data = [s for s in sheet_data if s['category'] in categories or s['ticker'] == 'SPY']
            
            target_ids = [symbol_id_map.get((s['ticker'], s['exchange'])) for s in sheet_data]

            # 2. Cascade Deletion for Rebuild
            if active_lvl > 0:
                logger.info(f"Cascaded Rebuild Triggered: Clearing downstream from level {active_lvl}")
                if active_lvl <= 5: # Signals
                    db.query(MarketSignal).delete()
                if active_lvl <= 4: # Ranks
                    if categories: db.query(RelativeRank).filter(RelativeRank.symbol_id.in_(target_ids)).delete()
                    else: db.query(RelativeRank).delete()
                if active_lvl <= 3: # Indicators
                    if categories: db.query(Indicator).filter(Indicator.symbol_id.in_(target_ids)).delete()
                    else: db.query(Indicator).delete()
                if active_lvl <= 2: # Prices (Real tickers only, SPY is preserved unless explicit)
                    if categories: db.query(DailyPrice).filter(DailyPrice.symbol_id.in_(target_ids), Symbol.ticker != 'SPY').delete()
                    # We move on, Phase 2 will fill the gaps
                db.commit()

            # 3. Phase 2: Price Sync (T2)
            spy_latest_date = sync_phase_t2_prices(db, sheet_data, symbol_id_map, config["data_collection"]["initial_fetch_days"], skip_fetch=skip_fetch)
            
            # 4. Phase 2+: Virtual Themes
            virtual_items = [d for d in sheet_data if d['theme_type'] == 'virtual']
            for v_item in virtual_items:
                v_id = symbol_id_map.get((v_item['ticker'], v_item['exchange']))
                synth_df = build_virtual_index_prices(db, v_id)
                if not synth_df.empty:
                    db.query(DailyPrice).filter(DailyPrice.symbol_id == v_id).delete()
                    db.query(Indicator).filter(Indicator.symbol_id == v_id).delete()
                    db.bulk_save_objects([DailyPrice(symbol_id=v_id, date=row['date'], open=row['open'], high=row['high'], low=row['low'], close=row['close'], volume=0) for _, row in synth_df.iterrows()])
                    db.commit()

            # 5. Phase 3: Indicators (T3)
            if not skip_t3:
                sync_phase_t3_indicators(db, sheet_data, symbol_id_map, spy_latest_date, skip_fetch=skip_fetch)
            
            # 6. Phase 4: Ranks (T4)
            sync_phase_t4_ranks(db, spy_latest_date)
            
            # 7. Phase 5: Signals (T5)
            sync_phase_t5_signals(db)

            logger.info("--- Step 3 Pipeline COMPLETED SUCCESSFULLY ---")
    except Exception as e:
        logger.error(f"--- Step 3 Pipeline FAILURE --- Type: {type(e).__name__}, Message: {str(e)}")
        logger.error(traceback.format_exc()); raise e

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Step 3 Data Pipeline.")
    parser.add_argument("--rebuild-from", type=str, help="Rebuild from a specific table (T2, T3, T4, T5).")
    parser.add_argument("--re-calculate", action="store_true", help="Redownload all data and recalculate all indicators.")
    parser.add_argument("--category", type=str, help="Comma-separated categories to process.")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip yfinance price fetching.")
    parser.add_argument("--skip-sync", action="store_true", help="Skip Google Spreadsheet sync.")
    parser.add_argument("--skip-t3", action="store_true", help="Skip T3 indicator calculation.")
    args = parser.parse_args()
    selected_categories = [c.strip() for c in args.category.split(",")] if args.category else None
    run_step3_pipeline(rebuild_from=args.rebuild_from, categories=selected_categories, skip_fetch=args.skip_fetch, skip_sync=args.skip_sync, skip_t3=args.skip_t3, recalculate_all=args.re_calculate)
