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


def load_config(config_path: str) -> dict:
    """Load TOML configuration file."""
    with open(config_path, 'rb') as f:
        return tomli.load(f)
def preload_data(engine, start_date: str, end_date: str, refresh_cache: bool = False):
    """
    Preload all required data into pandas DataFrames.
    Uses Parquet cache if available and not refreshing.

    Returns:
        Tuple of (df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates)
    """
    import sys
    import pathlib
    def log(msg):
        print(msg)
        sys.stdout.flush()

    cache_dir = pathlib.Path(__file__).parent / "cache"
    cache_dir.mkdir(exist_ok=True)

    # Cache filenames based on the date range
    cache_suffix = f"{start_date.replace('-','')}_{end_date.replace('-','')}"
    
    # Intelligently check if we have a larger golden cache (20210326 to 20260326)
    # that fully contains the requested range.
    golden_suffix = "20210326_20260326"
    golden_paths = {
        'symbols': cache_dir / "symbols.parquet",
        'prices': cache_dir / f"prices_{golden_suffix}.parquet",
        'indicators': cache_dir / f"indicators_{golden_suffix}.parquet",
        'ranks': cache_dir / f"ranks_{golden_suffix}.parquet",
        'tc': cache_dir / "theme_constituents.parquet"
    }
    
    use_golden = False
    if not refresh_cache:
        # If specific cache exists, use it.
        # Otherwise, if golden cache exists and requested range is within it, use golden!
        specific_paths = {
            'symbols': cache_dir / "symbols.parquet",
            'prices': cache_dir / f"prices_{cache_suffix}.parquet",
            'indicators': cache_dir / f"indicators_{cache_suffix}.parquet",
            'ranks': cache_dir / f"ranks_{cache_suffix}.parquet",
            'tc': cache_dir / "theme_constituents.parquet"
        }
        if all(p.exists() for p in specific_paths.values()):
            paths = specific_paths
        elif all(p.exists() for p in golden_paths.values()):
            # Verify if requested dates are within 2021-03-26 and 2026-03-26
            try:
                sd_req = dt_date.fromisoformat(start_date)
                ed_req = dt_date.fromisoformat(end_date)
                sd_gold = dt_date.fromisoformat("2021-03-26")
                ed_gold = dt_date.fromisoformat("2026-03-26")
                if sd_gold <= sd_req <= ed_gold and sd_gold <= ed_req <= ed_gold:
                    use_golden = True
                    paths = golden_paths
                    log(f"  -> Golden cache ({golden_suffix}) fully covers requested range ({start_date} to {end_date}). Reusing golden cache!")
            except Exception:
                pass
            if not use_golden:
                paths = specific_paths
        else:
            paths = specific_paths
    else:
        paths = {
            'symbols': cache_dir / "symbols.parquet",
            'prices': cache_dir / f"prices_{cache_suffix}.parquet",
            'indicators': cache_dir / f"indicators_{cache_suffix}.parquet",
            'ranks': cache_dir / f"ranks_{cache_suffix}.parquet",
            'tc': cache_dir / "theme_constituents.parquet"
        }

    if not refresh_cache and all(p.exists() for p in paths.values()):
        log("Loading data from Parquet cache...")
        t0 = time.time()
        try:
            log("  -> Reading symbols...")
            df_symbols = pd.read_parquet(paths['symbols'])
            log("  -> Reading prices...")
            df_prices = pd.read_parquet(paths['prices'])
            log("  -> Reading indicators...")
            df_indicators = pd.read_parquet(paths['indicators'])
            log("  -> Reading ranks...")
            df_ranks = pd.read_parquet(paths['ranks'])
            log("  -> Reading theme constituents...")
            df_theme_constituents = pd.read_parquet(paths['tc'])

            log("  -> Parsing dates into canonical format...")
            # Convert dates safely
            df_prices['date'] = pd.to_datetime(df_prices['date']).dt.date
            df_indicators['date'] = pd.to_datetime(df_indicators['date']).dt.date
            df_ranks['date'] = pd.to_datetime(df_ranks['date']).dt.date
            
            # Slice in-memory if we reused the golden cache
            if use_golden:
                sd = dt_date.fromisoformat(start_date)
                ed = dt_date.fromisoformat(end_date)
                log(f"  -> Slicing golden cache in-memory for requested range: {start_date} to {end_date}...")
                df_prices = df_prices[(df_prices['date'] >= sd) & (df_prices['date'] <= ed)]
                df_indicators = df_indicators[(df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)]
                df_ranks = df_ranks[(df_ranks['date'] >= sd) & (df_ranks['date'] <= ed)]
            
            log(f"  -> Date types after parse: prices={df_prices['date'].dtype}, ind={df_indicators['date'].dtype}, ranks={df_ranks['date'].dtype}")
            if not df_ranks.empty:
                log(f"  -> Sample rank date: {df_ranks['date'].iloc[0]} (Type: {type(df_ranks['date'].iloc[0])})")

            log(f"  Cache loaded successfully in {time.time()-t0:.1f}s")

            # Get unique sorted trading dates
            sd = dt_date.fromisoformat(start_date)
            ed = dt_date.fromisoformat(end_date)
            trading_dates = sorted(
                df_indicators[
                    (df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)
                ]['date'].unique()
            )
            return df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates
        except Exception as e:
            log(f"  Warning: Failed to load cache ({e}). Falling back to DB.")

    log("Loading data from SQLite DB into memory...")
    t0 = time.time()

    sd = dt_date.fromisoformat(start_date)
    ed = dt_date.fromisoformat(end_date)
    # Buffer dates for lookback/exit simulation
    buf_start = (sd - timedelta(days=30)).isoformat()
    buf_end = (ed + timedelta(days=200)).isoformat()

    # Symbols
    log("  Loading symbols...")
    df_symbols = pd.read_sql(
        "SELECT id, ticker, name, category, active FROM symbols WHERE active = 1",
        engine
    )
    log(f"  Symbols: {len(df_symbols)} loaded ({time.time()-t0:.1f}s)")

    # Daily Prices (chunked to avoid segfaults/OOM)
    log("  Loading daily prices...")
    t1 = time.time()
    query_prices = (
        f"SELECT symbol_id, date, open, high, low, close, volume, market_cap "
        f"FROM daily_prices WHERE date >= '{buf_start}' AND date <= '{buf_end}'"
    )
    chunks_prices = []
    for chunk in pd.read_sql(query_prices, engine, parse_dates=['date'], chunksize=100000):
        chunk['date'] = pd.to_datetime(chunk['date']).dt.date
        chunks_prices.append(chunk)
    df_prices = pd.concat(chunks_prices, ignore_index=True) if chunks_prices else pd.DataFrame()
    log(f"  Daily Prices: {len(df_prices)} rows loaded ({time.time()-t1:.1f}s)")

    # Indicators (chunked)
    log("  Loading indicators...")
    t1 = time.time()
    query_ind = (
        f"SELECT symbol_id, date, sma_50, sma_150, sma_200, ema_21, atr_14, "
        f"adr_pct_21, dist_sma50_atr, vol_surge_21, rel_vol_vs_spy_21, "
        f"rs_ratio_14, rs_ratio_21, rs_ratio_63, rs_momentum_21, "
        f"rs_condition_21, trend_template_ok, td9, "
        f"vcr, rs_blue_dot, rs_red_dot, up_down_vol_ratio_50, pct_from_52w_high, "
        f"change_1d_pct, change_1w_pct, change_1m_pct "
        f"FROM indicators WHERE date >= '{buf_start}' AND date <= '{buf_end}'"
    )
    chunks_ind = []
    for chunk in pd.read_sql(query_ind, engine, parse_dates=['date'], chunksize=50000):
        chunk['date'] = pd.to_datetime(chunk['date']).dt.date
        chunks_ind.append(chunk)
    df_indicators = pd.concat(chunks_ind, ignore_index=True) if chunks_ind else pd.DataFrame()
    log(f"  Indicators: {len(df_indicators)} rows loaded ({time.time()-t1:.1f}s)")

    # Backfill market_cap from latest known values in the entire DB if missing historically
    if not df_prices.empty and 'market_cap' in df_prices.columns:
        log("  Backfilling market_cap robustly from global latest values...")
        try:
            # Fetch the absolute latest market_cap for each symbol directly from the DB
            mc_query = "SELECT symbol_id, market_cap FROM daily_prices WHERE market_cap IS NOT NULL AND date = (SELECT MAX(date) FROM daily_prices dp2 WHERE dp2.symbol_id = daily_prices.symbol_id AND dp2.market_cap IS NOT NULL)"
            global_mc_df = pd.read_sql(mc_query, engine).drop_duplicates(subset=['symbol_id'], keep='last')
            if not global_mc_df.empty:
                global_mc_map = global_mc_df.set_index('symbol_id')['market_cap']
                df_prices['market_cap'] = df_prices['market_cap'].fillna(df_prices['symbol_id'].map(global_mc_map))
                log(f"  Backfilled market_cap using global DB values for {len(global_mc_map)} symbols.")
        except Exception as e:
            log(f"  Warning: failed to globally backfill market_cap: {e}")

    # Relative Ranks
    log("  Loading relative ranks...")
    t1 = time.time()
    rank_start = (sd - timedelta(days=10)).isoformat()
    query_ranks = (
        f"SELECT symbol_id, indicator_name, date, percent_rank "
        f"FROM relative_ranks "
        f"WHERE date >= '{rank_start}' AND date <= '{buf_end}' "
        f"AND indicator_name IN ('rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63')"
    )
    chunks_ranks = []
    for chunk in pd.read_sql(query_ranks, engine, parse_dates=['date'], chunksize=100000):
        chunk['date'] = pd.to_datetime(chunk['date']).dt.date
        chunks_ranks.append(chunk)
    df_ranks = pd.concat(chunks_ranks, ignore_index=True) if chunks_ranks else pd.DataFrame()
    log(f"  Relative Ranks: {len(df_ranks)} rows loaded ({time.time()-t1:.1f}s)")

    # Theme Constituents
    log("  Loading theme constituents...")
    df_theme_constituents = pd.read_sql(
        "SELECT theme_id, symbol_id FROM theme_constituents",
        engine
    )
    log(f"  Theme Constituents: {len(df_theme_constituents)} rows loaded")

    # Log memory usage
    total_mem_mb = (
        df_symbols.memory_usage(deep=True).sum() +
        df_prices.memory_usage(deep=True).sum() +
        df_indicators.memory_usage(deep=True).sum() +
        df_ranks.memory_usage(deep=True).sum() +
        df_theme_constituents.memory_usage(deep=True).sum()
    ) / (1024 * 1024)
    log(f"  Total Data Memory Usage: {total_mem_mb:.2f} MB")

    # Get unique sorted trading dates within the backtest range
    trading_dates = sorted(
        df_indicators[
            (df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)
        ]['date'].unique()
    )
    if trading_dates:
        log(f"  Trading Dates: {len(trading_dates)} days ({trading_dates[0]} ~ {trading_dates[-1]})")
    else:
        log(f"  Trading Dates: 0 days")

    elapsed = time.time() - t0
    log(f"  Data loading completed in {elapsed:.1f}s")
    
    # Save to Parquet cache
    log("  Saving into Parquet cache...")
    try:
        # We store as Timestamp for Parquet compatibility, then convert back to date on load
        df_symbols.to_parquet(paths['symbols'])
        
        # Prices/Indicators need Timestamp for to_parquet usually
        p2 = df_prices.copy()
        p2['date'] = pd.to_datetime(p2['date'])
        p2.to_parquet(paths['prices'])
        
        i2 = df_indicators.copy()
        i2['date'] = pd.to_datetime(i2['date'])
        i2.to_parquet(paths['indicators'])
        
        r2 = df_ranks.copy()
        r2['date'] = pd.to_datetime(r2['date'])
        r2.to_parquet(paths['ranks'])
        
        df_theme_constituents.to_parquet(paths['tc'])
        log("  Cache saved successfully.\n")
    except Exception as e:
        log(f"  Warning: Failed to save cache ({e})")

    return df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates


