"""
etf_single_runner.py — ETF Single-Ticker Backtest Engine

Simulates a position-sizing strategy on a single ETF using VXV/VIX EMA regime
detection, and compares against Buy & Hold and Dollar-Cost Averaging (DCA).

Key design decisions:
  - Rebalance only on regime change (not daily)
  - No transaction costs / slippage
  - Final day: liquidate all holdings and apply tax on unrealized gains
  - DCA: split initial capital equally across investment months
"""

import os
import time
import json
import logging
import datetime
import pathlib
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regime definitions
# ---------------------------------------------------------------------------
REGIMES = {
    "BOTTOM":   {"position_pct": 0.25, "label": "Bottom (Deep Bear Panic)"},
    "BEAR":     {"position_pct": 0.00, "label": "Bear (Weak / Correction)"},
    "BULL":     {"position_pct": 1.00, "label": "Bull (Stable Trend)"},
    "OVERHEAT": {"position_pct": 0.50, "label": "Overheat (Overbought)"},
}


# ---------------------------------------------------------------------------
# Lightweight data loader (prices + symbols only)
# ---------------------------------------------------------------------------
def load_etf_data(
    start_date: str,
    end_date: str,
    target_tickers: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, list]:
    """
    Load only price data for the specified tickers from Parquet Master.
    Returns (df_symbols, df_prices_filtered, trading_dates).
    """
    from backend.pipeline.parquet_cache_manager import (
        get_parquet_master_dir,
        get_pointer_file_path,
        get_latest_master_files,
    )
    from backend.db.database import get_active_db_path

    db_path = get_active_db_path()
    if not db_path:
        try:
            import tomli
            config_path = pathlib.Path(__file__).parents[2] / "config.toml"
            with open(config_path, "rb") as f:
                config = tomli.load(f)
            db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
        except Exception:
            db_path = "data/stocktool.db"

    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    latest_files = get_latest_master_files(pointer_file)

    if not latest_files:
        raise FileNotFoundError(
            f"Parquet master cache not found at {parquet_dir}. "
            "Run the pipeline once to generate it."
        )

    print(f"  Loading symbols from Parquet...", flush=True)
    df_symbols = pd.read_parquet(latest_files["symbols"])

    # Resolve symbol_ids for target tickers
    ticker_id_map = {}
    for ticker in target_tickers:
        match = df_symbols[df_symbols["ticker"] == ticker]
        if not match.empty:
            ticker_id_map[ticker] = int(match["id"].iloc[0])
        else:
            print(f"  Warning: Ticker '{ticker}' not found in symbols table.", flush=True)

    if not ticker_id_map:
        raise ValueError(f"None of the target tickers found: {target_tickers}")

    valid_ids = list(ticker_id_map.values())
    print(f"  Loading prices for {len(valid_ids)} tickers from Parquet...", flush=True)

    t0 = time.time()
    sd = datetime.date.fromisoformat(start_date)
    # Warm-up: load data from 365 days before start_date to calculate SMA200 correctly
    sd_warmup = sd - datetime.timedelta(days=365)
    ed = datetime.date.fromisoformat(end_date)

    df_prices = pd.read_parquet(
        latest_files["prices"],
        filters=[
            ("symbol_id", "in", valid_ids),
            ("date", ">=", sd_warmup.isoformat()),
            ("date", "<=", end_date)
        ],
    )
    print(f"  -> Price loading completed in {time.time() - t0:.2f}s", flush=True)

    # Normalize dates
    df_prices["date"] = pd.to_datetime(df_prices["date"]).dt.date
    df_prices = df_prices[(df_prices["date"] >= sd_warmup) & (df_prices["date"] <= ed)]

    # Build trading dates from the target ETF prices (limited to start_date onwards for simulation)
    trading_dates = sorted(df_prices[df_prices["date"] >= sd]["date"].unique())
    print(
        f"  Trading Dates: {len(trading_dates)} days "
        f"({trading_dates[0]} ~ {trading_dates[-1]})" if trading_dates else "  No trading dates found",
        flush=True,
    )

    return df_symbols, df_prices, trading_dates


