"""rs_blue_dot_age / rs_red_dot_age（RS ドットの経過日数カウンタ）のテスト。

計画書: doc/in_progress/rs_dot_age_plan.md §3.1

生成規則:
  0=当日点灯 / n=n営業日前に点灯 / 999=未点灯・無効
  1. 点灯日に 0、翌営業日以降 +1
  2. 上限 N を超えたら 999
  3. 反対ドットが点灯したら即 999
  4. 再点灯したら 0 に戻す
  5. 利用可能な履歴が 252 本未満の行は 999
"""
import numpy as np
import pandas as pd
import pytest

from indicators.relative_strength import (
    RS_DOT_AGE_MAX,
    RS_DOT_AGE_NONE,
    RS_DOT_WARMUP_BARS,
    calc_relative_strength,
    compute_rs_dot_age,
)


# ============================================================
# カーネル単体（compute_rs_dot_age）
# ============================================================

def _flags(n, blue_idx=(), red_idx=()):
    blue = np.zeros(n, dtype=bool)
    red = np.zeros(n, dtype=bool)
    blue[list(blue_idx)] = True
    red[list(red_idx)] = True
    return blue, red


def test_warmup_rows_are_sentinel():
    """履歴が warmup 本に満たない行は、点灯していても 999 のまま。"""
    n = RS_DOT_WARMUP_BARS + 5
    blue, red = _flags(n, blue_idx=[0, 10, RS_DOT_WARMUP_BARS - 1])
    ba, _ra = compute_rs_dot_age(blue, red)
    assert (ba[:RS_DOT_WARMUP_BARS] == RS_DOT_AGE_NONE).all()


def test_lit_day_is_zero_and_increments():
    """点灯日が 0、翌日以降は 1 ずつ増える。"""
    w = RS_DOT_WARMUP_BARS
    n = w + 10
    blue, red = _flags(n, blue_idx=[w + 2])
    ba, _ra = compute_rs_dot_age(blue, red)
    assert ba[w + 1] == RS_DOT_AGE_NONE     # 点灯前
    assert ba[w + 2] == 0                    # 点灯日
    assert ba[w + 3] == 1
    assert ba[w + 4] == 2
    assert ba[w + 9] == 7


def test_relight_resets_to_zero():
    """カウント中に再点灯したら 0 に戻る（直近点灯からの経過を保つ）。"""
    w = RS_DOT_WARMUP_BARS
    n = w + 10
    blue, red = _flags(n, blue_idx=[w + 1, w + 4])
    ba, _ra = compute_rs_dot_age(blue, red)
    assert ba[w + 1] == 0
    assert ba[w + 3] == 2
    assert ba[w + 4] == 0     # 再点灯
    assert ba[w + 5] == 1


def test_saturates_to_sentinel_after_cap():
    """上限 N を超えたら 999（＝十分昔・未点灯と同一視）。"""
    w = RS_DOT_WARMUP_BARS
    n = w + RS_DOT_AGE_MAX + 5
    blue, red = _flags(n, blue_idx=[w])
    ba, _ra = compute_rs_dot_age(blue, red)
    assert ba[w + RS_DOT_AGE_MAX] == RS_DOT_AGE_MAX
    assert ba[w + RS_DOT_AGE_MAX + 1] == RS_DOT_AGE_NONE
    assert ba[w + RS_DOT_AGE_MAX + 2] == RS_DOT_AGE_NONE


def test_opposite_dot_invalidates_immediately():
    """ブルーをカウント中にレッドが点灯したら、ブルーは即 999。逆も同様。"""
    w = RS_DOT_WARMUP_BARS
    n = w + 10
    blue, red = _flags(n, blue_idx=[w + 1], red_idx=[w + 3])
    ba, ra = compute_rs_dot_age(blue, red)
    assert ba[w + 2] == 1
    assert ba[w + 3] == RS_DOT_AGE_NONE     # レッド点灯で無効化
    assert ba[w + 4] == RS_DOT_AGE_NONE     # 復活しない
    assert ra[w + 3] == 0
    assert ra[w + 4] == 1


