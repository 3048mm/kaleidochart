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

# Add backend to path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

project_root = os.path.dirname(backend_dir)

from db.database import init_db
from db import database

from backtest_screener import scan_signals_for_date, SignalRecord
from backtest_simulator import simulate_trade, ExitRules, TradeResult
from backtest_report import calculate_metrics, print_comparison_table, save_results_json


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
        f"SELECT symbol_id, date, open, high, low, close, volume "
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
        f"SELECT symbol_id, date, sma_50, ema_21, atr_14, "
        f"adr_pct_21, dist_sma50_atr, vol_surge_21, rel_vol_vs_spy_21, "
        f"rs_ratio_21, rs_ratio_63, rs_momentum_21, "
        f"rs_condition_21, trend_template_ok, market_cap, td9 "
        f"FROM indicators WHERE date >= '{buf_start}' AND date <= '{buf_end}'"
    )
    chunks_ind = []
    for chunk in pd.read_sql(query_ind, engine, parse_dates=['date'], chunksize=50000):
        chunk['date'] = pd.to_datetime(chunk['date']).dt.date
        chunks_ind.append(chunk)
    df_indicators = pd.concat(chunks_ind, ignore_index=True) if chunks_ind else pd.DataFrame()
    log(f"  Indicators: {len(df_indicators)} rows loaded ({time.time()-t1:.1f}s)")

    # Backfill market_cap from latest known values in the entire DB if missing historically
    if not df_indicators.empty and 'market_cap' in df_indicators.columns:
        log("  Backfilling market_cap robustly from global latest values...")
        try:
            # Fetch the absolute latest market_cap for each symbol directly from the DB
            mc_query = "SELECT symbol_id, market_cap FROM indicators WHERE market_cap IS NOT NULL AND date = (SELECT MAX(date) FROM indicators i2 WHERE i2.symbol_id = indicators.symbol_id AND i2.market_cap IS NOT NULL)"
            global_mc_df = pd.read_sql(mc_query, engine).drop_duplicates(subset=['symbol_id'], keep='last')
            if not global_mc_df.empty:
                global_mc_map = global_mc_df.set_index('symbol_id')['market_cap']
                df_indicators['market_cap'] = df_indicators['market_cap'].fillna(df_indicators['symbol_id'].map(global_mc_map))
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
        f"AND indicator_name IN ('rs_ratio_21', 'rs_ratio_63')"
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

    # Get unique sorted trading dates within the backtest range
    trading_dates = sorted(
        df_indicators[
            (df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)
        ]['date'].unique()
    )
    log(f"  Trading Dates: {len(trading_dates)} days ({trading_dates[0]} ~ {trading_dates[-1]})")

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


def run_single_strategy(strat_dict: dict, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, trading_dates, exit_rules, show_progress=True):
    strat_name = strat_dict.get('name', 'Optuna_Strategy')
    strat_desc = strat_dict.get('description', '')
    if show_progress:
        print(f"Running strategy: {strat_name} ({strat_desc})")
    
    t0 = time.time()
    trades = []
    active_positions = set()

    for i, td in enumerate(trading_dates):
        prev_date = trading_dates[i - 1] if i > 0 else None
        signals = scan_signals_for_date(
            target_date=td, df_ind=df_indicators, df_price=df_prices, df_ranks=df_ranks,
            df_symbols=df_symbols, df_theme_constituents=df_theme_constituents,
            strategy=strat_dict, prev_date=prev_date,
        )

        for signal in signals:
            if signal.symbol_id in active_positions:
                continue
            df_price_sym = df_prices[df_prices['symbol_id'] == signal.symbol_id]
            df_ind_sym = df_indicators[df_indicators['symbol_id'] == signal.symbol_id]
            result = simulate_trade(signal, df_price_sym, df_ind_sym, exit_rules)
            if result:
                trades.append(result)
                active_positions.add(signal.symbol_id)

        exited_ids = set()
        for t in trades:
            if t.exit_date <= td and t.symbol_id in active_positions:
                exited_ids.add(t.symbol_id)
        active_positions -= exited_ids

        if show_progress and (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(trading_dates)} days, {len(trades)} trades so far...")

    elapsed = time.time() - t0
    metrics = calculate_metrics(trades)
    if show_progress:
        print(f"  Completed: {len(trades)} trades in {elapsed:.1f}s")
    
    return metrics, trades

def run_backtest(config: dict, strategy_filter: str = None, refresh_cache: bool = False, db_path_override: str = None):
    # (Existing beginning part of run_backtest up to initialization)
    general = config.get('general', {})
    start_date = general.get('start_date', '2021-03-26')
    end_date = general.get('end_date', '2026-03-25')
    exit_rules = ExitRules.from_config(config)

    strategies = config.get('strategy', [])
    if strategy_filter:
        strategies = [s for s in strategies if s['name'] == strategy_filter]
        if not strategies:
            print(f"Strategy '{strategy_filter}' not found in config.")
            return

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
