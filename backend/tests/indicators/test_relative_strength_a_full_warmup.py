"""A-full（min_periods=n 統一）に伴う rs_trend_sN/rs_ratio_eN/rs_momentum_eN の
warmup 仕様の回帰テスト（TDD red フェーズ）。

計画書: doc/in_progress/min_periods_warmup_plan.md 5-9c
対象: `backend/indicators/relative_strength.py` の `calc_relative_strength`、
229/248/249/268/269行目の `min_periods=max(1, n//2)` を `min_periods=n` に
統一する変更（本ファイル作成時点では未実装。次工程で別ワーカーが実装する）。

## 何を固定するか

`min_periods=max(1, n//2)` は窓の半分の本数（n//2）が埋まった時点で「それらしい
値」を返してしまう。例えば n=200 の場合、100本しか無い区間でも非NULLの値を
返す。これは本計画①（min_periods=window統一）と同じ病気の4つ目の流儀であり、
`min_periods=n` に統一するのが修正方針。

本ファイルは「窓を満たすまではNaNであるべき」という**修正後の仕様**を直接
検証する。現行実装に対しては意図的に red になる。

## しきい値の実測（2026-09-24・本ワーカーが実測）

`_build_df(900)` に対する現行実装（修正前）の各列の最初の非NULL位置
（0始まり）を実測したところ、計画書のコメントに記載された実測例
（rs_trend_s200: 約100→199 / rs_ratio_e200: 約298→398 /
rs_momentum_e200: 約610→810）と完全に一致した:

    rs_trend_s200:    99   (n//2=100本目で非NULL化。修正後は199=n本目から)
    rs_value_e200:   199   (EMAのwarmup。n本必要。min_periodsとは無関係で不変)
    rs_ratio_e200:   298   (=199 + 99。rs_value_e200が始まってからn//2本後)
    rs_roc_ema_200:  511   (=roc_emaのEMA warmup。rs_ratio_e200のしきい値に連動)
    rs_momentum_e200: 610  (=511 + 99。修正後は710ではなく810になる。
                            roc_ema自身のwarmupもrs_ratio_e200のしきい値シフト
                            分だけ後ろにずれるため、単純な+100ではなく
                            計画書記載の610→810になる）

新しいしきい値（199 / 398 / 810）は「窓を完全に満たすまでNaN」という仕様から
導出した理論値であり、修正後の実装が満たすべき下限。本ファイルは以下を検証する:

1. 旧しきい値ちょうど（99 / 298 / 610）では、修正後はまだ窓が半分程度しか
   埋まっておらずNaNであるべき（現行実装は非NULLを返すため red になる）
2. 新しきい値を十分に超えた成熟区間では、非NULLかつ独立計算（numpy）と
   一致すること（値そのものの計算式は変えないため、現行実装でも成立する）
"""
import numpy as np
import pandas as pd
import pytest

from indicators.relative_strength import calc_relative_strength


def _build_df(n_rows: int, seed: int = 11):
    """calc_relative_strength 用の合成 OHLCV + SPY データを作る。

    `test_relative_strength_precision.py` の
    `test_rs_ratio_matches_reference_implementation_on_normal_data` と
    同じ狙い（通常の価格レンジ・欠損なしの連続データ）。
    """
    dates = pd.date_range(start='2020-01-01', periods=n_rows, freq='B')
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, size=n_rows))
    close = np.clip(close, 10, None)
    spy_close = 400 + np.cumsum(rng.normal(0, 0.5, size=n_rows))

    df = pd.DataFrame({
        'date': dates, 'close': close, 'high': close, 'low': close,
        'volume': [1000] * n_rows,
    })
    df_spy = pd.DataFrame({'date': dates, 'close': spy_close, 'volume': [5000] * n_rows})
    return df, df_spy


# 現行実装（修正前）で実測した「n//2本目で非NULL化する」しきい値（0始まり）。
_OLD_THRESHOLD_POS = {
    'rs_trend_s200': 99,
    'rs_ratio_e200': 298,
    'rs_momentum_e200': 610,
}

# A-full修正後に「窓(n=200)を完全に満たして初めて非NULLになる」べき理論しきい値
# （0始まり）。計画書記載の実測例（99→199 / 298→398 / 610→810）と一致。
_NEW_THRESHOLD_POS = {
    'rs_trend_s200': 199,
    'rs_ratio_e200': 398,
    'rs_momentum_e200': 810,
}

