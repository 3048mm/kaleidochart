# -*- coding: utf-8 -*-
"""T3 の全列が「未来のバーで値が変化しない」ことを固定する回帰テスト。

計画書: doc/in_progress/t3_incremental_plan.md 5-2b（`tmp/measure_settling_window.py` の昇格）

## 固定する性質

    series(prices[:T+1])[i]  ==  series(prices)[i]      （i <= T）

全期間の価格から計算した系列を正とし、「ある時点 T までしか見えていない状態」で
計算した系列と比べ、位置 T 以前の値が一致することを確認する。これは
`incremental_state_registry.py` の (c) TWO_SIDED（両側参照＝未来のバーで値が変わる）
に分類される列が実際には存在しないこと（5-2 の実測結果）を、新しい指標の追加で
壊れても検出できる形にしたもの。

## 陽性対照（must-have）

測定手法そのものが「常に差分なし」を返す壊れた実装であれば、この性質は
どんな入力でも自明に成立してしまい、テストとして無意味になる。これを防ぐため、
**実際に未来のバーで過去の値が変わる関数**を陽性対照として使い、同じ測定手法が
その差分を検出できることを先に確認する。

対照に選んだのは `structure_pivot.pivot_strength_low()`。この関数は各足について
「左右対称の単調スタック」で強度を求めるが、**右側の走査（`right[i]`）は
その足より後ろのデータに依存する**（直後に `low[j] <= low[i]` となる `j` までの
距離）。そのため、配列の末尾に新しいバーを追加すると、既存の `right[i]` が
再計算されて変わりうる——実測では末尾から最大347本（
`doc/in_progress/t3_incremental_plan.md` §7-1 参照）。

`structure_pivot_series`（T3 が実際に書き出す列 `sp_pivot`/`sp_hl`）は
`_scan_for_length` の確定遅延（confirmed_index = hl_index + length）により
この未来依存を吸収して外部には見せない設計になっている（5-2 の実測結果）。
`pivot_strength_low()` 単体はその「吸収される前」の生の中間値であり、
未来依存が実際に存在する数少ない既知の対照として使える。
"""
import numpy as np
import pandas as pd

from indicators.calculate import calculate_indicators
from indicators.incremental_state_registry import INDICATOR_COLUMN_REGISTRY
from indicators.structure_pivot import pivot_strength_low


def _make_price_frame(n: int, seed: int, base: float) -> pd.DataFrame:
    """合成 OHLCV 系列を作る（値が変化し続ける現実的な系列。定数列は差分が出ず無意味）。

    構造（LL-HL）・ゾーンブレイクが実際に発生する程度のボラティリティを持たせるため、
    定期的なスパイクを混ぜる（`test_calculate_incremental_equivalence.py` の
    `_make_series` と同じ狙い）。
    """
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.normal(0.05, 1.3, n))
    close = base + walk
    for i in range(45, n, 45):
        close[i:] += rng.normal(0, 1.3 * 8)
    close = np.abs(close) + 10.0
    high = close + np.abs(rng.normal(0.9, 0.3, n))
    low = np.minimum(close - np.abs(rng.normal(0.9, 0.3, n)), close - 0.01)
    openp = close + rng.normal(0, 0.2, n)
    volume = rng.integers(100_000, 900_000, n).astype(float)
    dates = pd.bdate_range('2020-01-06', periods=n).date
    return pd.DataFrame({
        'date': dates, 'open': openp, 'high': high, 'low': low, 'close': close, 'volume': volume,
    })


def _mismatched_columns(full_df: pd.DataFrame, trunc_df: pd.DataFrame, upto: int, columns) -> list:
    """`trunc_df` の [0, upto] 行を `full_df` の同じ範囲と比較し、値が食い違う列名を返す。

    NaN 同士は一致とみなす（`np.isclose(..., equal_nan=True)`）。`state=None` の
    戻り値は NaN が None に変換されて object dtype になりうるため、
    `pd.to_numeric(errors='coerce')` で数値化してから比較する。
    """
    mismatched = []
    full_slice = full_df[columns].iloc[:upto + 1]
    trunc_slice = trunc_df[columns].iloc[:upto + 1]
    for col in columns:
        a = pd.to_numeric(full_slice[col], errors='coerce').to_numpy(dtype=np.float64)
        b = pd.to_numeric(trunc_slice[col], errors='coerce').to_numpy(dtype=np.float64)
        if not np.all(np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)):
            mismatched.append(col)
    return mismatched


class TestT3ColumnsAreUnaffectedByFutureBars:
    """T3 の全列（レジストリ登録済み列）が未来のバーで変化しないことの固定テスト。"""

    N = 600
    # 末尾寄り・中間の2点を見る。sp_*/zb_* の lookback（最大400）をカバーしつつ、
    # 全期間再計算の呼び出し回数を抑えてテスト時間を短く保つ。
    CUTOFFS = (598, 400)

    def _build(self):
        df_full = _make_price_frame(self.N, seed=101, base=80.0)
        spy_full = _make_price_frame(self.N, seed=202, base=300.0)[['date', 'close', 'volume']]
        full_res = calculate_indicators(df_full, spy_full, state=None)
        return df_full, spy_full, full_res

    def test_全列が未来のバーで変化しない(self):
        df_full, spy_full, full_res = self._build()
        columns = list(INDICATOR_COLUMN_REGISTRY.keys())

        offenders = {}
        for T in self.CUTOFFS:
            df_trunc = df_full.iloc[:T + 1].reset_index(drop=True)
            spy_trunc = spy_full.iloc[:T + 1].reset_index(drop=True)
            trunc_res = calculate_indicators(df_trunc, spy_trunc, state=None)
            mismatches = _mismatched_columns(full_res, trunc_res, T, columns)
            if mismatches:
                offenders[T] = mismatches

        assert not offenders, (
            f'未来のバーを追加すると過去の値が変わる列が見つかりました: {offenders}\n'
            '(c) TWO_SIDED に分類されるべき列です。'
            'incremental_state_registry.py の分類・doc/backend_specification.md の'
            '特性タイプ表・test_incremental_state_registry.py の '
            'test_two_sided列が存在しない を更新してください。'
        )

    def test_陽性対照_pivot_strength_lowは未来のバーで過去の値が変わる(self):
        """測定手法自体が差分を検出できることの確認（must-have）。

        `pivot_strength_low()` は右側走査が未来のデータに依存するため、
        上のテストと同じ「前方切り詰め比較」で実際に差分が検出できるはずである。
        これが検出できないなら、上のテストが「差分なし」と報告していても、
        測定手法自体が壊れていて何も検出できていない可能性を排除できない。
        """
        df_full, _spy_full, _full_res = self._build()
        low_full = df_full['low'].to_numpy(dtype=np.float64)
        strength_full = pivot_strength_low(low_full)

        found_diff = False
        max_distance = 0
        for T in self.CUTOFFS:
            strength_trunc = pivot_strength_low(low_full[:T + 1])
            diff_idx = np.flatnonzero(strength_full[:T + 1] != strength_trunc)
            if diff_idx.size:
                found_diff = True
                max_distance = max(max_distance, T - int(diff_idx.min()))

        assert found_diff, (
            'pivot_strength_low() は未来のバーを追加すると過去の強度値が変わるはずですが、'
            'この合成データでは差分が検出されませんでした。テストデータのボラティリティ・'
            '長さを見直すか、測定手法（_mismatched_columns 相当のロジック）自体が'
            '壊れていないか確認してください。'
        )
        # 参考値として記録（アサーションの主目的ではない）。
        assert max_distance > 0
