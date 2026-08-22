"""
test_optimization_score.py — 最適化バックテストの目的関数（再設計後）の純関数テスト。

2026-08-22 再設計（doc/in_progress/objective_quality_first_plan.md）:
  score = avg_gain × (1 - dd_est/100)  （検出件数が quality_gate=[lo, hi] の内側の場合のみ）
  - 主指標を period_CAGR（資産成長）→ avg_gain（1トレード平均リターン% = 質）へ差し替え
  - 検出件数は掛け算（detect_adequacy）ではなくゲート（帯の外は失格・帯の内はスコアに無関係）
  - DD ペナルティは閾値付き2乗をやめ、dd_est = |型1DD| × avg_slots**0.5 / 9.1（型3相当への
    換算）を使った回復コスト型 `1 - dd_est/100` に変更（旧: `doc/completed/objective_redesign_plan.md`）
"""
import pytest

from optimization_runner import (calculate_custom_score, detect_adequacy, resolve_detect_band,
                                 resolve_prune_floor, calculate_prune_penalty)


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
# calculate_custom_score（2026-08-22 再設計: 質 × DDペナルティ、検出件数はゲート）
#
# 背景（doc/in_progress/objective_quality_first_plan.md §2.3/§2.4）:
#   旧: score = period_CAGR / dd_penalty × detect_adequacy(hits/day) × lcb_gate
#   新: 検出件数が quality_gate=[lo, hi] の外 → 失格
#       検出件数が quality_gate=[lo, hi] の内 → score = avg_gain × (1 - dd_est/100)
#   検出件数は掛け算ではなくゲート。帯の中にいる限り取引数はスコアに影響しない
#   （「量を増やして点を稼ぐ」経路を消すのが本改修の核心）。
#   dd_est = |型1DD| × avg_slots**0.5 / 9.1（型3 相当への換算。§2.4）。
# =============================================================

GATE = (0.3, 12.0)  # (lo, hi) 検出件数ゲート


def _metrics(avg_gain=3.0, max_drawdown_pct=-10.0, total_trades=756,
             avg_slots=1.0, expectancy_lcb=None):
    """テスト用 metrics 生成（必要キーのみ）。

    expectancy_lcb を渡した時のみキーを含める（None ならキー自体を持たせない＝
    LCBゲート後方互換のケースを再現）。
    """
    m = {
        'avg_gain': avg_gain,
        'max_drawdown_pct': max_drawdown_pct,
        'total_trades': total_trades,
        'avg_slots': avg_slots,
    }
    if expectancy_lcb is not None:
        m['expectancy_lcb'] = expectancy_lcb
    return m


def test_none_metrics_returns_sentinel():
    assert calculate_custom_score(None, 252, GATE) == -1000.0
    assert calculate_custom_score({}, 252, GATE) == -1000.0


def test_below_min_trades_gate():
    # 5件未満は勾配ゲート（-100 + trades*20）。Optuna が「近づいている」ことを学べる
    m = _metrics(total_trades=3)
    assert calculate_custom_score(m, 252, GATE) == pytest.approx(-40.0)


def test_detection_below_gate_lo_is_disqualified():
    # 0.29件/日（下限0.3をわずかに下回る）は失格（大きな負値）
    m = _metrics(total_trades=int(1000 * 0.299), avg_gain=10.0)  # 総日数1000で0.299件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score < -100.0


def test_detection_at_gate_lo_boundary_is_not_disqualified():
    # 0.3件/日ちょうどはゲート内（境界含む）。通常のスコア計算が行われる
    m = _metrics(total_trades=300, avg_gain=3.0, max_drawdown_pct=0.0)  # 総日数1000で0.3件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score == pytest.approx(3.0)  # DD=0 なので score=avg_gain そのまま


def test_detection_above_gate_hi_is_disqualified():
    # 20件/日（上限12超）は失格
    m = _metrics(total_trades=252 * 20, avg_gain=10.0)
    score = calculate_custom_score(m, 252, GATE)
    assert score < -100.0


def test_detection_at_gate_hi_boundary_is_not_disqualified():
    # 12件/日ちょうどはゲート内（境界含む）
    m = _metrics(total_trades=12000, avg_gain=3.0, max_drawdown_pct=0.0)  # 総日数1000で12件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score == pytest.approx(3.0)


def test_quantity_within_band_does_not_affect_score():
    # 帯内である限り、検出件数はスコアに影響しない（量を稼ぐ経路が消えていることの回帰）
    m_low = _metrics(avg_gain=3.0, total_trades=int(252 * 1.0), avg_slots=2.0)   # 1件/日
    m_high = _metrics(avg_gain=3.0, total_trades=int(252 * 10.0), avg_slots=2.0)  # 10件/日
    assert calculate_custom_score(m_low, 252, GATE) == pytest.approx(
        calculate_custom_score(m_high, 252, GATE))


