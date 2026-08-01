"""
backtest_runner.py — CLI Entry Point for Backtest Engine

Preloads all required data into memory, then iterates through trading days
to scan signals and simulate trades for each strategy.

Usage:
    python backtest_runner.py [--config PATH] [--strategy NAME]
"""
import os
import sys
import time
import argparse
from datetime import date as dt_date, timedelta

import tomli
import pandas as pd

# Add project root and backend to path for flexible import resolution
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.db.database import init_db
from backend.db import database

from backend.backtest.backtest_screener import scan_signals_for_date, SignalRecord
from backend.backtest.backtest_simulator import simulate_trade, ExitRules, TradeResult
from backend.backtest.backtest_report import calculate_metrics, print_comparison_table, save_results_json
from backend.backtest.strategy_normalizer import normalize_strategy_keys


def load_config(config_path: str) -> dict:
    """Load TOML configuration file."""
    with open(config_path, 'rb') as f:
        return tomli.load(f)


def preload_data(engine, start_date: str, end_date: str, refresh_cache: bool = False):
    """
    Preload all required data into pandas DataFrames.
    Loads ALL data from the Parquet Master full-history files and performs in-memory slicing.
    SQLite connection is COMPLETELY bypassed.
    
    Returns:
        Tuple of (df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates)
    """
    import sys
    import pathlib
    import logging
    from backend.db.database import get_active_db_path
    from backend.pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files, rotate_and_archive_to_parquet
    
    def log(msg):
        print(msg)
        sys.stdout.flush()

    t0 = time.time()
    
    # 1. Determine active DB path & Parquet master directory
    db_path = get_active_db_path()
    if not db_path:
        # Fallback to loading from config.toml
        try:
            import tomllib
            config_path = pathlib.Path(__file__).parents[2] / "config.toml"
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
        except Exception:
            db_path = "data/stocktool.db"
            
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    
    # 2. Get latest Parquet files pointer
    latest_files = get_latest_master_files(pointer_file)
    if not latest_files or refresh_cache:
        log("  Master Parquet cache not found or refresh requested. Generating initial Parquet master from SQLite...")
        try:
            from backend.db import database
            with database.get_db() as db:
                rotate_and_archive_to_parquet(db, db_path, logging.getLogger())
            latest_files = get_latest_master_files(pointer_file)
        except Exception as e:
            log(f"  Error: Failed to dynamically generate initial Parquet master: {e}")
            
    if not latest_files:
        raise FileNotFoundError(f"Parquet master cache files not found at {parquet_dir}! Please run the pipeline once to generate it.")
        
    log(f"Loading data from Parquet Master cache: {pathlib.Path(latest_files['prices']).name} ...")
    
    try:
        # Calculate date buffer for prices rolling calculation (window=21 -> safe buffer 45 days)
        sd_dt = dt_date.fromisoformat(start_date)
        prices_sd_str = (sd_dt - timedelta(days=45)).isoformat()
        
        # 3. Read Parquet full-history masters into memory with pushdown date filters to minimize RAM usage
        t_load = time.time()
        df_symbols = pd.read_parquet(latest_files['symbols'])
        df_prices = pd.read_parquet(latest_files['prices'], filters=[('date', '>=', prices_sd_str), ('date', '<=', end_date)])
        df_indicators = pd.read_parquet(latest_files['indicators'], filters=[('date', '>=', start_date), ('date', '<=', end_date)])
        df_ranks = pd.read_parquet(latest_files['ranks'], filters=[('date', '>=', start_date), ('date', '<=', end_date)])
        df_theme_constituents = pd.read_parquet(latest_files['tc'])
        log(f"  -> Parquet file loading completed in {time.time()-t_load:.2f}s")
        
        # Convert date column safely into canonical datetime.date object
        df_prices['date'] = pd.to_datetime(df_prices['date']).dt.date
        df_indicators['date'] = pd.to_datetime(df_indicators['date']).dt.date
        df_ranks['date'] = pd.to_datetime(df_ranks['date']).dt.date
        
        # 4. In-memory slicing based on start_date and end_date
        t_slice = time.time()
        sd = dt_date.fromisoformat(start_date)
        ed = dt_date.fromisoformat(end_date)

        # Slice in-memory
        df_prices = df_prices[(df_prices['date'] >= sd) & (df_prices['date'] <= ed)]
        df_indicators = df_indicators[(df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)]
        df_ranks = df_ranks[(df_ranks['date'] >= sd) & (df_ranks['date'] <= ed)]
        
        # Ensure df_ranks wide percent_ranks is properly melted to narrow format if it is still wide
        # (Though parquet_cache_manager keeps relative_ranks narrow, we ensure 100% safety)
        if not df_ranks.empty and 'indicator_name' not in df_ranks.columns:
            available_vars = [
                col for col in [
                    'rs_ratio_rank_e5', 'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63', 'rs_ratio_rank_e200',
                    'rs_trend_rank_s5', 'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63', 'rs_trend_rank_s200',
                    'rs_momentum_rank_e5', 'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63', 'rs_momentum_rank_e200',
                    'rs_macd_hist_rank_21'
                ] if col in df_ranks.columns
            ]
            df_ranks = df_ranks.melt(
                id_vars=['symbol_id', 'date'],
                value_vars=available_vars,
                var_name='indicator_name',
                value_name='percent_rank'
            ).dropna(subset=['percent_rank'])
            
        log(f"  -> In-memory pandas slicing completed in {time.time()-t_slice:.3f}s")
        
        # Log memory usage metrics
        total_mem_mb = (
            df_symbols.memory_usage(deep=True).sum() +
            df_prices.memory_usage(deep=True).sum() +
            df_indicators.memory_usage(deep=True).sum() +
            df_ranks.memory_usage(deep=True).sum() +
            df_theme_constituents.memory_usage(deep=True).sum()
        ) / (1024 * 1024)
        log(f"  Total Data Memory Usage: {total_mem_mb:.2f} MB")
        
        # Get unique sorted trading dates
        trading_dates = sorted(
            df_indicators[
                (df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)
            ]['date'].unique()
        )
        if trading_dates:
            log(f"  Trading Dates: {len(trading_dates)} days ({trading_dates[0]} ~ {trading_dates[-1]})")
        else:
            log(f"  Trading Dates: 0 days")
            
        log(f"  Cache load & slicing completed in {time.time()-t0:.2f}s successfully.\n")
        return df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates
        
    except Exception as e:
        log(f"  Critical Error: Failed to preload Parquet cache: {e}")
        raise e
