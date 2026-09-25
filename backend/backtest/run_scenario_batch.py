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


def generate_preset_toml(strategies: list, toml_path: str):
    """Optuna best params から screener_presets.toml 形式のファイルを生成する。

    Args:
        strategies: `[(strategy_name, best_params), ...]` のリスト。
                    複数指定すると `[[rise]]` ブロックを戦略の数だけ生成する
                    （和集合で複数戦略を組み合わせたシナリオテスト用）。
                    単一戦略でも要素数1のリストで渡すこと。
        toml_path: 出力先パス。

    後方互換: 単一戦略（要素数1）で呼び出した場合、出力される TOML は
    このリスト対応版になる前とバイト単位で同一になる（回帰テストで担保）。
    """
    ids_str = ", ".join(f'"{name}_opt"' for name, _ in strategies)
    toml_str = f'active_rise_ids = [{ids_str}]\nactive_fall_ids = []\n\n'

    blocks = []
    for strategy_name, best_params in strategies:
        block = '[[rise]]\n'
        block += f'id = "{strategy_name}_opt"\n'
        block += f'name = "{strategy_name}_opt"\n'
        block += f'subname = "Optuna Best for {strategy_name}"\n'
        # group は必ず "Pickup" にすること。戦略名は
        # f"{section.capitalize()} - {group} - {name}" で組まれ、
        # SCENARIO_TARGET_PREFIX = 'Rise - Pickup' がスキャン対象を決めるため、
        # ここがずれるとその戦略が黙ってスキャンされなくなる
        # （2026-09-03 の Pickup/Check/Common 再編で "Check" から変更）。
        block += 'group = "Pickup"\n'
        block += 'use_vxv_vix_hysteresis = true\n'
        block += 'vxv_vix_hysteresis_type = "vxv_vix_ema"\n'
        block += '\n[rise.filters]\n'

        for k, v in best_params.items():
            if isinstance(v, bool):
                toml_val = "true" if v else "false"
            elif isinstance(v, str):
                toml_val = f'"{v}"'
            else:
                toml_val = v
            block += f"{k} = {toml_val}\n"

        blocks.append(block)

    # ブロック間は空行区切り（単一ブロックのときは join が何も足さないため、
    # 従来出力とバイト単位で一致する）。
    toml_str += "\n".join(blocks)

    os.makedirs(os.path.dirname(toml_path), exist_ok=True)
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write(toml_str)


def resolve_job_strategy_specs(job: dict):
    """ジョブ定義から `(strategy_codes, study_names, is_multi)` を解決する。

    単数形 (`strategy_code` / `study_name`) と複数形 (`strategy_codes` / `study_names`)
    の両方を受け付けるが、**併記や長さ不一致はエラー**にする
    （タイポで意図と違う組み合わせが黙って走る事故を防ぐ）。

    Returns:
        strategy_codes: list[str]
        study_names: list[str | None]
        is_multi: bool — True なら複数戦略ジョブ。呼び出し側は study が1つでも
                  欠けた場合に即エラー終了しなければならない
                  （単一戦略ジョブの「Skipping して continue」とは扱いが異なる）。
    """
    name = job.get("name")
    has_single = "strategy_code" in job
    has_multi = "strategy_codes" in job

    if has_single and has_multi:
        raise ValueError(
            f"ジョブ '{name}': strategy_code と strategy_codes は同時に指定できません"
        )
    if not has_single and not has_multi:
        raise ValueError(f"ジョブ '{name}': strategy_code (または strategy_codes) が必要です")

    if has_multi:
        codes = list(job["strategy_codes"])
        studies = list(job.get("study_names", [None] * len(codes)))
        if len(codes) != len(studies):
            raise ValueError(
                f"ジョブ '{name}': strategy_codes ({len(codes)}件) と "
                f"study_names ({len(studies)}件) の長さが一致しません"
            )
        return codes, studies, True

    return [job["strategy_code"]], [job.get("study_name")], False


