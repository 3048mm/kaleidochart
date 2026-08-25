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
from datetime import datetime
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
    recompute_indicators,
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
    （詳細: doc/completed/parquet_recompute_plan.md §7）。
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


# ---------------------------------------------------------------------------
# recompute_indicators
#
# 価格を修正・継ぎ足した銘柄は T3 を作り直さないと指標が古い価格のまま残る。
# 旧世代の T3 をコピーするのは不可（指標列が 50 → 63 に増えており、
# 流用すると新しい列が欠損したままスクリーナーとバックテストに入る）。
# ---------------------------------------------------------------------------
def _price_frame(n_days: int = 260) -> pd.DataFrame:
    """SPY(id=1) と対象銘柄(id=2) の OHLCV を作る"""
    dates = pd.bdate_range("2023-01-02", periods=n_days).strftime("%Y-%m-%d")
    rows = []
    for sid, base in [(1, 400.0), (2, 50.0)]:
        for i, d in enumerate(dates):
            c = base * (1 + 0.001 * i)
            rows.append({"symbol_id": sid, "date": d, "open": c * 0.99,
                         "high": c * 1.01, "low": c * 0.98, "close": c,
                         "volume": 1_000_000 + i})
    return pd.DataFrame(rows)


def test_recomputes_only_requested_symbols():
    px = _price_frame()
    out = recompute_indicators([2], px, ["ema_21", "atr_14"], spy_id=1)

    assert set(out["symbol_id"]) == {2}, "指定外の銘柄まで計算している"
    assert len(out) == len(px[px.symbol_id == 2]), "全期間ぶん出ていない"


def test_output_columns_follow_the_given_order():
    """既存 indicators の列順に合わせられること。

    列順がズレると Parquet へ書き戻すときに値が入れ替わる。
    """
    px = _price_frame()
    cols = ["atr_14", "ema_21", "sma_50"]
    out = recompute_indicators([2], px, cols, spy_id=1)

    assert list(out.columns) == ["symbol_id", "date"] + cols


def test_unknown_column_becomes_null_not_error():
    """存在しない指標列を要求されても落ちない（NULL 列になる）。

    指標を追加・削除した直後でもスクリプトが動くようにするため。
    """
    px = _price_frame()
    out = recompute_indicators([2], px, ["ema_21", "no_such_indicator"], spy_id=1)

    assert "no_such_indicator" in out.columns
    assert out["no_such_indicator"].isna().all()


def test_spy_itself_gets_null_relative_strength():
    """SPY 自身は自分との相対強度を計算しない（NULL が正常）。

    ここで SPY を他銘柄と同じ扱いにすると rs_value が全て同じ値になり、
    順位計算まで汚染される。
    """
    px = _price_frame()
    out = recompute_indicators([1], px, ["rs_value", "ema_21"], spy_id=1)

    assert out["rs_value"].isna().all(), "SPY に相対強度が入っている"
    assert out["ema_21"].notna().any(), "SPY の通常指標まで NULL になっている"


def test_missing_symbol_is_skipped():
    """価格が無い銘柄は黙って飛ばす（空の DataFrame を返さない）"""
    px = _price_frame()
    out = recompute_indicators([2, 999], px, ["ema_21"], spy_id=1)

    assert set(out["symbol_id"]) == {2}


def test_empty_input_returns_empty_frame():
    px = _price_frame()
    out = recompute_indicators([], px, ["ema_21"], spy_id=1)
    assert out.empty



# ---------------------------------------------------------------------------
# rebuild_virtual_index_prices — 仮想テーマ指数の Parquet 版再合成
#
# ## なぜ必要か
#
# 仮想テーマ指数の合成は `pipeline/orchestrator.build_virtual_index_prices` にしか無く、
# SQLAlchemy セッションを要求する。構成銘柄の価格を Parquet 上で補正したときに
# **Parquet 経路から再合成できない**（SQLite は直近730日しか持たないので SQLite 経由では
# 過去に届かない）。同じ式の Parquet 版が要る。
#
# 実例: BYND の 1:30 併合が未調整だったため、所属する仮想テーマ3本
# （`_CNSM0A_` `_GRCL29_` `_NTRTFC_`）の指数が 2026-08-13 に ×3.44〜×5.87 で飛んだ。
# ---------------------------------------------------------------------------
def _virtual_price_frame():
    """構成銘柄2本 × 6営業日。ORM 版と Parquet 版の両方に同じ値を流す。"""
    rows = []
    closes = {
        10: [100.0, 101.0, 99.0, 103.0, 102.0, 105.0],
        11: [50.0, 51.5, 51.0, 50.0, 52.0, 53.0],
    }
    vols = {
        10: [1000, 1200, 800, 3000, 900, 1100],
        11: [2000, 2100, 1900, 2500, 2200, 2000],
    }
    dates = ["2026-05-01", "2026-05-04", "2026-05-05",
             "2026-05-06", "2026-05-07", "2026-05-08"]
    for sid in (10, 11):
        for d, c, v in zip(dates, closes[sid], vols[sid]):
            rows.append({"symbol_id": sid, "date": d, "open": c, "high": c,
                         "low": c, "close": c, "volume": float(v)})
    return pd.DataFrame(rows)


