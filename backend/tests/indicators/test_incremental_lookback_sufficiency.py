# -*- coding: utf-8 -*-
"""`INDICATOR_COLUMN_REGISTRY` の lookback が実際に十分であることを固定する回帰テスト。

計画書: doc/in_progress/t3_incremental_plan.md 5-2b（`tmp/measure_lookback.py` の昇格）

## `tmp/measure_lookback.py` との違い（意図的な変更。テスト困難な設計の記録）

`tmp/measure_lookback.py` は 5-2c 時点（5-3b の再設計より前）に「ゼロから
再計算する場合に必要な履歴本数」を、`state=None`（生価格のみ・全期間計算と
同じ経路）で `tail(L)` しながら実測した。

5-3b の再設計で `INDICATOR_COLUMN_REGISTRY.lookback` の**意味そのものが変わった**
（`incremental_state_registry.py` モジュール docstring 参照）。RECURSIVE 型列
（`ema_*`/`rs_value_eN`/`rs_roc_ema_N` 等）の `lookback` は「前日の保存値
（`prev_self`）から継ぐ前提での、当日分だけの追加入力本数」であり、
5-2c が実測した「ゼロから再構築する場合の本数」（例: `rs_roc_ema_200` なら
旧実測で511本）とは別物になっている（新しい登録値は15本）。

したがって `tmp/measure_lookback.py` の手法（`state=None` で `tail(lookback)` 本
だけ渡す）をそのまま現在のレジストリ値に対して適用すると、**RECURSIVE 型列は
定義上ほぼ必ず不一致になる**（前日値を継がずゼロ再計算するので、そもそも
`lookback` が想定している前提と矛盾する）。これは実装のバグではなく、
測定手法が現在のレジストリ定義と噛み合っていないだけである。

さらに WINDOW 型列も、`inputs` に他の T3 列（生価格ではない列）を参照する場合
（例: `rs_momentum_e200` は `rs_roc_ema_200` を、`rs_roc_ema_200` は
`rs_ratio_e200` を参照する）、**その列単独の `lookback` だけを満たしても
不十分**なことがある。`calc_relative_strength` 等は `state` の値に関わらず
WINDOW 型列を毎回 `.rolling()` で該当列の**全行**を再計算するため
（`rs_ratio_eN`・`rs_momentum_eN` 等。`relative_strength.py` の
`_rolling_std_independent_kernel` 参照）、ある列の正しさは「その列自身の
`lookback` 行」だけでなく「参照先の列が、参照される全ての行で正しく
計算できているか」に連鎖的に依存する。本番の T3 ワーカー（5-6）は
**全列に共通の1つの `K = max_lookback()` で履歴を読む**設計（5-4b）のため、
「列ごとに異なる `K` を個別に渡す」検証は本番の挙動を表さない
（実際に試すと、例えば `rs_roc_ema_200`（宣言 lookback=15）を単独で
15行だけの供給窓で検証すると、その内部で参照する `rs_ratio_e200`
（宣言 lookback=200）が15行の窓の中では正しく計算できず、確実に失敗する）。

## 本テストが検証すること

上記の理由により、本テストは「**`max_lookback()`（マージン無し）を全列共通の
供給本数として使ったとき、全履歴で計算した値と一致するか**」を検証する。
これは実際に5-6のT3ワーカーが使う経路（5-4b の増分呼び出し契約）そのものであり、
`test_calculate_incremental_equivalence.py`（`MARGIN=10` 付き）の
**マージン無し版**にあたる。同ファイルのコメントには
「理論上は margin=0 でも足りるはずだが、境界の丸め誤差を避けるための保守的な
余裕」とあり、本テストはその主張自体を固定する。

## zb_ssl / zb_bsl / is_zone_break_bull / is_zone_break_weak の扱い

この4列は必要履歴が原理的に非有界（トレンドレッグの長さに上限が無いため。
`doc/backend_specification.md` §3.4、`incremental_state_registry.py` の
`ZONE_BREAK_LOOKBACK` docstring 参照）で、`max_lookback()` を含むどんな
有限本数でも全履歴との厳密一致を保証できない（5-4c 実測: 300銘柄中2〜3銘柄で
2,400本でも収束しない）。この性質を持つこと自体は**除外ではなく、
レジストリに「非有界」と明記された既知の例外として記録されていること**を
別テストで確認する（数値的な厳密一致は求めない）。
"""
import numpy as np
import pandas as pd

from indicators.calculate import calculate_indicators
from indicators.incremental_state_registry import INDICATOR_COLUMN_REGISTRY, max_lookback

# zone_break 系4列: 必要履歴が原理的に非有界なため、本ファイルの厳密一致テストの対象外
# （doc/in_progress/t3_incremental_plan.md §7「5-4c」「5-4e」）。
_ZONE_BREAK_EXCEPTION_COLUMNS = frozenset({
    'zb_ssl', 'zb_bsl', 'is_zone_break_bull', 'is_zone_break_weak',
})

# calculate_indicators が内部で SPY とマージして作る列。T3 の実列ではないため、
# 「供給済み履歴」として渡す history から除く
# （test_calculate_incremental_equivalence.py と同じ理由）。
_NON_PERSISTED_MERGE_COLUMNS = ['spy_close', 'spy_volume']
_RAW_PRICE_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume']


