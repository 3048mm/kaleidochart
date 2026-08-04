"""退役判定の供給側検証テスト（scripts/retire_stale_symbols.py）

退役は不可逆な運用判断なので、切り分けを誤ると現役の上場企業を
バックテスト母集団から落とすことになる。

`firstTradeDate` が最近の日付に打ち直され、chart API の時系列だけがそこから始まる
状態を検出する。2026-08-02 にはこれを「Yahoo のデータ不具合」と誤診したが、
**2026-08-04 に SEC EDGAR で確認したところ全件が実際のコーポレートアクション**
（改称・買収による登録抹消）だった。

したがって本判定は「データ不具合」ではなく **「何かが起きたので調べろ」の合図**であり、
改称なら付け替え・上場廃止なら退役と対応が正反対になる。自動では退役させず、
`--tickers` での明示指定（＝人が EDGAR で調べた上での判断）のみ通す。
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

from scripts.retire_stale_symbols import _looks_like_corporate_action  # noqa: E402


def _epoch(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time(), tzinfo=dt.timezone.utc).timestamp())


def test_detects_corporate_action():
    """BLD(TopBuild) の実測値。初回売買日が1ヶ月前なのに52週高値 559.47 は自己矛盾。

    実際には 2026-07-13 に Form 15-12G を提出して登録抹消（QXO による買収）されていた。
    """
    meta = {
        "firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=33)),
        "fiftyTwoWeekLow": 330.51,
        "fiftyTwoWeekHigh": 559.468,
    }
    assert _looks_like_corporate_action(meta) is True


def test_normal_symbol_is_not_flagged():
    """AAPL の実測値。初回売買日 1980-12-12 は正常なので誤検出しない。"""
    meta = {
        "firstTradeDate": _epoch(dt.date(1980, 12, 12)),
        "fiftyTwoWeekLow": 201.68,
        "fiftyTwoWeekHigh": 344.57,
    }
    assert _looks_like_corporate_action(meta) is False


def test_genuine_new_listing_is_not_flagged():
    """本当に最近上場した銘柄は 52週レンジが立たない（lo==hi）ので誤検出しない。

    ここを取り違えると、新規上場銘柄をコーポレートアクションと誤判定してしまう。
    """
    meta = {
        "firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=10)),
        "fiftyTwoWeekLow": 25.0,
        "fiftyTwoWeekHigh": 25.0,
    }
    assert _looks_like_corporate_action(meta) is False


@pytest.mark.parametrize("meta", [
    {},
    {"firstTradeDate": None, "fiftyTwoWeekLow": 1.0, "fiftyTwoWeekHigh": 2.0},
    {"firstTradeDate": _epoch(dt.date.today()), "fiftyTwoWeekLow": None, "fiftyTwoWeekHigh": 2.0},
    {"firstTradeDate": _epoch(dt.date.today()), "fiftyTwoWeekLow": 1.0, "fiftyTwoWeekHigh": None},
])
def test_missing_fields_are_not_flagged(meta):
    """meta が欠けているときは判定しない（安全側＝既存の分類に委ねる）。"""
    assert _looks_like_corporate_action(meta) is False


def test_boundary_just_under_one_year():
    """境界: 初回売買日が1年未満なら要調査、1年以上なら正常。"""
    lo_hi = {"fiftyTwoWeekLow": 10.0, "fiftyTwoWeekHigh": 20.0}
    assert _looks_like_corporate_action({"firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=364)), **lo_hi}) is True
    assert _looks_like_corporate_action({"firstTradeDate": _epoch(dt.date.today() - dt.timedelta(days=366)), **lo_hi}) is False
