import os
import logging
import pandas as pd
import pathlib
import json
import time
import datetime
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

from backend.backtest.backtest_runner import preload_data
from backend.backtest.backtest_simulator import BacktestSimulator, ExitRules
from backend.backtest.scenario_market_score import MarketTrendScorer, MarketPhase
from backend.backtest.scenario_scorer import ScenarioScorer
from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig
from backend.backtest.scenario_reporter import ScenarioReporter
from backend.db.database import SessionLocal, init_db
from backend.backtest.backtest_screener import scan_signals_for_date, apply_filters_to_df

# --- Progress Tracking ---
PROGRESS_FILENAME = "scenario_progress.json"

def _progress_path(output_dir: str = "output/scenario") -> str:
    return os.path.join(output_dir, PROGRESS_FILENAME)

def update_scenario_progress(
    current_idx: int,
    total_days: int,
    start_time: float,
    active_pos_count: int,
    total_trades: int,
    current_date_str: str,
    output_dir: str = "output/scenario",
):
    elapsed = time.time() - start_time
    progress_pct = (current_idx / total_days) * 100 if total_days > 0 else 0
    
    if current_idx > 0:
        eta_seconds = (elapsed / current_idx) * (total_days - current_idx)
    else:
        eta_seconds = 0
        
    progress_data = {
        "status": "running" if current_idx < total_days else "completed",
        "current_date": current_date_str,
        "progress_pct": round(progress_pct, 2),
        "processed_days": current_idx,
        "total_days": total_days,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": round(eta_seconds, 1),
        "active_positions": active_pos_count,
        "total_trades": total_trades,
        "last_update": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    filepath = _progress_path(output_dir)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=4, ensure_ascii=False)

def get_scenario_progress(output_dir: str = "output/scenario") -> Dict[str, Any]:
    """External interface to read the progress file."""
    filepath = _progress_path(output_dir)
    if not os.path.exists(filepath):
        return {"status": "not_started"}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read progress file: %s", e)
        return {"status": "error"}

def load_scenario_config(config_path: str = "data/screener_presets.toml") -> Dict[str, Any]:
    import tomli
    
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    abs_path = os.path.join(project_root, config_path)
    
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Config file not found: {abs_path}")
    
    with open(abs_path, 'rb') as f:
        data = tomli.load(f)
        
    active_rise_ids = data.get('active_rise_ids', [])
    active_fall_ids = data.get('active_fall_ids', [])
    
    strategies = {}
    for section in ['rise', 'fall']:
        active_ids = active_rise_ids if section == 'rise' else active_fall_ids
        for item in data.get(section, []):
            item_id = item.get('id')
            # If active list is defined and not empty, skip strategies not included
            if active_ids and item_id not in active_ids:
                continue
                
            group = item.get('group', 'Other')
            name = item.get('name', item.get('id', 'Unknown'))
            filters = item.get('filters', {}).copy()
            
            expression = item.get('expression')
            if expression:
                filters['expression'] = expression

            strat_name = f"{section.capitalize()} - {group} - {name}"
            filters['_use_hysteresis'] = item.get('use_hysteresis', False) or item.get('use_vxv_vix_hysteresis', False)
            filters['use_vxv_vix_hysteresis'] = item.get('use_vxv_vix_hysteresis', False)
            filters['vxv_vix_hysteresis_type'] = item.get('vxv_vix_hysteresis_type', 'trend_follow')
            strategies[strat_name] = filters
        
    if not strategies:
        raise ValueError(f"No strategies found in {config_path}")
    
    return {"strategies": strategies}

