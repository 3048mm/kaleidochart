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

import pandas as pd
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
    SPLIT_DATE_WINDOW_DAYS,
    classify_price_jump,
    find_matching_split,
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
    """優先順位: virtual > **split_match** > market_wide > low_liquidity > split/real

    市場全体の日に低位株が動いても market_wide が優先される
    （個別要因ではないため）。分割記録との一致だけがこれを上書きする
    （下段「分割メタデータとの照合」を参照）。
    """
    r = _jump(ticker="PENNY", ratio=0.5, prev_close=1.0,
              adv21=100.0, same_day_count=30)
    assert classify_price_jump(r) == "market_wide"


# ---------------------------------------------------------------------------
# same_day_count に仮想テーマを数えてはいけない
#
# 2026-08-25 に発覚。`BYND` の 1:30 併合（比率 30.10）が **market_wide** に
# 誤分類され、週次監査の報告から漏れていた。
#
#     2026-08-13 の同日アノマリー 4件（MARKET_WIDE_MIN_SYMBOLS = 4）
#       _GRCL29_   5.83  virtual      ← BYND が汚染したテーマ
#       _CNSM0A_   3.44  virtual      ← 同上
#       _NTRTFC_   5.87  virtual      ← 同上
#       BYND      30.10  market_wide  ← 犯人が「市場イベント」に化けた
#
# 仮想指数は構成銘柄から合成されるので、**1銘柄が壊れると所属テーマの数だけ
# 同日件数が水増しされる**（1銘柄が最大5テーマに所属する）。
# 犯人が自分の作った波及に隠れる構造だった。
# ---------------------------------------------------------------------------
def test_market_wide_count_excludes_virtual_themes():
    """市場イベントの判定は**実在銘柄の数**で行うこと。"""
    from indicators.price_anomaly import count_real_symbols_per_day

    rows = pd.DataFrame([
        {"ticker": "_GRCL29_", "date": "2026-08-13"},
        {"ticker": "_CNSM0A_", "date": "2026-08-13"},
        {"ticker": "_NTRTFC_", "date": "2026-08-13"},
        {"ticker": "BYND", "date": "2026-08-13"},
    ])
    counts = count_real_symbols_per_day(rows)
    assert list(counts) == [1, 1, 1, 1], "仮想テーマまで数えている"


def test_market_wide_count_still_sees_real_market_events():
    """実在銘柄が並んだ日はちゃんと数える（COVID の急落日など）。"""
    from indicators.price_anomaly import count_real_symbols_per_day

    rows = pd.DataFrame([{"ticker": t, "date": "2020-03-09"}
                         for t in ("AAPL", "MSFT", "GOOG", "AMZN", "META")])
    counts = count_real_symbols_per_day(rows)
    assert list(counts) == [5] * 5


def test_bynd_is_classified_as_split_suspect_not_market_wide():
    """**本件の回帰テスト。** 実データそのままで `split_suspect` になること。"""
    cls = classify_price_jump({
        "ticker": "BYND",
        "ratio": 30.101426,
        "prev_close": 0.4141,
        "adv21": 2.015160e07,
        "dv_ratio": 0.713435,
        "dv_vs_adv": None,
        "same_day_count": 1,      # 仮想テーマを除いた実数
    })
    assert cls == "split_suspect", f"BYND が {cls} に分類されている"


