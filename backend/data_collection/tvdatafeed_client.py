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
from tvDatafeed import TvDatafeed, Interval

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
    """
    if tv_client is None:
        tv_client = TvDatafeed()

    n_bars = _calc_n_bars(start_date)

    try:
        hist_df = tv_client.get_hist(
            symbol=ticker, exchange=exchange, interval=Interval.in_daily, n_bars=n_bars
        )
    except Exception as e:
        logger.warning(f"[{ticker}] tvDatafeed取得に失敗しました: {e}")
        return pd.DataFrame()

    if hist_df is None or hist_df.empty:
        logger.warning(f"[{ticker}] tvDatafeedからデータが返りませんでした。")
        return pd.DataFrame()

    df = hist_df.reset_index()
    df = df.rename(columns={"datetime": "date"})
    df["date"] = df["date"].apply(lambda dt: dt.date() if isinstance(dt, datetime) else dt)

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype("int64")

    df = df[["date", "open", "high", "low", "close", "volume"]]

    start_date_val = pd.to_datetime(start_date).date()
    if end_date is None:
        df = df[df["date"] >= start_date_val]
    else:
        end_date_val = pd.to_datetime(end_date).date()
        df = df[(df["date"] >= start_date_val) & (df["date"] <= end_date_val)]

    df = df.sort_values("date").reset_index(drop=True)
    return df
