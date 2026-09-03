"""counter_trend_series — カウンタートレンドライン（Trend Line Break）のテスト。

作者の改良版が `rt_cnt_break` として出しているシグナルの移植。
原典（note.com の解説記事）から取れた仕様:

- ラインは**ショート側のピボット高値2点**を結ぶ
  - アンカー1: 直前の LL-HL 構造の終端までで**最も高い**ピボット高値
  - アンカー2: アンカー1 より後で、アンカー1 から見て**最も急な下向き傾き**になるピボット高値
  - 値: `y2 + m * (i - x2)`  ただし `m = (y2 - y1) / (x2 - x1)`
- **LL-HL 構造が生きていない期間にだけ**引かれる（構造が出来ると消える）
- このラインを**上抜けること**が買いシグナル

移植で壊れやすいのは LL-HL 側と同じく**確定遅延**なので、そこに専用テストを置く。
ピボット高値も左右 L 本を見る中心窓であり、`p + L` 本目まで存在を知り得ない。
"""
import numpy as np
import pytest

from indicators.structure_pivot import (counter_trend_series, find_counter_trends,
                                        pivot_strength_high, pivot_strength_low,
                                        structure_pivot_series)


# ============================================================
# pivot_strength_high — pivot_strength_low の鏡像
# ============================================================

class TestPivotStrengthHigh:

    def test_is_mirror_of_pivot_strength_low(self):
        """高値側の強度は、符号反転した系列の安値側の強度と一致する。"""
        rng = np.random.default_rng(0)
        h = rng.normal(100, 5, 200)
        assert np.array_equal(pivot_strength_high(h), pivot_strength_low(-h))

    def test_isolated_peak_has_high_strength(self):
        h = np.array([10, 11, 12, 20, 12, 11, 10], dtype=float)
        assert pivot_strength_high(h)[3] == 3

    def test_flat_series_has_zero_strength(self):
        assert pivot_strength_high(np.full(20, 50.0)).max() == 0

    def test_empty_input(self):
        assert pivot_strength_high(np.array([])).shape == (0,)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def downtrend():
    """下降トレンド（高値が切り下がる）→ 最後に上抜ける系列。

    ピボット高値を idx 5(=100) / 15(=90) / 25(=80) に置き、
    構造（LL-HL）が成立しないように安値も切り下げ続ける。
    """
    n = 60
    high = np.full(n, 60.0)
    low = np.full(n, 50.0)
    # 下向きに切り下がる3つのピーク
    for idx, peak in ((5, 100.0), (15, 90.0), (25, 80.0)):
        high[idx] = peak
        high[idx - 1] = peak - 8
        high[idx + 1] = peak - 8
    # 安値は切り下げつつジグザグにする。単調だとピボット安値が1つも出来ず、
    # limit_idx（ロング側の現在のピボット安値）が立たないためラインが引けない。
    # 谷は下がり続けるので HL は成立せず、構造は生きないまま
    idx = np.arange(n, dtype=float)
    low[:] = 58 - 0.45 * idx + 3.0 * np.sin(idx * 0.9)
    close = high - 1.0
    return high, low, close


# ============================================================
# ライン形状
# ============================================================

class TestLineGeometry:

    def test_line_descends_through_the_two_anchors(self, downtrend):
        """2アンカーを通る直線であること（傾きが負で、単調に下がる）。"""
        high, low, close = downtrend
        line = counter_trend_series(high, low, close, min_len=2, max_len=5)
        vals = line[~np.isnan(line)]
        assert len(vals) > 0, 'ラインが1本も引かれていない'
        diffs = np.diff(vals)
        assert (diffs <= 1e-9).all(), '下向きのラインになっていない'

    def test_no_line_when_there_are_fewer_than_two_pivot_highs(self):
        """ピボット高値が2つ未満ならラインは引けない。"""
        n = 30
        high = np.full(n, 60.0)
        high[10] = 100.0            # ピーク1つだけ
        high[9] = high[11] = 80.0
        low = np.linspace(58, 30, n)
        line = counter_trend_series(high, low, high - 1.0)
        assert np.isnan(line).all()

