import os
import csv
import json
from typing import List
from fastapi import APIRouter, HTTPException

from api.schemas import (
    BacktestScenarioSummary,
    BacktestEquityPoint,
    BacktestTradeLogItem
)

router = APIRouter(tags=["backtest"])

# Default output directory relative to project root (stocktool/output)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

def is_monte_carlo_group(name: str) -> bool:
    """
    Checks if the name represents a Monte Carlo simulation group (e.g. 'A', 'B1').
    It checks if '{name}_run_0' directory exists in 'output/scenario'.
    """
    if name in ["A", "B1", "B2", "B3", "E2"]:
        sub_dir = os.path.join(OUTPUT_DIR, "scenario")
        if os.path.exists(sub_dir):
            if os.path.exists(os.path.join(sub_dir, f"{name}_run_0")):
                return True
    return False

def resolve_scenario_path(name: str) -> str:
    """
    Resolves the directory path for a given scenario name.
    If name is 'latest', returns the path of the most recently modified scenario directory.
    Includes security checks to prevent path traversal.
    """
    # Prevent path traversal
    clean_name = os.path.basename(name)
    if clean_name != name or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid scenario name")
        
    if name == "latest":
        if not os.path.exists(OUTPUT_DIR):
            raise HTTPException(status_code=404, detail="Output directory not found")
        
        candidates = []
        # Search output directory
        for item in os.listdir(OUTPUT_DIR):
            d_path = os.path.join(OUTPUT_DIR, item)
            if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, "scenario_summary.json")):
                candidates.append((d_path, os.path.getmtime(d_path)))
                
        # Search output/scenario directory if it exists
        sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
        if os.path.exists(sub_scenario_dir) and os.path.isdir(sub_scenario_dir):
            for item in os.listdir(sub_scenario_dir):
                d_path = os.path.join(sub_scenario_dir, item)
                if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, "scenario_summary.json")):
                    candidates.append((d_path, os.path.getmtime(d_path)))
        
        if not candidates:
            raise HTTPException(status_code=404, detail="No backtest scenarios found")
            
        # Sort by modification time descending
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    elif is_monte_carlo_group(name):
        return os.path.join(OUTPUT_DIR, "scenario")
    else:
        # Check output directory
        scenario_path = os.path.join(OUTPUT_DIR, name)
        if os.path.exists(scenario_path) and os.path.isdir(scenario_path):
            return scenario_path
            
        # Check output/scenario directory
        scenario_path_sub = os.path.join(OUTPUT_DIR, "scenario", name)
        if os.path.exists(scenario_path_sub) and os.path.isdir(scenario_path_sub):
            return scenario_path_sub
            
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")

@router.get("/backtest/scenarios", response_model=List[str])
def get_scenarios():
    """
    Returns a list of scenario directory names that contain scenario_summary.json.
    Sorted by modification time (newest first).
    Injects dynamic Monte Carlo group names (e.g. 'A', 'B1') if their runs exist.
    """
    if not os.path.exists(OUTPUT_DIR):
        return []
        
    candidates = []
    # Search output directory
    for item in os.listdir(OUTPUT_DIR):
        d_path = os.path.join(OUTPUT_DIR, item)
        if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, "scenario_summary.json")):
            candidates.append((item, os.path.getmtime(d_path)))
            
    # Search output/scenario directory
    sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
    groups_detected = set()
    
    if os.path.exists(sub_scenario_dir) and os.path.isdir(sub_scenario_dir):
        for item in os.listdir(sub_scenario_dir):
            d_path = os.path.join(sub_scenario_dir, item)
            if os.path.isdir(d_path) and os.path.exists(os.path.join(d_path, "scenario_summary.json")):
                candidates.append((item, os.path.getmtime(d_path)))
                if "_run_" in item:
                    group_name = item.split("_run_")[0]
                    if group_name in ["A", "B1", "B2", "B3", "E2"]:
                        groups_detected.add(group_name)
            
    # Sort newest first
    candidates.sort(key=lambda x: x[1], reverse=True)
    sorted_names = [name for name, _ in candidates]
    
    # Prepend integrated groups
    sorted_groups = sorted(list(groups_detected))
    return sorted_groups + sorted_names