def test_score_is_quality_times_dd_factor():
    # score = avg_gain × (1 - dd_est/100), dd_est = |max_drawdown_pct| × avg_slots**0.5 / 9.1
    m = _metrics(avg_gain=5.0, max_drawdown_pct=-40.0, total_trades=756, avg_slots=2.0)
    dd_est = 40.0 * (2.0 ** 0.5) / 9.1
    expected = 5.0 * (1.0 - dd_est / 100.0)
    assert calculate_custom_score(m, 252, GATE) == pytest.approx(expected, rel=1e-9)


def test_known_dd_conversion_matches_calibration_example():
    # 型1DD 95.1・avg_slots 8.26 → dd_est ≈ 30.0（計画書 §2.4 の実測校正例）
    m = _metrics(avg_gain=1.0, max_drawdown_pct=-95.1, total_trades=756, avg_slots=8.26)
    dd_est = 95.1 * (8.26 ** 0.5) / 9.1
    assert dd_est == pytest.approx(30.0, abs=0.5)
    score = calculate_custom_score(m, 252, GATE)
    assert score == pytest.approx(1.0 * (1.0 - dd_est / 100.0), rel=1e-9)


def test_higher_quality_scores_higher():
    # DD・件数固定で質（avg_gain）が高いほどスコアが高い
    low = calculate_custom_score(_metrics(avg_gain=2.0), 252, GATE)
    high = calculate_custom_score(_metrics(avg_gain=5.0), 252, GATE)
    assert high > low


def test_deeper_dd_lowers_score():
    # 質・件数固定でDDが深いほどスコアが下がる
    m_low_dd = _metrics(avg_gain=5.0, max_drawdown_pct=-10.0, avg_slots=2.0)
    m_high_dd = _metrics(avg_gain=5.0, max_drawdown_pct=-60.0, avg_slots=2.0)
    s_low = calculate_custom_score(m_low_dd, 252, GATE)
    s_high = calculate_custom_score(m_high_dd, 252, GATE)
    assert s_low > s_high


def test_dd_conversion_divisor_is_configurable():
    # dd_divisor を小さくすると dd_est が大きくなり、スコアが下がる（ハードコードしない）
    m = _metrics(avg_gain=5.0, max_drawdown_pct=-90.0, avg_slots=9.0)
    default_score = calculate_custom_score(m, 252, GATE)
    tightened = calculate_custom_score(m, 252, GATE, dd_divisor=5.0)
    assert tightened < default_score


def test_dd_conversion_slots_exponent_is_configurable():
    # slots指数を変えられること（0にするとavg_slotsの影響が消える=sqrt補正なし相当）
    m = _metrics(avg_gain=5.0, max_drawdown_pct=-45.5, avg_slots=9.0)
    score_no_slots_effect = calculate_custom_score(m, 252, GATE, dd_slots_exponent=0.0)
    expected_dd_est = 45.5 * (9.0 ** 0.0) / 9.1  # = 45.5/9.1 = 5.0
    assert expected_dd_est == pytest.approx(5.0)
    assert score_no_slots_effect == pytest.approx(5.0 * (1.0 - 5.0 / 100.0), rel=1e-9)


def test_missing_avg_slots_falls_back_to_raw_dd_without_conversion():
    # 既存 trial には avg_slots が保存されていないため、その場合は換算せず素の DD を使う
    # （フォールバック方針。doc/in_progress/objective_quality_first_plan.md §5 検証6）
    m = _metrics(avg_gain=5.0, max_drawdown_pct=-30.0)
    del m['avg_slots']
    score = calculate_custom_score(m, 252, GATE)
    expected = 5.0 * (1.0 - 30.0 / 100.0)
    assert score == pytest.approx(expected, rel=1e-9)


# =============================================================
# expectancy_lcb ソフトゲート（質主軸でも単価エッジの下限が無いスクリーンを減点する）
# =============================================================

def test_lcb_gate_discounts_nonpositive_lcb():
    # 1トレードLCB<=0（単価で勝てていない＝生存者バイアス疑い）は割り引く
    base = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=0.5), 252, GATE)
    gated = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=-0.1), 252, GATE)
    assert gated < base
    assert gated == pytest.approx(base * 0.5, rel=1e-9)  # デフォルト係数 0.5


def test_lcb_gate_boundary_zero_is_gated():
    # LCB==0 は「>0 でない」ので割り引く（境界はゲート側）
    base = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=0.5), 252, GATE)
    at_zero = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=0.0), 252, GATE)
    assert at_zero == pytest.approx(base * 0.5, rel=1e-9)