# ---------------------------------------------------------------------------
# 分割メタデータとの照合（find_matching_split）
#
# ## なぜ足したか
#
# 分類器は**分割記録を一切見ていなかった**ため、比の大きさと同日件数だけで
# 判定していた。2026-09-10 に補正した本物の破損2件は、どちらもレポートに
# 入っていながら捨てられていた（`doc/issue_list.md` P1）:
#
#     IESC  2026-08-24  ratio=0.4731  same_day_count=4  → market_wide
#     WLFC  2026-07-20  ratio=0.3294  same_day_count=1  → real_move
#
# ## ケースはすべて上流の実記録から採っている
#
# 分割記録は 2026-09-10 に yfinance から実取得したもの:
#
#     IESC  2026-08-24 ×2.0    段差と**同日**
#     WLFC  2026-07-21 ×3.0    段差（07-20）の**翌日** ← 日付の幅が要る証拠
#     MNST  2026-08-11 ×2.0    上流が適用し損ねていた既知の事故銘柄
# ---------------------------------------------------------------------------
SPLITS = {
    "IESC": [("2026-08-24", 2.0)],
    "WLFC": [("2026-07-21", 3.0)],
    "MNST": [("2026-08-11", 2.0)],
    "SCCO": [("2026-05-01", 1.01)],      # 株式配当。段差では識別できない
}


def test_matches_split_on_the_same_day():
    """`IESC` — 段差の日にそのまま分割記録がある。"""
    m = find_matching_split("IESC", "2026-08-24", 0.473140, SPLITS)
    assert m is not None
    assert m["split_date"] == "2026-08-24"
    assert m["factor"] == 2.0
    assert m["days_off"] == 0


def test_matches_split_recorded_one_day_later():
    """`WLFC` — 上流の分割日は段差の**翌日**。厳密一致では取り逃す。"""
    m = find_matching_split("WLFC", "2026-07-20", 0.329400, SPLITS)
    assert m is not None, "1日のずれで取り逃している"
    assert m["factor"] == 3.0
    assert abs(m["days_off"]) == 1


def test_reverse_split_is_matched():
    """併合（factor<1）も同じ式で拾える。1:10 なら比は約10倍になる。"""
    splits = {"WBX": [("2026-03-02", 0.05)]}
    m = find_matching_split("WBX", "2026-03-02", 20.0, splits)
    assert m is not None
    assert m["factor"] == 0.05


def test_no_match_when_ratio_disagrees_with_factor():
    """**日付が合っても比が合わなければ一致にしない。**

    分割日に本物の急落が重なることはある。日付だけで断定すると、
    実際の値動きを分割として報告してしまう。
    """
    assert find_matching_split("IESC", "2026-08-24", 0.25, SPLITS) is None


def test_no_match_outside_the_date_window():
    """窓の外の分割は無関係。"""
    far = "2026-10-01"      # 記録の 2026-08-24 から 30日以上
    assert find_matching_split("IESC", far, 0.5, SPLITS) is None


def test_date_window_boundary():
    """窓の境界で挙動が切り替わる（±SPLIT_DATE_WINDOW_DAYS まで一致）。"""
    from datetime import date, timedelta

    base = date(2026, 8, 24)
    inside = (base + timedelta(days=SPLIT_DATE_WINDOW_DAYS)).isoformat()
    outside = (base + timedelta(days=SPLIT_DATE_WINDOW_DAYS + 1)).isoformat()
    assert find_matching_split("IESC", inside, 0.5, SPLITS) is not None
    assert find_matching_split("IESC", outside, 0.5, SPLITS) is None


def test_stock_dividend_is_not_discriminable():
    """`SCCO` — factor≈1.0 は「未適用」と「適用済み」が重なる。**判定を放棄する。**

    `scan_split_consistency.py` の `MIN_DISCRIMINABLE_GAP` と同じ理由。
    2026-09-09 の全ユニバース実測では、検出18件中16件がこの誤検出だった。
    """
    assert find_matching_split("SCCO", "2026-05-01", 0.99, SPLITS) is None


def test_unknown_ticker_and_empty_records():
    """記録が無いものは黙って一致にしない。"""
    assert find_matching_split("AAPL", "2026-08-24", 0.5, SPLITS) is None
    assert find_matching_split("IESC", "2026-08-24", 0.5, {}) is None
    assert find_matching_split("IESC", "2026-08-24", 0.5, None) is None


def test_none_ratio_is_not_a_match():
    """比が取れない行を分割にしない。"""
    assert find_matching_split("IESC", "2026-08-24", None, SPLITS) is None


