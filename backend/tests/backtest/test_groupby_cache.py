"""get_groupby_cache の同一性検証に関する回帰テスト。

背景（2026-07-29 発見）:
キャッシュキーが `(id(df), len(df), len(df.columns))` だった。`id()` が一意なのは
**生存中のオブジェクト間だけ**で、DataFrame が GC されると CPython は同じアドレスを
次の同サイズ確保に即座に再利用する。強参照を持たないと「解放済み DataFrame の id」と
「新しい DataFrame の id」が一致し、行数・列数も同じなら別データの groupby 結果が
黙って返っていた。

実測: 同形状の DataFrame 3,000 個を生成・破棄したところ、3,000 個すべてが同一キーになった。

顕在化の仕方: `test_backtest_runner_entry.py` の複数テストがフルスイート実行時のみ
間欠的に落ちる（トレード数が合わない）。テスト固有の問題ではなく、DataFrame の
生成・破棄を繰り返す Optuna 最適化で誤ったシグナルが静かに混入しうる。
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from backend.backtest.backtest_runner import (
    clear_groupby_cache,
    get_groupby_cache,
)

DATES = [date(2024, 1, 1) + timedelta(days=i) for i in range(3)]


def _frame(tag: int) -> pd.DataFrame:
    """行数・列数が常に同じ DataFrame（＝キーが衝突しやすい形）"""
    return pd.DataFrame([{"date": d, "symbol_id": 1, "val": tag} for d in DATES])


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_groupby_cache()
    yield
    clear_groupby_cache()


def test_returns_own_data_when_addresses_are_recycled():
    """生成・破棄を繰り返しても、常に自分自身の groupby 結果が返ること。

    修正前はここで最初の DataFrame の結果が返り続けていた。
    """
    for tag in range(300):
        df = _frame(tag)
        grouped = get_groupby_cache(df)
        assert grouped[DATES[0]]["val"].iloc[0] == tag, (
            f"tag={tag} の DataFrame に対して別データの groupby が返った "
            "（id(df) の再利用によるキャッシュ衝突）"
        )
        del df, grouped


def test_same_dataframe_is_cached_not_recomputed():
    """同一オブジェクトに対しては再計算せずキャッシュを返すこと（性能目的の担保）。"""
    df = _frame(1)
    first = get_groupby_cache(df)
    second = get_groupby_cache(df)
    assert first is second


def test_distinct_live_dataframes_do_not_share_entries():
    """同時に生存している同形状の DataFrame 同士が混ざらないこと。"""
    frames = [_frame(t) for t in range(5)]
    grouped = [get_groupby_cache(f) for f in frames]
    for tag, g in enumerate(grouped):
        assert g[DATES[0]]["val"].iloc[0] == tag


def test_col_name_is_part_of_the_key():
    """グループ化列が違えば別エントリになること（旧キーは col_name を含んでいなかった）。"""
    df = _frame(7)
    by_date = get_groupby_cache(df, col_name="date")
    by_symbol = get_groupby_cache(df, col_name="symbol_id")
    assert set(by_date.keys()) == set(DATES)
    assert set(by_symbol.keys()) == {1}


def test_cache_is_bounded():
    """エントリ数が上限を超えても青天井にならないこと（強参照を持つため重要）。"""
    from backend.backtest import backtest_runner

    keep = [_frame(t) for t in range(40)]  # 生存させたまま 40 個投入
    for f in keep:
        get_groupby_cache(f)
    assert len(backtest_runner._GROUPBY_CACHE) <= backtest_runner._GROUPBY_CACHE_MAX