def _make_series(n: int, seed: int, base: float, vol: float, spike_every: int = 45) -> pd.DataFrame:
    """合成 OHLCV 系列を作る（`test_calculate_incremental_equivalence.py` と同じ狙い）。"""
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.normal(0.03, vol, n))
    close = base + walk
    for i in range(spike_every, n, spike_every):
        close[i:] += rng.normal(0, vol * 8)
    close = np.abs(close) + 10.0
    high = close + np.abs(rng.normal(1.0, 0.3, n))
    low = np.minimum(close - np.abs(rng.normal(1.0, 0.3, n)), close - 0.01)
    openp = close + rng.normal(0, 0.2, n)
    volume = rng.integers(100_000, 900_000, n).astype(float)
    return pd.DataFrame({
        'open': openp, 'high': high, 'low': low, 'close': close, 'volume': volume,
    })


def _is_nan_like(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v))


def _find_mismatches(columns, row_a: pd.Series, row_b: pd.Series):
    """2つの行を指定列だけ rtol=atol=1e-9 で突き合わせ、不一致を列挙する。"""
    mismatches = []
    for col in columns:
        a, b = row_a[col], row_b[col]
        a_nan, b_nan = _is_nan_like(a), _is_nan_like(b)
        if a_nan and b_nan:
            continue
        if a_nan != b_nan:
            mismatches.append((col, a, b, 'NaN不一致'))
            continue
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            if a != b:
                mismatches.append((col, a, b, '非数値の不一致'))
            continue
        if not np.isclose(af, bf, rtol=1e-9, atol=1e-9):
            mismatches.append((col, af, bf, 'rtol/atol=1e-9で不一致'))
    return mismatches


class TestMaxLookbackWithoutMarginIsSufficient:
    """`max_lookback()`（マージン無し）が全列（zone_break系4列を除く）に十分であることの固定テスト。"""

    N_TOTAL = 1400  # max_lookback()(≈400) + 十分settleした位置を確保できる長さ

    def _build(self):
        n_total = self.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date

        px = _make_series(n_total, seed=31, base=90.0, vol=1.3)
        df_full = pd.DataFrame({'date': dates}).join(px)
        spy_px = _make_series(n_total, seed=42, base=250.0, vol=1.6)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)

        full_res = calculate_indicators(df_full, spy_full, state=None)

        k = max_lookback()  # マージン無し（これが本テストの核心）
        k_total = k + 1
        state_idx = n_total - k_total
        assert state_idx > 500, 'oracle が十分settleした位置から取れるよう全履歴を長くしてください'

        history = (
            full_res.iloc[state_idx: state_idx + k]
            .drop(columns=_NON_PERSISTED_MERGE_COLUMNS, errors='ignore')
            .reset_index(drop=True)
        )
        new_row_raw = df_full.iloc[[state_idx + k]][_RAW_PRICE_COLUMNS].reset_index(drop=True)
        df_inc = pd.concat([history, new_row_raw], ignore_index=True, sort=False)
        spy_inc = spy_full.iloc[state_idx: state_idx + k_total].reset_index(drop=True)

        got_res = calculate_indicators(df_inc, spy_inc, state=True)
        ref_row = full_res.iloc[state_idx + k]
        return ref_row, got_res.iloc[-1]

    def test_zone_break系を除く全列がmax_lookbackちょうどで全履歴計算と一致する(self):
        ref_row, got_row = self._build()
        columns = [
            name for name in INDICATOR_COLUMN_REGISTRY
            if name not in _ZONE_BREAK_EXCEPTION_COLUMNS
        ]
        mismatches = _find_mismatches(columns, ref_row, got_row)

        assert not mismatches, (
            f'{len(mismatches)}列で max_lookback()(マージン無し) では '
            '全履歴計算と一致しませんでした（lookback不足の可能性）:\n'
            + '\n'.join(f'  {c}: ref={a!r} got={b!r} ({reason})' for c, a, b, reason in mismatches)
        )


class TestZoneBreakLookbackIsDocumentedAsUnbounded:
    """zone_break系4列が「必要履歴が非有界な既知の例外」として記録されていることの確認。

    数値的な厳密一致（`max_lookback()` で全履歴と一致するか）は求めない
    （5-4c/5-4e の実測により原理的に達成不能と判明済み）。代わりに、
    この性質がレジストリの `note` に明記され、意図的な例外として扱われている
    ことを固定する（黙って除外されているのではないことの担保）。
    """

    def test_zone_break系4列は非有界である旨がnoteに記録されている(self):
        for name in sorted(_ZONE_BREAK_EXCEPTION_COLUMNS):
            spec = INDICATOR_COLUMN_REGISTRY[name]
            assert '非有界' in spec.note, (
                f'{name}: 必要履歴が非有界であることが note に記録されていません。\n'
                '意図的な例外であることが読み取れるよう note を更新してください。'
            )

    def test_zone_break系4列はlookbackが有限値として設定されている(self):
        """非有界であっても lookback フィールド自体は近似解として有限値が入っていること
        （None のまま放置されていないこと。max_lookback() の算出に必要）。"""
        for name in sorted(_ZONE_BREAK_EXCEPTION_COLUMNS):
            spec = INDICATOR_COLUMN_REGISTRY[name]
            assert spec.lookback is not None and spec.lookback >= 1