def _tc_frame():
    return pd.DataFrame([{"theme_id": 101, "symbol_id": 10},
                         {"theme_id": 101, "symbol_id": 11}])


def test_rebuild_virtual_index_matches_the_orm_implementation():
    """`orchestrator.build_virtual_index_prices` と**同じ値**を返すこと。

    アルゴリズムを書き換えるのではなく DB 依存を外すだけの移植なので、
    数値が一致しなければ移植に失敗している。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.models import Base, DailyPrice, Symbol, ThemeConstituent
    from pipeline.orchestrator import build_virtual_index_prices
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    px = _virtual_price_frame()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Symbol(id=10, ticker="AAA", exchange="NASDAQ", category="個別", active=1),
        Symbol(id=11, ticker="BBB", exchange="NASDAQ", category="個別", active=1),
        Symbol(id=101, ticker="_THEME_", exchange="VIRTUAL", category="テーマ",
               theme_type="virtual", active=1),
        ThemeConstituent(theme_id=101, symbol_id=10),
        ThemeConstituent(theme_id=101, symbol_id=11),
    ])
    # ORM の Date 列は `datetime.date` しか受け付けない。Parquet 側は文字列で持つので
    # ここで型を変換して同じ値を両方に流す。
    for _, r in px.iterrows():
        session.add(DailyPrice(symbol_id=int(r["symbol_id"]),
                               date=datetime.strptime(r["date"], "%Y-%m-%d").date(),
                               open=r["open"], high=r["high"], low=r["low"],
                               close=r["close"], volume=r["volume"]))
    session.commit()

    expected = build_virtual_index_prices(session, 101).sort_values("date").reset_index(drop=True)
    actual = rebuild_virtual_index_prices([101], px, _tc_frame())
    actual = actual.sort_values("date").reset_index(drop=True)

    assert list(actual["date"]) == [d.strftime("%Y-%m-%d") for d in expected["date"]]
    for col in ("close", "open", "high", "low", "volume"):
        pd.testing.assert_series_equal(
            actual[col].astype(float), expected[col].astype(float),
            check_names=False, rtol=1e-9,
            obj=f"{col} が ORM 版と一致しない",
        )


def test_index_ohlc_are_all_equal_to_close():
    """合成指数に日中値は無いので OHLC はすべて close と同値。"""
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    out = rebuild_virtual_index_prices([101], _virtual_price_frame(), _tc_frame())
    for col in ("open", "high", "low"):
        assert (out[col] == out["close"]).all(), f"{col} が close と違う"


def test_first_date_has_no_index_row():
    """初日はリターンが計算できないので指数に載らない（ORM 版と同じ挙動）。"""
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    out = rebuild_virtual_index_prices([101], _virtual_price_frame(), _tc_frame())
    assert out["date"].min() == "2026-05-04", "初日が指数に含まれている"


def test_scaling_a_constituent_uniformly_does_not_move_the_index():
    """構成銘柄の価格を**全期間**一律スケールしても指数は動かない。

    指数はリターンの連鎖なのでスケール不変。この性質が成り立たないなら
    合成式が価格の絶対水準を拾ってしまっている。
    """
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    px = _virtual_price_frame()
    base = rebuild_virtual_index_prices([101], px, _tc_frame())

    scaled = px.copy()
    m = scaled["symbol_id"] == 10
    for col in ("open", "high", "low", "close"):
        scaled.loc[m, col] *= 30.0
    scaled.loc[m, "volume"] /= 30.0
    after = rebuild_virtual_index_prices([101], scaled, _tc_frame())

    pd.testing.assert_frame_equal(
        base.reset_index(drop=True), after.reset_index(drop=True), rtol=1e-9)


def test_backadjusting_only_pre_split_rows_changes_only_the_seam():
    """**本件の核心**: 併合前だけを ×30 すると、変わるのは接合日の1本だけ。

    未調整のまま（＝いまの本番データ）は接合日に +2900% のリターンが入り、
    そこから先の指数水準が丸ごとずれる。補正後は接合日のリターンが正常化し、
    **接合日より前の指数は完全に一致する**。
    """
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    seam = "2026-05-06"
    broken = _virtual_price_frame()
    m = (broken["symbol_id"] == 10) & (broken["date"] < seam)
    for col in ("open", "high", "low", "close"):
        broken.loc[m, col] /= 30.0          # 併合前が未調整（低いスケール）の状態
    broken.loc[m, "volume"] *= 30.0

    fixed = _virtual_price_frame()          # 全期間が単一スケール＝補正後の姿

    idx_broken = rebuild_virtual_index_prices([101], broken, _tc_frame()).set_index("date")
    idx_fixed = rebuild_virtual_index_prices([101], fixed, _tc_frame()).set_index("date")

    before = idx_broken.index[idx_broken.index < seam]
    pd.testing.assert_series_equal(
        idx_broken.loc[before, "close"], idx_fixed.loc[before, "close"], rtol=1e-9,
        obj="接合日より前の指数まで変わっている")

    assert idx_broken.loc[seam, "close"] > idx_fixed.loc[seam, "close"] * 5, \
        "未調整データで接合日が跳ねていない（テストの前提が壊れている）"


def test_multiple_themes_are_rebuilt_in_one_call():
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    tc = pd.DataFrame([{"theme_id": 101, "symbol_id": 10},
                       {"theme_id": 101, "symbol_id": 11},
                       {"theme_id": 102, "symbol_id": 11}])
    out = rebuild_virtual_index_prices([101, 102], _virtual_price_frame(), tc)
    assert set(out["symbol_id"]) == {101, 102}


def test_theme_without_constituents_is_skipped():
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    out = rebuild_virtual_index_prices([999], _virtual_price_frame(), _tc_frame())
    assert out.empty


def test_non_positive_closes_are_excluded():
    """close が 0 / 負 / NULL の行は合成に使わない（ORM 版の `p.close > 0` と同じ）。"""
    from pipeline.parquet_recompute import rebuild_virtual_index_prices

    px = _virtual_price_frame()
    px.loc[(px["symbol_id"] == 11) & (px["date"] == "2026-05-05"), "close"] = 0.0
    out = rebuild_virtual_index_prices([101], px, _tc_frame())

    only10 = rebuild_virtual_index_prices(
        [101], _virtual_price_frame(),
        pd.DataFrame([{"theme_id": 101, "symbol_id": 10}]))
    # 05-05 は BBB が落ちるので AAA 単独のリターンになる
    assert out.set_index("date").loc["2026-05-05", "close"] != \
        pytest.approx(0.0), "0 の close が混入して指数が壊れている"
    assert len(out) == len(only10)


def test_recomputed_indicators_are_numeric_not_object():
    """指標列は数値 dtype で返すこと。**object のままだとメモリが数倍に膨れる。**

    `calculate_indicators` は素の Python float を object 列に入れて返すため、
    そのまま concat すると既存の float64 列まで object に巻き上げられる。
    実測（2026-08-25 / Sandbox）: 6,057,722行 × 63列の indicators で
    28列が object 化し、`sort_values` のコピーで OOM した。

    ```
    numpy.core._exceptions._ArrayMemoryError: Unable to allocate 1.26 GiB
    for an array with shape (28, 6057722) and data type object
    ```

    ディスク上は pyarrow が double に落として書くため Parquet は汚染されないが、
    **メモリ上でだけ膨らむ**ので気付きにくい。
    """
    px = _price_frame()
    out = recompute_indicators([2], px, ["ema_21", "sma_5", "rs_value"], spy_id=1)

    objs = [c for c in out.columns if out[c].dtype == object and c != "date"]
    assert not objs, f"object 列が残っている: {objs}"
    assert out["date"].dtype == object, "date は文字列のままにする"


def test_recomputed_indicators_keep_all_null_columns_numeric():
    """全 NULL の列（SPY の RS 系など）も object にしない。"""
    px = _price_frame()
    out = recompute_indicators([1], px, ["rs_value", "ema_21"], spy_id=1)
    assert out["rs_value"].isna().all()
    assert out["rs_value"].dtype != object, "全 NULL 列が object になっている"
