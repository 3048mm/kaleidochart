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
import time
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
from backend.backtest.common_constraints import load_tax_rate

# Global variable inside each subprocess memory space to hold the preloaded data cache
_child_preloaded_data = None


def load_scenario_batch_jobs(toml_path: str) -> list:
    """data/scenario_batch_jobs.toml を読み込み、ジョブのリストを返す。"""
    if not os.path.exists(toml_path):
        return []
    with open(toml_path, "rb") as f:
        config = tomli.load(f)
    return config.get("job", [])


def filter_jobs_by_names(jobs: list, names) -> list:
    """`--jobs` 指定でジョブを絞り込む（`jobs` 内の元の順序を維持）。

    `names` が None/空なら全件そのまま返す。指定された名前が1件でも
    `jobs` に存在しなければ、黙って無視せず例外にする
    （タイポで「絞り込んだつもりが実は全件スキップ」になる事故を防ぐ）。
    """
    if not names:
        return jobs
    job_names = {j["name"] for j in jobs}
    unknown = [n for n in names if n not in job_names]
    if unknown:
        raise ValueError(
            f"--jobs に存在しないジョブ名があります: {unknown}\n"
            f"  指定できるジョブ名: {sorted(job_names)}"
        )
    wanted = set(names)
    return [j for j in jobs if j["name"] in wanted]


# ポートフォリオ構成パラメータの既定値。
# ジョブが指定しなければこれが使われる（従来のハードコード値と同一なので、
# TOML を触らない限り挙動は変わらない）。
DEFAULT_PORTFOLIO = {
    "initial_capital": 100000.0,
    "max_positions": 8,
    "min_score": 1,
    "stop_loss_pct": -0.08,
    "profit_target_pct": 0.20,
}


def resolve_portfolio_params(job: dict) -> dict:
    """ジョブ定義からポートフォリオ構成パラメータを解決する。

    従来これらは `run_single_mc_scenario()` に全ジョブ共通のハードコードで渡されており、
    `scenario_batch_jobs.toml` はスクリーン条件（`override_params`）しか変えられなかった。
    そのため「戦略Xは保有数を絞った方が CAGR が伸びるか」のような
    **ポートフォリオ構成側の比較検証が構造的に不可能**だった。

    キー名の誤記はサイレントに無視されると「設定したのに効かない」事故になるため、
    未知のキーは例外にする（D-2/I-6 と同型のサイレント失敗を作らない）。
    """
    override = job.get("portfolio", {}) or {}
    unknown = set(override) - set(DEFAULT_PORTFOLIO)
    if unknown:
        raise ValueError(
            f"ジョブ '{job.get('name')}' の [job.portfolio] に未知のキーがあります: "
            f"{sorted(unknown)}\n"
            f"  指定できるキー: {sorted(DEFAULT_PORTFOLIO)}"
        )
    params = dict(DEFAULT_PORTFOLIO)
    params.update(override)
    return params


def get_best_params_from_db(db_path: str, study_name: str) -> dict:
    """Optuna DB から best trial のパラメータを取得する。"""
    storage_url = f"sqlite:///{db_path}"
    try:
        study = optuna.load_study(study_name=study_name, storage=storage_url)
        return study.best_trial.params
    except Exception as e:
        print(f"Error loading study '{study_name}': {e}")
        return None


