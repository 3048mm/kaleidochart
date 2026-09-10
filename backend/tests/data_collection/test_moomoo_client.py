"""moomoo_client.py の単体テスト。

**実際の moomoo API・OpenD には一切接続しない。** `context_factory` / `port_check` を
差し替えた疑似コンテキスト（`FakeQuoteContext`）だけで検証する。
Wrapper を単体で検証できるようにする、というのがこのモジュールの存在理由そのもの
（`moomoo` パッケージ本体は `moomoo_client.py` 以外からimportしない制約と対）。
"""

from __future__ import annotations

import os
import re

import pandas as pd
import pytest

from data_collection.moomoo_client import (
    MoomooApiError,
    MoomooClient,
    MoomooNotConnectedError,
    SlidingWindowRateLimiter,
)


class FakeQuoteContext:
    """`moomoo.OpenQuoteContext` のうち、このラッパーが使う3メソッドだけを再現する。"""

    def __init__(self):
        self.closed = False
        self.rehab_calls: list[str] = []
        self.kline_calls: list[dict] = []
        self.quota_calls = 0
        self.rehab_response = (0, "fake-rehab-data")
        self.kline_pages = [(0, pd.DataFrame({"close": [1]}), None)]
        self.quota_response = (0, (0, 300, []))

    def get_rehab(self, code):
        self.rehab_calls.append(code)
        return self.rehab_response

    def request_history_kline(self, code, start=None, end=None, ktype=None,
                               autype=None, max_count=None, page_req_key=None):
        self.kline_calls.append({"code": code, "page_req_key": page_req_key})
        idx = len(self.kline_calls) - 1
        return self.kline_pages[idx]

    def get_history_kl_quota(self, get_detail=False):
        self.quota_calls += 1
        return self.quota_response

    def close(self):
        self.closed = True


def _client(port_open=True, **kwargs):
    fake = FakeQuoteContext()
    factory_calls = []

    def factory(host, port):
        factory_calls.append((host, port))
        return fake

    c = MoomooClient(
        context_factory=factory,
        port_check=lambda host, port, timeout: port_open,
        **kwargs,
    )
    return c, fake, factory_calls


# --- 接続ガード --------------------------------------------------------------

def test_raises_when_opend_not_running():
    """OpenD が起動していなければ、疑似コンテキストにすら触れず即座に失敗する。"""
    c, fake, factory_calls = _client(port_open=False)
    with pytest.raises(MoomooNotConnectedError):
        c.get_rehab("AAPL")
    assert factory_calls == []


def test_connects_lazily_and_reuses_context():
    c, fake, factory_calls = _client()
    c.get_rehab("AAPL")
    c.get_rehab("MSFT")
    assert len(factory_calls) == 1


def test_close_resets_context_for_reconnect():
    c, fake, factory_calls = _client()
    c.get_rehab("AAPL")
    c.close()
    assert fake.closed is True
    c.get_rehab("AAPL")
    assert len(factory_calls) == 2


# --- get_rehab ----------------------------------------------------------------

def test_get_rehab_applies_us_prefix():
    c, fake, _ = _client()
    c.get_rehab("AAPL")
    assert fake.rehab_calls == ["US.AAPL"]


def test_get_rehab_always_prefixes_us_market():
    """本プロジェクトの対象は米国株のみ。市場プレフィックスの自動判定はしない（YAGNI）。"""
    c, fake, _ = _client()
    c.get_rehab("BRK-B")
    assert fake.rehab_calls == ["US.BRK-B"]


def test_get_rehab_prefixes_even_tickers_containing_a_dot():
    """以前は `.` を含むティッカーをプレフィックスなしで素通ししていた分岐を削除したことの回帰確認。

    `.` の有無で分岐しない単純な実装になったことを、まさにその分岐が発火していた
    入力（ドット入りティッカー）で確認する。
    """
    c, fake, _ = _client()
    c.get_rehab("BRK.B")
    assert fake.rehab_calls == ["US.BRK.B"]


def test_get_rehab_returns_data_on_success():
    c, fake, _ = _client()
    assert c.get_rehab("AAPL") == "fake-rehab-data"


def test_get_rehab_raises_on_api_error():
    c, fake, _ = _client()
    fake.rehab_response = (-1, "権限がありません")
    with pytest.raises(MoomooApiError) as exc:
        c.get_rehab("AAPL")
    assert "権限がありません" in str(exc.value)


# --- request_history_kline（ページング） ---------------------------------------

def test_request_history_kline_single_page():
    c, fake, _ = _client()
    fake.kline_pages = [(0, pd.DataFrame({"close": [1, 2]}), None)]
    out = c.request_history_kline("AAPL", start="2026-01-01", end="2026-01-05")
    assert list(out["close"]) == [1, 2]
    assert fake.kline_calls[0]["code"] == "US.AAPL"


