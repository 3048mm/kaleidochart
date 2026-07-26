import sys
import os
import pandas as pd
import numpy as np
import pytest

backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from indicators.calculate import calculate_indicators


def _make_df(closes, volumes):
    periods = len(closes)
    dates = pd.date_range(start='2025-01-01', periods=periods, freq='B')
    df_dummy = pd.DataFrame({
        'date': dates,
        'close': closes,
        'open': closes,
        'high': [c * 1.01 for c in closes],
        'low': [c * 0.99 for c in closes],
        'volume': volumes,
    })
    df_spy = pd.DataFrame({
        'date': dates,
        'close': [300.0] * periods,
        'volume': [5000000] * periods,
    })
    return calculate_indicators(df_dummy, df_spy)


def test_avg_dollar_volume_21_constant_series():
    """close×volume が一定なら avg_dollar_volume_21 も常にその値になること
    （min_periods=1 のウォームアップ期間を含め、定数列のローリング平均は定数のまま）。
    """
    res = _make_df(closes=[100.0] * 30, volumes=[1_000_000] * 30)
    assert 'avg_dollar_volume_21' in res.columns
    expected = 100.0 * 1_000_000
    assert (res['avg_dollar_volume_21'] == expected).all()


def test_avg_dollar_volume_21_rolling_window():
    """21日を超えた時点で、直近21日分の close×volume の単純平均になっていること。"""
    periods = 25
    # 出来高は1日目だけ極端に多く、以降は少ない一定値にする
    volumes = [10_000_000] + [100_000] * (periods - 1)
    closes = [50.0] * periods
    res = _make_df(closes=closes, volumes=volumes)

    dollar_vol = [c * v for c, v in zip(closes, volumes)]

    # index 20 (21件目、0-indexed) はウィンドウが直近21日分埋まった直後
    expected_at_20 = np.mean(dollar_vol[0:21])
    assert abs(res['avg_dollar_volume_21'].iloc[20] - expected_at_20) < 1e-6

    # index 24 では窓が1日目(巨大出来高)を外れ、平均が下がっているはず
    expected_at_24 = np.mean(dollar_vol[4:25])
    assert abs(res['avg_dollar_volume_21'].iloc[24] - expected_at_24) < 1e-6
    assert res['avg_dollar_volume_21'].iloc[24] < res['avg_dollar_volume_21'].iloc[20]
