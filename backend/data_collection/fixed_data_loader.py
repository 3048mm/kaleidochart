"""静的CSV(Investing.comからの手動エクスポート)を読むローダー。

`data/fixed_data/<ticker>.csv` を読み、`fetch_daily_data` と同じ列構成
(date/open/high/low/close/volume) の DataFrame に変換する。
CSVはUTF-8 BOM付き・日本語ヘッダー・日付降順(新しい日付が先頭)。
"""

import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)

# 日本語ヘッダー -> 内部列名のマッピング（変化率%は捨てる）
_COLUMN_MAP = {
    "日付": "date",
    "終値": "close",
    "始値": "open",
    "高値": "high",
    "安値": "low",
    "出来高": "volume",
}


def load_fixed_data(
    ticker: str,
    start_date: str,
    end_date: str | None = None,
    fixed_data_dir: str = "data/fixed_data",
) -> pd.DataFrame:
    """`<fixed_data_dir>/<ticker>.csv` を読み、指定範囲でフィルタして返す。

    ファイルが存在しない場合は空DataFrameを返す。
    """
    path = os.path.join(fixed_data_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        logger.debug(f"[{ticker}] fixed_data ファイルが存在しません: {path}")
        return pd.DataFrame()

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df = df.rename(columns=_COLUMN_MAP)

    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    df["volume"] = df["volume"].fillna("").replace("", "0")
    df["volume"] = df["volume"].astype(str).str.replace(",", "", regex=False)
    df["volume"] = df["volume"].astype("int64")

    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("date").reset_index(drop=True)

    if end_date is None:
        end_date_val = df["date"].max()
    else:
        end_date_val = pd.to_datetime(end_date).date()
    start_date_val = pd.to_datetime(start_date).date()

    df = df[(df["date"] >= start_date_val) & (df["date"] <= end_date_val)]
    df = df.reset_index(drop=True)
    return df