def test_request_history_kline_follows_pagination():
    c, fake, _ = _client()
    fake.kline_pages = [
        (0, pd.DataFrame({"close": [1]}), "next-key"),
        (0, pd.DataFrame({"close": [2]}), None),
    ]
    out = c.request_history_kline("AAPL")
    assert list(out["close"]) == [1, 2]
    assert fake.kline_calls[1]["page_req_key"] == "next-key"


def test_request_history_kline_raises_on_api_error():
    c, fake, _ = _client()
    fake.kline_pages = [(-1, "quota exceeded", None)]
    with pytest.raises(MoomooApiError):
        c.request_history_kline("AAPL")


# --- レート制限 -----------------------------------------------------------------

def test_rate_limiter_allows_burst_up_to_limit():
    """上限までは待たない。"""
    clock = [0.0]
    slept = []
    rl = SlidingWindowRateLimiter(limit=3, window_sec=30,
                                   now=lambda: clock[0], sleep=slept.append)
    for _ in range(3):
        rl.acquire()
    assert slept == []


def test_rate_limiter_waits_when_limit_exceeded():
    clock = [0.0]

    def _sleep(sec):
        clock[0] += sec

    rl = SlidingWindowRateLimiter(limit=3, window_sec=30,
                                   now=lambda: clock[0], sleep=_sleep)
    for _ in range(4):
        rl.acquire()
    assert clock[0] >= 30 - 1e-9


def test_rate_limiter_forgets_old_calls():
    clock = [0.0]
    slept = []
    rl = SlidingWindowRateLimiter(limit=3, window_sec=30,
                                   now=lambda: clock[0], sleep=slept.append)
    for _ in range(3):
        rl.acquire()
    clock[0] += 31  # 窓の外に出た
    rl.acquire()
    assert slept == []


def test_client_calls_go_through_rate_limiter():
    c, fake, _ = _client()
    acquired = []
    c.rate_limiter.acquire = lambda: acquired.append(1)
    c.get_rehab("AAPL")
    assert acquired == [1]


# --- 使用量の可視化 --------------------------------------------------------------

def test_usage_status_reports_rate_limit_window():
    c, fake, _ = _client()
    c.get_rehab("AAPL")
    status = c.usage_status()
    assert status["rate_limit"]["calls_in_window"] >= 1
    assert status["rate_limit"]["limit"] == c.rate_limiter.limit


def test_usage_status_reports_history_kl_quota_when_connected():
    c, fake, _ = _client()
    fake.quota_response = (0, (5, 295, []))
    status = c.usage_status()
    assert status["connected"] is True
    assert status["history_kl_quota"] == {"used": 5, "remain": 295}
    assert status["history_kl_quota_error"] is None


def test_usage_status_surfaces_quota_query_failure_instead_of_silently_dropping_it():
    """接続はできているがクォータ照会自体が失敗したケースを握り潰さない。

    `history_kl_quota: None` のままだと「未接続」「正常に0件」と見分けがつかない
    （フェイルラウド逸脱）ため、専用フィールドでエラー内容を残す。
    """
    c, fake, _ = _client()
    fake.quota_response = (-1, "quota query failed")
    status = c.usage_status()
    assert status["connected"] is True
    assert status["history_kl_quota"] is None
    assert status["history_kl_quota_error"] == "quota query failed"


def test_usage_status_does_not_raise_when_opend_not_running():
    """使用量確認そのものが、接続失敗で例外にならないこと（呼び出し側が気軽に確認できるように）。"""
    c, fake, _ = _client(port_open=False)
    status = c.usage_status()
    assert status["connected"] is False
    assert status["history_kl_quota"] is None


# --- 直接importの禁止（本体制約） --------------------------------------------------

def test_no_direct_moomoo_import_outside_client():
    """`moomoo` パッケージは `moomoo_client.py` だけから import してよい。

    ユーザー要望「本体コードから moomoo API を直接呼び出さず、Wrapper 経由で
    呼び出す制約を入れる」を機械的に固定する。
    """
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".."))
    allowed = os.path.abspath(
        os.path.join(backend_dir, "data_collection", "moomoo_client.py"))
    pattern = re.compile(r"^\s*(import moomoo\b|from moomoo\b)", re.MULTILINE)
    violations = []
    for root, dirs, files in os.walk(backend_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            if os.path.abspath(path) == allowed:
                continue
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if pattern.search(text):
                violations.append(path)
    assert violations == [], (
        f"moomoo_client.py 以外から moomoo を直接importしている: {violations}")
