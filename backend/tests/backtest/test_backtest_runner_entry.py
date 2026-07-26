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

from backend.backtest.backtest_runner import run_single_strategy, count_signal_episodes
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
            rank_rows.append({
                "symbol_id": sid, "date": d,
                "indicator_name": "rs_ratio_rank_e14", "percent_rank": 0.5,
            })
            rank_rows.append({
                "symbol_id": sid, "date": d,
                "indicator_name": "rs_ratio_rank_e63", "percent_rank": 0.5,
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
# rank 床フィルタの needs_rs* トリガー漏れ（2026-07-05 発見）
# min_rs_ratio_rank_e14/e63 が単独で使われた場合、rank カラムが
# マージされず黙って素通しになるバグの回帰テスト。
# =============================================================

@pytest.mark.parametrize("rank_key", ["min_rs_ratio_rank_e14", "min_rs_ratio_rank_e63"])
def test_individual_rank_floor_works_standalone(rank_key):
    """個別 rank 床が「他の rank 条件なしで単独」でも機能すること。

    フィクスチャの rank は 0.5。閾値 0.95 なら 0 件、0.3 なら通過するはず。
    トリガー漏れがあると 0.95 でも素通しでトレードが発生してしまう。
    """
    frames = _build_frames(signal_days={0})

    strict = _strategy(**{rank_key: 0.95})
    _, trades_strict = _run(strict, frames)
    assert len(trades_strict) == 0, (
        f"{rank_key}=0.95（データは0.5）なのにシグナルが通過 — "
        "needs_rs* トリガー漏れで rank カラム未マージのまま素通しになっている"
    )

    loose = _strategy(**{rank_key: 0.3})
    _, trades_loose = _run(loose, frames)
    assert len(trades_loose) == 1, f"{rank_key}=0.3 は通過するはず（フィルタが機能した上で緩い閾値）"


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


# =============================================================
# 2026-07-23: fast_prune のハード境界を「生シグナル数」ではなく
# 「エピソード数（連続日数を合算した近似実トレード数）」に寄せる
# =============================================================

class _FakeSignal:
    """count_signal_episodes のテスト用軽量モック（symbol_id のみ必要）。"""
    def __init__(self, symbol_id):
        self.symbol_id = symbol_id


def test_count_signal_episodes_collapses_consecutive_days():
    # 同一銘柄が3日連続で発火 → 1エピソード
    dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    signals_by_date = {d: [_FakeSignal(1)] for d in dates}
    assert count_signal_episodes(dates, signals_by_date) == 1


def test_count_signal_episodes_resets_after_gap():
    # 3日連続発火 → 間が空く → 再度発火 = 2エピソード
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(6)]
    signals_by_date = {
        dates[0]: [_FakeSignal(1)],
        dates[1]: [_FakeSignal(1)],
        dates[2]: [_FakeSignal(1)],
        # dates[3]: 発火なし（間が空く）
        dates[4]: [_FakeSignal(1)],
    }
    assert count_signal_episodes(dates, signals_by_date) == 2


def test_count_signal_episodes_counts_symbols_independently():
    # 同日に2銘柄が発火し、片方だけ翌日も継続 → 2エピソード（新規1・継続1）
    dates = [date(2024, 1, 1), date(2024, 1, 2)]
    signals_by_date = {
        dates[0]: [_FakeSignal(1), _FakeSignal(2)],
        dates[1]: [_FakeSignal(1)],  # symbol 1 のみ継続
    }
    assert count_signal_episodes(dates, signals_by_date) == 2


def test_count_signal_episodes_no_signals_is_zero():
    dates = [date(2024, 1, 1), date(2024, 1, 2)]
    assert count_signal_episodes(dates, {}) == 0


def test_fast_prune_uses_episode_count_not_raw_signal_count():
    """状態が持続する戦略で、生シグナル数ではなくエピソード数がプルーニング判定に使われること。

    フィクスチャ: 1銘柄が10営業日中5日連続で発火（1エピソードのみ）。
    生シグナル数ベースなら avg=5/10=0.5/日でmin_avg=0.3を通過してしまうが、
    エピソード数ベースなら avg=1/10=0.1/日で min_avg=0.3 を下回りプルーニングされるべき。
    """
    frames = _build_frames(signal_days=range(5), n_symbols=1)
    metrics, trades = _run(
        _strategy(), frames, fast_prune=True, prune_bounds=(0.3, 15.0, 5.0))

    assert metrics.get("fast_pruned") is True
    assert metrics["avg_per_day"] == pytest.approx(0.1)  # 1エピソード / 10日