def run_single_strategy(strat_dict: dict, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, trading_dates, exit_rules, show_progress=True, fast_prune=False, prune_bounds=(1.0, 15.0, 5.0)):
    """
    Run backtest for a single strategy.
    If fast_prune=True, it will pre-scan all signals and immediately return ("PRUNED", None) 
    if the signal counts exceed the prune_bounds (min_avg, max_avg, min_hit_rate_pct).
    """
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
    ind_day_cache = {d: group for d, group in df_indicators.groupby('date')}
    price_day_cache = {d: group for d, group in df_prices.groupby('date')}
    ranks_day_cache = {d: group for d, group in df_ranks.groupby('date')}
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
        avg_per_day = total_signals / total_days
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
    # BB1 fix: look-ahead simulation completes trades instantly, so we only need
    # to prevent duplicate entries on the same day for the same symbol.
    # No need for cross-day active_positions blocking.

    for i, td in enumerate(trading_dates):
        signals = signals_by_date.get(td, [])
        daily_entered = set()  # Same-day duplicate prevention only

        for signal in signals:
            if signal.symbol_id in daily_entered:
                continue
            df_price_sym = price_by_sym.get(signal.symbol_id, pd.DataFrame())
            df_ind_sym = ind_by_sym.get(signal.symbol_id, pd.DataFrame())
            result = simulate_trade(signal, df_price_sym, df_ind_sym, exit_rules)
            if result:
                trades.append(result)
                daily_entered.add(signal.symbol_id)

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
    metrics = calculate_metrics(trades, spy_period_return=spy_period_return)
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

    all_results = {}
    all_trades = {}

    for strat in strategies:
        strat_name = strat.get('name', 'Strategy')
        metrics, trades = run_single_strategy(
            strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, 
            trading_dates, exit_rules, show_progress=True
        )
        all_results[strat_name] = metrics
        all_trades[strat_name] = trades

    # Print comparison table
    print_comparison_table(all_results, start_date, end_date)

    # Save results
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    save_results_json(all_results, all_trades, results_dir, start_date, end_date)


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
