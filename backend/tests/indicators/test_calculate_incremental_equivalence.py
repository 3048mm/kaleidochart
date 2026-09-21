"""calculate_indicators の増分計算(state)と全期間計算の等価性テスト（TDD）。

計画書: doc/in_progress/t3_incremental_plan.md §3.1, §3.3, 5-4, 5-4b, 5-5

固定する点:
1. `state=None` のときの出力が現行実装と完全に一致すること
   （5-4 の絶対条件。`backend/tests/indicators` の既存テスト群が全期間計算の
   出力を保護しているため、本ファイルでは重複させない）
2. 「増分1歩の結果 == 全期間再計算の最終行」（全67列を比較する。NaN 同士は
   一致とみなす。分類を誤った列や、供給履歴の配線を誤った列があれば、
   このテストが検出する）

## 増分呼び出しの入力契約（5-4b。本ファイルが検証する設計）

5-4/5-5 版は「生価格 K+1 本」＋コンテキスト行1本分のスカラー state（RECURSIVE
型列の前日値のみ）を入力とし、RECURSIVE 型列を毎回コンテキスト行から窓全体
（K本）を再帰的に歩き直していた。この設計では WINDOW 型列（`rs_ratio_eN` 等）
が増分ウィンドウ内でしか rolling 窓を作れず、窓が育つまでの区間（最大 N-1
行）が不正確な値になり、それが `roc`（14日ROC）経由で `rs_roc_ema_N` の
再帰入力に混入すると、α の小さい列（N=200 で約0.01）では窓を伸ばしても
汚染が解消しきらなかった（`rs_roc_ema_63`/`rs_momentum_e63`/
`rs_roc_ema_200`/`rs_momentum_e200` の4列が未収束のまま残っていた）。

5-4b は入力契約を「生価格 K+1 本」から「生価格 K+1 本 ＋ 保存済み T3
中間列 K 本」に拡張して解決する:

- `df`（生価格）＝ 全履歴の直近 `K+1` 本。生の価格列は全行に値がある
- **同じ `df` に、保存済み T3 列（`INDICATOR_COLUMN_REGISTRY` の全列）が
  行 0..K-1 に実値として入っている。最終行（K）だけが NaN**（＝これから
  計算する日。raw price 列のみを持つ行として供給する）
- `K = max_lookback()`（+余裕）。5-4b の設計では RECURSIVE 型列は
  「供給された行K-1の値をシードに最終行だけ計算」、WINDOW 型列は
  「マージ済みの実値だけに依存する通常の計算」になるため、収束を待つ必要が
  なくなり、レジストリの宣言する lookback だけで厳密一致する
  （詳細: `backend/indicators/incremental_merge.py` モジュール docstring）
- `state=True`（真偽フラグ）を渡すことで増分モードを有効にする。個々の
  RECURSIVE 型列の前日シードは、外部から辞書で渡すのではなく、関数が
  df 自身の供給済み履歴（最終行の1つ前の行）から機械的に導出する
"""
import math

import numpy as np
import pandas as pd

from indicators.calculate import calculate_indicators
from indicators.incremental_state_registry import max_lookback


def _make_series(n: int, seed: int, base: float, vol: float, spike_every: int = 45):
    """合成 OHLCV 系列を作る。

    RS ドット点灯・TD9 の反転など「自己修復が必要な離散シグナル」が
    十分な頻度で発生するよう、定期的にスパイクを混ぜる（1000本以上、
    値が変化し続ける現実的な系列という 5-5 の要求）。
    """
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.normal(0.03, vol, n))
    close = base + walk
    for i in range(spike_every, n, spike_every):
        close[i:] += rng.normal(0, vol * 8)
    close = np.abs(close) + 10.0  # 正値保証
    high = close + np.abs(rng.normal(1.0, 0.3, n))
    low = close - np.abs(rng.normal(1.0, 0.3, n))
    low = np.minimum(low, close - 0.01)
    openp = close + rng.normal(0, 0.2, n)
    volume = rng.integers(100_000, 900_000, n).astype(float)
    return pd.DataFrame({
        'open': openp, 'high': high, 'low': low, 'close': close, 'volume': volume,
    })


