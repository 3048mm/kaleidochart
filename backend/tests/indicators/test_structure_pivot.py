"""structure_pivot.py — LL-HL 構造ピボット検出のテスト。

TradingView 公開スクリプト `Structure Pivot (LL-HL / HH-LH)` のロング側を移植したもの。
移植で最も壊れやすいのは以下3点なので、それぞれに専用のテストを置く:

1. **ピボットの確定遅延**（`ta.pivotlow(low, L, L)` は L 本先まで確定しない）
   — ここが崩れると先読みバイアスが入る。将来スクリーナー/バックテストへ
   転用したときに黙って成績を良くしてしまうため、最優先で守る。
2. **ピボット価格の定義**（LL と HL の「間」の最高値。両端は含まない）
3. **HL 割れによる無効化**
"""
import numpy as np
import pytest

from indicators.structure_pivot import Structure, find_structures, pivot_strength_low


# ============================================================
# Fixtures — 合成データ
# ============================================================

@pytest.fixture
def base_series():
    """LL(idx10=40) -> 戻り高値(idx15) -> HL(idx20=42) -> 上昇 という素直な系列。

    idx:  0 .. 10 が単調下降(50->40)、11..20 が山を作って 42 まで戻し、21..30 が上昇。
    """
    low = np.array([50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 40,
                    42, 44, 46, 48, 50, 48, 46, 44, 43, 42,
                    44, 46, 48, 50, 52, 54, 56, 58, 60, 62], dtype=np.float64)
    high = low + 3.0
    close = low + 1.5
    return high, low, close


@pytest.fixture
def broken_series(base_series):
    """base_series の idx26 に HL(42) を割り込む安値を入れた系列。"""
    high, low, close = base_series
    low2 = low.copy()
    low2[26] = 35.0
    return low2 + 3.0, low2, low2 + 1.5


# ============================================================
# pivot_strength_low: 「その足が成立する最大の L」
# ============================================================

def test_pivot_strength_is_max_length_that_qualifies(base_series):
    """強度は「左右それぞれ何本、自分より高い足が連続するか」の小さい方。

    idx10(=40) は左に10本・右に20本すべて自分より高いので 10。
    idx20(=42) は左が idx11(=42) で止まるので 8、右は最後まで伸びるので 10 -> min=8。
    """
    _, low, _ = base_series
    strength = pivot_strength_low(low)

    assert strength[10] == 10
    assert strength[20] == 8


def test_pivot_strength_identifies_only_true_pivots(base_series):
    """L>=3 で成立するのは LL(idx10) と HL(idx20) の2点だけ。"""
    _, low, _ = base_series
    strength = pivot_strength_low(low)

    assert np.flatnonzero(strength >= 3).tolist() == [10, 20]


def test_pivot_strength_is_monotonic_in_length(base_series):
    """単調性: 強度 s の足は L<=s のすべてでピボットとして成立する。

    この性質があるから TV 版の「Min〜Max を並列に9本走らせる」実装が
    1パスに畳める。実装がこの前提に依存しているので明示的に守る。
    """
    _, low, _ = base_series
    strength = pivot_strength_low(low)

    for i in np.flatnonzero(strength >= 1):
        s = int(strength[i])
        for length in range(1, s + 1):
            left = low[i - length:i]
            right = low[i + 1:i + length + 1]
            assert (left > low[i]).all(), f'idx{i} L={length} 左側が条件を満たさない'
            assert (right > low[i]).all(), f'idx{i} L={length} 右側が条件を満たさない'


# ============================================================
# find_structures: LL-HL 構造の検出
# ============================================================

def test_detects_single_ll_hl_structure(base_series):
    high, low, close = base_series
    structures = find_structures(high, low, close, min_len=2, max_len=10)

    assert len(structures) == 1
    s = structures[0]
    assert s.ll_index == 10
    assert s.ll_price == pytest.approx(40.0)
    assert s.hl_index == 20
    assert s.hl_price == pytest.approx(42.0)


def test_pivot_is_highest_high_strictly_between_ll_and_hl(base_series):
    """ピボット = LL と HL の「間」の最高値。両端の足は含まない。"""
    high, low, close = base_series
    s = find_structures(high, low, close)[0]

    expected = high[s.ll_index + 1:s.hl_index].max()
    assert s.pivot_price == pytest.approx(expected)
    assert s.pivot_index == 15
    assert s.pivot_price == pytest.approx(53.0)


def test_structure_is_not_visible_before_confirmation(base_series):
    """確定遅延: HL(idx20) は L 本先まで確定しない = 先読みが無いこと。

    最短の L=2 でも確定は idx22。idx21 までしか無いデータでは構造は存在しない。
    """
    high, low, close = base_series

    assert find_structures(high[:22], low[:22], close[:22]) == []

    structures = find_structures(high[:23], low[:23], close[:23])
    assert len(structures) == 1
    assert structures[0].confirmed_index == 22


def test_confirmed_index_equals_hl_index_plus_length(base_series):
    high, low, close = base_series
    s = find_structures(high, low, close)[0]

    assert s.confirmed_index == s.hl_index + s.length


def test_tightest_winner_selects_shortest_length_on_equal_pivot(base_series):
    """Tightest はピボット価格が最小のものを選ぶ。同値なら先に見つかった短い L が残る。

    この系列では L=2..8 のすべてが同じ LL/HL/ピボットを指すため L=2 が勝つ。
    """
    high, low, close = base_series
    s = find_structures(high, low, close, min_len=2, max_len=10)[0]

    assert s.length == 2


