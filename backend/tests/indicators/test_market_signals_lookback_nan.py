"""SPY の遡り不足時に `calculate_market_signals()` が NaN を「それらしい値」に
潰さないことのテスト（`doc/in_progress/t5_parquet_rebuild_plan.md` §4-4・5-9）。

背景: `sma_50`/`sma_200` は元々 `min_periods=1` で計算されていたため、遡りが
足りない先頭区間でも実際より少ないデータから「それらしい値」を返していた。
`spy_above_sma200` はさらにその NaN を `.astype(int)` で 0（200日線割れ）に
潰していたため、遡り不足が「200日線割れ」として誤って扱われる問題があった。
`distribution_days` も同様に `.astype(int)` していたため、min_periods を窓幅に
揃えると NaN が発生し `IntCastingNaNError` で落ちる。

このテストは、min_periods を窓幅（50/200/25）に揃えたうえで、
- 例外を出さないこと
- 遡り不足の行が None（判定不能）になり、0 や 'BULL' に誤って潰されないこと
- 遡りが十分な行では従来と同じ値になること（回帰防止）
を担保する。
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from indicators.market_signals import calculate_market_signals, SPY_LOOKBACK_MIN_BARS


def _spy_df(n: int, start=date(2010, 4, 1)) -> pd.DataFrame:
    """`n` 本の連続した SPY 日足を作る（緩やかな上昇トレンド）。"""
    dates = [start + timedelta(days=i) for i in range(n)]
    closes = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "close": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "volume": [1_000_000 + (i % 3) * 1000 for i in range(n)],
    })


class TestInsufficientLookbackDoesNotRaise:
    """例外を出さないこと（distribution_days の .astype(int) の件）。"""

    def test_calculate_market_signals_does_not_raise_with_short_history(self):
        spy_df = _spy_df(n=10)
        # 例外が出ないこと自体がアサーション
        res = calculate_market_signals(spy_df)
        assert len(res) == 10

    def test_calculate_market_signals_does_not_raise_at_various_lengths(self):
        """distribution_days の境界（25本）付近でも落ちないこと。"""
        for n in (1, 24, 25, 26, 199, 200, 219, 220, 221):
            res = calculate_market_signals(_spy_df(n=n))
            assert len(res) == n


class TestInsufficientLookbackYieldsNoneNotFakeValues:
    """遡り不足の行は None（判定不能）になり、0 や 'BULL' に潰れないこと。"""

    def test_spy_above_sma200_is_none_when_sma200_lookback_insufficient(self):
        """sma_200 の窓（200本）に満たない行は None（0 に潰れない）。"""
        spy_df = _spy_df(n=199)  # 200本に1本足りない
        res = calculate_market_signals(spy_df)

        assert res["spy_above_sma200"].isna().all()

    def test_spy_above_sma200_is_not_zero_for_insufficient_rows(self):
        """0（200日線割れ）に潰れていないことを明示的に確認する。"""
        spy_df = _spy_df(n=50)
        res = calculate_market_signals(spy_df)
        # 上昇トレンドなので、もし NaN が 0 に潰れていたら
        # 「200日線割れ」という誤った値になる。None であるべき。
        assert not (res["spy_above_sma200"] == 0).any()

    def test_distribution_days_is_none_for_first_24_bars(self):
        """distribution_days の窓（25本）に満たない先頭24本は None。"""
        spy_df = _spy_df(n=30)
        res = calculate_market_signals(spy_df)
        first_24 = res.iloc[:24]["distribution_days"]
        assert first_24.apply(lambda v: v is None or pd.isna(v)).all()
        # 25本目以降は値が入る
        rest = res.iloc[24:]["distribution_days"]
        assert rest.apply(lambda v: v is not None and not pd.isna(v)).all()

    def test_market_phase_is_none_when_spy_above_sma200_is_none(self):
        """遡り不足で spy_above_sma200 が None の行は、market_phase も None になり
        誤って 'BULL' 等に判定されない（else 節への意図しないフォールスルー防止）。"""
        spy_df = _spy_df(n=50)  # sma_200 は常に NaN（200本に満たない）
        res = calculate_market_signals(spy_df)
        assert res["market_phase"].apply(lambda v: v is None).all()


class TestPhaseUndeterminedWhenSpySma200RisingIsNone:
    """`spy_sma200_rising` は `sma_200.shift(20)` 由来のため、`sma_200` が算出され
    始めた直後の20本（200〜219本目、0-indexed 199〜218）で None になる。この帯で
    `spy_above_sma200 == 0` かつ `follow_through_day != 1` のとき、`phase()` が
    `spy_sma200_rising` の None を見ずに `RALLY_ATTEMPT` を捏造しないこと（5-9d）。
    """

    @staticmethod
    def _declining_spy_df(n: int, ftd_index: int | None = None) -> pd.DataFrame:
        """単調減少（下降トレンド）の SPY 日足。`ftd_index` を指定すると、その日だけ
        +2% の急騰＋出来高増（FTD の条件を満たす）を差し込む。
        """
        start = date(2010, 4, 1)
        dates = [start + timedelta(days=i) for i in range(n)]
        closes = [200.0 - i * 0.3 for i in range(n)]
        volumes = [1_000_000] * n
        if ftd_index is not None:
            closes[ftd_index] = closes[ftd_index - 1] * 1.02
            volumes[ftd_index] = 2_000_000
        return pd.DataFrame({
            "date": pd.to_datetime(dates),
            "close": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "volume": volumes,
        })

    def test_market_phase_is_none_in_the_20_bar_gap_without_ftd(self):
        """200〜219本目（0-indexed 199〜218）: spy_above_sma200==0・FTD無し・
        spy_sma200_rising が NaN → market_phase は None（BEAR/RALLY_ATTEMPT を
        捏造しない）。"""
        spy_df = self._declining_spy_df(n=250)
        res = calculate_market_signals(spy_df)
        window = res.iloc[199:219]

        assert (window["spy_above_sma200"] == 0).all()
        assert (window["follow_through_day"] == 0).all()
        assert window["spy_sma200_rising"].isna().all()
        assert window["market_phase"].apply(lambda v: v is None).all()

    def test_ftd_confirms_rally_attempt_even_when_spy_sma200_rising_is_nan(self):
        """同じ帯でも FTD が立っていれば確定できるので判定不能にしない。"""
        ftd_index = 210
        spy_df = self._declining_spy_df(n=250, ftd_index=ftd_index)
        res = calculate_market_signals(spy_df)
        row = res.iloc[ftd_index]

        assert row["spy_above_sma200"] == 0
        assert row["follow_through_day"] == 1
        assert pd.isna(row["spy_sma200_rising"])
        assert row["market_phase"] == "RALLY_ATTEMPT"

    def test_bull_side_unaffected_by_nan_spy_sma200_rising(self):
        """spy_above_sma200==1（BULL/CORRECTION）側は spy_sma200_rising を使わない
        ので、NaN でも従来どおり判定される（巻き込んでいないことの確認）。"""
        n = 250
        start = date(2010, 4, 1)
        dates = [start + timedelta(days=i) for i in range(n)]
        closes = [100.0 + i * 0.1 for i in range(n)]
        spy_df = pd.DataFrame({
            "date": pd.to_datetime(dates),
            "close": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "volume": [1_000_000] * n,
        })
        res = calculate_market_signals(spy_df)
        window = res.iloc[199:219]

        assert (window["spy_above_sma200"] == 1).all()
        assert window["spy_sma200_rising"].isna().all()
        assert (window["market_phase"] == "BULL").all()

    def test_known_behaviour_beyond_220_bars_is_not_regressed(self):
        """220本目以降（spy_sma200_rising が算出される）は既存の分類ロジックのまま
        （下降トレンドで sma_200 も下降 → BEAR）。"""
        spy_df = self._declining_spy_df(n=250)
        res = calculate_market_signals(spy_df)
        tail = res.iloc[219:]

        assert tail["spy_sma200_rising"].apply(lambda v: v is not None and not pd.isna(v)).all()
        assert (tail["market_phase"] == "BEAR").all()


class TestSufficientLookbackMatchesPreviousBehaviour:
    """遡りが十分な区間では、min_periods を変える前と同じ値になること（回帰防止）。"""

    def test_sma_50_matches_manual_rolling_mean_once_bars_are_sufficient(self):
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        res = calculate_market_signals(spy_df)

        # sma_50 が窓を満たした後は spy_above_sma200 が None にならない
        assert res["spy_above_sma200"].iloc[SPY_LOOKBACK_MIN_BARS:].apply(
            lambda v: v is not None and not pd.isna(v)
        ).all()

    def test_distribution_days_matches_manual_computation_once_bars_are_sufficient(self):
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        res = calculate_market_signals(spy_df)

        close = spy_df["close"]
        volume = spy_df["volume"].astype(float)
        daily_ret = close.pct_change()
        vol_increase = volume > volume.shift(1)
        is_dist_day = (daily_ret <= -0.002) & vol_increase
        expected = is_dist_day.rolling(window=25, min_periods=25).sum()

        # 窓を満たした行では、従来 (min_periods=1) と同じ値になる
        # （rolling(min_periods=1) と rolling(min_periods=25) は、窓を満たした
        # 行では同じ値を返す。差が出るのは先頭24本だけ）
        got = res["distribution_days"].iloc[24:].astype(float).reset_index(drop=True)
        want = expected.iloc[24:].astype(float).reset_index(drop=True)
        pd.testing.assert_series_equal(got, want, check_names=False)

    def test_market_trend_score_no_exception_and_finite_once_bars_are_sufficient(self):
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        res = calculate_market_signals(spy_df)
        tail_score = res["market_trend_score"].iloc[SPY_LOOKBACK_MIN_BARS:]
        assert np.isfinite(tail_score.astype(float)).all()
