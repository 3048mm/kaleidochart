"""volatility.py のテスト。

計画書: doc/in_progress/t3_incremental_plan.md 5-15b（code-review指摘3）

`_atr_wilder_kernel` のシード（`true_range[0:window]` の平均）は `ta` ライブラリの
Wilder再帰平滑化とビット単位で一致させる目的で自前実装された（T3増分化計画 5-4）。
`ta`（内部的に pandas の rolling mean 相当）は NaN をスキップする平均を使うが、
修正前の実装は `np.mean` を使っており、窓の先頭 `window` 本のいずれかに NaN
（high/low の欠損に由来する `true_range` の NaN）が1つでもあると、シード自体が
NaN になり `atr_14`/`atr_pct_14`/`sma50_atr_mult` が列全体 NaN になっていた。
"""
import numpy as np
import pandas as pd

from indicators.volatility import _true_range, calc_volatility

WINDOW = 14


def _build_df(n=25, nan_idx=5):
    """先頭付近（`window`本以内）の1本だけ high/low が NaN の合成OHLC。

    `_true_range` は high/low の両方が NaN の行でのみ真に NaN を返す
    （どちらか一方だけがNaNだと `.max(axis=1)` が skipna で他方を拾ってしまうため、
    意図的に両方 NaN にする。実運用の「NULL OHLC行」を模したもの）。
    """
    close = pd.Series(np.linspace(100.0, 100.0 + n - 1, n))
    high = (close + 1.0).copy()
    low = (close - 1.0).copy()
    high.iloc[nan_idx] = np.nan
    low.iloc[nan_idx] = np.nan
    return pd.DataFrame({'close': close, 'high': high, 'low': low})


class TestAtrWilderSeedSkipsNaN:
    """指摘3: ATRのシードが `np.mean`（NaN伝播）ではなく `ta` 同様の
    NaNスキップの平均になっていることの固定。"""

    def test_窓内にNaNのhigh_lowがあってもatr_14が全列NaNにならない(self):
        df = _build_df()
        result = calc_volatility(df.copy(), state=None)

        # 修正前（np.mean）ではシード自体がNaNになり、seed_idx（=window-1=13）
        # 以降の全行がNaNになっていた（これが本テストのredポイント）。
        assert result['atr_14'].iloc[WINDOW - 1:].notna().all(), (
            '窓内にNaNのhigh/lowが1本あるだけでatr_14が全てNaNになった'
            '（np.meanのNaN伝播の回帰）'
        )
        assert result['atr_pct_14'].iloc[WINDOW - 1:].notna().all()

    def test_シード値はNaNをスキップした平均と一致する(self):
        df = _build_df()
        result = calc_volatility(df.copy(), state=None)

        true_range = _true_range(df['high'], df['low'], df['close'])
        tr_values = true_range.to_numpy(dtype=float)
        expected_seed = float(pd.Series(tr_values[:WINDOW]).mean())

        assert np.isnan(tr_values[5]), '前提: true_range[5] がNaNになっていること'
        assert not np.isnan(expected_seed), '前提: NaNスキップの平均は有限値になること'
        assert np.isclose(
            result['atr_14'].iloc[WINDOW - 1], expected_seed, rtol=1e-9, atol=1e-9
        )

    def test_NaNが無ければ従来どおりnp_meanと同じ値になる(self):
        """回帰確認: NaN が無い通常ケースでは `np.mean` と `pd.Series.mean` は
        同じ値を返すため、既存の（state=None の）挙動は変わらない。"""
        close = pd.Series(np.linspace(100.0, 124.0, 25))
        high = close + 1.0
        low = close - 1.0
        df = pd.DataFrame({'close': close, 'high': high, 'low': low})

        result = calc_volatility(df.copy(), state=None)

        true_range = _true_range(df['high'], df['low'], df['close'])
        tr_values = true_range.to_numpy(dtype=float)
        expected_seed_np = float(np.mean(tr_values[:WINDOW]))
        expected_seed_pd = float(pd.Series(tr_values[:WINDOW]).mean())

        assert np.isclose(expected_seed_np, expected_seed_pd, rtol=1e-12, atol=1e-12)
        assert np.isclose(
            result['atr_14'].iloc[WINDOW - 1], expected_seed_pd, rtol=1e-9, atol=1e-9
        )
