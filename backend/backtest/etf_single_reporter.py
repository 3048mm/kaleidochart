"""
etf_single_reporter.py — ETF Single-Ticker Backtest Reporter

Generates comparison reports for VXV/VIX EMA strategy vs Buy & Hold vs DCA.
Calculates CAGR, Max Drawdown, Sharpe Ratio, and Yearly Returns for each strategy.
"""

import datetime
from typing import Dict, Any, List, Optional

import numpy as np


class EtfSingleReporter:
    """Generates comparison metrics and combined equity curves for ETF single backtest."""

    @staticmethod
    def calculate_cagr(initial: float, final: float, years: float) -> float:
        """Calculate Compound Annual Growth Rate as percentage."""
        if years <= 0 or initial <= 0:
            return 0.0
        return round(((final / initial) ** (1.0 / years) - 1.0) * 100.0, 2)

    @staticmethod
    def calculate_max_drawdown(equity_curve: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
        """
        Calculate maximum drawdown from equity curve.
        Returns dict with 'pct' (negative percentage), 'peak_date', 'trough_date'.
        """
        if not equity_curve or len(equity_curve) < 2:
            return {"pct": 0.0, "peak_date": None, "trough_date": None}

        peak = equity_curve[0][key]
        peak_date = equity_curve[0].get("date")
        max_dd_pct = 0.0
        max_dd_peak_date = peak_date
        max_dd_trough_date = peak_date

        for snap in equity_curve:
            val = snap[key]
            if val > peak:
                peak = val
                peak_date = snap.get("date")

            if peak > 0:
                dd_pct = (val / peak - 1.0) * 100.0
                if dd_pct < max_dd_pct:
                    max_dd_pct = dd_pct
                    max_dd_peak_date = peak_date
                    max_dd_trough_date = snap.get("date")

        return {
            "pct": round(max_dd_pct, 2),
            "peak_date": _fmt_date(max_dd_peak_date),
            "trough_date": _fmt_date(max_dd_trough_date),
        }

    @staticmethod
    def calculate_sharpe_ratio(
        equity_curve: List[Dict[str, Any]],
        key: str,
        risk_free_rate: float = 0.0,
        trading_days_per_year: int = 252,
    ) -> float:
        """
        Calculate annualized Sharpe ratio from equity curve.
        risk_free_rate is annual rate (e.g., 0.0 for 0%).
        """
        if not equity_curve or len(equity_curve) < 3:
            return 0.0

        values = [snap[key] for snap in equity_curve]
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                returns.append(values[i] / values[i - 1] - 1.0)

        if not returns:
            return 0.0

        mean_return = np.mean(returns)
        std_return = np.std(returns, ddof=1)

        if std_return == 0:
            return 0.0

        daily_rf = risk_free_rate / trading_days_per_year
        sharpe = (mean_return - daily_rf) / std_return * np.sqrt(trading_days_per_year)
        return round(float(sharpe), 2)

    @staticmethod
    def calculate_yearly_returns(
        equity_curve: List[Dict[str, Any]], key: str
    ) -> Dict[int, float]:
        """
        Calculate return percentage for each calendar year.
        Uses the last equity value of the previous year as the base.
        """
        if not equity_curve:
            return {}

        # Group by year
        by_year: Dict[int, List] = {}
        for snap in equity_curve:
            d = snap.get("date")
            if d is None:
                continue
            if isinstance(d, str):
                year = int(d[:4])
            elif hasattr(d, "year"):
                year = d.year
            else:
                continue
            by_year.setdefault(year, []).append(snap[key])

        sorted_years = sorted(by_year.keys())
        yearly_returns = {}
        prev_year_end = None

        for year in sorted_years:
            values = by_year[year]
            if not values:
                continue

            base = prev_year_end if prev_year_end is not None else values[0]
            end = values[-1]

            if base > 0:
                yearly_returns[year] = round((end / base - 1.0) * 100.0, 2)
            else:
                yearly_returns[year] = 0.0

            prev_year_end = end

        return yearly_returns

    def generate_report(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        consider_tax: float,
        trading_dates: list,
        vxv_result: Dict[str, Any],
        mts_v3_raw_result: Dict[str, Any],
        based_sma200_result: Dict[str, Any],
        based_sma63_result: Dict[str, Any],
        bh_result: Dict[str, Any],
        dca_result: Dict[str, Any],
        benchmark_data: Dict[str, Dict] = None,
    ) -> Dict[str, Any]:
        """
        Generates the full comparison report with summary and merged equity curve.
        """
        # Calculate years for CAGR
        sd = datetime.date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
        ed = datetime.date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
        years = (ed - sd).days / 365.25

        # Build per-strategy summaries
        strategies = {}

        # VXV Strategy
        vxv_eq = vxv_result["equity_curve"]
        strategies["vxv_vix_ema"] = {
            "final_capital": vxv_result["final_capital"],
            "total_return_pct": round(
                (vxv_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, vxv_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(vxv_eq, "vxv_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(vxv_eq, "vxv_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(vxv_eq, "vxv_equity"),
            "yearly_returns": self.calculate_yearly_returns(vxv_eq, "vxv_equity"),
            "regime_changes": vxv_result.get("regime_changes", 0),
            "rebalance_count": vxv_result.get("rebalance_count", 0),
            "time_in_market_pct": vxv_result.get("time_in_market_pct", 0.0),
        }

        # MTS v3 Raw Strategy
        mts_eq = mts_v3_raw_result["equity_curve"]
        strategies["mts_v3_raw"] = {
            "final_capital": mts_v3_raw_result["final_capital"],
            "total_return_pct": round(
                (mts_v3_raw_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, mts_v3_raw_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(mts_eq, "mts_v3_raw_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(mts_eq, "mts_v3_raw_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(mts_eq, "mts_v3_raw_equity"),
            "yearly_returns": self.calculate_yearly_returns(mts_eq, "mts_v3_raw_equity"),
            "rebalance_count": mts_v3_raw_result.get("rebalance_count", 0),
            "time_in_market_pct": mts_v3_raw_result.get("time_in_market_pct", 0.0),
        }

        # Based ETF from SMA200 Strategy
        based_eq = based_sma200_result["equity_curve"]
        strategies["based_sma200"] = {
            "final_capital": based_sma200_result["final_capital"],
            "total_return_pct": round(
                (based_sma200_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, based_sma200_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(based_eq, "based_sma200_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(based_eq, "based_sma200_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(based_eq, "based_sma200_equity"),
            "yearly_returns": self.calculate_yearly_returns(based_eq, "based_sma200_equity"),
            "rebalance_count": based_sma200_result.get("rebalance_count", 0),
            "time_in_market_pct": based_sma200_result.get("time_in_market_pct", 0.0),
        }

        # Based ETF from SMA63 Strategy
        based_sma63_eq = based_sma63_result["equity_curve"]
        strategies["based_sma63"] = {
            "final_capital": based_sma63_result["final_capital"],
            "total_return_pct": round(
                (based_sma63_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, based_sma63_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(based_sma63_eq, "based_sma63_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(based_sma63_eq, "based_sma63_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(based_sma63_eq, "based_sma63_equity"),
            "yearly_returns": self.calculate_yearly_returns(based_sma63_eq, "based_sma63_equity"),
            "rebalance_count": based_sma63_result.get("rebalance_count", 0),
            "time_in_market_pct": based_sma63_result.get("time_in_market_pct", 0.0),
        }

        # Buy & Hold
        bh_eq = bh_result["equity_curve"]
        strategies["buy_and_hold"] = {
            "final_capital": bh_result["final_capital"],
            "total_return_pct": round(
                (bh_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, bh_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(bh_eq, "buyhold_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(bh_eq, "buyhold_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(bh_eq, "buyhold_equity"),
            "yearly_returns": self.calculate_yearly_returns(bh_eq, "buyhold_equity"),
        }

        # DCA
        dca_eq = dca_result["equity_curve"]
        strategies["dca"] = {
            "final_capital": dca_result["final_capital"],
            "total_return_pct": round(
                (dca_result["final_capital"] / initial_capital - 1.0) * 100.0, 2
            ),
            "cagr": self.calculate_cagr(initial_capital, dca_result["final_capital"], years),
            "max_drawdown_pct": self.calculate_max_drawdown(dca_eq, "dca_equity")["pct"],
            "max_drawdown_date": self.calculate_max_drawdown(dca_eq, "dca_equity").get("trough_date"),
            "sharpe_ratio": self.calculate_sharpe_ratio(dca_eq, "dca_equity"),
            "yearly_returns": self.calculate_yearly_returns(dca_eq, "dca_equity"),
        }

        summary = {
            "ticker": ticker,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "consider_tax": consider_tax,
            "trading_days": len(trading_dates),
            "strategies": strategies,
        }

        # Merge equity curves into a single timeline
        merged_equity = self._merge_equity_curves(
            vxv_eq, mts_eq, based_eq, based_sma63_eq, bh_eq, dca_eq, benchmark_data, initial_capital
        )

        return {
            "summary": summary,
            "equity_curve": merged_equity,
        }

    def _merge_equity_curves(
        self,
        vxv_eq: List[Dict],
        mts_eq: List[Dict],
        based_eq: List[Dict],
        based_sma63_eq: List[Dict],
        bh_eq: List[Dict],
        dca_eq: List[Dict],
        benchmark_data: Optional[Dict] = None,
        initial_capital: float = 100000.0,
    ) -> List[Dict[str, Any]]:
        """Merge all strategy equity curves into a unified daily timeline."""

        # Index by date
        mts_map = {_normalize_date(e.get("date")): e for e in mts_eq}
        based_map = {_normalize_date(e.get("date")): e for e in based_eq}
        based_sma63_map = {_normalize_date(e.get("date")): e for e in based_sma63_eq}
        bh_map = {_normalize_date(e.get("date")): e.get("buyhold_equity", 0) for e in bh_eq}
        dca_map = {_normalize_date(e.get("date")): e.get("dca_equity", 0) for e in dca_eq}

        # Build benchmark start prices
        bench_start = {}
        bench_maps = {}
        if benchmark_data:
            for bench_tick, bp in benchmark_data.items():
                sorted_dates = sorted(bp.keys())
                if sorted_dates:
                    bench_start[bench_tick] = bp[sorted_dates[0]].get("close", 0)
                    bench_maps[bench_tick] = bp

        merged = []
        for snap in vxv_eq:
            day = snap.get("date")
            day_key = _normalize_date(day)
            
            mts_snap = mts_map.get(day_key, {})
            based_snap = based_map.get(day_key, {})
            based_sma63_snap = based_sma63_map.get(day_key, {})

            point = {
                "date": _fmt_date(day),
                "vxv_equity": snap.get("vxv_equity", 0),
                "mts_v3_raw_equity": mts_snap.get("mts_v3_raw_equity", 0),
                "based_sma200_equity": based_snap.get("based_sma200_equity", 0),
                "based_sma63_equity": based_sma63_snap.get("based_sma63_equity", 0),
                "buyhold_equity": bh_map.get(day_key, 0),
                "dca_equity": dca_map.get(day_key, 0),
                "vxv_position_pct": snap.get("vxv_position_pct", 0),
                "mts_v3_raw_position_pct": mts_snap.get("mts_v3_raw_position_pct", 0),
                "based_sma200_position_pct": based_snap.get("based_sma200_position_pct", 0),
                "based_sma63_position_pct": based_sma63_snap.get("based_sma63_position_pct", 0),
                "regime": snap.get("regime", ""),
                "mts_score": mts_snap.get("mts_score"),
                "mts_ema5": mts_snap.get("mts_ema5"),
            }

            # Add benchmark equities
            for bench_tick, bp in bench_maps.items():
                start_price = bench_start.get(bench_tick, 0)
                day_data = bp.get(day_key, {})
                close = day_data.get("close", 0) if day_data else 0
                if start_price > 0 and close > 0:
                    point[f"{bench_tick.lower()}_equity"] = round(
                        (close / start_price) * initial_capital, 2
                    )

            merged.append(point)

        return merged


def _normalize_date(d) -> str:
    """Convert a date to normalized 'YYYY-MM-DD' string for dict keys."""
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d) if d else ""


def _fmt_date(d) -> Optional[str]:
    """Format a date for JSON output."""
    if d is None:
        return None
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)
