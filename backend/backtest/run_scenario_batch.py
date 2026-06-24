"""
シナリオバッチ並列実行スクリプト（3階層出力対応版）

出力フォルダ構造:
  output/scenario/{strategy}/{model}/run_{idx}/
      scenario_summary.json
      scenario_equity_curve.csv
      scenario_progress.json
      ...

使用方法:
  python backend/backtest/run_scenario_batch.py
"""
import os
import sys
import optuna
import tomli
import pandas as pd
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure backend is on path (script is in backend/backtest/)
_script_dir = os.path.dirname(os.path.abspath(__file__))   # stocktool/backend/backtest
_backend_dir = os.path.dirname(_script_dir)                # stocktool/backend
_project_root = os.path.dirname(_backend_dir)              # stocktool

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from backend.backtest.scenario_runner import run_scenario_test
from backend.backtest.backtest_runner import preload_data

# Global variable inside each subprocess memory space to hold the preloaded data cache
_child_preloaded_data = None


def get_best_params_from_db(db_path: str, strategy_code: str) -> dict:
    """Optuna DB から best trial のパラメータを取得する。"""
    storage_url = f"sqlite:///{db_path}"
    study_name = f"opt_strategy_{strategy_code}_multi_period"
    try:
        study = optuna.load_study(study_name=study_name, storage=storage_url)
        return study.best_trial.params
    except Exception as e:
        print(f"Error loading study for {strategy_code}: {e}")
        return None


def generate_preset_toml(strategy_code: str, best_params: dict, toml_path: str):
    """Optuna best params から screener_presets.toml 形式のファイルを生成する。"""
    toml_str = f'active_rise_ids = ["{strategy_code}_opt"]\nactive_fall_ids = []\n\n'
    toml_str += '[[rise]]\n'
    toml_str += f'id = "{strategy_code}_opt"\n'
    toml_str += f'name = "{strategy_code}_opt"\n'
    toml_str += f'subname = "Optuna Best for {strategy_code}"\n'
    toml_str += 'group = "Check"\n'
    toml_str += 'use_vxv_vix_hysteresis = true\n'
    toml_str += 'vxv_vix_hysteresis_type = "vxv_vix_ema"\n'
    toml_str += '\n[rise.filters]\n'

    for k, v in best_params.items():
        if isinstance(v, bool):
            toml_val = "true" if v else "false"
        elif isinstance(v, str):
            toml_val = f'"{v}"'
        else:
            toml_val = v
        toml_str += f"{k} = {toml_val}\n"

    os.makedirs(os.path.dirname(toml_path), exist_ok=True)
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write(toml_str)


def run_single_mc_scenario(strat: str, model: str, run_idx: int,
                           start_date: str, end_date: str,
                           preset_toml_path: str, project_root: str) -> dict:
    """
    単一のモンテカルロ実行を行う。サブプロセス内で呼ばれる。

    出力先: output/scenario/{strat}/{model}/run_{run_idx}/
    """
    global _child_preloaded_data

    # 3階層の出力ディレクトリ
    run_output_dir = os.path.join(
        project_root, "output", "scenario", strat, model, f"run_{run_idx}"
    )
    config_rel_path = os.path.relpath(preset_toml_path, project_root)

    # サブプロセスごとに1回だけ Parquet データを読み込む
    if _child_preloaded_data is None:
        print(f"Subprocess preloading Parquet data for {strat}/{model}/run_{run_idx}...", flush=True)
        try:
            _child_preloaded_data = preload_data(None, start_date, end_date, refresh_cache=False)
            print("Subprocess local preloading complete.", flush=True)
        except Exception as pe:
            print(f"Preload failed in subprocess: {pe}")
            import traceback
            traceback.print_exc()
            return None

    try:
        res = run_scenario_test(
            start_date=start_date,
            end_date=end_date,
            initial_capital=100000.0,
            max_positions=8,
            min_score=1,
            stop_loss_pct=-0.08,
            profit_target_pct=0.20,
            output_dir=run_output_dir,
            refresh_cache=False,
            config_path=config_rel_path,
            use_vxv_vix=True,
            monte_carlo_mode=True,
            monte_carlo_seed=run_idx,
            regime_model=model,
            preloaded_data=_child_preloaded_data
        )
        summary = res['summary']
        return {
            'run_idx': run_idx,
            'final_capital': summary.get('final_capital', 100000.0),
            'total_return_pct': summary.get('total_return_pct', 0.0),
            'cagr': summary.get('cagr', 0.0),
            'max_drawdown_pct': (
                summary.get('max_drawdown', {}).get('pct', 0.0)
                if isinstance(summary.get('max_drawdown'), dict)
                else summary.get('max_drawdown', 0.0)
            ),
            'win_rate': summary.get('win_rate', 0.0),
            'total_trades': summary.get('total_trades', 0),
            'profit_factor': summary.get('profit_factor', 0.0)
        }
    except Exception as e:
        print(f"Error in {strat}/{model}/run_{run_idx}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    _s = os.path.dirname(os.path.abspath(__file__))
    project_root_here = os.path.dirname(os.path.dirname(_s))  # stocktool/
    db_path = os.path.join(project_root_here, "data", "optimization_trials.db")

    strategies = ["A", "B1", "B2", "B3", "B4", "E2"]
    models = ["mts_raw", "vxv_vix_ema", "spy_sma200", "spy_sma63"]

    start_date = "2022-01-01"
    end_date = "2026-03-26"
    num_runs = 10
    max_workers = 3

    print("=" * 60)
    print("Scenario Batch: 6 strategies × 4 models × 10 MC runs")
    print(f"Period: {start_date} to {end_date}")
    print(f"Output: output/scenario/{{strategy}}/{{model}}/run_{{N}}/")
    print("=" * 60)

    for strat in strategies:
        print(f"\n>>> Strategy: {strat} ...")
        best_params = get_best_params_from_db(db_path, strat)
        if not best_params:
            print(f"  Skipping {strat}: Optuna params not found.")
            continue

        # Preset TOML を tmp/ に書き出す（実行ごとに再生成）
        preset_toml_path = os.path.join(project_root_here, "tmp", f"preset_{strat}_opt.toml")
        generate_preset_toml(strat, best_params, preset_toml_path)

        for model in models:
            print(f"  > Regime Model: {model} ...")
            strat_runs = []

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        run_single_mc_scenario,
                        strat, model, run_idx,
                        start_date, end_date,
                        preset_toml_path, project_root_here
                    ): run_idx
                    for run_idx in range(num_runs)
                }

                for future in as_completed(futures):
                    run_idx = futures[future]
                    try:
                        res = future.result()
                        if res:
                            strat_runs.append(res)
                    except Exception as fe:
                        print(f"  Future error for run_{run_idx}: {fe}")

            if strat_runs:
                df_runs = pd.DataFrame(strat_runs)
                print(
                    f"    Done ({len(strat_runs)}/{num_runs} runs). "
                    f"Return (Avg): {df_runs['total_return_pct'].mean():.2f}% | "
                    f"MaxDD (Avg): {df_runs['max_drawdown_pct'].mean():.2f}% | "
                    f"CAGR (Avg): {df_runs['cagr'].mean()*100:.2f}%"
                )
            else:
                print(f"    No successful runs for {strat}/{model}.")

    print("\n" + "=" * 60)
    print("ALL STRATEGIES & REGIMES COMPLETE")
    print("=" * 60)


if __name__ == '__main__':
    main()
