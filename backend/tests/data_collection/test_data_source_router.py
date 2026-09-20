"""data_source_router.py の単体テスト（TDD red フェーズ）。

fixed_data → tvdatafeed → historyofmarket(フォールバック) → yfinance、の
振り分け・結合ロジックを検証する本体。下位の各アダプタ（
`load_fixed_data` / `fetch_from_tvdatafeed` / `fetch_from_historyofmarket`）は
モジュール属性として直接importされている前提（本リポジトリの既存の
`data_collection.*` の import 規約 - 例: `t2_prices.py` の
`from data_collection.fetcher import fetch_daily_data` - に合わせた設計）で、
`monkeypatch.setattr(router, "...", ...)` で差し替える。

`yfinance_fetcher` は明示的な関数引数（循環import回避のためのDI）なので、
そちらは疑似callableを直接渡す。
"""

from datetime import date

import pandas as pd

import data_collection.data_source_router as router
from data_collection.data_source_router import SOURCE_MAP, fetch_data


def _df(rows):
    """rows: [(date, close), ...] -> open=high=low=closeの簡易DataFrame。"""
    return pd.DataFrame(
        {
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "volume": [0] * len(rows),
        }
    )


def _empty_df():
    return pd.DataFrame()


class _RecordingFetcher:
    """`yfinance_fetcher`引数に渡す疑似callable。呼び出し引数を記録する。"""

    def __init__(self, result=None):
        self.calls = []
        self._result = result if result is not None else _empty_df()

    def __call__(self, ticker, start_date, end_date, progress):
        self.calls.append(
            {"ticker": ticker, "start_date": start_date, "end_date": end_date, "progress": progress}
        )
        return self._result


def _raise(msg):
    def _f(*a, **k):
        raise AssertionError(msg)
    return _f


# --- SOURCE_MAPに無いティッカー: yfinance_fetcherだけが呼ばれる -----------------

def test_ticker_not_in_source_map_uses_only_yfinance(monkeypatch):
    assert "AAPL" not in SOURCE_MAP
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: _empty_df())
    monkeypatch.setattr(router, "fetch_from_tvdatafeed",
                         _raise("SOURCE_MAPに無いティッカーでtvdatafeedが呼ばれた"))
    monkeypatch.setattr(router, "fetch_from_historyofmarket",
                         _raise("SOURCE_MAPに無いティッカーでhistoryofmarketが呼ばれた"))

    yf_result = _df([(date(2020, 1, 1), 1.0), (date(2020, 1, 2), 2.0)])
    fetcher = _RecordingFetcher(result=yf_result)

    df = fetch_data("AAPL", "2020-01-01", "2020-01-02", "", fetcher)

    assert len(fetcher.calls) == 1
    assert fetcher.calls[0] == {
        "ticker": "AAPL", "start_date": "2020-01-01", "end_date": "2020-01-02", "progress": "",
    }
    assert list(df["date"]) == [date(2020, 1, 1), date(2020, 1, 2)]


# --- SOURCE_MAPにあるティッカー: fixed_dataが完全カバー -------------------------

def test_source_map_ticker_fully_covered_by_fixed_data(monkeypatch):
    fixed_result = _df([(date(2020, 1, 1), 10.0), (date(2020, 1, 2), 11.0)])
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: fixed_result)
    monkeypatch.setattr(router, "fetch_from_tvdatafeed",
                         _raise("fixed_dataで完全にカバーされているのにtvdatafeedが呼ばれた"))
    monkeypatch.setattr(router, "fetch_from_historyofmarket",
                         _raise("fixed_dataで完全にカバーされているのにhistoryofmarketが呼ばれた"))

    fetcher = _RecordingFetcher()
    df = fetch_data("S5FI", "2020-01-01", "2020-01-02", "", fetcher)

    assert fetcher.calls == [], "fixed_dataで完全にカバーされているのにyfinance_fetcherが呼ばれた"
    assert list(df["date"]) == [date(2020, 1, 1), date(2020, 1, 2)]
    assert df["close"].tolist() == [10.0, 11.0]


# --- fixed_dataが一部だけカバー: 残りの範囲がtvdatafeedへ渡る -------------------