@router.get("/backtest/scenario/{name}/summary", response_model=BacktestScenarioSummary)
def get_scenario_summary(name: str):
    """
    Returns the scenario summary JSON formatted to the BacktestScenarioSummary schema.
    Supports 'latest' as a dynamic name alias, and Monte Carlo groups as integrated summaries.
    """
    if is_monte_carlo_group(name):
        sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
        run_summaries = []
        run_paths = []
        for run_idx in range(100):
            run_path = os.path.join(sub_scenario_dir, f"{name}_run_{run_idx}")
            summary_file = os.path.join(run_path, "scenario_summary.json")
            if not os.path.exists(summary_file):
                break
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    run_summaries.append(json.load(f))
                    run_paths.append(run_path)
            except Exception:
                pass
                
        if not run_summaries:
            raise HTTPException(status_code=404, detail=f"No run summaries found for group {name}")
            
        cagrs = []
        max_drawdowns = []
        win_rates = []
        total_trades_list = []
        profit_factors = []
        final_capitals = []
        
        first_sum = run_summaries[0]
        start_date_str = first_sum.get("start_date")
        end_date_str = first_sum.get("end_date")
        
        years = 1.0
        if start_date_str and end_date_str:
            try:
                from datetime import datetime
                start = datetime.strptime(start_date_str, "%Y-%m-%d")
                end = datetime.strptime(end_date_str, "%Y-%m-%d")
                years = (end - start).days / 365.25
            except Exception:
                pass
                
        for s in run_summaries:
            f_cap = s.get("final_capital", 100000.0)
            i_cap = s.get("initial_capital", 100000.0)
            cagr_val = (f_cap / i_cap) ** (1 / years) - 1 if years > 0 and i_cap > 0 else 0.0
            
            cagrs.append(cagr_val)
            
            raw_dd = s.get("max_drawdown", 0.0)
            if isinstance(raw_dd, dict):
                dd_val = -raw_dd.get("pct", 0.0) / 100.0
            else:
                dd_val = float(raw_dd)
            max_drawdowns.append(dd_val)
            
            win_rates.append(s.get("win_rate", 0.0))
            total_trades_list.append(s.get("total_trades", 0))
            profit_factors.append(s.get("profit_factor", 0.0))
            final_capitals.append(f_cap)
            
        import numpy as np
        
        # Calculate yearly returns for each run
        run_yearly_returns = []
        for run_path in run_paths:
            yearly_returns = {}
            equity_file = os.path.join(run_path, "scenario_equity_curve.csv")
            if os.path.exists(equity_file):
                try:
                    with open(equity_file, "r", encoding="utf-8") as eq_f:
                        reader = csv.DictReader(eq_f)
                        equity_by_year = {}
                        for row in reader:
                            e_key = "total_equity" if "total_equity" in row else "equity"
                            if "date" in row and e_key in row:
                                date_str = row["date"]
                                year_str = date_str.split("-")[0]
                                eq_val = float(row[e_key])
                                if year_str not in equity_by_year:
                                    equity_by_year[year_str] = []
                                equity_by_year[year_str].append((date_str, eq_val))
                        
                        sorted_years = sorted(equity_by_year.keys())
                        for i, year in enumerate(sorted_years):
                            year_data = sorted(equity_by_year[year], key=lambda x: x[0])
                            first_eq = year_data[0][1]
                            if i > 0:
                                prev_year = sorted_years[i-1]
                                prev_year_data = sorted(equity_by_year[prev_year], key=lambda x: x[0])
                                first_eq = prev_year_data[-1][1]
                            last_eq = year_data[-1][1]
                            
                            ret_pct = ((last_eq - first_eq) / first_eq) * 100.0 if first_eq > 0 else 0.0
                            yearly_returns[year] = ret_pct
                except Exception:
                    pass
            run_yearly_returns.append(yearly_returns)
            
        # Average yearly returns across runs
        avg_yearly_returns = {}
        if run_yearly_returns:
            all_years = set()
            for yr in run_yearly_returns:
                all_years.update(yr.keys())
            for year in all_years:
                vals = [yr[year] for yr in run_yearly_returns if year in yr]
                if vals:
                    avg_yearly_returns[year] = float(np.mean(vals))
                    
        # Average yearly performance metrics (Total Trades, Win Rate, Net PnL, Profit Factor, Avg PnL %)
        yearly_performance = {}
        first_yp = first_sum.get("yearly_performance", {})
        
        for year in first_yp.keys():
            trades_list = []
            win_rates_y = []
            net_pnls = []
            profit_factors_y = []
            avg_pnl_pcts = []
            
            for s in run_summaries:
                yp = s.get("yearly_performance", {})
                y_item = yp.get(year, {})
                if y_item:
                    trades_list.append(y_item.get("total_trades", 0))
                    win_rates_y.append(y_item.get("win_rate", 0.0))
                    net_pnls.append(y_item.get("net_pnl", 0.0))
                    profit_factors_y.append(y_item.get("profit_factor", 0.0))
                    avg_pnl_pcts.append(y_item.get("avg_pnl_pct", 0.0))
                    
            if trades_list:
                yearly_performance[year] = {
                    "total_trades": int(np.mean(trades_list)),
                    "win_rate": float(np.mean(win_rates_y)),
                    "net_pnl": float(np.mean(net_pnls)),
                    "profit_factor": float(np.mean(profit_factors_y)),
                    "avg_pnl_pct": float(np.mean(avg_pnl_pcts)),
                    "spy_return_pct": first_yp[year].get("spy_return_pct", 0.0),
                    "return_pct": avg_yearly_returns.get(year, 0.0)
                }
                
        cagr_avg = float(np.mean(cagrs))
        cagr_max = float(np.max(cagrs))
        cagr_min = float(np.min(cagrs))
        
        max_dd_avg = float(np.mean(max_drawdowns))
        max_dd_min = float(np.min(max_drawdowns))
        max_dd_max = float(np.max(max_drawdowns))
        
        win_rate_avg = float(np.mean(win_rates))
        total_trades_avg = int(np.mean(total_trades_list))
        profit_factor_avg = float(np.mean(profit_factors))
        
        final_capital_avg = float(np.mean(final_capitals))
        final_capital_max = float(np.max(final_capitals))
        final_capital_min = float(np.min(final_capitals))
        
        summary_obj = BacktestScenarioSummary(
            cagr=cagr_avg,
            profit_factor=profit_factor_avg,
            max_drawdown=max_dd_avg,
            win_rate=win_rate_avg,
            total_trades=total_trades_avg,
            yearly_performance=yearly_performance
        )
        
        summary_obj.__dict__.update({
            "is_monte_carlo": True,
            "runs_count": len(run_summaries),
            "cagr_avg": cagr_avg,
            "cagr_max": cagr_max,
            "cagr_min": cagr_min,
            "max_drawdown_avg": max_dd_avg,
            "max_drawdown_min": max_dd_min,
            "max_drawdown_max": max_dd_max,
            "win_rate_avg": win_rate_avg,
            "total_trades_avg": total_trades_avg,
            "profit_factor_avg": profit_factor_avg,
            "final_capital_avg": final_capital_avg,
            "final_capital_max": final_capital_max,
            "final_capital_min": final_capital_min
        })
        
        return summary_obj

    scenario_path = resolve_scenario_path(name)
    summary_file = os.path.join(scenario_path, "scenario_summary.json")
    
    if not os.path.exists(summary_file):
        raise HTTPException(status_code=404, detail="scenario_summary.json not found in the scenario directory")
        
    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        total_trades = raw_data.get("total_trades", 0)
        win_rate = raw_data.get("win_rate", 0.0)
        profit_factor = raw_data.get("profit_factor", 0.0)
        
        # 1. Resolve max drawdown
        raw_dd = raw_data.get("max_drawdown", 0.0)
        if isinstance(raw_dd, dict):
            # Convert percentage (e.g. 47.61%) to dynamic negative ratio (e.g. -0.4761)
            max_drawdown = -raw_dd.get("pct", 0.0) / 100.0
        else:
            max_drawdown = float(raw_dd)
            
        # 2. Resolve CAGR dynamically if not present
        cagr = raw_data.get("cagr")
        if cagr is None:
            start_date_str = raw_data.get("start_date")
            end_date_str = raw_data.get("end_date")
            initial_cap = raw_data.get("initial_capital", 1.0)
            final_cap = raw_data.get("final_capital", 1.0)
            
            if start_date_str and end_date_str:
                try:
                    from datetime import datetime
                    start = datetime.strptime(start_date_str, "%Y-%m-%d")
                    end = datetime.strptime(end_date_str, "%Y-%m-%d")
                    years = (end - start).days / 365.25
                    if years > 0 and initial_cap > 0:
                        cagr = (final_cap / initial_cap) ** (1 / years) - 1
                    else:
                        cagr = 0.0
                except Exception:
                    cagr = 0.0
            else:
                cagr = 0.0
        else:
            cagr = float(cagr)
            
        # 3. Calculate and inject exact yearly returns dynamically from scenario_equity_curve.csv
        yearly_returns = {}
        equity_file = os.path.join(scenario_path, "scenario_equity_curve.csv")
        if os.path.exists(equity_file):
            try:
                with open(equity_file, "r", encoding="utf-8") as eq_f:
                    reader = csv.DictReader(eq_f)
                    equity_by_year = {}
                    for row in reader:
                        e_key = "total_equity" if "total_equity" in row else "equity"
                        if "date" in row and e_key in row:
                            date_str = row["date"]
                            year_str = date_str.split("-")[0]
                            eq_val = float(row[e_key])
                            if year_str not in equity_by_year:
                                equity_by_year[year_str] = []
                            equity_by_year[year_str].append((date_str, eq_val))
                    
                    # Sort years chronologically
                    sorted_years = sorted(equity_by_year.keys())
                    for i, year in enumerate(sorted_years):
                        year_data = sorted(equity_by_year[year], key=lambda x: x[0])
                        first_eq = year_data[0][1]
                        
                        # Use previous year's last day equity as base for better precision (start of this year)
                        if i > 0:
                            prev_year = sorted_years[i-1]
                            prev_year_data = sorted(equity_by_year[prev_year], key=lambda x: x[0])
                            first_eq = prev_year_data[-1][1]
                            
                        last_eq = year_data[-1][1]
                        
                        if first_eq > 0:
                            ret_pct = ((last_eq - first_eq) / first_eq) * 100.0
                        else:
                            ret_pct = 0.0
                        yearly_returns[year] = ret_pct
            except Exception as ex:
                # Log error and fallback silently
                print(f"Error dynamically calculating yearly returns from CSV: {ex}")
                
        yearly_performance = raw_data.get("yearly_performance", {})
        for year, item in yearly_performance.items():
            if isinstance(item, dict):
                # Inject strategy yearly return pct
                item["return_pct"] = yearly_returns.get(year, 0.0)
                
        return BacktestScenarioSummary(
            cagr=cagr,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            total_trades=total_trades,
            yearly_performance=yearly_performance
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read/parse summary JSON: {str(e)}")

@router.get("/backtest/scenario/{name}/equity", response_model=List[BacktestEquityPoint])
def get_scenario_equity(name: str):
    """
    Returns daily equity curve points from scenario_equity_curve.csv.
    Enriches with SPY, QQQ, TQQQ, SOXL equity comparison and MarketTrendScore history dynamically.
    Supports Monte Carlo group integration with multiple run curves.
    """
    import numpy as np
    
    if is_monte_carlo_group(name):
        sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
        run_curves = {}
        valid_runs = []
        
        for run_idx in range(100):
            run_path = os.path.join(sub_scenario_dir, f"{name}_run_{run_idx}")
            csv_file = os.path.join(run_path, "scenario_equity_curve.csv")
            if not os.path.exists(csv_file):
                break
            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    run_curves[run_idx] = list(reader)
                    valid_runs.append(run_idx)
            except Exception:
                pass
                
        if not valid_runs:
            raise HTTPException(status_code=404, detail=f"No run equity curves found for group {name}")
            
        ref_run = valid_runs[0]
        ref_rows = run_curves[ref_run]
        
        run_equity_maps = {}
        for run_idx in valid_runs:
            run_equity_maps[run_idx] = {}
            for row in run_curves[run_idx]:
                d_key = "total_equity" if "total_equity" in row else "equity"
                if "date" in row and d_key in row:
                    run_equity_maps[run_idx][row["date"]] = float(row[d_key])
                    
        start_date_str = ref_rows[0]["date"]
        end_date_str = ref_rows[-1]["date"]
        
        spy_prices_dict = {}
        qqq_prices_dict = {}
        tqqq_prices_dict = {}
        soxl_prices_dict = {}
        trend_scores_dict = {}
        
        try:
            from db.database import get_active_db_path
            import pandas as pd
            
            db_path = get_active_db_path() or os.path.join(PROJECT_ROOT, "data", "stocktool.db")
            parquet_dir = os.path.join(os.path.dirname(db_path), "parquet_master")
            pointer_file = os.path.join(parquet_dir, "latest_master.json")
            
            if os.path.exists(pointer_file):
                with open(pointer_file, "r", encoding="utf-8") as f:
                    latest_files = json.load(f)
                
                symbols_file = os.path.join(parquet_dir, os.path.basename(latest_files['symbols']))
                prices_file = os.path.join(parquet_dir, os.path.basename(latest_files['prices']))
                
                if os.path.exists(symbols_file) and os.path.exists(prices_file):
                    df_symbols = pd.read_parquet(symbols_file)
                    
                    def get_id(ticker):
                        s = df_symbols[df_symbols['ticker'] == ticker]['id']
                        return int(s.iloc[0]) if not s.empty else None
                        
                    spy_id = get_id("SPY") or get_id("^GSPC")
                    qqq_id = get_id("QQQ")
                    tqqq_id = get_id("TQQQ")
                    soxl_id = get_id("SOXL")
                    
                    valid_ids = [i for i in [spy_id, qqq_id, tqqq_id, soxl_id] if i is not None]
                    if valid_ids:
                        df_prices = pd.read_parquet(
                            prices_file,
                            filters=[
                                ('symbol_id', 'in', valid_ids),
                                ('date', '>=', start_date_str),
                                ('date', '<=', end_date_str)
                            ],
                            columns=['symbol_id', 'date', 'close']
                        )
                        df_prices['date'] = df_prices['date'].astype(str)
                        
                        if spy_id is not None:
                            spy_df = df_prices[df_prices['symbol_id'] == spy_id]
                            spy_prices_dict = {row['date']: float(row['close']) for _, row in spy_df.iterrows()}
                        if qqq_id is not None:
                            qqq_df = df_prices[df_prices['symbol_id'] == qqq_id]
                            qqq_prices_dict = {row['date']: float(row['close']) for _, row in qqq_df.iterrows()}
                        if tqqq_id is not None:
                            tqqq_df = df_prices[df_prices['symbol_id'] == tqqq_id]
                            tqqq_prices_dict = {row['date']: float(row['close']) for _, row in tqqq_df.iterrows()}
                        if soxl_id is not None:
                            soxl_df = df_prices[df_prices['symbol_id'] == soxl_id]
                            soxl_prices_dict = {row['date']: float(row['close']) for _, row in soxl_df.iterrows()}
        except Exception as py_ex:
            print(f"Parquet query failed during get_scenario_equity: {py_ex}")
            
        try:
            from db.database import SessionLocal
            from db.models import MarketSignal
            db = SessionLocal()
            if db:
                signals = db.query(MarketSignal).filter(
                    MarketSignal.date.between(start_date_str, end_date_str)
                ).all()
                trend_scores_dict = {str(s.date): float(s.market_trend_score) for s in signals if s.market_trend_score is not None}
                db.close()
        except Exception:
            pass
            
        first_row_eq_key = "total_equity" if "total_equity" in ref_rows[0] else "equity"
        initial_equity = float(ref_rows[0][first_row_eq_key]) if first_row_eq_key in ref_rows[0] else 100000.0
        
        spy_start_price = None
        qqq_start_price = None
        tqqq_start_price = None
        soxl_start_price = None
        for row in ref_rows:
            d_val = row["date"]
            if d_val in spy_prices_dict and spy_start_price is None:
                spy_start_price = spy_prices_dict[d_val]
            if d_val in qqq_prices_dict and qqq_start_price is None:
                qqq_start_price = qqq_prices_dict[d_val]
            if d_val in tqqq_prices_dict and tqqq_start_price is None:
                tqqq_start_price = tqqq_prices_dict[d_val]
            if d_val in soxl_prices_dict and soxl_start_price is None:
                soxl_start_price = soxl_prices_dict[d_val]
                
        points = []
        for row in ref_rows:
            date_val = row["date"]
            equity_key = "total_equity" if "total_equity" in row else "equity"
            cash_key = "cash"
            
            if date_val and equity_key in row and cash_key in row:
                equities_at_date = []
                run_equities_dict = {}
                for run_idx in valid_runs:
                    eq_val = run_equity_maps[run_idx].get(date_val)
                    if eq_val is not None:
                        equities_at_date.append(eq_val)
                        run_equities_dict[f"run_{run_idx}"] = eq_val
                        
                avg_equity = float(np.mean(equities_at_date)) if equities_at_date else float(row[equity_key])
                cash_val = float(row[cash_key])
                
                spy_equity_val = initial_equity
                if spy_start_price and date_val in spy_prices_dict:
                    spy_equity_val = (spy_prices_dict[date_val] / spy_start_price) * initial_equity
                qqq_equity_val = None
                if qqq_start_price and date_val in qqq_prices_dict:
                    qqq_equity_val = (qqq_prices_dict[date_val] / qqq_start_price) * initial_equity
                tqqq_equity_val = None
                if tqqq_start_price and date_val in tqqq_prices_dict:
                    tqqq_equity_val = (tqqq_prices_dict[date_val] / tqqq_start_price) * initial_equity
                soxl_equity_val = None
                if soxl_start_price and date_val in soxl_prices_dict:
                    soxl_equity_val = (soxl_prices_dict[date_val] / soxl_start_price) * initial_equity
                    
                trend_score_val = trend_scores_dict.get(date_val, 0.0)
                
                points.append(BacktestEquityPoint(
                    date=date_val,
                    equity=avg_equity,
                    cash=cash_val,
                    spy_equity=spy_equity_val,
                    qqq_equity=qqq_equity_val,
                    tqqq_equity=tqqq_equity_val,
                    soxl_equity=soxl_equity_val,
                    trend_score=trend_score_val,
                    run_equities=run_equities_dict
                ))
        return points

    scenario_path = resolve_scenario_path(name)
    csv_file = os.path.join(scenario_path, "scenario_equity_curve.csv")
    
    if not os.path.exists(csv_file):
        raise HTTPException(status_code=404, detail="scenario_equity_curve.csv not found in the scenario directory")
    
    points = []
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        if not rows:
            return []
            
        start_date_str = rows[0]["date"]
        end_date_str = rows[-1]["date"]
        
        # Read initial capital for SPY index baseline
        first_row_eq_key = "total_equity" if "total_equity" in rows[0] else "equity"
        initial_equity = float(rows[0][first_row_eq_key]) if first_row_eq_key in rows[0] else 100000.0
        
        # Enriched DB queries & Parquet queries
        spy_prices_dict = {}
        qqq_prices_dict = {}
        tqqq_prices_dict = {}
        soxl_prices_dict = {}
        trend_scores_dict = {}
        
        # 1. Fetch benchmark prices from Parquet
        try:
            from db.database import get_active_db_path
            import pandas as pd
            
            db_path = get_active_db_path() or os.path.join(PROJECT_ROOT, "data", "stocktool.db")
            parquet_dir = os.path.join(os.path.dirname(db_path), "parquet_master")
            pointer_file = os.path.join(parquet_dir, "latest_master.json")
            
            if os.path.exists(pointer_file):
                with open(pointer_file, "r", encoding="utf-8") as f:
                    latest_files = json.load(f)
                
                # Load symbols from Parquet
                symbols_file = os.path.join(parquet_dir, os.path.basename(latest_files['symbols']))
                prices_file = os.path.join(parquet_dir, os.path.basename(latest_files['prices']))
                
                if os.path.exists(symbols_file) and os.path.exists(prices_file):
                    df_symbols = pd.read_parquet(symbols_file)
                    
                    # Resolve symbol IDs
                    def get_id(ticker):
                        s = df_symbols[df_symbols['ticker'] == ticker]['id']
                        return int(s.iloc[0]) if not s.empty else None
                        
                    spy_id = get_id("SPY") or get_id("^GSPC")
                    qqq_id = get_id("QQQ")
                    tqqq_id = get_id("TQQQ")
                    soxl_id = get_id("SOXL")
                    
                    valid_ids = [i for i in [spy_id, qqq_id, tqqq_id, soxl_id] if i is not None]
                    if valid_ids:
                        df_prices = pd.read_parquet(
                            prices_file,
                            filters=[
                                ('symbol_id', 'in', valid_ids),
                                ('date', '>=', start_date_str),
                                ('date', '<=', end_date_str)
                            ],
                            columns=['symbol_id', 'date', 'close']
                        )
                        df_prices['date'] = df_prices['date'].astype(str)
                        
                        if spy_id is not None:
                            spy_df = df_prices[df_prices['symbol_id'] == spy_id]
                            spy_prices_dict = {row['date']: float(row['close']) for _, row in spy_df.iterrows()}
                        if qqq_id is not None:
                            qqq_df = df_prices[df_prices['symbol_id'] == qqq_id]
                            qqq_prices_dict = {row['date']: float(row['close']) for _, row in qqq_df.iterrows()}
                        if tqqq_id is not None:
                            tqqq_df = df_prices[df_prices['symbol_id'] == tqqq_id]
                            tqqq_prices_dict = {row['date']: float(row['close']) for _, row in tqqq_df.iterrows()}
                        if soxl_id is not None:
                            soxl_df = df_prices[df_prices['symbol_id'] == soxl_id]
                            soxl_prices_dict = {row['date']: float(row['close']) for _, row in soxl_df.iterrows()}
        except Exception as py_ex:
            print(f"Parquet query failed during get_scenario_equity: {py_ex}")
            
        # 2. Fetch Market Trend Score from DB
        try:
            from db.database import SessionLocal
            from db.models import MarketSignal
            
            db = SessionLocal()
            if db:
                signals = db.query(MarketSignal).filter(
                    MarketSignal.date.between(start_date_str, end_date_str)
                ).all()
                trend_scores_dict = {str(s.date): float(s.market_trend_score) for s in signals if s.market_trend_score is not None}
                db.close()
        except Exception as db_ex:
            print(f"Database query skipped during get_scenario_equity: {db_ex}")
            
        # Determine baseline prices to align chart start
        spy_start_price = None
        qqq_start_price = None
        tqqq_start_price = None
        soxl_start_price = None
        for row in rows:
            d_val = row["date"]
            if d_val in spy_prices_dict and spy_start_price is None:
                spy_start_price = spy_prices_dict[d_val]
            if d_val in qqq_prices_dict and qqq_start_price is None:
                qqq_start_price = qqq_prices_dict[d_val]
            if d_val in tqqq_prices_dict and tqqq_start_price is None:
                tqqq_start_price = tqqq_prices_dict[d_val]
            if d_val in soxl_prices_dict and soxl_start_price is None:
                soxl_start_price = soxl_prices_dict[d_val]
                
        # Parse CSV rows and attach scaled equities and Trend Scores
        for row in rows:
            equity_key = "total_equity" if "total_equity" in row else "equity"
            cash_key = "cash"
            date_key = "date"
            
            if date_key in row and equity_key in row and cash_key in row:
                date_val = row[date_key]
                eq_val = float(row[equity_key])
                cash_val = float(row[cash_key])
                
                # Scale SPY equity matching portfolio start balance
                spy_equity_val = initial_equity
                if spy_start_price and date_val in spy_prices_dict:
                    spy_equity_val = (spy_prices_dict[date_val] / spy_start_price) * initial_equity
                    
                # Scale QQQ
                qqq_equity_val = None
                if qqq_start_price and date_val in qqq_prices_dict:
                    qqq_equity_val = (qqq_prices_dict[date_val] / qqq_start_price) * initial_equity
                    
                # Scale TQQQ
                tqqq_equity_val = None
                if tqqq_start_price and date_val in tqqq_prices_dict:
                    tqqq_equity_val = (tqqq_prices_dict[date_val] / tqqq_start_price) * initial_equity
                    
                # Scale SOXL
                soxl_equity_val = None
                if soxl_start_price and date_val in soxl_prices_dict:
                    soxl_equity_val = (soxl_prices_dict[date_val] / soxl_start_price) * initial_equity
                    
                # Market Trend Score (0.0 to 100.0)
                trend_score_val = trend_scores_dict.get(date_val, 0.0)
                
                points.append(BacktestEquityPoint(
                    date=date_val,
                    equity=eq_val,
                    cash=cash_val,
                    spy_equity=spy_equity_val,
                    qqq_equity=qqq_equity_val,
                    tqqq_equity=tqqq_equity_val,
                    soxl_equity=soxl_equity_val,
                    trend_score=trend_score_val
                ))
        return points
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse equity CSV: {str(e)}")

@router.get("/backtest/scenario/{name}/trades", response_model=List[BacktestTradeLogItem])
def get_scenario_trades(name: str):
    """
    Returns transaction history from scenario_trade_logs.csv.
    Supports 'latest' as a dynamic name alias, and Monte Carlo groups.
    For groups, returns the trade logs of run_0 as a representative.
    """
    if is_monte_carlo_group(name):
        sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
        scenario_path = os.path.join(sub_scenario_dir, f"{name}_run_0")
    else:
        scenario_path = resolve_scenario_path(name)
        
    csv_file = os.path.join(scenario_path, "scenario_trade_logs.csv")
    
    if not os.path.exists(csv_file):
        raise HTTPException(status_code=404, detail="scenario_trade_logs.csv not found in the scenario directory")
        
    trades = []
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Support mapping from actual backtest exit logs structure
                ticker_val = row.get("ticker", "")
                
                # Map exit_date as primary date, fallback to entry_date or general date
                date_val = row.get("exit_date", row.get("date", row.get("entry_date", "")))
                
                # Map exit_price as primary price, fallback to price or entry_price
                price_val = float(row.get("exit_price", row.get("price", row.get("entry_price", 0.0))))
                
                # Map shares as primary size, fallback to size
                size_val = float(row.get("shares", row.get("size", 0.0)))
                
                # Map exit_reason as primary reason, fallback to reason
                reason_val = row.get("exit_reason", row.get("reason", ""))
                
                # Convert pnl ratio (e.g. -0.05) to percentage (e.g. -5.0).
                raw_pnl = float(row.get("pnl_pct", 0.0))
                if abs(raw_pnl) > 0.0 and abs(raw_pnl) < 1.0:
                    pnl_pct_val = raw_pnl * 100.0
                else:
                    pnl_pct_val = raw_pnl
                
                trades.append(BacktestTradeLogItem(
                    date=date_val,
                    ticker=ticker_val,
                    action="SELL",  # Exit trades are sells
                    price=price_val,
                    size=size_val,
                    reason=reason_val,
                    pnl_pct=pnl_pct_val
                ))
        return trades
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse trades CSV: {str(e)}")
