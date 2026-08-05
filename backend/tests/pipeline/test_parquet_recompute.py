"""Parquet 上での再計算部品のテスト（pipeline/parquet_recompute.py）

## なぜ Parquet 上で再計算するのか

T4 は **SQLite の `indicators` に存在する日付しか計算できない**。SQLite は730日分しか
持たないため、`--rebuild-from T4` を回しても直近730日の順位しか作られない
（2026-08-05 に改称6件で実際に発生。5時間実行して T4=500 行しか埋まらなかった）。

Parquet 上で直接再計算すれば全期間を約4分で作れる。

## 最重要: SQL の PERCENT_RANK を厳密に再現すること

    PERCENT_RANK() OVER(PARTITION BY s.category ORDER BY col ASC)
      = (rank - 1) / (n - 1)     rank は同値で最小順位を共有

**pandas の `rank(pct=True)` では一致しない**（`rank / n` になる）。
また **SQLite は NULL を最小値として扱う**ため、NaN を除外してはいけない。
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline.parquet_recompute import (  # noqa: E402
    RANK_EXCLUDED_CATEGORIES,
    find_affected_virtual_themes,
    percent_rank,
    recompute_ranks,
)


# ---------------------------------------------------------------------------
# percent_rank — SQL 準拠
# ---------------------------------------------------------------------------
def test_matches_sql_percent_rank_formula():
    """(rank - 1) / (n - 1)。最小値が 0、最大値が 1 になる。"""
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    r = percent_rank(s)
    assert list(r) == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_ties_share_the_minimum_rank():
    """同値は最小順位を共有する（SQL の PERCENT_RANK と同じ）。

    pandas の既定 `method='average'` では一致しないため `method='min'` が必須。
    """
    s = pd.Series([10.0, 20.0, 20.0, 40.0, 50.0])
    r = percent_rank(s)
    assert list(r) == [0.0, 0.25, 0.25, 0.75, 1.0]


def test_null_is_treated_as_smallest():
    """SQLite は NULL を最小値として扱う。除外してはいけない。

    ここを `rank()` の既定（NaN を NaN のまま返す）にすると、
    NULL を持つ銘柄の順位が欠落し、母集団サイズも変わって全体がずれる。

    実測で二重に確認済み（2026-08-05）:
        SQLite の合成テスト  VALUES (NULL),(10),(20),(30) → NULL の percent_rank = 0.0
        本番データ           AIPO の rs_ratio_e5=NULL → ランク 0.0

    なお **Parquet の 2024-08 以前には NULL を 1.0 としている行が 1,348 行ある**。
    これは旧コードによる誤りで、本実装での再計算により修正される
    （詳細: doc/in_progress/parquet_recompute_plan.md §7）。
    """
    s = pd.Series([np.nan, 10.0, 20.0, 30.0, 40.0])
    r = percent_rank(s)
    assert r.iloc[0] == 0.0, "NULL が最小として扱われていない"
    assert list(r) == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_all_null_gives_zero():
    """全部 NULL なら全員が同順位＝0（SQL でも同じ）"""
    s = pd.Series([np.nan] * 4)
    assert list(percent_rank(s)) == [0.0, 0.0, 0.0, 0.0]


def test_single_element_is_zero():
    """n=1 のとき (n-1) がゼロ除算になる。SQL の PERCENT_RANK は 0 を返す。"""
    assert list(percent_rank(pd.Series([42.0]))) == [0.0]


def test_pandas_pct_true_would_be_wrong():
    """`rank(pct=True)` を使ってはいけないことを明示的に固定する。

    実装を「簡潔に」書き換えたくなったときの歯止め。
    """
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    naive = s.rank(pct=True)          # = rank / n → [0.2, 0.4, 0.6, 0.8, 1.0]
    assert not np.allclose(naive, percent_rank(s)), "pct=True と一致してしまっている"


# ---------------------------------------------------------------------------
# recompute_ranks
# ---------------------------------------------------------------------------
def _frames():
    symbols = pd.DataFrame([
        {"id": 1, "ticker": "A", "category": "個別",     "active": 1},
        {"id": 2, "ticker": "B", "category": "個別",     "active": 1},
        {"id": 3, "ticker": "C", "category": "個別",     "active": 1},
        {"id": 4, "ticker": "T", "category": "テーマ",   "active": 1},
        {"id": 5, "ticker": "L", "category": "レバレッジ", "active": 1},
        {"id": 6, "ticker": "X", "category": "指標",     "active": 1},
        {"id": 7, "ticker": "D", "category": "個別",     "active": 0},   # 退役
    ])
    rows = []
    for sid, val in [(1, 1.0), (2, 2.0), (3, 3.0), (4, 9.0), (5, 5.0), (6, 6.0), (7, 99.0)]:
        for d in ["2020-01-02", "2020-01-03"]:
            rows.append({"symbol_id": sid, "date": d, "rs_value": val,
                         "rs_ratio_e21": val * 2})
    return symbols, pd.DataFrame(rows)


def test_excludes_leverage_and_index_categories():
    """レバレッジ / 指標 は T4 の対象外（t4_ranks.py の NOT IN と同じ）"""
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols, cols=[("rs_value", "rs_value_rank")])

    assert set(RANK_EXCLUDED_CATEGORIES) == {"レバレッジ", "指標"}
    assert 5 not in set(out["symbol_id"]), "レバレッジが含まれている"
    assert 6 not in set(out["symbol_id"]), "指標が含まれている"


def test_excludes_inactive_symbols():
    """active=0 は母集団から外す。

    Parquet は行削除が伝播しないため退役銘柄の古い順位が残るが、
    再計算では現在の active を使う（SQLite の T4 と同じ慣習）。
    """
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols, cols=[("rs_value", "rs_value_rank")])
    assert 7 not in set(out["symbol_id"])


def test_ranks_are_computed_within_category_and_date():
    """パーティションは (date, category)。カテゴリを跨いで順位を付けない。"""
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols, cols=[("rs_value", "rs_value_rank")])

    d = out[out["date"] == "2020-01-02"].set_index("symbol_id")
    # 個別は A(1.0) < B(2.0) < C(3.0) の3件 → 0, 0.5, 1.0
    assert d.loc[1, "rs_value_rank"] == 0.0
    assert d.loc[2, "rs_value_rank"] == 0.5
    assert d.loc[3, "rs_value_rank"] == 1.0
    # テーマは1件のみ → 0.0（カテゴリを跨いで比較されていない証拠）
    assert d.loc[4, "rs_value_rank"] == 0.0


def test_group_name_is_the_category():
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols, cols=[("rs_value", "rs_value_rank")])
    assert set(out[out.symbol_id.isin([1, 2, 3])]["group_name"]) == {"個別"}
    assert set(out[out.symbol_id == 4]["group_name"]) == {"テーマ"}


def test_min_allowed_date_is_respected():
    """T4 は min_allowed_date（既定 2018-04-01）より前を計算しない。

    T2 は index_start_date(2010) から入るが T4 は 2018 からというのが仕様。
    """
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols, cols=[("rs_value", "rs_value_rank")],
                          min_allowed_date="2020-01-03")
    assert set(out["date"]) == {"2020-01-03"}


def test_multiple_columns_are_all_ranked():
    symbols, ind = _frames()
    out = recompute_ranks(ind, symbols,
                          cols=[("rs_value", "rs_value_rank"),
                                ("rs_ratio_e21", "rs_ratio_rank_e21")])
    assert {"rs_value_rank", "rs_ratio_rank_e21"} <= set(out.columns)
    d = out[out["date"] == "2020-01-02"].set_index("symbol_id")
    assert d.loc[1, "rs_ratio_rank_e21"] == 0.0
    assert d.loc[3, "rs_ratio_rank_e21"] == 1.0


# ---------------------------------------------------------------------------
# find_affected_virtual_themes
# ---------------------------------------------------------------------------
def test_finds_themes_containing_the_symbol():
    """補正した銘柄が所属する仮想テーマを逆引きする。

    仮想テーマは構成銘柄のリターンを連鎖させて算出するため、
    構成銘柄の価格を直したら所属テーマも再合成しないと誤った値が残る。
    """
    symbols = pd.DataFrame([
        {"id": 10, "ticker": "_THEME_A_", "category": "テーマ", "active": 1},
        {"id": 11, "ticker": "_THEME_B_", "category": "テーマ", "active": 1},
        {"id": 12, "ticker": "REALETF",   "category": "テーマ", "active": 1},
    ])
    tc = pd.DataFrame([
        {"theme_id": 10, "symbol_id": 1},
        {"theme_id": 11, "symbol_id": 1},
        {"theme_id": 11, "symbol_id": 2},
        {"theme_id": 12, "symbol_id": 1},   # 実在 ETF は再合成の対象外
    ])
    assert find_affected_virtual_themes([1], tc, symbols) == [10, 11]
    assert find_affected_virtual_themes([2], tc, symbols) == [11]
    assert find_affected_virtual_themes([999], tc, symbols) == []
