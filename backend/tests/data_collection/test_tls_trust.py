"""TLS 傍受環境の CA バンドル設定のテスト（data_collection/tls_trust.py）

## 背景

Norton の HTTPS 傍受で `curl_cffi`（yfinance が使う）の証明書検証が失敗し、
**全銘柄が `possibly delisted` になる**。urllib は Windows 証明書ストアを使うので通るため、
「urllib では取れるのに yfinance だけ 0 行」という紛らわしい症状になる。

## なぜ `.bat` ではなく Python なのか

`run_daily_update.bat` に日本語コメント付きで `set CURL_CA_BUNDLE=...` を置いたら、
`chcp 65001` 環境で cmd のパース位置がずれ、コメント断片がコマンドとして実行され
**`set` 行が実行されなかった**（2026-08-07 の日次が取得ゼロのまま「成功」と報告した）。

そもそも `.bat` の env var はその起動経路でしか効かない。スケジューラ・手動実行・
テストで同じ設定を保つには Python 側に置く。**`.bat` にマルチバイト文字を書かないこと。**
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_collection.tls_trust import (  # noqa: E402
    CA_BUNDLE_ENV,
    ensure_ca_bundle,
    resolve_ca_bundle,
)


@pytest.fixture
def cert(tmp_path):
    p = tmp_path / "wscert.pem"
    p.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    return str(p)


def test_sets_a_bundle_that_includes_the_av_cert(cert, monkeypatch, tmp_path):
    """傍受用の証明書を**含んだ**バンドルを指すこと（単体を指すのではない）。"""
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)

    got = ensure_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert got is not None
    assert os.environ[CA_BUNDLE_ENV] == got
    assert got != cert, "傍受用の証明書だけを指すと、傍受されない接続が壊れる"


def test_bundle_is_a_superset_even_without_an_av_cert(monkeypatch, tmp_path):
    """傍受用証明書が無くても certifi + Windows ストアの統合バンドルを使う。

    既定（certifi）の**上位集合**なので上書きしても壊れない。むしろ
    「対話セッションでは傍受・タスクでは非傍受」のように環境が揺れる場合に、
    どちらでも通る唯一の設定になる。
    """
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)

    got = ensure_ca_bundle(project_root=str(tmp_path),
                           candidates=(str(tmp_path / "nope.pem"),))

    assert got is not None
    assert open(got, encoding="utf-8").read().count("-----BEGIN CERTIFICATE-----") > 10


def test_existing_setting_is_respected(cert, monkeypatch, tmp_path):
    """明示指定を上書きしない（利用者が別の CA を指したいことがある）。"""
    monkeypatch.setenv(CA_BUNDLE_ENV, "/my/own/bundle.pem")

    got = ensure_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert got is None
    assert os.environ[CA_BUNDLE_ENV] == "/my/own/bundle.pem"


def test_blank_setting_is_treated_as_unset(cert, monkeypatch, tmp_path):
    monkeypatch.setenv(CA_BUNDLE_ENV, "   ")

    assert ensure_ca_bundle(project_root=str(tmp_path), candidates=(cert,)) is not None


def test_local_config_wins_over_known_paths(cert, monkeypatch, tmp_path):
    """環境差は `config.local.toml`（git 管理外）で吸収する。"""
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)
    other = tmp_path / "corp.pem"
    other.write_text("x", encoding="utf-8")
    (tmp_path / "config.local.toml").write_text(
        f'[tls]\nca_bundle = "{str(other).replace(chr(92), "/")}"\n', encoding="utf-8")

    got = resolve_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert got is not None and got.endswith("corp.pem")


def test_nonexistent_configured_path_falls_back(cert, monkeypatch, tmp_path):
    """**存在しないパスを黙って渡さない。**

    `curl` は存在しない CA バンドルを渡されると**全通信に失敗する**。
    設定ミスで取得が全滅するより、既知のパスへフォールバックする。
    """
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)
    (tmp_path / "config.local.toml").write_text(
        '[tls]\nca_bundle = "/does/not/exist.pem"\n', encoding="utf-8")

    got = resolve_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert got == cert


def test_is_idempotent(cert, monkeypatch, tmp_path):
    """入口ごとに呼ぶので、2度目は何もしないこと。"""
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)

    first = ensure_ca_bundle(project_root=str(tmp_path), candidates=(cert,))
    second = ensure_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert first is not None and second is None


def test_resolve_does_not_mutate_the_environment(cert, monkeypatch, tmp_path):
    """判定と適用を分ける（テスト・診断で環境を汚さないため）。"""
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)

    resolve_ca_bundle(project_root=str(tmp_path), candidates=(cert,))

    assert CA_BUNDLE_ENV not in os.environ


# ---------------------------------------------------------------------------
# 統合バンドルの生成（1枚だけ指定すると別の環境で壊れる）
# ---------------------------------------------------------------------------
class TestCombinedBundle:
    """**`CURL_CA_BUNDLE` は既定のバンドルを「置き換える」。**

    傍受用の証明書だけを渡すと、**傍受されていない接続が今度は検証できなくなる**。
    2026-08-08 にタスクスケジューラ経由で実際に起きた:

        対話セッション   Norton が傍受 → Norton の証明書で通る
        タスクセッション 傍受されない  → Norton の証明書では通らない
                         （SSLKEYLOGFILE / NODE_EXTRA_CA_CERTS が未設定なのが目印）

    `urllib` は両方で通る。Windows 証明書ストアを使うため。
    そこで **certifi + Windows ストア + 傍受用証明書**を1つにまとめて curl に渡す。
    """

    def test_bundle_contains_multiple_certificates(self, tmp_path):
        from data_collection.tls_trust import build_ca_bundle

        dest = str(tmp_path / "bundle.pem")
        got = build_ca_bundle(dest)

        assert got == dest
        body = open(dest, encoding="utf-8").read()
        assert body.count("-----BEGIN CERTIFICATE-----") > 10, "証明書が少なすぎる"

    def test_extra_certificate_is_merged(self, tmp_path, cert):
        """傍受用の証明書が統合バンドルに含まれること。"""
        from data_collection.tls_trust import build_ca_bundle

        extra = tmp_path / "av.pem"
        extra.write_text(
            "-----BEGIN CERTIFICATE-----\nMIIBAgMBAAE=\n-----END CERTIFICATE-----\n",
            encoding="utf-8")
        dest = str(tmp_path / "bundle.pem")

        build_ca_bundle(dest, extra_paths=(str(extra),))

        assert "MIIBAgMBAAE=" in open(dest, encoding="utf-8").read()

    def test_missing_extra_path_is_skipped(self, tmp_path):
        """存在しない証明書パスを渡してもバンドル生成は成功する。"""
        from data_collection.tls_trust import build_ca_bundle

        dest = str(tmp_path / "bundle.pem")

        assert build_ca_bundle(dest, extra_paths=(str(tmp_path / "nope.pem"),)) == dest

    def test_ensure_prefers_the_combined_bundle(self, monkeypatch, tmp_path):
        """既知の証明書が1枚あっても、それ単体ではなく統合バンドルを指すこと。"""
        from data_collection.tls_trust import ensure_ca_bundle

        monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)
        av = tmp_path / "wscert.pem"
        av.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
                      encoding="utf-8")

        got = ensure_ca_bundle(project_root=str(tmp_path), candidates=(str(av),))

        assert got is not None
        assert got != str(av), "傍受用の証明書だけを指している（他の接続が壊れる）"
        assert "_ca_bundle_generated" in got
