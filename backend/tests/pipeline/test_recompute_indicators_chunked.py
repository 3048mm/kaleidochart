"""全銘柄・全期間の T3 再計算をチャンク処理で行う部分のテスト。

## なぜチャンク処理が要るのか

`recompute_indicators()` は銘柄ごとの結果を `out` リストに溜めて最後に
`pd.concat` する。**全銘柄(約600万行 × 66列)で呼ぶとメモリを一度に確保できず落ちる。**

2026-09-01 に rotate の `pd.concat` が同じ規模で失敗した実績がある:

    Unable to allocate 1.77 GiB for an array with shape (39, 6082624)
    and data type float64

空きメモリは 28.7GB あったので総量不足ではなく**連続領域の断片化**。
つまり「メモリを増やせば直る」類ではないため、そもそも一度に確保しない形にする。

## このモジュールが守るべき性質

1. チャンクに割っても、割らずに計算した結果と**完全に一致**する
   （銘柄ごとの計算は独立なので、分割は結果に影響してはいけない）
2. SPY は RS の基準として**どのチャンクにも渡される**
   （渡し忘れると、そのチャンクの銘柄だけ RS 列が NULL になる）
3. 出力列は既存 Parquet の列順・型に揃う（後段の concat で dtype が壊れないため）
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

from pipeline.parquet_recompute import recompute_indicators  # noqa: E402


SPY_ID = 1


def _prices(symbol_ids, n_days=320, seed=0):
    """銘柄ごとに少しずつ違う価格系列を作る。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days).date
    rows = []
    for k, sid in enumerate(symbol_ids):
        base = 100.0 + k * 7
        walk = np.cumsum(rng.normal(0.05, 1.0, n_days))
        close = base + walk
        rows.append(pd.DataFrame({
            "symbol_id": sid,
            "date": dates,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": rng.integers(1_000_00, 5_000_00, n_days).astype(float),
        }))
    return pd.concat(rows, ignore_index=True)


@pytest.fixture
def sample():
    ids = [SPY_ID, 2, 3, 4, 5, 6, 7]
    px = _prices(ids)
    from indicators.calculate import calculate_indicators
    probe = calculate_indicators(
        px[px["symbol_id"] == 2][["date", "open", "high", "low", "close", "volume"]]
        .reset_index(drop=True),
        px[px["symbol_id"] == SPY_ID][["date", "close", "volume"]].reset_index(drop=True),
    )
    ind_cols = [c for c in probe.columns if c != "date"]
    return ids, px, ind_cols


class TestChunkedRecompute:
    def test_chunked_matches_unchunked(self, sample):
        """チャンクに割っても割らなくても結果が一致すること。

        銘柄ごとの計算は独立なので、分割の仕方で値が変わってはいけない。
        """
        from pipeline.parquet_recompute_chunked import recompute_indicators_chunked

        ids, px, ind_cols = sample
        targets = [i for i in ids if i != SPY_ID]

        ref = recompute_indicators(targets, px, ind_cols, SPY_ID)
        got = pd.concat(
            list(recompute_indicators_chunked(targets, px, ind_cols, SPY_ID, chunk_size=2)),
            ignore_index=True,
        )

        ref = ref.sort_values(["symbol_id", "date"]).reset_index(drop=True)
        got = got.sort_values(["symbol_id", "date"]).reset_index(drop=True)

        assert len(got) == len(ref), "行数が一致しない"
        assert list(got.columns) == list(ref.columns), "列構成が一致しない"
        for c in ref.columns:
            a, b = ref[c], got[c]
            if pd.api.types.is_numeric_dtype(a):
                assert np.allclose(a.astype(float), b.astype(float),
                                   rtol=0, atol=0, equal_nan=True), f"{c} が一致しない"
            else:
                assert a.equals(b), f"{c} が一致しない"

    def test_spy_is_supplied_to_every_chunk(self, sample):
        """SPY を渡し忘れると RS 列が NULL になる。全チャンクで RS が計算されること。"""
        from pipeline.parquet_recompute_chunked import recompute_indicators_chunked

        ids, px, ind_cols = sample
        targets = [i for i in ids if i != SPY_ID]

        for chunk in recompute_indicators_chunked(targets, px, ind_cols, SPY_ID, chunk_size=1):
            assert chunk["rs_value"].notna().any(), (
                f"symbol_id={chunk['symbol_id'].iloc[0]} の RS が全て NULL。"
                " SPY がそのチャンクに渡されていない"
            )

    def test_chunk_size_larger_than_universe(self, sample):
        """チャンクサイズが銘柄数より大きくても壊れないこと。"""
        from pipeline.parquet_recompute_chunked import recompute_indicators_chunked

        ids, px, ind_cols = sample
        targets = [i for i in ids if i != SPY_ID]
        chunks = list(recompute_indicators_chunked(targets, px, ind_cols, SPY_ID, chunk_size=999))
        assert len(chunks) == 1
        assert set(chunks[0]["symbol_id"]) == set(targets)

    def test_empty_targets(self, sample):
        from pipeline.parquet_recompute_chunked import recompute_indicators_chunked

        ids, px, ind_cols = sample
        assert list(recompute_indicators_chunked([], px, ind_cols, SPY_ID, chunk_size=2)) == []

    def test_spy_itself_can_be_a_target(self, sample):
        """SPY 自身も再計算対象になりうる（自分との RS は NULL が正常）。"""
        from pipeline.parquet_recompute_chunked import recompute_indicators_chunked

        ids, px, ind_cols = sample
        chunks = list(recompute_indicators_chunked([SPY_ID], px, ind_cols, SPY_ID, chunk_size=2))
        assert len(chunks) == 1
        assert (chunks[0]["symbol_id"] == SPY_ID).all()
        assert chunks[0]["rs_value"].isna().all(), "SPY の自己相対強度は NULL が正常"
