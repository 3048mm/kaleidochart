"""
test_optimization_score.py — 型1最適化の目的関数（再設計後）の純関数テスト。

再設計の要点（doc/in_progress/objective_redesign_plan.md）:
  score = period_CAGR / dd_penalty × detect_adequacy(avg_hits_per_day)
  - 主指標を expectancy_lcb（トレード単価）→ 期間CAGR（複利での資産成長）へ差し替え
  - DDペナルティ（閾値付き2乗・max_allowed_dd）は CAGR に対して掛ける（Calmar型）
  - 検出件数は実用帯 detect_band=(lo, hi, floor) の係数でソフトに選好する
"""
import math
import pytest

from optimization_runner import calculate_custom_score, detect_adequacy


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

def _metrics(strat_multiplier=1.2, max_drawdown_pct=-10.0, total_trades=756):
    """テスト用 metrics 生成（必要キーのみ）。"""
    return {
        'strat_multiplier': strat_multiplier,
        'max_drawdown_pct': max_drawdown_pct,
        'total_trades': total_trades,
    }


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