def test_partial_fixed_data_passes_remaining_range_to_tvdatafeed(monkeypatch):
    fixed_result = _df([(date(2020, 1, 1), 10.0), (date(2020, 1, 5), 11.0)])
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: fixed_result)

    tv_calls = []

    def _tv(ticker, start_date, end_date=None, **kwargs):
        tv_calls.append({"ticker": ticker, "start_date": start_date, "end_date": end_date})
        return _df([(date(2020, 1, 6), 20.0), (date(2020, 1, 10), 21.0)])

    monkeypatch.setattr(router, "fetch_from_tvdatafeed", _tv)
    monkeypatch.setattr(router, "fetch_from_historyofmarket",
                         _raise("tvdatafeedが成功しているのにフォールバックが呼ばれた"))

    fetcher = _RecordingFetcher()
    df = fetch_data("S5FI", "2020-01-01", "2020-01-10", "", fetcher)

    assert len(tv_calls) == 1
    assert tv_calls[0]["ticker"] == "S5FI"
    assert tv_calls[0]["start_date"] == "2020-01-06", "fixed_data最終日の翌日から要求されていない"
    assert tv_calls[0]["end_date"] == "2020-01-10"
    assert list(df["date"]) == [
        date(2020, 1, 1), date(2020, 1, 5), date(2020, 1, 6), date(2020, 1, 10),
    ]


# --- tvdatafeedが空 -> historyofmarketへフォールバック ---------------------------

def test_tvdatafeed_empty_falls_back_to_historyofmarket(monkeypatch):
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: _empty_df())
    monkeypatch.setattr(router, "fetch_from_tvdatafeed", lambda *a, **k: _empty_df())

    hom_calls = []

    def _hom(ticker, start_date, end_date=None, **kwargs):
        hom_calls.append({"ticker": ticker, "start_date": start_date, "end_date": end_date})
        return _df([(date(2020, 1, 1), 30.0)])

    monkeypatch.setattr(router, "fetch_from_historyofmarket", _hom)

    fetcher = _RecordingFetcher()
    df = fetch_data("S5TH", "2020-01-01", "2020-01-05", "", fetcher)

    assert len(hom_calls) == 1
    assert hom_calls[0]["ticker"] == "S5TH"
    assert hom_calls[0]["start_date"] == "2020-01-01"
    assert hom_calls[0]["end_date"] == "2020-01-05"
    assert list(df["date"]) == [date(2020, 1, 1)]


# --- tvdatafeedとhistoryofmarket両方失敗 ----------------------------------------

def test_both_tv_and_historyofmarket_fail_returns_only_fixed_data(monkeypatch):
    """fixed_data分がある場合、それだけが返る。"""
    fixed_result = _df([(date(2020, 1, 1), 10.0)])
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: fixed_result)
    monkeypatch.setattr(router, "fetch_from_tvdatafeed", lambda *a, **k: _empty_df())
    monkeypatch.setattr(router, "fetch_from_historyofmarket", lambda *a, **k: _empty_df())

    fetcher = _RecordingFetcher()
    df = fetch_data("S5FI", "2020-01-01", "2020-01-10", "", fetcher)

    assert list(df["date"]) == [date(2020, 1, 1)]


def test_both_tv_and_historyofmarket_fail_with_no_fixed_data_returns_empty(monkeypatch):
    """fixed_dataも無い場合、全体として空DataFrame（既存の失敗契約通り）。"""
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: _empty_df())
    monkeypatch.setattr(router, "fetch_from_tvdatafeed", lambda *a, **k: _empty_df())
    monkeypatch.setattr(router, "fetch_from_historyofmarket", lambda *a, **k: _empty_df())

    fetcher = _RecordingFetcher()
    df = fetch_data("S5FI", "2020-01-01", "2020-01-10", "", fetcher)

    assert isinstance(df, pd.DataFrame)
    assert df.empty


# --- 結合結果は date 昇順。重複日付は残り分（下位ソース）を優先 -------------------

def test_merge_result_is_sorted_ascending_and_remaining_source_wins_on_duplicate_date(monkeypatch):
    fixed_result = _df([
        (date(2020, 1, 1), 1.0),
        (date(2020, 1, 5), 5.0),
    ])
    monkeypatch.setattr(router, "load_fixed_data", lambda *a, **k: fixed_result)

    # 残り分がfixed_dataと重複する日付(2020-01-05)を含むケース(境界のズレ等を想定)
    tv_result = _df([
        (date(2020, 1, 7), 7.0),
        (date(2020, 1, 5), 99.0),
        (date(2020, 1, 6), 6.0),
    ])
    monkeypatch.setattr(router, "fetch_from_tvdatafeed", lambda *a, **k: tv_result)
    monkeypatch.setattr(router, "fetch_from_historyofmarket",
                         _raise("tvdatafeedが成功しているのにフォールバックが呼ばれた"))

    fetcher = _RecordingFetcher()
    df = fetch_data("S5FI", "2020-01-01", "2020-01-07", "", fetcher)

    assert list(df["date"]) == [
        date(2020, 1, 1), date(2020, 1, 5), date(2020, 1, 6), date(2020, 1, 7),
    ]
    close_by_date = dict(zip(df["date"], df["close"]))
    assert close_by_date[date(2020, 1, 5)] == 99.0, "重複日付は残り分（下位ソース）が優先されるべき"