def generate_preset_toml(strategy_name: str, best_params: dict, toml_path: str):
    """Optuna best params から screener_presets.toml 形式のファイルを生成する。"""
    toml_str = f'active_rise_ids = ["{strategy_name}_opt"]\nactive_fall_ids = []\n\n'
    toml_str += '[[rise]]\n'
    toml_str += f'id = "{strategy_name}_opt"\n'
    toml_str += f'name = "{strategy_name}_opt"\n'
    toml_str += f'subname = "Optuna Best for {strategy_name}"\n'
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
                           preset_toml_path: str, project_root: str,
                           portfolio: dict = None,
                           consider_tax: float = 0.0) -> dict:
    """
    単一のモンテカルロ実行を行う。サブプロセス内で呼ばれる。

    出力先: output/scenario/{strat}/{model}/run_{run_idx}/

    Args:
        portfolio: ポートフォリオ構成パラメータ（`resolve_portfolio_params()` の戻り値）。
                   省略時は既定値。
        consider_tax: 適用税率（率。0.2=20%）。並列 MC はサブプロセス（ProcessPoolExecutor）で
                      走るため、親プロセスで解決した値を明示的に引数として渡す必要がある
                      （子プロセスは親のメモリ空間を共有しない）。
    """
    portfolio = portfolio or dict(DEFAULT_PORTFOLIO)
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
            initial_capital=portfolio["initial_capital"],
            max_positions=portfolio["max_positions"],
            min_score=portfolio["min_score"],
            stop_loss_pct=portfolio["stop_loss_pct"],
            profit_target_pct=portfolio["profit_target_pct"],
            output_dir=run_output_dir,
            refresh_cache=False,
            config_path=config_rel_path,
            use_vxv_vix=True,
            monte_carlo_mode=True,
            monte_carlo_seed=run_idx,
            regime_model=model,
            preloaded_data=_child_preloaded_data,
            consider_tax=consider_tax
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


def find_stale_run_outputs(failures, project_root: str, batch_start: float) -> list:
    """失敗した run の出力先に「前回の結果」が残っていないか調べる。

    `run_single_mc_scenario` は例外時に出力を書かず None を返すだけなので、
    **前回バッチの出力ディレクトリがそのまま残る**。下流の集計はディレクトリの
    存在だけを見るため、古い結果が新しい結果に混ざったまま比較されてしまう。

    実例（2026-07-29）: 流動性ハード制約フィックス後の全戦略再実行で
    B2 の full_position/run_9 だけ他より1日以上古いタイムスタンプで取り残されていた。
    たまたま別のチェックで気づけたが、通常の指標比較では発見できない。

    Args:
        failures: (strat, model, run_idx) のリスト
        batch_start: バッチ開始時刻（time.time()）。これより古い出力を stale とみなす

    Returns:
        [(strat, model, run_idx, パス, 最終更新時刻)] — 汚染リスクのあるものだけ
    """
    stale = []
    for strat, model, run_idx in failures:
        d = os.path.join(project_root, "output", "scenario", strat, model, f"run_{run_idx}")
        if not os.path.isdir(d):
            continue  # 出力が無い＝集計に混ざらないので安全
        mtimes = [
            os.path.getmtime(os.path.join(d, f))
            for f in os.listdir(d)
            if os.path.isfile(os.path.join(d, f))
        ]
        if not mtimes:
            continue
        newest = max(mtimes)
        if newest < batch_start:
            stale.append((strat, model, run_idx, d, newest))
    return stale


def report_batch_failures(failures, num_runs: int, project_root: str, batch_start: float) -> bool:
    """バッチ全体の失敗をまとめて報告する。

    個々の失敗は実行中にも出るが、大量のログに埋もれる。**終了直前にまとめて出す**ことで
    見落としを防ぐ。戻り値は「安全に集計してよいか」。

    Returns:
        True なら問題なし。False なら失敗あり（呼び出し元は非ゼロ終了すべき）。
    """
    if not failures:
        print("\n全 run が成功しました（集計結果は最新です）。")
        return True

    print("\n" + "!" * 60)
    print(f"!! 失敗した run が {len(failures)} 件あります")
    print("!" * 60)
    by_job = {}
    for strat, model, run_idx in failures:
        by_job.setdefault((strat, model), []).append(run_idx)
    for (strat, model), idxs in sorted(by_job.items()):
        print(f"  {strat} / {model}: {len(idxs)}/{num_runs} 件失敗 (run_{sorted(idxs)})")

    stale = find_stale_run_outputs(failures, project_root, batch_start)
    if stale:
        print("\n  ** 前回の結果が残っており、集計に混ざる恐れがあります **")
        for strat, model, run_idx, path, mtime in stale:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            print(f"    {strat}/{model}/run_{run_idx}  最終更新 {ts}")
            print(f"      {path}")
        print("\n  → 集計・比較の前に、上記を削除して該当 run を再実行してください。")
    else:
        print("\n  失敗した run の出力は残っていません（古い結果が混ざる心配はありません）。")

    return False


