"""tvDatafeed(TradingView非公式データ取得ライブラリ)アダプタ。

S5FI/S5THの継続取得(主経路)用。匿名アクセスで`exchange='INDEX'`を
指定すると取得できることが実運用検証済み(2006-12-29〜取得確認済み)。
他のexchange prefix(TVC/SP/AMEX/CBOE)は接続が切られるため使わない。

`TvDatafeed`/`Interval`はこのモジュールの名前空間に直接importしている
(テストが`monkeypatch.setattr(mod, "TvDatafeed", ...)`で差し替える前提)。
"""

import logging
from datetime import date, datetime

import pandas as pd

from data_collection._series_utils import filter_date_range, normalize_columns

try:
    from tvDatafeed import TvDatafeed, Interval
except ImportError:
    # tvdatafeed-enhanced未インストールの環境(別マシン・古いvenv・CI等)でも
    # fetcher.py のimport自体が失敗して全銘柄のyfinance取得が止まらないようにする。
    TvDatafeed = None
    Interval = None

logger = logging.getLogger(__name__)


def _calc_n_bars(start_date: str) -> int:
    """start_dateから今日までのカレンダー日数を、営業日換算+マージンして返す。"""
    start = pd.to_datetime(start_date).date()
    today = date.today()
    calendar_days = max((today - start).days, 1)
    business_day_estimate = calendar_days * 5 / 7
    return int(business_day_estimate * 1.15) + 20


def fetch_from_tvdatafeed(
    ticker: str,
    start_date: str,
    end_date: str | None = None,
    exchange: str = "INDEX",
    tv_client=None,
) -> pd.DataFrame:
    """tvDatafeed経由でティッカーの日次データを取得し、指定範囲でフィルタして返す。

    例外・None・空データはいずれも空DataFrameで返す(呼び出し元に伝播させない)。
    取得後の変換・フィルタ処理も含め、関数の主要処理全体を1つのtry/exceptで
    囲む(volumeがNaN等の場合の`.astype("int64")`失敗等が呼び出し元に伝播し、
    t2_prices.pyのループで全銘柄の更新が止まるのを防ぐため)。
    """
    if tv_client is None and TvDatafeed is None:
        logger.error(f"[{ticker}] tvDatafeed(tvdatafeed-enhanced)が未インストールのため取得できません。")
        return pd.DataFrame()

    try:
        if tv_client is None:
            tv_client = TvDatafeed()

        n_bars = _calc_n_bars(start_date)

        hist_df = tv_client.get_hist(
            symbol=ticker, exchange=exchange, interval=Interval.in_daily, n_bars=n_bars
        )

        if hist_df is None or hist_df.empty:
            logger.warning(f"[{ticker}] tvDatafeedからデータが返りませんでした。")
            return pd.DataFrame()

        df = hist_df.reset_index()
        df = df.rename(columns={"datetime": "date"})
        df["date"] = df["date"].apply(lambda dt: dt.date() if isinstance(dt, datetime) else dt)

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype("int64")

        df = normalize_columns(df)
        df = filter_date_range(df, start_date, end_date)

        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"[{ticker}] tvDatafeed取得に失敗しました: {e}")
        return pd.DataFrame()
