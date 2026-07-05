"""run_single_strategy のエントリー挙動テスト（再エントリー禁止・entry_mode・売買代金下限）。

背景（doc/completed/backtest_optimization_hardening_plan.md 修正1・2・4）:
- 修正4: 建玉存続中の同一銘柄シグナルはスキップ（1つの上昇ムーブが複数トレードに
  水増しされ、VCP 系の評価が壊れていた）。
- 修正1: entry_mode = "next_open" でシグナル翌営業日の寄付価格エントリー。
- 修正2: min_avg_dollar_volume_21 による最適化対象外のハード足切り。
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from backend.backtest.backtest_runner import run_single_strategy
from backend.backtest.backtest_simulator import ExitRules

# 10 営業日（土日を含まない連続日として単純化）
DATES = [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]


def _build_frames(signal_days=range(5), n_symbols=1, dollar_volume=None):
    """シグナルが決定論的に出る最小データセットを構築する。

    - カテゴリ「個別」・active=1 の銘柄
    - 戦略フィルタは min_change_1d_pct のみ（signal_days の日に 9.0、他は 0.0）
    - ema_21 = 999 で常に close < ema21 → エントリーの2営業日後に ema21_exit で決済
    - 価格は常に 100（ストップ・部分利確は発動しない）
    """
    symbols = pd.DataFrame([
        {"id": sid, "ticker": f"TST{sid}", "name": f"Test{sid}",
         "category": "個別", "active": 1}
        for sid in range(1, n_symbols + 1)
    ])

    price_rows = []
    ind_rows = []
    rank_rows = []
    for sid in range(1, n_symbols + 1):
        for i, d in enumerate(DATES):
            vol = 1_000_000
            close = 100.0
            if dollar_volume is not None:
                vol = int(dollar_volume[sid] / close)
            price_rows.append({
                "symbol_id": sid, "date": d,
                "open": 101.0, "high": 102.0, "low": 99.0, "close": close,
                "volume": vol, "market_cap": 1e9,
            })
            ind_rows.append({
                "symbol_id": sid, "date": d,
                "change_1d_pct": 9.0 if i in signal_days else 0.0,
                "ema_21": 999.0, "sma_50": 100.0, "atr_14": 1.0,
                "sma50_atr_mult": 0.0,
            })
            rank_rows.append({
                "symbol_id": sid, "date": d,
                "indicator_name": "rs_ratio_rank_e21", "percent_rank": 0.9,
            })

    df_prices = pd.DataFrame(price_rows)
    df_ind = pd.DataFrame(ind_rows)
    df_ranks = pd.DataFrame(rank_rows)
    df_tc = pd.DataFrame(columns=["theme_id", "symbol_id"])
    return symbols, df_prices, df_ind, df_ranks, df_tc


def _strategy(**overrides):
    strat = {
        "name": "TEST_reentry",
        "max_hits_per_day": 10,
        "sort_column": "rs_ratio_rank_e21",
        "sort_ascending": False,
        "min_change_1d_pct": 5.0,
    }
    strat.update(overrides)
    return strat


def _exit_rules():
    # ema21_exit（2日連続 close < ema21）だけが発動する設定
    return ExitRules(stop_loss_pct=-8.0, partial_take_profit_pct=999.0,
                     partial_take_profit_sma50_atr=999.0,
                     full_exit_ema21_consecutive_days=2,
                     full_exit_sma50_atr=999.0,
                     time_stop_days=7, failsafe_max_days=120)


def _run(strat, frames, **kwargs):
    symbols, df_prices, df_ind, df_ranks, df_tc = frames
    return run_single_strategy(
        strat, df_ind, df_prices, df_ranks, symbols, df_tc,
        DATES, _exit_rules(), show_progress=False, **kwargs)


# =============================================================
# 修正4: 同一銘柄の再エントリー禁止
# =============================================================

def test_reentry_blocked_while_position_open():
    """建玉存続中（entry < signal <= exit）の同一銘柄シグナルはスキップされること。

    シグナル日 d0..d4、各トレードは entry の2営業日後に exit:
    - d0 エントリー → d2 exit（d1, d2 のシグナルはブロック）
    - d3 エントリー → d5 exit（d4 のシグナルはブロック）
    → トレードは 2 件になる（従来は 5 件に水増しされていた）。
    """
    frames = _build_frames(signal_days=range(5))
    metrics, trades = _run(_strategy(), frames)

    assert len(trades) == 2
    assert trades[0].entry_date == DATES[0]
    assert trades[0].exit_date == DATES[2]
    assert trades[1].entry_date == DATES[3]
    assert trades[1].exit_date == DATES[5]


def test_reentry_allowed_after_exit():
    """exit 翌営業日以降のシグナルは再エントリーできること（永久ブロックではない）。"""
    # シグナルは d0 と d4 のみ（d4 は d0 トレードの exit=d2 より後）
    frames = _build_frames(signal_days={0, 4})
    metrics, trades = _run(_strategy(), frames)
    assert len(trades) == 2


def test_reentry_escape_hatch_reproduces_old_behavior():
    """比較レポート用の内部引数 allow_reentry_during_hold=True で旧挙動を再現できること。"""
    frames = _build_frames(signal_days=range(5))
    metrics, trades = _run(_strategy(), frames, allow_reentry_during_hold=True)
    assert len(trades) == 5


# =============================================================
# 修正1: entry_mode（close / next_open）
# =============================================================

def test_entry_mode_default_is_close():
    """デフォルト（close）ではシグナル当日の終値がエントリー価格（現行互換）。"""
    frames = _build_frames(signal_days={0})
    metrics, trades = _run(_strategy(), frames)
    assert len(trades) == 1
    assert trades[0].entry_price == pytest.approx(100.0)  # close
    assert trades[0].pnl_pct == pytest.approx(0.0)


def test_add_avg_dollar_volume_rolling_per_symbol():
    """close×volume の21日ローリング平均が銘柄ごとに独立して計算されること。"""
    from backend.backtest.backtest_runner import add_avg_dollar_volume

    rows = []
    for i, d in enumerate(DATES[:3]):
        rows.append({"symbol_id": 1, "date": d, "close": 100.0, "volume": 1000 * (i + 1)})
        rows.append({"symbol_id": 2, "date": d, "close": 10.0, "volume": 50})
    df = pd.DataFrame(rows)

    out = add_avg_dollar_volume(df, window=21)

    s1 = out[out["symbol_id"] == 1].sort_values("date")["avg_dollar_volume_21"].tolist()
    s2 = out[out["symbol_id"] == 2].sort_values("date")["avg_dollar_volume_21"].tolist()
    # sym1: dv = 100k, 200k, 300k → 累積平均（min_periods=1）
    assert s1 == pytest.approx([100_000.0, 150_000.0, 200_000.0])
    # sym2: dv = 500 一定（sym1 と混ざらない）
    assert s2 == pytest.approx([500.0, 500.0, 500.0])


def test_min_avg_dollar_volume_filter_blocks_illiquid_symbols():
    """min_avg_dollar_volume_21 未満の銘柄はシグナルが出ないこと（ハード足切り）。

    sym1: $1M/日（足切り）、sym2: $5M/日（通過）。閾値 $2M。
    """
    from backend.backtest.backtest_runner import add_avg_dollar_volume

    symbols, df_prices, df_ind, df_ranks, df_tc = _build_frames(
        signal_days={0}, n_symbols=2, dollar_volume={1: 1e6, 2: 5e6})
    df_prices = add_avg_dollar_volume(df_prices)
    frames = (symbols, df_prices, df_ind, df_ranks, df_tc)

    strat = _strategy(min_avg_dollar_volume_21=2e6)
    metrics, trades = _run(strat, frames)

    assert len(trades) == 1
    assert trades[0].symbol_id == 2


def test_entry_mode_next_open_uses_next_day_open():
    """next_open ではシグナル翌営業日の寄付価格でエントリーすること。

    フィクスチャは open=101 / close=100 なので、
    エントリー 101 → exit 100（翌々日 ema21_exit）で pnl ≈ -0.99%。
    exit 評価窓は従来と同じ（シグナル翌営業日の終値から）。
    """
    frames = _build_frames(signal_days={0})
    metrics, trades = _run(_strategy(), frames, entry_mode="next_open")
    assert len(trades) == 1
    assert trades[0].entry_price == pytest.approx(101.0)  # 翌日の open
    assert trades[0].exit_date == DATES[2]                # exit タイミングは不変
    assert trades[0].pnl_pct == pytest.approx((100.0 - 101.0) / 101.0 * 100.0)
