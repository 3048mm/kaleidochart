"""3アダプタ(fixed_data_loader/tvdatafeed_client/historyofmarket_client)間で
重複していた「列順の整形」「日付範囲フィルタ」を切り出した内部ユーティリティ。

先頭アンダースコアは「内部ユーティリティ、外部から直接呼ばない」の意図。
"""

import pandas as pd

_EXPECTED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """date/open/high/low/close/volume の順で列を揃える。"""
    return df[_EXPECTED_COLUMNS]


def filter_date_range(df: pd.DataFrame, start_date: str, end_date: str | None) -> pd.DataFrame:
    """date列でstart_date~end_date(Noneなら上限なし)にフィルタする。"""
    start_date_val = pd.to_datetime(start_date).date()
    if end_date is None:
        return df[df["date"] >= start_date_val]
    end_date_val = pd.to_datetime(end_date).date()
    return df[(df["date"] >= start_date_val) & (df["date"] <= end_date_val)]
