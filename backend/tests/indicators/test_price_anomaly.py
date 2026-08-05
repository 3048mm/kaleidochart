"""価格アノマリーの分類テスト（indicators/price_anomaly.py）

## 背景

現在の検出は「前日比が 0.61 以下 または 1.79 以上」だけを見ており、
**大きな値動きを全部拾っているだけ**。モメンタム系スクリーナーの母集団
（小型株・バイオを含む）では ±50〜70% の単日変動は日常的に起きる。

実測（2026-08-04 / Parquet 全期間・active 銘柄）:

    アノマリー総数 1,081件 / 504銘柄  ← 大半はノイズ

課題リストの「未調整の株式分割が299件」は誤りで、本物の分割はごく少数。

## テストケースはすべて実測データから採っている

作り物のデータではなく、**実際に誤判定した事例**で固定する。
"""

import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from indicators.price_anomaly import (  # noqa: E402
    ANOMALY_RATIO_HI,
    ANOMALY_RATIO_LO,
    classify_price_jump,
    is_anomalous_ratio,
)


def _jump(**kw):
    """既定は「実売買しうる水準の、ごく普通の急落」"""
    base = dict(ticker="XYZ", ratio=0.50, prev_close=50.0, adv21=10_000_000.0,
                dv_ratio=1.0, dv_vs_adv=1.0, same_day_count=1)
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# is_anomalous_ratio — 検出のしきい値
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ratio,expected", [
    (0.50, True),    # -50%
    (2.00, True),    # +100%
    (0.61, True),    # 境界（以下）
    (1.79, True),    # 境界（以上）
    (0.62, False),
    (1.78, False),
    (1.00, False),
])
def test_ratio_threshold(ratio, expected):
    assert is_anomalous_ratio(ratio) is expected
    assert (ANOMALY_RATIO_LO, ANOMALY_RATIO_HI) == (0.61, 1.79)


# ---------------------------------------------------------------------------
# virtual — 仮想テーマ指数
# ---------------------------------------------------------------------------
def test_virtual_theme_index():
    """`_DRON_` 2019-01-31（285.71→142.86）の実測ケース。

    仮想テーマ指数は構成銘柄から合成した値で、テーマ自体は分割しない。
    段差は構成銘柄の入れ替え、または構成銘柄の分割の継承による。
    """
    assert classify_price_jump(_jump(ticker="_DRON_", ratio=0.500)) == "virtual"


def test_virtual_takes_priority_over_everything():
    """仮想テーマは他の条件より先に判定する（合成値なので他の指標に意味がない）"""
    r = _jump(ticker="_BLOK_", ratio=0.05, prev_close=0.5,
              adv21=100.0, same_day_count=30)
    assert classify_price_jump(r) == "virtual"


# ---------------------------------------------------------------------------
# market_wide — 市場全体の急落日
# ---------------------------------------------------------------------------
def test_market_wide_day():
    """`DIN` 2020-03-18（COVID・同日16銘柄）の実測ケース。

    2020-03-09 は34銘柄が同時にアノマリーになった。個別の分割ではない。
    """
    assert classify_price_jump(_jump(ticker="DIN", ratio=0.558,
                                     same_day_count=16)) == "market_wide"


def test_two_symbols_same_day_is_not_market_wide():
    """2〜3銘柄の偶然の一致は市場イベントとみなさない（N=4）"""
    assert classify_price_jump(_jump(same_day_count=3)) != "market_wide"
    assert classify_price_jump(_jump(same_day_count=4)) == "market_wide"


# ---------------------------------------------------------------------------
# low_liquidity — 低位株・薄商い
# ---------------------------------------------------------------------------
def test_penny_stock_tick_oscillation():
    """`ATLX` の実測ケース（0.75↔1.50 を何年も往復、27件）。

    分割は一度きりで不可逆なので、往復するものは分割ではない。
    ぴったり 0.500 / 2.000 になるため「きれいな分割比」で判定すると誤検出源になる。
    """
    assert classify_price_jump(_jump(ticker="ATLX", ratio=0.500,
                                     prev_close=1.50)) == "low_liquidity"


def test_thin_volume_is_excluded_even_if_price_is_high():
    """株価が高くても売買代金が薄ければ実売買しない → 対象外"""
    assert classify_price_jump(_jump(prev_close=80.0, adv21=50_000.0)) == "low_liquidity"


def test_boundary_of_price_and_liquidity():
    assert classify_price_jump(_jump(prev_close=5.0)) == "low_liquidity"
    assert classify_price_jump(_jump(prev_close=5.01)) != "low_liquidity"
    assert classify_price_jump(_jump(adv21=1_000_000.0)) == "low_liquidity"
    assert classify_price_jump(_jump(adv21=1_000_001.0)) != "low_liquidity"