def test_lcb_gate_no_effect_when_positive():
    # LCB>0 は減点なし（キー無し=後方互換ケースと一致）
    absent = calculate_custom_score(_metrics(avg_gain=5.0), 252, GATE)
    positive = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=0.5), 252, GATE)
    assert positive == pytest.approx(absent, rel=1e-12)


def test_lcb_gate_skipped_when_key_absent():
    # expectancy_lcb キーが無い metrics は割り引かない（後方互換）
    m = _metrics(avg_gain=5.0)
    assert 'expectancy_lcb' not in m
    with_pos = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=1.0), 252, GATE)
    assert calculate_custom_score(m, 252, GATE) == pytest.approx(with_pos, rel=1e-12)


def test_lcb_gate_penalty_is_configurable():
    base = calculate_custom_score(_metrics(avg_gain=5.0, expectancy_lcb=0.5), 252, GATE)
    gated = calculate_custom_score(
        _metrics(avg_gain=5.0, expectancy_lcb=-0.1), 252, GATE, lcb_gate_penalty=0.25)
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


# =============================================================
# resolve_prune_floor — fast_prune の発火点を「速度の安全弁」まで下げる（2026-08-15）
#
# 背景: fast_prune は学習時間短縮のための仕組みなのに、発火点が実用帯の下限
# （min_avg_hits_per_day = 1.0）に置かれていたため実質的に評価を支配していた。
# base_penalty=-100 の不連続に加え、罰点が return で残り期間の評価をスキップするため、
# Bull で健全（1.09）でも Bear が僅かに薄い（0.91）だけでスコアが -323 に確定していた。
# =============================================================

def test_prune_floor_defaults_to_global_value():
    """既定では [optimization_pruning] の prune_floor_hits_per_day が使われること。"""
    floor = resolve_prune_floor(strat_base={}, prune_conf={'prune_floor_hits_per_day': 0.2},
                                min_avg=1.0)
    assert floor == 0.2


def test_prune_floor_never_tightens_a_strategy_specific_min_avg():
    """戦略側が min_avg を floor より低く設定している場合、その値を尊重すること。

    E2 は min_avg_hits_per_day=0.02、E1 は 0.05 を個別設定している。
    ここで floor(0.2) を無条件に採用すると **これらの戦略が逆に厳しくなる**。
    """
    floor = resolve_prune_floor(strat_base={}, prune_conf={'prune_floor_hits_per_day': 0.2},
                                min_avg=0.02)
    assert floor == 0.02


def test_prune_floor_strategy_override_wins():
    """戦略側の prune_floor_hits_per_day が最優先されること。"""
    floor = resolve_prune_floor(strat_base={'prune_floor_hits_per_day': 0.05},
                                prune_conf={'prune_floor_hits_per_day': 0.2}, min_avg=1.0)
    assert floor == 0.05


def test_no_penalty_between_prune_floor_and_practical_band():
    """floor 以上・実用帯未満（0.2〜1.0）では罰点が出ないこと（＝実スコアが計算される）。

    回帰テスト: B5 は Bear 2022 で 0.91 件/日 だったため -323 の罰点を受け、
    Bull 期間（1.09 で健全）が一切評価されないままスコアが確定していた。
    """
    bounds = (0.2, 5.0, 5.0)   # (prune_floor, max_avg, min_hit_rate)
    for avg in (0.91, 0.65, 0.61, 0.98, 0.21):
        assert calculate_prune_penalty(avg, hit_rate_pct=50.0, bounds=bounds) is None, (
            f'avg={avg} で罰点が出た（floor=0.2 未満でないのに足切りされている）'
        )


def test_penalty_still_fires_below_prune_floor():
    """floor 未満（明らかに死んでいる領域）では従来どおり罰点が出ること。"""
    bounds = (0.2, 5.0, 5.0)
    penalty = calculate_prune_penalty(0.05, hit_rate_pct=50.0, bounds=bounds)
    assert penalty is not None and penalty < 0


def test_detect_adequacy_gives_gradient_below_practical_band():
    """0.2〜1.0 は detect_adequacy が連続的に割り引くこと（崖ではなく傾斜）。"""
    band = (1.0, 5.0, 0.4)   # (lo, hi, floor)
    assert detect_adequacy(1.0, band) == 1.0
    # lo 未満は x/lo で線形に減衰し、floor が下限
    assert detect_adequacy(0.91, band) == pytest.approx(0.91)
    assert detect_adequacy(0.65, band) == pytest.approx(0.65)
    assert detect_adequacy(0.2, band) == pytest.approx(0.4)   # floor で下げ止まる
    # 単調非増加であること（崖が無い）
    vals = [detect_adequacy(x / 100, band) for x in range(20, 101)]
    assert all(a <= b + 1e-9 for a, b in zip(vals, vals[1:]))
