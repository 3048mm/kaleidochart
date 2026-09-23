"""calculate_indicators の増分計算(state)と全期間計算の等価性テスト（TDD）。

計画書: doc/in_progress/t3_incremental_plan.md §3.1, §3.3, 5-4, 5-4b, 5-5

固定する点:
1. `state=None` のときの出力が現行実装と完全に一致すること
   （5-4 の絶対条件。`backend/tests/indicators` の既存テスト群が全期間計算の
   出力を保護しているため、本ファイルでは重複させない）
2. 「増分1歩の結果 == 全期間再計算の最終行」（63列を比較する。NaN 同士は
   一致とみなす。分類を誤った列や、供給履歴の配線を誤った列があれば、
   このテストが検出する）
3. `zb_ssl`/`zb_bsl`/`is_zone_break_bull`/`is_zone_break_weak` の4列は
   上記2とは**別の判定基準**を使う（5-4c）。詳細は下記「zb_* の判定基準について」。

## zb_* の判定基準について（5-4c・5-4e で見直し）

`zone_break` の内部状態は確定した反転（BOS）ごとにリセットされるが、
トレンドレッグの長さに上限が無い（移植元 Pine Script の性質）ため、
必要履歴本数が**原理的に非有界**（300銘柄実測: 中央値120本・p99 250本・
最大2,400本、250本超が2〜3/300銘柄）。そのため `zb_ssl`/`zb_bsl`/
`is_zone_break_bull`/`is_zone_break_weak` の4列は「全履歴との厳密一致」を
達成できない（これは増分化が新たに生じさせた問題ではなく、現行実装も
SQLite の504行だけで日次計算しているため同じ制約を持つ。約1%の銘柄で
全履歴計算と値が異なりうる。詳細: doc/backend_specification.md、
doc/in_progress/t3_incremental_plan.md §8）。

このため、この4列は**比較対象を「全履歴で計算した値」から
「`ZONE_BREAK_LOOKBACK`（400本。5-4e）だけで全期間計算した値」に差し替える**
（除外はしない）。5-4c 版は `HOT_WINDOW_BARS`（504本＝SQLiteの保持行数）を
そのまま使っていたが、これは `max_lookback()+1` が保持行数を1行超えマージンが
ゼロだった。5-4e でユーザー判断により `ZONE_BREAK_LOOKBACK`（400。
`incremental_state_registry.py` 参照）に見直し、SQLiteの保持行数に対して
約100行のマージンを確保した。400と600〜2400の間で精度が変わらないと
実測で確認済みのため、比較対象を400本に変えても検出力は変わらない。
これは「現行実装と同等（回帰が無い）」ことの確認であり、「厳密解である」
ことの確認ではない。他の63列との厳密一致テストとは判定基準そのものが
異なるため、別のテストメソッドに分離している
（`test_zone_break系4列はホットウィンドウでの全期間計算と一致する`）。

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
from indicators.incremental_state_registry import ZONE_BREAK_LOOKBACK, max_lookback


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


def _find_mismatches(columns, row_a: pd.Series, row_b: pd.Series):
    """2つの行（Series）を指定列だけ rtol=atol=1e-9 で突き合わせ、不一致を列挙する。

    複数のテストメソッド（63列の全期間一致・zb_*のホットウィンドウ一致）で
    比較ロジックを共有するための共通ヘルパー。
    """
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
        if not math.isclose(af, bf, rel_tol=1e-9, abs_tol=1e-9):
            mismatches.append((col, af, bf, 'rtol/atol=1e-9で不一致'))
    return mismatches


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

    def _build_full_and_incremental(self, k=None, state_idx=None):
        """全期間計算オラクルと増分計算の結果を作る。

        Args:
            k: 供給する履歴本数。None（既定）なら `max_lookback() + MARGIN`
                （63列の厳密一致テスト用）。zb_* の「ホットウィンドウでの
                全期間計算」との一致を見る専用テスト（5-4c・5-4e）では
                `k=ZONE_BREAK_LOOKBACK` を明示的に渡す。
            state_idx: 供給履歴の開始位置（全履歴中のインデックス）。None（既定）なら
                「十分に settle した位置」（末尾から k_total 本）を自動算出する。
                供給履歴にその銘柄自身の窓の先頭（atr_pct_14 の立ち上がりゼロ等）を
                含めたいテスト（5-4d）では `state_idx=0` を明示的に渡す。
        """
        n_total = self.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date

        px = _make_series(n_total, seed=1, base=100.0, vol=1.2)
        df_full = pd.DataFrame({'date': dates}).join(px)

        spy_px = _make_series(n_total, seed=2, base=300.0, vol=2.0)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)

        # 正: 全履歴を state=None（全期間計算）で計算
        full_res = calculate_indicators(df_full, spy_full, state=None)

        # K はレジストリの lookback 最大値から導出する（ハードコードしない）。
        if k is None:
            k = max_lookback() + self.MARGIN
        k_total = k + 1  # 供給履歴K本 + 新規計算する最終行1本
        if state_idx is None:
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

        # 正の参照行は `full_res.iloc[state_idx + k]`（=増分側の新規計算対象と同じ日付）。
        # state_idx を既定（末尾settle位置）で使う限り常に `iloc[-1]` と同じだが、
        # 5-4d のように state_idx=0 を明示指定するケースでは異なるため、
        # `-1` 固定ではなく必ずこちらを使う。
        ref_row = full_res.iloc[state_idx + k]
        return ref_row, got_res.iloc[-1], full_res, k_total, df_full, spy_full

    def test_dot_lighting_events_exist_in_window(self):
        """自己修復性の前提（テストデータの健全性チェック）。

        `rs_blue_dot_age`/`rs_red_dot_age` は state 継続時に warmup ガードを
        バイパスする設計（`_rs_dot_age_kernel` の `use_state` 引数を参照）に
        なっている。これが正しく機能していることは、増分ウィンドウ内にドット
        点灯（age=0）が実際に発生するテストデータでなければ検証できない
        （点灯が一度も無いと age は sentinel に飽和したまま何を見ても
        変わらず、テストとして無意味になる）。
        """
        _, _, full_res, k_total, _, _ = self._build_full_and_incremental()
        window = full_res.iloc[-k_total:]
        blue_lightings = int((window['rs_blue_dot_age'] == 0).sum())
        red_lightings = int((window['rs_red_dot_age'] == 0).sum())
        assert blue_lightings > 0 or red_lightings > 0, (
            '増分ウィンドウ内にRSドット点灯が一度も無いテストデータでは '
            'rs_blue_dot_age/rs_red_dot_age の warmup バイパス設計を検証できない'
        )

    # 5-4c: zone_break系4列は必要履歴が原理的に非有界なため、このテストの判定基準
    # （全履歴で計算した値との厳密一致）を満たせない。除外はせず、判定基準そのものを
    # 変えた別テスト（test_zone_break系4列はホットウィンドウでの全期間計算と一致する）
    # で比較する（モジュール docstring「zb_* の判定基準について」参照）。
    ZONE_BREAK_HOT_WINDOW_COLUMNS = (
        'zb_ssl', 'zb_bsl', 'is_zone_break_bull', 'is_zone_break_weak',
    )

    def test_incremental_matches_full_recompute_for_all_columns(self):
        """zb_*以外の63列が rtol=atol=1e-9 で厳密一致すること（5-4bで未収束4列の除外を撤廃）。

        zone_break系4列（ZONE_BREAK_HOT_WINDOW_COLUMNS）は判定基準が異なるため、
        この厳密一致テストの対象から除く（比較しないのではなく、比較先を変えて
        別テストで検証する。5-4c）。
        """
        ref_row, got_row, _, _, _, _ = self._build_full_and_incremental()

        columns = [
            col for col in ref_row.index
            if col != 'date' and col not in self.ZONE_BREAK_HOT_WINDOW_COLUMNS
        ]
        mismatches = _find_mismatches(columns, ref_row, got_row)

        assert not mismatches, (
            f'{len(mismatches)}列で増分計算が全期間再計算と一致しませんでした:\n'
            + '\n'.join(f'  {c}: ref={a!r} got={b!r} ({reason})' for c, a, b, reason in mismatches)
        )


class TestZoneBreakHotWindowEquivalence:
    """zb_ssl/zb_bsl/is_zone_break_bull/is_zone_break_weak 専用の等価性テスト（5-4c・5-4e）。

    `TestIncrementalMatchesFullRecompute` は「全履歴で計算した値」との厳密一致を
    正としているが、zone_break系4列は必要履歴が原理的に非有界（モジュール
    docstring「zb_* の判定基準について」参照）なため、それを満たせない。
    この4列だけは判定基準を「`ZONE_BREAK_LOOKBACK`（400本。5-4e）だけで
    全期間計算した値」に差し替え、増分呼び出しがそれと一致すること
    （＝実際にT3ワーカーが供給する窓での計算との同等性・回帰が無いこと）を確認する。
    5-4c 版は `HOT_WINDOW_BARS`（504本）を使っていたが、5-4e でレジストリの
    lookback を見直した（ZONE_BREAK_LOOKBACK docstring 参照）ため、比較窓も
    実際にレジストリが宣言する値に揃えている。

    `zone_break_series`/`structure_pivot_series`（backend/indicators/calculate.py）
    は `state` の値に関わらず df の生価格列だけから無条件に計算されるため、
    「同じ生価格ウィンドウに対する増分呼び出し(state=True)」と
    「同じ生価格ウィンドウに対する全期間計算(state=None)」は実装上常に一致する。
    本テストはこの契約——「zb_*の増分結果は、供給したウィンドウの生価格だけで
    決まる」——を固定する回帰テストである（将来この4列の計算方法が変わって
    契約が崩れたとき、または供給ウィンドウの長さがずれたときに検出する）。
    """

    def test_zone_break系4列はホットウィンドウでの全期間計算と一致する(self):
        base = TestIncrementalMatchesFullRecompute()
        _, got_row, _, k_total, df_full, spy_full = base._build_full_and_incremental(k=ZONE_BREAK_LOOKBACK)
        assert k_total == ZONE_BREAK_LOOKBACK + 1

        # 「実際にT3ワーカーが供給する窓」＝ ZONE_BREAK_LOOKBACK(400) 本の生価格だけを使い、
        # 毎回ゼロから計算する（state=None）。増分呼び出しが使ったのと同じ末尾の
        # 生価格ウィンドウに揃える。
        df_hot = df_full.tail(k_total).reset_index(drop=True)
        spy_hot = spy_full.tail(k_total).reset_index(drop=True)
        hot_row = calculate_indicators(df_hot, spy_hot, state=None).iloc[-1]

        mismatches = _find_mismatches(
            TestIncrementalMatchesFullRecompute.ZONE_BREAK_HOT_WINDOW_COLUMNS, hot_row, got_row,
        )

        assert not mismatches, (
            f'{len(mismatches)}列で増分計算がホットウィンドウでの全期間計算と一致しませんでした:\n'
            + '\n'.join(f'  {c}: hot={a!r} got={b!r} ({reason})' for c, a, b, reason in mismatches)
        )


class TestSuppliedHistoryDtypeRegression:
    """5-4d: 増分呼び出しが実データ（SQLite経由）で `ZeroDivisionError` になっていた
    不具合の回帰テスト。

    ## 前任者のテスト（TestIncrementalMatchesFullRecompute）が検出できなかった理由

    そちらは `state_idx > 500`（十分に settle した位置）からしか供給履歴を切り出して
    おらず、その銘柄自身の「窓の先頭」（`atr_14` の Wilder 平滑化がリテラル 0.0 で
    始まる区間、`_atr_wilder_kernel` のシード方式。先頭 `window-1`=13 本）を
    含んでいなかった。実運用では、増分呼び出しは SQLite が保持する履歴
    （最大504本）を供給履歴として使うため、**必ずその銘柄自身の窓の先頭を含む**
    （SQLite は全履歴を持たず、保持している最古の行が供給履歴の先頭になる）。

    ## 合成データが「現実的」である理由

    `calculate_indicators(state=None)` の戻り値は末尾（`calculate.py` の
    `df.replace({np.nan: None})`）で NaN を None に変換しており、これは
    **同じ DataFrame 内の他の列に NaN があれば、当該列自体に NaN が無くても
    ほぼ全列が object dtype になる**（pandas の仕様。実測: 67列中58列、
    生価格の open/high/low/close/volume すら object になる）。本テストは
    合成データではなくこの「実際に calculate_indicators が返す戻り値」を
    そのまま増分呼び出しの供給履歴として使うため、実データ（SQLite経由）と
    同じ dtype 汚染を再現する。
    """

    def test_supplied_history_becomes_object_dtype_by_default(self):
        """前提確認（回帰の記録）: calculate_indicators(state=None) の戻り値は
        大半の列が object dtype になり、atr_pct_14 の先頭13本はNaNになる
        （min_periods_warmup計画 5-4b: Wilder平滑化シード方式でも
        window-1本に満たない先頭区間はリテラル0ではなくNaNで埋める）。
        """
        base = TestIncrementalMatchesFullRecompute()
        n_total = base.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date
        px = _make_series(n_total, seed=1, base=100.0, vol=1.2)
        df_full = pd.DataFrame({'date': dates}).join(px)
        spy_px = _make_series(n_total, seed=2, base=300.0, vol=2.0)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)

        full_res = calculate_indicators(df_full, spy_full, state=None)

        assert full_res['atr_pct_14'].dtype == object, (
            '前提: atr_pct_14 が object dtype になること'
            '（同じ df 内の他列の NaN に引きずられる既存の pandas 挙動）'
        )
        assert full_res['atr_pct_14'].iloc[:13].isna().all(), (
            '前提: atr_14 の Wilder 平滑化シード方式により先頭13本がNaNになること'
        )

    def test_incremental_does_not_raise_zero_division_when_history_includes_symbol_start(self):
        """供給履歴が銘柄自身の窓の先頭（atr_pct_14=0.0 の立ち上がり区間、
        かつ object dtype）を含んでも ZeroDivisionError にならないこと（5-4d の回帰テスト）。

        `TestIncrementalMatchesFullRecompute._build_full_and_incremental` を
        `state_idx=0` で呼び出す（＝供給履歴の先頭を全履歴の先頭に固定する）ことで、
        SQLite の保持行数制約下で実際に発生していた状況を再現する。
        """
        base = TestIncrementalMatchesFullRecompute()
        ref_row, got_row, full_res, k_total, df_full, spy_full = (
            base._build_full_and_incremental(state_idx=0)
        )

        # 供給履歴（行0..K-1）が実際に object dtype ＋ atr_pct_14=NaN（先頭13本）を
        # 含んでいることを確認する（このテストが「合成データで再現できていない」状態に
        # 静かに劣化するのを防ぐ）。
        history_atr_pct_14 = full_res['atr_pct_14'].iloc[0: k_total - 1]
        assert history_atr_pct_14.dtype == object
        assert history_atr_pct_14.iloc[:13].isna().all()

        # ZeroDivisionError が送出されないこと自体がこのテストの主目的。
        # 加えて、増分1歩の結果が全期間再計算の最終行と一致すること
        # （zb_*以外。5-5と同じ判定基準）も確認する。
        columns = [
            col for col in ref_row.index
            if col != 'date'
            and col not in TestIncrementalMatchesFullRecompute.ZONE_BREAK_HOT_WINDOW_COLUMNS
        ]
        mismatches = _find_mismatches(columns, ref_row, got_row)
        assert not mismatches, (
            f'{len(mismatches)}列で増分計算が全期間再計算と一致しませんでした:\n'
            + '\n'.join(f'  {c}: ref={a!r} got={b!r} ({reason})' for c, a, b, reason in mismatches)
        )

    def test_incremental_does_not_raise_zero_division_with_explicitly_object_dtype_input(self):
        """供給列が明示的に object dtype のケース（5-4d が要求する2つ目のケース）。

        T3ワーカーがSQLiteから読んだ結果、境界条件により一部列だけがobject dtype
        になる場合（列ごとにNaNの有無が異なる等）も含めて、`.astype(object)` で
        意図的にすべての列をobject化した入力でも例外が出ないことを確認する。
        """
        base = TestIncrementalMatchesFullRecompute()
        n_total = base.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date
        px = _make_series(n_total, seed=1, base=100.0, vol=1.2)
        df_full = pd.DataFrame({'date': dates}).join(px)
        spy_px = _make_series(n_total, seed=2, base=300.0, vol=2.0)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)
        full_res = calculate_indicators(df_full, spy_full, state=None)

        k = max_lookback() + base.MARGIN
        k_total = k + 1
        state_idx = 0

        history = (
            full_res.iloc[state_idx: state_idx + k]
            .drop(columns=_NON_PERSISTED_MERGE_COLUMNS, errors='ignore')
            .reset_index(drop=True)
            .astype(object)  # 明示的に全列をobject dtype化する
        )
        new_row_raw = df_full.iloc[[state_idx + k]][_RAW_PRICE_COLUMNS].reset_index(drop=True)
        df_inc = pd.concat([history, new_row_raw], ignore_index=True, sort=False)
        spy_inc = spy_full.iloc[state_idx: state_idx + k_total].reset_index(drop=True)

        # ZeroDivisionError が出ないことがこのテストの主目的。
        got_res = calculate_indicators(df_inc, spy_inc, state=True)
        assert not got_res.empty

    def test_incremental_does_not_raise_when_supplied_rows_are_fewer_than_max_lookback_plus_one(self):
        """供給行数が `max_lookback() + 1` に満たなくてもクラッシュしないこと。

        5-4d 時点では `max_lookback()`（当時は `HOT_WINDOW_BARS` = 504 と同値。
        zone_break系4列の lookback だった）が SQLite ホットキャッシュの保持行数
        上限と一致していたため、T3ワーカーが「K+1行ちょうど」を要求すると
        SQLiteの保持行数では1本足りない、という実運用シナリオが存在した。
        5-4e で zone_break系4列の lookback を `ZONE_BREAK_LOOKBACK`（400）に
        下げたことで `max_lookback()+1`（401）は `HOT_WINDOW_BARS`（504）に
        対して約100行のマージンを持つようになり、このシナリオ自体は基本的に
        発生しなくなった。

        それでも「供給行数が要求に満たない場合でもクラッシュしない」という
        防御的な性質自体は、新規上場銘柄（保有履歴がそもそも少ない）など
        別の理由でも成立してほしい普遍的な要件のため、`max_lookback()` を
        意図的に1本下回る行数を供給してテストを維持する
        （5-4d で確認。WINDOW型は `min_periods` により行数不足でも例外にならず、
        RECURSIVE型は `prev_self_seed` が `len(df) >= 2` しか要求しないため、
        設計上は安全なはずだが、将来の変更でこの前提が壊れていないかを検出する）。
        """
        base = TestIncrementalMatchesFullRecompute()
        n_total = base.N_TOTAL
        dates = pd.bdate_range('2019-01-02', periods=n_total).date
        px = _make_series(n_total, seed=1, base=100.0, vol=1.2)
        df_full = pd.DataFrame({'date': dates}).join(px)
        spy_px = _make_series(n_total, seed=2, base=300.0, vol=2.0)
        spy_full = pd.DataFrame({'date': dates}).join(spy_px)
        full_res = calculate_indicators(df_full, spy_full, state=None)

        # `max_lookback() + 1` にちょうど1本足りない供給行数を意図的に作る
        # （HOT_WINDOW_BARS には依存しない。5-4e でマージンが生まれたため）。
        supplied_total = max_lookback()
        assert supplied_total < max_lookback() + 1
        history = (
            full_res.iloc[-supplied_total:-1]
            .drop(columns=_NON_PERSISTED_MERGE_COLUMNS, errors='ignore')
            .reset_index(drop=True)
        )
        new_row_raw = df_full.iloc[[-1]][_RAW_PRICE_COLUMNS].reset_index(drop=True)
        df_inc = pd.concat([history, new_row_raw], ignore_index=True, sort=False)
        spy_inc = spy_full.iloc[-supplied_total:].reset_index(drop=True)
        assert len(df_inc) == supplied_total

        got_res = calculate_indicators(df_inc, spy_inc, state=True)
        assert not got_res.empty
        assert len(got_res) == supplied_total
