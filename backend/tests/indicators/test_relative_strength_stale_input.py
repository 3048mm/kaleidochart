"""calc_relative_strength の SPY 欠損検知に関するテスト（5-6b）。

背景: market_signals.py の VIX/VXV は ffill する前に `report_stale_input_gaps`
でログを出しているのに、relative_strength.py の SPY は無言で ffill していた。
RS は全銘柄・全指標の土台であり VIX/VXV より影響範囲が広いため、同じ検知を
入れる（ffill の挙動自体は変えない）。
"""
import numpy as np
import pandas as pd

from indicators.relative_strength import calc_relative_strength


def _build_df_with_spy_gap(n_rows=60, gap_start=30, gap_len=5):
    """個別銘柄側は連続データ、SPY 側の一部区間だけ欠損させた合成データ。"""
    dates = pd.date_range(start='2024-01-01', periods=n_rows, freq='B')
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1.0, size=n_rows))
    close = np.clip(close, 10, None)
    spy_close = 400 + np.cumsum(rng.normal(0, 0.5, size=n_rows))

    df = pd.DataFrame({'date': dates, 'close': close, 'high': close, 'low': close,
                        'volume': [1000] * n_rows})

    # SPY 側の一部日付を間引いて欠損を作る（merge の左結合で NaN になる）
    spy_dates = list(dates)
    for i in range(gap_start, gap_start + gap_len):
        spy_dates[i] = None
    df_spy = pd.DataFrame({'date': dates, 'close': spy_close, 'volume': [5000] * n_rows})
    df_spy = df_spy.drop(df_spy.index[gap_start:gap_start + gap_len]).reset_index(drop=True)

    return df, df_spy


def test_spy_gap_is_logged(caplog):
    """SPY 側に欠損区間があると、ffill する前に INFO/WARNING ログが出ること。"""
    df, df_spy = _build_df_with_spy_gap()
    with caplog.at_level('INFO'):
        calc_relative_strength(df, df_spy)
    assert 'SPY' in caplog.text


def test_no_gap_produces_no_log(caplog):
    """SPY 側に欠損が無ければログは出ないこと。"""
    n_rows = 60
    dates = pd.date_range(start='2024-01-01', periods=n_rows, freq='B')
    rng = np.random.default_rng(2)
    close = 100 + np.cumsum(rng.normal(0, 1.0, size=n_rows))
    close = np.clip(close, 10, None)
    spy_close = 400 + np.cumsum(rng.normal(0, 0.5, size=n_rows))

    df = pd.DataFrame({'date': dates, 'close': close, 'high': close, 'low': close,
                        'volume': [1000] * n_rows})
    df_spy = pd.DataFrame({'date': dates, 'close': spy_close, 'volume': [5000] * n_rows})

    with caplog.at_level('INFO'):
        calc_relative_strength(df, df_spy)
    assert 'SPY' not in caplog.text


def test_gap_detection_does_not_change_rs_value(caplog):
    """検知を追加しても rs_value（ffill 後の計算結果）自体は変わらないこと。"""
    df, df_spy = _build_df_with_spy_gap()
    with caplog.at_level('INFO'):
        res = calc_relative_strength(df.copy(), df_spy.copy())

    # 欠損区間の直後の rs_value が、持ち越された spy_close を使って計算されていること
    spy_close_filled = df_spy.set_index('date')['close'].reindex(df['date']).ffill()
    expected = df['close'] / spy_close_filled.to_numpy()
    both_notna = res['rs_value'].notna() & pd.notna(expected)
    assert both_notna.sum() > 0
    diff = (res['rs_value'][both_notna].to_numpy() - expected[both_notna].to_numpy())
    assert np.nanmax(np.abs(diff)) < 1e-9