_GROUPBY_CACHE = {}
_GROUPBY_CACHE_MAX = 30


def get_groupby_cache(df, col_name='date'):
    """
    Cache pandas groupby('date') dictionary to prevent memory allocation explosion
    and dramatic overhead reduction during Optuna optimization runs.

    【重要】キャッシュエントリは DataFrame への強参照を保持する。

    キーに `id(df)` を使うが、`id()` が一意なのは**生存中のオブジェクト間だけ**である。
    DataFrame が GC されると CPython は同じアドレスを次の同サイズ確保に即座に再利用するため、
    強参照を持たないと「解放済み DataFrame の id」と「新しい DataFrame の id」が一致し、
    行数・列数も同じなら**別データの groupby 結果が黙って返る**。
    実測: 同形状の DataFrame 3,000 個を生成・破棄すると、3,000 個すべてが同一キーになった。

    これは 2026-07-29 に `backend/tests/backtest/test_backtest_runner_entry.py` の複数テストが
    間欠的に失敗する（トレード数が合わない）現象として顕在化した。テストだけの問題ではなく、
    DataFrame の生成・破棄を繰り返す Optuna 最適化で**誤ったシグナルが静かに混入**しうる。

    対策: エントリに DataFrame 自身を持たせて生存を保証し（＝id の再利用を防ぎ）、
    取り出し時に `is` で同一性を検証する。
    """
    global _GROUPBY_CACHE
    meta_key = (id(df), len(df), len(df.columns), col_name)

    entry = _GROUPBY_CACHE.get(meta_key)
    if entry is not None and entry[0] is df:
        return entry[1]

    # Prevent cache leak over multiple optimization scenarios
    if len(_GROUPBY_CACHE) >= _GROUPBY_CACHE_MAX:
        _GROUPBY_CACHE.clear()

    grouped = {d: group for d, group in df.groupby(col_name)}
    _GROUPBY_CACHE[meta_key] = (df, grouped)
    return grouped


