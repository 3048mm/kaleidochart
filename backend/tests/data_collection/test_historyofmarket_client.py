"""historyofmarket_client.py の単体テスト（TDD red フェーズ）。

`historyofmarket.com` の breadth JSON API（`GET /api/sp500/breadth.json`）を
tvDatafeed失敗時のフォールバックとして叩くアダプタ。**実際のHTTPアクセスは
一切行わない** — `http_get` 引数（テスト容易性のためのDI）に疑似関数を
注入して検証する。
"""

from datetime import date

import pandas as pd

from data_collection.historyofmarket_client import fetch_from_historyofmarket


# 実測されたレスポンス形式を模したサンプル(2023-07-13がAPI側の最古日)
_SAMPLE_JSON = {
    "_license": "CC BY 4.0",
    "_attribution": "History of Market · 美股編年寢 (historyofmarket.com)",
    "updated": "2026-09-17",
    "index": "S&P 500",
    "members": 503,
    "series": [
        {"date": "2023-07-13", "pct50": 70.1, "pct200": 80.2, "close": 4500.0},
        {"date": "2026-09-15", "pct50": 34.0, "pct200": 55.0, "close": 7600.0},
        {"date": "2026-09-16", "pct50": 32.0, "pct200": 54.0, "close": 7620.0},
        {"date": "2026-09-17", "pct50": 31.4, "pct200": 53.5, "close": 7637.76},
    ],
    "latest": {},
    "source": "Member daily closes, current constituents",
}


class _FakeResponse:
    """`requests.Response` のうち使う範囲だけを再現する疑似レスポンス。"""

    def __init__(self, json_data=None, status_code=200, json_error=None):
        self._json_data = json_data
        self.status_code = status_code
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


def _make_http_get(response=None, exception=None):
    calls = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if exception is not None:
            raise exception
        return response

    return _get, calls


# --- 正常系: pct50/pct200のマッピング ------------------------------------------

def test_maps_pct50_to_close_for_s5fi():
    http_get, calls = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("S5FI", "2026-09-15", "2026-09-17", http_get=http_get)

    assert list(df["date"]) == [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    assert df["close"].tolist() == [34.0, 32.0, 31.4]
    assert (df["open"] == df["close"]).all()
    assert (df["high"] == df["close"]).all()
    assert (df["low"] == df["close"]).all()
    assert (df["volume"] == 0).all()
    assert pd.api.types.is_integer_dtype(df["volume"])
    assert calls and "historyofmarket.com/api/sp500/breadth.json" in calls[0]


def test_maps_pct200_to_close_for_s5th():
    http_get, _ = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("S5TH", "2026-09-15", "2026-09-17", http_get=http_get)

    assert df["close"].tolist() == [55.0, 54.0, 53.5]


# --- 対象外ticker --------------------------------------------------------------

def test_unsupported_ticker_returns_empty_without_http_call():
    """S5FI/S5TH以外は空DataFrame。HTTP取得自体も発生しない。"""
    http_get, calls = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("AAPL", "2026-09-15", "2026-09-17", http_get=http_get)

    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert calls == [], "対象外tickerでHTTP取得が発生している"


# --- 失敗系: 例外・非200・JSON parse失敗を外に伝播させず空DataFrameを返す --------

def test_http_exception_returns_empty_dataframe():
    http_get, _ = _make_http_get(exception=RuntimeError("network down"))

    df = fetch_from_historyofmarket("S5FI", "2026-09-01", "2026-09-17", http_get=http_get)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_non_200_status_returns_empty_dataframe():
    http_get, _ = _make_http_get(response=_FakeResponse(_SAMPLE_JSON, status_code=503))

    df = fetch_from_historyofmarket("S5FI", "2026-09-01", "2026-09-17", http_get=http_get)

    assert df.empty


def test_json_parse_failure_returns_empty_dataframe():
    http_get, _ = _make_http_get(response=_FakeResponse(json_error=ValueError("bad json")))

    df = fetch_from_historyofmarket("S5FI", "2026-09-01", "2026-09-17", http_get=http_get)

    assert df.empty


# --- 日付範囲フィルタ ------------------------------------------------------------

def test_filters_by_date_range():
    http_get, _ = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("S5FI", "2026-09-16", "2026-09-16", http_get=http_get)

    assert list(df["date"]) == [date(2026, 9, 16)]


# --- APIの最古日(2023-07-13)より前を要求した場合 --------------------------------

def test_request_entirely_before_earliest_available_date_returns_empty():
    """APIのデータは2023-07-13以降のみ。それより前だけを要求すると空になる。"""
    http_get, _ = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("S5FI", "2020-01-01", "2023-07-01", http_get=http_get)

    assert df.empty


def test_request_straddling_earliest_available_date_returns_partial_result():
    """開始日をAPIのデータより前に設定しても、存在する分だけ部分的に返る。"""
    http_get, _ = _make_http_get(response=_FakeResponse(_SAMPLE_JSON))

    df = fetch_from_historyofmarket("S5FI", "2020-01-01", "2023-07-13", http_get=http_get)

    assert list(df["date"]) == [date(2023, 7, 13)]
    assert df["close"].tolist() == [70.1]