# ============================================================
# 確定遅延（最重要）
# ============================================================

class TestConfirmationLag:

    def test_pivot_high_is_not_used_before_it_is_confirmed(self, downtrend):
        """ピボット高値 p は p + min_len 本目より前のラインに影響してはいけない。"""
        high, low, close = downtrend
        min_len = 2
        line = counter_trend_series(high, low, close, min_len=min_len, max_len=5)
        # 2つ目のピボット(idx15)が確定するのは idx17。それ以前にラインは存在しない
        assert np.isnan(line[:15 + min_len]).all(), '確定前のバーにラインが出ている'

    def test_future_bars_do_not_change_past_values(self, downtrend):
        """系列を途中で切っても、切る前の値が変わらないこと（先読みしていない）。"""
        high, low, close = downtrend
        full = counter_trend_series(high, low, close)
        cut = 40
        partial = counter_trend_series(high[:cut], low[:cut], close[:cut])
        np.testing.assert_allclose(partial, full[:cut], equal_nan=True)


# ============================================================
# LL-HL 構造との排他
# ============================================================

class TestExclusiveWithStructure:

    def test_no_line_while_a_structure_is_active(self):
        """LL-HL 構造が生きている間はラインを引かない。

        系列は test_structure_pivot.py の `base_series` と同じものを使う
        （構造が確実に成立することが既存テストで担保されている）。
        """
        low = np.array([50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 40,
                        42, 44, 46, 48, 50, 48, 46, 44, 43, 42,
                        44, 46, 48, 50, 52, 54, 56, 58, 60, 62], dtype=np.float64)
        high = low + 3.0
        close = low + 1.5
        sp_pivot, _ = structure_pivot_series(high, low, close)
        line = counter_trend_series(high, low, close)
        active = ~np.isnan(sp_pivot)
        assert active.any(), 'この系列で構造が成立していない（フィクスチャの前提が崩れた）'
        assert np.isnan(line[active]).all(), '構造が生きている間にラインが出ている'


# ============================================================
# 入力の縮退
# ============================================================

class TestDegenerateInput:

    @pytest.mark.parametrize('n', [0, 1, 5])
    def test_short_input_returns_all_nan(self, n):
        a = np.arange(n, dtype=float) + 10
        line = counter_trend_series(a, a - 1, a - 0.5)
        assert line.shape == (n,)
        assert np.isnan(line).all()

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            counter_trend_series(np.zeros(10), np.zeros(9), np.zeros(10))

    def test_flat_series_has_no_line(self):
        a = np.full(50, 100.0)
        assert np.isnan(counter_trend_series(a, a, a)).all()


# ============================================================
# 履歴の長さに対する安定性（2026-08-29 の実障害の回帰テスト）


# ============================================================
# 傾きの選び方（2026-08-29 に実際に間違えた箇所）
# ============================================================

class TestSlopeSelection:
    """Pine 原文は `find_highs` のとき **`m > best_slope`** で更新する＝**最大化**。

    「最も急な下向き傾き」と読み違えて最小化で実装したところ、
    作者の公開出力（8/26 の BHVN / ERAS）が **2/2 → 0/2** になった。
    """

    def test_rising_highs_draw_no_line(self):
        """高値が切り上がり続ける局面ではラインが引けないこと。

        当初は「*カウンター*トレンド線なので下向きのはず」と考えて
        `slope < 0` を課していたが、**原文にその条件は無い**。
        条件が無くても成り立つのは、アンカー1 が `idx <= limit_idx` の**最大値**で、
        アンカー2 の候補は `a1 < idx < limit_idx` に限られるため。
        切り上がり続けると最後のピークがアンカー1 になり、その後ろに候補が無くなる。
        （＝ 傾きは構造上つねに 0 以下。符号判定は冗長だった）
        """
        n = 70
        high = np.full(n, 60.0)
        for idx_, peak in ((10, 80.0), (20, 90.0), (30, 100.0)):   # 切り上がり
            high[idx_] = peak
            high[idx_ - 1] = high[idx_ + 1] = peak - 12
        idx = np.arange(n, dtype=float)
        low = 58 - 0.45 * idx + 3.0 * np.sin(idx * 0.9)
        line = counter_trend_series(high, low, high - 1.0)
        assert np.isnan(line).all()