def clear_groupby_cache():
    """groupby キャッシュを明示的に破棄する（テスト・長時間バッチの区切り用）。"""
    _GROUPBY_CACHE.clear()


def count_signal_episodes(trading_dates: list, signals_by_date: dict) -> int:
    """生シグナル（`scan_signals_for_date` の日次結果）を「エピソード数」へ合算する。

    同一銘柄が連続営業日で戦略の全条件を満たし続けても1エピソードとしてカウントする
    （間が空けば新規エピソード）。fast_prune のハード境界（min/max_avg_hits_per_day）が
    「実トレード数」に近い量を見られるようにするための近似（2026-07-23 追加）。

    背景: 状態が何日も持続するタイプの戦略（RSトレンド・テーマモメンタム等）は、
    Phase 2 のトレードシミュレーション（再エントリー禁止ロジック適用後）で数えられる
    `total_trades` よりも、Phase 1 の生シグナル数の方がずっと大きくなりがちで、
    ハードプルーニングだけがこの食い違いの影響を受けていた。厳密な重複排除には
    出口シミュレーション（実際の保有日数）が必要だが、それでは fast_prune の
    「シミュレーションを回さず安く弾く」という速度上の役割が失われる。連続日数の合算は
    追加のマジックナンバー（クールダウン日数等）を持ち込まずに近似する方法。
    """
    total_episodes = 0
    prev_symbols = set()
    for td in trading_dates:
        signals = signals_by_date.get(td)
        if not signals:
            prev_symbols = set()
            continue
        current_symbols = {s.symbol_id for s in signals}
        total_episodes += len(current_symbols - prev_symbols)
        prev_symbols = current_symbols
    return total_episodes


