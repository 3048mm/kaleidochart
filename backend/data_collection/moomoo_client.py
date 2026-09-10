"""moomoo API への唯一の入り口（薄いラッパー）。

## なぜ要るか

**本体コードから `moomoo` パッケージを直接 import しない。** 理由:

1. **レート制限**: moomoo の quote 系インターフェースは 60req/30秒が上限
   （エンドポイント別かはドキュメントから確認できなかったため、全呼び出し
   合算で保守的に数える）。呼び出し口を1つに絞ればハンドリングを1箇所に
   集約でき、超過を防げる。
2. **接続時の無限リトライを避ける**: `moomoo.OpenQuoteContext(...)` は OpenD が
   起動していないと**明確なタイムアウトなくリトライし続ける**
   （2026-09-11 実測: bash の `timeout 20` で強制終了しても3回目のリトライ中）。
   ここで先に短いタイムアウトのポート疎通確認を行い、繋がらなければ即座に
   `MoomooNotConnectedError` にする。
3. **使用量の可視化**: `usage_status()` でレート制限枠とヒストリカルK線
   クォータの残量を呼び出し側から確認できるようにする。

セットアップ手順・API仕様・実測結果は `.claude/skills/moomoo-api/SKILL.md` 参照。

## 制約

**このモジュール以外から `moomoo` を import しない。**
`backend/tests/data_collection/test_moomoo_client.py::test_no_direct_moomoo_import_outside_client`
が違反を機械的に検出する。
"""

from __future__ import annotations

import socket
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111

# moomoo の quote インターフェースのレート制限。エンドポイント別かは未確認のため、
# 全呼び出し合算で保守的に数える（.claude/skills/moomoo-api/SKILL.md §6）。
RATE_LIMIT_CALLS = 60
RATE_LIMIT_WINDOW_SEC = 30.0

US_MARKET_PREFIX = "US."


class MoomooError(Exception):
    """moomoo 連携の基底例外。"""


class MoomooNotConnectedError(MoomooError):
    """OpenD ゲートウェイに接続できない。

    `OpenQuoteContext` 自体は接続失敗時に明確なタイムアウトなくリトライし
    続けるため（実測）、そこに辿り着く前にここで打ち切る。
    """


class MoomooApiError(MoomooError):
    """moomoo API が成功コード以外を返した。"""

    def __init__(self, method: str, code: str, message: str):
        self.method = method
        self.code = code
        self.message = message
        super().__init__(f"{method}({code}) failed: {message}")


