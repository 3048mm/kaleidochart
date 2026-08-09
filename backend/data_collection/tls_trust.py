"""TLS 傍受環境で `curl_cffi`（yfinance が使う）が信頼する CA を設定する。

## なぜ必要か

Norton などのセキュリティ製品が HTTPS を傍受していると、`curl_cffi` は独自の
CA バンドルを見るため傍受用のルート証明書を信頼できず、**全銘柄が失敗する**。

    curl_cffi.requests.exceptions.CertificateVerifyError:
      curl: (60) SSL certificate problem: unable to get local issuer certificate

yfinance はこれを 404 や 429 と同じ `possibly delisted; no price data found` に
畳んでしまうため、**上流障害と区別がつかない**（`upstream-data-diagnosis` §2.1）。
Python 標準の `ssl`（urllib）は Windows 証明書ストアを使うので通る。この非対称性が
「urllib では取れるのに yfinance だけ 0 行」という紛らわしい症状になる。

## なぜ `.bat` ではなく Python でやるのか

当初 `run_daily_update.bat` に `set CURL_CA_BUNDLE=...` を置いたが失敗した。
日本語コメントを入れたところ、`chcp 65001` 環境で cmd のパース位置がずれ、
**コメントの断片がコマンドとして実行され `set` 行が実行されなかった**
（2026-08-07 の日次が取得ゼロのまま「成功」と報告した）。

    '..検証が失敗し、全銘柄が' is not recognized as an internal or external command

そもそも `.bat` の env var は**その起動経路でしか効かない**。スケジューラ・手動実行・
テスト・バックテストで同じ設定を保つには Python 側に置くのが素直で、
cmd のパースの機微に依存しなくなる。

> [!NOTE]
> `.bat` にマルチバイト文字を書かないこと（コメントも含む）。
> 経緯は `doc/db_recovery_procedure.md` と `.claude/skills/upstream-data-diagnosis/SKILL.md` §2.1。

## 設定の優先順位

    1. 既に設定済みの `CURL_CA_BUNDLE` … 尊重して何もしない
    2. `config.local.toml` の `[tls] ca_bundle`（git 管理外・環境差を吸収）
    3. 既知のセキュリティ製品の証明書パス（存在するものだけ）
    4. 何も見つからなければ何もしない（傍受していない環境では不要）
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CA_BUNDLE_ENV = "CURL_CA_BUNDLE"
LOCAL_CONFIG = "config.local.toml"

# HTTPS を傍受するセキュリティ製品が置くルート証明書。存在するものだけ使う。
KNOWN_AV_CA_BUNDLES = (
    r"C:\ProgramData\Norton\Antivirus\wscert.pem",
    r"C:\ProgramData\Norton\wscert.pem",
)

# 生成した統合バンドルの置き場所（実行のたびに作り直す。git 管理外）
GENERATED_BUNDLE = os.path.join("data", "_ca_bundle_generated.pem")

_PEM_HEAD = "-----BEGIN CERTIFICATE-----"

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _from_local_config(project_root: str) -> str | None:
    path = os.path.join(project_root, LOCAL_CONFIG)
    if not os.path.exists(path):
        return None
    try:
        import tomli
        with open(path, "rb") as f:
            conf = tomli.load(f)
        return ((conf.get("tls") or {}).get("ca_bundle") or "").strip() or None
    except Exception as e:  # noqa: BLE001 — 設定ミスで取得処理ごと落とさない
        logger.warning(f"{LOCAL_CONFIG} の [tls] ca_bundle を読めませんでした: {e}")
        return None


def resolve_ca_bundle(project_root: str | None = None,
                      candidates: tuple[str, ...] = KNOWN_AV_CA_BUNDLES,
                      env: dict | None = None) -> str | None:
    """設定すべき CA バンドルのパスを決める。**環境変数は書き換えない。**

    Returns:
        設定すべきパス。既に設定済み、または見つからなければ ``None``。
    """
    env = os.environ if env is None else env
    if (env.get(CA_BUNDLE_ENV) or "").strip():
        return None                      # 明示指定を尊重する

    configured = _from_local_config(project_root or _project_root)
    if configured:
        # 設定ミスに黙って従わない（存在しないパスを渡すと curl は全通信に失敗する）
        if os.path.exists(configured):
            return configured
        logger.warning(f"{LOCAL_CONFIG} の [tls] ca_bundle が存在しません: {configured}")

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def collect_windows_root_certs() -> list[str]:
    """Windows 証明書ストアの ROOT を PEM 文字列として取り出す。

    **`urllib` が通るのに `curl_cffi` が通らない**のは、前者が Windows 証明書ストアを
    使い、後者が独自バンドル（certifi）を見るため。傍受用のルート証明書は
    セキュリティ製品が Windows ストアに入れるので、そこを取り込めば両者が揃う。

    Windows 以外・取得失敗時は空リスト（呼び出し側でフォールバックする）。
    """
    try:
        import ssl
        out = []
        for store in ("ROOT", "CA"):
            try:
                for der, _enc, _trust in ssl.enum_certificates(store):
                    out.append(ssl.DER_cert_to_PEM_cert(der))
            except Exception:  # noqa: BLE001 — 片方のストアが無くても続ける
                continue
        return out
    except Exception as e:  # noqa: BLE001 — Windows 以外では単に使わない
        logger.debug(f"Windows 証明書ストアを取得できませんでした: {e}")
        return []


def build_ca_bundle(dest_path: str, extra_paths: tuple[str, ...] = ()) -> str | None:
    """certifi + Windows 証明書ストア + 指定ファイルを1つの PEM にまとめる。

    **1枚だけ指定してはいけない。** `CURL_CA_BUNDLE` は既定のバンドルを**置き換える**ため、
    傍受用の証明書だけを渡すと、傍受されていない接続が今度は検証できなくなる。
    2026-08-08 にタスクスケジューラ経由で実際に起きた（対話セッションでは Norton が
    傍受するので Norton の証明書で通るが、タスク側の環境では傍受されず失敗した）。

    Returns:
        書き出したパス。証明書を1枚も集められなければ ``None``。
    """
    pems: list[str] = []

    try:
        import certifi
        with open(certifi.where(), encoding="utf-8") as f:
            pems.append(f.read())
    except Exception as e:  # noqa: BLE001 — certifi が無くても続行
        logger.debug(f"certifi を読めませんでした: {e}")

    pems.extend(collect_windows_root_certs())

    for p in extra_paths:
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    pems.append(f.read())
            except Exception as e:  # noqa: BLE001
                logger.debug(f"{p} を読めませんでした: {e}")

    body = "\n".join(t.strip() for t in pems if _PEM_HEAD in t)
    if not body:
        return None

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body + "\n")
    return dest_path


def ensure_ca_bundle(project_root: str | None = None,
                     candidates: tuple[str, ...] = KNOWN_AV_CA_BUNDLES) -> str | None:
    """必要なら `CURL_CA_BUNDLE` を設定する。冪等。

    yfinance を使う経路の入口で呼ぶこと（`fetcher` の import 時と `run_pipeline` の冒頭）。

    `config.local.toml` で明示指定があればそれを尊重し、無ければ
    **certifi + Windows 証明書ストア + 既知の傍受用証明書**を統合したバンドルを生成する。

    Returns:
        設定したパス。何もしなければ ``None``。
    """
    root = project_root or _project_root
    explicit = resolve_ca_bundle(root, candidates)
    if (os.environ.get(CA_BUNDLE_ENV) or "").strip():
        return None                       # 明示指定を尊重する

    dest = os.path.join(root, GENERATED_BUNDLE)
    built = build_ca_bundle(dest, extra_paths=tuple(p for p in (explicit,) if p))
    if built:
        os.environ[CA_BUNDLE_ENV] = built
        logger.info(f"{CA_BUNDLE_ENV} を設定しました（certifi + Windows ストア統合）: {built}")
        return built

    if explicit:
        os.environ[CA_BUNDLE_ENV] = explicit
        logger.info(f"{CA_BUNDLE_ENV} を設定しました: {explicit}")
    return explicit
