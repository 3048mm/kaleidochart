"""SEC EDGAR クライアントのテスト（data_collection/sec_client.py）

## なぜ専用クライアントが要るか

SEC は規約で **10 req/sec の上限**と**連絡先入り User-Agent** を求めている。
無制限に投げる作りだとブロックされうる。

## 連絡先は git 管理外に置く

`config.toml` は git 管理下なので、メールアドレスを書くとリポジトリにコミットされる。
先々の公開可能性を踏まえ、環境変数 → `config.local.toml`（gitignore 済み）の順で解決し、
**見つからなければ起動時エラー**にする。「UA 無しで黙って叩く」を許さない。
"""

import os
import sys
import time

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_collection.sec_client import (  # noqa: E402
    CONTACT_ENV,
    RATE_LIMIT_PER_SEC,
    RateLimiter,
    SecClient,
    SecNotFound,
    resolve_contact,
)


# ---------------------------------------------------------------------------
# 連絡先の解決
# ---------------------------------------------------------------------------
def test_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(CONTACT_ENV, "stocktool env@example.com")
    (tmp_path / "config.local.toml").write_text(
        '[sec]\ncontact = "from file"\n', encoding="utf-8")

    assert resolve_contact(str(tmp_path)) == "stocktool env@example.com"


def test_falls_back_to_config_local(tmp_path, monkeypatch):
    monkeypatch.delenv(CONTACT_ENV, raising=False)
    (tmp_path / "config.local.toml").write_text(
        '[sec]\ncontact = "stocktool file@example.com"\n', encoding="utf-8")

    assert resolve_contact(str(tmp_path)) == "stocktool file@example.com"


def test_raises_when_contact_is_missing(tmp_path, monkeypatch):
    """見つからなければ**起動時エラー**。黙って UA 無しで叩かせない。

    SEC は連絡先の無いトラフィックをブロックしうる。
    「動いているように見えて実は弾かれている」が最も避けたい状態。
    """
    monkeypatch.delenv(CONTACT_ENV, raising=False)

    with pytest.raises(ValueError, match=CONTACT_ENV):
        resolve_contact(str(tmp_path))


def test_blank_contact_is_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(CONTACT_ENV, "   ")
    with pytest.raises(ValueError):
        resolve_contact(str(tmp_path))


# ---------------------------------------------------------------------------
# レート制限
# ---------------------------------------------------------------------------
def test_rate_limiter_allows_burst_up_to_limit():
    """上限までは待たない（週次の十数リクエストが遅くならないように）"""
    clock = [0.0]
    slept = []
    rl = RateLimiter(RATE_LIMIT_PER_SEC, now=lambda: clock[0], sleep=slept.append)

    for _ in range(RATE_LIMIT_PER_SEC):
        rl.acquire()

    assert slept == []


def test_rate_limiter_waits_when_limit_exceeded():
    """上限を超えたら待つ。SEC の規約は 10 req/sec。"""
    clock = [0.0]
    slept = []

    def _sleep(sec):
        slept.append(sec)
        clock[0] += sec

    rl = RateLimiter(RATE_LIMIT_PER_SEC, now=lambda: clock[0], sleep=_sleep)
    for _ in range(RATE_LIMIT_PER_SEC + 1):
        rl.acquire()

    assert len(slept) == 1
    assert 0 < slept[0] <= 1.0


def test_rate_limiter_forgets_old_calls():
    """1秒より前の呼び出しは枠を占有しない"""
    clock = [0.0]
    slept = []
    rl = RateLimiter(RATE_LIMIT_PER_SEC, now=lambda: clock[0], sleep=slept.append)

    for _ in range(RATE_LIMIT_PER_SEC):
        rl.acquire()
    clock[0] += 1.5
    rl.acquire()

    assert slept == []


def test_default_rate_limit_matches_sec_policy():
    assert RATE_LIMIT_PER_SEC == 10