# ---------------------------------------------------------------------------
# split_suspect — 未調整の分割の疑い
# ---------------------------------------------------------------------------
def test_extreme_ratio_with_continuous_dollar_volume():
    """`SOXS` の実測ケース（1146.15→62.18、売買代金比 1.16）。

    分割は株数が変わるだけなので売買代金は連続する。
    """
    assert classify_price_jump(_jump(ticker="SOXS", ratio=0.054,
                                     prev_close=1146.15,
                                     dv_ratio=1.16)) == "split_suspect"


def test_reverse_split_upward():
    """`WBX` の実測ケース（1:20 併合、価格比 19.37・代金比 0.71）。

    +1837% は実急騰に見えるが併合だった。見た目では判定できない。
    """
    assert classify_price_jump(_jump(ticker="WBX", ratio=19.37,
                                     prev_close=1.0, adv21=5_000_000.0,
                                     dv_ratio=0.71)) == "split_suspect"


def test_moderate_ratio_is_not_split_even_if_volume_looks_continuous():
    """**1:2 前後の帯では売買代金比が効かない。**

    `DIN` `ADAM` `BBAI` の COVID 暴落を「分割」と誤判定した実例がある。
    暴落は出来高が1.5〜3倍に跳ねるため代金比が 0.5〜2.0 に収まり、分割と区別できない。
    したがって中庸な比率では split_suspect にしない（偽陽性を出さない側に倒す）。
    """
    assert classify_price_jump(_jump(ticker="BBAI", ratio=0.604,
                                     dv_ratio=1.04)) != "split_suspect"


def test_extreme_ratio_but_volume_exploded_is_real_move():
    """`BMNR` の実測ケース（価格比 7.95・代金比 32,440）。

    代金が跳ねているので分割ではなく実際の急騰。
    """
    assert classify_price_jump(_jump(ticker="BMNR", ratio=7.95,
                                     prev_close=20.0,
                                     dv_ratio=32440.0)) == "real_move"


def test_undecidable_when_there_is_no_trading_at_all():
    """`CORZZ` の実測ケース。そもそも取引が無いので判定材料が無い。

    78行中 出来高>0 はわずか4日。当日代金0 / 直前21日平均2.1万 = 0。
    黙って分類せず保留する。
    """
    assert classify_price_jump(_jump(ticker="CORZZ", ratio=0.529,
                                     prev_close=28.98,
                                     dv_ratio=None, dv_vs_adv=0.0)) == "undecided"


def test_halt_resumption_is_a_real_move_not_undecided():
    """`MESO` 2023-08-04 の実測ケース。**取引停止からの再開**。

    前日まで2日間 出来高0（停止）だったため代金比が取れないが、
    当日代金1,199万 / 直前21日平均166万 = 7.2倍 で明らかに実際の値動き。
    前日比だけを見て「判定不能」に落とすと、要対応リストに残り続ける。
    """
    assert classify_price_jump(_jump(ticker="MESO", ratio=0.411,
                                     prev_close=7.98, adv21=1_658_197.0,
                                     dv_ratio=None, dv_vs_adv=7.23)) == "real_move"


def test_halt_resumption_with_continuous_volume_can_still_be_a_split():
    """停止明けでも、極端な比率かつ代金が21日平均並みなら分割の疑いとして拾う。

    代替指標を入れたことで実際の分割を見落とさないことを固定する。
    """
    assert classify_price_jump(_jump(ticker="X", ratio=0.05, prev_close=1000.0,
                                     dv_ratio=None, dv_vs_adv=1.1)) == "split_suspect"


# ---------------------------------------------------------------------------
# real_move — 実際の値動き（大多数）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ticker,ratio,prev_close,note", [
    ("MDGL", 2.450, 108.43, "2018-05-31 NASH 治験成功"),
    ("PCG",  0.476,  17.29, "2019-01-14 破産申請"),
    ("NVAX", 0.333,  42.60, "2019-02-28 治験失敗"),
    ("CLDX", 0.353,  32.25, "2018-04-16 治験失敗"),
])
def test_real_price_moves(ticker, ratio, prev_close, note):
    """バイオのバイナリーイベント等。**正常なデータで対応不要**。

    これらを「分割の疑い」と報告してしまうのが現状の問題。
    """
    r = _jump(ticker=ticker, ratio=ratio, prev_close=prev_close,
              adv21=20_000_000.0, dv_ratio=5.0)
    assert classify_price_jump(r) == "real_move", note


def test_classification_priority_order():
    """優先順位: virtual > market_wide > low_liquidity > split/real

    市場全体の日に低位株が動いても market_wide が優先される
    （個別要因ではないため）。
    """
    r = _jump(ticker="PENNY", ratio=0.5, prev_close=1.0,
              adv21=100.0, same_day_count=30)
    assert classify_price_jump(r) == "market_wide"
