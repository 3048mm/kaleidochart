"""
test_optimization_score.py — 最適化バックテストの目的関数（再設計後）の純関数テスト。

2026-08-24 改訂（doc/in_progress/objective_quality_first_plan.md）:
  score = geo_mean_gain  （検出件数が quality_gate=[lo, hi] の内側の場合のみ）
  - 主指標は geo_mean_gain（1トレードあたりの**幾何平均**リターン%）。
    算術平均（avg_gain）は分散に無関心で「95%が負けで上位5%が全部稼ぐ」構成を
    高く評価してしまうため、大負けを罰する幾何平均へ差し替えた。
    幾何平均は勝率を内在的に要求するので、勝率を明示的に掛ける必要がない（二重計上の回避）。
  - 検出件数は掛け算（detect_adequacy）ではなくゲート（帯の外は失格・帯の内はスコアに無関係）
  - **DD 項は削除**。型1 DD は型3 CAGR と +0.770 の正相関を持つため、減点すると
    良い戦略ほど罰せられる（実測39観測で相関 +0.722→+0.375 と半減）。DD の管理は型3 の責務。
  - 実測（型3の39観測）: 勝率との整合 算術+0.333 → 幾何+0.498 /
    分散との結びつき 算術+0.907 → 幾何+0.719
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
#       検出件数が quality_gate=[lo, hi] の内 → score = geo_mean_gain
#   検出件数は掛け算ではなくゲート。帯の中にいる限り取引数はスコアに影響しない
#   （「量を増やして点を稼ぐ」経路を消すのが本改修の核心）。
# =============================================================

GATE = (0.3, 12.0)  # (lo, hi) 検出件数ゲート


def _metrics(geo_mean_gain=3.0, max_drawdown_pct=-10.0, total_trades=756,
             avg_slots=1.0, expectancy_lcb=None, win_rate=0.4, avg_gain=None):
    """テスト用 metrics 生成（必要キーのみ）。

    expectancy_lcb を渡した時のみキーを含める（None ならキー自体を持たせない＝
    LCBゲート後方互換のケースを再現）。
    """
    m = {
        'geo_mean_gain': geo_mean_gain,
        'avg_gain': avg_gain if avg_gain is not None else geo_mean_gain,
        'max_drawdown_pct': max_drawdown_pct,
        'total_trades': total_trades,
        'avg_slots': avg_slots,
        'win_rate': win_rate,
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
    m = _metrics(total_trades=int(1000 * 0.299), geo_mean_gain=10.0)  # 総日数1000で0.299件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score < -100.0


def test_detection_at_gate_lo_boundary_is_not_disqualified():
    # 0.3件/日ちょうどはゲート内（境界含む）。通常のスコア計算が行われる
    m = _metrics(total_trades=300, geo_mean_gain=3.0)  # 総日数1000で0.3件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score == pytest.approx(3.0)  # win_rate=1.0 なので score=avg_gain そのまま


def test_detection_above_gate_hi_is_disqualified():
    # 20件/日（上限12超）は失格
    m = _metrics(total_trades=252 * 20, geo_mean_gain=10.0)
    score = calculate_custom_score(m, 252, GATE)
    assert score < -100.0


def test_detection_at_gate_hi_boundary_is_not_disqualified():
    # 12件/日ちょうどはゲート内（境界含む）
    m = _metrics(total_trades=12000, geo_mean_gain=3.0)  # 総日数1000で12件/日
    score = calculate_custom_score(m, 1000, GATE)
    assert score == pytest.approx(3.0)


def test_quantity_within_band_does_not_affect_score():
    # 帯内である限り、検出件数はスコアに影響しない（量を稼ぐ経路が消えていることの回帰）
    m_low = _metrics(geo_mean_gain=3.0, total_trades=int(252 * 1.0), avg_slots=2.0)   # 1件/日
    m_high = _metrics(geo_mean_gain=3.0, total_trades=int(252 * 10.0), avg_slots=2.0)  # 10件/日
    assert calculate_custom_score(m_low, 252, GATE) == pytest.approx(
        calculate_custom_score(m_high, 252, GATE))


def test_score_is_geometric_mean():
    # score = geo_mean_gain（そのまま）
    m = _metrics(geo_mean_gain=3.33)
    assert calculate_custom_score(m, 648, GATE) == pytest.approx(3.33)


def test_win_rate_is_not_multiplied():
    # 2026-08-24: 勝率は掛けない（幾何平均が内在的に要求するため。二重計上の回避）
    a = calculate_custom_score(_metrics(geo_mean_gain=3.0, win_rate=0.25), 648, GATE)
    b = calculate_custom_score(_metrics(geo_mean_gain=3.0, win_rate=0.50), 648, GATE)
    assert a == pytest.approx(b)


def test_higher_quality_scores_higher():
    # 幾何平均が高いほどスコアが高い
    low = calculate_custom_score(_metrics(geo_mean_gain=2.0), 648, GATE)
    high = calculate_custom_score(_metrics(geo_mean_gain=5.0), 648, GATE)
    assert high > low


def test_falls_back_to_avg_gain_when_geo_absent():
    # 後方互換: 古い metrics（geo_mean_gain 無し）では avg_gain を使う
    m = _metrics(geo_mean_gain=3.0, avg_gain=5.0)
    del m['geo_mean_gain']
    assert calculate_custom_score(m, 648, GATE) == pytest.approx(5.0)


def test_drawdown_does_not_affect_score():
    # 2026-08-23: DD 項は削除された。型1 DD は型3 CAGR と正相関するため減点に使わない。
    # DD が違ってもスコアは変わらないことの回帰。
    shallow = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36,
                                              max_drawdown_pct=-5.0), 252, GATE)
    deep = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36,
                                           max_drawdown_pct=-90.0), 252, GATE)
    assert shallow == pytest.approx(deep, rel=1e-9)


def test_avg_slots_does_not_affect_score():
    # avg_slots は trial 属性として記録し続けるが、スコアには使わない
    a = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, avg_slots=2.0), 252, GATE)
    b = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, avg_slots=20.0), 252, GATE)
    assert a == pytest.approx(b, rel=1e-9)


def test_missing_win_rate_does_not_affect_score():
    # 2026-08-24: 勝率はスコアに使わないので、キーが無くても影響しない
    m = _metrics(geo_mean_gain=5.0)
    del m['win_rate']
    assert calculate_custom_score(m, 252, GATE) == pytest.approx(5.0)


# =============================================================
# 赤字期間のガード（2026-08-23 追加）
#
# 掛け算のペナルティは、スコアが負のとき「罰」ではなく「ご褒美」になる
# （負の値に 0.2 を掛けるとゼロに近づく＝改善する）。スコアは2つの学習期間の
# 平均なので、「片方で大勝ち・片方で赤字かつ罰あり」の構成が最適解として
# 選ばれてしまっていた。赤字期間は掛け算の経路に入れないこと。
# =============================================================

def test_negative_avg_gain_returns_raw_value():
    # 赤字期間は素の avg_gain をそのまま返す（掛け算しない）
    m = _metrics(geo_mean_gain=-3.9, win_rate=0.25)
    assert calculate_custom_score(m, 648, GATE) == pytest.approx(-3.9)


def test_negative_avg_gain_is_not_improved_by_lcb_gate():
    # ★本命の回帰: lcb ゲートが赤字期間を「改善」してしまわないこと
    penalized = calculate_custom_score(
        _metrics(geo_mean_gain=-3.9, win_rate=0.25, expectancy_lcb=-0.5), 648, GATE)
    clean = calculate_custom_score(
        _metrics(geo_mean_gain=-3.9, win_rate=0.25, expectancy_lcb=0.5), 648, GATE)
    assert penalized == pytest.approx(clean)
    assert penalized == pytest.approx(-3.9)


def test_negative_avg_gain_is_monotone():
    # 赤字が深いほどスコアが低い（Optuna が勾配を学べる）
    mild = calculate_custom_score(_metrics(geo_mean_gain=-1.0, win_rate=0.3), 648, GATE)
    severe = calculate_custom_score(_metrics(geo_mean_gain=-8.0, win_rate=0.3), 648, GATE)
    assert severe < mild


def test_zero_avg_gain_returns_zero():
    # 境界。0 は掛け算経路に入れない（win_rate を掛けても 0 だが、明示的に確認する）
    assert calculate_custom_score(_metrics(geo_mean_gain=0.0, win_rate=0.36), 648, GATE) == pytest.approx(0.0)


def test_losing_period_never_outranks_profitable_one():
    # 赤字期間が黒字期間より高い点数になることはない（符号反転の回帰）
    losing = calculate_custom_score(
        _metrics(geo_mean_gain=-3.9, win_rate=0.25, expectancy_lcb=-0.5), 648, GATE)
    profitable = calculate_custom_score(
        _metrics(geo_mean_gain=0.5, win_rate=0.25, expectancy_lcb=-0.5), 648, GATE)
    assert profitable > losing


# =============================================================
# expectancy_lcb ソフトゲート（質主軸でも単価エッジの下限が無いスクリーンを減点する）
# =============================================================

def test_lcb_gate_discounts_nonpositive_lcb():
    # 1トレードLCB<=0（単価で勝てていない＝生存者バイアス疑い）は割り引く
    base = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=0.5), 252, GATE)
    gated = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=-0.1), 252, GATE)
    assert gated < base
    assert gated == pytest.approx(base * 0.5, rel=1e-9)  # デフォルト係数 0.5


def test_lcb_gate_boundary_zero_is_gated():
    # LCB==0 は「>0 でない」ので割り引く（境界はゲート側）
    base = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=0.5), 252, GATE)
    at_zero = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=0.0), 252, GATE)
    assert at_zero == pytest.approx(base * 0.5, rel=1e-9)


def test_lcb_gate_no_effect_when_positive():
    # LCB>0 は減点なし（キー無し=後方互換ケースと一致）
    absent = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36), 252, GATE)
    positive = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=0.5), 252, GATE)
    assert positive == pytest.approx(absent, rel=1e-12)


def test_lcb_gate_skipped_when_key_absent():
    # expectancy_lcb キーが無い metrics は割り引かない（後方互換）
    m = _metrics(geo_mean_gain=5.0, win_rate=0.36)
    assert 'expectancy_lcb' not in m
    with_pos = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=1.0), 252, GATE)
    assert calculate_custom_score(m, 252, GATE) == pytest.approx(with_pos, rel=1e-12)


def test_lcb_gate_penalty_is_configurable():
    base = calculate_custom_score(_metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=0.5), 252, GATE)
    gated = calculate_custom_score(
        _metrics(geo_mean_gain=5.0, win_rate=0.36, expectancy_lcb=-0.1), 252, GATE, lcb_gate_penalty=0.25)
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