# ---------------------------------------------------------------------------
# SecClient
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(monkeypatch, tmp_path, payloads=None, errors=None):
    monkeypatch.setenv(CONTACT_ENV, "stocktool test@example.com")
    c = SecClient(project_root=str(tmp_path))
    calls = []

    def fake_open(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        if errors:
            err = errors.pop(0)
            if err is not None:
                raise err
        return _FakeResponse((payloads or {}).get("default", {}))

    monkeypatch.setattr(c, "_open", fake_open)
    return c, calls


def test_user_agent_includes_contact(monkeypatch, tmp_path):
    c, calls = _client(monkeypatch, tmp_path)
    c.submissions(320193)

    ua = calls[0]["headers"]["User-Agent"]
    assert "test@example.com" in ua, f"UA に連絡先が入っていない: {ua}"


def test_cik_is_zero_padded_to_ten_digits(monkeypatch, tmp_path):
    """submissions API は10桁ゼロ埋めの CIK を要求する。

    `CIK320193.json` ではなく `CIK0000320193.json`。
    """
    c, calls = _client(monkeypatch, tmp_path)
    c.submissions(320193)

    assert "CIK0000320193.json" in calls[0]["url"]


def test_404_raises_sec_not_found(monkeypatch, tmp_path):
    """404 は「存在しない」。リトライせず即座に区別できる例外にする。

    yfinance が 404 と 429 を同じメッセージに畳んでいたせいで誤診した経験があるため、
    ここでは明確に分ける。
    """
    import urllib.error
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    c, calls = _client(monkeypatch, tmp_path, errors=[err])

    with pytest.raises(SecNotFound):
        c.submissions(999999)
    assert len(calls) == 1, "404 でリトライしている"


def test_429_is_retried(monkeypatch, tmp_path):
    """429 はレート制限。バックオフして再試行する。"""
    import urllib.error
    err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    c, calls = _client(monkeypatch, tmp_path, errors=[err, None])
    monkeypatch.setattr(c, "_backoff", lambda attempt: None)

    c.submissions(320193)

    assert len(calls) == 2, "429 でリトライしていない"


def test_retries_are_bounded(monkeypatch, tmp_path):
    """無限リトライしない（同一エラーで粘らない）"""
    import urllib.error
    errs = [urllib.error.HTTPError("u", 500, "err", {}, None)] * 10
    c, calls = _client(monkeypatch, tmp_path, errors=errs)
    monkeypatch.setattr(c, "_backoff", lambda attempt: None)

    with pytest.raises(urllib.error.HTTPError):
        c.submissions(320193)
    assert len(calls) <= 4, f"リトライしすぎ: {len(calls)}回"


# ---------------------------------------------------------------------------
# lookup_cik_by_ticker — 一括マスタの取りこぼしを埋めるフォールバック
# ---------------------------------------------------------------------------
class _FakeTextResponse:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_ATOM_HIT = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <company-info>
    <cik>0000004904</cik>
    <conformed-name>AMERICAN ELECTRIC POWER CO INC</conformed-name>
  </company-info>
</feed>"""

_ATOM_MISS = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>EDGAR Search Results</title>
</feed>"""


def _text_client(monkeypatch, tmp_path, body):
    monkeypatch.setenv(CONTACT_ENV, "stocktool test@example.com")
    c = SecClient(project_root=str(tmp_path))
    calls = []

    def fake_open(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeTextResponse(body)

    monkeypatch.setattr(c, "_open", fake_open)
    return c, calls


def test_lookup_cik_by_ticker_resolves_symbols_missing_from_master(monkeypatch, tmp_path):
    """`company_tickers.json` は**全登録企業を網羅していない**。

    実測（2026-08-06）: `AEP`（American Electric Power, CIK 4904）は
    submissions API に存在するのに一括マスタ 10,398 件に含まれない。
    マスタだけに頼ると、こうした銘柄が「SEC キー無し＝追跡対象外」に落ちて
    **改称・上場廃止を静かに見逃す**。
    """
    c, calls = _text_client(monkeypatch, tmp_path, _ATOM_HIT)

    assert c.lookup_cik_by_ticker("AEP") == 4904
    assert "CIK=AEP" in calls[0]


def test_lookup_cik_by_ticker_returns_none_when_unknown(monkeypatch, tmp_path):
    """未知のティッカーは 404 ではなく「company-info の無いページ」が返る。

    仮想テーマ（`_DRON_`）・指数（`^VIX`）・OTC 銘柄も同じ形になる。
    例外にせず None を返し、呼び出し側が未解決として扱えるようにする。
    """
    c, _ = _text_client(monkeypatch, tmp_path, _ATOM_MISS)

    assert c.lookup_cik_by_ticker("ZZZZQQ") is None


def test_lookup_cik_by_ticker_rejects_ambiguous_multiple_hits(monkeypatch, tmp_path):
    """複数社がヒットしたら**採用しない**。

    誤った CIK を割り当てると、以後その銘柄は他社のコーポレートアクションを
    追い続けることになる。曖昧なら未解決のまま人間に回す方が安全。
    """
    body = _ATOM_HIT.replace("</company-info>",
                             "</company-info><company-info><cik>0000320193</cik></company-info>")
    c, _ = _text_client(monkeypatch, tmp_path, body)

    assert c.lookup_cik_by_ticker("AMBIG") is None


def test_masters_are_cached_within_one_instance(monkeypatch, tmp_path):
    """マスタ3ファイルは週次で1回ずつ取れば足りる。同一インスタンスで再取得しない。"""
    monkeypatch.setenv(CONTACT_ENV, "stocktool test@example.com")
    c = SecClient(project_root=str(tmp_path))
    calls = []

    def fake_open(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse({"0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple"}})

    monkeypatch.setattr(c, "_open", fake_open)

    c.company_tickers()
    c.company_tickers()

    assert len(calls) == 1, "マスタを毎回取得している"
