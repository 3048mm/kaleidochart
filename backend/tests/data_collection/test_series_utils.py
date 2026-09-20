"""_series_utils.py の単体テスト。

fixed_data_loader/tvdatafeed_client/historyofmarket_client の3アダプタで
重複していた「列順の整形」「日付範囲フィルタ」を切り出した内部ユーティリティ。
"""

from datetime import date

import pandas as pd

from data_collection._series_utils import filter_date_range, normalize_columns


def test_normalize_columns_orders_columns_as_expected():
    """列の並び順を date/open/high/low/close/volume に揃える(元の順は無関係)。"""
    df = pd.DataFrame(
        {
            "volume": [100],
            "close": [10.0],
            "date": [date(2026, 1, 1)],
            "low": [8.0],
            "open": [9.0],
            "high": [11.0],
        }
    )

    result = normalize_columns(df)

    assert list(result.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert result["date"].tolist() == [date(2026, 1, 1)]
    assert result["volume"].tolist() == [100]


def test_filter_date_range_with_both_start_and_end():
    """start_date~end_date両方指定時、範囲内の行だけが残る。"""
    df = pd.DataFrame(
        {
            "date": [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 10)],
            "close": [1.0, 2.0, 3.0],
        }
    )

    result = filter_date_range(df, "2026-01-02", "2026-01-05")

    assert result["date"].tolist() == [date(2026, 1, 5)]


def test_filter_date_range_with_end_date_none_has_no_upper_bound():
    """end_dateがNoneの場合、start_date以降が上限なしで残る。"""
    df = pd.DataFrame(
        {
            "date": [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 10)],
            "close": [1.0, 2.0, 3.0],
        }
    )

    result = filter_date_range(df, "2026-01-05", None)

    assert result["date"].tolist() == [date(2026, 1, 5), date(2026, 1, 10)]