def validate_jobs_studies(jobs: list[dict], db_path: str) -> None:
    """全ジョブの定義および必要な Optuna study の存在・整合性を一括検証する。

    バッチ実行のループに入る前にすべてのジョブを検査し、1つでも study の欠落や
    不正な設定があれば、すべてのエラーをまとめて報告して ValueError を送出する。
    これにより、長時間のバッチ処理が途中のジョブで突然失敗したり、
    存在しない study がサイレントにスキップされる事態を防ぐ。

    Args:
        jobs: 実行対象のジョブのリスト。
        db_path: Optuna の SQLite DB のパス。

    Raises:
        ValueError: いずれかのジョブで study が見つからない、または設定不正がある場合。
    """
    if not jobs:
        return

    errors: list[str] = []

    # Optuna DB の存在が必要かどうか
    needs_optuna_db = any(
        job.get("source", "optuna") == "optuna"
        or (job.get("source") == "manual" and job.get("base_study_name"))
        for job in jobs
    )

    if needs_optuna_db and not os.path.exists(db_path):
        raise ValueError(
            f"シナリオバッチ開始前検証エラー: Optuna DB ファイルが存在しません: {db_path}"
        )

    storage_url = f"sqlite:///{db_path}"

    for job in jobs:
        job_name = job.get("name", "<unnamed>")
        source = job.get("source", "optuna")

        if source not in ("optuna", "manual"):
            errors.append(
                f"ジョブ '{job_name}': 未知の source '{source}' です "
                f"('optuna' または 'manual' を指定してください)"
            )
            continue

        try:
            strategy_codes, study_names, is_multi = resolve_job_strategy_specs(job)
        except ValueError as e:
            errors.append(str(e))
            continue

        if source == "optuna":
            for strategy_code, study_name in zip(strategy_codes, study_names):
                if not study_name:
                    errors.append(
                        f"ジョブ '{job_name}' (strategy={strategy_code}): "
                        f"'study_name' が指定されていません"
                    )
                    continue

                try:
                    study = optuna.load_study(study_name=study_name, storage=storage_url)
                    _ = study.best_trial.params
                except KeyError:
                    errors.append(
                        f"ジョブ '{job_name}' (strategy={strategy_code}): "
                        f"Optuna study '{study_name}' が DB に存在しません"
                    )
                except ValueError as ve:
                    errors.append(
                        f"ジョブ '{job_name}' (strategy={strategy_code}): "
                        f"Optuna study '{study_name}' に完了したトライアルがありません ({ve})"
                    )
                except Exception as ex:
                    errors.append(
                        f"ジョブ '{job_name}' (strategy={strategy_code}): "
                        f"Optuna study '{study_name}' の読み込みに失敗しました: {ex}"
                    )

        elif source == "manual":
            base_study_name = job.get("base_study_name")
            if base_study_name:
                try:
                    study = optuna.load_study(study_name=base_study_name, storage=storage_url)
                    _ = study.best_trial.params
                except KeyError:
                    errors.append(
                        f"ジョブ '{job_name}': base_study_name で指定された "
                        f"Optuna study '{base_study_name}' が DB に存在しません"
                    )
                except ValueError as ve:
                    errors.append(
                        f"ジョブ '{job_name}': base_study_name で指定された "
                        f"Optuna study '{base_study_name}' に完了したトライアルがありません ({ve})"
                    )
                except Exception as ex:
                    errors.append(
                        f"ジョブ '{job_name}': base_study_name で指定された "
                        f"Optuna study '{base_study_name}' の読み込みに失敗しました: {ex}"
                    )

    if errors:
        msg = (
            f"シナリオバッチ開始前検証エラー: {len(errors)} 件の問題が見つかりました。\n"
            + "\n".join(f"  - {err}" for err in errors)
        )
        raise ValueError(msg)