# ---------------------------------------------------------------------------
# Load and compute MTS v3 Raw Timeline
# ---------------------------------------------------------------------------
def build_mts_v3_raw_timeline(
    trading_dates: list,
    prices_df: pd.DataFrame,
    symbols_df: pd.DataFrame
) -> Dict[Any, float]:
    """
    Builds a complete daily timeline of MTS v3 Raw scores for the specified trading dates.
    Uses DB values where available, and falls back to MarketTrendScorer on-the-fly calculations for pre-2020.
    """
    from backend.backtest.scenario_market_score import MarketTrendScorer
    
    mts_timeline = {}
    
    # 1. Try to load precomputed market_trend_score from Parquet signals master for speed
    from backend.pipeline.parquet_cache_manager import (
        get_parquet_master_dir,
        get_pointer_file_path,
        get_latest_master_files,
    )
    from backend.db.database import get_active_db_path
    
    db_path = get_active_db_path()
    if not db_path:
        try:
            import tomli
            config_path = pathlib.Path(__file__).parents[2] / "config.toml"
            with open(config_path, "rb") as f:
                config = tomli.load(f)
            db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
        except Exception:
            db_path = "data/stocktool.db"
            
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    latest_files = get_latest_master_files(pointer_file)
    
    if latest_files and "signals" in latest_files:
        try:
            print("  Attempting to load computed market_trend_score from Parquet signals...", flush=True)
            df_signals = pd.read_parquet(latest_files["signals"])
            df_signals['date'] = pd.to_datetime(df_signals['date']).dt.date
            
            # Map values to dict
            df_sig_filtered = df_signals[df_signals['date'].isin(trading_dates)]
            for _, row in df_sig_filtered.iterrows():
                d = row['date']
                score = row['market_trend_score']
                if score is not None and not pd.isna(score):
                    mts_timeline[d] = float(score)
            
            missing_dates = [d for d in trading_dates if d not in mts_timeline]
            if not missing_dates:
                print(f"  Successfully loaded all {len(trading_dates)} MTS v3 Raw scores from Parquet signals cache.", flush=True)
                return mts_timeline
            else:
                print(f"  Loaded {len(trading_dates) - len(missing_dates)} scores from Parquet, {len(missing_dates)} dates remain to compute dynamically.", flush=True)
        except Exception as se:
            print(f"  Warning: Failed to load signals from Parquet: {se}. Falling back to dynamic calculation.", flush=True)
            missing_dates = trading_dates
    else:
        missing_dates = trading_dates
        
    daily_metrics = {}
    if missing_dates:
        from backend.pipeline.parquet_cache_manager import (
            get_parquet_master_dir,
            get_pointer_file_path,
            get_latest_master_files,
        )
        from backend.db.database import get_active_db_path
        
        db_path = get_active_db_path()
        if not db_path:
            try:
                import tomli
                config_path = pathlib.Path(__file__).parents[2] / "config.toml"
                with open(config_path, "rb") as f:
                    config = tomli.load(f)
                db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
            except Exception:
                db_path = "data/stocktool.db"
                
        parquet_dir = get_parquet_master_dir(db_path)
        pointer_file = get_pointer_file_path(parquet_dir)
        latest_files = get_latest_master_files(pointer_file)
        
        if latest_files:
            try:
                print("  Pre-calculating daily market breadth & momentum from Parquet master...", flush=True)
                active_stocks = symbols_df[(symbols_df['category'] == '個別') & (symbols_df['active'] == 1)]['id'].unique()
                valid_stock_ids = list(active_stocks)
                
                min_date_str = str(min(missing_dates))
                max_date_str = str(max(missing_dates))
                
                df_stk_prices = pd.read_parquet(
                    latest_files["prices"],
                    columns=['date', 'symbol_id', 'close'],
                    filters=[
                        ("symbol_id", "in", valid_stock_ids),
                        ("date", ">=", min_date_str),
                        ("date", "<=", max_date_str)
                    ],
                )
                df_stk_prices['date'] = pd.to_datetime(df_stk_prices['date']).dt.date
                
                df_stk_indicators = pd.read_parquet(
                    latest_files["indicators"],
                    columns=['date', 'symbol_id', 'sma_50'],
                    filters=[
                        ("symbol_id", "in", valid_stock_ids),
                        ("date", ">=", min_date_str),
                        ("date", "<=", max_date_str)
                    ],
                )
                df_stk_indicators['date'] = pd.to_datetime(df_stk_indicators['date']).dt.date
                
                merged_m = pd.merge(df_stk_prices, df_stk_indicators, on=['date', 'symbol_id'], how='inner')
                merged_m['is_above_sma50'] = merged_m['close'] > merged_m['sma_50']
                
                merged_m = merged_m.sort_values(['symbol_id', 'date'])
                merged_m['prev_close'] = merged_m.groupby('symbol_id')['close'].shift(1)
                merged_m['is_up'] = merged_m['close'] > merged_m['prev_close']
                
                daily_metrics_df = merged_m.groupby('date').agg(
                    breadth_sma50=('is_above_sma50', lambda x: x.mean() if not x.isna().all() else 0.5),
                    momentum_ratio=('is_up', lambda x: x.mean() if not x.isna().all() else 0.5)
                ).reset_index()
                
                daily_metrics = {row['date']: row.to_dict() for _, row in daily_metrics_df.iterrows()}
                print(f"  Successfully loaded metrics for {len(daily_metrics)} dates.", flush=True)
            except Exception as ex:
                print(f"  Error loading metrics from Parquet: {ex}", flush=True)

        print(f"  On-the-fly calculating MTS v3 Raw for {len(missing_dates)} historical dates...", flush=True)
        scorer = MarketTrendScorer(
            prices_df,
            symbols_df,
            daily_metrics=daily_metrics,
            use_vxv_vix=True,
            scaling_ratio=None
        )
        for d in missing_dates:
            try:
                score, _ = scorer.evaluate_market_phase(d)
                mts_timeline[d] = score
            except Exception as e:
                mts_timeline[d] = 70.0 # Default fallback
                
    return mts_timeline


