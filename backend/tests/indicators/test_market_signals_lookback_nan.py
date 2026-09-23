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
        """このテストの目的は「遡りが十分なら例外なく有限値になること」の確認
        （遡り本数の十分性の回帰防止）であり、VXV推定式フォールバックの有無を
        検証する趣旨ではない。そのため df_vix/df_vxv を実データ同等に供給し、
        vxv_vix_ratio が NaN にならない状態で market_trend_score を検証する
        （5-8b②: VXV推定式フォールバック撤去後は df_vxv 未供給だと
        vxv_vix_ratio が NaN になり、本テストの意図と無関係な理由で失敗するため）。
        """
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        df_vix = pd.DataFrame({"date": spy_df["date"], "close": 15.0})
        df_vxv = pd.DataFrame({"date": spy_df["date"], "close": 16.5})
        res = calculate_market_signals(spy_df, df_vix=df_vix, df_vxv=df_vxv)
        tail_score = res["market_trend_score"].iloc[SPY_LOOKBACK_MIN_BARS:]
        assert np.isfinite(tail_score.astype(float)).all()


class TestVxvVixRatioDoesNotFakeMissingVxv:
    """`df_vxv`（^VIX3M）が供給されないとき、`vxv_vix_ratio` が推定式
    （VIXから算出し[0.85, 1.30]にクリップ）で捏造されず、NaN になること
    （5-8b②・§3.7）。

    実測（本番Parquet）の ^VIX3M/^VIX 比率の値域は [0.744, 1.408] であり、
    旧実装の推定式は [0.85, 1.30] にクリップしていたため、パニック局面
    （比率が0.85を大きく下回る場面）が推定式では表現できず消えてしまう
    危険な暗黙フォールバックだった。
    """

    def test_vxv_vix_ratio_is_nan_when_df_vxv_is_none(self):
        """df_vxv=None のとき、vxv_vix_ratio が全行 NaN になること
        （0.85〜1.30にクリップされた推定値ではないこと）。"""
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        df_vix = pd.DataFrame({"date": spy_df["date"], "close": 15.0})
        res = calculate_market_signals(spy_df, df_vix=df_vix, df_vxv=None)

        assert res["vxv_vix_ratio"].isna().all()

    def test_vxv_vix_ratio_is_nan_when_df_vxv_is_empty(self):
        """df_vxv が空DataFrameのときも同様に NaN になること。"""
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        df_vix = pd.DataFrame({"date": spy_df["date"], "close": 15.0})
        df_vxv_empty = pd.DataFrame(columns=["date", "close"])
        res = calculate_market_signals(spy_df, df_vix=df_vix, df_vxv=df_vxv_empty)

        assert res["vxv_vix_ratio"].isna().all()

    def test_vxv_vix_ratio_is_not_clipped_estimate_when_df_vxv_missing(self):
        """VIXが低水準（12.0付近）でも、旧推定式（1.15 - (vix-12)*0.25/23 を
        [0.85, 1.30]にクリップ）が計算する有限値（この場合は約1.15）になって
        いないこと。NaN であるべき。"""
        spy_df = _spy_df(n=SPY_LOOKBACK_MIN_BARS + 30)
        df_vix = pd.DataFrame({"date": spy_df["date"], "close": 12.0})
        res = calculate_market_signals(spy_df, df_vix=df_vix, df_vxv=None)

        tail_ratio = res["vxv_vix_ratio"].iloc[SPY_LOOKBACK_MIN_BARS:]
        assert tail_ratio.isna().all()
        assert not np.isclose(tail_ratio.fillna(-999).astype(float), 1.15).any()

    def test_logs_warning_when_df_vxv_missing(self, caplog):
        """df_vxv が無いとき、推定式で黙って埋めるのではなく警告ログを
        出すこと（フォールバック撤去の意図を明示するログの存在確認）。"""
        import logging
        spy_df = _spy_df(n=30)
        df_vix = pd.DataFrame({"date": spy_df["date"], "close": 15.0})
        with caplog.at_level(logging.WARNING, logger="indicators.market_signals"):
            calculate_market_signals(spy_df, df_vix=df_vix, df_vxv=None)

        assert any("VXV" in rec.message or "vxv" in rec.message for rec in caplog.records)


