"""rs_ratio_eN / rs_momentum_eN の rolling std 数値精度問題の回帰テスト。

背景: doc/completed/rs_rolling_std_precision_plan.md
pandas の `.rolling().std()` は全履歴に対して1回のパスで逐次更新するため、
極端に桁の異なる値（累積分割・併合で価格が数十億倍になった過去の区間など）を
通過した際の浮動小数点誤差が、何年も後の全く異なる桁の区間の計算まで汚染し、
rs_std が厳密に 0.0 になったり（NULL化）、大きくずれた値になったりする。
"""
import numpy as np
import pandas as pd
import pytest

from indicators.relative_strength import calc_relative_strength


def _build_extreme_range_df(n_early=1200, n_late=1200, seed=42):
    """前半を極端に巨大な価格帯（1e10オーダー）、後半を通常の価格帯（100前後）にした
    合成データ。SPY側は緩やかな上昇のみで極端な値を含まない。
    """
    rng_idx = np.arange(n_early)
    early = 1e10 * (1 + 0.0001 * np.sin(rng_idx / 5.0))
    rng_idx2 = np.arange(n_late)
    late = 100 * (1 + 0.01 * np.sin(rng_idx2 / 7.0) + 0.001 * rng_idx2 / n_late)
    close = np.concatenate([early, late])

    total = n_early + n_late
    dates = pd.date_range(start='2010-01-01', periods=total, freq='B')
    spy_close = 400 + 0.01 * np.arange(total)

    df = pd.DataFrame({
        'date': dates, 'close': close, 'high': close, 'low': close,
        'volume': [1000] * total,
    })
    df_spy = pd.DataFrame({'date': dates, 'close': spy_close, 'volume': [5000] * total})
    return df, df_spy, n_early, n_late


def test_rs_ratio_no_anomalous_null_after_extreme_price_range():
    """後半（通常価格帯に戻ってから十分経過した成熟区間）で
    rs_ratio_eN が NULL にならないこと（ウォームアップ由来のNULLは対象外）。
    """
    df, df_spy, n_early, n_late = _build_extreme_range_df()
    res = calc_relative_strength(df, df_spy)

    # 後半に入ってから100営業日以上経過した安定区間のみを検査対象にする
    mature = res.iloc[n_early + 100:]
    for n in [5, 14, 21, 63, 200]:
        col = f'rs_ratio_e{n}'
        null_count = mature[col].isna().sum()
        assert null_count == 0, (
            f'{col} が成熟区間で {null_count} 件 NULL になっている'
            f'（極端な価格レンジによる浮動小数点精度問題が再発している可能性）'
        )


def test_rs_ratio_matches_independent_numpy_zscore():
    """成熟区間のある1点について、rs_ratio_e21 が独立に numpy で計算した
    z-score とほぼ一致すること（値そのものが壊れていないことの検証）。
    """
    df, df_spy, n_early, n_late = _build_extreme_range_df()
    res = calc_relative_strength(df, df_spy)

    n = 21
    rs_ema = res['rs_value_e21']
    idx = len(res) - 1
    window = rs_ema.iloc[idx - n + 1: idx + 1].to_numpy()
    expected_mean = window.mean()
    expected_std = np.std(window, ddof=1)
    expected_z = (window[-1] - expected_mean) / expected_std

    actual_z = res['rs_ratio_e21'].iloc[idx]
    assert actual_z == pytest.approx(expected_z, abs=1e-3)


def test_rs_ratio_matches_reference_implementation_on_normal_data():
    """通常の価格レンジのデータで、rs_ratio_eN / rs_momentum_eN が
    素朴な参照実装（各ウィンドウをnumpyで独立に計算）と一致すること。
    """
    n_rows = 600  # n=200のEMAウォームアップ(先頭約199行がNaN)を抜けて十分な検証区間を確保する
    dates = pd.date_range(start='2024-01-01', periods=n_rows, freq='B')
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1.0, size=n_rows))
    close = np.clip(close, 10, None)
    spy_close = 400 + np.cumsum(rng.normal(0, 0.5, size=n_rows))

    df = pd.DataFrame({'date': dates, 'close': close, 'high': close, 'low': close,
                        'volume': [1000] * n_rows})
    df_spy = pd.DataFrame({'date': dates, 'close': spy_close, 'volume': [5000] * n_rows})

    res = calc_relative_strength(df, df_spy)

    def _nan_aware_std(x, min_periods):
        valid = x[~np.isnan(x)]
        if len(valid) < min_periods or len(valid) < 2:
            return np.nan
        return np.std(valid, ddof=1)

    for n in [5, 14, 21, 63, 200]:
        rs_ema = res[f'rs_value_e{n}' if n != 5 else 'rs_value_e5']
        min_periods = max(1, n // 2)
        ref_std = rs_ema.rolling(window=n, min_periods=1).apply(
            lambda x, mp=min_periods: _nan_aware_std(x, mp), raw=True
        )
        actual = res[f'rs_ratio_e{n}']
        rs_mean = rs_ema.rolling(window=n, min_periods=min_periods).mean()
        ref_ratio = np.where(ref_std.isna() | (ref_std == 0), np.nan,
                              (rs_ema - rs_mean) / ref_std)

        both_notna = actual.notna() & pd.notna(ref_ratio)
        assert both_notna.sum() > 0, f'rs_ratio_e{n}: 比較可能な行が0件'
        diff = (actual[both_notna].to_numpy() - np.asarray(ref_ratio)[both_notna.to_numpy()])
        assert np.nanmax(np.abs(diff)) < 1e-6, f'rs_ratio_e{n} が参照実装と一致しない'
