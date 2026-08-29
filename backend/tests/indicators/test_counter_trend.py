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

from indicators.structure_pivot import (counter_trend_series, pivot_strength_high,
                                        pivot_strength_low, structure_pivot_series)


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
    # 安値は単調に切り下げ、HL が出来ないようにする
    low[:] = np.linspace(58, 30, n)
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

    def test_no_line_when_slope_is_not_downward(self):
        """高値が切り上がる（傾きが正）局面では*カウンター*トレンド線にならない。"""
        n = 60
        high = np.full(n, 60.0)
        for idx, peak in ((5, 80.0), (15, 90.0), (25, 100.0)):   # 切り上がり
            high[idx] = peak
            high[idx - 1] = high[idx + 1] = peak - 8
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
