"""`compute_breadth_momentum` の純関数化、および NaN sma_50 の除外に関するテスト。

背景（`doc/in_progress/t5_parquet_rebuild_plan.md` §3.1）:
`t5_signals.py` にインラインで書かれていた「個別銘柄の close/sma_50/前日close ->
日付別の breadth_sma50/momentum_ratio」の処理を純関数に切り出す。
SQLite 経路（`t5_signals.py`）と Parquet 経路（`recompute_parquet_signals.py`）が
同じ関数を使うことを保証する。

さらに `doc/in_progress/min_periods_warmup_plan.md` チェックリスト 5-8:
`is_above_sma50 = close > sma_50` という bool 比較は、sma_50 が NaN
（遡り不足で正しく「判定不能」になった銘柄）でも False になってしまい、
「sma50を上回っていない」と区別できない。これにより
`mean(skipna=True)` が判定不能銘柄を分母から正しく除外できず、breadth が
不当に下振れする。このテストは、sma_50 が NaN の銘柄を breadth の分母から
正しく除外することを固定する（`_reference_metrics` は修正後の正しいロジックを
再現しており、`compute_breadth_momentum` 側の修正が完了するまでは
テストの一部が red になる）。
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from indicators.market_signals import compute_breadth_momentum

DATES = [date(2024, 1, 1) + timedelta(days=i) for i in range(6)]


def _reference_metrics(raw_df: pd.DataFrame) -> pd.DataFrame:
    """sma_50 が NaN の銘柄を breadth の分母から正しく除外する参照実装。

    `close > sma_50` の bool 比較は sma_50 が NaN でも False になり、
    「判定不能」と「sma50を上回っていない」を区別できない。ここでは
    sma_50 が NaN の行を明示的に NaN にしてから `mean(skipna=True)` に渡すことで、
    判定不能銘柄を正しく分母から除外する（修正後の `compute_breadth_momentum` が
    採るべきロジックと同じ）。

    入力は SQL の `ORDER BY dp.symbol_id, dp.date` で既にソートされている前提。
    """
    if raw_df.empty:
        return pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])
    raw_df = raw_df.copy()
    raw_df['prev_close'] = raw_df.groupby('symbol_id')['close'].shift(1)
    raw_df['is_up'] = raw_df['close'] > raw_df['prev_close']
    raw_df['is_above_sma50'] = np.where(
        raw_df['sma_50'].isna(), np.nan,
        raw_df['close'] > raw_df['sma_50']
    )
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
    """`compute_breadth_momentum` が正しい参照実装（NaN sma_50 を除外するロジック）と
    一致すること。

    注: `_sorted_raw_df()` は symbol_id=1 の DATES[3] のみ sma_50=NaN だが、
    同じ日の symbol_id=2 が `close < sma_50`（False）のため、新旧どちらの
    ロジックでもその日の breadth が偶然 0.0 で一致してしまい、この既存
    フィクスチャだけでは NaN 除外の修正効果が観測できない
    （観測できるケースは `TestNaNSma50ExcludedFromBreadthDenominator` を参照）。
    """

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


class TestNaNSma50ExcludedFromBreadthDenominator:
    """sma_50 が NaN（判定不能）の銘柄が breadth_sma50 の分母から除外されること。

    計画書 `doc/in_progress/min_periods_warmup_plan.md` 5-8:
    `close > sma_50` の bool 比較は NaN でも False になるため、判定不能銘柄が
    「sma50を上回っていない」と誤って断定され、breadth が下振れする。
    """

    def test_nan_sma50_symbol_is_excluded_not_counted_as_below(self):
        """3銘柄中1銘柄が sma_50=NaN（判定不能）、残り2銘柄は close>sma_50（True）。

        - 旧ロジック（NaNをFalse扱い）: mean([False, True, True]) = 2/3 ≈ 0.667
        - 新ロジック（NaNを除外）    : mean(skipna=True で [True, True])  = 2/2 = 1.0
        """
        raw_df = pd.DataFrame({
            'symbol_id': [1, 2, 3],
            'date': [DATES[0], DATES[0], DATES[0]],
            'close': [10.0, 10.0, 12.0],
            'sma_50': [np.nan, 8.0, 9.0],
        })
        got = compute_breadth_momentum(raw_df)
        breadth = got.loc[got['date'] == DATES[0], 'breadth_sma50'].iloc[0]
        # 新ロジックでは判定不能銘柄(symbol_id=1)を分母から除外し 1.0 になるはず
        # （旧ロジックのままなら 0.667 になり、このアサーションは red になる）
        assert abs(breadth - 1.0) < 1e-9

    def test_reference_matches_current_implementation_after_fix(self):
        """上記と同じフィクスチャで `_reference_metrics`（正しいロジック）と
        `compute_breadth_momentum` が一致すること（プロダクションコード修正後に green）。
        """
        raw_df = pd.DataFrame({
            'symbol_id': [1, 2, 3],
            'date': [DATES[0], DATES[0], DATES[0]],
            'close': [10.0, 10.0, 12.0],
            'sma_50': [np.nan, 8.0, 9.0],
        })
        got = compute_breadth_momentum(raw_df)
        want = _reference_metrics(raw_df)
        pd.testing.assert_frame_equal(
            got.reset_index(drop=True), want.reset_index(drop=True)
        )