# ============================================================
# 履歴の長さに対する安定性（2026-08-29 の実障害の回帰テスト）
# ============================================================

class TestHistoryLengthStability:
    """**同じ状況なら、読んだ本数が違っても同じ値になること。**

    アンカーの候補は「長さごとの状態機械が持つ prev / curr の2点」だけなので、
    値は直近の数ピボットで決まり、履歴の長さに依存しない。
    途中で「窓の中の全ピボットから探す」実装にしたときは、
    本番実測で T3（SQLite 約500本）と Parquet バックフィル（全期間）が
    最終バーで 12% / 直近60本で 44% 食い違った。**この性質は落とせない。**
    """

    @staticmethod
    def _series(n, seed):
        rng = np.random.default_rng(seed)
        base = 100 + np.cumsum(rng.normal(0, 1.2, n))
        return (base + rng.random(n) * 2 + 1, base - rng.random(n) * 2 - 1, base)

    @pytest.mark.parametrize('seed', range(8))
    def test_tail_values_do_not_depend_on_how_much_history_is_loaded(self, seed):
        extra, warmup = 400, 120
        high, low, close = self._series(extra + warmup + 400, seed)
        full = counter_trend_series(high, low, close)
        cut = counter_trend_series(high[extra:], low[extra:], close[extra:])
        np.testing.assert_allclose(cut[warmup:], full[extra + warmup:],
                                   equal_nan=True, rtol=1e-9)


# ============================================================
# find_counter_trends — アンカー座標つきの線分列（チャート描画用）
# ============================================================
#
# `counter_trend_series` はバーごとのスカラーしか返さないため、線分として
# 描くのに必要な起点2点が取れない。`find_structures` ↔ `structure_pivot_series`
# と同じ二層構造を作る。
#
# **最優先の制約: 既存の値を1つも変えないこと。** `counter_trend_series` は
# 2026-08-29 に Pine 原文と照合して確定し、T3・スクリーナー・バックテストの
# 3経路が同じ値を使っている。等価性テストを先頭に置く。

class TestFindCounterTrendsEquivalence:
    """新関数が既存 series と完全に一致すること（§2.2 の最優先制約）。"""

    def test_segments_reproduce_series_values_exactly(self, downtrend):
        """各線分の区間内で、直線の値が series と厳密に一致する。"""
        high, low, close = downtrend
        line = counter_trend_series(high, low, close, min_len=2, max_len=5)
        segments = find_counter_trends(high, low, close, min_len=2, max_len=5)
        assert segments, '線分が1本も返っていない'
        for seg in segments:
            for t in range(seg.start_index, seg.end_index + 1):
                expected = seg.a2_price + seg.slope * (t - seg.a2_index)
                assert line[t] == pytest.approx(expected, abs=1e-9), (
                    f'bar {t}: series={line[t]} vs segment={expected}')

    def test_segments_cover_every_non_nan_bar_exactly_once(self, downtrend):
        """series が値を持つバーは、ちょうど1本の線分に覆われる。"""
        high, low, close = downtrend
        line = counter_trend_series(high, low, close, min_len=2, max_len=5)
        segments = find_counter_trends(high, low, close, min_len=2, max_len=5)

        covered = []
        for seg in segments:
            covered.extend(range(seg.start_index, seg.end_index + 1))
        assert len(covered) == len(set(covered)), '線分の区間が重複している'
        assert sorted(covered) == sorted(np.flatnonzero(~np.isnan(line)).tolist())

    def test_returns_empty_when_series_is_all_nan(self):
        """ラインが1本も引かれない入力では空リストを返す（例外にしない）。"""
        n = 30
        high = np.full(n, 60.0)
        high[10] = 100.0
        high[9] = high[11] = 80.0
        low = np.linspace(58, 30, n)
        assert find_counter_trends(high, low, high - 1.0) == []


