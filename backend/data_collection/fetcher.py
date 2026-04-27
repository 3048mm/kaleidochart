import logging
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def _normalize_ticker(ticker: str) -> str:
    """
    yfinanceでエラーになりやすい記号を正規化します。
    例: BRK/B -> BRK-B, BF.B -> BF-B
    """
    if not ticker:
        return ticker
    # スラッシュやドットをハイフンに変換
    normalized = ticker.replace('/', '-').replace('.', '-')
    if normalized != ticker:
        logger.debug(f"Ticker normalized: {ticker} -> {normalized}")
    return normalized

def fetch_daily_data(ticker: str, start_date: str, end_date: str = None, progress: str = "") -> pd.DataFrame:
    """
    yfinanceを用いて指定銘銘柄の株価データを取得します。
    例外ハンドリング(Volume欠損値)やffillによる欠損日補完を行います。
    """
    # Normalize ticker symbol for yfinance (e.g., BRK/B -> BRK-B)
    yf_ticker = _normalize_ticker(ticker)
    
    prog_prefix = f"{progress} " if progress else ""
    logger.info(f"{prog_prefix}[{ticker}] Fetching data (as {yf_ticker}) from {start_date} to {end_date or 'today'}...")
    try:
        if end_date:
            df = yf.download(yf_ticker, start=start_date, end=end_date, progress=False)
        else:
            df = yf.download(yf_ticker, start=start_date, progress=False)
            
        if df.empty:
            logger.warning(f"[{ticker}] No data returned from yfinance.")
            return pd.DataFrame()

        # If it returns multi-index columns, sometimes happens with yf.download(ticker list length=1)
        if isinstance(df.columns, pd.MultiIndex):
            # df.columns = df.columns.droplevel(1) # drops ticker name
            df = df.xs(yf_ticker, axis=1, level=1, drop_level=True) if len(df.columns.levels[1]) > 0 and yf_ticker in df.columns.levels[1] else df
            
            # Additional cleanup in case it's still weird
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        # Forward fill missing data points (weekends/holidays that somehow ended up as NaNs)
        df = df.ffill()
        
        # Extremely IMPORTANT: Handle Volume for indicators like ^VIX which have missing Volume
        if 'Volume' not in df.columns:
            logger.debug(f"[{ticker}] Volume column missing, injecting 0.")
            df['Volume'] = 0
            
        # Fill any remaining NaNs in volume with 0
        df['Volume'] = df['Volume'].fillna(0)
        
        # Calculate correct column map
        df = df.reset_index()
        # Ensure column names match what we expect
        rename_map = {
            'Date': 'date',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }
        
        # Sometimes yfinance returns "Datetime"
        if 'Datetime' in df.columns:
            rename_map['Datetime'] = 'date'
            
        df = df.rename(columns=rename_map)
        
        # Convert date to date object
        if 'date' in df.columns and pd.api.types.is_datetime64_any_dtype(df['date']):
            df['date'] = df['date'].dt.date
            
        # Ensure we return valid types before DB insert
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        
        if 'volume' in df.columns:
            df['volume'] = df['volume'].astype('int64')

        # Drop any unexpected columns (Adj Close etc)
        expected_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        cols_to_keep = [c for c in expected_cols if c in df.columns]
        df = df[cols_to_keep]
        
        # --- 安全装置: 市場が完全に閉まる前（EST 18:00）は当日付の取得を無効化（Drop）する ---
        if not df.empty:
            import pytz
            from datetime import datetime as dt_now
            
            # US/Eastern は自動的にEST/EDT（サマータイム等）を吸収してくれます
            now_est = dt_now.now(pytz.timezone('US/Eastern'))
            if now_est.hour < 18:
                df = df[df['date'] < now_est.date()]
        # -----------------------------------------------------------------------------------
        
        logger.info(f"[{ticker}] Retrieved {len(df)} records.")
        return df
        
    except Exception as e:
        logger.error(f"[{ticker}] Failed to fetch data: {str(e)}")
        return pd.DataFrame()


def fetch_multiple_daily_data(
    tickers: list[str],
    start_date: str,
    sleep_seconds: float = 1.0,
    start_date_overrides: dict[str, str] | None = None
) -> dict[str, pd.DataFrame]:
    """
    複数銘柄のデータを順次取得します。APIレート制限回避のためSleepを挟みます。

    Args:
        tickers: 取得対象のティッカーリスト
        start_date: デフォルトの取得開始日 (YYYY-MM-DD)
        sleep_seconds: 銘柄間のスリープ秒数
        start_date_overrides: {ticker: "YYYY-MM-DD"} の形式で銘柄ごとの開始日を上書きできる
    """
    results = {}
    overrides = start_date_overrides or {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        ticker_start = overrides.get(ticker, start_date)
        progress_str = f"[{i+1}/{total}]"
        df = fetch_daily_data(ticker, ticker_start, progress=progress_str)
        if not df.empty:
            results[ticker] = df
            
        if i < len(tickers) - 1 and sleep_seconds > 0:
            logger.debug(f"Sleeping for {sleep_seconds}s before next ticker...")
            time.sleep(sleep_seconds)
    return results

def fetch_fundamentals(ticker: str) -> dict:
    """
    yfinanceから最新のinfo, 発行済株式数履歴(shares), 四半期損益計算書(income_stmt)を取得します。
    """
    yf_ticker = _normalize_ticker(ticker)
    logger.info(f"[{ticker}] Fetching fundamentals (as {yf_ticker}) (shares, income_stmt)...")
    res = {"shares": None, "income_stmt": None, "info": None}
    try:
        t = yf.Ticker(yf_ticker)
        
        try:
            res["info"] = dict(t.info) if t.info else {}
        except Exception:
            pass
            
        try:
            shares = t.get_shares_full(start="2000-01-01", end=None)
            if shares is not None and not shares.empty:
                res["shares"] = pd.DataFrame(shares)
        except Exception as e:
            logger.debug(f"[{ticker}] Failed to fetch historical shares: {e}")
            
        try:
            stmt = t.quarterly_income_stmt
            if stmt is not None and not stmt.empty:
                res["income_stmt"] = stmt
        except Exception as e:
            logger.debug(f"[{ticker}] Failed to fetch income statement: {e}")
            
    except Exception as e:
        logger.error(f"[{ticker}] Failed to fetch fundamentals: {e}")
        
    return res
