"""
test_optimization_score.py — 最適化バックテストの目的関数（再設計後）の純関数テスト。

再設計の要点（doc/in_progress/objective_redesign_plan.md）:
  score = period_CAGR / dd_penalty × detect_adequacy(avg_hits_per_day)
  - 主指標を expectancy_lcb（トレード単価）→ 期間CAGR（複利での資産成長）へ差し替え
  - DDペナルティ（閾値付き2乗・max_allowed_dd）は CAGR に対して掛ける（Calmar型）
  - 検出件数は実用帯 detect_band=(lo, hi, floor) の係数でソフトに選好する
"""
import math
import pytest

from optimization_runner import calculate_custom_score, detect_adequacy, resolve_detect_band


# =============================================================
# detect_adequacy 単体
# =============================================================

BAND = (1.0, 12.0, 0.4)  # lo, hi, floor


def test_detect_adequacy_inside_band_is_one():
    # 帯の内側（境界含む）は減点なし = 1.0
    assert detect_adequacy(1.0, BAND) == 1.0
    assert detect_adequacy(3.0, BAND) == 1.0
    assert detect_adequacy(12.0, BAND) == 1.0


def test_detect_adequacy_too_few_decays_linearly_to_floor():
    # lo 未満は x/lo で線形に減衰、ただし floor が下限
    assert detect_adequacy(0.5, BAND) == pytest.approx(0.5)   # max(0.4, 0.5)
    assert detect_adequacy(0.2, BAND) == pytest.approx(0.4)   # floor が効く


def test_detect_adequacy_too_many_decays_to_floor():
    # hi 超は hi/x で減衰、floor が下限
    assert detect_adequacy(24.0, BAND) == pytest.approx(0.5)  # max(0.4, 12/24=0.5)
    assert detect_adequacy(100.0, BAND) == pytest.approx(0.4)  # 12/100=0.12 < floor


def test_detect_adequacy_peaks_inside_band():
    # 帯内が帯外より必ず高い（多すぎず少なすぎずが最大）
    assert detect_adequacy(3.0, BAND) > detect_adequacy(0.5, BAND)
    assert detect_adequacy(3.0, BAND) > detect_adequacy(20.0, BAND)


# =============================================================
# calculate_custom_score
# =============================================================

def _metrics(strat_multiplier=1.2, max_drawdown_pct=-10.0, total_trades=756,
             expectancy_lcb=None):
    """テスト用 metrics 生成（必要キーのみ）。

    expectancy_lcb を渡した時のみキーを含める（None ならキー自体を持たせない＝
    LCBゲート後方互換のケースを再現）。
    """
    m = {
        'strat_multiplier': strat_multiplier,
        'max_drawdown_pct': max_drawdown_pct,
        'total_trades': total_trades,
    }
    if expectancy_lcb is not None:
        m['expectancy_lcb'] = expectancy_lcb
    return m


def test_none_metrics_returns_sentinel():
    assert calculate_custom_score(None, 252, 20.0, BAND) == -1000.0
    assert calculate_custom_score({}, 252, 20.0, BAND) == -1000.0


def test_below_min_trades_gate():
    # 5件未満は勾配ゲート（-100 + trades*20）。Optuna が「近づいている」ことを学べる
    m = _metrics(total_trades=3)
    assert calculate_custom_score(m, 252, 20.0, BAND) == pytest.approx(-40.0)


def test_score_uses_period_cagr_formula():
    # 1年（252営業日）・strat_mult=1.2 → CAGR=20%。dd=10%(<=閾値20) → penalty=1+sqrt(10)*0.1
    # avg=3件/日（帯内） → factor=1.0。score = 20 / (1+0.31623) ≈ 15.195
    m = _metrics(strat_multiplier=1.2, max_drawdown_pct=-10.0, total_trades=756)
    expected = 20.0 / (1.0 + math.sqrt(10.0) * 0.1)
    assert calculate_custom_score(m, 252, 20.0, BAND) == pytest.approx(expected, abs=1e-6)


def test_cagr_is_annualized():
    # 2年（504営業日）で strat_mult=1.44 → 年率CAGR=20%（1.44=1.2^2）
    m = _metrics(strat_multiplier=1.44, max_drawdown_pct=-10.0, total_trades=1512)
    expected = 20.0 / (1.0 + math.sqrt(10.0) * 0.1)
    assert calculate_custom_score(m, 504, 20.0, BAND) == pytest.approx(expected, abs=1e-3)


def test_higher_cagr_scores_higher():
    # DD・件数固定でCAGRが高いほどスコアが高い（成長を最適化している）
    low = calculate_custom_score(_metrics(strat_multiplier=1.2), 252, 20.0, BAND)
    high = calculate_custom_score(_metrics(strat_multiplier=1.4), 252, 20.0, BAND)
    assert high > low


def test_dd_penalty_beyond_threshold_collapses_score():
    # CAGR・件数固定でDDが閾値を超えると急落する
    m_low_dd = _metrics(strat_multiplier=1.3, max_drawdown_pct=-10.0)
    m_high_dd = _metrics(strat_multiplier=1.3, max_drawdown_pct=-40.0)  # 閾値20超
    s_low = calculate_custom_score(m_low_dd, 252, 20.0, BAND)
    s_high = calculate_custom_score(m_high_dd, 252, 20.0, BAND)
    assert s_low > s_high
    assert s_high < s_low * 0.5  # 2乗ペナルティで大きく沈む