class TestCounterTrendAnchors:

    def test_anchors_are_actual_pivot_highs(self, downtrend):
        """アンカーの価格は、そのインデックスの高値そのもの。"""
        high, low, close = downtrend
        for seg in find_counter_trends(high, low, close, min_len=2, max_len=5):
            assert seg.a1_price == high[seg.a1_index]
            assert seg.a2_price == high[seg.a2_index]

    def test_anchor1_precedes_anchor2(self, downtrend):
        """アンカー2 はアンカー1 より後（原文の a1 < idx < limit_idx）。"""
        high, low, close = downtrend
        for seg in find_counter_trends(high, low, close, min_len=2, max_len=5):
            assert seg.a1_index < seg.a2_index

    def test_slope_matches_the_two_anchors(self, downtrend):
        """傾きは2アンカーから決まる値と一致する。"""
        high, low, close = downtrend
        for seg in find_counter_trends(high, low, close, min_len=2, max_len=5):
            expected = (seg.a2_price - seg.a1_price) / (seg.a2_index - seg.a1_index)
            assert seg.slope == pytest.approx(expected, abs=1e-12)

    def test_segment_starts_at_or_after_anchor2(self, downtrend):
        """線が引かれ始めるのはアンカー2 の確定後。先読みしていないことの確認。"""
        high, low, close = downtrend
        for seg in find_counter_trends(high, low, close, min_len=2, max_len=5):
            assert seg.start_index > seg.a2_index


class TestCounterTrendSegmentation:

    def test_new_segment_when_anchors_change(self, downtrend):
        """アンカー組が変わったら別の線分に切れる（同じ組が連続することはない）。"""
        high, low, close = downtrend
        segments = find_counter_trends(high, low, close, min_len=2, max_len=5)
        pairs = [(s.a1_index, s.a2_index) for s in segments]
        for a, b in zip(pairs, pairs[1:]):
            assert a != b, f'同じアンカー組 {a} が連続した線分に分かれている'

    def test_segments_are_in_chronological_order(self, downtrend):
        high, low, close = downtrend
        segments = find_counter_trends(high, low, close, min_len=2, max_len=5)
        for a, b in zip(segments, segments[1:]):
            assert a.end_index < b.start_index

    def test_is_current_marks_only_the_last_bar_segment(self, downtrend):
        """最終バーまで生きている線分だけが is_current。"""
        high, low, close = downtrend
        line = counter_trend_series(high, low, close, min_len=2, max_len=5)
        segments = find_counter_trends(high, low, close, min_len=2, max_len=5)
        expected = not np.isnan(line[-1])
        assert [s.is_current for s in segments].count(True) == (1 if expected else 0)
        if expected:
            assert segments[-1].is_current
            assert segments[-1].end_index == len(high) - 1


class TestFindCounterTrendsHistoryStability:
    """`TestHistoryLengthStability` の線分版。

    T3（SQLite 約500本）と Parquet バックフィル（全期間）で読む本数が違うため、
    値が履歴長に依存すると2経路が静かに食い違う（§5.6 の実害）。
    アンカー座標も同じ性質を持たなければならない。
    """

    @staticmethod
    def _series(n, seed):
        rng = np.random.default_rng(seed)
        close = 100 + np.cumsum(rng.normal(0, 1.5, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        return high, low, close

    @pytest.mark.parametrize('seed', [0, 1, 2, 3, 4])
    def test_last_segment_anchors_do_not_depend_on_history_length(self, seed):
        full_h, full_l, full_c = self._series(1200, seed)
        short = 400
        seg_full = find_counter_trends(full_h, full_l, full_c)
        seg_short = find_counter_trends(full_h[-short:], full_l[-short:], full_c[-short:])
        if not seg_full or not seg_short:
            pytest.skip('この乱数系列ではラインが引かれない')
        a, b = seg_full[-1], seg_short[-1]
        offset = len(full_h) - short
        assert a.a1_index - offset == b.a1_index
        assert a.a2_index - offset == b.a2_index
        assert a.slope == pytest.approx(b.slope, abs=1e-9)
