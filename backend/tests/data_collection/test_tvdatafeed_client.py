"""tvdatafeed_client.py の単体テスト（TDD red フェーズ）。

`tvDatafeed`（`tvdatafeed-enhanced`）ライブラリは、匿名アクセスでも
`exchange='INDEX'` を指定すれば `S5FI`/`S5TH` の値を取得できることが
実運用検証済み。**実際のネットワーク接続は一切行わない** — `tv_client`
引数（テスト容易性のためのDI）に疑似クライアントを注入して検証する。
"""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from data_collection.tvdatafeed_client import fetch_from_tvdatafeed


def _sample_hist_df(dates=None):
    """`tv_client.get_hist()` が返す形（datetimeインデックス、列は小文字）を模す。"""
    if dates is None:
        dates = [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    idx = pd.DatetimeIndex(
        [datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc) for d in dates],
        name="datetime",
    )
    n = len(dates)
    return pd.DataFrame(
        {
            "symbol": ["INDEX:S5FI"] * n,
            "open": [34.59, 30.81, 31.01][:n],
            "high": [35.18, 35.18, 31.01][:n],
            "low": [33.39, 29.42, 29.62][:n],
            "close": [34.59, 30.81, 30.81][:n],
            "volume": [0.0, 0.0, 0.0][:n],
        },
        index=idx,
    )


class _FakeTvClient:
    """`tvDatafeed.TvDatafeed` のうち `get_hist()` だけを再現する疑似クライアント。"""

    def __init__(self, hist_df=None, exception=None):
        self.calls = []
        self._hist_df = hist_df
        self._exception = exception

    def get_hist(self, symbol=None, exchange=None, interval=None, n_bars=None):
        self.calls.append(
            {"symbol": symbol, "exchange": exchange, "interval": interval, "n_bars": n_bars}
        )
        if self._exception is not None:
            raise self._exception
        return self._hist_df


# --- 正常系: 変換・フィルタ ---------------------------------------------------

def test_converts_get_hist_result_to_expected_columns_and_filters_range():
    fake = _FakeTvClient(hist_df=_sample_hist_df())

    df = fetch_from_tvdatafeed("S5FI", "2026-09-16", "2026-09-17", tv_client=fake)

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert list(df["date"]) == [date(2026, 9, 16), date(2026, 9, 17)]
    assert df["close"].tolist() == [30.81, 30.81]
    assert pd.api.types.is_float_dtype(df["open"])
    assert pd.api.types.is_integer_dtype(df["volume"])


def test_uses_index_exchange_by_default_and_daily_interval():
    from tvDatafeed import Interval

    fake = _FakeTvClient(hist_df=_sample_hist_df())

    fetch_from_tvdatafeed("S5FI", "2026-09-01", tv_client=fake)

    call = fake.calls[0]
    assert call["symbol"] == "S5FI"
    assert call["exchange"] == "INDEX"
    assert call["interval"] == Interval.in_daily


def test_custom_exchange_is_passed_through():
    fake = _FakeTvClient(hist_df=_sample_hist_df())

    fetch_from_tvdatafeed("S5FI", "2026-09-01", exchange="SP", tv_client=fake)

    assert fake.calls[0]["exchange"] == "SP"


# --- 失敗系: 例外・None・空データを外に伝播させず空DataFrameを返す ---------------

def test_returns_empty_dataframe_when_get_hist_raises():
    """接続断（実測: `Connection to remote host was lost`）等の例外を外に伝播させない。"""
    fake = _FakeTvClient(exception=RuntimeError("Connection to remote host was lost"))

    df = fetch_from_tvdatafeed("S5FI", "2026-09-01", tv_client=fake)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_returns_empty_dataframe_when_get_hist_returns_none():
    fake = _FakeTvClient(hist_df=None)

    df = fetch_from_tvdatafeed("S5FI", "2026-09-01", tv_client=fake)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_returns_empty_dataframe_when_get_hist_returns_empty_dataframe():
    fake = _FakeTvClient(hist_df=pd.DataFrame())

    df = fetch_from_tvdatafeed("S5FI", "2026-09-01", tv_client=fake)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


# --- n_bars: start_dateから今日までの日数を営業日換算＋余裕を持って計算する -------

def test_n_bars_covers_requested_range_with_business_day_margin():
    """営業日換算(約5/7)ちょうどだと祝日等でデータ不足しうるため、余裕(マージン)が必要。

    一方でカレンダー日数そのものより多く要求するのは過剰。
    """
    today = date.today()
    start = today - timedelta(days=365)
    fake = _FakeTvClient(hist_df=_sample_hist_df())

    fetch_from_tvdatafeed("S5FI", start.isoformat(), tv_client=fake)

    n_bars = fake.calls[0]["n_bars"]
    calendar_days = (today - start).days
    business_day_estimate = calendar_days * 5 / 7

    assert n_bars > business_day_estimate, "営業日換算分ちょうどで、マージンが無い"
    assert n_bars < calendar_days, "カレンダー日数そのものより多く要求している"


def test_n_bars_grows_with_wider_date_range():
    today = date.today()
    fake = _FakeTvClient(hist_df=_sample_hist_df())

    fetch_from_tvdatafeed("S5FI", (today - timedelta(days=30)).isoformat(), tv_client=fake)
    n_bars_short = fake.calls[0]["n_bars"]

    fetch_from_tvdatafeed("S5FI", (today - timedelta(days=365)).isoformat(), tv_client=fake)
    n_bars_long = fake.calls[1]["n_bars"]

    assert n_bars_long > n_bars_short


# --- tv_client省略時のデフォルト生成 -------------------------------------------

def test_creates_default_tvdatafeed_client_when_not_injected(monkeypatch):
    """`tv_client`省略時、内部で`tvDatafeed.TvDatafeed()`を生成しようとすること。

    実際のネットワーク接続はモック（`TvDatafeed`クラス自体を差し替え）で防ぐ。
    """
    import data_collection.tvdatafeed_client as mod

    created = []

    class _StubTv:
        def __init__(self, *a, **kw):
            created.append(self)

        def get_hist(self, **kwargs):
            return _sample_hist_df()

    monkeypatch.setattr(mod, "TvDatafeed", _StubTv)

    df = fetch_from_tvdatafeed("S5FI", "2026-09-01")

    assert len(created) == 1, "tv_client省略時にTvDatafeed()が生成されていない"
    assert not df.empty