def format_elapsed(seconds: float) -> str:
    """経過秒数を `H:MM:SS` / `M:SS` の読みやすい形式にする。"""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_args(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="個別銘柄シナリオテストの並列バッチ実行")
    parser.add_argument(
        "--jobs", type=str, default=None,
        help="実行するジョブ名をカンマ区切りで指定（例: --jobs B1,B2）。省略時は全ジョブ。",
    )
    parser.add_argument(
        "--list-jobs", action="store_true",
        help="実行せず、scenario_batch_jobs.toml に定義済みのジョブ名一覧だけを表示して終了する。",
    )
    args = parser.parse_args(argv)
    job_names = [n.strip() for n in args.jobs.split(",") if n.strip()] if args.jobs else None
    return job_names, args.list_jobs


def main():
    _s = os.path.dirname(os.path.abspath(__file__))
    project_root_here = os.path.dirname(os.path.dirname(_s))  # stocktool/
    db_path = os.path.join(project_root_here, "data", "optimization_trials.db")
    jobs_path = os.path.join(project_root_here, "data", "scenario_batch_jobs.toml")

    job_names, list_jobs_only = parse_args()

    all_jobs = load_scenario_batch_jobs(jobs_path)
    if not all_jobs:
        print(f"No jobs found in {jobs_path}. Exiting.")
        return

    if list_jobs_only:
        print(f"利用可能なジョブ名（{jobs_path}）:")
        for j in all_jobs:
            print(f"  {j['name']}  (strategy_code={j['strategy_code']}, source={j.get('source', 'optuna')})")
        return

    try:
        jobs = filter_jobs_by_names(all_jobs, job_names)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    models = ["full_position", "spy_sma200", "spy_sma63", "vxv_vix_ema", "mts_raw"]

    start_date = "2022-01-01"
    end_date = "2026-03-26"
    num_runs = 10
    max_workers = 2
    n_jobs = len(jobs)
    n_models = len(models)
    total_blocks = n_jobs * n_models  # 進捗表示・ETA 算出用の「job x model」単位数

    # 税率は backtest_config.toml [general] consider_tax から解決する
    # （jobs_path=scenario_batch_jobs.toml とは別ファイルなので混同しないこと）。
    tax_rate = load_tax_rate()

    print("=" * 60)
    print(f"Scenario Batch: {n_jobs} jobs x {n_models} models x {num_runs} MC runs")
    if job_names:
        print(f"  (--jobs 指定により {len(all_jobs)} 件中 {n_jobs} 件に絞り込み: {[j['name'] for j in jobs]})")
    print(f"Period: {start_date} to {end_date}")
    print(f"Jobs file: {jobs_path}")
    if tax_rate > 0.0:
        print(f"  [Tax] 適用税率: {tax_rate * 100:.1f}% (consider_tax={tax_rate})")
    else:
        print("  [Tax] 税なし (consider_tax=0.0)")
    print("=" * 60)

    # 失敗した run の出力先に「前回の結果」が残っているかを後で判定するための基準時刻
    batch_start = time.time()
    failures = []
    blocks_done = 0  # 完了した (job, model) の数。ETA 算出に使う

    for job_idx, job in enumerate(jobs, start=1):
        strat_name = job["name"]
        strategy_code = job["strategy_code"]
        source = job.get("source", "optuna")

        print(f"\n>>> [Job {job_idx}/{n_jobs}] {strat_name} (Base Strategy: {strategy_code}, Source: {source}) ...")

        # Load parameters
        best_params = {}
        if source == "optuna":
            study_name = job.get("study_name")
            if not study_name:
                print(f"  Skipping {strat_name}: 'study_name' is missing for optuna source.")
                blocks_done += n_models
                continue
            best_params = get_best_params_from_db(db_path, study_name)
        elif source == "manual":
            base_study_name = job.get("base_study_name")
            if base_study_name:
                best_params = get_best_params_from_db(db_path, base_study_name)
                if not best_params:
                    best_params = {}
            else:
                best_params = {}

        if source == "optuna" and not best_params:
            print(f"  Skipping {strat_name}: Optuna params not found in study '{job.get('study_name')}'")
            blocks_done += n_models
            continue

        # Apply manual parameter overrides
        override_params = job.get("override_params", {})
        if override_params:
            print(f"  Applying parameter overrides: {override_params}")
            best_params.update(override_params)

        # Preset TOML を tmp/ に書き出す（実行ごとに再生成）
        preset_toml_path = os.path.join(project_root_here, "tmp", f"preset_{strat_name}_opt.toml")
        generate_preset_toml(strategy_code, best_params, preset_toml_path)

        portfolio = resolve_portfolio_params(job)
        diff = {k: v for k, v in portfolio.items() if v != DEFAULT_PORTFOLIO[k]}
        if diff:
            print(f"  Portfolio overrides: {diff}")

        for model_idx, model in enumerate(models, start=1):
            block_start = time.time()
            elapsed = block_start - batch_start
            # ETA: 完了済みブロックの平均所要時間 x 残りブロック数（雑だが目安としては十分）
            eta_str = ""
            if blocks_done > 0:
                avg_per_block = elapsed / blocks_done
                remaining = total_blocks - blocks_done
                eta_str = f", ETA {format_elapsed(avg_per_block * remaining)}"
            print(
                f"  > [Model {model_idx}/{n_models}] {model} "
                f"(elapsed {format_elapsed(elapsed)}{eta_str}) ..."
            )
            strat_runs = []
            n_completed = 0

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        run_single_mc_scenario,
                        strat_name, model, run_idx,
                        start_date, end_date,
                        preset_toml_path, project_root_here,
                        portfolio,
                        tax_rate
                    ): run_idx
                    for run_idx in range(num_runs)
                }

                for future in as_completed(futures):
                    run_idx = futures[future]
                    n_completed += 1
                    try:
                        res = future.result()
                        if res:
                            strat_runs.append(res)
                            print(
                                f"      run_{run_idx} done ({n_completed}/{num_runs}) "
                                f"CAGR={res['cagr']:.1f}% DD={res['max_drawdown_pct']:.1f}% "
                                f"trades={res['total_trades']}",
                                flush=True,
                            )
                        else:
                            # run_single_mc_scenario は例外時に None を返す。
                            # ここで拾わないと「失敗した」という事実が残らない。
                            failures.append((strat_name, model, run_idx))
                            print(f"      run_{run_idx} FAILED ({n_completed}/{num_runs})", flush=True)
                    except Exception as fe:
                        print(f"  Future error for run_{run_idx}: {fe}")
                        failures.append((strat_name, model, run_idx))

            blocks_done += 1

            if strat_runs:
                df_runs = pd.DataFrame(strat_runs)
                print(
                    f"    Done ({len(strat_runs)}/{num_runs} runs, {format_elapsed(time.time() - block_start)}). "
                    f"Return (Avg): {df_runs['total_return_pct'].mean():.2f}% | "
                    f"MaxDD (Avg): {df_runs['max_drawdown_pct'].mean():.2f}% | "
                    f"CAGR (Avg): {df_runs['cagr'].mean():.2f}%"
                )
                if len(strat_runs) < num_runs:
                    # 平均値は成功分だけで算出されるため、件数を見ないと
                    # 「少ないサンプルの平均」を正常値と誤読する
                    print(
                        f"    [WARNING] {num_runs - len(strat_runs)} 件失敗しています。"
                        f"上記の平均は成功した {len(strat_runs)} 件のみで算出されています。"
                    )
            else:
                print(f"    No successful runs for {strat_name}/{model}.")

    print("\n" + "=" * 60)
    print("ALL JOBS & REGIMES COMPLETE")
    print("=" * 60)

    ok = report_batch_failures(failures, num_runs, project_root_here, batch_start)
    if not ok:
        # 非ゼロ終了にして、バッチを回す側（人間・スケジューラ）が気づけるようにする
        sys.exit(1)


if __name__ == '__main__':
    main()
