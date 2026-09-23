"""moving_averages.py の calc_moving_averages のテスト。

計画書: doc/in_progress/min_periods_warmup_plan.md 5-2/5-3

`sma_{period}` は現状 `close.rolling(window=period, min_periods=1).mean()`
（moving_averages.py:80）で計算されており、窓長に満たない本数でも
「ある分だけの平均」という偽の値を返してしまう。同じループ内の `ema_{period}`
（calculate_ema_tv 経由）は既に「窓に満たない間は NaN」という正しい設計に
なっており、本テストはその挙動へ `sma_*` を揃えることを固定する（red）。

このファイルは実装（min_periods=1 → min_periods=window への変更、5-3）より
先に作成しており、現時点の実装に対しては意図的に失敗する。
"""
import numpy as np
import pandas as pd
import pytest

from indicators.moving_averages import calc_moving_averages

PERIODS = [5, 21, 50, 63, 150, 200]


def _build_df(n):
    """close が単調増加する長さ n の合成 DataFrame。"""
    close = pd.Series(np.linspace(100.0, 100.0 + n - 1, n))
    return pd.DataFrame({'close': close})


class TestSmaWarmupIsNaN:
    """窓長に満たない本数のとき、sma_{period} は全行 NaN であるべき（warmup）。"""

    @pytest.mark.parametrize('period', PERIODS)
    def test_period本未満のときsma列は全行NaN(self, period):
        df = _build_df(period - 1)
        result = calc_moving_averages(df.copy(), state=None)

        col = f'sma_{period}'
        assert col in result.columns
        assert result[col].isna().all(), (
            f'{col} は close が {period} 本未満のとき全行 NaN であるべきだが、'
            f'min_periods=1 のせいで非NULLの値が混入している'
        )


class TestSmaFirstValidPosition:
    """close がちょうど period 本のとき、最後の行だけが非NULLで、
    それより前（0..period-2）はすべて NaN であること（warmup_bars = period-1）。"""

    @pytest.mark.parametrize('period', PERIODS)
    def test_ちょうどperiod本で最後の行だけ非NULL(self, period):
        df = _build_df(period)
        result = calc_moving_averages(df.copy(), state=None)

        col = f'sma_{period}'
        # 0 .. period-2 行目（warmup区間）は NaN
        assert result[col].iloc[:period - 1].isna().all(), (
            f'{col} は先頭 {period - 1} 本が NaN であるべき（窓が育っていない）'
        )
        # period-1 行目（0始まりで period 本目）は非NULL
        assert pd.notna(result[col].iloc[period - 1]), (
            f'{col} は {period} 本目（position {period - 1}）で初めて非NULLになるべき'
        )


class TestSmaValueMatchesStrictRolling:
    """窓を超えて十分な本数があるとき、非NULL区間の値は
    close.rolling(period, min_periods=period).mean()（pandas既定のmin_periods=window）
    と一致すること。"""

    @pytest.mark.parametrize('period', PERIODS)
    def test_十分な本数で厳密なrolling平均と一致する(self, period):
        n = period + 50
        df = _build_df(n)
        result = calc_moving_averages(df.copy(), state=None)

        col = f'sma_{period}'
        expected = df['close'].rolling(window=period, min_periods=period).mean()

        pd.testing.assert_series_equal(
            result[col], expected, check_names=False,
            obj=col,
        )


class TestSmaExactNumericValues:
    """代表的な period（5, 200）について、手計算/模範解に基づく厳密な数値検証。"""

    def test_sma5の実測値(self):
        # close: 1..30 の連番。sma_5 は position4以降 close.rolling(5).mean() と一致するはず。
        closes = list(range(1, 31))
        df = pd.DataFrame({'close': pd.Series(closes, dtype=float)})
        result = calc_moving_averages(df.copy(), state=None)

        # warmup: position 0..3 は NaN
        assert result['sma_5'].iloc[:4].isna().all()

        # position 4 (5本目): (1+2+3+4+5)/5 = 3.0
        assert result['sma_5'].iloc[4] == pytest.approx(3.0)
        # position 9 (10本目): (6+7+8+9+10)/5 = 8.0
        assert result['sma_5'].iloc[9] == pytest.approx(8.0)
        # position 29 (最終行): (26+27+28+29+30)/5 = 28.0
        assert result['sma_5'].iloc[29] == pytest.approx(28.0)

    def test_sma200の実測値(self):
        # close は全て一定値100.0で250本。sma_200 は warmup後は常に100.0のはず。
        n = 250
        closes = [100.0] * n
        df = pd.DataFrame({'close': pd.Series(closes, dtype=float)})
        result = calc_moving_averages(df.copy(), state=None)

        # warmup: position 0..198 (先頭199本) は NaN
        assert result['sma_200'].iloc[:199].isna().all(), (
            'sma_200 は先頭199本が NaN であるべき（200本目で初めて窓が育つ）'
        )
        # position 199 (200本目) 以降は常に100.0
        # 注意: `Series == pytest.approx(scalar)` は要素ごとの近似比較にならず
        # pandas の Series.__eq__ が優先されて常に全要素 False になる（既知の罠）。
        # np.isclose で明示的に配列比較する。
        assert np.isclose(result['sma_200'].iloc[199:].to_numpy(), 100.0).all()
