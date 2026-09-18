"""データ取得元の振り分け・結合を一元管理するルーター。

fixed_data(静的CSV) → tvdatafeed → historyofmarket(フォールバック) →
yfinance、の優先順で範囲を充足し、結合結果を返す。

`load_fixed_data`/`fetch_from_tvdatafeed`/`fetch_from_historyofmarket`は
このモジュールの名前空間に直接importすること(呼び出し元テストが
`monkeypatch.setattr(router, "...", ...)`で差し替える前提)。
"""

import logging

import pandas as pd

from data_collection.fixed_data_loader import load_fixed_data
from data_collection.tvdatafeed_client import fetch_from_tvdatafeed
from data_collection.historyofmarket_client import fetch_from_historyofmarket

logger = logging.getLogger(__name__)

# ティッカー別の取得元振り分け(コード内辞書で管理。§2.2参照)
SOURCE_MAP: dict[str, str] = {
    "S5FI": "tvdatafeed",
    "S5TH": "tvdatafeed",
}


def fetch_data(
    ticker: str,
    start_date: str,
    end_date: str | None,
    progress: str,
    yfinance_fetcher,
) -> pd.DataFrame:
    """fixed_data優先で範囲を充足し、残りをSOURCE_MAPに従って取得・結合する。"""
    fixed_df = load_fixed_data(ticker, start_date, end_date)

    if fixed_df is not None and not fixed_df.empty:
        last_fixed_date = fixed_df["date"].max()
        remaining_start = (
            pd.to_datetime(last_fixed_date) + pd.Timedelta(days=1)
        ).date().isoformat()
        remaining_end = end_date
        if remaining_end is not None and remaining_start > remaining_end:
            # fixed_dataが要求範囲を完全にカバーしている
            return fixed_df.sort_values("date").reset_index(drop=True)
    else:
        remaining_start = start_date
        remaining_end = end_date

    source = SOURCE_MAP.get(ticker, "yfinance")

    if source == "tvdatafeed":
        remaining_df = fetch_from_tvdatafeed(ticker, remaining_start, remaining_end)
        if remaining_df is None or remaining_df.empty:
            remaining_df = fetch_from_historyofmarket(ticker, remaining_start, remaining_end)
    else:
        remaining_df = yfinance_fetcher(ticker, remaining_start, end_date, progress)

    parts = [df for df in (fixed_df, remaining_df) if df is not None and not df.empty]
    if not parts:
        return pd.DataFrame()

    # 重複する日付は残り分(下位ソース)を優先: fixed_dataの重複行を先に落としてからconcat
    if fixed_df is not None and not fixed_df.empty and remaining_df is not None and not remaining_df.empty:
        remaining_dates = set(remaining_df["date"])
        fixed_df = fixed_df[~fixed_df["date"].isin(remaining_dates)]
        parts = [df for df in (fixed_df, remaining_df) if df is not None and not df.empty]

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged
