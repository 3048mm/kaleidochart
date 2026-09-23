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
    """close×volume が一定でも、21本のウォームアップ（min_periods=window）が
    正しく効いていること: 先頭20本（position 0..19）はNaN、21本目（position 20）
    以降は一定値になる（min_periods=1 の偽値を許容しない）。
    """
    res = _make_df(closes=[100.0] * 30, volumes=[1_000_000] * 30)
    assert 'avg_dollar_volume_21' in res.columns
    expected = 100.0 * 1_000_000
    assert res['avg_dollar_volume_21'].iloc[:20].apply(lambda v: pd.isna(v)).all()
    tail = res['avg_dollar_volume_21'].iloc[20:].astype(float)
    assert np.isclose(tail.to_numpy(), expected).all()


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


def test_avg_dollar_volume_21_warmup_boundary():
    """avg_dollar_volume_21: 20本のときは全行NaN、21本ちょうどのとき最後の1行だけ非NULLになること
    （min_periods=window の境界確認）。
    """
    res_20 = _make_df(closes=[100.0] * 20, volumes=[1_000_000] * 20)
    assert res_20['avg_dollar_volume_21'].apply(pd.isna).all()

    res_21 = _make_df(closes=[100.0] * 21, volumes=[1_000_000] * 21)
    assert res_21['avg_dollar_volume_21'].iloc[:20].apply(pd.isna).all()
    assert not pd.isna(res_21['avg_dollar_volume_21'].iloc[20])


def test_vol_surge_columns_warmup_boundary():
    """vol_surge_21 / vol_surge_rel_spy_21: vol_sma_21（および spy_vol_sma_21）が
    21本育つまではNaNを維持し、21本目で初めて値が出ること。
    """
    res_20 = _make_df(closes=[100.0] * 20, volumes=[1_000_000] * 20)
    assert res_20['vol_surge_21'].apply(pd.isna).all()
    assert res_20['vol_surge_rel_spy_21'].apply(pd.isna).all()

    res_21 = _make_df(closes=[100.0] * 21, volumes=[1_000_000] * 21)
    assert res_21['vol_surge_21'].iloc[:20].apply(pd.isna).all()
    assert not pd.isna(res_21['vol_surge_21'].iloc[20])
    assert res_21['vol_surge_rel_spy_21'].iloc[:20].apply(pd.isna).all()
    assert not pd.isna(res_21['vol_surge_rel_spy_21'].iloc[20])


def test_dist_63d_high_pct_warmup_boundary():
    """dist_63d_high_pct: max_63d が63本育つまではNaN、63本目で初めて値が出ること。"""
    closes_62 = [100.0 + i * 0.1 for i in range(62)]
    res_62 = _make_df(closes=closes_62, volumes=[1_000_000] * 62)
    assert res_62['dist_63d_high_pct'].apply(pd.isna).all()

    closes_63 = [100.0 + i * 0.1 for i in range(63)]
    res_63 = _make_df(closes=closes_63, volumes=[1_000_000] * 63)
    assert res_63['dist_63d_high_pct'].iloc[:62].apply(pd.isna).all()
    assert not pd.isna(res_63['dist_63d_high_pct'].iloc[62])


def test_dist_52w_high_pct_warmup_boundary():
    """dist_52w_high_pct: max_252d が252本育つまではNaN、252本目で初めて値が出ること。
    252本規模のため、単調増加の軽量な合成データで検証する。
    """
    closes_251 = [100.0 + i * 0.05 for i in range(251)]
    res_251 = _make_df(closes=closes_251, volumes=[1_000_000] * 251)
    assert res_251['dist_52w_high_pct'].apply(pd.isna).all()

    closes_252 = [100.0 + i * 0.05 for i in range(252)]
    res_252 = _make_df(closes=closes_252, volumes=[1_000_000] * 252)
    assert res_252['dist_52w_high_pct'].iloc[:251].apply(pd.isna).all()
    assert not pd.isna(res_252['dist_52w_high_pct'].iloc[251])


def test_vol_accum_days_5_warmup_and_values():
    """vol_accum_days_5: vol_sma_21(21本) + rolling(5)(4本) = 実質25本のウォームアップが
    正しく伝播すること。24本ではNaN、25本目(position 24)以降で初めて値が出て、
    その値が0〜5の妥当な範囲になること。
    """
    # 24本（25本未満）: 全行NaN/None
    closes_24 = [100.0] * 24
    volumes_24 = [1_000_000] * 24
    res_24 = _make_df(closes=closes_24, volumes=volumes_24)
    assert res_24['vol_accum_days_5'].apply(pd.isna).all()

    # 十分な本数（40本）。5日ごとに出来高が急増する日を混ぜ、値の妥当性も確認する
    periods = 40
    closes_40 = [100.0 + i * 0.1 for i in range(periods)]  # 単調増加＝毎日 close.diff() > 0
    volumes_40 = []
    for i in range(periods):
        if i % 5 == 0:
            volumes_40.append(5_000_000)  # 出来高急増日
        else:
            volumes_40.append(500_000)
    res_40 = _make_df(closes=closes_40, volumes=volumes_40)

    # position 0..23 (24本未満相当) はウォームアップ中でNaN/None
    assert res_40['vol_accum_days_5'].iloc[:24].apply(pd.isna).all()
    # position 24 (25本目) 以降は非NULLになる
    tail = res_40['vol_accum_days_5'].iloc[24:]
    assert not tail.apply(pd.isna).any()
    # 妥当な範囲（0〜5）に収まっていること
    tail_values = tail.astype(int)
    assert tail_values.between(0, 5).all()


def test_is_trend_template_none_when_max_252d_not_yet_available():
    """is_trend_template: max_252d（252本ウィンドウ、cond5の分母）がまだ埋まっていない
    200〜251本の履歴帯では、cond1〜cond4が全て真でも判定不能（None）であるべき。

    現行実装（calc_volume_and_trends）は外側のnp.whereが sma200_20d_ago
    （sma_200.shift(20)）のNaNしか見ておらず、max_252dのNaNを無視している。
    cond5 = close >= (max_252d * 0.70) はNaN比較になり例外なくFalseへ落ちる
    （NaN比較はNaNにならずFalseになる）ため、cond1〜4が真の行では
    is_trend_template=0（「条件を満たさない」という誤った断定）が保存されてしまう。
    正しくは None（判定不能）であるべき。

    価格パターン: close = 100 + i*0.1 の緩やかな単調増加（220本）。
    sma_50 > sma_150 > sma_200、close > sma_50、sma_200 >= sma_200[20日前] が
    すべて成立する一方、252本に満たないため max_252d は全行NaN。
    なお sma200_20d_ago（sma_200.shift(20)）が非NaNになるのはこの本数では
    最終行（position 219）のみ（sma_200 自体が position 199〜219 でしか
    埋まらないため、20日前を参照できるのはそのうち最後の1点だけ）。
    """
    periods = 220
    closes = [100.0 + i * 0.1 for i in range(periods)]
    volumes = [1_000_000] * periods
    res = _make_df(closes=closes, volumes=volumes)

    # 前提確認: この本数では max_252d（252本必要）は全行NaN
    assert res['dist_52w_high_pct'].apply(pd.isna).all()
    # 前提確認: 最終行は sma_200 が埋まっており、cond1〜4を評価できる
    assert not pd.isna(res['sma_200'].iloc[-1])

    # 判定不能な行なので None（NaN）であるべき
    assert pd.isna(res['is_trend_template'].iloc[-1])


def test_is_trend_template_true_when_all_conditions_met_with_full_history():
    """回帰確認: 252本以上の履歴があり max_252d が埋まっている場合、cond1〜5が
    すべて満たされる緩やかな上昇トレンドでは従来通り is_trend_template=1 になること。
    """
    periods = 260
    closes = [100.0 + i * 0.1 for i in range(periods)]
    volumes = [1_000_000] * periods
    res = _make_df(closes=closes, volumes=volumes)

    assert res['is_trend_template'].iloc[-1] == 1