def _is_nan_like(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


# calculate_indicators が内部で SPY とマージして作る列。T3 の実列（DBに保存される列）
# ではないため、「供給済み履歴」として渡す history から除く（5-4b の入力契約は
# INDICATOR_COLUMN_REGISTRY の列のみを供給する。SPY 側の生価格は df_spy として
# 別途・独立に渡すのが本来の契約）。
_NON_PERSISTED_MERGE_COLUMNS = ['spy_close', 'spy_volume']

_RAW_PRICE_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume']


class TestIncrementalMatchesFullRecompute:
    """「増分1歩の結果 == 全期間再計算の最終行」の等価性テスト（計画書 5-5、5-4bで再設計）。"""

    N_TOTAL = 1400  # 1,000本以上・値が変化し続ける現実的な系列（5-5 の要求）
    MARGIN = 10  # max_lookback() ちょうどでも理論上は足りるが、境界の丸め誤差を避ける余裕

    def _build_full_and_incremental(self):
        n_total = self.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date

        px = _make_series(n_total, seed=1, base=100.0, vol=1.2)
        df_full = pd.DataFrame({'date': dates}).join(px)

        spy_px = _make_series(n_total, seed=2, base=300.0, vol=2.0)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)

        # 正: 全履歴を state=None（全期間計算）で計算
        full_res = calculate_indicators(df_full, spy_full, state=None)

        # K はレジストリの lookback 最大値から導出する（ハードコードしない）。
        k = max_lookback() + self.MARGIN
        k_total = k + 1  # 供給履歴K本 + 新規計算する最終行1本
        state_idx = n_total - k_total
        assert state_idx > 500, 'oracle 自体が十分に settle した位置から取れるよう、全履歴を長くしてください'

        # 供給する履歴（行0..K-1）: 生価格 + 保存済みT3列（すべて実値）。
        # spy_close/spy_volume は T3 の実列ではない（calc_relative_strength が
        # df_spy から都度導出する）ため除く。
        history = (
            full_res.iloc[state_idx: state_idx + k]
            .drop(columns=_NON_PERSISTED_MERGE_COLUMNS, errors='ignore')
            .reset_index(drop=True)
        )
        # 新規に計算する最終行（行K）: 生価格のみ（T3列は無い＝concat後にNaNになる）。
        new_row_raw = df_full.iloc[[state_idx + k]][_RAW_PRICE_COLUMNS].reset_index(drop=True)
        df_inc = pd.concat([history, new_row_raw], ignore_index=True, sort=False)

        spy_inc = spy_full.iloc[state_idx: state_idx + k_total].reset_index(drop=True)

        # 被験: 増分モード（state=True）。K+1本のうち最終行だけを新規計算する。
        got_res = calculate_indicators(df_inc, spy_inc, state=True)

        return full_res.iloc[-1], got_res.iloc[-1], full_res, k_total

    def test_dot_lighting_events_exist_in_window(self):
        """自己修復性の前提（テストデータの健全性チェック）。

        `rs_blue_dot_age`/`rs_red_dot_age` は state 継続時に warmup ガードを
        バイパスする設計（`_rs_dot_age_kernel` の `use_state` 引数を参照）に
        なっている。これが正しく機能していることは、増分ウィンドウ内にドット
        点灯（age=0）が実際に発生するテストデータでなければ検証できない
        （点灯が一度も無いと age は sentinel に飽和したまま何を見ても
        変わらず、テストとして無意味になる）。
        """
        _, _, full_res, k_total = self._build_full_and_incremental()
        window = full_res.iloc[-k_total:]
        blue_lightings = int((window['rs_blue_dot_age'] == 0).sum())
        red_lightings = int((window['rs_red_dot_age'] == 0).sum())
        assert blue_lightings > 0 or red_lightings > 0, (
            '増分ウィンドウ内にRSドット点灯が一度も無いテストデータでは '
            'rs_blue_dot_age/rs_red_dot_age の warmup バイパス設計を検証できない'
        )

    def test_incremental_matches_full_recompute_for_all_columns(self):
        """全67列が rtol=atol=1e-9 で厳密一致すること（5-4bで未収束4列の除外を撤廃）。"""
        ref_row, got_row, _, _ = self._build_full_and_incremental()

        mismatches = []
        for col in ref_row.index:
            if col == 'date':
                continue
            a, b = ref_row[col], got_row[col]
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
            if not math.isclose(af, bf, rel_tol=1e-9, abs_tol=1e-9):
                mismatches.append((col, af, bf, 'rtol/atol=1e-9で不一致'))

        assert not mismatches, (
            f'{len(mismatches)}列で増分計算が全期間再計算と一致しませんでした:\n'
            + '\n'.join(f'  {c}: ref={a!r} got={b!r} ({reason})' for c, a, b, reason in mismatches)
        )