# 全テスト共通の合成データの行数（新しきい値810 + 十分な成熟区間を確保する）。
N_ROWS = 900


class TestAFullWarmupIsNaNUntilWindowIsFull:
    """窓の半分（旧しきい値）ではA-full修正後はまだNaNであるべき（redテスト本体）。"""

    def test_rs_trend_s200_is_nan_at_old_half_window_threshold(self):
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _OLD_THRESHOLD_POS['rs_trend_s200']
        assert pd.isna(res['rs_trend_s200'].iloc[idx]), (
            f'rs_trend_s200[{idx}] は窓(200本)の半分(100本)しか埋まっていない'
            '時点であり、A-full(min_periods=n統一)後はNaNであるべき。'
            '現行実装(min_periods=max(1,n//2)=100)ではこの時点で非NULLの値を'
            '返してしまう（本テストは現行実装に対して意図的にredになる）。'
        )

    def test_rs_ratio_e200_is_nan_at_old_half_window_threshold(self):
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _OLD_THRESHOLD_POS['rs_ratio_e200']
        assert pd.isna(res['rs_ratio_e200'].iloc[idx]), (
            f'rs_ratio_e200[{idx}] は rs_value_e200 の rolling(200) 窓が半分'
            '(100本)しか埋まっていない時点であり、A-full後はNaNであるべき。'
            '現行実装ではこの時点で非NULLの値を返してしまう'
            '（本テストは現行実装に対して意図的にredになる）。'
        )

    def test_rs_momentum_e200_is_nan_at_old_half_window_threshold(self):
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _OLD_THRESHOLD_POS['rs_momentum_e200']
        assert pd.isna(res['rs_momentum_e200'].iloc[idx]), (
            f'rs_momentum_e200[{idx}] は rs_roc_ema_200 の rolling(200) 窓が'
            '半分(100本)しか埋まっていない時点であり、A-full後はNaNであるべき。'
            '現行実装ではこの時点で非NULLの値を返してしまう'
            '（本テストは現行実装に対して意図的にredになる）。'
        )


class TestValueUnchangedAfterFullWarmup:
    """窓を十分に満たした成熟区間では、値そのものの計算式は変わらないため、
    非NULLかつ独立計算(numpy)と一致すること（現行実装でも成立する回帰ガード）。
    """

    def test_rs_trend_s200_matches_reference_after_new_threshold(self):
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _NEW_THRESHOLD_POS['rs_trend_s200'] + 50  # 新しきい値を十分に超えた成熟区間
        rs = res['rs_value']
        window = rs.iloc[idx - 200 + 1: idx + 1].to_numpy()
        assert not np.isnan(window).any(), '検証対象の窓に想定外のNaNが含まれている'
        expected_sma = window.mean()
        expected = res['rs_value_e5'].iloc[idx] / expected_sma

        actual = res['rs_trend_s200'].iloc[idx]
        assert pd.notna(actual)
        assert actual == pytest.approx(expected, abs=1e-9)

    def test_rs_ratio_e200_matches_reference_after_new_threshold(self):
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _NEW_THRESHOLD_POS['rs_ratio_e200'] + 50  # 新しきい値を十分に超えた成熟区間
        rs_ema = res['rs_value_e200']
        window = rs_ema.iloc[idx - 200 + 1: idx + 1].to_numpy()
        assert not np.isnan(window).any(), '検証対象の窓に想定外のNaNが含まれている'
        expected_mean = window.mean()
        expected_std = np.std(window, ddof=1)
        expected_z = (window[-1] - expected_mean) / expected_std

        actual = res['rs_ratio_e200'].iloc[idx]
        assert pd.notna(actual)
        assert actual == pytest.approx(expected_z, abs=1e-6)

    def test_rs_momentum_e200_is_non_null_after_new_threshold(self):
        """rs_momentum_e200 は roc_ema の二重EMA経由のため独立計算での厳密な
        再現は行わず、新しきい値を超えた成熟区間で非NULLになることのみを確認する
        （値の正しさ自体は既存の test_relative_strength_precision.py が別途担保）。
        """
        df, df_spy = _build_df(N_ROWS)
        res = calc_relative_strength(df, df_spy)

        idx = _NEW_THRESHOLD_POS['rs_momentum_e200'] + 50
        assert idx < N_ROWS, 'N_ROWS が新しきい値+成熟区間を確保できていない'
        assert pd.notna(res['rs_momentum_e200'].iloc[idx])
