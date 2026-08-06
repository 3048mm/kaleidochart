"""SEC EDGAR への薄いクライアント。

## なぜ SEC を見るのか

Yahoo は「データが少ない」としか教えてくれない。**改称なのか上場廃止なのかを
区別できない**ため、2026-08-02 に17銘柄の異常を「上流のデータ不具合」と誤診し、
復元・T4 全期間再計算・パージに丸一日を費やした。

SEC EDGAR に当たれば**事実で確定できる**。同じ17銘柄を数分で診断できた。

    tickers      … 現在のティッカー（改称後の新ティッカーが分かる）
    formerNames  … 旧社名と日付範囲
    filings      … Form 15-12G / 25-NSE の提出があれば登録抹消＝上場廃止が確定

## 規約

SEC は以下を求めている（守らないとブロックされうる）。

  - **連絡先入りの User-Agent**（アプリ名 + メールアドレス）
  - **10 req/sec** の上限

連絡先は git 管理外に置く。`config.toml` は git 管理下のため、
メールアドレスを書くとリポジトリにコミットされてしまう。

詳細: `doc/in_progress/sec_ticker_tracking_plan.md`
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# 連絡先を渡す環境変数。これが最優先。
CONTACT_ENV = "STOCKTOOL_SEC_CONTACT"

# git 管理外の設定ファイル（`.gitignore` に登録すること）
LOCAL_CONFIG = "config.local.toml"

# SEC の規約上の上限
RATE_LIMIT_PER_SEC = 10

MAX_RETRIES = 3
TIMEOUT_SEC = 60

_SEC_WWW = "https://www.sec.gov"
_SEC_DATA = "https://data.sec.gov"


class SecNotFound(Exception):
    """HTTP 404。対象が SEC に存在しない。

    レート制限(429)と**明確に区別する**ために専用の例外にしている。
    yfinance が 404 と 429 を同じメッセージに畳んでいたせいで誤診した経験があるため。
    """


def resolve_contact(project_root: str | None = None) -> str:
    """User-Agent に載せる連絡先（メールアドレス）を解決する。

    優先順位:
        1. 環境変数 ``STOCKTOOL_SEC_CONTACT``
        2. ``config.local.toml`` の ``[sec] contact``（**git 管理外**）
        3. 見つからなければ ``ValueError``

    Raises:
        ValueError: 連絡先が見つからない場合。**黙って UA 無しで叩かせない。**
            SEC は連絡先の無いトラフィックをブロックしうるため、
            「動いているように見えて実は弾かれている」状態を作らない。
    """
    env = (os.environ.get(CONTACT_ENV) or "").strip()
    if env:
        return env

    root = project_root or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, LOCAL_CONFIG)
    if os.path.exists(path):
        import tomli
        with open(path, "rb") as f:
            conf = tomli.load(f)
        val = ((conf.get("sec") or {}).get("contact") or "").strip()
        if val:
            return val

    raise ValueError(
        f"SEC への連絡先が未設定です。SEC は連絡先入りの User-Agent を規約で求めています。\n"
        f"  環境変数 {CONTACT_ENV} を設定するか、\n"
        f"  {path} に次を書いてください（git 管理外）:\n"
        f'    [sec]\n    contact = "stocktool <あなたのメールアドレス>"'
    )


# gzip のマジックバイト。Content-Encoding ヘッダが無い応答でも判定できるようにする
GZIP_MAGIC = bytes((0x1F, 0x8B))

def _decode(response) -> str:
    """レスポンス本文を文字列にする。

    `urllib` は gzip を自動解凍しない。`Accept-Encoding: gzip` を送っている以上、
    ここで解凍しないと `UnicodeDecodeError` になる（SEC のマスタは 1〜3MB あり、
    圧縮の効果が大きいのでヘッダは残す）。
    """
    raw = response.read()
    enc = ""
    try:
        enc = (response.headers.get("Content-Encoding") or "").lower()
    except Exception:  # noqa: BLE001 — テストのダミー応答はヘッダを持たない
        pass
    if enc == "gzip" or raw[:2] == GZIP_MAGIC:
        import gzip
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        import zlib
        raw = zlib.decompress(raw)
    return raw.decode("utf-8")


class RateLimiter:
    """直近1秒の呼び出し回数を上限内に収める（トークンバケット相当）。

    上限までは待たないため、週次の十数リクエストは遅くならない。
    テストしやすいよう時計と sleep を注入できる。
    """

    def __init__(self, per_sec: int = RATE_LIMIT_PER_SEC, now=time.monotonic, sleep=time.sleep):
        self.per_sec = per_sec
        self._now = now
        self._sleep = sleep
        self._calls: list[float] = []

    def acquire(self) -> None:
        t = self._now()
        # 1秒より前の呼び出しは枠を解放する
        self._calls = [c for c in self._calls if t - c < 1.0]
        if len(self._calls) >= self.per_sec:
            wait = 1.0 - (t - self._calls[0])
            if wait > 0:
                self._sleep(wait)
                t = self._now()
                self._calls = [c for c in self._calls if t - c < 1.0]
        self._calls.append(self._now())


class SecClient:
    """SEC EDGAR の必要な3エンドポイントだけを扱う。

    マスタ3ファイルはインスタンス内でキャッシュする（週次で1回ずつ取れば足りる）。
    """

    def __init__(self, contact: str | None = None, project_root: str | None = None,
                 limiter: RateLimiter | None = None):
        self.contact = contact or resolve_contact(project_root)
        self.limiter = limiter or RateLimiter()
        self._cache: dict[str, dict] = {}

    # -- 低レベル ----------------------------------------------------------
    def _open(self, url: str, headers=None, timeout=None):
        return urllib.request.urlopen(
            urllib.request.Request(url, headers=headers or {}), timeout=timeout)

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 8))

    def _get_text(self, url: str) -> str:
        headers = {"User-Agent": self.contact, "Accept-Encoding": "gzip, deflate"}
        last = None
        for attempt in range(MAX_RETRIES + 1):
            self.limiter.acquire()
            try:
                with self._open(url, headers=headers, timeout=TIMEOUT_SEC) as r:
                    return _decode(r)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # 「存在しない」は確定情報。リトライしても変わらない
                    raise SecNotFound(url) from e
                last = e
            except Exception as e:  # noqa: BLE001 — ネットワーク系は一律リトライ
                last = e
            if attempt < MAX_RETRIES:
                self._backoff(attempt)
        raise last

    def _get_json(self, url: str) -> dict:
        return json.loads(self._get_text(url))

    # -- 公開 API ----------------------------------------------------------
    def company_tickers(self) -> dict:
        """事業会社の ticker → cik。ETF・ファンドは含まれない。"""
        if "ct" not in self._cache:
            d = self._get_json(f"{_SEC_WWW}/files/company_tickers.json")
            self._cache["ct"] = {
                v["ticker"].upper(): int(v["cik_str"])
                for v in d.values() if v.get("ticker")
            }
        return self._cache["ct"]

    def company_tickers_mf(self) -> dict:
        """ETF・ファンドの symbol → (cik, seriesId, classId)。

        **ETF は `classId` で追跡する。** CIK はトラスト単位で粗すぎるため
        （`RSHO` の CIK 1944285 は Tema ETF Trust の13ファンドを含む）。
        """
        if "mf" not in self._cache:
            d = self._get_json(f"{_SEC_WWW}/files/company_tickers_mf.json")
            fields = d["fields"]
            ci, si = fields.index("cik"), fields.index("symbol")
            sei = fields.index("seriesId") if "seriesId" in fields else None
            cli = fields.index("classId") if "classId" in fields else None
            self._cache["mf"] = {
                r[si].upper(): (int(r[ci]),
                                r[sei] if sei is not None else None,
                                r[cli] if cli is not None else None)
                for r in d["data"] if r[si]
            }
        return self._cache["mf"]

    def lookup_cik_by_ticker(self, ticker: str) -> int | None:
        """ティッカー1件から CIK を引く（**一括マスタの取りこぼし専用**）。

        `company_tickers.json` は全登録企業を網羅していない。実測（2026-08-06）で
        `AEP`（CIK 4904）は submissions API に存在するのに一括マスタ 10,398 件に無い。
        マスタだけに頼ると、この種の銘柄が「キー無し＝追跡対象外」に静かに落ちる。

        1銘柄1リクエストなので**一括マスタで解決できなかった分だけ**に使うこと。

        Returns:
            CIK。未知のティッカー、または複数社がヒットした場合は ``None``。
            誤った CIK を割り当てると以後ずっと他社を追い続けるため、
            曖昧なら未解決のまま人間に回す。
        """
        url = (f"{_SEC_WWW}/cgi-bin/browse-edgar?action=getcompany"
               f"&CIK={urllib.parse.quote(ticker)}&type=10-K&dateb=&owner=include"
               f"&count=1&output=atom")
        try:
            body = self._get_text(url)
        except SecNotFound:
            return None
        ciks = re.findall(r"<cik>(\d+)</cik>", body)
        if len(ciks) != 1:
            return None
        return int(ciks[0])

    def submissions(self, cik: int | str) -> dict:
        """CIK の詳細。`tickers` / `formerNames` / `filings` を含む。

        CIK は**10桁ゼロ埋め**でないと 404 になる。
        """
        cik10 = str(int(cik)).zfill(10)
        return self._get_json(f"{_SEC_DATA}/submissions/CIK{cik10}.json")