def port_is_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """OpenD が待ち受けているかを軽量に確認する（実接続を試みる前のガード）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class SlidingWindowRateLimiter:
    """直近 window_sec 秒の呼び出し回数を上限内に収める（トークンバケット相当）。

    `data_collection/sec_client.py` の `RateLimiter` と同じ発想
    （時計とsleepを注入してテストしやすくする）だが、window幅が違う
    （moomoo=30秒 / SEC=1秒）ため、このモジュール内に閉じて持つ。
    """

    def __init__(self, limit: int = RATE_LIMIT_CALLS,
                 window_sec: float = RATE_LIMIT_WINDOW_SEC,
                 now=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.window_sec = window_sec
        self._now = now
        self._sleep = sleep
        self._calls: list[float] = []

    def acquire(self) -> None:
        t = self._now()
        self._calls = [c for c in self._calls if t - c < self.window_sec]
        if len(self._calls) >= self.limit:
            wait = self.window_sec - (t - self._calls[0])
            if wait > 0:
                self._sleep(wait)
                t = self._now()
                self._calls = [c for c in self._calls if t - c < self.window_sec]
        self._calls.append(self._now())

    def calls_in_window(self) -> int:
        t = self._now()
        return len([c for c in self._calls if t - c < self.window_sec])


def _default_context_factory(host: str, port: int):
    import moomoo  # 唯一の import 箇所
    return moomoo.OpenQuoteContext(host=host, port=port)


class MoomooClient:
    """moomoo API への唯一の入り口。本体コードはこのクラス経由でのみ呼び出す。

    テストでは `context_factory` / `port_check` を差し替えて、実際の
    OpenD・ネットワークに触れずに単体検証する
    （`backend/tests/data_collection/test_moomoo_client.py` の `FakeQuoteContext`）。
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *,
                 rate_limiter: SlidingWindowRateLimiter | None = None,
                 connect_timeout: float = 3.0,
                 context_factory=None,
                 port_check=None):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter()
        self._context_factory = context_factory or _default_context_factory
        self._port_check = port_check or port_is_open
        self._ctx = None

    def _ensure_connected(self):
        if self._ctx is not None:
            return self._ctx
        if not self._port_check(self.host, self.port, self.connect_timeout):
            raise MoomooNotConnectedError(
                f"OpenD ({self.host}:{self.port}) に接続できません。"
                f" OpenD が起動してログイン済みか確認してください。")
        self._ctx = self._context_factory(self.host, self.port)
        return self._ctx

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    @staticmethod
    def _to_code(ticker: str) -> str:
        """米国株のみサポート。`AAPL` → `US.AAPL`。

        本プロジェクトの対象は米国株のみ（`doc/architecture.md`）。多市場対応は
        現時点で要件が無いため、`.`混入ティッカーの誤判定を避けるためにも
        常に `US.` を付ける単純な実装にする（YAGNI。必要になったら市場を
        明示引数で受け取る形に変える）。
        """
        return f"{US_MARKET_PREFIX}{ticker}"

    def get_rehab(self, ticker: str):
        """分割・配当記録を取得する。フィールド意味は SKILL.md §5 参照。

        ヒストリカルK線クォータは消費しない（2026-09-11実測）。
        """
        ctx = self._ensure_connected()
        self.rate_limiter.acquire()
        ret, data = ctx.get_rehab(self._to_code(ticker))
        if ret != 0:
            raise MoomooApiError("get_rehab", str(ret), str(data))
        return data

    def request_history_kline(self, ticker: str, start: str | None = None,
                               end: str | None = None, ktype: str = "K_DAY",
                               autype: str = "qfq", max_count: int = 1000):
        """ヒストリカルK線を取得する（ページングは内部で吸収する）。

        **ヒストリカルK線クォータを消費する**（`get_rehab` は消費しない）。
        事前に `usage_status()` で残量を確認すること。
        """
        ctx = self._ensure_connected()
        code = self._to_code(ticker)
        frames = []
        page_req_key = None
        while True:
            self.rate_limiter.acquire()
            ret, data, page_req_key = ctx.request_history_kline(
                code, start=start, end=end, ktype=ktype, autype=autype,
                max_count=max_count, page_req_key=page_req_key)
            if ret != 0:
                raise MoomooApiError("request_history_kline", str(ret), str(data))
            frames.append(data)
            if not page_req_key:
                break
        if len(frames) == 1:
            return frames[0]
        import pandas as pd
        return pd.concat(frames, ignore_index=True)

    def usage_status(self) -> dict:
        """レート制限枠とヒストリカルK線クォータの残量を返す。

        呼び出し側が「あとどれだけ叩けるか」を都度確認できるようにするための窓口。
        **接続できなくても例外にしない**（気軽に確認できることを優先する。
        実データ取得の `get_rehab`/`request_history_kline` は従来どおり fail-loud）。
        """
        status = {
            "connected": False,
            "rate_limit": {
                "calls_in_window": self.rate_limiter.calls_in_window(),
                "limit": self.rate_limiter.limit,
                "window_sec": self.rate_limiter.window_sec,
            },
            "history_kl_quota": None,
            "history_kl_quota_error": None,
        }
        try:
            ctx = self._ensure_connected()
        except MoomooNotConnectedError:
            return status
        status["connected"] = True
        self.rate_limiter.acquire()
        ret, data = ctx.get_history_kl_quota(get_detail=False)
        if ret == 0:
            used, remain, _detail = data
            status["history_kl_quota"] = {"used": used, "remain": remain}
        else:
            # 接続はできているがクォータ照会自体が失敗したケース。`history_kl_quota: None`
            # のままだと「未接続」や「正常に0件」と見分けがつかなくなるため、
            # エラー内容を別フィールドで残す（フェイルラウド。黙って揉み消さない）。
            status["history_kl_quota_error"] = str(data)
        return status
