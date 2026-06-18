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
    df_prices = pd.read_parquet(
        latest_files["prices"],
        filters=[("symbol_id", "in", valid_ids)],
    )
    print(f"  -> Price loading completed in {time.time() - t0:.2f}s", flush=True)

    # Normalize dates
    df_prices["date"] = pd.to_datetime(df_prices["date"]).dt.date
    sd = datetime.date.fromisoformat(start_date)
    ed = datetime.date.fromisoformat(end_date)
    df_prices = df_prices[(df_prices["date"] >= sd) & (df_prices["date"] <= ed)]

    # Build trading dates from the target ETF prices (use the ticker with most data)
    trading_dates = sorted(df_prices["date"].unique())
    print(
        f"  Trading Dates: {len(trading_dates)} days "
        f"({trading_dates[0]} ~ {trading_dates[-1]})" if trading_dates else "  No trading dates found",
        flush=True,
    )

    return df_symbols, df_prices, trading_dates


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

    # 2. Build price lookup dicts {date -> {close, ...}}
    def build_price_dict(tick: str) -> Dict:
        match = df_symbols[df_symbols["ticker"] == tick]
        if match.empty:
            return {}
        sym_id = int(match["id"].iloc[0])
        sub = df_prices[df_prices["symbol_id"] == sym_id][["date", "close"]].copy()
        return {row["date"]: {"close": float(row["close"])} for _, row in sub.iterrows()}

    etf_prices = build_price_dict(ticker)
    vix_prices = build_price_dict("^VIX")
    vxv_prices = build_price_dict("^VIX3M")

    if not etf_prices:
        raise ValueError(f"No price data found for ticker '{ticker}'.")

    # Filter trading_dates to only dates where the target ETF has data
    trading_dates = [d for d in trading_dates if d in etf_prices]
    print(f"  Target ETF '{ticker}' has {len(trading_dates)} trading days.", flush=True)

    # 3. Run all three strategies
    print(f"\n  [1/3] Running VXV/VIX EMA Strategy...", flush=True)
    vxv_result = _simulate_vxv_strategy(
        trading_dates, etf_prices, vix_prices, vxv_prices,
        initial_capital, consider_tax,
    )
    print(f"    -> Final: ${vxv_result['final_capital']:,.2f} "
          f"(Regime changes: {vxv_result['regime_changes']}, "
          f"Rebalances: {vxv_result['rebalance_count']})", flush=True)

    print(f"  [2/3] Running Buy & Hold...", flush=True)
    bh_result = _simulate_buy_and_hold(
        trading_dates, etf_prices, initial_capital, consider_tax,
    )
    print(f"    -> Final: ${bh_result['final_capital']:,.2f}", flush=True)

    print(f"  [3/3] Running DCA (Monthly)...", flush=True)
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
    parser.add_argument("--tax", type=float, default=0.0, help="Tax rate (e.g. 0.20 for 20%%)")
    parser.add_argument("--output-dir", type=str, default="output/etf_single")

    args = parser.parse_args()

    result = run_etf_single_backtest(
        ticker=args.ticker,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.capital,
        consider_tax=args.tax,
        output_dir=args.output_dir,
    )

    print("\n=== Strategy Comparison ===")
    for strat_name, strat_data in result["summary"]["strategies"].items():
        print(f"\n  {strat_name}:")
        for k, v in strat_data.items():
            if k != "yearly_returns":
                print(f"    {k}: {v}")