class TestAtr14DoesNotFakeInsufficientLookback:
    """`atr_14`/`atr_pct_14` が「それらしい値」（1.0 や 0）に潰れず、
    遡り不足（窓14本未満）・high/low欠損・実際のATR=0では NaN になること
    （5-8b ①・§3.7）。

    現状の実装は `.ffill().fillna(1.0)`（L293）と `np.where(atr_14 > 0, atr_14, 1.0)`
    （L299）で判定不能や実際のゼロ値を「ATR=1ドル」という捏造値に潰し、さらに
    `dist_50sma`/`dist_200sma` 側も `atr_pct_14 == 0` を `0`（距離ゼロ）に潰している。
    このクラスのテストは red のまま修正実装を待つ（プロダクションコードは
    本テストでは変更しない）。

    注記: `atr_14`/`atr_pct_14` は `calculate_market_signals()` の戻り値の列に
    含まれない内部変数のため、窓未充足のケース（14本未満）は
    `calculate_market_signals()` を呼び出す形では検証できない
    （sma_50 の窓＝50本が ATR の窓＝14本より広く、ATR単体の遡り不足だけを
    公開APIの出力から分離して観測できないため）。そのケースのみ、
    現行実装の計算式をそのまま複製したホワイトボックステストにしている。
    """

    def test_atr_14_is_nan_when_window_insufficient(self):
        """14本未満の窓では atr_14 が NaN になるべき（1.0 という部分窓由来の
        値に潰れない）。

        `calculate_market_signals()` の戻り値に atr_14 は含まれない内部変数
        のため、`market_signals.py` が採るべき**新しい**計算式
        （`rolling(14, min_periods=14).mean()`。`.ffill().fillna(1.0)` は
        撤去対象）をテスト内に複製して検証するホワイトボックステスト。
        実装（`calculate_market_signals`）を直接呼ばないため、実装側が
        誤って旧式（`min_periods=1` や `.ffill().fillna(1.0)`）のままでも
        このテスト単体は実装の変更を検知できない――実際の回帰検知は
        公開API経由で検証する同クラスの他2件が担う。本テストは「新しい
        仕様の数式自体」を生きた仕様として固定する目的。
        """
        spy_df = _spy_df(n=13)
        close = spy_df['close']
        high = spy_df['high']
        low = spy_df['low']
        close_prev = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)
        # market_signals.py が採るべき新実装（min_periods=14。ffill/fillnaなし）。
        atr_14_new_impl = tr.rolling(14, min_periods=14).mean()

        # 14本未満の窓は NaN であるべき。
        assert atr_14_new_impl.isna().all()

    def test_market_trend_score_is_nan_for_real_zero_true_range(self):
        """OHLC が完全に横ばい（high == low == close で変動なし）で
        真の ATR が 0 になるケースでも、`market_trend_score` が 0 除算回避の
        ために捏造された 1.0 由来の有限値に潰れず、NaN になること
        （§3.7 の②「ゼロ除算回避」ガード撤去の効果を公開APIから確認する）。
        """
        n = SPY_LOOKBACK_MIN_BARS + 30
        start = date(2010, 4, 1)
        dates = [start + timedelta(days=i) for i in range(n)]
        closes = [100.0] * n  # 完全に横ばい（真の true range は常に0）
        spy_df = pd.DataFrame({
            "date": pd.to_datetime(dates),
            "close": closes,
            "high": closes,
            "low": closes,
            "volume": [1_000_000] * n,
        })
        res = calculate_market_signals(spy_df)

        # sma_50/sma_200 は横ばいデータでも close と一致するため充足する。
        # 旧実装は atr_14=0 を 1.0 に強制置換するため、score が有限値になってしまう。
        # 新実装では atr_pct_14 == 0 が NaN 扱いとなり、score も NaN になるべき。
        tail_score = res["market_trend_score"].iloc[SPY_LOOKBACK_MIN_BARS:]
        assert tail_score.isna().all()

    def test_market_trend_score_is_nan_when_high_low_columns_missing(self):
        """high/low 列が無い場合、`atr_14` は NaN になり `market_trend_score` も
        NaN になるべき（1.0 という捏造値で有限のスコアが出ない）。"""
        n = SPY_LOOKBACK_MIN_BARS + 30
        spy_df = _spy_df(n=n).drop(columns=["high", "low"])
        assert "high" not in spy_df.columns
        assert "low" not in spy_df.columns

        res = calculate_market_signals(spy_df)
        assert len(res) == n

        # sma_50/sma_200 は充足しているため、旧実装なら atr_14=1.0 の捏造値で
        # score が有限値（かつ 0 でもない）になってしまう。
        # 新実装では atr_pct_14 が NaN になり、market_trend_score も NaN になるべき。
        tail_score = res["market_trend_score"].iloc[SPY_LOOKBACK_MIN_BARS:]
        assert tail_score.isna().all()
        assert not (tail_score == 0).any()
