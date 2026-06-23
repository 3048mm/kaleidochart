import os
import csv
import json
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks

from api.schemas import (
    BacktestScenarioSummary,
    BacktestEquityPoint,
    BacktestTradeLogItem,
    EtfSingleSummary,
    EtfSingleEquityPoint,
    EtfSingleRegimeItem,
    ScenarioComparisonSummary,
    ScenarioComparisonEquityPoint,
)

router = APIRouter(tags=["backtest"])

# Default output directory relative to project root (stocktool/output)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

def is_monte_carlo_group(name: str) -> bool:
    """
    Checks if the name represents a Monte Carlo simulation group (e.g. 'A', 'B1', or 'A_mts_raw').
    It checks if '{name}_run_0' directory exists in 'output/scenario'.
    """
    base_groups = ["A", "B1", "B2", "B3", "B4", "E2"]
    is_valid_group = False
    
    if name in base_groups:
        is_valid_group = True
    else:
        for bg in base_groups:
            if name.startswith(f"{bg}_") and not name.endswith("_run_"):
                is_valid_group = True
                break
                
    if is_valid_group:
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
                    base_prefixes = ["A", "B1", "B2", "B3", "B4", "E2"]
                    is_matching_mc = False
                    if group_name in base_prefixes:
                        is_matching_mc = True
                    else:
                        for bp in base_prefixes:
                            if group_name.startswith(f"{bp}_"):
                                is_matching_mc = True
                                break
                    if is_matching_mc:
                        groups_detected.add(group_name)
            
    # Sort newest first
    candidates.sort(key=lambda x: x[1], reverse=True)
    sorted_names = [name for name, _ in candidates if "_run_" not in name]
    
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
        
        # Average exit reasons stats
        exit_reasons_merged = {}
        reasons_detected = set()
        for s in run_summaries:
            er = s.get("exit_reasons", {})
            if isinstance(er, dict):
                reasons_detected.update(er.keys())
            
        for reason in reasons_detected:
            counts = []
            pnl_pcts = []
            holding_days_list = []
            for s in run_summaries:
                er = s.get("exit_reasons", {})
                if isinstance(er, dict):
                    r_stats = er.get(reason, {})
                    if isinstance(r_stats, dict):
                        counts.append(r_stats.get("count", 0))
                        pnl_pcts.append(r_stats.get("avg_pnl_pct", 0.0))
                        holding_days_list.append(r_stats.get("avg_holding_days", 0.0))
            
            if counts:
                exit_reasons_merged[reason] = {
                    "count": int(np.mean(counts)),
                    "avg_pnl_pct": float(np.mean(pnl_pcts)) if any(c > 0 for c in counts) else 0.0,
                    "avg_holding_days": float(np.mean(holding_days_list)) if any(c > 0 for c in counts) else 0.0
                }
        
        summary_obj = BacktestScenarioSummary(
            cagr=cagr_avg,
            profit_factor=profit_factor_avg,
            max_drawdown=max_dd_avg,
            win_rate=win_rate_avg,
            total_trades=total_trades_avg,
            final_capital=final_capital_avg,
            yearly_performance=yearly_performance,
            exit_reasons=exit_reasons_merged
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
            "final_capital": final_capital_avg,
            "final_capital_avg": final_capital_avg,
            "final_capital_max": final_capital_max,
            "final_capital_min": final_capital_min,
            "exit_reasons": exit_reasons_merged
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
            final_capital=raw_data.get("final_capital"),
            yearly_performance=yearly_performance,
            exit_reasons=raw_data.get("exit_reasons", {})
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


# ============================================================
# ETF Single Backtest Endpoints
# ============================================================

def _resolve_etf_single_path(ticker: str) -> str:
    """Resolve the output directory path for an ETF single backtest result."""
    clean = os.path.basename(ticker).upper()
    if clean != ticker.upper() or ".." in ticker:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    etf_dir = os.path.join(OUTPUT_DIR, "etf_single", clean)
    if not os.path.isdir(etf_dir):
        raise HTTPException(status_code=404, detail=f"No ETF backtest results found for '{clean}'")
    return etf_dir


@router.get("/backtest/etf-single/{ticker}/summary", response_model=EtfSingleSummary)
def get_etf_single_summary(ticker: str):
    """Returns the ETF single backtest summary JSON."""
    etf_dir = _resolve_etf_single_path(ticker)
    summary_file = os.path.join(etf_dir, "etf_single_summary.json")
    if not os.path.exists(summary_file):
        raise HTTPException(status_code=404, detail="etf_single_summary.json not found")
    try:
        with open(summary_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return EtfSingleSummary(**raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse summary: {str(e)}")


@router.get("/backtest/etf-single/{ticker}/equity", response_model=List[EtfSingleEquityPoint])
def get_etf_single_equity(ticker: str):
    """Returns the ETF single backtest equity curve from CSV."""
    etf_dir = _resolve_etf_single_path(ticker)
    csv_file = os.path.join(etf_dir, "etf_single_equity.csv")
    if not os.path.exists(csv_file):
        raise HTTPException(status_code=404, detail="etf_single_equity.csv not found")
    try:
        points = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mts_eq = row.get("mts_v3_raw_equity")
                mts_pos = row.get("mts_v3_raw_position_pct")
                mts_sc = row.get("mts_score")
                mts_ema = row.get("mts_ema5")
                based_eq = row.get("based_sma200_equity")
                based_pos = row.get("based_sma200_position_pct")
                based_sma63_eq = row.get("based_sma63_equity")
                based_sma63_pos = row.get("based_sma63_position_pct")
                
                points.append(EtfSingleEquityPoint(
                    date=row.get("date", ""),
                    vxv_equity=float(row.get("vxv_equity", 0)),
                    buyhold_equity=float(row.get("buyhold_equity", 0)),
                    dca_equity=float(row.get("dca_equity", 0)),
                    vxv_position_pct=float(row.get("vxv_position_pct", 0)),
                    regime=row.get("regime", ""),
                    mts_v3_raw_equity=float(mts_eq) if mts_eq else None,
                    mts_v3_raw_position_pct=float(mts_pos) if mts_pos else None,
                    mts_score=float(mts_sc) if mts_sc else None,
                    mts_ema5=float(mts_ema) if mts_ema else None,
                    based_sma200_equity=float(based_eq) if based_eq else None,
                    based_sma200_position_pct=float(based_pos) if based_pos else None,
                    based_sma63_equity=float(based_sma63_eq) if based_sma63_eq else None,
                    based_sma63_position_pct=float(based_sma63_pos) if based_sma63_pos else None,
                ))
        return points
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse equity CSV: {str(e)}")


@router.get("/backtest/etf-single/{ticker}/regimes", response_model=List[EtfSingleRegimeItem])
def get_etf_single_regimes(ticker: str):
    """Returns the ETF single backtest regime history from CSV."""
    etf_dir = _resolve_etf_single_path(ticker)
    csv_file = os.path.join(etf_dir, "etf_single_regimes.csv")
    if not os.path.exists(csv_file):
        raise HTTPException(status_code=404, detail="etf_single_regimes.csv not found")
    try:
        items = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ema5_val = row.get("ema5", "")
                ema21_val = row.get("ema21", "")
                items.append(EtfSingleRegimeItem(
                    date=row.get("date", ""),
                    regime=row.get("regime", ""),
                    vxv_vix_ratio=float(row.get("vxv_vix_ratio", 0)),
                    ema5=float(ema5_val) if ema5_val and ema5_val.lower() != "none" else None,
                    ema21=float(ema21_val) if ema21_val and ema21_val.lower() != "none" else None,
                    position_pct=float(row.get("position_pct", 0)),
                ))
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse regimes CSV: {str(e)}")


# ============================================================
# Scenario Comparison Endpoints
# ============================================================

_comparison_run_status = {"status": "idle"}

def _bg_run_comparison(
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    min_score: int,
    stop_loss_pct: float,
    profit_target_pct: float,
    refresh_cache: bool,
    use_vxv_vix: bool
):
    global _comparison_run_status
    import datetime
    _comparison_run_status = {
        "status": "running", 
        "start_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        from backend.backtest.scenario_comparison_runner import run_comparison
        comp_dir = os.path.join(OUTPUT_DIR, "scenario_comparison")
        run_comparison(
            start_date=start_date,
            end_date=end_date,
            output_dir=comp_dir,
            initial_capital=initial_capital,
            max_positions=max_positions,
            min_score=min_score,
            stop_loss_pct=stop_loss_pct,
            profit_target_pct=profit_target_pct,
            refresh_cache=refresh_cache,
            use_vxv_vix=use_vxv_vix
        )
        _comparison_run_status = {"status": "completed"}
    except Exception as e:
        _comparison_run_status = {"status": "error", "error": str(e)}


@router.post("/backtest/comparison/run")
def run_scenario_comparison(
    start_date: str,
    end_date: str,
    initial_capital: float = 100000.0,
    max_positions: int = 8,
    min_score: int = 2,
    stop_loss_pct: float = -0.08,
    profit_target_pct: float = 0.20,
    refresh_cache: bool = False,
    use_vxv_vix: bool = False,
    background_tasks: BackgroundTasks = None
):
    """
    Triggers the side-by-side market regime comparison test as a background task.
    """
    global _comparison_run_status
    if _comparison_run_status["status"] == "running":
        raise HTTPException(status_code=400, detail="A comparison run is already in progress.")
        
    from fastapi import BackgroundTasks as FastAPIBackgroundTasks
    
    # Instantiate or use background tasks parameter
    bg_tasks = background_tasks or FastAPIBackgroundTasks()
    
    bg_tasks.add_task(
        _bg_run_comparison,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        max_positions=max_positions,
        min_score=min_score,
        stop_loss_pct=stop_loss_pct,
        profit_target_pct=profit_target_pct,
        refresh_cache=refresh_cache,
        use_vxv_vix=use_vxv_vix
    )
    _comparison_run_status = {"status": "running"}
    return {"status": "started", "message": "Comparison run initiated."}


@router.get("/backtest/comparison/status")
def get_scenario_comparison_status():
    """
    Returns the general trigger state of the comparison run.
    """
    global _comparison_run_status
    return _comparison_run_status


@router.get("/backtest/comparison/progress")
def get_scenario_comparison_progress():
    """
    Returns the average progress percentage of the ongoing comparison run.
    """
    models = ['mts_raw', 'vxv_vix_ema', 'spy_sma200', 'spy_sma63']
    comp_dir = os.path.join(OUTPUT_DIR, "scenario_comparison")
    
    from backend.backtest.scenario_runner import get_scenario_progress
    
    status_list = []
    progress_pcts = []
    
    for model in models:
        model_dir = os.path.join(comp_dir, model)
        prog = get_scenario_progress(model_dir)
        status_list.append(prog.get("status", "not_started"))
        if "progress_pct" in prog:
            progress_pcts.append(prog["progress_pct"])
            
    if all(s == "completed" for s in status_list):
        return {"status": "completed", "progress_pct": 100.0}
    elif any(s == "running" for s in status_list):
        avg_prog = sum(progress_pcts) / len(models) if progress_pcts else 0.0
        return {"status": "running", "progress_pct": round(avg_prog, 2)}
    elif any(s == "error" for s in status_list):
        return {"status": "error", "progress_pct": 0.0}
    else:
        return {"status": "not_started", "progress_pct": 0.0}


@router.get("/backtest/comparison/summary", response_model=ScenarioComparisonSummary)
def get_scenario_comparison_summary():
    """
    Returns the combined scenario comparison summary.
    """
    summary_path = os.path.join(OUTPUT_DIR, "scenario_comparison", "comparison_summary.json")
    if not os.path.exists(summary_path):
        raise HTTPException(status_code=404, detail="Comparison summary not found. Please run the comparison test first.")
    
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse comparison summary: {str(e)}")


@router.get("/backtest/comparison/equity", response_model=List[ScenarioComparisonEquityPoint])
def get_scenario_comparison_equity():
    """
    Returns the daily equity curves for all regime models compared side-by-side.
    """
    csv_path = os.path.join(OUTPUT_DIR, "scenario_comparison", "comparison_equity_curve.csv")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="Comparison equity curve CSV not found. Please run the comparison test first.")
    
    try:
        points = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                points.append(ScenarioComparisonEquityPoint(
                    date=row.get("date", ""),
                    spy_equity=float(row.get("spy_equity", 0.0)),
                    equity_mts_raw=float(row.get("equity_mts_raw", 0.0)),
                    equity_vxv_vix_ema=float(row.get("equity_vxv_vix_ema", 0.0)),
                    equity_spy_sma200=float(row.get("equity_spy_sma200", 0.0)),
                    equity_spy_sma63=float(row.get("equity_spy_sma63", 0.0))
                ))
        return points
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse comparison equity CSV: {str(e)}")


@router.get("/backtest/comparison/group/{group}")
def get_group_comparison(group: str):
    """
    Returns the side-by-side comparison of the 4 market regimes for a given Monte Carlo group.
    Loads and aggregates results across their respective 10 runs.
    """
    base_groups = ["A", "B1", "B2", "B3", "B4", "E2"]
    if group not in base_groups:
        raise HTTPException(status_code=400, detail="Invalid group name")
    sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
    
    # Dynamically detect regime model names from output/scenario directories
    detected_models = set()
    if os.path.exists(sub_scenario_dir):
        for item in os.listdir(sub_scenario_dir):
            if item.startswith(f"{group}_") and "_run_" in item:
                # Format: {group}_{model}_run_{idx}
                parts = item.split("_run_")
                if len(parts) >= 2:
                    model_part = parts[0][len(f"{group}_"):]
                    if model_part:
                        detected_models.add(model_part)
                        
    models = sorted(list(detected_models)) if detected_models else ["mts_raw", "vxv_vix_ema", "spy_sma200", "spy_sma63"]
    
    strategies_summary = {}
    curves_by_model = {}
    
    for model in models:
        name = f"{group}_{model}"
        sub_scenario_dir = os.path.join(OUTPUT_DIR, "scenario")
        
        # Check if run_0 folder exists for this group + model combination
        if not os.path.exists(os.path.join(sub_scenario_dir, f"{name}_run_0")):
            continue
            
        try:
            summary_data = get_scenario_summary(name)
            equity_points = get_scenario_equity(name)
            
            # Resolve fields from Pydantic model (response from get_scenario_summary)
            strategies_summary[model] = {
                "final_capital": getattr(summary_data, 'final_capital', 100000.0),
                "cagr": getattr(summary_data, 'cagr', 0.0) * 100.0, # convert decimal to percent (e.g. 0.31 -> 31.0)
                "max_drawdown": getattr(summary_data, 'max_drawdown', 0.0) * 100.0, # convert decimal to percent (e.g. -0.28 -> -28.0)
                "win_rate": getattr(summary_data, 'win_rate', 0.0),
                "total_trades": getattr(summary_data, 'total_trades', 0),
                "profit_factor": getattr(summary_data, 'profit_factor', 0.0),
            }
            
            curves_by_model[model] = {p.date: p for p in equity_points}
        except Exception as e:
            print(f"Error loading group comparison for {name}: {e}")
            continue

    if not strategies_summary:
        raise HTTPException(status_code=404, detail=f"No Monte Carlo comparison data found for group '{group}'")

    # Merge equity curves by date
    all_dates = set()
    for model in curves_by_model:
        all_dates.update(curves_by_model[model].keys())
        
    sorted_dates = sorted(list(all_dates))
    merged_curves = []
    
    first_model = list(curves_by_model.keys())[0]
    
    for d in sorted_dates:
        row = {"date": d}
        
        pt_ref = curves_by_model[first_model].get(d)
        row["spy_equity"] = getattr(pt_ref, "spy_equity", 100000.0) or 100000.0
        
        for model in models:
            if model in curves_by_model:
                pt = curves_by_model[model].get(d)
                row[f"equity_{model}"] = getattr(pt, "equity", 100000.0) or 100000.0
            else:
                row[f"equity_{model}"] = 100000.0
                
        merged_curves.append(row)
        
    start_date = sorted_dates[0] if sorted_dates else ""
    end_date = sorted_dates[-1] if sorted_dates else ""

    return {
        "group": group,
        "start_date": start_date,
        "end_date": end_date,
        "strategies": strategies_summary,
        "equity_curves": merged_curves
    }
