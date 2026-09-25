"""
optimization_runner.py — Optuna-based Hyperparameter Optimization for Backtesting
"""
import os
import sys
import argparse
import subprocess
import sqlite3
import tomli
from datetime import date as dt_date, datetime
import pandas as pd

import optuna
import logging

# Add backend directory to path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
backtest_dir = os.path.join(backend_dir, 'backtest')
if backtest_dir not in sys.path:
    sys.path.insert(0, backtest_dir)

from backtest.backtest_runner import preload_data, run_single_strategy
from backtest.backtest_simulator import ExitRules
from backtest.common_constraints import load_min_avg_dollar_volume_21, inject_liquidity_floor, load_tax_rate
from db.database import init_db
from db import database

# Configure optuna logging
optuna.logging.set_verbosity(optuna.logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary for cached data per period
_cached_data_dict = {}

# 開始時に1回だけ解決した Parquet マスタの参照先（backtest_stable_data_plan.md §3-C）。
# 全期間で同じ世代を使うため、get_cached_data はこの値を使い回す（期間ごとに
# 探索・ポインタ読みをやり直さない）。main() の起動時に resolve_data_source() で設定する。
_resolved_master_files = None
_resolved_data_source_meta = None


def resolve_data_source(data_source: str | None = None, active_db_path: str | None = None):
    """開始時に1回だけ参照先を解決し、以降の get_cached_data 呼び出しに固定する。

    Args:
        data_source: ``"backup"`` / ``"latest"`` / バックアップのフォルダ名。
            ``None``（既定）なら `paths.is_production()` で判定する
            （本番なら backup、そうでなければ latest。backtest_stable_data_plan.md §7-3）。
        active_db_path: ``"latest"`` の参照先解決に使う DB パス（現在の data ディレクトリ）。
    """
    global _resolved_master_files, _resolved_data_source_meta
    from pipeline.parquet_cache_manager import resolve_backtest_data_source

    master_files, meta = resolve_backtest_data_source(data_source, active_db_path=active_db_path)
    print(f"Data source: {meta['data_source']}"
          + (f" (backup: {meta['backup_name']})" if meta['backup_name'] else "")
          + f" | generation={meta['parquet_generation']}", flush=True)
    _resolved_master_files = master_files
    _resolved_data_source_meta = meta
    return master_files, meta


def _backup_sqlite_file(db_path: str) -> str:
    """`db_path` の SQLite ファイルを、sqlite3 の backup API で安全にコピーする。

    退避（study の delete_study）の前に必ず呼ぶ（rules §10.1: ユーザー資産の
    ファイルバックアップ）。単純な `shutil.copy2` は WAL モード中の書き込みと
    競合すると不整合なコピーになりうるため、SQLite 公式の backup API を使う。
    """
    backup_path = f"{db_path}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def retire_study_if_data_source_changed(storage, study_name: str, meta: dict, db_path: str) -> optuna.Study:
    """study の Parquet 世代が変わっていたら退避し、正式名で新規作成する
    （backtest_stable_data_plan.md §4-5）。

    study 名は戦略名そのもので、`run_scenario_batch.py` がこの名前で最良パラメータを
    引くため、常に**新しいデータの study が正式名を持つ**（古い方を別名に退避する）。

    分岐:
      - 正式名の study が無い: 新規作成して今回の参照先を記録する。
      - 既存 study の ``user_attrs["parquet_generation"]`` が今回と一致: そのまま再開する。
      - 不一致: 先にファイルバックアップを取り、``optuna.copy_study()`` で
        ``<study_name>__<旧世代>``（名前衝突時は ``_2`` 、``_3``… を付与）に退避したうえで
        元を ``delete_study()`` し、正式名で新規作成する。
      - 記録が無い既存 study（本機能の導入前に作られたもの）: 退避せず、
        今回の参照先を記録して引き継ぐ（警告ログを出す）。

    Args:
        storage: Optuna の RDBStorage。
        study_name: 正式名（戦略名）。
        meta: `resolve_backtest_data_source()` が返すメタ情報
            （``data_source`` / ``backup_name`` / ``parquet_generation``）。
        db_path: `storage` が指す SQLite ファイルのパス（退避前のバックアップ対象）。
    """
    try:
        existing = optuna.load_study(study_name=study_name, storage=storage)
    except KeyError:
        existing = None

    if existing is None:
        study = optuna.create_study(
            study_name=study_name, storage=storage, load_if_exists=True, direction="maximize",
        )
        study.set_user_attr("data_source", meta["data_source"])
        study.set_user_attr("backup_name", meta["backup_name"])
        study.set_user_attr("parquet_generation", meta["parquet_generation"])
        return study

    prev_gen = existing.user_attrs.get("parquet_generation")

    if prev_gen is None:
        print(f"  [WARNING] study '{study_name}' に世代記録がありません"
              f"（本機能導入前の study の可能性）。退避せず今回の参照先を記録して引き継ぎます。")
        existing.set_user_attr("data_source", meta["data_source"])
        existing.set_user_attr("backup_name", meta["backup_name"])
        existing.set_user_attr("parquet_generation", meta["parquet_generation"])
        return existing

    if prev_gen == meta["parquet_generation"]:
        return existing

    # 世代が不一致 -> 古い study を退避してから正式名で新規作成する
    backup_path = _backup_sqlite_file(db_path)
    print(f"  [INFO] 世代不一致のため optimization_trials.db をバックアップしました: {backup_path}")

    existing_names = set(optuna.study.get_all_study_names(storage))
    retired_name = f"{study_name}__{prev_gen}"
    suffix = 2
    while retired_name in existing_names:
        retired_name = f"{study_name}__{prev_gen}_{suffix}"
        suffix += 1

    optuna.copy_study(from_study_name=study_name, from_storage=storage,
                       to_storage=storage, to_study_name=retired_name)
    # ユーザー資産なので、コピー先の trial 数が元と一致することを確かめてから元を消す
    n_src = len(existing.get_trials(deepcopy=False))
    n_dst = len(optuna.load_study(study_name=retired_name, storage=storage).get_trials(deepcopy=False))
    if n_src != n_dst:
        raise RuntimeError(
            f"study の退避コピーが不完全です（元 {n_src} 件 / コピー {n_dst} 件）。"
            f"元の study '{study_name}' は削除していません。バックアップ: {backup_path}")
    optuna.delete_study(study_name=study_name, storage=storage)
    print(f"  [INFO] study '{study_name}' の参照データ世代が変わりました"
          f"（{prev_gen} -> {meta['parquet_generation']}）。旧 study は '{retired_name}' に退避しました。")

    study = optuna.create_study(study_name=study_name, storage=storage, direction="maximize")
    study.set_user_attr("data_source", meta["data_source"])
    study.set_user_attr("backup_name", meta["backup_name"])
    study.set_user_attr("parquet_generation", meta["parquet_generation"])
    return study


def get_cached_data(config_app, start_date, end_date):
    global _cached_data_dict
    period_key = f"{start_date}_{end_date}"
    if period_key in _cached_data_dict:
        return _cached_data_dict[period_key]

    print(f"Preloading data from {start_date} to {end_date} for optimization...")
    res = preload_data(database.engine, start_date, end_date, refresh_cache=False,
                        master_files=_resolved_master_files)
    _cached_data_dict[period_key] = res
    print("Data preload complete.", flush=True)
    return res

def calculate_custom_score(metrics, total_trading_days,
                           quality_gate: tuple = (0.3, 12.0),
                           lcb_gate_penalty: float = 0.5):
    """最適化スコアを計算する（高いほど良い）。

    score = geo_mean_gain（1トレードあたりの**幾何平均**リターン%）
            ただし検出件数が quality_gate=(lo, hi) の内側の場合のみ。

    **型1（最適化バックテスト）が測るのは「1トレードの質」だけ**である。
    資産成長（CAGR）とドローダウンは型3（個別銘柄シナリオ・有限資産）の責務であり、
    枠を埋めるのは**個々の戦略ではなく組み合わせの仕事**という役割分担に基づく（§6.1）。

    構成:
      1. トレード5件未満 → 勾配ゲート（統計的に無意味）
      2. 検出件数が帯の外 → 失格。**帯の内側ではスコアに一切影響しない**
         （「量を増やして点を稼ぐ」経路を断つため、掛け算ではなくゲートにしている）
      3. 幾何平均が0以下 → その値をそのまま返す（**掛け算の経路に入れない**。下記の罠を参照）
      4. それ以外 → score = 幾何平均。`expectancy_lcb <= 0` なら lcb_gate_penalty で割引

    **なぜ幾何平均なのか**: 算術平均（`avg_gain`）は分散に無関心で、
    「95%が負けで上位5%が全部稼ぐ」構成と「安定して勝つ」構成を同じ点数にする。
    幾何平均は積なので大きな負けを強く罰し、**勝率を内在的に要求する**
    （勝率を明示的に掛ける必要がない＝二重計上の回避。`avg_gain` は既に勝率を内包している）。
    CAGR（`strat_multiplier`）も積＝幾何平均であり、旧 CAGR 主軸が結果として
    1取引%と勝率を両立させていたのはこの性質による。本指標は CAGR から
    `avg_slots` 正規化と年率化を外し、**取引数の寄与を含まない純粋な質**にしたもの。

    **罠: 掛け算のペナルティは、スコアが負のとき「罰」ではなく「ご褒美」になる。**
    負の値に係数（<1）を掛けるとゼロに近づく＝改善してしまう。スコアは複数の学習期間の
    平均なので、これを放置すると「片方で大勝ち・片方で赤字かつ罰あり」の構成が
    最適解として選ばれる。ステップ3のガードはこれを防ぐためにある。**削除しないこと。**

    設計の経緯と、捨てた案（DD 項・√勝率の掛け算）を捨てた理由:
    `doc/completed/objective_quality_first_plan.md`
    """
    if not metrics:
        return -1000.0

    trades_count = metrics.get('total_trades', 0)
    if trades_count < 5:
        # Gradient so Optuna knows if it's getting closer
        return -100.0 + (trades_count * 20.0)
    avg_trades_per_day = trades_count / total_trading_days

    # --- 1. 検出件数ゲート（帯の外は失格。帯の内側はスコアに一切影響させない） ---
    gate_lo, gate_hi = quality_gate
    if avg_trades_per_day < gate_lo:
        # 過少。calculate_prune_penalty の「過少」と同じ傾斜（-100 - 不足分×2000）
        return -100.0 - ((gate_lo - avg_trades_per_day) * 2000.0)
    if avg_trades_per_day > gate_hi:
        # 過多。calculate_prune_penalty の「過多」と同じ傾斜（-100 - 超過分×50）
        return -100.0 - ((avg_trades_per_day - gate_hi) * 50.0)

    # --- 2. 主指標: 1トレードあたりの幾何平均リターン% ---
    quality = metrics.get('geo_mean_gain')
    if quality is None:
        # 後方互換: 古い metrics には geo_mean_gain が無い
        quality = metrics.get('avg_gain', 0.0)

    # --- 3. 赤字のガード（掛け算の符号反転を防ぐ。docstring の「罠」を参照） ---
    if quality <= 0:
        return quality

    # --- 4. expectancy_lcb ソフトゲート（単価エッジの下限が無いスクリーンを減点） ---
    score = quality
    lcb = metrics.get('expectancy_lcb')
    if lcb is not None and lcb <= 0.0:
        score *= lcb_gate_penalty

    return score


def resolve_prune_floor(strat_base: dict, prune_conf: dict, min_avg: float) -> float:
    """fast_prune（高速足切り）の発火点を解決する（2026-08-15 追加）。

    `fast_prune` は **学習時間短縮のための速度の安全弁**であって、評価の門番ではない
    （`doc/issue_list.md` 2026-07-23 エントリ）。ところが発火点が実用帯の下限
    （`min_avg_hits_per_day` = 1.0）に置かれていたため、実質的に評価を支配していた:

      - `calculate_prune_penalty` は `base_penalty = -100` から始まる**不連続**な罰点で、
        1.000 件/日なら実スコア（例 +50）、0.999 なら -100 と 150 ポイント跳ぶ
      - 罰点は `return` で**残りの期間の評価をスキップ**する。Bull で健全（1.09）でも
        Bear が僅かに薄い（0.91）だけでスコアが -323 に確定していた（B5 の実例）

    そこで発火点を `prune_floor_hits_per_day`（既定 0.2 件/日 ≒ 251営業日で50件未満の
    「明らかに死んでいる」領域）まで下げる。
    （2026-08-25: `prune_floor`〜`min_avg` の範囲は当時 `detect_adequacy` の連続減衰に
    委ねていたが、目的関数のゲート化に伴い `detect_adequacy` は廃止した。
    現在この範囲はスコアに影響せず、`quality_gate` の下限のみが門番として働く。）

    **戦略側が既に `min_avg_hits_per_day` を `prune_floor` より低く設定している場合は
    その値を尊重する**（E2 の 0.02、E1 の 0.05 等。引き締めになってはいけない）。

    解決順序: 戦略側 > [optimization_pruning] > コード既定 0.2。
    """
    floor = float(strat_base.get('prune_floor_hits_per_day',
                                 prune_conf.get('prune_floor_hits_per_day', 0.2)))
    return min(float(min_avg), floor)


def calculate_prune_penalty(avg_hits: float, hit_rate_pct: float, bounds: tuple):
    """
    Calculate directional penalty when a trial fails pruning condition.
    bounds = (min_avg, max_avg, min_hit_rate_pct)
    Allows Optuna's TPE to infer whether to tighten or relax conditions.
    """
    min_avg, max_avg, min_hit_rate = bounds
    
    # Base penalty offset inside the forbidden zone
    base_penalty = -100.0
    
    if avg_hits < min_avg:
        # Too few hits. Penalty gets much worse as it reaches 0.
        return base_penalty - ((min_avg - avg_hits) * 2000.0)
        
    if avg_hits > max_avg:
        # Too many hits. Penalty gets linearly worse.
        return base_penalty - ((avg_hits - max_avg) * 50.0)
        
    if hit_rate_pct < min_hit_rate:
        # Hit rate ratio too low.
        return base_penalty - ((min_hit_rate - hit_rate_pct) * 100.0)
        
    return None

# =============================================================
# TOML-driven parameter parsing functions (testable, pure)
# =============================================================

def resolve_strategy(config, strategy_name):
    """
    Find the strategy configuration dict and actual name in backtest_config.toml.
    Supports both exact match (full name) and legacy short code/prefix fallback.
    """
    strategies = config.get('strategy', [])
    
    # 1. Exact match
    strat = next((s for s in strategies if s.get('name') == strategy_name), None)
    if strat:
        return strat, strategy_name
        
    # 2. Legacy short code fallback mapping
    full_names = {
        'A': 'A_momentum_breakout',
        'B': 'B_theme_momentum',
        'C1': 'C1_rrg_leading_in',
        'C2': 'C2_rrg_improving_in',
        'D': 'D_ema21_pullback',
        'E': 'E1_vcp',  # Map legacy 'E' to 'E1_vcp'
        'F': 'F_elite_momentum97'
    }
    legacy_name = full_names.get(strategy_name)
    if legacy_name:
        strat = next((s for s in strategies if s.get('name') == legacy_name), None)
        if strat:
            return strat, legacy_name
            
    # 3. Wildcard prefix check (e.g., strategy_name='B4' matches 'B4_rs_trend_with_theme')
    found_name = next((s['name'] for s in strategies if s.get('name', '').startswith(strategy_name + "_")), None)
    if found_name:
        strat = next((s for s in strategies if s.get('name') == found_name), None)
        return strat, found_name
        
    return None, None


def list_optimizable_strategies(config):
    """`--strategy all` の展開対象を返す。

    `[strategy.optimization]`（探索空間）を持つ戦略だけを、config の記載順に返す。
    探索空間が無い戦略（ゾーン系 I1/I2/J1/J2 など）は study を作れず実行時に落ちるため含めない。
    """
    return [
        s['name'] for s in config.get('strategy', [])
        if s.get('name') and s.get('optimization')
    ]


def run_all_strategies(config, trials, storage, n_jobs, data_source=None, run=subprocess.run):
    """最適化対象の全戦略を、戦略ごとに**別プロセス**で順に単体実行する。

    別プロセスにするのは、数時間〜十数時間かかる実行でのメモリ肥大や、
    1戦略の異常終了が後続に波及するのを避けるため（run_optimization.bat が
    戦略ごとに python を起動していたのと同じ挙動）。1本が失敗しても残りを続ける。

    Args:
        run: `subprocess.run` 互換の呼び出し（テストで差し替える）。

    Returns:
        失敗（終了コード != 0）した戦略名のリスト。全て成功なら空。
    """
    names = list_optimizable_strategies(config)
    failed = []
    for i, name in enumerate(names, 1):
        print("\n" + "=" * 70, flush=True)
        print(f"  [all {i}/{len(names)}] Optimizing Strategy: {name}", flush=True)
        print("=" * 70, flush=True)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--strategy", name, "--trials", str(trials), "--n-jobs", str(n_jobs)]
        if data_source is not None:
            cmd += ["--data-source", data_source]
        if storage:
            cmd += ["--storage", storage]
        result = run(cmd)
        if result.returncode != 0:
            print(f"[Warning] Error occurred while running strategy {name} "
                  f"(exit code {result.returncode})", flush=True)
            failed.append(name)
    return failed


def parse_optimization_params(config, strategy_name):
    """Parse optimization parameter definitions from TOML config.
    
    Args:
        config: Parsed TOML config dict.
        strategy_name: Strategy name (e.g. 'B4_rs_trend_with_theme').
    
    Returns:
        List of parameter definition dicts.
    
    Raises:
        ValueError: If strategy is not found in config.
    """
    strat_base, actual_name = resolve_strategy(config, strategy_name)
    if not strat_base:
        raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
    raw_params = strat_base.get('optimization', {})
    result = []
    for param_name, param_def in raw_params.items():
        entry = {'name': param_name, 'type': param_def['type']}
        if param_def['type'] in ('float', 'int'):
            entry['min'] = param_def['min']
            entry['max'] = param_def['max']
            entry['step'] = param_def['step']
        elif param_def['type'] == 'categorical':
            # Convert string 'none' to Python None
            entry['choices'] = [
                None if (isinstance(v, str) and v.lower() == 'none') else v
                for v in param_def['choices']
            ]
        result.append(entry)
    return result


def apply_trial_params(trial, param_defs, strat):
    """Apply Optuna trial suggestions to strategy dict based on param definitions."""
    for p in param_defs:
        name = p['name']
        if p['type'] == 'float':
            strat[name] = trial.suggest_float(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'int':
            strat[name] = trial.suggest_int(name, p['min'], p['max'], step=p['step'])
        elif p['type'] == 'categorical':
            strat[name] = trial.suggest_categorical(name, p['choices'])


def parse_optimization_periods(config):
    """Parse optimization evaluation periods from TOML config."""
    if 'optimization_periods' not in config:
        raise ValueError(
            "No [optimization_periods] section found in config. "
            "Please define evaluation periods in backtest_config.toml."
        )
    raw_periods = config['optimization_periods']['periods']
    return [(p['start'], p['end']) for p in raw_periods]


def parse_validation_sets(config) -> list:
    """[[optimization_validation.sets]] をパースする。

    学習期間（optimization_periods）とは別の、スコア計算に一切使わない
    ホールドアウト検証セット。無ければ空リスト（検証スキップ）。
    Returns: [{'label': str, 'windows': [(start, end), ...]}, ...]
    """
    section = config.get('optimization_validation', {})
    result = []
    for s in section.get('sets', []):
        result.append({
            'label': s['label'],
            'windows': [(w['start'], w['end']) for w in s['windows']],
        })
    return result


def evaluate_holdout_for_params(strat: dict, config_app, exit_rules, validation_sets: list,
                                entry_mode: str = "close", consider_tax: float = 0.0) -> dict:
    """ホールドアウト検証セットでパラメータを評価する（スコア計算には一切使わない）。

    セット内の全窓のトレードを**プールしてから**指標計算する。3ヶ月窓単体に
    活動量ゲート等を当てると静かなスクリーンが不当に沈むため。窓別内訳も併記する。

    注意: プール後の指標（pooled）は窓をまたぐため DD は additive（legacy）値。
    窓単体の MTM DD は windows[i]['metrics'] 側を参照する。
    """
    from backtest.backtest_report import calculate_metrics as _calc_metrics

    results = {}
    for vset in validation_sets:
        pooled_trades = []
        window_metrics = []
        for start_date, end_date in vset['windows']:
            df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = \
                get_cached_data(config_app, start_date, end_date)
            metrics, trades = run_single_strategy(
                strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents,
                trading_dates, exit_rules, show_progress=False,
                consider_tax=consider_tax, entry_mode=entry_mode,
            )
            window_metrics.append({'start': start_date, 'end': end_date, 'metrics': metrics})
            if trades:
                pooled_trades.extend(trades)
        results[vset['label']] = {
            'pooled': _calc_metrics(pooled_trades, consider_tax=consider_tax),
            'windows': window_metrics,
        }
    return results


def run_holdout_validation(study, strat_base: dict, actual_name: str, config, config_app,
                           exit_rules, validation_sets: list, top_n: int = 5):
    """study 完了後に best-N trial をホールドアウト検証セットで評価し、表と JSON を出力する。

    in-sample スコア（study の value）と並記することで、勝者の呪い（選択バイアス）
    による劣化率を可視化する。結果は backend/backtest/results/holdout_{strategy}.json に保存。
    """
    completed = [t for t in study.trials
                 if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None]
    top_trials = sorted(completed, key=lambda t: t.value, reverse=True)[:top_n]
    if not top_trials:
        print("  Holdout: 評価可能な trial がありません。")
        return

    entry_mode = config.get('general', {}).get('entry_mode', 'close')
    # 型1（最適化）経路: consider_tax_optimization を読む（型2/型3の consider_tax とは分離）
    consider_tax = load_tax_rate(config, for_optimization=True)
    liquidity_floor = load_min_avg_dollar_volume_21(config)

    print("=" * 70)
    print(f"  Holdout Validation: best {len(top_trials)} trials / "
          f"sets: {', '.join(v['label'] for v in validation_sets)}")
    print("  （この期間はスコア最大化に一切使用していない未学習期間）")
    print("=" * 70)

    report = {
        'strategy': actual_name,
        'validation_sets': [{'label': v['label'], 'windows': v['windows']} for v in validation_sets],
        'trials': [],
    }
    for t in top_trials:
        strat = strat_base.copy()
        strat.update(t.params)
        strat['name'] = f"{actual_name}_holdout_t{t.number}"
        strat = inject_liquidity_floor(strat, liquidity_floor)

        results = evaluate_holdout_for_params(
            strat, config_app, exit_rules, validation_sets,
            entry_mode=entry_mode, consider_tax=consider_tax)

        print(f"\n  [Trial {t.number}] in-sample score = {t.value:.2f}")
        entry = {'trial': t.number, 'in_sample_score': t.value, 'params': t.params, 'sets': {}}
        for label, res in results.items():
            p = res['pooled']
            print(f"    {label:<14} trades={p['total_trades']:>4} "
                  f"win={p['win_rate'] * 100:5.1f}% exp={p['expectancy']:+6.2f}% "
                  f"lcb={p['expectancy_lcb']:+6.2f}% dd={p['max_drawdown_pct']:6.1f}%")
            entry['sets'][label] = {
                'pooled': {k: p.get(k) for k in (
                    'total_trades', 'win_rate', 'expectancy', 'expectancy_lcb',
                    'avg_gain', 'profit_factor', 'max_drawdown_pct')},
                'windows': [{'start': w['start'], 'end': w['end'],
                             'trades': (w['metrics'] or {}).get('total_trades', 0),
                             'win_rate': (w['metrics'] or {}).get('win_rate', 0.0),
                             'expectancy': (w['metrics'] or {}).get('expectancy', 0.0)}
                            for w in res['windows']],
            }
        report['trials'].append(entry)

    import json as _json
    out_dir = os.path.join(backend_dir, 'backtest', 'results')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'holdout_{actual_name}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        _json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Holdout report saved: {out_path}")


def enqueue_baseline_trial(study, config: dict, strategy_name: str) -> bool:
    """
    Extract baseline default parameters for a strategy from TOML config
    and enqueue them as the first trial in the study.
    """
    strat_base, actual_name = resolve_strategy(config, strategy_name)
    if not strat_base:
        raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
    # Parse optimization params from TOML
    try:
        param_defs = parse_optimization_params(config, actual_name)
    except ValueError:
        return False
        
    # Extract baseline parameters from the default strategy config
    default_params = {}
    for p in param_defs:
        name = p['name']
        if name in strat_base:
            default_params[name] = strat_base[name]
            
    if default_params:
        study.enqueue_trial(default_params)
        return True
    return False


# =============================================================
# Optuna objective function (TOML-driven)
# =============================================================

def objective(trial: optuna.Trial, strategy_name: str, config, config_app, exit_rules: ExitRules, periods: list):
    import traceback
    try:
        # Resolve strategy config
        strat_base, actual_name = resolve_strategy(config, strategy_name)
        if not strat_base:
            raise ValueError(f"Strategy '{strategy_name}' not found in backtest config.")
        
        # Copy strategy config to preserve baseline settings
        strat = strat_base.copy()
        strat['name'] = f"{actual_name}_Trial_{trial.number}"

        # 流動性ハード制約（最適化対象外・全戦略共通。戦略側の明示指定があればそちらを優先）
        strat = inject_liquidity_floor(strat, load_min_avg_dollar_volume_21(config))

        # Parse optimization params from TOML and apply via Optuna trial
        param_defs = parse_optimization_params(config, actual_name)
        apply_trial_params(trial, param_defs, strat)
        # Extract prune bounds from config if available (allow strategy-specific overrides)
        prune_conf = config.get('optimization_pruning', {})
        min_avg = strat_base.get('min_avg_hits_per_day', prune_conf.get('min_avg_hits_per_day', 1.0))
        max_avg = strat_base.get('max_avg_hits_per_day', prune_conf.get('max_avg_hits_per_day', 5.0))
        min_hit_rate = strat_base.get('min_hit_rate_pct', prune_conf.get('min_hit_rate_pct', 5.0))
        # fast_prune の発火点は「速度の安全弁」まで下げる（resolve_prune_floor 参照）。
        prune_min = resolve_prune_floor(strat_base, prune_conf, min_avg)
        prune_bounds = (prune_min, max_avg, min_hit_rate)

        # 型1（最適化）経路: consider_tax_optimization を読む（型2/型3の consider_tax とは分離）
        consider_tax = load_tax_rate(config, for_optimization=True)
        entry_mode = config.get('general', {}).get('entry_mode', 'close')

        # 検出件数ゲート quality_gate=(lo, hi)（2026-08-22 再設計）。
        # 下限は既存の min_avg_hits_per_day とは別の新設定キー（既定 0.3件/日）。
        # min_avg_hits_per_day をそのままゲート下限に流用すると、B1/B2/B3/B5/B6 等の
        # 上位戦略がほぼ全滅する（doc/completed/objective_quality_first_plan.md §2.3 Q3）。
        # 上限は既存のハードプルーニング上限 max_avg をそのまま流用する。
        # 2026-08-24: 戦略が min_avg_hits_per_day で**自分より低い**検出下限を宣言している場合は
        # そちらを尊重する（resolve_prune_floor と同じ「引き締めにならない」流儀）。
        # E1(0.05) / E2(0.02) は VCP ブレイクアウトで検出が稀なのが仕様だが、
        # 一律 0.3 のゲートを課すと条件を緩めるしかなくなり、実測で 1取引% が
        # +1.44 → -0.29 とマイナスに転落した（戦略の性格が別物に変質した）。
        _gate_default = float(prune_conf.get('quality_gate_min_hits_per_day', 0.3))
        quality_gate_lo = float(strat_base.get('quality_gate_min_hits_per_day',
                                               min(_gate_default, float(min_avg))))
        quality_gate = (quality_gate_lo, max_avg)

        # dd_est 換算係数（型1DD → 型3相当）。実測13点からの経験値のためハードコードせず設定化する
        # （計画書 §2.4 の過適合懸念。再最適化後に再検証予定）。

        # expectancy_lcb ソフトゲート係数。戦略側 > [optimization_pruning] > コード既定 の順で解決
        lcb_gate_penalty = float(strat_base.get('lcb_gate_penalty',
                                                prune_conf.get('lcb_gate_penalty', 0.5)))

        total_score = 0.0
        total_trades = 0
        total_trading_days_all = 0
        total_wins = 0
        expectancy_sum = 0.0
        expectancy_lcb_sum = 0.0
        avg_gain_sum = 0.0
        geo_mean_gain_sum = 0.0
        avg_spy_gain_sum = 0.0
        avg_holding_days_sum = 0.0
        avg_slots_sum = 0.0
        max_dd_overall = 0.0
        periods_with_trades = 0

        overall_strat_mult = 1.0
        overall_spy_mult = 1.0

        for start_date, end_date in periods:
            df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = get_cached_data(config_app, start_date, end_date)
            metrics, _ = run_single_strategy(
                strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents,
                trading_dates, exit_rules, show_progress=True,
                fast_prune=True, prune_bounds=prune_bounds, consider_tax=consider_tax,
                entry_mode=entry_mode
            )

            # --- Handle directional penalty branching ---
            if isinstance(metrics, dict) and metrics.get('fast_pruned'):
                penalty = calculate_prune_penalty(
                    metrics['avg_per_day'],
                    metrics['hit_rate_pct'],
                    prune_bounds
                )
                if penalty is not None:
                    # We return the heavy penalty immediately (skip remaining periods)
                    # so Optuna TPE learns the gradient.
                    return penalty
                else:
                    # Fallback, theoretically shouldn't reach if bounds logic matched
                    raise optuna.TrialPruned()

            period_score = calculate_custom_score(metrics, len(trading_dates), quality_gate,
                                                  lcb_gate_penalty)
            total_score += period_score
            total_trading_days_all += len(trading_dates)

            if metrics:
                total_trades += metrics.get('total_trades', 0)
                total_wins += metrics.get('win_trades', 0)
                max_dd_overall = min(max_dd_overall, metrics.get('max_drawdown_pct', 0.0))
                if metrics.get('total_trades', 0) > 0:
                    expectancy_sum += metrics.get('expectancy', 0.0)
                    expectancy_lcb_sum += metrics.get('expectancy_lcb', 0.0)
                    avg_gain_sum += metrics.get('avg_gain', 0.0)
                    geo_mean_gain_sum += metrics.get('geo_mean_gain', 0.0)
                    avg_spy_gain_sum += metrics.get('avg_spy_gain', 0.0)
                    avg_holding_days_sum += metrics.get('avg_holding_days', 0.0)
                    avg_slots_sum += metrics.get('avg_slots', 1.0)
                    overall_strat_mult *= metrics.get('strat_multiplier', 1.0)
                    overall_spy_mult *= metrics.get('spy_multiplier', 1.0)
                    periods_with_trades += 1

        # Average score across periods
        avg_score = total_score / len(periods)
        
        # Log aggregated metrics
        trial.set_user_attr("total_trades", total_trades)
        if total_trades > 0:
            trial.set_user_attr("win_rate", (total_wins / total_trades) * 100)
        else:
            trial.set_user_attr("win_rate", 0.0)
        
        if periods_with_trades > 0:
            avg_expectancy = expectancy_sum / periods_with_trades
            avg_gain = avg_gain_sum / periods_with_trades
            # 2026-08-25: スコアの主軸そのものを記録する。これが無いと
            # 「何を最大化した結果なのか」を DB から事後検証できない（実際、幾何平均へ
            # 切り替えた後も分析は avg_gain=相加平均を代理に見ていた）。
            # 集計方法はスコアと同じ「期間ごとの値の相加平均」に揃えてあるので、
            # lcb ゲートが掛からなければ score とほぼ一致する。
            trial.set_user_attr("geo_mean_gain", round(geo_mean_gain_sum / periods_with_trades, 3))
            avg_spy = avg_spy_gain_sum / periods_with_trades
            trial.set_user_attr("expectancy", round(avg_expectancy, 3))
            trial.set_user_attr("expectancy_lcb", round(expectancy_lcb_sum / periods_with_trades, 3))
            trial.set_user_attr("avg_gain", round(avg_gain, 3))
            trial.set_user_attr("avg_spy_gain", round(avg_spy, 3))
            trial.set_user_attr("alpha", round(avg_gain - avg_spy, 3))
            trial.set_user_attr("avg_holding_days", round(avg_holding_days_sum / periods_with_trades, 3))
            trial.set_user_attr("avg_slots", round(avg_slots_sum / periods_with_trades, 3))
        else:
            trial.set_user_attr("expectancy", 0.0)
            trial.set_user_attr("expectancy_lcb", 0.0)
            trial.set_user_attr("avg_gain", 0.0)
            trial.set_user_attr("geo_mean_gain", 0.0)
            trial.set_user_attr("avg_spy_gain", 0.0)
            trial.set_user_attr("alpha", 0.0)
            trial.set_user_attr("avg_holding_days", 0.0)
            trial.set_user_attr("avg_slots", 1.0)

        # avg_hits_per_day: ゲート判定の再現に必要（§3.3）。全期間通算の日平均ヒット件数
        avg_hits_per_day = total_trades / total_trading_days_all if total_trading_days_all > 0 else 0.0
        trial.set_user_attr("avg_hits_per_day", round(avg_hits_per_day, 4))

        # Calculate total years across all periods to compute true annualized CAGR
        total_years = 0.0
        for start_date, end_date in periods:
            from datetime import datetime, date as dt_date
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").date() if isinstance(start_date, str) else start_date
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").date() if isinstance(end_date, str) else end_date
            total_years += (e_dt - s_dt).days / 365.25

        port_total_return = (overall_strat_mult - 1.0) * 100.0
        spy_total_return = (overall_spy_mult - 1.0) * 100.0

        if total_years > 0:
            portfolio_cagr = (overall_strat_mult ** (1.0 / total_years) - 1.0) * 100.0
            spy_bh_cagr = (overall_spy_mult ** (1.0 / total_years) - 1.0) * 100.0
        else:
            portfolio_cagr = 0.0
            spy_bh_cagr = 0.0
            
        portfolio_vs_spy = portfolio_cagr - spy_bh_cagr
        port_vs_spy_total = port_total_return - spy_total_return

        trial.set_user_attr("max_drawdown", max_dd_overall)
        trial.set_user_attr("port_cagr", round(portfolio_cagr, 2))
        trial.set_user_attr("spy_cagr", round(spy_bh_cagr, 2))
        trial.set_user_attr("port_vs_spy", round(portfolio_vs_spy, 2))
        trial.set_user_attr("port_total_return", round(port_total_return, 2))
        trial.set_user_attr("spy_total_return", round(spy_total_return, 2))
        trial.set_user_attr("port_vs_spy_total", round(port_vs_spy_total, 2))

        # Generate TOML format string for parameters (for easy copy-paste)
        toml_params = []
        for key, value in trial.params.items():
            if isinstance(value, bool):
                toml_val = "true" if value else "false"
            elif isinstance(value, str):
                toml_val = f'"{value}"'
            else:
                toml_val = value
            toml_params.append(f"{key} = {toml_val}")
        
        params_toml_str = "\n".join(toml_params)
        # Key renamed to 'z_params_toml' so it sorts to the very end of the list in Optuna Dashboard
        trial.set_user_attr("z_params_toml", params_toml_str)
        # Also set as system attribute 'note' for Optuna Dashboard
        trial.set_system_attr("note", params_toml_str)

        # --- Trial Summary Log ---
        print(f"  [Trial {trial.number}] Score: {avg_score:.2f} | PortCAGR: {portfolio_cagr:+.1f}% (vs SPY {portfolio_vs_spy:+.1f}%) | MaxDD: {max_dd_overall:.1f}% | {avg_hits_per_day:.1f} hits/day | Trades: {total_trades} | Win: {trial.user_attrs['win_rate']:.1f}% | AvgGain: {trial.user_attrs['avg_gain']:+.2f}%", flush=True)

        return avg_score
    except Exception as e:
        print(f"\n!!! EXCEPTION IN TRIAL {trial.number} !!!", flush=True)
        traceback.print_exc()
        raise e

def main():
    parser = argparse.ArgumentParser(description="Optimize backtest parameters with Optuna")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy name or short code (A, B, C...). 'all' で [strategy.optimization] を持つ全戦略を順に実行")
    parser.add_argument("--trials", type=int, default=30)
    # ワークツリーから実験する場合の逃がし口。ワークツリーの data/ は空なので、
    # 何も指定しないと本体とは別の空の trial DB が黙って作られ、既存 study と比較できない。
    # （データ本体の切り替えは STOCKTOOL_DB_PATH が既に担当している）
    parser.add_argument("--storage", type=str, default=None,
                        help="Optuna trial DB のパス（既定: <project_root>/data/optimization_trials.db）")
    # 2026-07-18: n_jobs=-1（全コア並列）は RDBStorage(SQLite) の並行書き込み耐性の低さと
    # 衝突し、"ValueError: Cannot tell a COMPLETE trial" が発生する（複数スレッドが同一trialの
    # 完了を同時に study.tell しようとして SQLite 側でレース）。n_jobs=4 に下げても再発を確認
    # （2026-07-17）— 並列度を下げるだけでは解消せず、SQLite storage + スレッド並列という
    # 組み合わせ自体が構造的に不安定。確実性を優先し既定を 1（完全逐次）にする。
    # 加えて過去には get_cached_data のキャッシュ未ロックによるメモリ枯渇も引き起こした
    # （並列時のみ発生する問題だったため、これも n_jobs=1 なら再発しない）。
    parser.add_argument("--n-jobs", type=int, default=1,
                        help="並列トライアル数。SQLite storage は並列(>1)だと "
                             "COMPLETE trial エラーで停止しうるため既定は1（完全逐次・最も安全）")
    parser.add_argument("--data-source", type=str, default=None,
                        help="最適化が読む Parquet マスタの参照先。"
                             "'backup'（検証済みの最新バックアップ・常に本番）/ "
                             "'latest'（現在の data ディレクトリの最新世代）/ "
                             "バックアップのフォルダ名（例 '_bk_20260925_...')。"
                             "省略時は本番なら backup、それ以外（ワークツリー・sandbox）なら latest。")
    args = parser.parse_args()
    
    # Use config just to load exit rules
    config_path = os.path.join(backend_dir, 'backtest', 'backtest_config.toml')
    with open(config_path, 'rb') as f:
        config = tomli.load(f)

    # --strategy all: 戦略ごとに別プロセスで単体実行して終了する（DB 初期化・データ読み込みは各プロセスが行う）
    if args.strategy.lower() == 'all':
        names = list_optimizable_strategies(config)
        print(f"--strategy all: 最適化対象 {len(names)} 戦略を順に実行します（各 {args.trials} trials）:", flush=True)
        print("  " + ", ".join(names), flush=True)
        failed = run_all_strategies(config, args.trials, args.storage, args.n_jobs, args.data_source)
        print("\n" + "=" * 70, flush=True)
        print(f"  all 完了: 成功 {len(names) - len(failed)} / {len(names)} 戦略", flush=True)
        if failed:
            print(f"  失敗: {', '.join(failed)}", flush=True)
        print("=" * 70, flush=True)
        sys.exit(1 if failed else 0)

    exit_rules = ExitRules.from_config(config)

    # Validate strategy parameters in configuration
    try:
        from backtest.backtest_runner import validate_strategies_config, print_validation_warnings
        dummy_df = pd.DataFrame()
        warnings = validate_strategies_config(config.get('strategy', []), dummy_df, dummy_df)
        if warnings:
            print_validation_warnings(warnings)
    except Exception as e:
        print(f"Warning during config validation: {e}")
    
    # Init DB
    project_root = os.path.dirname(backend_dir)
    config_path_app = os.path.join(project_root, 'config.toml')
    with open(config_path_app, 'rb') as f:
        config_app = tomli.load(f)
    db_path_main = os.path.join(project_root, config_app['system']['db_path'])
    init_db(db_path_main)
    
    # Load periods from TOML config
    periods = parse_optimization_periods(config)

    # 参照先は開始時に1回だけ解決し、全期間で同じ Parquet マスタ世代を使う
    # （backtest_stable_data_plan.md §3-C）。"latest" は db_path_main（現在の
    # data ディレクトリ）を active_db_path として渡す。
    resolve_data_source(args.data_source, active_db_path=db_path_main)

    # 2026-07-18: n_jobs=-1（後段の study.optimize）は複数トライアルを並列スレッドで
    # 実行するため、_cached_data_dict（get_cached_data のグローバルキャッシュ）に
    # ロックが無いと、起動直後に全スレッドが同じ期間へ同時にキャッシュミスし、
    # 各スレッドが独立に同一データ（indicators だけで1〜3GB/期間）をロードしてしまう
    # （8コア環境で実測: 同一期間が8重にロードされ物理メモリ35GB超で MemoryError）。
    # 並列実行が始まる前に全期間を逐次ロードしてキャッシュを温めておくことで、
    # 各スレッドは読み取り専用アクセスのみになりレースが起きない。
    print("Pre-warming period data cache (sequential, before parallel trials start)...")
    for start_date, end_date in periods:
        get_cached_data(config_app, start_date, end_date)
    print("Pre-warm complete.")

    # Setup storage
    db_path = os.path.abspath(args.storage) if args.storage else \
        os.path.join(project_root, 'data', 'optimization_trials.db')
    
    # 1. Ensure directories exist
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
    # 2. Use RDBStorage with high timeout to prevent "database is locked" errors
    from optuna.storages import RDBStorage
    storage = RDBStorage(
        url=f"sqlite:///{db_path}",
        engine_kwargs={
            "connect_args": {"timeout": 60.0} # Extend timeout from default 5s to 60s
        }
    )
    # Resolve actual strategy name from configuration
    strat_base, actual_name = resolve_strategy(config, args.strategy)
    if not strat_base:
        print(f"Error: Strategy '{args.strategy}' not found in backtest config.")
        return

    study_name = actual_name
    
    tax_rate = load_tax_rate(config, for_optimization=True)
    print("=" * 60)
    print(f"  Optuna Optimization Runner: Strategy {actual_name} (from: {args.strategy})")
    print(f"  Periods: {periods}")
    if tax_rate > 0.0:
        print(f"  [Tax] 適用税率: {tax_rate * 100:.1f}% (consider_tax_optimization={tax_rate})")
    else:
        print("  [Tax] 税なし (consider_tax_optimization=0.0)")
    print("=" * 60)
    
    # 参照データの世代が既存 study と変わっていれば退避する（backtest_stable_data_plan.md §4-5）。
    study = retire_study_if_data_source_changed(
        storage, study_name, _resolved_data_source_meta, db_path,
    )

    try:
        enqueued = enqueue_baseline_trial(study, config, actual_name)
        if enqueued:
            print("  Enqueued baseline trial from default config values.")
    except Exception as e:
        print(f"  Warning: Could not enqueue baseline trial: {e}")
        
    study.optimize(
        lambda t: objective(t, actual_name, config, config_app, exit_rules, periods),
        n_trials=args.trials,
        n_jobs=args.n_jobs
    )
    
    print("-" * 60)
    print("Optimization finished.")
    print("Best trial:")
    trial = study.best_trial
    print(f"  Value (Score): {trial.value:.2f}")
    print("\n[TOML Params for copy-paste]")
    print("-" * 30)
    for key, value in trial.params.items():
        if isinstance(value, bool):
            toml_val = "true" if value else "false"
        elif isinstance(value, str):
            toml_val = f'"{value}"'
        else:
            toml_val = value
        print(f"{key} = {toml_val}")
    print("-" * 30 + "\n")
        
    if "expectancy" in trial.user_attrs:
        print(f"  Port CAGR:      {trial.user_attrs.get('port_cagr', 0):.2f}% (Total: {trial.user_attrs.get('port_total_return', 0):.2f}%)  ← スコアの主指標（年率化・DDペナルティ・検出件数帯で調整）")
        print(f"  Max Drawdown:   {trial.user_attrs.get('max_drawdown', 0):.1f}%")
        print(f"  Expectancy:     {trial.user_attrs['expectancy']:.3f}%")
        print(f"  Expectancy LCB: {trial.user_attrs.get('expectancy_lcb', 0):.3f}%  (期待値 − 2×SE。補助指標＝シグナルの質)")
        print(f"  Avg Gain/Trade: {trial.user_attrs['avg_gain']:.3f}%")
        print(f"  Avg SPY Gain:   {trial.user_attrs['avg_spy_gain']:.3f}%")
        print(f"  Alpha:          {trial.user_attrs.get('alpha', 0):.3f}%")
        print(f"  Win Rate:       {trial.user_attrs.get('win_rate', 0):.1f}%")
        print(f"  Total Trades:   {trial.user_attrs.get('total_trades', 0)}")
        print(f"  SPY B&H CAGR:   {trial.user_attrs.get('spy_cagr', 0):.2f}% (Total: {trial.user_attrs.get('spy_total_return', 0):.2f}%)")
        print(f"  Port vs SPY:    {trial.user_attrs.get('port_vs_spy', 0):.2f}% (Total: {trial.user_attrs.get('port_vs_spy_total', 0):.2f}%)")

    # --- ホールドアウト検証（未学習期間で best-N を評価。スコア最大化には不使用） ---
    validation_sets = parse_validation_sets(config)
    if validation_sets:
        try:
            run_holdout_validation(study, strat_base, actual_name, config, config_app,
                                   exit_rules, validation_sets)
        except Exception as e:
            import traceback
            print(f"\n  Warning: ホールドアウト検証でエラーが発生しました: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()
