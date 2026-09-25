"""preload_data() の参照先指定（backtest_stable_data_plan.md §3-B）に関するテスト。

- master_files を直接渡すと、探索・ポインタ読み（resolve_backtest_data_source）を
  一切呼ばないこと（シナリオバッチの子プロセス用の経路）。
- data_source="backup"（既定）のときに --refresh-cache を付けると ValueError
  になること（バックアップへは書き込まない）。
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
    """既定（data_source='backup'）で --refresh-cache は ValueError（バックアップへは書けない）。"""
    from backend.backtest.backtest_runner import preload_data

    with pytest.raises(ValueError, match="production"):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True)

    assert resolve_spy == [], "refresh_cache のガードより先に解決処理へ入った"


def test_refresh_cache_with_named_backup_raises_value_error(resolve_spy):
    """名前指定のバックアップでも --refresh-cache は ValueError。"""
    from backend.backtest.backtest_runner import preload_data

    with pytest.raises(ValueError, match="production"):
        preload_data(None, "2020-01-01", "2020-12-31", refresh_cache=True,
                     data_source="_bk_20260925_173413")

    assert resolve_spy == []