def test_detection_out_of_band_discounts_score():
    # CAGR・DD固定で、検出件数が帯外（過多）だとスコアが割り引かれる
    in_band = calculate_custom_score(
        _metrics(strat_multiplier=1.3, total_trades=756), 252, 20.0, BAND)   # 3件/日
    over = calculate_custom_score(
        _metrics(strat_multiplier=1.3, total_trades=5040), 252, 20.0, BAND)  # 20件/日
    assert in_band > over
    # factor 比（1.0 vs max(0.4, 12/20=0.6)）に一致
    assert over == pytest.approx(in_band * 0.6, rel=1e-6)


def test_negative_cagr_penalized_by_dd_depth():
    # CAGR<=0 は成長がマイナス。DDが深いほどさらに沈める（detect係数は掛けない）
    m = _metrics(strat_multiplier=0.8, max_drawdown_pct=-15.0, total_trades=756)  # CAGR=-20%
    assert calculate_custom_score(m, 252, 20.0, BAND) == pytest.approx(-35.0)


# =============================================================
# expectancy_lcb ソフトゲート（CAGRの右裾過適合対策）
# =============================================================

def test_lcb_gate_discounts_nonpositive_lcb():
    # CAGR>0 でも 1トレードLCB<=0（単価で勝てていない＝生存者バイアス疑い）は割り引く
    base = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=0.5), 252, 20.0, BAND)
    gated = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=-0.1), 252, 20.0, BAND)
    assert gated < base
    assert gated == pytest.approx(base * 0.5, rel=1e-9)  # デフォルト係数 0.5


def test_lcb_gate_boundary_zero_is_gated():
    # LCB==0 は「>0 でない」ので割り引く（境界はゲート側）
    base = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=0.5), 252, 20.0, BAND)
    at_zero = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=0.0), 252, 20.0, BAND)
    assert at_zero == pytest.approx(base * 0.5, rel=1e-9)


def test_lcb_gate_no_effect_when_positive():
    # LCB>0 は減点なし（キー無し=後方互換ケースと一致）
    absent = calculate_custom_score(_metrics(strat_multiplier=1.3), 252, 20.0, BAND)
    positive = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=0.5), 252, 20.0, BAND)
    assert positive == pytest.approx(absent, rel=1e-12)


def test_lcb_gate_skipped_when_key_absent():
    # expectancy_lcb キーが無い metrics は割り引かない（後方互換）
    m = _metrics(strat_multiplier=1.3)
    assert 'expectancy_lcb' not in m
    with_pos = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=1.0), 252, 20.0, BAND)
    assert calculate_custom_score(m, 252, 20.0, BAND) == pytest.approx(with_pos, rel=1e-12)


def test_lcb_gate_penalty_is_configurable():
    base = calculate_custom_score(_metrics(strat_multiplier=1.3, expectancy_lcb=0.5), 252, 20.0, BAND)
    gated = calculate_custom_score(
        _metrics(strat_multiplier=1.3, expectancy_lcb=-0.1), 252, 20.0, BAND, lcb_gate_penalty=0.25)
    assert gated == pytest.approx(base * 0.25, rel=1e-9)


# =============================================================
# resolve_detect_band（2026-07-21 追加）
#
# 背景: detect_band(ソフトな実用帯)が、既に戦略ごとに個別校正されている
# prune_bounds(min/max_avg_hits_per_day、ハードな門番)と無関係な独立の
# グローバル既定値を持っていたため、ハード側だけ緩めた戦略でもソフト側は
# 一律のグローバル値のまま割引かれる不整合があった。detect_lo/hiの既定値を
# min_avg/max_avg（呼び出し側で解決済みのハード境界）に連動させることで解消する。
# 解決順序は従来通り: 戦略側 > [optimization_pruning] > ハード境界(min_avg/max_avg)。
# =============================================================

def test_resolve_detect_band_defaults_to_hard_prune_bounds():
    # detect_lo/hi/floorが戦略側にもグローバル設定にも無い場合、
    # ソフト帯はハードプルーニングのmin_avg/max_avgにそのまま追従する
    band = resolve_detect_band(strat_base={}, prune_conf={}, min_avg=0.05, max_avg=3.0)
    assert band == (0.05, 3.0, 0.4)


def test_resolve_detect_band_strategy_override_wins():
    # 戦略側で明示指定すればハード境界より優先される
    strat_base = {'detect_lo': 2.0, 'detect_hi': 8.0, 'detect_floor': 0.3}
    band = resolve_detect_band(strat_base=strat_base, prune_conf={}, min_avg=0.05, max_avg=3.0)
    assert band == (2.0, 8.0, 0.3)


def test_resolve_detect_band_global_override_wins_over_hard_bounds():
    # [optimization_pruning] のグローバル指定は、戦略側指定が無ければハード境界より優先される
    prune_conf = {'detect_lo': 1.0, 'detect_hi': 12.0}
    band = resolve_detect_band(strat_base={}, prune_conf=prune_conf, min_avg=0.05, max_avg=3.0)
    assert band == (1.0, 12.0, 0.4)


def test_resolve_detect_band_strategy_override_wins_over_global():
    # 戦略側指定はグローバル指定よりも優先される（解決順序: 戦略 > グローバル > ハード境界）
    strat_base = {'detect_lo': 2.0}
    prune_conf = {'detect_lo': 1.0, 'detect_hi': 12.0}
    band = resolve_detect_band(strat_base=strat_base, prune_conf=prune_conf, min_avg=0.05, max_avg=3.0)
    assert band == (2.0, 12.0, 0.4)