# ---------------------------------------------------------------------------
# 分割一致が分類の優先順位を上書きする（classify_price_jump）
# ---------------------------------------------------------------------------
def test_split_match_overrides_market_wide():
    """**`IESC` の回帰テスト。** 同日4件でも分割記録があれば要対応にする。

    `MARKET_WIDE_MIN_SYMBOLS = 4` は「同日に4件の分割が重なると全部を
    市場全体の動きとして消す」。2026-08-24 がまさにそれだった。
    """
    r = _jump(ticker="IESC", ratio=0.473140, prev_close=71.5,
              adv21=1.2e7, same_day_count=4,
              split_match=find_matching_split("IESC", "2026-08-24",
                                              0.473140, SPLITS))
    assert classify_price_jump(r) == "split_suspect"


def test_split_match_overrides_real_move():
    """**`WLFC` の回帰テスト。** 同日1件でも分割記録があれば要対応にする。"""
    r = _jump(ticker="WLFC", ratio=0.329400, prev_close=180.0,
              adv21=3.0e7, dv_ratio=3.5, same_day_count=1,
              split_match=find_matching_split("WLFC", "2026-07-20",
                                              0.329400, SPLITS))
    assert classify_price_jump(r) == "split_suspect"


def test_split_match_overrides_low_liquidity():
    """低位株・薄商いでも隠さない（§4-3 の判断）。

    `low_liquidity` は「破損かどうか」ではなく「**対応する価値があるか**」の
    足切り。未調整分割は Parquet マスタが壊れたまま残り T4 の横断ランクにも
    乗るので、価格水準を理由に握り潰さない。
    """
    r = _jump(ticker="IESC", ratio=0.473140, prev_close=1.0, adv21=100.0,
              same_day_count=1,
              split_match=find_matching_split("IESC", "2026-08-24",
                                              0.473140, SPLITS))
    assert classify_price_jump(r) == "split_suspect"


def test_virtual_still_wins_over_split_match():
    """仮想テーマ指数は合成値であって、テーマ自体は分割しない。"""
    r = _jump(ticker="_AI_", ratio=0.5, split_match={"factor": 2.0,
                                                     "split_date": "2026-08-24",
                                                     "days_off": 0})
    assert classify_price_jump(r) == "virtual"


def test_nearby_split_is_found_even_when_ratio_disagrees():
    """`STKH` — 比が11%外れて一致にはならないが、**近傍にあることは報告する**。

    分割 2026-07-27 ×1:3（期待比 3.0）に対し、翌日の段差は 2.6667。
    「分割日に11%下げただけ」とも「分割が中途半端に効いている」とも読め、
    yfinance だけでは決着しない。捨てずに人間へ渡す。
    """
    from indicators.price_anomaly import find_nearby_split

    splits = {"STKH": [("2026-07-27", 1 / 3)]}
    assert find_matching_split("STKH", "2026-07-28", 2.6667, splits) is None
    near = find_nearby_split("STKH", "2026-07-28", splits)
    assert near is not None
    assert near["days_off"] == -1
    assert round(near["expected_ratio"], 4) == 3.0


def test_nearby_split_ignores_stock_dividends():
    """報告専用の経路でも、識別できない帯（株式配当）は拾わない。"""
    from indicators.price_anomaly import find_nearby_split

    assert find_nearby_split("SCCO", "2026-05-01", SPLITS) is None


def test_without_split_match_behaviour_is_unchanged():
    """**後方互換。** `split_match` を渡さなければ従来どおり。

    呼び出し側を段階的に移せるようにするための保証。
    """
    r = _jump(ticker="IESC", ratio=0.473140, prev_close=71.5,
              adv21=1.2e7, same_day_count=4)
    assert classify_price_jump(r) == "market_wide"
    assert classify_price_jump({**r, "split_match": None}) == "market_wide"