def run_single_strategy(strat_dict: dict, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, trading_dates, exit_rules, show_progress=True, fast_prune=False, prune_bounds=(1.0, 15.0, 5.0), consider_tax=0.0,
                        allow_reentry_during_hold=False, entry_mode="close"):
    """
    Run backtest for a single strategy.
    If fast_prune=True, it will pre-scan all signals and immediately return ("PRUNED", None)
    if the signal counts exceed the prune_bounds (min_avg, max_avg, min_hit_rate_pct).

    allow_reentry_during_hold: True で建玉存続中の再エントリーを許可する（旧挙動）。
        修正前後の比較レポート専用の内部引数で、TOML には公開しない。
        デフォルト False = 同一銘柄は exit 翌営業日まで新規シグナルをスキップ
        （急騰継続銘柄の連日シグナルで1つのムーブが複数トレードに水増しされ、
        トレード独立性と VCP 系の評価が壊れるため）。

    entry_mode: "close"（シグナル当日終値。24時間取引でのオーバーナイト発注運用と整合）
        または "next_open"（シグナル翌営業日の寄付価格。翌営業日データが無い場合は
        シグナル破棄）。exit 評価窓はどちらもシグナル翌営業日の終値から（不変）。
        両方式の成績差で「引け値で買えること自体がエッジか」の感度を計測する。
    """
    import dataclasses
    strat_name = strat_dict.get('name', 'Optuna_Strategy')
    strat_desc = strat_dict.get('description', '')
    if show_progress:
        print(f"Running strategy: {strat_name} ({strat_desc})", flush=True)
    
    t0 = time.time()
    
    # --- CRITICAL: Clear attrs to avoid deepcopy explosion during groupby/iteration ---
    df_indicators.attrs = {}
    df_prices.attrs = {}
    df_ranks.attrs = {}

    # --- Pre-build groupby caches (PP1) ---
    ind_day_cache = get_groupby_cache(df_indicators, 'date')
    price_day_cache = get_groupby_cache(df_prices, 'date')
    ranks_day_cache = get_groupby_cache(df_ranks, 'date')
    ranks_dates_sorted = sorted(ranks_day_cache.keys())
    
    # --- PHASE 1: Fast Pre-Scan ---
    signals_by_date = {}
    total_signals = 0
    days_with_signals = 0

    for i, td in enumerate(trading_dates):
        prev_date = trading_dates[i - 1] if i > 0 else None
        signals = scan_signals_for_date(
            target_date=td, df_ind=df_indicators, df_price=df_prices, df_ranks=df_ranks,
            df_symbols=df_symbols, df_theme_constituents=df_theme_constituents,
            strategy=strat_dict, prev_date=prev_date,
            ind_day_cache=ind_day_cache, price_day_cache=price_day_cache,
            ranks_day_cache=ranks_day_cache, ranks_dates_sorted=ranks_dates_sorted,
        )
        if signals:
            signals_by_date[td] = signals
            total_signals += len(signals)
            days_with_signals += 1

        if show_progress and (i + 1) % 100 == 0:
            print(f"  [Scan] Processed {i + 1}/{len(trading_dates)} days...", flush=True)

    total_days = len(trading_dates)
    if total_days > 0:
        # 2026-07-23: avg_per_day はエピソード数（連続日数を合算した近似実トレード数）ベースに変更。
        # 生シグナル数(total_signals)のままだと、状態が持続する戦略でハードプルーニングが
        # 実トレード数(スコア側が見ている値)より遥かに厳しく効いてしまう問題への対処。
        total_episodes = count_signal_episodes(trading_dates, signals_by_date)
        avg_per_day = total_episodes / total_days
        hit_rate_pct = (days_with_signals / total_days) * 100.0
    else:
        avg_per_day = 0
        hit_rate_pct = 0
        
    # --- PRUNING GATE ---
    if fast_prune:
        min_avg, max_avg, min_hit_rate = prune_bounds
        if avg_per_day < min_avg or avg_per_day > max_avg or hit_rate_pct < min_hit_rate:
            if show_progress:
                print(f"  [Pruned] avg={avg_per_day:.2f}, hit_days={hit_rate_pct:.1f}%", flush=True)
            return {"fast_pruned": True, "avg_per_day": avg_per_day, "hit_rate_pct": hit_rate_pct}, None

    # --- PP3: Pre-build per-symbol DataFrames for trade simulation ---
    # CRITICAL: Clear attrs before grouping to avoid deepcopy explosion of large cached dicts
    df_prices.attrs = {}
    df_indicators.attrs = {}
    df_ranks.attrs = {}

    needed_ids = set(s.symbol_id for sigs in signals_by_date.values() for s in sigs)
    if needed_ids:
        # Use filtered view and then group to avoid unnecessary copies
        df_prices_filtered = df_prices[df_prices['symbol_id'].isin(needed_ids)]
        df_ind_filtered = df_indicators[df_indicators['symbol_id'].isin(needed_ids)]
        
        price_by_sym = {sid: group for sid, group in df_prices_filtered.groupby('symbol_id')}
        ind_by_sym = {sid: group for sid, group in df_ind_filtered.groupby('symbol_id')}
    else:
        price_by_sym = {}
        ind_by_sym = {}

    # --- PHASE 2: Trade Simulation ---
    trades = []
    # look-ahead シミュレーションでトレードは即時完結するため、exit_date を記録して
    # 建玉存続中（entry < signal.date <= exit）の同一銘柄シグナルをスキップする。
    # 同日の重複シグナルも従来通り排除。
    active_until = {}  # symbol_id -> 直近トレードの exit_date

    for i, td in enumerate(trading_dates):
        signals = signals_by_date.get(td, [])
        daily_entered = set()  # Same-day duplicate prevention

        for signal in signals:
            if signal.symbol_id in daily_entered:
                continue
            if not allow_reentry_during_hold:
                held_until = active_until.get(signal.symbol_id)
                if held_until is not None and signal.date <= held_until:
                    continue
            df_price_sym = price_by_sym.get(signal.symbol_id, pd.DataFrame())
            df_ind_sym = ind_by_sym.get(signal.symbol_id, pd.DataFrame())

            if entry_mode == "next_open":
                # シグナル翌営業日の寄付価格をエントリー価格に差し替える
                future_px = df_price_sym[df_price_sym['date'] > signal.date]
                if future_px.empty:
                    continue
                next_open = future_px.sort_values('date').iloc[0]['open']
                if next_open is None or pd.isna(next_open) or next_open <= 0:
                    continue
                signal = dataclasses.replace(signal, entry_price=float(next_open))

            result = simulate_trade(signal, df_price_sym, df_ind_sym, exit_rules)
            if result:
                trades.append(result)
                daily_entered.add(signal.symbol_id)
                active_until[signal.symbol_id] = result.exit_date

        if show_progress and (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{total_days} days, {len(trades)} trades so far...", flush=True)

    # --- Inject SPY benchmark returns into each trade ---
    spy_period_return = 0.0
    spy_row = df_symbols[df_symbols['ticker'] == 'SPY']
    if not spy_row.empty:
        spy_id = spy_row.iloc[0]['id']
        df_spy = df_prices[df_prices['symbol_id'] == spy_id].set_index('date')['close']
        
        # Calculate full period SPY return
        if trading_dates:
            spy_start = df_spy.get(trading_dates[0])
            spy_end = df_spy.get(trading_dates[-1])
            if spy_start is not None and spy_end is not None and spy_start > 0:
                spy_period_return = (spy_end - spy_start) / spy_start
                
        # Inject per-trade SPY returns
        if trades:
            for t in trades:
                spy_entry = df_spy.get(t.entry_date)
                spy_exit = df_spy.get(t.exit_date)
                if spy_entry is not None and spy_exit is not None and spy_entry > 0:
                    t.spy_pnl_pct = (spy_exit - spy_entry) / spy_entry * 100.0
                else:
                    t.spy_pnl_pct = None

    elapsed = time.time() - t0
    metrics = calculate_metrics(
        trades, spy_period_return=spy_period_return, consider_tax=consider_tax,
        price_by_sym=price_by_sym, partial_ratio=exit_rules.partial_ratio,
    )
    if show_progress:
        print(f"  Completed: {len(trades)} trades in {elapsed:.1f}s", flush=True)
    
    return metrics, trades

def run_backtest(config: dict, strategy_filter: str = None, refresh_cache: bool = False, db_path_override: str = None):
    # (Existing beginning part of run_backtest up to initialization)
    general = config.get('general', {})
    start_date = general.get('start_date', '2021-03-26')
    end_date = general.get('end_date', '2026-03-25')
    exit_rules = ExitRules.from_config(config)

    strategies = config.get('strategy', [])
    strategies = [normalize_strategy_keys(s) for s in strategies]
    if strategy_filter:
        # Support short codes (A, B, C1, C2...) and prefixes
        filtered = [s for s in strategies if s['name'] == strategy_filter or s['name'].startswith(strategy_filter + "_")]
        if not filtered:
            # Fallback mapping (same as optimization_runner)
            full_names = {
                'A': 'A_momentum_breakout',
                'B': 'B_theme_momentum',
                'C1': 'C1_rrg_leading_in',
                'C2': 'C2_rrg_improving_in',
                'D': 'D_ema21_pullback',
                'E': 'E_vcp',
                'F': 'F_elite_momentum97'
            }
            if strategy_filter in full_names:
                target_name = full_names[strategy_filter]
                filtered = [s for s in strategies if s['name'] == target_name]
            
        if not filtered:
            print(f"Strategy '{strategy_filter}' not found in config.")
            return
        strategies = filtered

    if db_path_override:
        db_path = db_path_override
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
    else:
        config_path_app = os.path.join(project_root, 'config.toml')
        with open(config_path_app, 'rb') as f:
            app_config = tomli.load(f)
        db_path = os.path.join(project_root, app_config['system']['db_path'])
    
    print(f"  Connecting to DB: {db_path}")
    init_db(db_path)

    df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = \
        preload_data(database.engine, start_date, end_date, refresh_cache=refresh_cache)

    # Validate configuration parameters
    warnings = validate_strategies_config(strategies, df_indicators, df_prices, df_ranks, df_theme_constituents)
    if warnings:
        print_validation_warnings(warnings)

    # Calculate VXV/VIX Ratio (using ^VIX3M and ^VIX)
    vxv_vix_series = {}
    vix_row = df_symbols[df_symbols['ticker'] == '^VIX']
    vix3m_row = df_symbols[df_symbols['ticker'] == '^VIX3M']
    if not vix_row.empty and not vix3m_row.empty:
        vix_id = vix_row.iloc[0]['id']
        vix3m_id = vix3m_row.iloc[0]['id']
        
        vix_prices = df_prices[df_prices['symbol_id'] == vix_id].set_index('date')['close']
        vix3m_prices = df_prices[df_prices['symbol_id'] == vix3m_id].set_index('date')['close']
        
        # Merge prices on date and calculate ratio
        merged_vix = pd.DataFrame({'vix': vix_prices, 'vix3m': vix3m_prices}).dropna()
        if not merged_vix.empty:
            merged_vix['ratio'] = merged_vix['vix3m'] / merged_vix['vix']
            vxv_vix_series = merged_vix['ratio'].to_dict()
            print(f"  Calculated VXV/VIX (VIX3M/VIX) ratio for {len(vxv_vix_series)} dates.")
        else:
            print("  Warning: No overlapping date records found between ^VIX and ^VIX3M.")
    else:
        missing = []
        if vix_row.empty: missing.append("^VIX")
        if vix3m_row.empty: missing.append("^VIX3M")
        print(f"  Warning: VXV/VIX Ratio cannot be calculated because {', '.join(missing)} symbol is missing in database.")
    
    exit_rules.vxv_vix_series = vxv_vix_series

    all_results = {}
    all_trades = {}

    consider_tax = config.get('general', {}).get('consider_tax', 0.0)
    entry_mode = config.get('general', {}).get('entry_mode', 'close')
    # 流動性ハード制約（最適化対象外・全戦略共通。戦略側の明示指定があればそちらを優先）
    min_dollar_vol = config.get('general', {}).get('min_avg_dollar_volume_21')
    for strat in strategies:
        strat_name = strat.get('name', 'Strategy')
        if min_dollar_vol is not None and 'min_avg_dollar_volume_21' not in strat:
            strat = {**strat, 'min_avg_dollar_volume_21': float(min_dollar_vol)}
        metrics, trades = run_single_strategy(
            strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents,
            trading_dates, exit_rules, show_progress=True, consider_tax=consider_tax,
            entry_mode=entry_mode
        )
        all_results[strat_name] = metrics
        all_trades[strat_name] = trades

    # Print comparison table
    print_comparison_table(all_results, start_date, end_date)

    # Save results
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    save_results_json(all_results, all_trades, results_dir, start_date, end_date)

    # Print warnings again at the very end
    if warnings:
        print_validation_warnings(warnings)


def print_validation_warnings(warnings: list):
    """Print validation warnings in a prominent visual block."""
    if not warnings:
        return
    print()
    print("=" * 75)
    print("WARNING: Invalid or unsupported strategy parameters detected in config!")
    print("=" * 75)
    for w in warnings:
        print(f"  - {w}")
    print("=" * 75)
    print()


def validate_strategies_config(strategies: list, df_ind: pd.DataFrame, df_prices: pd.DataFrame, df_ranks: pd.DataFrame = None, df_theme_constituents: pd.DataFrame = None) -> list:
    """Validate parameters in strategies configuration against schema and preloaded data."""
    import re
    from backend.db.models import RelativeRank
    from backend.backtest.strategy_normalizer import normalize_strategy_keys

    strategies = [normalize_strategy_keys(s) for s in strategies]

    try:
        from backend.indicators.screener_filters import SPECIAL_FILTER_KEYS
        from backend.api.screener_router import _INDICATOR_COLUMNS, _VIRTUAL_COLUMNS
    except ImportError:
        SPECIAL_FILTER_KEYS = set()
        _INDICATOR_COLUMNS = {}
        _VIRTUAL_COLUMNS = {}

    warnings = []

    # 特殊フィルタに付随する数値パラメータ（is_vcp_breakout の閾値群など）
    FILTER_ATTACHED_PARAM_KEYS = {
        'breakout_high_window', 'vcr_contraction_max', 'base_high_tol',
        'near_high_tol', 'breakout_change', 'breakout_vol_mult',
        'pivot_tol', 'base_vol_dry_max',
    }

    # Metadata, execution, and validation controller params
    METADATA_KEYS = {
        'id', 'name', 'subtitle', 'subname', 'description', 'group', 'filters', 'use_hysteresis',
        'max_hits_per_day', 'sort_column', 'sort_ascending', 'expression', '_use_hysteresis',
        'min_avg_hits_per_day', 'min_hit_rate_pct', 'max_allowed_dd',
        # Scenario runner internal parameters
        'use_vxv_vix_hysteresis', 'vxv_vix_hysteresis_type',
        # Optimization configuration
        'optimization'
    }

    # Allowed rank indicators from RelativeRank
    rank_column_names = {
        'rs_value_rank',
        'rs_ratio_rank_e5', 'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63', 'rs_ratio_rank_e200',
        'rs_momentum_rank_e5', 'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63', 'rs_momentum_rank_e200',
        'rs_trend_rank_s5', 'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63', 'rs_trend_rank_s200',
        'rs_roc_ema_rank_e5', 'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63', 'rs_roc_ema_rank_e200',
        'rs_macd_hist_rank_21',
    }

    # Aliases in backtest_screener.py
    alias_map = {
        'change_intraday_pct', 'rs21_rank', 'rs63_rank',
        'trend_template_ok', 'rs_condition_14_rank', 'rs_condition_21_rank', 'rs_condition_63_rank'
    }

    for strat in strategies:
        strat_name = strat.get('name', 'Unknown')
        for key, value in strat.items():
            if key in METADATA_KEYS:
                continue

            # 1. Custom boolean / RRG filters
            if key in SPECIAL_FILTER_KEYS:
                continue
            if key == 'rrg_intensity_threshold':
                continue
            # 特殊フィルタに付随する数値パラメータ（min_/max_/is_ 接頭辞を持たない）
            if key in FILTER_ATTACHED_PARAM_KEYS:
                continue

            # Normalize theme-based keys for standard validation checks (e.g. min_theme_rs_trend_s21 -> min_rs_trend_s21)
            eval_key = key
            if key.startswith('min_theme_'):
                eval_key = 'min_' + key[10:]
            elif key.startswith('max_theme_'):
                eval_key = 'max_' + key[10:]
            elif key.startswith('is_theme_'):
                eval_key = 'is_' + key[9:]

            # 2. Moving average cross-above (is_close_gt_* / close_gt_*)
            if eval_key.startswith('close_gt_') or eval_key.startswith('is_close_gt_'):
                ind_name = eval_key[9:] if eval_key.startswith('close_gt_') else eval_key[12:]
                if ind_name in ('sma5', 'sma21', 'sma50', 'sma63', 'sma150', 'sma200', 'ema5', 'ema21', 'ema50', 'ema63', 'ema150', 'ema200'):
                    for num in ('200', '150', '63', '50', '21', '5'):
                        if ind_name.endswith(num) and not ind_name.endswith('_' + num):
                            ind_name = ind_name.replace(num, '_' + num)
                            break
                if ind_name in _INDICATOR_COLUMNS or ind_name in df_ind.columns:
                    continue
                else:
                    warnings.append(f"Strategy '{strat_name}': Invalid moving average parameter '{key}'.")
                    continue

            # 3. Specific rank parameter bounds (min/max_<col>_rank)
            rank_match = re.match(r'^(min|max)_(.+)_rank$', eval_key)
            if rank_match:
                direction, indicator = rank_match.group(1), rank_match.group(2)
                if hasattr(RelativeRank, indicator) or indicator in rank_column_names:
                    continue
                else:
                    warnings.append(f"Strategy '{strat_name}': Unknown rank parameter '{key}'.")
                    continue

            # 4. Standard prefixes (min_, max_, bool_, is_, has_)
            col_name = eval_key
            op = None
            if eval_key.startswith('min_'):
                col_name = eval_key[4:]; op = '>='
            elif eval_key.startswith('max_'):
                col_name = eval_key[4:]; op = '<='
            elif isinstance(value, bool) or eval_key.startswith('bool_') or eval_key.startswith('is_') or eval_key.startswith('has_'):
                op = '=='
                if eval_key.startswith('bool_'): col_name = eval_key[5:]
                elif eval_key.startswith('is_'): col_name = eval_key[3:]
                elif eval_key.startswith('has_'): col_name = eval_key[4:]

            if not op:
                warnings.append(f"Strategy '{strat_name}': Parameter '{key}' has no valid prefix (min_/max_/is_/bool_).")
                continue

            # Resolve check
            is_valid_col = (
                col_name in _INDICATOR_COLUMNS or 
                col_name in df_ind.columns or 
                col_name in _VIRTUAL_COLUMNS or 
                col_name in alias_map or 
                hasattr(RelativeRank, col_name) or
                col_name in rank_column_names or
                col_name in df_prices.columns or
                eval_key in _INDICATOR_COLUMNS or
                eval_key in df_ind.columns
            )

            if not is_valid_col:
                warnings.append(f"Strategy '{strat_name}': Unknown parameter '{key}'.")

    return warnings


def main():
    parser = argparse.ArgumentParser(description='Backtest Engine Runner')
    parser.add_argument('--config', type=str, default=None,
                        help='Path to backtest config TOML file')
    parser.add_argument('--strategy', type=str, default=None,
                        help='Run only a specific strategy by name')
    parser.add_argument('--start-date', type=str, default=None,
                        help='Override start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=None,
                        help='Override end date (YYYY-MM-DD)')
    parser.add_argument('--refresh-cache', action='store_true',
                        help='Force reload data from DB and refresh Parquet cache')
    parser.add_argument('--db-path', type=str, default=None,
                        help='Path to a specific SQLite DB file to use for this run')
    parser.add_argument('--delete-db-after', action='store_true',
                        help='Delete the database file specified in --db-path after completion')
    args = parser.parse_args()

    # Default config path
    if args.config:
        config_path = args.config
    else:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'backtest_config.toml'
        )

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    config = load_config(config_path)
    
    # Overrides from CLI
    if args.start_date:
        config['general']['start_date'] = args.start_date
    if args.end_date:
        config['general']['end_date'] = args.end_date

    print("=" * 60)
    print("  Stock Backtest Engine")
    print("=" * 60)
    print(f"  Config: {config_path}")
    print(f"  Period: {config['general']['start_date']} ~ {config['general']['end_date']}")
    print(f"  Strategies: {len(config.get('strategy', []))}")
    if args.strategy:
        print(f"  Filter: {args.strategy}")
    if args.refresh_cache:
        print(f"  Cache: REFRESHING")
    else:
        print(f"  Cache: AUTO (use Parquet if available)")
    print("=" * 60)
    print()

    run_backtest(
        config, 
        strategy_filter=args.strategy, 
        refresh_cache=args.refresh_cache,
        db_path_override=args.db_path
    )

    # Optional cleanup
    if args.delete_db_after and args.db_path:
        if os.path.exists(args.db_path):
            print(f"\nCleaning up temporary database: {args.db_path}")
            # Close engine connection first to unlock the file
            database.engine.dispose()
            try:
                os.remove(args.db_path)
            except Exception as e:
                print(f"Warning: Failed to delete DB file: {e}")


if __name__ == '__main__':
    main()
