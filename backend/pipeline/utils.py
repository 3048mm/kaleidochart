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
