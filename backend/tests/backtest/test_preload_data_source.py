"""preload_data() の参照先指定（backtest_stable_data_plan.md §3-B）に関するテスト。

- master_files を直接渡すと、探索・ポインタ読み（resolve_backtest_data_source）を
  一切呼ばないこと（シナリオバッチの子プロセス用の経路）。
- data_source="backup"（バックアップ）のときに --refresh-cache を付けると ValueError
  になること（バックアップへは書き込まない。"latest" のときだけ許可される）。
"""
import pytest


@pytest.fixture
def resolve_spy(monkeypatch):
    """resolve_backtest_data_source の呼び出し回数を数える。呼ばれたら失敗させる。"""
    import backend.pipeline.parquet_cache_manager as pcm

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("resolve_backtest_data_source が呼ばれてはいけない（master_files 指定時）")

    monkeypatch.setattr(pcm, "resolve_backtest_data_source", _spy)
    return calls


def test_master_files_bypasses_lookup(resolve_spy):
    """master_files が渡されたら探索・ポインタ読みを一切しない。"""
    from backend.backtest.backtest_runner import preload_data

    # 存在しないファイルを渡し、読み込み自体は失敗させてよい。
    # 見るべきは resolve_backtest_data_source が呼ばれていないことだけ。
    bogus = {
        "symbols": "does_not_exist_symbols.parquet",
        "prices": "does_not_exist_prices.parquet",
        "indicators": "does_not_exist_indicators.parquet",
        "ranks": "does_not_exist_ranks.parquet",
        "tc": "does_not_exist_tc.parquet",
    }

    with pytest.raises(Exception):
        preload_data(None, "2020-01-01", "2020-12-31", master_files=bogus)

    assert resolve_spy == [], "master_files 指定時に探索・ポインタ読みが行われた"


def test_refresh_cache_with_backup_raises_value_error(resolve_spy):
    """data_source='backup' で --refresh-cache は ValueError（バックアップへは書けない）。"""
    from backend.backtest.backtest_runner import preload_data

    with pytest.raises(ValueError, match="latest"):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True, data_source="backup")

    assert resolve_spy == [], "refresh_cache のガードより先に解決処理へ入った"


def test_refresh_cache_with_named_backup_raises_value_error(resolve_spy):
    """名前指定のバックアップでも --refresh-cache は ValueError。"""
    from backend.backtest.backtest_runner import preload_data

    with pytest.raises(ValueError, match="latest"):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True,
                     data_source="_bk_20260925_173413")

    assert resolve_spy == []


def test_default_data_source_is_backup_when_production(monkeypatch):
    """data_source 省略時、本番なら 'backup' として resolve_backtest_data_source に渡す。"""
    import backend.pipeline.parquet_cache_manager as pcm
    import backend.backtest.backtest_runner as br
    from backend.backtest.backtest_runner import preload_data

    monkeypatch.setattr(br, "resolve_backtest_db_path", lambda *a, **k: "dummy_db_path")
    monkeypatch.setattr("paths.is_production", lambda *a, **k: True)

    seen = []

    def fake_resolve(data_source, active_db_path=None):
        seen.append(data_source)
        raise RuntimeError("stop before actual file IO")

    monkeypatch.setattr(pcm, "resolve_backtest_data_source", fake_resolve)

    with pytest.raises(RuntimeError):
        preload_data(None, "2020-01-01", "2020-12-31")

    # preload_data で判定した既定値（本番なら 'backup'）がそのまま resolve に渡る。
    # 判定とガードと解決で同じ値を使う（G3 2周目 R5: refresh_cache の既定 latest が
    # ガードだけに使われ、解決には None が渡って backup を読んでいた）。
    assert seen == ["backup"]


def test_refresh_cache_defaults_to_latest_even_when_production(monkeypatch):
    """本番かつ data_source 省略でも --refresh-cache は latest を既定にする（R2）。

    scenario_runner.py / scenario_comparison_runner.py / verify_db_vs_cache.py は
    --data-source を持たないため、本番で refresh_cache=True にすると常に ValueError
    になり回避不能だった。data_source 未指定なら refresh_cache 優先で latest にする。
    """
    import backend.backtest.backtest_runner as br
    import backend.pipeline.parquet_cache_manager as pcm

    monkeypatch.setattr(br, "resolve_backtest_db_path", lambda *a, **k: "dummy_db_path")
    monkeypatch.setattr("paths.is_production", lambda *a, **k: True)

    calls = []
    monkeypatch.setattr(
        pcm, "rotate_and_archive_to_parquet",
        lambda *a, **k: calls.append("rotate"),
    )
    import contextlib
    import backend.db.database as db_module
    monkeypatch.setattr(db_module, "get_db", lambda *a, **k: contextlib.nullcontext(object()))

    def fake_resolve(ds, active_db_path=None):
        calls.append(("resolve", ds))
        raise RuntimeError("stop before actual file IO")
    monkeypatch.setattr(pcm, "resolve_backtest_data_source", fake_resolve)

    from backend.backtest.backtest_runner import preload_data
    with pytest.raises(RuntimeError):
        # ValueError（refresh_cache のガード）で落ちていないことだけを見る（= 本番でも
        # data_source=None + refresh_cache=True で effective が 'latest' と判定されている）。
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True, data_source=None)

    assert calls == ["rotate", ("resolve", "latest")], (
        "本番かつ data_source 未指定でも refresh_cache のガードで止まった"
    )


def test_default_data_source_is_latest_when_not_production(monkeypatch):
    """data_source 省略時、本番以外（ワークツリー・sandbox）なら --refresh-cache が許可される
    （= effective が 'latest' と判定されている）。"""
    import backend.backtest.backtest_runner as br
    import backend.pipeline.parquet_cache_manager as pcm
    from backend.backtest.backtest_runner import preload_data

    monkeypatch.setattr(br, "resolve_backtest_db_path", lambda *a, **k: "dummy_db_path")
    monkeypatch.setattr("paths.is_production", lambda *a, **k: False)

    calls = []
    monkeypatch.setattr(
        pcm, "rotate_and_archive_to_parquet",
        lambda *a, **k: calls.append("rotate"),
    )
    import contextlib
    import backend.db.database as db_module
    monkeypatch.setattr(db_module, "get_db", lambda *a, **k: contextlib.nullcontext(object()))

    with pytest.raises(Exception):
        # rotate 後の実ファイル読み込みで失敗してよい。ValueError（ガード）で
        # 落ちていないことだけを見る。
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True)

    assert calls == ["rotate"], "既定が 'latest' と判定されず refresh_cache のガードで止まった"
