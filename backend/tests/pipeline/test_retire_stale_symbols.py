"""退役判定の供給側検証テスト（scripts/retire_stale_symbols.py）

退役は不可逆な運用判断なので、「本当に上場廃止か」の切り分けが誤ると
現役の上場企業をバックテスト母集団から落とすことになる。

2026-08-02 に判明した第3のケース（上流の系列切断）を確実に区別することを保証する。
Yahoo が銘柄レコードを作り直すと `firstTradeDate` が最近の日付に打ち直され、
chart API の時系列だけがそこから始まる。上場廃止でも改称でもないため
**退役させてはいけない**（対応は旧 Parquet 世代からの復元）。
"""

import datetime as dt
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.retire_stale_symbols import _is_truncated  # noqa: E402


def _epoch(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time(), tzinfo=dt.timezone.utc).timestamp())


def test_detects_truncated_series():
    """BLD(TopBuild) の実測値。初回売買日が1ヶ月前なのに52週高値 559.47 は自己矛盾。

    52週高安は1年分のデータが無ければ算出できないので、集計レイヤーに履歴があるのに
    時系列だけが孤立している証拠になる。
    """
    meta = {
        "firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=33)),
        "fiftyTwoWeekLow": 330.51,
        "fiftyTwoWeekHigh": 559.468,
    }
    assert _is_truncated(meta) is True


def test_normal_symbol_is_not_truncated():
    """AAPL の実測値。初回売買日 1980-12-12 は正常なので誤検出しない。"""
    meta = {
        "firstTradeDate": _epoch(dt.date(1980, 12, 12)),
        "fiftyTwoWeekLow": 201.68,
        "fiftyTwoWeekHigh": 344.57,
    }
    assert _is_truncated(meta) is False


def test_genuine_new_listing_is_not_truncated():
    """本当に最近上場した銘柄は 52週レンジが立たない（lo==hi）ので誤検出しない。

    ここを取り違えると、新規上場銘柄を「切断」と誤判定して復元を試みることになる。
    """
    meta = {
        "firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=10)),
        "fiftyTwoWeekLow": 25.0,
        "fiftyTwoWeekHigh": 25.0,
    }
    assert _is_truncated(meta) is False


@pytest.mark.parametrize("meta", [
    {},
    {"firstTradeDate": None, "fiftyTwoWeekLow": 1.0, "fiftyTwoWeekHigh": 2.0},
    {"firstTradeDate": _epoch(dt.date.today()), "fiftyTwoWeekLow": None, "fiftyTwoWeekHigh": 2.0},
    {"firstTradeDate": _epoch(dt.date.today()), "fiftyTwoWeekLow": 1.0, "fiftyTwoWeekHigh": None},
])
def test_missing_fields_are_not_truncated(meta):
    """meta が欠けているときは判定しない（安全側＝既存の分類に委ねる）。"""
    assert _is_truncated(meta) is False


def test_boundary_just_under_one_year():
    """境界: 初回売買日が1年未満なら切断、1年以上なら正常。"""
    lo_hi = {"fiftyTwoWeekLow": 10.0, "fiftyTwoWeekHigh": 20.0}
    assert _is_truncated({"firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=364)), **lo_hi}) is True
    assert _is_truncated({"firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=366)), **lo_hi}) is False
