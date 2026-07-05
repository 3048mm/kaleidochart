"""backtest_report.calculate_metrics の統計拡張（LCB / MTM ドローダウン）のテスト。

背景（doc/completed/backtest_optimization_hardening_plan.md 修正3・5）:
- 修正3: 少数トレードのまぐれを罰するため、期待値の下側信頼限界
  expectancy_lcb = expectancy − 2 × SE（SE = std/√n）を指標に追加。
- 修正5: DD を「exit日ソートの確定損益累積」から「日次 mark-to-market
  エクイティカーブ」ベースへ変更。含み損の谷・同時被弾を DD に反映する。
"""
import math
from datetime import date, timedelta

import pandas as pd
import pytest

from backend.backtest.backtest_simulator import TradeResult
from backend.backtest.backtest_report import calculate_metrics


def _trade(symbol_id, entry, exit_, entry_price, exit_price, pnl,
           partial_pnl=None, partial_date=None, ticker=None):
    t = TradeResult(
        symbol_id=symbol_id, ticker=ticker or f"SYM{symbol_id}",
        entry_date=entry, exit_date=exit_,
        entry_price=entry_price, exit_price=exit_price,
        pnl_pct=pnl, holding_days=(exit_ - entry).days,
        exit_reason="test", partial_exit_pnl_pct=partial_pnl,
    )
    if partial_date is not None:
        t.partial_exit_date = partial_date
    return t


# =============================================================
# 修正3: expectancy の下側信頼限界（LCB）
# =============================================================

def test_expectancy_lcb_fields():
    """pnl_std / expectancy_se / expectancy_lcb が正しく計算されること。"""
    d0 = date(2024, 1, 2)
    pnls = [10.0, -5.0, 5.0, -2.0, 7.0]
    trades = [
        _trade(i + 1, d0 + timedelta(days=i), d0 + timedelta(days=i + 5),
               100.0, 100.0 * (1 + p / 100), p)
        for i, p in enumerate(pnls)
    ]
    m = calculate_metrics(trades)

    n = len(pnls)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)  # 標本分散 (ddof=1)
    std = math.sqrt(var)
    se = std / math.sqrt(n)

    assert m["pnl_std"] == pytest.approx(std)
    assert m["expectancy_se"] == pytest.approx(se)
    assert m["expectancy_lcb"] == pytest.approx(mean - 2.0 * se)
    # expectancy（勝率×平均益+負率×平均損）は代数的に平均と一致する
    assert m["expectancy"] == pytest.approx(mean)


def test_expectancy_lcb_single_trade_falls_back_to_expectancy():
    """トレード1件では SE が定義できないため LCB = expectancy（<5件ゲートが別途罰する）。"""
    d0 = date(2024, 1, 2)
    trades = [_trade(1, d0, d0 + timedelta(days=5), 100.0, 108.0, 8.0)]
    m = calculate_metrics(trades)
    assert m["pnl_std"] == 0.0
    assert m["expectancy_se"] == 0.0
    assert m["expectancy_lcb"] == pytest.approx(m["expectancy"])


def test_lcb_punishes_high_variance():
    """平均が同じでも分散が大きいほど LCB が低いこと（まぐれの自動減点）。"""
    d0 = date(2024, 1, 2)
    stable = [_trade(i + 1, d0 + timedelta(days=i), d0 + timedelta(days=i + 3),
                     100.0, 102.0, 2.0) for i in range(10)]
    lumpy_pnls = [40.0, -2.0, -3.0, -2.0, -3.0, -2.0, -3.0, -2.0, -3.0, 0.0]  # 平均 2.0
    lumpy = [_trade(i + 1, d0 + timedelta(days=i), d0 + timedelta(days=i + 3),
                    100.0, 100.0 * (1 + p / 100), p) for i, p in enumerate(lumpy_pnls)]

    m_stable = calculate_metrics(stable)
    m_lumpy = calculate_metrics(lumpy)
    assert m_stable["expectancy"] == pytest.approx(m_lumpy["expectancy"])
    assert m_stable["expectancy_lcb"] > m_lumpy["expectancy_lcb"]


# =============================================================
# 修正5: mark-to-market ドローダウン
# =============================================================

