"""calc_relative_strength の RS ドット判定（252日窓）の min_periods 統一を固定するテスト。

計画書: doc/in_progress/min_periods_warmup_plan.md チェックリスト 5-6
対象: backend/indicators/relative_strength.py の calc_relative_strength 内、
      rs_252_high / close_252_high / rs_252_low / close_252_low の4箇所
      （`.rolling(window=252, min_periods=1)` → `min_periods=252` へ統一する）。

このテストの性質について（重要）:
  rs_blue_dot_age / rs_red_dot_age は RS_DOT_WARMUP_BARS(=252) による
  ウォームアップガードで「利用可能な履歴が 252 本未満の行は無条件で sentinel」
  にされる（compute_rs_dot_age 内、`i < warmup` で強制 999）。
  このガードは rs_252_high 等の min_periods 設定に関わらず常にかかるため、
  対象4箇所の min_periods=1→252 変更は「観測可能な最終出力（rs_blue_dot_age/
  rs_red_dot_age）には影響しない」ことが期待される。
  つまりこのテストは min_periods=1（変更前）でも min_periods=252（変更後）でも
  green になる想定であり、一般的な TDD の red-green 原則からは外れるが、
  この工程固有の事情（ウォームアップガードによる吸収）によるものであり意図的。
  このテストが固定するのは「252本未満の履歴では、rolling 側の min_periods
  設定がどうであれ、価格パターンに関わらず出力は必ず sentinel である」という
  不変条件そのもの（= 今回の変更が安全である根拠）。
"""
import numpy as np
import pandas as pd

from indicators.relative_strength import (
    RS_DOT_AGE_NONE,
    RS_DOT_WARMUP_BARS,
    calc_relative_strength,
)


def _build_short_history_df(n, close_fn, spy_fn):
    dates = pd.date_range('2023-01-02', periods=n, freq='B')
    close = np.array([close_fn(i) for i in range(n)], dtype=float)
    spy = np.array([spy_fn(i) for i in range(n)], dtype=float)
    df = pd.DataFrame({
        'date': dates, 'close': close, 'high': close, 'low': close,
        'volume': np.full(n, 1000.0),
    })
    df_spy = pd.DataFrame({'date': dates, 'close': spy, 'volume': np.full(n, 5000.0)})
    return df, df_spy


def test_short_history_blue_dot_is_sentinel_despite_naive_trigger_pattern():
    """252本未満の履歴では、rs が毎日「素朴な意味での新高値」を更新し続け、
    close が初日ピークを下回り続ける（＝ min_periods=1 の rolling なら
    ブルードットが連日点灯してしまう）価格パターンでも、warmup ガードにより
    rs_blue_dot_age は全行 999 のままであることを確認する。
    """
    n = 50  # 252本未満（かつ spy が 0 に近づかない範囲）
    assert n < RS_DOT_WARMUP_BARS

    # close は初日をピークにゆるやかに下降（close_252_high = close[0] が上限として残る）
    close_fn = lambda i: 150.0 - 0.01 * i
    # spy は close よりずっと速く下降するため rs=close/spy は単調増加
    # （min_periods=1 の rolling max なら毎日「今日が最高値」になる）
    spy_fn = lambda i: 200.0 - 1.0 * i

    df, df_spy = _build_short_history_df(n, close_fn, spy_fn)
    res = calc_relative_strength(df, df_spy)

    # rs が単調増加していること（このパターンの前提を確認）
    rs = res['close'] / df_spy['close'].to_numpy()
    assert np.all(np.diff(rs.to_numpy()) > 0)

    ba = res['rs_blue_dot_age'].to_numpy()
    assert (ba == RS_DOT_AGE_NONE).all(), (
        '252本未満の履歴では rolling 側の min_periods 設定に関わらず '
        'rs_blue_dot_age は sentinel(999) で固定されるべき'
    )


def test_short_history_red_dot_is_sentinel_despite_naive_trigger_pattern():
    """ブルードットと対称のパターン（rs が単調減少・close は初日安値を上回り続ける）
    でも、252本未満の履歴では rs_red_dot_age が全行 999 のままであることを確認する。
    """
    n = 50  # 252本未満
    assert n < RS_DOT_WARMUP_BARS

    # close は初日を底にゆるやかに上昇（close_252_low = close[0] が下限として残る）
    close_fn = lambda i: 100.0 + 0.01 * i
    # spy は close よりずっと速く上昇するため rs=close/spy は単調減少
    # （min_periods=1 の rolling min なら毎日「今日が最安値」になる）
    spy_fn = lambda i: 200.0 + 1.0 * i

    df, df_spy = _build_short_history_df(n, close_fn, spy_fn)
    res = calc_relative_strength(df, df_spy)

    rs = res['close'] / df_spy['close'].to_numpy()
    assert np.all(np.diff(rs.to_numpy()) < 0)

    ra = res['rs_red_dot_age'].to_numpy()
    assert (ra == RS_DOT_AGE_NONE).all(), (
        '252本未満の履歴では rolling 側の min_periods 設定に関わらず '
        'rs_red_dot_age は sentinel(999) で固定されるべき'
    )
