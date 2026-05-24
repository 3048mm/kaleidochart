import logging
import pandas as pd
from data_collection.fetcher import fetch_fundamentals

def sanitize_numeric(row, key):
    """Safely extract and sanitize a numeric value from a row."""
    v = row.get(key)
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
    except Exception:
        pass
    return v

def attach_market_cap(ticker: str, df: pd.DataFrame, logger: logging.Logger) -> pd.DataFrame:
    """
    Fetch fundamentals and attach market_cap column to the price DataFrame.
    """
    df = df.copy()
    df['market_cap'] = None
    try:
        fund_res = fetch_fundamentals(ticker)
        shares_df, info = fund_res.get("shares"), fund_res.get("info")
        
        if shares_df is not None and not shares_df.empty:
            try:
                s_series = shares_df.iloc[:, 0]
                s_series.index = pd.to_datetime(s_series.index).tz_localize(None).normalize()
                s_df = s_series.reset_index()
                s_df.columns = ['date', 'shares']
                
                df_dt = df.copy()
                df_dt['date_dt'] = pd.to_datetime(df['date'])
                
                merged = pd.merge_asof(
                    df_dt.sort_values('date_dt'), 
                    s_df.sort_values('date'), 
                    left_on='date_dt', 
                    right_on='date', 
                    direction='backward'
                )
                df['market_cap'] = df['close'].values * merged['shares'].values
            except Exception as e:
                logger.debug(f"[{ticker}] Failed to calc market_cap from shares: {e}")
        
        if info and 'marketCap' in info:
            df['market_cap'] = df['market_cap'].fillna(info['marketCap'])
    except Exception as e:
        logger.warning(f"[{ticker}] Failed to attach market_cap: {e}")
        
    return df

from datetime import datetime, date
from db.models import PipelineMeta

def get_pipeline_meta(db) -> tuple[datetime | None, date | None]:
    """
    Retrieves the pipeline execution metadata (last_completed_at, last_spy_date).
    Returns (None, None) if no metadata exists.
    """
    meta = db.query(PipelineMeta).filter(PipelineMeta.id == 1).first()
    if meta:
        return meta.last_completed_at, meta.last_spy_date
    return None, None

def update_pipeline_meta(db, start_time: datetime, spy_latest_date: date):
    """
    Upserts the pipeline execution metadata.
    Stores the data fetch start_time and the latest SPY trading date.
    """
    meta = db.query(PipelineMeta).filter(PipelineMeta.id == 1).first()
    if meta:
        meta.last_completed_at = start_time
        meta.last_spy_date = spy_latest_date
    else:
        meta = PipelineMeta(id=1, last_completed_at=start_time, last_spy_date=spy_latest_date)
        db.add(meta)
    db.commit()

def get_expired_earnings_date_tickers(db, target_tickers: list[str], today: date) -> list[str]:
    """
    Filters target_tickers and returns only those that are active and whose
    next_earnings_date is NULL or prior to today (expired).
    """
    from sqlalchemy import or_
    from db.models import Symbol
    
    if not target_tickers:
        return []
        
    expired_symbols = db.query(Symbol.ticker).filter(
        Symbol.active == 1,
        Symbol.ticker.in_(target_tickers),
        or_(
            Symbol.next_earnings_date.is_(None),
            Symbol.next_earnings_date < today
        )
    ).all()
    
    return [r[0] for r in expired_symbols]

def update_earnings_dates_sync(db, tickers: list[str], sleep_seconds: float = 0.0):
    """
    Synchronously fetches next earnings dates for tickers and updates the symbols table.
    """
    import time
    from db.models import Symbol
    from data_collection.fetcher import fetch_next_earnings_date
    
    if not tickers:
        return
        
    for i, ticker in enumerate(tickers):
        next_date = fetch_next_earnings_date(ticker)
        if next_date:
            symbol = db.query(Symbol).filter(Symbol.ticker == ticker, Symbol.active == 1).first()
            if symbol:
                symbol.next_earnings_date = next_date
                db.flush()
                
        if i < len(tickers) - 1 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
            
    db.commit()


