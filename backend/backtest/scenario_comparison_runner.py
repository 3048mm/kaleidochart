import os
import json
import pandas as pd
from typing import Dict, Any, List
import datetime

from backend.backtest.scenario_runner import run_scenario_test
from backend.db import database
from backend.backtest.backtest_runner import preload_data

def run_comparison(
    start_date: str,
    end_date: str,
    output_dir: str = "output/scenario_comparison",
    initial_capital: float = 100000.0,
    max_positions: int = 8,
    min_score: int = 2,
    stop_loss_pct: float = -0.08,
    profit_target_pct: float = 0.20,
    refresh_cache: bool = False,
    config_path: str = "data/screener_presets.toml",
    use_vxv_vix: bool = False
):
    """
    Runs the 4 market regime models (mts_raw, vxv_vix_ema, spy_sma200, spy_sma63)
    concurrently (sequentially in loop) and merges their summaries and equity curves
    for side-by-side comparison.
    """
    models = ['mts_raw', 'vxv_vix_ema', 'spy_sma200', 'spy_sma63', 'full_position']
    
    os.makedirs(output_dir, exist_ok=True)
    
    results = {}
    equity_curves = {}
    
    # 1. Run each scenario test
    for model in models:
        model_output_dir = os.path.join(output_dir, model)
        print(f"Running scenario test for regime model: {model}...")
        
        # Run test
        run_scenario_test(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            max_positions=max_positions,
            min_score=min_score,
            stop_loss_pct=stop_loss_pct,
            profit_target_pct=profit_target_pct,
            output_dir=model_output_dir,
            refresh_cache=refresh_cache,
            config_path=config_path,
            use_vxv_vix=use_vxv_vix,
            regime_model=model
        )
        
        # Load the summary
        summary_path = os.path.join(model_output_dir, "scenario_summary.json")
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary_data = json.load(f)
            
        # Calculate CAGR
        d_start = pd.to_datetime(start_date)
        d_end = pd.to_datetime(end_date)
        days = (d_end - d_start).days
        years = days / 365.25 if days > 0 else 1.0
        
        final_cap = summary_data.get("final_capital", initial_capital)
        cagr_val = round(((final_cap / initial_capital) ** (1.0 / years) - 1.0) * 100.0, 2)
        
        # Resolve max drawdown
        dd_data = summary_data.get("max_drawdown", {})
        dd_pct = dd_data.get("pct", 0.0) if isinstance(dd_data, dict) else dd_data
        
        results[model] = {
            "final_capital": final_cap,
            "cagr": cagr_val,
            "max_drawdown": dd_pct,
            "win_rate": summary_data.get("win_rate", 0.0),
            "total_trades": summary_data.get("total_trades", 0),
            "profit_factor": summary_data.get("profit_factor", 0.0)
        }
        
        # Load equity curve CSV
        equity_csv_path = os.path.join(model_output_dir, "scenario_equity_curve.csv")
        if os.path.exists(equity_csv_path):
            df_eq = pd.read_csv(equity_csv_path)
            if not df_eq.empty and 'date' in df_eq.columns and 'total_equity' in df_eq.columns:
                df_eq['date'] = pd.to_datetime(df_eq['date']).dt.strftime('%Y-%m-%d')
                equity_curves[model] = df_eq[['date', 'total_equity']].rename(columns={'total_equity': f'equity_{model}'})

    # 2. Get SPY data for benchmark curve
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app_config_path = os.path.join(project_root, "config.toml")
    db_path = os.path.join(project_root, "data/stocktool.db")
    try:
        import tomli
        if os.path.exists(app_config_path):
            with open(app_config_path, "rb") as f:
                app_config = tomli.load(f)
            config_db_path = app_config.get("system", {}).get("db_path", "data/stocktool.db")
            if not os.path.isabs(config_db_path):
                db_path = os.path.join(project_root, config_db_path)
    except Exception as e:
        print(f"Warning: Failed to load config.toml: {e}")
        
    database.init_db(db_path)
    db = database.SessionLocal()
    spy_df = pd.DataFrame()
    try:
        engine = db.get_bind()
        df_symbols, df_prices, _, _, _, _ = preload_data(engine, start_date, end_date, refresh_cache)
        spy_id = df_symbols[df_symbols['ticker'] == 'SPY']['id'].values
        if len(spy_id) > 0:
            spy_df = df_prices[df_prices['symbol_id'] == spy_id[0]].copy()
    finally:
        db.close()
        
    if not spy_df.empty:
        spy_df = spy_df.sort_values('date')
        spy_df['date'] = pd.to_datetime(spy_df['date']).dt.strftime('%Y-%m-%d')
        spy_p0 = spy_df.iloc[0]['close']
        spy_df['spy_equity'] = initial_capital * (spy_df['close'] / spy_p0)
        spy_equity_df = spy_df[['date', 'spy_equity']]
    else:
        spy_equity_df = pd.DataFrame(columns=['date', 'spy_equity'])

    # 3. Merge all curves
    merged_df = spy_equity_df
    for model in models:
        if model in equity_curves:
            if merged_df.empty:
                merged_df = equity_curves[model]
            else:
                merged_df = pd.merge(merged_df, equity_curves[model], on='date', how='outer')
                
    if not merged_df.empty:
        merged_df = merged_df.sort_values('date').reset_index(drop=True)
        cols_to_fill = [col for col in merged_df.columns if col != 'date']
        merged_df[cols_to_fill] = merged_df[cols_to_fill].ffill().fillna(initial_capital)
        
    # Ensure all required columns are present
    required_cols = ['date', 'spy_equity', 'equity_mts_raw', 'equity_vxv_vix_ema', 'equity_spy_sma200', 'equity_spy_sma63', 'equity_full_position']
    for col in required_cols:
        if col not in merged_df.columns:
            merged_df[col] = initial_capital
            
    merged_df = merged_df[required_cols]
    
    # Save CSV
    equity_curve_path = os.path.join(output_dir, "comparison_equity_curve.csv")
    merged_df.to_csv(equity_curve_path, index=False)
    
    # Save comparison summary JSON
    comparison_summary = {
        "start_date": start_date,
        "end_date": end_date,
        "strategies": results
    }
    summary_json_path = os.path.join(output_dir, "comparison_summary.json")
    with open(summary_json_path, 'w', encoding='utf-8') as f:
        json.dump(comparison_summary, f, indent=4, ensure_ascii=False)
        
    print(f"Comparison runner successfully completed. Outputs saved to {output_dir}")