def test_never_lit_is_sentinel_everywhere():
    """一度も点灯しなければ全行 999（NULL を使わない）。"""
    n = RS_DOT_WARMUP_BARS + 20
    blue, red = _flags(n)
    ba, ra = compute_rs_dot_age(blue, red)
    assert (ba == RS_DOT_AGE_NONE).all()
    assert (ra == RS_DOT_AGE_NONE).all()


def test_output_dtype_is_integer_and_has_no_nan():
    """numeric フィルタが NaN 落ちしないよう、常に整数値が入る。"""
    n = RS_DOT_WARMUP_BARS + 5
    blue, red = _flags(n, blue_idx=[RS_DOT_WARMUP_BARS + 1])
    ba, ra = compute_rs_dot_age(blue, red)
    assert np.issubdtype(ba.dtype, np.integer)
    assert np.issubdtype(ra.dtype, np.integer)


def test_empty_input():
    ba, ra = compute_rs_dot_age(np.zeros(0, bool), np.zeros(0, bool))
    assert len(ba) == 0 and len(ra) == 0


# ============================================================
# T3 経路（calc_relative_strength）への結線
# ============================================================

def _series_with_blue_dot():
    """ブルードットが点灯する系列を作る。

    - i < 252 : close/spy が同率で伸びるので rs は一定（= ローリング最大と等しい）。
                ただし close も更新し続けるため `close < close_252_high` が偽で点灯しない
    - 252<=i<262 : close は下げるが SPY はもっと下げる → rs が新高値・close は高値未満
                   ＝ **ブルードット点灯**
    - i >= 262 : close 横ばい・SPY 反発 → rs は最大を割るので点灯しない（age が増える）
    """
    n = 400
    close = np.empty(n)
    spy = np.empty(n)
    for i in range(n):
        if i < 252:
            close[i] = 100.0 + 0.5 * i
            spy[i] = 200.0 + 1.0 * i
        elif i < 262:
            k = i - 251
            close[i] = close[251] - 1.0 * k
            spy[i] = spy[251] - 5.0 * k
        else:
            k = i - 261
            close[i] = close[261]
            spy[i] = spy[261] + 0.30 * k
    dates = pd.date_range('2023-01-02', periods=n, freq='B')
    df = pd.DataFrame({'date': dates, 'close': close, 'high': close,
                       'low': close, 'volume': np.full(n, 1000.0)})
    df_spy = pd.DataFrame({'date': dates, 'close': spy, 'volume': np.full(n, 5000.0)})
    return df, df_spy


def test_t3_emits_age_columns_not_flags():
    df, df_spy = _series_with_blue_dot()
    res = calc_relative_strength(df, df_spy)
    assert 'rs_blue_dot_age' in res.columns
    assert 'rs_red_dot_age' in res.columns
    # 旧フラグは廃止（同名で意味を変えると truthy 判定が静かに反転するため）
    assert 'is_rs_blue_dot' not in res.columns
    assert 'is_rs_red_dot' not in res.columns
    assert res['rs_blue_dot_age'].notna().all()
    assert res['rs_red_dot_age'].notna().all()


def test_t3_blue_dot_age_counts_from_the_lit_day():
    df, df_spy = _series_with_blue_dot()
    res = calc_relative_strength(df, df_spy)
    ba = res['rs_blue_dot_age'].to_numpy()

    # ウォームアップ内は 999
    assert (ba[:RS_DOT_WARMUP_BARS] == RS_DOT_AGE_NONE).all()
    # 252〜261 は連続点灯 → すべて 0
    assert (ba[252:262] == 0).all()
    # 以降は 1 ずつ増える
    assert ba[262] == 1
    assert ba[263] == 2
    # 上限を超えたら 999
    assert ba[261 + RS_DOT_AGE_MAX] == RS_DOT_AGE_MAX
    assert ba[262 + RS_DOT_AGE_MAX] == RS_DOT_AGE_NONE


def test_t3_without_spy_is_all_sentinel():
    """SPY が無い場合（RS が計算できない）は 999 で埋める。0 にしてはいけない。"""
    df, _ = _series_with_blue_dot()
    res = calc_relative_strength(df, None)
    assert (res['rs_blue_dot_age'] == RS_DOT_AGE_NONE).all()
    assert (res['rs_red_dot_age'] == RS_DOT_AGE_NONE).all()