def _price_frame(symbol_id, closes_by_date):
    rows = [{"symbol_id": symbol_id, "date": d,
             "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for d, c in closes_by_date.items()]
    return pd.DataFrame(rows).sort_values("date")


def test_mtm_drawdown_captures_open_position_dip():
    """保有中の含み損の谷が MTM DD に現れ、旧 additive DD には現れないこと。

    エントリー100 → 保有中に60（-40%）まで下落 → 110（+10%）で exit。
    旧方式: 確定損益 [+10] の累積なので DD=0。
    MTM 方式: エクイティが -40 まで沈むので DD=40（avg_slots=1）。
    """
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(6)]
    closes = {dates[0]: 100.0, dates[1]: 90.0, dates[2]: 60.0,
              dates[3]: 80.0, dates[4]: 100.0, dates[5]: 110.0}
    trades = [_trade(1, dates[0], dates[5], 100.0, 110.0, 10.0)]
    price_by_sym = {1: _price_frame(1, closes)}

    m = calculate_metrics(trades, price_by_sym=price_by_sym, trading_dates=dates)

    assert m["max_drawdown_legacy_pct"] == pytest.approx(0.0)
    assert m["max_drawdown_pct"] == pytest.approx(-40.0)


def test_mtm_drawdown_without_price_data_falls_back_to_legacy():
    """price_by_sym 未指定の呼び出し（後方互換）では legacy DD を返すこと。"""
    d0 = date(2024, 1, 2)
    trades = [
        _trade(1, d0, d0 + timedelta(days=2), 100.0, 110.0, 10.0),
        _trade(2, d0 + timedelta(days=3), d0 + timedelta(days=5), 100.0, 94.0, -6.0),
    ]
    m = calculate_metrics(trades)
    assert m["max_drawdown_pct"] == pytest.approx(m["max_drawdown_legacy_pct"])
    # +10 のピークから -6（正規化前の生 DD = 6。avg_slots(>=1) で割られるため上限 -6）
    assert -6.0 <= m["max_drawdown_pct"] < 0.0


def test_mtm_partial_exit_reduces_open_exposure():
    """部分利確後は確定分（1/3）が固定され、残り 2/3 のみ時価評価されること。

    エントリー100 → 120 で部分利確（+20% × 1/3 確定）→ 100 に逆戻り → 100 で exit。
    部分利確日以降 100 に戻った日の寄与 = 20×(1/3) + 0×(2/3) ≈ +6.67。
    もし全量時価評価なら 0 になるので、DD の谷が浅くなることで検証する。
    """
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
    closes = {dates[0]: 100.0, dates[1]: 120.0, dates[2]: 100.0,
              dates[3]: 100.0, dates[4]: 100.0}
    ratio = 0.333
    total_pnl = 20.0 * ratio + 0.0 * (1 - ratio)
    trades = [_trade(1, dates[0], dates[4], 100.0, 100.0, total_pnl,
                     partial_pnl=20.0, partial_date=dates[1])]
    price_by_sym = {1: _price_frame(1, closes)}

    m = calculate_metrics(trades, price_by_sym=price_by_sym, trading_dates=dates,
                          partial_ratio=ratio)

    # エクイティ推移: d1=+20（ピーク）→ d2以降 = 20×1/3 ≈ +6.66
    # → DD = 20 − 6.66 ≈ 13.34。全量時価評価なら DD = 20 になってしまう。
    expected_dd = 20.0 - 20.0 * ratio
    assert m["max_drawdown_pct"] == pytest.approx(-expected_dd, abs=0.01)


def test_mtm_concurrent_trades_normalized_by_avg_slots():
    """同時保有トレードの DD が Little's Law の avg_slots で正規化されること。"""
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(4)]
    # 2銘柄が同一期間に同時被弾: どちらも -10% まで沈んで 0% で exit
    closes = {dates[0]: 100.0, dates[1]: 90.0, dates[2]: 95.0, dates[3]: 100.0}
    trades = [
        _trade(1, dates[0], dates[3], 100.0, 100.0, 0.0),
        _trade(2, dates[0], dates[3], 100.0, 100.0, 0.0),
    ]
    price_by_sym = {1: _price_frame(1, closes), 2: _price_frame(2, closes)}

    m = calculate_metrics(trades, price_by_sym=price_by_sym, trading_dates=dates)

    # 生のエクイティの谷 = -20（-10 × 2銘柄同時）
    # avg_slots = arrival_rate × avg_holding ≈ 2 で正規化 → DD ≈ 10 前後
    # （厳密値は trading_days 換算に依存するため範囲で検証）
    dd = abs(m["max_drawdown_pct"])
    assert 5.0 < dd < 20.0, f"正規化されていない可能性: dd={dd}"
    raw_dd_if_not_normalized = 20.0
    assert dd < raw_dd_if_not_normalized
