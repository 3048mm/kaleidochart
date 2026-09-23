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
    # 5-21: 全銘柄が判定不能の日の breadth_sma50 は 0.5 ではなく NaN のまま残す
    # （§2.3: 判定できなければ NaN。旧版はここを 0.5 に捏造し、参照実装も同じ挙動を
    # 固定していたため新仕様（NaN）に書き換えた）。fillna(0.5) は momentum_ratio のみ。
    metrics_df = raw_df.groupby('date').agg(
        breadth_sma50=('is_above_sma50', lambda x: x.mean(skipna=True) if not x.isna().all() else np.nan),
        momentum_ratio=('is_up', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5)
    ).reset_index()
    metrics_df['momentum_ratio'] = metrics_df['momentum_ratio'].fillna(0.5)
    return metrics_df


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


class TestAllNanSma50DayYieldsNanBreadth:
    """全銘柄の sma_50 が NaN の日は breadth_sma50 が NaN のまま流れること（5-21・R2）。

    旧実装はこの日の breadth を 0.5（偽の中立値）にして返したため、
    `has_breadth = breadth_sma50.notna()` が True になり、2017年Q1 のような
    「全銘柄の sma_50 が算出できない期間」で偽 breadth を含む4成分 MTS が作られた。
    """

    @staticmethod
    def _two_day_raw_df():
        """DATES[0] は全銘柄 sma_50=NaN、DATES[1] は全銘柄 sma_50 が算出済み。"""
        return pd.DataFrame({
            'symbol_id': [1, 1, 2, 2],
            'date': [DATES[0], DATES[1], DATES[0], DATES[1]],
            'close': [10.0, 11.0, 5.0, 4.0],
            'sma_50': [np.nan, 10.0, np.nan, 6.0],
        })

    def test_breadth_is_nan_when_all_sma50_nan(self):
        got = compute_breadth_momentum(self._two_day_raw_df())
        day0 = got.loc[got['date'] == DATES[0]].iloc[0]
        day1 = got.loc[got['date'] == DATES[1]].iloc[0]
        assert pd.isna(day0['breadth_sma50'])
        # 判定できる日は従来どおり（銘柄1のみ上回り 1/2）
        assert abs(day1['breadth_sma50'] - 0.5) < 1e-9

    def test_object_dtype_all_null_sma50_does_not_raise(self):
        """SQLite の pd.read_sql は全行 NULL の列を object 型（None）で返す。
        その入力でも例外にならず、breadth が NaN になること。"""
        raw_df = pd.DataFrame({
            'symbol_id': [1, 1],
            'date': [DATES[0], DATES[1]],
            'close': [10.0, 11.0],
            'sma_50': pd.Series([None, None], dtype=object),
        })
        got = compute_breadth_momentum(raw_df)
        assert got['breadth_sma50'].isna().all()

    def test_momentum_ratio_is_not_nan(self):
        """momentum_ratio は bool 由来で NaN にならない。fillna(0.5) は momentum 側にのみ効く。"""
        got = compute_breadth_momentum(self._two_day_raw_df())
        assert got['momentum_ratio'].notna().all()

    def test_mts_uses_three_components_on_all_nan_day(self):
        """`calculate_market_signals` で has_breadth=False（NaN）→ 3成分スコアになる。"""
        from indicators.market_signals import calculate_market_signals, SPY_LOOKBACK_MIN_BARS

        n = SPY_LOOKBACK_MIN_BARS + 30
        start = pd.Timestamp('2020-01-01')
        spy_dates = [start + pd.Timedelta(days=i) for i in range(n)]
        closes = [100.0 + i * 0.1 for i in range(n)]
        spy_df = pd.DataFrame({
            'date': spy_dates,
            'close': closes,
            'high': [c + 0.5 for c in closes],
            'low': [c - 0.5 for c in closes],
            'volume': [1_000_000 + (i % 3) * 1000 for i in range(n)],
        })
        df_vix = pd.DataFrame({'date': spy_dates, 'close': 15.0})
        df_vxv = pd.DataFrame({'date': spy_dates, 'close': 16.5})

        d_nan, d_ok = spy_dates[-2], spy_dates[-1]
        raw_df = pd.DataFrame({
            'symbol_id': [1, 1, 2, 2],
            'date': [d_nan, d_ok, d_nan, d_ok],
            'close': [10.0, 11.0, 5.0, 6.5],
            'sma_50': [np.nan, 10.0, np.nan, 6.0],
        })
        metrics_df = compute_breadth_momentum(raw_df)

        res = calculate_market_signals(spy_df, df_vix, df_vxv, metrics_df)
        res_none = calculate_market_signals(spy_df, df_vix, df_vxv, None)

        row_nan = res[res['date'] == d_nan].iloc[0]
        row_ok = res[res['date'] == d_ok].iloc[0]
        none_nan = res_none[res_none['date'] == d_nan].iloc[0]

        # calculate_market_signals は breadth_sma50 列を返さないため、入力側で NaN を確認する
        assert pd.isna(metrics_df.loc[metrics_df['date'] == d_nan, 'breadth_sma50'].iloc[0])
        # breadth が判定不能の日は 3成分スコア（breadth 無しの計算と一致）
        assert abs(row_nan['market_trend_score'] - none_nan['market_trend_score']) < 1e-9
        # breadth が算出できる日は 4成分（3成分と一致しない）
        none_ok = res_none[res_none['date'] == d_ok].iloc[0]
        assert not np.isclose(row_ok['market_trend_score'], none_ok['market_trend_score'])
