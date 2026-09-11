"""`compute_breadth_momentum` の純関数化に関するテスト。

背景（`doc/in_progress/t5_parquet_rebuild_plan.md` §3.1）:
`t5_signals.py` にインラインで書かれていた「個別銘柄の close/sma_50/前日close ->
日付別の breadth_sma50/momentum_ratio」の処理を純関数に切り出す。
SQLite 経路（`t5_signals.py`）と Parquet 経路（`recompute_parquet_signals.py`）が
同じ関数を使うことを保証し、**切り出し前後で出力が1ビットも変わらないこと**を
このテストで固定する。
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from indicators.market_signals import compute_breadth_momentum

DATES = [date(2024, 1, 1) + timedelta(days=i) for i in range(6)]


def _reference_metrics(raw_df: pd.DataFrame) -> pd.DataFrame:
    """切り出し前の `t5_signals.py:71-82` のロジックをそのまま再現した参照実装。

    入力は SQL の `ORDER BY dp.symbol_id, dp.date` で既にソートされている前提
    （切り出し前のコードが依存していた前提と同じ）。
    """
    if raw_df.empty:
        return pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])
    raw_df = raw_df.copy()
    raw_df['prev_close'] = raw_df.groupby('symbol_id')['close'].shift(1)
    raw_df['is_up'] = raw_df['close'] > raw_df['prev_close']
    raw_df['is_above_sma50'] = raw_df['close'] > raw_df['sma_50']
    metrics_df = raw_df.groupby('date').agg(
        breadth_sma50=('is_above_sma50', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5),
        momentum_ratio=('is_up', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5)
    ).reset_index()
    return metrics_df.fillna(0.5)


def _sorted_raw_df():
    """複数銘柄・NaN混じりの sma_50 を含む、SQL の ORDER BY 済み相当の入力。"""
    return pd.DataFrame({
        'symbol_id': [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2],
        'date': DATES + DATES,
        'close': [10.0, 11.0, 9.0, 12.0, 12.0, 13.0,
                  5.0, 5.5, 5.0, 4.5, 4.5, 5.0],
        'sma_50': [10.0, 10.0, 10.0, np.nan, 11.0, 11.0,
                   6.0, 6.0, 6.0, 6.0, 6.0, 6.0],
    })


class TestComputeBreadthMomentumMatchesReferenceImplementation:
    """切り出し前後で出力が変わらないこと（回帰の要）。"""

    def test_matches_reference_on_sorted_multi_symbol_input(self):
        raw_df = _sorted_raw_df()
        got = compute_breadth_momentum(raw_df)
        want = _reference_metrics(raw_df)
        pd.testing.assert_frame_equal(
            got.reset_index(drop=True), want.reset_index(drop=True)
        )

    def test_empty_input_returns_empty_frame_with_expected_columns(self):
        raw_df = pd.DataFrame(columns=['symbol_id', 'date', 'close', 'sma_50'])
        got = compute_breadth_momentum(raw_df)
        assert list(got.columns) == ['date', 'breadth_sma50', 'momentum_ratio']
        assert got.empty

    def test_single_symbol_first_date_has_no_prev_close(self):
        """初日は前日closeが無く is_up=False（NaNではない）になる既存挙動を保つ。"""
        raw_df = pd.DataFrame({
            'symbol_id': [1, 1, 1],
            'date': DATES[:3],
            'close': [10.0, 11.0, 9.0],
            'sma_50': [10.0, 10.0, 10.0],
        })
        got = compute_breadth_momentum(raw_df)
        want = _reference_metrics(raw_df)
        pd.testing.assert_frame_equal(
            got.reset_index(drop=True), want.reset_index(drop=True)
        )
        # 初日の momentum_ratio は 0.0（NaN比較は False であり 0.5 フォールバックに落ちない）
        assert got.loc[got['date'] == DATES[0], 'momentum_ratio'].iloc[0] == 0.0


class TestComputeBreadthMomentumIsOrderIndependent:
    """Parquet 経路は SQL の ORDER BY を経由しないため、未ソート入力でも
    ソート済み入力と同じ結果になることを保証する（純関数側で並べ替える）。"""

    def test_shuffled_input_matches_sorted_input(self):
        sorted_df = _sorted_raw_df()
        shuffled = sorted_df.sample(frac=1.0, random_state=42).reset_index(drop=True)

        got_sorted = compute_breadth_momentum(sorted_df)
        got_shuffled = compute_breadth_momentum(shuffled)

        pd.testing.assert_frame_equal(
            got_sorted.reset_index(drop=True), got_shuffled.reset_index(drop=True)
        )

    def test_does_not_mutate_caller_dataframe(self):
        raw_df = _sorted_raw_df()
        before = raw_df.copy()
        compute_breadth_momentum(raw_df)
        pd.testing.assert_frame_equal(raw_df, before)
