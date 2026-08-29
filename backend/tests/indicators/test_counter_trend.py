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