def test_length_range_is_honored(base_series):
    """HL(idx20) の強度は 8 なので、min_len=9 では構造が成立しない。"""
    high, low, close = base_series

    assert find_structures(high, low, close, min_len=9, max_len=10) == []
    assert len(find_structures(high, low, close, min_len=2, max_len=8)) == 1


def test_structure_survives_to_last_bar_when_not_broken(base_series):
    high, low, close = base_series
    s = find_structures(high, low, close)[0]

    assert s.invalidated is False
    assert s.is_current is True
    assert s.end_index == len(low) - 1


# ============================================================
# 無効化
# ============================================================

def test_structure_invalidated_when_low_breaks_hl(broken_series):
    """HL を当日安値が割ったバーで構造は消える（Pine と同じく安値で判定）。"""
    high, low, close = broken_series
    structures = find_structures(high, low, close)

    assert len(structures) == 1
    s = structures[0]
    assert s.invalidated is True
    assert s.is_current is False
    assert s.end_index == 25          # idx26 で割れたので、描画は idx25 まで
    assert low[26] < s.hl_price


# ============================================================
# 異常系
# ============================================================

@pytest.mark.parametrize('n', [0, 1, 10, 29])
def test_short_input_returns_empty(n):
    low = np.linspace(100, 90, n) if n else np.array([], dtype=np.float64)
    high = low + 1
    close = low + 0.5

    assert find_structures(high, low, close) == []


def test_flat_series_has_no_structure():
    """全て同値の系列はピボットが成立しない（厳密比較のため）。"""
    low = np.full(60, 100.0)
    assert find_structures(low + 1, low, low + 0.5) == []


def test_monotonic_downtrend_has_no_higher_low():
    """単調下降は安値を切り上げないので LL-HL 構造は生まれない。"""
    low = np.linspace(200, 100, 80)
    assert find_structures(low + 1, low, low + 0.5) == []


def test_returns_structure_dataclass(base_series):
    high, low, close = base_series
    s = find_structures(high, low, close)[0]

    assert isinstance(s, Structure)
    # 描画に必要な項目が全て埋まっていること
    for field in ('length', 'll_index', 'll_price', 'hl_index', 'hl_price',
                  'pivot_index', 'pivot_price', 'confirmed_index', 'end_index',
                  'invalidated', 'is_current'):
        assert getattr(s, field) is not None


# ============================================================
# 既定の長さ帯（設計判断の固定）
# ============================================================

def test_default_length_band_is_2_to_5():
    """既定帯は 2-5。チャート表示と T3（スクリーナー）で同じ水準を見るための約束。

    片方だけ変えると「画面で見えている水準」と「スクリーニングされる水準」が
    食い違う。変更するときは両方の仕様書も直すこと
    （doc/in_progress/structure_pivot_screener_plan.md §2.2）。
    """
    from indicators.structure_pivot import DEFAULT_MAX_LEN, DEFAULT_MIN_LEN

    assert (DEFAULT_MIN_LEN, DEFAULT_MAX_LEN) == (2, 5)


# ============================================================
# structure_pivot_series — T3 用の時系列 API
# ============================================================

def test_series_returns_nan_where_no_structure(base_series):
    """構造が生きていないバーは NaN。確定前も NaN でなければならない。"""
    from indicators.structure_pivot import structure_pivot_series

    high, low, close = base_series
    sp_pivot, sp_hl = structure_pivot_series(high, low, close)

    assert sp_pivot.shape == low.shape
    assert sp_hl.shape == low.shape
    # idx22 で確定するので、それ以前は全て NaN（= 先読みが無い）
    assert np.isnan(sp_pivot[:22]).all()
    assert np.isnan(sp_hl[:22]).all()


def test_series_matches_find_structures(base_series):
    """時系列 API と描画用 API が同じ構造を指すこと。

    両者は同じ `_scan_for_length` を使うので、食い違うとしたら勝者選択のズレ。
    """
    from indicators.structure_pivot import find_structures, structure_pivot_series

    high, low, close = base_series
    sp_pivot, sp_hl = structure_pivot_series(high, low, close)
    structures = find_structures(high, low, close)

    assert len(structures) == 1
    s = structures[0]
    for t in range(s.confirmed_index, s.end_index + 1):
        assert sp_pivot[t] == pytest.approx(s.pivot_price)
        assert sp_hl[t] == pytest.approx(s.hl_price)


def test_series_clears_after_invalidation(broken_series):
    """HL 割れ以降は NaN に戻る。"""
    from indicators.structure_pivot import structure_pivot_series

    high, low, close = broken_series
    sp_pivot, sp_hl = structure_pivot_series(high, low, close)

    assert not np.isnan(sp_pivot[25])      # 生存最終バー
    assert np.isnan(sp_pivot[26:]).all()   # idx26 で HL 割れ
    assert np.isnan(sp_hl[26:]).all()


def test_series_hl_is_below_pivot_where_defined(base_series):
    """定義されているバーでは必ず HL < ピボット（構造の前提）。"""
    from indicators.structure_pivot import structure_pivot_series

    high, low, close = base_series
    sp_pivot, sp_hl = structure_pivot_series(high, low, close)
    defined = ~np.isnan(sp_pivot)

    assert defined.any()
    assert (sp_hl[defined] < sp_pivot[defined]).all()


@pytest.mark.parametrize('n', [0, 1, 10])
def test_series_short_input_is_all_nan(n):
    low = np.linspace(100, 90, n) if n else np.array([], dtype=np.float64)
    from indicators.structure_pivot import structure_pivot_series

    sp_pivot, sp_hl = structure_pivot_series(low + 1, low, low + 0.5)

    assert sp_pivot.shape == (n,)
    assert np.isnan(sp_pivot).all()
    assert np.isnan(sp_hl).all()
