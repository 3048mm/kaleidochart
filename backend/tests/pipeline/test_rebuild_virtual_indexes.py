"""仮想テーマ指数の再合成スクリプトのテスト（scripts/rebuild_virtual_indexes.py）。

2026-08-07 の再ベース事故（170本中165本が 2024-08-06 に基準値1000へリセット）で
壊れたデータを作り直すためのスクリプト。**検算が本体**なので、そこを固定する。
"""

import os
import sys

import pandas as pd
import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.rebuild_virtual_indexes import (  # noqa: E402
    BASE_VALUE_BAND,
    SANITY_RETURN_LIMIT,
    check_index_sanity,
    check_no_base_value_restart,
    find_virtual_theme_ids,
)


def _symbols():
    return pd.DataFrame([
        {"id": 10, "ticker": "AAA", "active": 1},
        {"id": 101, "ticker": "_THEME_A_", "active": 1},
        {"id": 102, "ticker": "_THEME_B_", "active": 1},
        {"id": 103, "ticker": "REALETF", "active": 1},
    ])


# ---------------------------------------------------------------------------
# find_virtual_theme_ids
# ---------------------------------------------------------------------------
def test_picks_only_underscore_wrapped_tickers():
    """実在 ETF のテーマは合成しないので対象外。"""
    assert find_virtual_theme_ids(_symbols()) == [101, 102]


def test_can_narrow_by_ticker():
    assert find_virtual_theme_ids(_symbols(), ["_THEME_B_"]) == [102]


def test_unknown_ticker_aborts(capsys):
    """指定を打ち間違えたまま全件処理に流れないこと。"""
    with pytest.raises(SystemExit):
        find_virtual_theme_ids(_symbols(), ["_NOPE_"])
    assert "_NOPE_" in capsys.readouterr().out


def test_real_etf_cannot_be_forced_in():
    with pytest.raises(SystemExit):
        find_virtual_theme_ids(_symbols(), ["REALETF"])


# ---------------------------------------------------------------------------
# check_index_sanity
# ---------------------------------------------------------------------------
def _index(closes, sid=101, start="2024-08-01"):
    days = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({"symbol_id": sid, "date": days, "close": closes})


def test_smooth_index_is_clean():
    out = check_index_sanity(_index([1000, 1005, 1010, 1002, 1008]), _symbols())
    assert out.empty


def test_detects_a_leftover_step():
    """構成銘柄側に未処理の分割が残っていると指数にも段差が出る。

    指数は構成銘柄のリターン平均なので、単独銘柄より必ず穏やかになるはず。
    ±40% を超えたら合成の失敗か上流の未処理を疑う。
    """
    out = check_index_sanity(_index([1000, 1005, 3400, 3410]), _symbols())
    assert len(out) == 1
    assert out.iloc[0]["ticker"] == "_THEME_A_"
    assert out.iloc[0]["ret"] > 2.0


def test_first_row_is_not_flagged():
    """初日はリターンが計算できないので対象外。"""
    out = check_index_sanity(_index([1000, 1001]), _symbols())
    assert out.empty


def test_limit_is_configurable():
    idx = _index([1000, 1500])          # +50%
    assert check_index_sanity(idx, _symbols(), limit=0.60).empty
    assert not check_index_sanity(idx, _symbols(), limit=0.40).empty


def test_sanity_limit_default_is_looser_than_single_stock_moves():
    """既定値は単独銘柄の急変では鳴らない程度に緩いこと。

    厳しすぎると実際の暴落（BYND は 2025-10 に -48% / +146% を記録）で
    毎回止まってしまう。指数は平均なのでそこまでは動かない。
    """
    assert 0.2 <= SANITY_RETURN_LIMIT <= 0.6


# ---------------------------------------------------------------------------
# check_no_base_value_restart — 2026-08-07 事故の指紋
# ---------------------------------------------------------------------------
def test_initial_base_value_is_not_flagged():
    """初日が 1000 なのは正常（そこから連鎖を始めるため）。"""
    out = check_no_base_value_restart(_index([1000, 1200, 1500]), _symbols())
    assert out.empty


def test_detects_a_mid_series_restart():
    """途中で 1000 に戻るのは「窓の左端でリセット」の指紋。

    2026-08-07 の事故では 170本中165本が 2024-08-06 に ~1000 になっていた。
    """
    out = check_no_base_value_restart(
        _index([1000, 5000, 20000, 1002, 1010]), _symbols())
    # リセット後は基準値の近くをしばらく漂うので、複数行が挙がるのが正常
    assert set(out["ticker"]) == {"_THEME_A_"}
    assert out["date"].min() == "2024-08-06", "リセット日を捉えていない"
    assert (out["close"].between(*BASE_VALUE_BAND)).all()
    assert "2024-08-01" not in set(out["date"]), "初日の 1000 まで拾っている"


def test_multiple_themes_restarting_on_the_same_date_are_all_reported():
    a = _index([1000, 5000, 1005], sid=101)
    b = _index([1000, 8000, 1030], sid=102)
    out = check_no_base_value_restart(pd.concat([a, b], ignore_index=True), _symbols())
    assert set(out["ticker"]) == {"_THEME_A_", "_THEME_B_"}
    assert out["date"].nunique() == 1, "同じ日に集中していることが検知の決め手"


def test_values_far_from_the_base_are_not_flagged():
    out = check_no_base_value_restart(_index([1000, 1200, 900, 1500]), _symbols())
    assert out.empty, "900 は基準値バンド外（誤検知）"