def run_single_mc_scenario(strat: str, model: str, run_idx: int,
                           start_date: str, end_date: str,
                           preset_toml_path: str, project_root: str,
                           portfolio: dict = None,
                           consider_tax: float = 0.0,
                           master_files: dict = None) -> dict:
    """
    単一のモンテカルロ実行を行う。サブプロセス内で呼ばれる。

    出力先: output/scenario/{strat}/{model}/run_{run_idx}/

    Args:
        portfolio: ポートフォリオ構成パラメータ（`resolve_portfolio_params()` の戻り値）。
                   省略時は既定値。
        consider_tax: 適用税率（率。0.2=20%）。並列 MC はサブプロセス（ProcessPoolExecutor）で
                      走るため、親プロセスで解決した値を明示的に引数として渡す必要がある
                      （子プロセスは親のメモリ空間を共有しない）。
        master_files: 親プロセスが開始時に1回だけ解決した Parquet マスタのファイル辞書
                      （backtest_stable_data_plan.md §3-C）。子プロセスはこれを渡された
                      とおりに使い、探索・ポインタ読みを一切しない
                      （実行中に daily update が走っても run ごとに世代がずれないようにする）。
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
            _child_preloaded_data = preload_data(None, start_date, end_date, refresh_cache=False,
                                                  master_files=master_files)
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
    parser.add_argument(
        "--tax", type=float, default=None,
        help="適用税率（率。0.2=20%%）。省略時は backtest_config.toml [general] "
             "consider_tax を使う。税ありと税なしを比較したいときに、"
             "本番設定を書き換えずに切り替えるためのもの。",
    )
    parser.add_argument(
        "--data-source", type=str, default="backup",
        help="シナリオバッチが読む Parquet マスタの参照先。"
             "'backup'（既定・検証済みの最新バックアップ）/ 'production'（本番の最新世代）"
             "/ バックアップのフォルダ名（例 '_bk_20260925_...')",
    )
    args = parser.parse_args(argv)
    job_names = [n.strip() for n in args.jobs.split(",") if n.strip()] if args.jobs else None
    return job_names, args.list_jobs, args.tax, args.data_source


def resolve_tax_rate(tax_override):
    """適用税率を決める。`--tax` が優先、無ければ設定ファイル。

    単位検証（率で指定。`> 1.0` はエラー）は `load_tax_rate()` に集約しているので、
    CLI 値もそこへ通す。**元のバグは `consider_tax = 20.0`（＝2000%）という
    単位の取り違えで、指定しても結果が1円も変わらなかった**というものだった。
    CLI だけ検証を素通りさせると同じ穴が開く。

    Args:
        tax_override: `--tax` の値。`None` なら未指定。

    Note:
        判定は `is not None` で行うこと。`if tax_override:` にすると
        **`--tax 0.0`（税なし）が「未指定」に化けて設定値が使われる**。
        税あり・税なしの比較実測がこの挙動に依存している。
    """
    if tax_override is not None:
        return load_tax_rate({"general": {"consider_tax": tax_override}})
    return load_tax_rate()


def main(argv=None, db_path_override: str = None, jobs_path_override: str = None):
    _s = os.path.dirname(os.path.abspath(__file__))
    project_root_here = os.path.dirname(os.path.dirname(_s))  # stocktool/
    db_path = db_path_override or os.path.join(project_root_here, "data", "optimization_trials.db")
    jobs_path = jobs_path_override or os.path.join(project_root_here, "data", "scenario_batch_jobs.toml")

    job_names, list_jobs_only, tax_override, data_source = parse_args(argv)

    all_jobs = load_scenario_batch_jobs(jobs_path)
    if not all_jobs:
        print(f"No jobs found in {jobs_path}. Exiting.")
        return

    if list_jobs_only:
        print(f"利用可能なジョブ名（{jobs_path}）:")
        for j in all_jobs:
            codes, _studies, _is_multi = resolve_job_strategy_specs(j)
            codes_display = codes[0] if len(codes) == 1 else codes
            print(f"  {j['name']}  (strategy_code={codes_display}, source={j.get('source', 'optuna')})")
        return

    try:
        jobs = filter_jobs_by_names(all_jobs, job_names)
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # バッチ実行前に全ジョブの定義・Optuna study の存在を一括検証する
    try:
        validate_jobs_studies(jobs, db_path)
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

    # 税率は --tax があればそれ、無ければ backtest_config.toml [general] consider_tax
    # （jobs_path=scenario_batch_jobs.toml とは別ファイルなので混同しないこと）。
    tax_rate = resolve_tax_rate(tax_override)

    # 参照先は親プロセスで1回だけ解決し、子プロセス（ProcessPoolExecutor）へファイル辞書
    # として渡す（backtest_stable_data_plan.md §3-C）。子は探索もポインタ読みもしない。
    # 実行中に daily update が走っても、全 run が同じ Parquet 世代を読むことを保証する。
    from backend.pipeline.parquet_cache_manager import resolve_backtest_data_source
    resolved_master_files, data_source_meta = resolve_backtest_data_source(data_source)
    print(f"Data source: {data_source_meta['data_source']}"
          + (f" (backup: {data_source_meta['backup_name']})" if data_source_meta['backup_name'] else "")
          + f" | generation={data_source_meta['parquet_generation']}")

    print("=" * 60)
    print(f"Scenario Batch: {n_jobs} jobs x {n_models} models x {num_runs} MC runs")
    if job_names:
        print(f"  (--jobs 指定により {len(all_jobs)} 件中 {n_jobs} 件に絞り込み: {[j['name'] for j in jobs]})")
    print(f"Period: {start_date} to {end_date}")
    print(f"Jobs file: {jobs_path}")
    tax_src = "--tax" if tax_override is not None else "backtest_config.toml"
    if tax_rate > 0.0:
        print(f"  [Tax] 適用税率: {tax_rate * 100:.1f}% "
              f"(consider_tax={tax_rate}, 出どころ={tax_src})")
    else:
        print(f"  [Tax] 税なし (consider_tax=0.0, 出どころ={tax_src})")
    print("=" * 60)

    # 失敗した run の出力先に「前回の結果」が残っているかを後で判定するための基準時刻
    batch_start = time.time()
    failures = []
    blocks_done = 0  # 完了した (job, model) の数。ETA 算出に使う

    for job_idx, job in enumerate(jobs, start=1):
        strat_name = job["name"]
        source = job.get("source", "optuna")

        try:
            strategy_codes, study_names, is_multi = resolve_job_strategy_specs(job)
        except ValueError as e:
            # 複数 study のうち1つでも欠けたら（＝定義自体が壊れていたら）バッチ全体を止める。
            # 一部だけ読めた状態で走らせると意図と違う組み合わせになるため、
            # ここは Skip-continue にしない。
            print(f"[ERROR] {e}")
            sys.exit(1)

        codes_display = ", ".join(strategy_codes)
        print(f"\n>>> [Job {job_idx}/{n_jobs}] {strat_name} (Base Strategy: {codes_display}, Source: {source}) ...")

        # Load parameters（戦略ごとに1つずつ）
        strategies = []  # [(strategy_code, best_params), ...] -> generate_preset_toml に渡す
        for strategy_code, study_name in zip(strategy_codes, study_names):
            best_params = {}
            if source == "optuna":
                if not study_name:
                    print(
                        f"[ERROR] ジョブ '{strat_name}': 'study_name' が指定されていません "
                        f"(strategy={strategy_code})"
                    )
                    sys.exit(1)
                best_params = get_best_params_from_db(db_path, study_name)
                if not best_params:
                    print(
                        f"[ERROR] ジョブ '{strat_name}': Optuna params not found in study "
                        f"'{study_name}' (strategy={strategy_code})"
                    )
                    sys.exit(1)
            elif source == "manual":
                base_study_name = job.get("base_study_name")
                if base_study_name:
                    best_params = get_best_params_from_db(db_path, base_study_name)
                    if not best_params:
                        print(
                            f"[ERROR] ジョブ '{strat_name}': Optuna params not found in base_study "
                            f"'{base_study_name}'"
                        )
                        sys.exit(1)
                else:
                    best_params = {}
            strategies.append((strategy_code, best_params))

        # Apply manual parameter overrides（全戦略に同じ上書きを適用）
        override_params = job.get("override_params", {})
        if override_params:
            print(f"  Applying parameter overrides: {override_params}")
            for _, best_params in strategies:
                best_params.update(override_params)

        # Preset TOML を tmp/ に書き出す（実行ごとに再生成）
        preset_toml_path = os.path.join(project_root_here, "tmp", f"preset_{strat_name}_opt.toml")
        generate_preset_toml(strategies, preset_toml_path)

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
                        tax_rate,
                        resolved_master_files
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
                # CAGR 幾何平均・中央値・相加平均
                cagr_factors = np.maximum(1.0 + df_runs['cagr'] / 100.0, 1e-4)
                cagr_geo = (np.exp(np.mean(np.log(cagr_factors))) - 1.0) * 100.0
                cagr_med = df_runs['cagr'].median()
                cagr_avg = df_runs['cagr'].mean()
                print(
                    f"    Done ({len(strat_runs)}/{num_runs} runs, {format_elapsed(time.time() - block_start)}). "
                    f"Return (Avg): {df_runs['total_return_pct'].mean():.2f}% | "
                    f"MaxDD (Avg): {df_runs['max_drawdown_pct'].mean():.2f}% | "
                    f"CAGR (Geo): {cagr_geo:.2f}% (Med: {cagr_med:.2f}%, Avg: {cagr_avg:.2f}%)"
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