def run_scenario_test(
    start_date: str,
    end_date: str,
    initial_capital: float = 100000.0,
    max_positions: int = 8,
    min_score: int = 2,
    stop_loss_pct: float = -0.08,
    profit_target_pct: float = 0.20,
    output_dir: str = "output/scenario",
    refresh_cache: bool = False,
    config_path: str = "data/screener_presets.toml",
    market_weights: Dict[str, float] = None,
    use_vxv_vix: bool = False,
    scaling_ratio: float = 1.7,
    monte_carlo_mode: bool = False,
    monte_carlo_seed: int = None,
    consider_tax: float = 0.0,
    regime_model: str = "mts_raw",
    preloaded_data: Any = None
) -> Dict[str, Any]:
    """
    Executes the full portfolio-level scenario simulation.
    """
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # 1. Load Data
    if preloaded_data is not None:
        df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = preloaded_data
    else:
        from backend.db import database
        app_config_path = os.path.join(project_root, "config.toml")
        try:
            import tomli
            with open(app_config_path, "rb") as f:
                app_config = tomli.load(f)
            db_path = app_config.get("system", {}).get("db_path", "data/stocktool.db")
            if not os.path.isabs(db_path):
                db_path = os.path.join(project_root, db_path)
        except Exception as e:
            print(f"Warning: Failed to load config.toml: {e}")
            db_path = os.path.join(project_root, "data/stocktool.db")
            
        database.init_db(db_path)
        db = database.SessionLocal()
        try:
            engine = db.get_bind()
            df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = preload_data(engine, start_date, end_date, refresh_cache)
        finally:
            db.close()
        
    prices_df = df_prices
    symbols_df = df_symbols
    indicators_df = df_indicators
    ranks_df = df_ranks
    theme_constituents = df_theme_constituents

    # Validate configuration parameters in presets
    try:
        from backend.backtest.backtest_runner import validate_strategies_config, print_validation_warnings
        scenario_config = load_scenario_config(config_path)
        strat_list = []
        for name, filters in scenario_config.get("strategies", {}).items():
            strat_dict = filters.copy()
            strat_dict["name"] = name
            strat_list.append(strat_dict)
        
        warnings = validate_strategies_config(strat_list, df_indicators, df_prices, df_ranks, df_theme_constituents)
        if warnings:
            print_validation_warnings(warnings)
    except Exception as ve:
        print(f"Warning during scenario config validation: {ve}")
    
    if prices_df.empty:
        raise ValueError("No price data loaded for the specified date range.")
    
    # Merge indicators into prices for easier daily slicing
    if not indicators_df.empty:
        target_cols = ['date', 'symbol_id', 'atr_14', 'sma50_atr_mult']
        major_cols = [
            'ema_5', 'ema_21', 'ema_50', 'ema_63', 'ema_150', 'ema_200',
            'sma_5', 'sma_21', 'sma_50', 'sma_63', 'sma_150', 'sma_200'
        ]
        for col in major_cols:
            if col in indicators_df.columns:
                target_cols.append(col)
        prices_df = prices_df.merge(
            indicators_df[target_cols],
            on=['date', 'symbol_id'], how='left'
        )

    # 1.6 Pre-calculate Daily Market Breadth & Momentum statistics for the Scorer
    print("  Loading market breadth data (from market_signals + fallback calculation)...", flush=True)
    
    # --- Try to read pre-computed breadth_sma50 from market_signals (T5) ---
    db_breadth_by_date = {}
    try:
        from backend.db import database as _db_module
        if _db_module.SessionLocal is not None:
            with _db_module.get_db() as _db:
                from backend.db.models import MarketSignal as _MS
                from sqlalchemy import and_ as _and
                _ms_rows = _db.query(_MS.date, _MS.breadth_sma50, _MS.vxv_vix_ratio).filter(
                    _MS.breadth_sma50.isnot(None)
                ).all()
                for _r in _ms_rows:
                    import datetime as _dt
                    _d = _r.date if isinstance(_r.date, _dt.date) else _r.date.date()
                    db_breadth_by_date[_d] = float(_r.breadth_sma50)
    except Exception as _e:
        print(f"  Warning: Could not load breadth_sma50 from market_signals: {_e}", flush=True)
    
    # Dates within simulation range that are NOT in market_signals
    sim_dates_set = set(dates) if 'dates' in dir() else set()
    missing_breadth_dates = [d for d in (trading_dates if 'trading_dates' in dir() else []) 
                             if d not in db_breadth_by_date]
    
    # Fallback: compute from scratch only for dates not in market_signals
    fallback_metrics = {}
    if missing_breadth_dates:
        print(f"  Fallback: computing breadth for {len(missing_breadth_dates)} dates not in market_signals...", flush=True)
        active_stocks = df_symbols[(df_symbols['active'] == 1) & (df_symbols['category'] == '個別')]['id'].unique()
        active_stocks_set = set(active_stocks)
        
        p_sub = df_prices[df_prices['symbol_id'].isin(active_stocks_set)][['date', 'symbol_id', 'close']].copy()
        i_sub = df_indicators[df_indicators['symbol_id'].isin(active_stocks_set)][['date', 'symbol_id', 'sma_50']].copy()
        
        merged_metrics = pd.merge(p_sub, i_sub, on=['date', 'symbol_id'], how='inner')
        merged_metrics['is_above_sma50'] = merged_metrics['close'] > merged_metrics['sma_50']
        merged_metrics = merged_metrics.sort_values(['symbol_id', 'date'])
        merged_metrics['prev_close'] = merged_metrics.groupby('symbol_id')['close'].shift(1)
        merged_metrics['is_up'] = merged_metrics['close'] > merged_metrics['prev_close']
        
        fallback_df = merged_metrics.groupby('date').agg(
            breadth_sma50=('is_above_sma50', lambda x: x.mean() if not x.isna().all() else 0.5),
            momentum_ratio=('is_up', lambda x: x.mean() if not x.isna().all() else 0.5)
        ).reset_index()
        fallback_metrics = {row['date']: row.to_dict() for _, row in fallback_df.iterrows()}
    
    # Merge: market_signals takes priority, fallback fills gaps
    daily_metrics = {}
    for d in (trading_dates if 'trading_dates' in dir() else []):
        if d in db_breadth_by_date:
            daily_metrics[d] = {'date': d, 'breadth_sma50': db_breadth_by_date[d], 'momentum_ratio': 0.5}
        elif d in fallback_metrics:
            daily_metrics[d] = fallback_metrics[d]
    
    print(f"  Breadth ready: {len(db_breadth_by_date)} from market_signals, {len(fallback_metrics)} from fallback", flush=True)

    # 2. Initialize Components
    mc_rng = None
    if monte_carlo_mode:
        import random
        seed_val = monte_carlo_seed if monte_carlo_seed is not None else int(time.time() * 1000) % 100000
        mc_rng = random.Random(seed_val)

    config_dict = load_scenario_config(config_path)
    strategies = config_dict.get('strategies', {})
    
    market_scorer = MarketTrendScorer(prices_df, symbols_df, daily_metrics=daily_metrics, weights=market_weights, use_vxv_vix=use_vxv_vix, scaling_ratio=scaling_ratio)
    scenario_scorer = ScenarioScorer(target_group_prefix='Rise - Check')
    
    port_config = PortfolioConfig(
        initial_capital=initial_capital,
        max_positions=max_positions,
        stop_loss_pct=stop_loss_pct,
        consider_tax=consider_tax,
        regime_model=regime_model
     )
    portfolio = ScenarioPortfolio(port_config)
    portfolio.use_hysteresis = True
    
    # --- vxv_vix_ema warmup: pre-fill EMA history from market_signals before start_date ---
    if regime_model == "vxv_vix_ema" and db_breadth_by_date:
        try:
            from backend.db import database as _db2
            if _db2.SessionLocal is not None:
                with _db2.get_db() as _db2s:
                    from backend.db.models import MarketSignal as _MS2
                    _start_obj = pd.to_datetime(start_date).date()
                    _warmup_rows = _db2s.query(_MS2.date, _MS2.vxv_vix_ratio).filter(
                        _MS2.date < _start_obj,
                        _MS2.vxv_vix_ratio.isnot(None)
                    ).order_by(_MS2.date.desc()).limit(63).all()
                    _warmup_ratios = [float(r.vxv_vix_ratio) for r in reversed(_warmup_rows)]
                    if _warmup_ratios:
                        portfolio.vxv_vix_history = _warmup_ratios
                        print(f"  vxv_vix_ema: pre-filled {len(_warmup_ratios)} warmup ratios (EMA seed initialized)", flush=True)
        except Exception as _we:
            print(f"  Warning: vxv_vix_ema warmup failed: {_we}", flush=True)

    
    simulator = BacktestSimulator(prices_df, symbols_df)
    reporter = ScenarioReporter()
    
    # 2.5 Optimization: Pre-group data by date
    logger.info("Optimizing data structures for simulation...")
    print("  Optimizing data structures for simulation...", flush=True)
    ind_by_date = {d: group for d, group in indicators_df.groupby('date')}
    price_by_date = {d: group for d, group in prices_df.groupby('date')}
    ranks_day_cache = {d: group for d, group in ranks_df.groupby('date')}
    ranks_dates_sorted = sorted(ranks_day_cache.keys())
    
    # Exit Rules for the simulator adapter from backtest_config.toml
    import tomli
    bt_config_path = os.path.join(project_root, "backend/backtest/backtest_config.toml")
    try:
        with open(bt_config_path, "rb") as f:
            bt_config = tomli.load(f)
        exit_rules = ExitRules.from_config(bt_config)
    except Exception as e:
        print(f"Warning: Failed to load backtest_config.toml: {e}, using default exit rules.")
        exit_rules = ExitRules()
        
    # Override if custom values are passed via run_scenario_test args (ScenarioRunner uses decimal like -0.08, convert to percent like -8.0)
    if stop_loss_pct != -0.08:
        exit_rules.stop_loss_pct = stop_loss_pct * 100.0
    if profit_target_pct != 0.20:
        exit_rules.partial_take_profit_pct = profit_target_pct * 100.0

    # 3. Daily Loop
    dates = trading_dates # Use the canonical trading dates from preload_data
    start_date_obj = pd.to_datetime(start_date).date()
    end_date_obj = pd.to_datetime(end_date).date()
    
    total_days = len(dates)
    loop_start_time = time.time()
    
    # Write initial progress
    update_scenario_progress(
        current_idx=0, total_days=total_days, start_time=loop_start_time,
        active_pos_count=0, total_trades=0,
        current_date_str=start_date, output_dir=output_dir,
    )
    
    prev_date = None
    for i, current_date in enumerate(dates):
        # current_date is already a datetime.date from preload_data
        c_date = pd.to_datetime(current_date).date()
        
        if c_date < start_date_obj or c_date > end_date_obj:
            prev_date = c_date
            continue
            
        if (i + 1) % 50 == 0 or (i + 1) == total_days:
            elapsed = time.time() - loop_start_time
            print(f"  [Scenario] Simulating {c_date.strftime('%Y-%m-%d')} ({i + 1}/{total_days}) - Elapsed: {elapsed:.1f}s. Active positions: {len(portfolio.active_positions)}", flush=True)
            
            # Update progress file
            update_scenario_progress(
                current_idx=i + 1,
                total_days=total_days,
                start_time=loop_start_time,
                active_pos_count=len(portfolio.active_positions),
                total_trades=len(portfolio.trade_history),
                current_date_str=c_date.strftime('%Y-%m-%d'),
                output_dir=output_dir,
            )
            
        # Step A: Evaluate Exits for active positions using simulator
        portfolio.start_of_day(current_date)
        simulator.positions = portfolio.active_positions
        exits_triggered = simulator.evaluate_exit_for_day(current_date, exit_rules)
        portfolio.apply_exits(exits_triggered)
        
        # Step B: Evaluate Market Phase & Update Regime Allocation
        score, _ = market_scorer.evaluate_market_phase(current_date)
        spy_day_row = market_scorer._spy_by_date.get(current_date)
        spy_close = spy_day_row.get('close') if spy_day_row is not None else None
        spy_sma200 = spy_day_row.get('sma_200') if spy_day_row is not None else None
        spy_sma63 = spy_day_row.get('sma_63') if spy_day_row is not None else None
        ratio = market_scorer.get_vxv_vix_ratio(current_date)
        
        phase = portfolio.update_regime(
            date=current_date,
            spy_close=spy_close,
            spy_sma200=spy_sma200,
            spy_sma63=spy_sma63,
            vxv_vix_ratio=ratio,
            mts_score=score
        )
        
        # Determine target cash/buying capacity. If we need more cash, we might stop buying.
        # Phase constraints are handled inside portfolio.process_buy_candidate.
        
        # Step C: Generate Signals (Scan all target strategies)
        daily_signals_list = []
        
        # Performance: Prepare base merged data once per day
        ind_day = ind_by_date.get(c_date)
        price_day = price_by_date.get(c_date)
        
        if ind_day is not None and price_day is not None:
            # Merge once per day
            base_merged = ind_day.merge(
                price_day[['symbol_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap']],
                on=['symbol_id', 'date'],
                how='inner'
            )
            base_merged = base_merged.merge(
                symbols_df[['id', 'ticker', 'name', 'category', 'active']],
                left_on='symbol_id', right_on='id', how='inner'
            )
            
            for strat_name, strat_rules in strategies.items():
                if not strat_name.startswith('Rise - Check'):
                    continue
                
                # Apply filters to pre-merged data (copy to prevent cross-strategy contamination)
                filtered_df = apply_filters_to_df(
                    merged=base_merged.copy(),
                    target_date=current_date,
                    df_ind=indicators_df,
                    df_ranks=ranks_df,
                    df_symbols=symbols_df,
                    df_theme_constituents=theme_constituents,
                    strategy=strat_rules,
                    prev_date=prev_date,
                    ind_day_cache=ind_by_date,
                    ranks_day_cache=ranks_day_cache,
                    ranks_dates_sorted=ranks_dates_sorted,
                )
                
                if not filtered_df.empty:
                    # Format for ScenarioScorer
                    records_data = []
                    for _, row in filtered_df.iterrows():
                        records_data.append({
                            'date': current_date,
                            'symbol_id': row['symbol_id'],
                            'ticker': row['ticker'],
                            'strategy_name': strat_name,
                            'rs21_rank': row.get('rs21_rank', 0.0)
                        })
                    
                    if records_data:
                        signals_df = pd.DataFrame(records_data)
                        daily_signals_list.append(signals_df)
                
        # Step D: Score and Select Candidates
        if daily_signals_list:
            all_signals_df = pd.concat(daily_signals_list, ignore_index=True)
            scored_candidates = scenario_scorer.score_signals(all_signals_df, current_date)
            
            # Step E: Execute Buys
            for _, row in scored_candidates.iterrows():
                # Filter by minimum score (e.g., at least 2 strategy hits)
                if row['score'] < min_score:
                    continue
                    
                # --- MONTE CARLO STRESS TEST ---
                if monte_carlo_mode and mc_rng is not None:
                    if mc_rng.random() < 0.5:
                        # 50% probability to skip buy today (Chaos load)
                        # Cash rolls over to next days, allowing other candidate catches
                        continue
                    
                # Extract candidate details from cached daily prices (P3 optimization)
                if price_day is not None:
                    sym_price = price_day[price_day['symbol_id'] == row['symbol_id']]
                else:
                    continue
                if sym_price.empty:
                    continue
                
                candidate = {
                    'symbol_id': row['symbol_id'],
                    'ticker': row['ticker'],
                    'close': sym_price.iloc[0]['close'],
                    'score': row['score'],
                    'rs21_rank': row.get('rs21_rank')
                }
                
                bought = portfolio.process_buy_candidate(candidate, current_date, phase)
                # If we couldn't buy (max positions or cash limit reached), stop trying for today
                if not bought and len(portfolio.active_positions) >= portfolio.config.max_positions:
                    break

        prev_date = current_date

        # S2: Record daily equity snapshot (after all buys/sells)
        portfolio.record_daily_equity(current_date, price_by_date)

    # 4. Generate Report
    # Extract SPY prices for benchmark
    spy_id = symbols_df[symbols_df['ticker'] == 'SPY']['id'].values
    spy_prices = pd.DataFrame()
    if len(spy_id) > 0:
        spy_prices = prices_df[prices_df['symbol_id'] == spy_id[0]]
        
    # S3: Record run parameters for reproducibility
    run_params = {
        'start_date': start_date,
        'end_date': end_date,
        'initial_capital': initial_capital,
        'max_positions': max_positions,
        'min_score': min_score,
        'stop_loss_pct': stop_loss_pct,
        'profit_target_pct': profit_target_pct,
    }
    
    # Use final total equity if available, else fallback to capital
    final_cap = portfolio.equity_curve[-1]['total_equity'] if portfolio.equity_curve else portfolio.capital
    
    summary = reporter.generate_summary(
        trade_history=portfolio.trade_history,
        initial_capital=initial_capital,
        final_capital=final_cap,
        start_date=start_date_obj,
        end_date=end_date_obj,
        spy_prices=spy_prices,
        equity_curve=portfolio.equity_curve,
        run_params=run_params,
    )
    
    csv_path = os.path.join(output_dir, "scenario_trade_logs.csv")
    reporter.export_trade_logs(portfolio.trade_history, csv_path)
    
    # S2: Export equity curve
    equity_csv_path = os.path.join(output_dir, "scenario_equity_curve.csv")
    reporter.export_equity_curve(portfolio.equity_curve, equity_csv_path)
    
    # Save summary as JSON
    json_path = os.path.join(output_dir, "scenario_summary.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=4, ensure_ascii=False)
        
    total_time = time.time() - loop_start_time
    print(f"\n  [Scenario] Simulation completed in {total_time:.1f}s", flush=True)
    
    # Write completed progress
    update_scenario_progress(
        current_idx=total_days, total_days=total_days, start_time=loop_start_time,
        active_pos_count=len(portfolio.active_positions),
        total_trades=len(portfolio.trade_history),
        current_date_str=end_date, output_dir=output_dir,
    )
        
    return {
        'summary': summary,
        'trade_history': portfolio.trade_history,
        'equity_curve': portfolio.equity_curve,
    }

if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    parser = argparse.ArgumentParser(description='Scenario Test Runner')
    parser.add_argument('--start-date', type=str, required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, required=True, help='End date (YYYY-MM-DD)')
    parser.add_argument('--initial-capital', type=float, default=100000.0)
    parser.add_argument('--max-positions', type=int, default=8)
    parser.add_argument('--min-score', type=int, default=2, help='Minimum strategy hits required to buy')
    parser.add_argument('--stop-loss', type=float, default=-0.08, help='Stop loss percentage (e.g., -0.08 for -8%%)')
    parser.add_argument('--profit-target', type=float, default=0.20, help='Profit target percentage (e.g., 0.20 for +20%%)')
    parser.add_argument('--output-dir', type=str, default='output/scenario')
    parser.add_argument('--refresh-cache', action='store_true', help='Force refresh of data from database instead of using parquet cache')
    parser.add_argument('--config-path', type=str, default='data/screener_presets.toml', help='Path to the screener config TOML file')
    parser.add_argument('--use-vxv-vix', action='store_true', help='Use VXV/VIX ratio instead of VIX directly for market sentiment score')
    parser.add_argument('--regime-model', type=str, default='full_position', choices=['full_position', 'spy_sma200', 'spy_sma63', 'vxv_vix_ema', 'mts_raw'], help='Market regime switching model')
    
    args = parser.parse_args()
    
    print(f"Running scenario test from {args.start_date} to {args.end_date}...")
    result = run_scenario_test(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.initial_capital,
        max_positions=args.max_positions,
        min_score=args.min_score,
        stop_loss_pct=args.stop_loss,
        profit_target_pct=args.profit_target,
        output_dir=args.output_dir,
        refresh_cache=args.refresh_cache,
        config_path=args.config_path,
        use_vxv_vix=args.use_vxv_vix,
        regime_model=args.regime_model
    )
    
    print("\nScenario Test Summary:")
    for k, v in result['summary'].items():
        if k == 'yearly_performance':
            print(f"  {k}:")
            for year, stats in v.items():
                print(f"    {year}: {stats}")
        elif k == 'top_profitable_trades':
            print(f"  {k}:")
            for idx, trade in enumerate(v, 1):
                print(f"    Top {idx}: {trade['ticker']} ({trade['entry_date']} -> {trade['exit_date']}) | "
                      f"Buy: ${trade['entry_price']:.2f} | Sell: ${trade['exit_price']:.2f} | "
                      f"PnL Amount: ${trade['pnl_amount']:,} | PnL %: {trade['pnl_pct']:.2f}% | "
                      f"Shares: {trade['shares']:,} | Exit Reason: {trade['exit_reason']}")
        else:
            print(f"  {k}: {v}")
    print(f"\nTrade logs saved to {args.output_dir}/scenario_trade_logs.csv")
    print(f"Equity curve saved to {args.output_dir}/scenario_equity_curve.csv")
