"""run_single_strategy のエントリー挙動テスト（再エントリー禁止・entry_mode・売買代金下限）。

背景（doc/completed/backtest_optimization_hardening_plan.md 修正1・2・4）:
- 修正4: 建玉存続中の同一銘柄シグナルはスキップ（1つの上昇ムーブが複数トレードに
  水増しされ、VCP 系の評価が壊れていた）。
- 修正1: entry_mode = "next_open" でシグナル翌営業日の寄付価格エントリー。
- 修正2: min_avg_dollar_volume_21 による最適化対象外のハード足切り。
"""
from datetime import date, timedelta
from pathlib import Path

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
            ind_row = {
                "symbol_id": sid, "date": d,
                "change_1d_pct": 9.0 if i in signal_days else 0.0,
                "ema_21": 999.0, "sma_50": 100.0, "atr_14": 1.0,
                "sma50_atr_mult": 0.0,
            }
            if dollar_volume is not None:
                # 実プロダクションでは avg_dollar_volume_21 は indicators 側の列
                # （T3 パイプライン、backend/indicators/volume_and_trends.py）。
                # ここで price 側に同名列を足すと merge で _x/_y にサフィックスされ、
                # フィルタが列を見つけられずサイレントに素通りする(2026-07-28 発見の実障害)。
                ind_row["avg_dollar_volume_21"] = dollar_volume[sid]
            ind_rows.append(ind_row)
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


def test_min_avg_dollar_volume_filter_blocks_illiquid_symbols():
    """min_avg_dollar_volume_21 未満の銘柄はシグナルが出ないこと（ハード足切り）。

    sym1: $1M/日（足切り）、sym2: $5M/日（通過）。閾値 $2M。
    avg_dollar_volume_21 は indicators 側の列として渡す（実プロダクションの形）。
    """
    symbols, df_prices, df_ind, df_ranks, df_tc = _build_frames(
        signal_days={0}, n_symbols=2, dollar_volume={1: 1e6, 2: 5e6})
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


# =============================================================
# validate_strategies_config — screener_registry.resolve_filter_spec() への置換
# （doc/in_progress/screener_filter_unification_plan.md §5 Phase 1「検証系2箇所」）
# =============================================================

def _real_df_ind_and_prices():
    """Indicator / DailyPrice の実カラム名だけを持つ空 DataFrame を作る。

    validate_strategies_config の known_columns はこの2つの DataFrame の
    columns から組み立てられる。値は使わずカラム名の存在だけを検査する。
    """
    from backend.db.models import Indicator, DailyPrice
    ind_cols = [c for c in Indicator.__table__.columns.keys() if c not in ('id', 'symbol_id', 'date')]
    price_cols = [c for c in DailyPrice.__table__.columns.keys() if c not in ('id', 'symbol_id', 'date')]
    return pd.DataFrame(columns=ind_cols), pd.DataFrame(columns=price_cols)


def test_validate_strategies_config_rejects_typo_key():
    """タイポしたキー（min_vol_surge_2）は未知キーとしてエラーになること。"""
    from backend.backtest.backtest_runner import validate_strategies_config

    df_ind, df_prices = _real_df_ind_and_prices()
    strategies = [{"name": "typo_strategy", "min_vol_surge_2": 2.0}]

    errors = validate_strategies_config(strategies, df_ind, df_prices)

    assert len(errors) == 1
    assert "min_vol_surge_2" in errors[0]


def test_validate_strategies_config_with_empty_dataframes_has_zero_errors():
    """**空の DataFrame を渡しても**現行 backtest_config.toml でエラーが0件であること。

    回帰テスト（計画書 §7 P2-2）: `optimization_runner.py` は
    `validate_strategies_config(strategies, pd.DataFrame(), pd.DataFrame())` と
    **列を持たない DataFrame** を渡す。カラム集合を引数の DataFrame だけから作ると
    全キーが「未知」と判定され、2026-08-14 に本番で90件の誤検知が出た。

    カラム集合の権威はモデル定義であり、DataFrame は補助でしかない
    （「実際に供給されているか」の検証は適用時の MissingFilterColumnError の責務）。

    既存の `..._current_backtest_config_has_zero_errors` は実カラムを持つ
    DataFrame を渡していたため、この経路を再現できずすり抜けた。
    """
    import tomli
    from backend.backtest.backtest_runner import validate_strategies_config

    config_path = (Path(__file__).resolve().parents[3]
                   / "backend" / "backtest" / "backtest_config.toml")
    with open(config_path, "rb") as f:
        config = tomli.load(f)

    # optimization_runner.py と同一の呼び出し（列を持たない DataFrame）
    errors = validate_strategies_config(config.get("strategy", []),
                                        pd.DataFrame(), pd.DataFrame())

    assert errors == [], f"空 DataFrame で誤検知が出た（{len(errors)}件）: {errors[:5]}"