# ---------------------------------------------------------------------------
# VXV/VIX Regime Engine
# ---------------------------------------------------------------------------
class VxvVixRegimeEngine:
    """Calculates VXV/VIX ratio and determines regime using EMA crossover."""

    def __init__(self):
        self.history: List[float] = []
        self.current_regime: str = "BULL"  # default before warm-up
        self.current_target_pct: float = 1.0
        self.regime_history: List[Dict[str, Any]] = []

    def update(self, vxv_close: Optional[float], vix_close: Optional[float], date) -> str:
        """Update with daily VXV/VIX values and return the current regime."""
        if (
            vxv_close is not None
            and vix_close is not None
            and not pd.isna(vxv_close)
            and not pd.isna(vix_close)
            and vix_close > 0
        ):
            ratio = float(vxv_close / vix_close)
        else:
            ratio = 1.10  # fallback

        self.history.append(ratio)

        # Warm-up: need 21 days of history
        if len(self.history) < 21:
            regime = "BULL"
            ema5 = ratio
            ema21 = ratio
        else:
            series = pd.Series(self.history)
            ema5 = float(series.ewm(span=5, adjust=False).mean().iloc[-1])
            ema21 = float(series.ewm(span=21, adjust=False).mean().iloc[-1])

            if ema5 < 1.00:
                regime = "BOTTOM"
            elif ema5 > 1.20:
                regime = "OVERHEAT"
            elif ema5 >= ema21:
                regime = "BULL"
            else:
                regime = "BEAR"

        self.current_regime = regime
        self.current_target_pct = REGIMES[regime]["position_pct"]

        self.regime_history.append({
            "date": date,
            "regime": regime,
            "vxv_vix_ratio": ratio,
            "ema5": ema5 if len(self.history) >= 21 else None,
            "ema21": ema21 if len(self.history) >= 21 else None,
            "position_pct": self.current_target_pct,
        })

        return regime


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------
def _simulate_vxv_strategy(
    trading_dates: list,
    etf_prices: Dict,
    vix_prices: Dict,
    vxv_prices: Dict,
    initial_capital: float,
    consider_tax: float,
) -> Dict[str, Any]:
    """Run the VXV/VIX EMA position-sizing strategy."""

    regime_engine = VxvVixRegimeEngine()
    capital = initial_capital
    shares = 0
    avg_entry_price = 0.0
    prev_target_pct = None
    rebalance_count = 0
    regime_change_count = 0
    days_in_market = 0

    equity_curve = []
    trade_log = []

    for day in trading_dates:
        etf_close = etf_prices.get(day, {}).get("close")
        vix_close = vix_prices.get(day, {}).get("close")
        vxv_close = vxv_prices.get(day, {}).get("close")

        if etf_close is None or etf_close <= 0:
            continue

        # Update regime
        regime = regime_engine.update(vxv_close, vix_close, day)
        target_pct = regime_engine.current_target_pct

        # Rebalance only on regime change
        if prev_target_pct is not None and target_pct != prev_target_pct:
            regime_change_count += 1

            # Current portfolio value
            current_value = capital + shares * etf_close
            target_invested = current_value * target_pct
            current_invested = shares * etf_close

            delta = target_invested - current_invested

            if delta > 0:
                # Buy more
                shares_to_buy = int(delta / etf_close)
                cost = shares_to_buy * etf_close
                if cost <= capital and shares_to_buy > 0:
                    # Update average entry price
                    total_cost = avg_entry_price * shares + cost
                    shares += shares_to_buy
                    avg_entry_price = total_cost / shares if shares > 0 else 0.0
                    capital -= cost
                    rebalance_count += 1
                    trade_log.append({
                        "date": day, "action": "BUY",
                        "shares": shares_to_buy, "price": etf_close,
                        "reason": f"Regime -> {regime} (target {target_pct*100:.0f}%)",
                    })
            elif delta < 0:
                # Sell some
                shares_to_sell = min(shares, int(abs(delta) / etf_close))
                if shares_to_sell > 0:
                    proceeds = shares_to_sell * etf_close
                    # Tax on realized gains
                    realized_gain = (etf_close - avg_entry_price) * shares_to_sell
                    tax_paid = 0.0
                    if consider_tax > 0 and realized_gain > 0:
                        tax_paid = realized_gain * consider_tax
                        capital -= tax_paid

                    shares -= shares_to_sell
                    capital += proceeds
                    rebalance_count += 1
                    trade_log.append({
                        "date": day, "action": "SELL",
                        "shares": shares_to_sell, "price": etf_close,
                        "reason": f"Regime -> {regime} (target {target_pct*100:.0f}%)",
                        "tax_paid": round(tax_paid, 2),
                    })

                    # If sold all, reset avg entry
                    if shares == 0:
                        avg_entry_price = 0.0

        # First day: initial buy according to regime
        if prev_target_pct is None and target_pct > 0:
            target_invested = initial_capital * target_pct
            shares_to_buy = int(target_invested / etf_close)
            if shares_to_buy > 0:
                cost = shares_to_buy * etf_close
                shares = shares_to_buy
                avg_entry_price = etf_close
                capital -= cost
                trade_log.append({
                    "date": day, "action": "BUY",
                    "shares": shares_to_buy, "price": etf_close,
                    "reason": f"Initial buy (regime={regime}, target {target_pct*100:.0f}%)",
                })

        prev_target_pct = target_pct

        # Track days in market
        if shares > 0:
            days_in_market += 1

        # Record equity
        invested_value = shares * etf_close
        total_equity = capital + invested_value
        equity_curve.append({
            "date": day,
            "vxv_equity": round(total_equity, 2),
            "vxv_cash": round(capital, 2),
            "vxv_invested": round(invested_value, 2),
            "vxv_shares": shares,
            "vxv_position_pct": round((invested_value / total_equity * 100) if total_equity > 0 else 0, 1),
            "regime": regime,
        })

    # Final day: liquidate and apply tax
    final_equity = capital
    if shares > 0 and trading_dates:
        last_day = trading_dates[-1]
        last_close = etf_prices.get(last_day, {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            unrealized_gain = (last_close - avg_entry_price) * shares
            tax_paid = 0.0
            if consider_tax > 0 and unrealized_gain > 0:
                tax_paid = unrealized_gain * consider_tax
            final_equity = capital + proceeds - tax_paid
            trade_log.append({
                "date": last_day, "action": "LIQUIDATE",
                "shares": shares, "price": last_close,
                "reason": "Final day liquidation (tax applied)",
                "tax_paid": round(tax_paid, 2),
            })

    time_in_market_pct = (days_in_market / len(trading_dates) * 100) if trading_dates else 0

    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "regime_history": regime_engine.regime_history,
        "regime_changes": regime_change_count,
        "rebalance_count": rebalance_count,
        "time_in_market_pct": round(time_in_market_pct, 1),
    }


def _simulate_buy_and_hold(
    trading_dates: list,
    etf_prices: Dict,
    initial_capital: float,
    consider_tax: float,
) -> Dict[str, Any]:
    """Buy & Hold: invest 100% on day 1, liquidate on final day with tax."""

    capital = initial_capital
    shares = 0
    avg_entry_price = 0.0
    equity_curve = []

    for i, day in enumerate(trading_dates):
        close = etf_prices.get(day, {}).get("close")
        if close is None or close <= 0:
            continue

        # Buy on first valid day
        if shares == 0 and i == 0:
            shares = int(capital / close)
            if shares > 0:
                cost = shares * close
                avg_entry_price = close
                capital -= cost

        invested_value = shares * close
        total_equity = capital + invested_value
        equity_curve.append({
            "date": day,
            "buyhold_equity": round(total_equity, 2),
        })

    # Final day liquidation with tax
    final_equity = capital
    if shares > 0 and trading_dates:
        last_close = etf_prices.get(trading_dates[-1], {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            gain = (last_close - avg_entry_price) * shares
            tax_paid = gain * consider_tax if consider_tax > 0 and gain > 0 else 0.0
            final_equity = capital + proceeds - tax_paid

    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
    }


def _simulate_dca(
    trading_dates: list,
    etf_prices: Dict,
    initial_capital: float,
    consider_tax: float,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """DCA: split initial capital across months, invest on first trading day of each month."""

    sd = datetime.date.fromisoformat(start_date)
    ed = datetime.date.fromisoformat(end_date)

    # Calculate total investment months
    total_months = (ed.year - sd.year) * 12 + (ed.month - sd.month) + 1
    monthly_amount = initial_capital / total_months if total_months > 0 else initial_capital

    remaining_cash = initial_capital
    shares = 0
    total_cost_basis = 0.0
    invested_months = set()
    equity_curve = []

    for day in trading_dates:
        close = etf_prices.get(day, {}).get("close")
        if close is None or close <= 0:
            continue

        # Check if this is the first trading day of a new month
        month_key = (day.year, day.month)
        if month_key not in invested_months and remaining_cash >= monthly_amount:
            shares_to_buy = int(monthly_amount / close)
            if shares_to_buy > 0:
                cost = shares_to_buy * close
                total_cost_basis += cost
                shares += shares_to_buy
                remaining_cash -= cost
                invested_months.add(month_key)

        invested_value = shares * close
        total_equity = remaining_cash + invested_value
        equity_curve.append({
            "date": day,
            "dca_equity": round(total_equity, 2),
        })

    # Final day liquidation with tax
    final_equity = remaining_cash
    if shares > 0 and trading_dates:
        last_close = etf_prices.get(trading_dates[-1], {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            avg_price = total_cost_basis / shares if shares > 0 else 0
            gain = (last_close - avg_price) * shares
            tax_paid = gain * consider_tax if consider_tax > 0 and gain > 0 else 0.0
            final_equity = remaining_cash + proceeds - tax_paid

    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
    }


def _simulate_mts_v3_raw_strategy(
    trading_dates: list,
    etf_prices: Dict,
    mts_timeline: Dict[Any, float],
    initial_capital: float,
    consider_tax: float,
) -> Dict[str, Any]:
    """
    Run the MTS v3 Raw position-sizing strategy.
    Positions are determined by absolute MTS score thresholds:
      - MTS <= 20: 100% position (Buy the panic bottom)
      - MTS >= 80: 50% position (Reduce exposure on overheat)
      - 20 < MTS < 80: 100% position (Standard trend hold)
    Uses a 5-day EMA of MTS to filter daily noise.
    """
    # 1. Convert timeline dict to sorted pandas series to calculate EMA
    dates_sorted = sorted(mts_timeline.keys())
    scores_sorted = [mts_timeline[d] for d in dates_sorted]
    df_mts = pd.DataFrame({'date': dates_sorted, 'score': scores_sorted})
    df_mts['score_ema5'] = df_mts['score'].ewm(span=5, adjust=False).mean()
    
    # Create lookup for daily EMA
    mts_ema_lookup = {row['date']: row['score_ema5'] for _, row in df_mts.iterrows()}

    capital = initial_capital
    shares = 0
    avg_entry_price = 0.0
    prev_target_pct = None
    rebalance_count = 0
    days_in_market = 0

    equity_curve = []
    trade_log = []

    for day in trading_dates:
        etf_close = etf_prices.get(day, {}).get("close")
        if etf_close is None or etf_close <= 0:
            continue

        # Get MTS EMA5
        mts_ema = mts_ema_lookup.get(day, 70.0) # default neutral

        # Determine target allocation based on Trend Follower rules (40 / 60)
        if mts_ema <= 40.0:
            target_pct = 0.00  # BEAR phase -> Cash out (0% invested)
        elif mts_ema >= 60.0:
            target_pct = 1.00  # BULL phase -> Invest fully (100% invested)
        else:
            # NEUTRAL phase -> Maintain previous allocation
            target_pct = prev_target_pct if prev_target_pct is not None else 1.00

        # Rebalance only on target percentage change
        if prev_target_pct is not None and target_pct != prev_target_pct:
            current_value = capital + shares * etf_close
            target_invested = current_value * target_pct
            current_invested = shares * etf_close

            delta = target_invested - current_invested

            if delta > 0:
                shares_to_buy = int(delta / etf_close)
                cost = shares_to_buy * etf_close
                if cost <= capital and shares_to_buy > 0:
                    total_cost = avg_entry_price * shares + cost
                    shares += shares_to_buy
                    avg_entry_price = total_cost / shares if shares > 0 else 0.0
                    capital -= cost
                    rebalance_count += 1
                    trade_log.append({
                        "date": day, "action": "BUY",
                        "shares": shares_to_buy, "price": etf_close,
                        "reason": f"MTS EMA5={mts_ema:.1f} (target {target_pct*100:.0f}%)",
                    })
            elif delta < 0:
                shares_to_sell = min(shares, int(abs(delta) / etf_close))
                if shares_to_sell > 0:
                    proceeds = shares_to_sell * etf_close
                    realized_gain = (etf_close - avg_entry_price) * shares_to_sell
                    tax_paid = 0.0
                    if consider_tax > 0 and realized_gain > 0:
                        tax_paid = realized_gain * consider_tax
                        capital -= tax_paid

                    shares -= shares_to_sell
                    capital += proceeds
                    rebalance_count += 1
                    trade_log.append({
                        "date": day, "action": "SELL",
                        "shares": shares_to_sell, "price": etf_close,
                        "reason": f"MTS EMA5={mts_ema:.1f} (target {target_pct*100:.0f}%)",
                        "tax_paid": round(tax_paid, 2),
                    })
                    if shares == 0:
                        avg_entry_price = 0.0

        # First day initial buy
        if prev_target_pct is None and target_pct > 0:
            target_invested = initial_capital * target_pct
            shares_to_buy = int(target_invested / etf_close)
            if shares_to_buy > 0:
                cost = shares_to_buy * etf_close
                shares = shares_to_buy
                avg_entry_price = etf_close
                capital -= cost
                trade_log.append({
                    "date": day, "action": "BUY",
                    "shares": shares_to_buy, "price": etf_close,
                    "reason": f"Initial buy (MTS EMA5={mts_ema:.1f}, target {target_pct*100:.0f}%)",
                })

        prev_target_pct = target_pct

        if shares > 0:
            days_in_market += 1

        invested_value = shares * etf_close
        total_equity = capital + invested_value
        equity_curve.append({
            "date": day,
            "mts_v3_raw_equity": round(total_equity, 2),
            "mts_v3_raw_cash": round(capital, 2),
            "mts_v3_raw_invested": round(invested_value, 2),
            "mts_v3_raw_shares": shares,
            "mts_v3_raw_position_pct": round((invested_value / total_equity * 100) if total_equity > 0 else 0, 1),
            "mts_score": mts_timeline.get(day, 70.0),
            "mts_ema5": mts_ema,
        })

    # Final day liquidation
    final_equity = capital
    if shares > 0 and trading_dates:
        last_day = trading_dates[-1]
        last_close = etf_prices.get(last_day, {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            unrealized_gain = (last_close - avg_entry_price) * shares
            tax_paid = 0.0
            if consider_tax > 0 and unrealized_gain > 0:
                tax_paid = unrealized_gain * consider_tax
            final_equity = capital + proceeds - tax_paid
            trade_log.append({
                "date": last_day, "action": "LIQUIDATE",
                "shares": shares, "price": last_close,
                "reason": "Final day liquidation (tax applied)",
                "tax_paid": round(tax_paid, 2),
            })

    time_in_market_pct = (days_in_market / len(trading_dates) * 100) if trading_dates else 0
    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "rebalance_count": rebalance_count,
        "time_in_market_pct": round(time_in_market_pct, 1),
    }


def _simulate_based_etf_from_sma200_strategy(
    trading_dates: list,
    etf_prices: Dict,
    base_prices: Dict,
    base_sma200: Dict,
    leverage: float,
    initial_capital: float,
    consider_tax: float,
) -> Dict[str, Any]:
    """
    Run the Based ETF from SMA200 position-sizing strategy.
    - Signal: Underlying Base ETF (SPY or QQQ) close vs its SMA200 with +5%/-3% buffers
    - Rebuy: Triggered when target ETF price drops by -8% * leverage from its base sell price.
    - Rebalance: Executed at the NEXT OPEN for consistency with compare_all_etfs.py.
    """
    capital = initial_capital
    shares = 0.0
    avg_entry_price = 0.0
    pos_state = 1.0
    sell_exec_price = None
    rebalance_count = 0
    days_in_market = 0

    equity_curve = []
    trade_log = []

    rebuy_drop_ratio = 1.0 - (0.08 * leverage)

    # Initialize at first day open
    if trading_dates:
        first_day = trading_dates[0]
        first_open = etf_prices.get(first_day, {}).get("open")
        if first_open and first_open > 0:
            shares = capital / first_open
            avg_entry_price = first_open
            capital = 0.0
            pos_state = 1.0
            trade_log.append({
                "date": first_day, "action": "BUY",
                "shares": shares, "price": first_open,
                "reason": "Initial buy (100% position)",
            })

    for i, day in enumerate(trading_dates):
        etf_close = etf_prices.get(day, {}).get("close")
        if etf_close is None or etf_close <= 0:
            continue

        sig_c = base_prices.get(day, {}).get("close")
        sma = base_sma200.get(day)

        # Track portfolio value using daily close
        p_val = (shares * etf_close) + capital
        
        equity_curve.append({
            "date": day,
            "based_sma200_equity": round(p_val, 2),
            "based_sma200_cash": round(capital, 2),
            "based_sma200_invested": round(shares * etf_close, 2),
            "based_sma200_shares": shares,
            "based_sma200_position_pct": round((shares * etf_close / p_val * 100) if p_val > 0 else 0, 1),
        })

        if shares > 0:
            days_in_market += 1

        if sig_c is None or sma is None:
            continue

        buy_t = sma * 1.05
        sell_t = sma * 0.97

        # Signal checks logic - same as compare_all_etfs.py next-open execution
        if i + 1 < len(trading_dates):
            next_day = trading_dates[i + 1]
            next_open = etf_prices.get(next_day, {}).get("open")
            next_base_open = base_prices.get(next_day, {}).get("open")
            
            if next_open is None or next_open <= 0:
                continue

            # Check for Buyback/Full buy
            if sig_c > buy_t and pos_state < 1.0:
                current_value = (shares * etf_close) + capital
                target_invested = current_value * 1.0
                current_invested = shares * etf_close
                delta = target_invested - current_invested
                
                if delta > 0:
                    shares_to_buy = delta / next_open
                    total_cost = avg_entry_price * shares + shares_to_buy * next_open
                    shares += shares_to_buy
                    avg_entry_price = total_cost / shares if shares > 0 else 0.0
                    capital -= (shares_to_buy * next_open)
                    rebalance_count += 1
                    trade_log.append({
                        "date": next_day, "action": "BUY",
                        "shares": shares_to_buy, "price": next_open,
                        "reason": f"Signal close={sig_c:.1f} > SMA200*1.05={buy_t:.1f} (target 100%)",
                    })
                pos_state = 1.0
                sell_exec_price = None

            # Check for Sell
            elif sig_c < sell_t and pos_state == 1.0:
                proceeds = shares * next_open
                realized_gain = (next_open - avg_entry_price) * shares
                tax_paid = 0.0
                if consider_tax > 0 and realized_gain > 0:
                    tax_paid = realized_gain * consider_tax
                    capital -= tax_paid
                
                capital += proceeds
                shares = 0.0
                pos_state = 0.0
                rebalance_count += 1
                trade_log.append({
                    "date": next_day, "action": "SELL",
                    "shares": proceeds / next_open, "price": next_open,
                    "reason": f"Signal close={sig_c:.1f} < SMA200*0.97={sell_t:.1f} (target 0%)",
                    "tax_paid": round(tax_paid, 2),
                })
                sell_exec_price = next_open
                avg_entry_price = 0.0

            # Check for rebuy (only when in cash 0.0)
            elif pos_state == 0.0 and sell_exec_price is not None and etf_close < sell_exec_price * rebuy_drop_ratio:
                current_value = (shares * etf_close) + capital
                target_invested = current_value * 0.5
                shares_to_buy = target_invested / next_open
                shares = shares_to_buy
                avg_entry_price = next_open
                capital -= (shares_to_buy * next_open)
                rebalance_count += 1
                trade_log.append({
                    "date": next_day, "action": "BUY",
                    "shares": shares_to_buy, "price": next_open,
                    "reason": f"Rebuy drop: price={etf_close:.1f} < sell_price*ratio={sell_exec_price * rebuy_drop_ratio:.1f} (target 50%)",
                })
                pos_state = 0.5

    # Final day liquidation
    final_equity = capital
    if shares > 0 and trading_dates:
        last_day = trading_dates[-1]
        last_close = etf_prices.get(last_day, {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            unrealized_gain = (last_close - avg_entry_price) * shares
            tax_paid = 0.0
            if consider_tax > 0 and unrealized_gain > 0:
                tax_paid = unrealized_gain * consider_tax
            final_equity = capital + proceeds - tax_paid
            trade_log.append({
                "date": last_day, "action": "LIQUIDATE",
                "shares": shares, "price": last_close,
                "reason": "Final day liquidation (tax applied)",
                "tax_paid": round(tax_paid, 2),
            })

    time_in_market_pct = (days_in_market / len(trading_dates) * 100) if trading_dates else 0
    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "rebalance_count": rebalance_count,
        "time_in_market_pct": round(time_in_market_pct, 1),
    }


def _simulate_based_etf_from_sma63_strategy(
    trading_dates: list,
    etf_prices: Dict,
    base_prices: Dict,
    base_sma63: Dict,
    leverage: float,
    initial_capital: float,
    consider_tax: float,
) -> Dict[str, Any]:
    """
    Run the Based ETF from SMA63 position-sizing strategy.
    - Signal: Underlying Base ETF (SPY or QQQ) close vs its SMA63 with +5%/-3% buffers.
    - Rebuy: Triggered when target ETF price drops by -8% * leverage from its base sell price.
    - Rebalance: Executed at the NEXT OPEN for consistency.
    """
    capital = initial_capital
    shares = 0.0
    avg_entry_price = 0.0
    pos_state = 1.0
    sell_exec_price = None
    rebalance_count = 0
    days_in_market = 0

    equity_curve = []
    trade_log = []

    rebuy_drop_ratio = 1.0 - (0.08 * leverage)

    # Initialize at first day open
    if trading_dates:
        first_day = trading_dates[0]
        first_open = etf_prices.get(first_day, {}).get("open")
        if first_open and first_open > 0:
            shares = capital / first_open
            avg_entry_price = first_open
            capital = 0.0
            pos_state = 1.0
            trade_log.append({
                "date": first_day, "action": "BUY",
                "shares": shares, "price": first_open,
                "reason": "Initial buy (100% position)",
            })

    for i, day in enumerate(trading_dates):
        etf_close = etf_prices.get(day, {}).get("close")
        if etf_close is None or etf_close <= 0:
            continue

        sig_c = base_prices.get(day, {}).get("close")
        sma = base_sma63.get(day)

        # Track portfolio value using daily close
        p_val = (shares * etf_close) + capital
        
        equity_curve.append({
            "date": day,
            "based_sma63_equity": round(p_val, 2),
            "based_sma63_cash": round(capital, 2),
            "based_sma63_invested": round(shares * etf_close, 2),
            "based_sma63_shares": shares,
            "based_sma63_position_pct": round((shares * etf_close / p_val * 100) if p_val > 0 else 0, 1),
        })

        if shares > 0:
            days_in_market += 1

        if sig_c is None or sma is None:
            continue

        buy_t = sma * 1.05
        sell_t = sma * 0.97

        # Signal checks logic
        if i + 1 < len(trading_dates):
            next_day = trading_dates[i + 1]
            next_open = etf_prices.get(next_day, {}).get("open")
            
            if next_open is None or next_open <= 0:
                continue

            # Check for Buyback/Full buy
            if sig_c > buy_t and pos_state < 1.0:
                current_value = (shares * etf_close) + capital
                target_invested = current_value * 1.0
                current_invested = shares * etf_close
                delta = target_invested - current_invested
                
                if delta > 0:
                    shares_to_buy = delta / next_open
                    total_cost = avg_entry_price * shares + shares_to_buy * next_open
                    shares += shares_to_buy
                    avg_entry_price = total_cost / shares if shares > 0 else 0.0
                    capital -= (shares_to_buy * next_open)
                    rebalance_count += 1
                    trade_log.append({
                        "date": next_day, "action": "BUY",
                        "shares": shares_to_buy, "price": next_open,
                        "reason": f"Signal close={sig_c:.1f} > SMA63*1.05={buy_t:.1f} (target 100%)",
                    })
                pos_state = 1.0
                sell_exec_price = None

            # Check for Sell
            elif sig_c < sell_t and pos_state == 1.0:
                proceeds = shares * next_open
                realized_gain = (next_open - avg_entry_price) * shares
                tax_paid = 0.0
                if consider_tax > 0 and realized_gain > 0:
                    tax_paid = realized_gain * consider_tax
                    capital -= tax_paid
                
                capital += proceeds
                shares = 0.0
                pos_state = 0.0
                rebalance_count += 1
                trade_log.append({
                    "date": next_day, "action": "SELL",
                    "shares": proceeds / next_open, "price": next_open,
                    "reason": f"Signal close={sig_c:.1f} < SMA63*0.97={sell_t:.1f} (target 0%)",
                    "tax_paid": round(tax_paid, 2),
                })
                sell_exec_price = next_open
                avg_entry_price = 0.0

            # Check for rebuy (only when in cash 0.0)
            elif pos_state == 0.0 and sell_exec_price is not None and etf_close < sell_exec_price * rebuy_drop_ratio:
                current_value = (shares * etf_close) + capital
                target_invested = current_value * 0.5
                shares_to_buy = target_invested / next_open
                shares = shares_to_buy
                avg_entry_price = next_open
                capital -= (shares_to_buy * next_open)
                rebalance_count += 1
                trade_log.append({
                    "date": next_day, "action": "BUY",
                    "shares": shares_to_buy, "price": next_open,
                    "reason": f"Rebuy drop: price={etf_close:.1f} < sell_price*ratio={sell_exec_price * rebuy_drop_ratio:.1f} (target 50%)",
                })
                pos_state = 0.5

    # Final day liquidation
    final_equity = capital
    if shares > 0 and trading_dates:
        last_day = trading_dates[-1]
        last_close = etf_prices.get(last_day, {}).get("close", 0)
        if last_close > 0:
            proceeds = shares * last_close
            unrealized_gain = (last_close - avg_entry_price) * shares
            tax_paid = 0.0
            if consider_tax > 0 and unrealized_gain > 0:
                tax_paid = unrealized_gain * consider_tax
            final_equity = capital + proceeds - tax_paid
            trade_log.append({
                "date": last_day, "action": "LIQUIDATE",
                "shares": shares, "price": last_close,
                "reason": "Final day liquidation (tax applied)",
                "tax_paid": round(tax_paid, 2),
            })

    time_in_market_pct = (days_in_market / len(trading_dates) * 100) if trading_dates else 0
    return {
        "final_capital": round(final_equity, 2),
        "equity_curve": equity_curve,
        "trade_log": trade_log,
        "rebalance_count": rebalance_count,
        "time_in_market_pct": round(time_in_market_pct, 1),
    }




# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_etf_single_backtest(
    ticker: str,
    start_date: str,
    end_date: str,
    initial_capital: float = 100000.0,
    consider_tax: float = 0.0,
    output_dir: str = "output/etf_single",
) -> Dict[str, Any]:
    """
    Runs a complete ETF single-ticker backtest with 3 strategies:
      1. VXV/VIX EMA position sizing
      2. Buy & Hold
      3. DCA (monthly)

    Results are written to output_dir/{ticker}/.
    """
    print(f"\n{'='*60}", flush=True)
    print(f"  ETF Single Backtest: {ticker}", flush=True)
    print(f"  Period: {start_date} ~ {end_date}", flush=True)
    print(f"  Capital: ${initial_capital:,.0f}  Tax: {consider_tax*100:.0f}%", flush=True)
    print(f"{'='*60}\n", flush=True)

    t_start = time.time()

    # Tickers to load: target + VIX/VIX3M + benchmark ETFs for comparison
    benchmark_tickers = ["SPY", "QQQ", "TQQQ"]
    all_tickers = list(set(
        [ticker, "^VIX", "^VIX3M"] + benchmark_tickers
    ))

    # 1. Load data
    df_symbols, df_prices, trading_dates = load_etf_data(
        start_date, end_date, all_tickers
    )

    if not trading_dates:
        raise ValueError("No trading dates found for the specified period.")

    # 2. Build price lookup dicts {date -> {close, open, ...}}
    def build_price_dict(tick: str) -> Dict:
        match = df_symbols[df_symbols["ticker"] == tick]
        if match.empty:
            return {}
        sym_id = int(match["id"].iloc[0])
        sub = df_prices[df_prices["symbol_id"] == sym_id][["date", "close", "open"]].copy()
        return {row["date"]: {"close": float(row["close"]), "open": float(row["open"])} for _, row in sub.iterrows()}

    etf_prices = build_price_dict(ticker)
    vix_prices = build_price_dict("^VIX")
    vxv_prices = build_price_dict("^VIX3M")

    # Build SMA200 and price lookups for SPY and QQQ
    def build_sma200_dict(tick: str) -> Dict[Any, float]:
        match = df_symbols[df_symbols["ticker"] == tick]
        if match.empty:
            return {}
        sym_id = int(match["id"].iloc[0])
        sub = df_prices[df_prices["symbol_id"] == sym_id].sort_values("date").copy()
        sub["sma200"] = sub["close"].rolling(window=200, min_periods=200).mean()
        sub["date"] = pd.to_datetime(sub["date"]).dt.date
        return {row["date"]: float(row["sma200"]) for _, row in sub.iterrows() if not pd.isna(row["sma200"])}

    spy_sma200_lookup = build_sma200_dict("SPY")
    qqq_sma200_lookup = build_sma200_dict("QQQ")
    
    def build_sma63_dict(tick: str) -> Dict[Any, float]:
        match = df_symbols[df_symbols["ticker"] == tick]
        if match.empty:
            return {}
        sym_id = int(match["id"].iloc[0])
        sub = df_prices[df_prices["symbol_id"] == sym_id].sort_values("date").copy()
        sub["sma63"] = sub["close"].rolling(window=63, min_periods=63).mean()
        sub["date"] = pd.to_datetime(sub["date"]).dt.date
        return {row["date"]: float(row["sma63"]) for _, row in sub.iterrows() if not pd.isna(row["sma63"])}

    spy_sma63_lookup = build_sma63_dict("SPY")
    qqq_sma63_lookup = build_sma63_dict("QQQ")
    

    
    spy_prices_raw = build_price_dict("SPY")
    qqq_prices_raw = build_price_dict("QQQ")

    if not etf_prices:
        raise ValueError(f"No price data found for ticker '{ticker}'.")

    # Filter trading_dates to only dates where the target ETF has data
    trading_dates = [d for d in trading_dates if d in etf_prices]
    print(f"  Target ETF '{ticker}' has {len(trading_dates)} trading days.", flush=True)

    # Load and build MTS v3 Raw timeline for the period
    print(f"\n  Building MTS v3 Raw Daily Timeline...", flush=True)
    mts_timeline = build_mts_v3_raw_timeline(trading_dates, df_prices, df_symbols)

    # 3. Run all strategies
    print(f"\n  [1/6] Running VXV/VIX EMA Strategy...", flush=True)
    vxv_result = _simulate_vxv_strategy(
        trading_dates, etf_prices, vix_prices, vxv_prices,
        initial_capital, consider_tax,
    )
    print(f"    -> Final: ${vxv_result['final_capital']:,.2f} "
          f"(Regime changes: {vxv_result['regime_changes']}, "
          f"Rebalances: {vxv_result['rebalance_count']})", flush=True)

    print(f"  [2/6] Running MTS v3 Raw Position Strategy...", flush=True)
    mts_v3_raw_result = _simulate_mts_v3_raw_strategy(
        trading_dates, etf_prices, mts_timeline,
        initial_capital, consider_tax,
    )
    print(f"    -> Final: ${mts_v3_raw_result['final_capital']:,.2f} "
          f"(Rebalances: {mts_v3_raw_result['rebalance_count']})", flush=True)

    # Resolve Base Index and Leverage
    etf_configs = {
        "SPY": {"base": "SPY", "leverage": 1},
        "QQQ": {"base": "QQQ", "leverage": 1},
        "TQQQ": {"base": "QQQ", "leverage": 3},
        "UPRO": {"base": "SPY", "leverage": 3},
        "SOXX": {"base": "QQQ", "leverage": 1},
        "SOXL": {"base": "QQQ", "leverage": 3},
        "UGL": {"base": "SPY", "leverage": 2},
    }
    cfg = etf_configs.get(ticker, {"base": "SPY", "leverage": 1})
    base_idx = cfg["base"]
    lev = cfg["leverage"]

    print(f"  [3/6] Running Based ETF from SMA200 Strategy (Base: {base_idx}, Lev: {lev}x)...", flush=True)
    base_prices = spy_prices_raw if base_idx == "SPY" else qqq_prices_raw
    base_sma200 = spy_sma200_lookup if base_idx == "SPY" else qqq_sma200_lookup

    based_sma200_result = _simulate_based_etf_from_sma200_strategy(
        trading_dates, etf_prices, base_prices, base_sma200,
        lev, initial_capital, consider_tax
    )
    print(f"    -> Final: ${based_sma200_result['final_capital']:,.2f} "
          f"(Rebalances: {based_sma200_result['rebalance_count']})", flush=True)

    print(f"  [4/6] Running Based ETF from SMA63 Strategy (Base: {base_idx}, Lev: {lev}x)...", flush=True)
    base_sma63 = spy_sma63_lookup if base_idx == "SPY" else qqq_sma63_lookup

    based_sma63_result = _simulate_based_etf_from_sma63_strategy(
        trading_dates, etf_prices, base_prices, base_sma63,
        lev, initial_capital, consider_tax
    )
    print(f"    -> Final: ${based_sma63_result['final_capital']:,.2f} "
          f"(Rebalances: {based_sma63_result['rebalance_count']})", flush=True)

    print(f"  [5/6] Running Buy & Hold...", flush=True)
    bh_result = _simulate_buy_and_hold(
        trading_dates, etf_prices, initial_capital, consider_tax,
    )
    print(f"    -> Final: ${bh_result['final_capital']:,.2f}", flush=True)

    print(f"  [6/6] Running DCA (Monthly)...", flush=True)
    dca_result = _simulate_dca(
        trading_dates, etf_prices, initial_capital, consider_tax,
        start_date, end_date,
    )
    print(f"    -> Final: ${dca_result['final_capital']:,.2f}", flush=True)

    # 4. Build benchmark comparison prices (for equity chart)
    benchmark_data = {}
    for bench_tick in benchmark_tickers:
        if bench_tick == ticker:
            continue  # Already included via the strategy curves
        bp = build_price_dict(bench_tick)
        if bp:
            benchmark_data[bench_tick] = bp

    # 5. Generate report
    from backend.backtest.etf_single_reporter import EtfSingleReporter

    reporter = EtfSingleReporter()
    report = reporter.generate_report(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        consider_tax=consider_tax,
        trading_dates=trading_dates,
        vxv_result=vxv_result,
        mts_v3_raw_result=mts_v3_raw_result,
        based_sma200_result=based_sma200_result,
        based_sma63_result=based_sma63_result,
        bh_result=bh_result,
        dca_result=dca_result,
        benchmark_data=benchmark_data,
    )

    # 6. Save outputs
    ticker_dir = os.path.join(output_dir, ticker)
    pathlib.Path(ticker_dir).mkdir(parents=True, exist_ok=True)

    # Summary JSON
    summary_path = os.path.join(ticker_dir, "etf_single_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(report["summary"], f, indent=4, ensure_ascii=False, default=str)

    # Equity CSV
    equity_path = os.path.join(ticker_dir, "etf_single_equity.csv")
    pd.DataFrame(report["equity_curve"]).to_csv(equity_path, index=False)

    # Regimes CSV
    regimes_path = os.path.join(ticker_dir, "etf_single_regimes.csv")
    pd.DataFrame(vxv_result["regime_history"]).to_csv(regimes_path, index=False)

    elapsed = time.time() - t_start
    print(f"\n  Backtest completed in {elapsed:.1f}s", flush=True)
    print(f"  Results saved to: {ticker_dir}/", flush=True)

    return {
        "summary": report["summary"],
        "equity_curve": report["equity_curve"],
        "regime_history": vxv_result["regime_history"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="ETF Single Backtest")
    parser.add_argument("--ticker", type=str, required=True, help="Target ETF ticker (e.g. SPY, QQQ, TQQQ)")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")
    parser.add_argument("--tax", type=float, default=None,
                         help="Tax rate (e.g. 0.20 for 20%%). 省略時は backtest_config.toml [general] consider_tax を使う")
    parser.add_argument("--output-dir", type=str, default="output/etf_single")

    args = parser.parse_args()

    # 税率解決: 明示指定があればそれを、無ければ backtest_config.toml の値を使う
    from backend.backtest.common_constraints import load_tax_rate
    if args.tax is not None:
        consider_tax = load_tax_rate({'general': {'consider_tax': args.tax}})
    else:
        consider_tax = load_tax_rate()
    if consider_tax > 0.0:
        print(f"  [Tax] 適用税率: {consider_tax * 100:.1f}% (consider_tax={consider_tax})")
    else:
        print("  [Tax] 税なし (consider_tax=0.0)")

    result = run_etf_single_backtest(
        ticker=args.ticker,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.capital,
        consider_tax=consider_tax,
        output_dir=args.output_dir,
    )

    print("\n=== Strategy Comparison ===")
    for strat_name, strat_data in result["summary"]["strategies"].items():
        print(f"\n  {strat_name}:")
        for k, v in strat_data.items():
            if k != "yearly_returns":
                print(f"    {k}: {v}")
