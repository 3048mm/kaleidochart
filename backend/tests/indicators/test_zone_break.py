"""zone_break.py — Direction via Zone Break [by rukich] の状態機械のテスト。

TradingView 公開スクリプト `Direction via Zone Break [by rukich]`（Pine v6）を
移植したもの。3本足フラクタルで SSL(安値側)/BSL(高値側) を確定・追従更新し、
終値のブレイクでトレンド継続/反転(フリップ)を判定する状態機械の挙動を検証する。

入力・期待値は doc/in_progress/zone_break_plan.md §3.1.1 で確定済みのものを
そのまま使用する（本ファイルでの再計算・再解釈は行わない）。
"""
import numpy as np
import pytest

from indicators.zone_break import zone_break_series


# ============================================================
# ケース1: 初回SSL/BSL確定
# ============================================================

def test_初回ssl_bsl確定():
    """3本足フラクタルで SSL(安値側)→BSL(高値側)の順に初回確定していく過程を検証する。"""
    high = np.array([10, 11, 10, 12, 11, 13, 12, 15, 13, 9, 8, 10], dtype=float)
    low = np.array([9, 10, 9, 10, 10, 11, 11, 12, 11, 7, 6, 8], dtype=float)
    close = np.array([9.5, 10.5, 9.5, 11, 10.5, 12, 11.5, 14, 12, 8, 7, 9], dtype=float)

    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(high, low, close)

    expected_is_bull = [True] * 12
    expected_ssl = [0, 0, 0, 9, 9, 9, 9, 9, 9, 9, 9, 9]
    expected_bsl = [0, 0, 0, 0, 12, 12, 13, 13, 15, 15, 15, 15]
    expected_is_weak = [False] * 12

    assert list(is_bull) == expected_is_bull
    np.testing.assert_array_equal(zb_ssl, expected_ssl)
    np.testing.assert_array_equal(zb_bsl, expected_bsl)
    assert list(is_weak) == expected_is_weak


# ============================================================
# ケース2: 内部フラクタル追従 + 継続ブレイク
# ============================================================

def test_内部フラクタル追従と継続ブレイク():
    """内部候補の追従更新後、終値がBSLを上抜けても is_bull は変化せず(継続)、
    最新の追従値が zb_ssl/zb_bsl に反映されることを検証する。
    """
    high = np.array(
        [10, 11, 10, 12, 11, 13, 12, 15, 13, 9, 8, 10, 10, 9, 11, 16, 18, 14],
        dtype=float,
    )
    low = np.array(
        [9, 10, 9, 10, 10, 11, 11, 12, 11, 7, 6, 8, 5, 4, 6, 8, 9, 7],
        dtype=float,
    )
    close = np.array(
        [9.5, 10.5, 9.5, 11, 10.5, 12, 11.5, 14, 12, 8, 7, 9, 10, 9.5, 10.5, 16, 17, 13],
        dtype=float,
    )

    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(high, low, close)

    expected_is_bull = [True] * 18
    expected_ssl = [0, 0, 0, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 4]
    expected_bsl = [0, 0, 0, 0, 12, 12, 13, 13, 15, 15, 15, 15, 15, 15, 15, 15, 15, 18]
    expected_is_weak = [False] * 18

    assert list(is_bull) == expected_is_bull
    np.testing.assert_array_equal(zb_ssl, expected_ssl)
    np.testing.assert_array_equal(zb_bsl, expected_bsl)
    assert list(is_weak) == expected_is_weak


# ============================================================
# ケース3: フリップ反転（Bull -> Bear）
# ============================================================

def test_フリップ反転():
    """終値がSSLを下抜けて反転シグナルが立ち、次の安値フラクタル確定で
    is_bull が False に切り替わり SSL/BSL が再設定されることを検証する。
    """
    high = np.array(
        [10, 11, 10, 12, 11, 13, 12, 15, 13, 9, 8, 10, 10, 9, 8],
        dtype=float,
    )
    low = np.array(
        [9, 10, 9, 10, 10, 11, 11, 12, 11, 7, 6, 8, 7, 5, 6],
        dtype=float,
    )
    close = np.array(
        [9.5, 10.5, 9.5, 11, 10.5, 12, 11.5, 14, 12, 8, 7, 9, 8, 7, 6.5],
        dtype=float,
    )

    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(high, low, close)

    expected_is_bull = [True] * 14 + [False]
    expected_ssl = [0, 0, 0, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 5]
    expected_bsl = [0, 0, 0, 0, 12, 12, 13, 13, 15, 15, 15, 15, 15, 15, 15]
    expected_is_weak = [False] * 15

    assert list(is_bull) == expected_is_bull
    np.testing.assert_array_equal(zb_ssl, expected_ssl)
    np.testing.assert_array_equal(zb_bsl, expected_bsl)
    assert list(is_weak) == expected_is_weak


# ============================================================
# ケース4: is_weak（強気FVG生成→無効化）
# ============================================================

def test_is_weak_フラグの生成と無効化():
    """強気FVGゾーンが生成された後、終値がその下端を割り込むと is_weak が
    True に切り替わることを検証する。同じバーでBSLが独立に初期確定する。
    """
    high = np.array([10, 11, 10, 12, 13, 11], dtype=float)
    low = np.array([9, 10, 9, 10, 12, 10], dtype=float)
    close = np.array([9.5, 10.5, 9.5, 11, 12.5, 8], dtype=float)

    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(high, low, close)

    expected_is_bull = [True] * 6
    expected_ssl = [0, 0, 0, 9, 9, 9]
    expected_bsl = [0, 0, 0, 0, 0, 13]
    expected_is_weak = [False] * 5 + [True]

    assert list(is_bull) == expected_is_bull
    np.testing.assert_array_equal(zb_ssl, expected_ssl)
    np.testing.assert_array_equal(zb_bsl, expected_bsl)
    assert list(is_weak) == expected_is_weak