def test_validate_strategies_config_allows_vcp_attached_params():
    """特殊フィルタの随伴パラメータ（is_vcp_breakout の閾値8種）がエラーにならないこと。

    回帰テスト（計画書 §7 P1-1）: 旧実装のローカル定数 FILTER_ATTACHED_PARAM_KEYS を
    レジストリへ移す際に取りこぼすと、これらが「未知のキー」と判定され、fail-loud により
    **is_vcp_breakout を使う戦略でバックテスト全体が停止する**。
    2026-08-10 の検収で実際に8件の誤検知として再現したため固定する。
    """
    from backend.backtest.backtest_runner import validate_strategies_config

    df_ind, df_prices = _real_df_ind_and_prices()
    strategies = [{
        "name": "vcp_strategy",
        "is_vcp_breakout": True,
        "breakout_high_window": 63,
        "vcr_contraction_max": 0.8,
        "base_high_tol": 15.0,
        "near_high_tol": 4.0,
        "breakout_change": 4.0,
        "breakout_vol_mult": 1.5,
        "pivot_tol": 2.0,
        "base_vol_dry_max": 0.8,
    }]

    errors = validate_strategies_config(strategies, df_ind, df_prices)

    assert errors == [], f"随伴パラメータが誤検知された: {errors}"


def test_validate_strategies_config_allows_rrg_intensity_threshold():
    """rrg_intensity_threshold（RRG 3種の随伴パラメータ）がエラーにならないこと。"""
    from backend.backtest.backtest_runner import validate_strategies_config

    df_ind, df_prices = _real_df_ind_and_prices()
    strategies = [{
        "name": "rrg_strategy",
        "rrg_leading_in": True,
        "rrg_intensity_threshold": 1.5,
    }]

    errors = validate_strategies_config(strategies, df_ind, df_prices)

    assert errors == [], f"随伴パラメータが誤検知された: {errors}"


def test_validate_strategies_config_allows_metadata_keys():
    """METADATA_KEYS（max_allowed_dd / min_avg_hits_per_day / max_avg_hits_per_day 等の
    制御キー）はフィルタキーではないため、エラーにならないこと。"""
    from backend.backtest.backtest_runner import validate_strategies_config

    df_ind, df_prices = _real_df_ind_and_prices()
    strategies = [{
        "name": "metadata_only",
        "max_hits_per_day": 10,
        "sort_column": "rs_ratio_rank_e21",
        "sort_ascending": False,
        "min_avg_hits_per_day": 0.1,
        "max_avg_hits_per_day": 5.0,
        "max_allowed_dd": 35.0,
        "min_hit_rate_pct": 1.0,
    }]

    errors = validate_strategies_config(strategies, df_ind, df_prices)

    assert errors == []


def test_validate_strategies_config_current_backtest_config_has_zero_errors():
    """現行 backtest_config.toml の全戦略でエラーが0件であること。

    ここが0件でないと U-3 の「差分ゼロ」目標が崩れるため、レジストリへの
    置換によって既存戦略の解釈が変わっていないことをここで固定する。
    """
    import pathlib
    from backend.backtest.backtest_runner import load_config, validate_strategies_config

    config_path = pathlib.Path(__file__).resolve().parents[3] / "backend" / "backtest" / "backtest_config.toml"
    config = load_config(str(config_path))
    strategies = config.get('strategy', [])
    assert strategies, "backtest_config.toml に戦略が1件も読み込めていない"

    df_ind, df_prices = _real_df_ind_and_prices()

    errors = validate_strategies_config(strategies, df_ind, df_prices)

    assert errors == [], f"backtest_config.toml に未解決のフィルタキーがある: {errors}"
