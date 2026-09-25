"""
backtest_runner.py — CLI Entry Point for Backtest Engine

Preloads all required data into memory, then iterates through trading days
to scan signals and simulate trades for each strategy.

Usage:
    python backtest_runner.py [--config PATH] [--strategy NAME]
"""
import os
import sys
import time
import argparse
from datetime import date as dt_date, timedelta

import tomli
import pandas as pd

# Add project root and backend to path for flexible import resolution
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.db.database import init_db
from backend.db import database

from backend.backtest.backtest_screener import scan_signals_for_date, SignalRecord
from backend.backtest.backtest_simulator import simulate_trade, ExitRules, TradeResult
from backend.backtest.backtest_report import calculate_metrics, print_comparison_table, save_results_json
from backend.backtest.strategy_normalizer import normalize_strategy_keys
from backend.backtest.common_constraints import load_min_avg_dollar_volume_21, inject_liquidity_floor, load_tax_rate


def load_config(config_path: str) -> dict:
    """Load TOML configuration file."""
    with open(config_path, 'rb') as f:
        return tomli.load(f)


# preload_data() が最後に解決した参照先のメタ情報（backtest_stable_data_plan.md §3-D）。
# master_files を直接渡された場合（シナリオバッチの子プロセス）は None のまま。
# run_backtest() が結果 JSON に記録するために読む。
_last_preload_meta = None


def resolve_backtest_db_path(active_db_path, logger=None) -> str:
    """バックテストが読む Parquet マスターの位置を決める DB パスを解決する。

    優先順位: `STOCKTOOL_DB_PATH` > `init_db()` が設定した値 > `config.toml` > 既定。

    **環境変数を最優先にする理由**（2026-08-26 に実際に事故った）:
    `get_active_db_path()` は `init_db()` が設定するモジュールグローバルを返す。
    本モジュールは `backend.db.database` を import しているが、
    `optimization_runner` は `db.database`（PYTHONPATH=backend 形式）で `init_db` する。
    **Python はこの2つを別モジュール実体として扱う**ため、ここでは None のままになり、
    環境変数を無視して `config.toml` の本番 DB へフォールバックしていた
    ＝ Sandbox を指定しても本番 Parquet を読んでいた。
    import 形式の混在は既存の規約（CLAUDE.md）なので解消せず、**この関数を
    唯一の解決経路にして環境変数を勝たせる**ことで塞ぐ。

    さらに、`init_db()` 済みの値が環境変数と食い違う場合は**警告を出す**。
    「初期化はしたが別の DB を指していた」も同じく隔離が破れている状態のため。
    """
    import os
    import pathlib
    import logging

    log = logger or logging.getLogger(__name__)
    env_path = os.environ.get("STOCKTOOL_DB_PATH")

    if env_path:
        if active_db_path and os.path.abspath(active_db_path) != os.path.abspath(env_path):
            log.warning(
                "STOCKTOOL_DB_PATH (%s) と初期化済み DB (%s) が食い違っています。"
                "環境変数を優先します（Sandbox 隔離を守るため）。", env_path, active_db_path)
        return env_path

    if active_db_path:
        return active_db_path

    # 最終フォールバックは paths.py に一任する。
    # 旧コードは config.toml を直読みして失敗時に相対パス "data/stocktool.db" を
    # 返しており、ワークツリーでは存在しない DB（＝空の Parquet ディレクトリ）を
    # 指していた。paths 側は未プロビジョニングなら DataNotProvisionedError を出す。
    import paths
    return paths.get_db_path("stocktool")


def preload_data(engine, start_date: str, end_date: str, refresh_cache: bool = False,
                  data_source: str | None = None, master_files: dict | None = None):
    """
    Preload all required data into pandas DataFrames.
    Loads ALL data from the Parquet Master full-history files and performs in-memory slicing.
    SQLite connection is COMPLETELY bypassed.

    Args:
        data_source: 参照先（backtest_stable_data_plan.md §3-B / §7-3）。
            ``"backup"``（検証済みの最新バックアップ・常に本番 data ディレクトリ）/
            ``"latest"``（現在の data ディレクトリの最新世代。本体なら本番、
            ワークツリー・sandbox なら自分のデータ。旧実装の参照先と同じ）/
            バックアップのフォルダ名（例 ``"_bk_20260925_..."``）。
            ``None``（既定）なら `paths.is_production()` で判定する
            （本番なら `"backup"`、そうでなければ `"latest"`）。
            ``"production"`` は廃止済みで受け付けない。
            ``master_files`` が渡された場合はこの引数は無視される。
        master_files: 解決済みのファイル辞書（`get_latest_master_files` 等と同じキー）。
            渡された場合、探索・ポインタ読みは一切行わない
            （シナリオバッチの子プロセスが、親で1回だけ解決した結果を使うための経路）。

    Returns:
        Tuple of (df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates)
    """
    import sys
    import pathlib
    import logging
    import paths
    from backend.db.database import get_active_db_path
    from backend.pipeline.parquet_cache_manager import (
        rotate_and_archive_to_parquet, resolve_backtest_data_source,
    )

    global _last_preload_meta

    def log(msg):
        print(msg)
        sys.stdout.flush()

    t0 = time.time()

    # refresh_cache（SQLite から再生成）はバックアップへ書き込めない。"latest" 専用。
    # data_source 未指定（None）の場合、本番では既定が "backup" になるため、
    # refresh_cache=True と組み合わせるとこのガードで常に ValueError になり
    # scenario_runner.py / scenario_comparison_runner.py / verify_db_vs_cache.py
    # （--data-source を持たない）が回避できない（R2）。
    # refresh_cache=True かつ未指定なら "latest" を既定にする。明示的に "backup" 等を
    # 指定した場合は従来どおり ValueError のまま（書き込み禁止の意図を守る）。
    effective_data_source = data_source
    if effective_data_source is None:
        if refresh_cache:
            effective_data_source = "latest"
        else:
            effective_data_source = "backup" if paths.is_production() else "latest"
    if refresh_cache and effective_data_source != "latest":
        raise ValueError(
            f"--refresh-cache は data_source='latest' のときだけ許可されます"
            f"（指定: data_source={data_source!r} → {effective_data_source!r}）。"
            f" バックアップへは書き込みません。"
        )

    if master_files is not None:
        # 呼び出し元（親プロセス）が既に解決済み。探索・ポインタ読みは一切しない。
        latest_files = master_files
        _last_preload_meta = None
        log("Data source: externally provided master_files (no lookup performed).")
    else:
        # 1. Determine active DB path & Parquet master directory
        #    Sandbox 隔離（STOCKTOOL_DB_PATH 等）を反映した「現在の data ディレクトリ」を
        #    解決する。"latest" の参照先そのものであり、--refresh-cache の書き込み先でもある。
        db_path = resolve_backtest_db_path(get_active_db_path())

        if refresh_cache:
            # 暗黙の自動再生成は行わない（2026-09-09）。
            # 旧実装は「ポインタが読めない」というだけで rotate_and_archive_to_parquet() を呼び、
            # **読み取り専用のはずのバックテストが本番 Parquet を書き換えていた**。
            # ローテートは旧世代とのマージを伴うため、ポインタが壊れていると
            # SQLite のホット期間（730日）だけの世代を公開して全期間履歴を失う経路になる。
            # 再生成の入口は明示指定（--refresh-cache、かつ data_source='latest'）だけに絞る。
            log("  Refresh requested. Regenerating Parquet master from SQLite...")
            # 失敗をログだけにして先へ進まない。旧実装は例外を握り潰したうえで
            # 後段の「ファイルが無い」という**誤誘導のメッセージ**に化けていた
            # （実際にはファイルは存在し、読めなかっただけ）。
            from backend.db import database
            with database.get_db() as db:
                rotate_and_archive_to_parquet(db, db_path, logging.getLogger())

        # 2. 参照先を解決する（backup / latest / 名前指定のバックアップ）。
        #    "latest" は db_path（Sandbox 隔離を反映済み）を active_db_path として渡す。
        latest_files, meta = resolve_backtest_data_source(data_source, active_db_path=db_path)
        _last_preload_meta = meta
        log(f"Data source: {meta['data_source']}"
            + (f" (backup: {meta['backup_name']})" if meta['backup_name'] else "")
            + f" | generation={meta['parquet_generation']}")

    if not latest_files:
        raise FileNotFoundError(
            f"Parquet master cache files not found (data_source={data_source!r})!\n"
            f"  パイプラインを1回実行してマスタを生成するか、--refresh-cache を付けて実行してください。")

    log(f"Loading data from Parquet Master cache: {pathlib.Path(latest_files['prices']).name} ...")
    
    try:
        # Calculate date buffer for prices rolling calculation (window=21 -> safe buffer 45 days)
        sd_dt = dt_date.fromisoformat(start_date)
        prices_sd_str = (sd_dt - timedelta(days=45)).isoformat()
        
        # 3. Read Parquet full-history masters into memory with pushdown date filters to minimize RAM usage
        t_load = time.time()
        df_symbols = pd.read_parquet(latest_files['symbols'])
        df_prices = pd.read_parquet(latest_files['prices'], filters=[('date', '>=', prices_sd_str), ('date', '<=', end_date)])
        df_indicators = pd.read_parquet(latest_files['indicators'], filters=[('date', '>=', start_date), ('date', '<=', end_date)])
        df_ranks = pd.read_parquet(latest_files['ranks'], filters=[('date', '>=', start_date), ('date', '<=', end_date)])
        df_theme_constituents = pd.read_parquet(latest_files['tc'])
        log(f"  -> Parquet file loading completed in {time.time()-t_load:.2f}s")
        
        # Convert date column safely into canonical datetime.date object
        df_prices['date'] = pd.to_datetime(df_prices['date']).dt.date
        df_indicators['date'] = pd.to_datetime(df_indicators['date']).dt.date
        df_ranks['date'] = pd.to_datetime(df_ranks['date']).dt.date

        # 価格データの最終日を1行で出す（backtest_stable_data_plan.md §3-D）。
        # 参照先の種類・バックアップ名は上の "Data source: ..." 行で既に出ているので重複しない。
        if len(df_prices) > 0:
            log(f"  Price data through: {df_prices['date'].max()}")

        # 4. In-memory slicing based on start_date and end_date
        t_slice = time.time()
        sd = dt_date.fromisoformat(start_date)
        ed = dt_date.fromisoformat(end_date)

        # Slice in-memory
        df_prices = df_prices[(df_prices['date'] >= sd) & (df_prices['date'] <= ed)]
        df_indicators = df_indicators[(df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)]
        df_ranks = df_ranks[(df_ranks['date'] >= sd) & (df_ranks['date'] <= ed)]

        # df_ranks は Parquet 上も wide 形式（SQLite と同一スキーマ）なので、
        # long への melt は行わずそのまま wide で返す（P0-1/P0-2 対応）。
        log(f"  -> In-memory pandas slicing completed in {time.time()-t_slice:.3f}s")
        
        # Log memory usage metrics
        total_mem_mb = (
            df_symbols.memory_usage(deep=True).sum() +
            df_prices.memory_usage(deep=True).sum() +
            df_indicators.memory_usage(deep=True).sum() +
            df_ranks.memory_usage(deep=True).sum() +
            df_theme_constituents.memory_usage(deep=True).sum()
        ) / (1024 * 1024)
        log(f"  Total Data Memory Usage: {total_mem_mb:.2f} MB")
        
        # Get unique sorted trading dates
        trading_dates = sorted(
            df_indicators[
                (df_indicators['date'] >= sd) & (df_indicators['date'] <= ed)
            ]['date'].unique()
        )
        if trading_dates:
            log(f"  Trading Dates: {len(trading_dates)} days ({trading_dates[0]} ~ {trading_dates[-1]})")
        else:
            log(f"  Trading Dates: 0 days")
            
        log(f"  Cache load & slicing completed in {time.time()-t0:.2f}s successfully.\n")
        return df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates
        
    except Exception as e:
        log(f"  Critical Error: Failed to preload Parquet cache: {e}")
        raise e
_GROUPBY_CACHE = {}
_GROUPBY_CACHE_MAX = 30


def get_groupby_cache(df, col_name='date'):
    """
    Cache pandas groupby('date') dictionary to prevent memory allocation explosion
    and dramatic overhead reduction during Optuna optimization runs.

    【重要】キャッシュエントリは DataFrame への強参照を保持する。

    キーに `id(df)` を使うが、`id()` が一意なのは**生存中のオブジェクト間だけ**である。
    DataFrame が GC されると CPython は同じアドレスを次の同サイズ確保に即座に再利用するため、
    強参照を持たないと「解放済み DataFrame の id」と「新しい DataFrame の id」が一致し、
    行数・列数も同じなら**別データの groupby 結果が黙って返る**。
    実測: 同形状の DataFrame 3,000 個を生成・破棄すると、3,000 個すべてが同一キーになった。

    これは 2026-07-29 に `backend/tests/backtest/test_backtest_runner_entry.py` の複数テストが
    間欠的に失敗する（トレード数が合わない）現象として顕在化した。テストだけの問題ではなく、
    DataFrame の生成・破棄を繰り返す Optuna 最適化で**誤ったシグナルが静かに混入**しうる。

    対策: エントリに DataFrame 自身を持たせて生存を保証し（＝id の再利用を防ぎ）、
    取り出し時に `is` で同一性を検証する。
    """
    global _GROUPBY_CACHE
    meta_key = (id(df), len(df), len(df.columns), col_name)

    entry = _GROUPBY_CACHE.get(meta_key)
    if entry is not None and entry[0] is df:
        return entry[1]

    # Prevent cache leak over multiple optimization scenarios
    if len(_GROUPBY_CACHE) >= _GROUPBY_CACHE_MAX:
        _GROUPBY_CACHE.clear()

    grouped = {d: group for d, group in df.groupby(col_name)}
    _GROUPBY_CACHE[meta_key] = (df, grouped)
    return grouped


def clear_groupby_cache():
    """groupby キャッシュを明示的に破棄する（テスト・長時間バッチの区切り用）。"""
    _GROUPBY_CACHE.clear()


def count_signal_episodes(trading_dates: list, signals_by_date: dict) -> int:
    """生シグナル（`scan_signals_for_date` の日次結果）を「エピソード数」へ合算する。

    同一銘柄が連続営業日で戦略の全条件を満たし続けても1エピソードとしてカウントする
    （間が空けば新規エピソード）。fast_prune のハード境界（min/max_avg_hits_per_day）が
    「実トレード数」に近い量を見られるようにするための近似（2026-07-23 追加）。

    背景: 状態が何日も持続するタイプの戦略（RSトレンド・テーマモメンタム等）は、
    Phase 2 のトレードシミュレーション（再エントリー禁止ロジック適用後）で数えられる
    `total_trades` よりも、Phase 1 の生シグナル数の方がずっと大きくなりがちで、
    ハードプルーニングだけがこの食い違いの影響を受けていた。厳密な重複排除には
    出口シミュレーション（実際の保有日数）が必要だが、それでは fast_prune の
    「シミュレーションを回さず安く弾く」という速度上の役割が失われる。連続日数の合算は
    追加のマジックナンバー（クールダウン日数等）を持ち込まずに近似する方法。
    """
    total_episodes = 0
    prev_symbols = set()
    for td in trading_dates:
        signals = signals_by_date.get(td)
        if not signals:
            prev_symbols = set()
            continue
        current_symbols = {s.symbol_id for s in signals}
        total_episodes += len(current_symbols - prev_symbols)
        prev_symbols = current_symbols
    return total_episodes


def run_single_strategy(strat_dict: dict, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents, trading_dates, exit_rules, show_progress=True, fast_prune=False, prune_bounds=(1.0, 15.0, 5.0), consider_tax=0.0,
                        allow_reentry_during_hold=False, entry_mode="close"):
    """
    Run backtest for a single strategy.
    If fast_prune=True, it will pre-scan all signals and immediately return ("PRUNED", None)
    if the signal counts exceed the prune_bounds (min_avg, max_avg, min_hit_rate_pct).

    allow_reentry_during_hold: True で建玉存続中の再エントリーを許可する（旧挙動）。
        修正前後の比較レポート専用の内部引数で、TOML には公開しない。
        デフォルト False = 同一銘柄は exit 翌営業日まで新規シグナルをスキップ
        （急騰継続銘柄の連日シグナルで1つのムーブが複数トレードに水増しされ、
        トレード独立性と VCP 系の評価が壊れるため）。

    entry_mode: "close"（シグナル当日終値。24時間取引でのオーバーナイト発注運用と整合）
        または "next_open"（シグナル翌営業日の寄付価格。翌営業日データが無い場合は
        シグナル破棄）。exit 評価窓はどちらもシグナル翌営業日の終値から（不変）。
        両方式の成績差で「引け値で買えること自体がエッジか」の感度を計測する。
    """
    import dataclasses
    strat_name = strat_dict.get('name', 'Optuna_Strategy')
    strat_desc = strat_dict.get('description', '')
    if show_progress:
        print(f"Running strategy: {strat_name} ({strat_desc})", flush=True)
    
    t0 = time.time()
    
    # --- CRITICAL: Clear attrs to avoid deepcopy explosion during groupby/iteration ---
    df_indicators.attrs = {}
    df_prices.attrs = {}
    df_ranks.attrs = {}

    # --- Pre-build groupby caches (PP1) ---
    ind_day_cache = get_groupby_cache(df_indicators, 'date')
    price_day_cache = get_groupby_cache(df_prices, 'date')
    ranks_day_cache = get_groupby_cache(df_ranks, 'date')
    ranks_dates_sorted = sorted(ranks_day_cache.keys())
    
    # --- PHASE 1: Fast Pre-Scan ---
    signals_by_date = {}
    total_signals = 0
    days_with_signals = 0

    for i, td in enumerate(trading_dates):
        prev_date = trading_dates[i - 1] if i > 0 else None
        signals = scan_signals_for_date(
            target_date=td, df_ind=df_indicators, df_price=df_prices, df_ranks=df_ranks,
            df_symbols=df_symbols, df_theme_constituents=df_theme_constituents,
            strategy=strat_dict, prev_date=prev_date,
            ind_day_cache=ind_day_cache, price_day_cache=price_day_cache,
            ranks_day_cache=ranks_day_cache, ranks_dates_sorted=ranks_dates_sorted,
        )
        if signals:
            signals_by_date[td] = signals
            total_signals += len(signals)
            days_with_signals += 1

        if show_progress and (i + 1) % 100 == 0:
            print(f"  [Scan] Processed {i + 1}/{len(trading_dates)} days...", flush=True)

    total_days = len(trading_dates)
    if total_days > 0:
        # 2026-07-23: avg_per_day はエピソード数（連続日数を合算した近似実トレード数）ベースに変更。
        # 生シグナル数(total_signals)のままだと、状態が持続する戦略でハードプルーニングが
        # 実トレード数(スコア側が見ている値)より遥かに厳しく効いてしまう問題への対処。
        total_episodes = count_signal_episodes(trading_dates, signals_by_date)
        avg_per_day = total_episodes / total_days
        hit_rate_pct = (days_with_signals / total_days) * 100.0
    else:
        avg_per_day = 0
        hit_rate_pct = 0
        
    # --- PRUNING GATE ---
    if fast_prune:
        min_avg, max_avg, min_hit_rate = prune_bounds
        if avg_per_day < min_avg or avg_per_day > max_avg or hit_rate_pct < min_hit_rate:
            if show_progress:
                print(f"  [Pruned] avg={avg_per_day:.2f}, hit_days={hit_rate_pct:.1f}%", flush=True)
            return {"fast_pruned": True, "avg_per_day": avg_per_day, "hit_rate_pct": hit_rate_pct}, None

    # --- PP3: Pre-build per-symbol DataFrames for trade simulation ---
    # CRITICAL: Clear attrs before grouping to avoid deepcopy explosion of large cached dicts
    df_prices.attrs = {}
    df_indicators.attrs = {}
    df_ranks.attrs = {}

    needed_ids = set(s.symbol_id for sigs in signals_by_date.values() for s in sigs)
    if needed_ids:
        # Use filtered view and then group to avoid unnecessary copies
        df_prices_filtered = df_prices[df_prices['symbol_id'].isin(needed_ids)]
        df_ind_filtered = df_indicators[df_indicators['symbol_id'].isin(needed_ids)]
        
        price_by_sym = {sid: group for sid, group in df_prices_filtered.groupby('symbol_id')}
        ind_by_sym = {sid: group for sid, group in df_ind_filtered.groupby('symbol_id')}
    else:
        price_by_sym = {}
        ind_by_sym = {}

    # --- PHASE 2: Trade Simulation ---
    trades = []
    # look-ahead シミュレーションでトレードは即時完結するため、exit_date を記録して
    # 建玉存続中（entry < signal.date <= exit）の同一銘柄シグナルをスキップする。
    # 同日の重複シグナルも従来通り排除。
    active_until = {}  # symbol_id -> 直近トレードの exit_date

    for i, td in enumerate(trading_dates):
        signals = signals_by_date.get(td, [])
        daily_entered = set()  # Same-day duplicate prevention

        for signal in signals:
            if signal.symbol_id in daily_entered:
                continue
            if not allow_reentry_during_hold:
                held_until = active_until.get(signal.symbol_id)
                if held_until is not None and signal.date <= held_until:
                    continue
            df_price_sym = price_by_sym.get(signal.symbol_id, pd.DataFrame())
            df_ind_sym = ind_by_sym.get(signal.symbol_id, pd.DataFrame())

            if entry_mode == "next_open":
                # シグナル翌営業日の寄付価格をエントリー価格に差し替える
                future_px = df_price_sym[df_price_sym['date'] > signal.date]
                if future_px.empty:
                    continue
                next_open = future_px.sort_values('date').iloc[0]['open']
                if next_open is None or pd.isna(next_open) or next_open <= 0:
                    continue
                signal = dataclasses.replace(signal, entry_price=float(next_open))

            result = simulate_trade(signal, df_price_sym, df_ind_sym, exit_rules)
            if result:
                trades.append(result)
                daily_entered.add(signal.symbol_id)
                active_until[signal.symbol_id] = result.exit_date

        if show_progress and (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{total_days} days, {len(trades)} trades so far...", flush=True)

    # --- Inject SPY benchmark returns into each trade ---
    spy_period_return = 0.0
    spy_row = df_symbols[df_symbols['ticker'] == 'SPY']
    if not spy_row.empty:
        spy_id = spy_row.iloc[0]['id']
        df_spy = df_prices[df_prices['symbol_id'] == spy_id].set_index('date')['close']
        
        # Calculate full period SPY return
        if trading_dates:
            spy_start = df_spy.get(trading_dates[0])
            spy_end = df_spy.get(trading_dates[-1])
            if spy_start is not None and spy_end is not None and spy_start > 0:
                spy_period_return = (spy_end - spy_start) / spy_start
                
        # Inject per-trade SPY returns
        if trades:
            for t in trades:
                spy_entry = df_spy.get(t.entry_date)
                spy_exit = df_spy.get(t.exit_date)
                if spy_entry is not None and spy_exit is not None and spy_entry > 0:
                    t.spy_pnl_pct = (spy_exit - spy_entry) / spy_entry * 100.0
                else:
                    t.spy_pnl_pct = None

    elapsed = time.time() - t0
    metrics = calculate_metrics(
        trades, spy_period_return=spy_period_return, consider_tax=consider_tax,
        price_by_sym=price_by_sym, partial_ratio=exit_rules.partial_ratio,
    )
    if show_progress:
        print(f"  Completed: {len(trades)} trades in {elapsed:.1f}s", flush=True)
    
    return metrics, trades

def run_backtest(config: dict, strategy_filter: str = None, refresh_cache: bool = False, db_path_override: str = None,
                  data_source: str | None = None):
    # (Existing beginning part of run_backtest up to initialization)
    general = config.get('general', {})
    start_date = general.get('start_date', '2021-03-26')
    end_date = general.get('end_date', '2026-03-25')
    exit_rules = ExitRules.from_config(config)

    strategies = config.get('strategy', [])
    strategies = [normalize_strategy_keys(s) for s in strategies]
    if strategy_filter:
        # Support short codes (A, B, C1, C2...) and prefixes
        filtered = [s for s in strategies if s['name'] == strategy_filter or s['name'].startswith(strategy_filter + "_")]
        if not filtered:
            # Fallback mapping (same as optimization_runner)
            full_names = {
                'A': 'A_momentum_breakout',
                'B': 'B_theme_momentum',
                'C1': 'C1_rrg_leading_in',
                'C2': 'C2_rrg_improving_in',
                'D': 'D_ema21_pullback',
                'E': 'E_vcp',
                'F': 'F_elite_momentum97'
            }
            if strategy_filter in full_names:
                target_name = full_names[strategy_filter]
                filtered = [s for s in strategies if s['name'] == target_name]
            
        if not filtered:
            print(f"Strategy '{strategy_filter}' not found in config.")
            return
        strategies = filtered

    if db_path_override:
        db_path = db_path_override
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
    else:
        # パス解決は paths.py に一任する（--db-path での明示指定は上で優先済み）。
        import paths
        db_path = paths.get_db_path("stocktool")


    print(f"  Connecting to DB: {db_path}")
    init_db(db_path)

    df_symbols, df_prices, df_indicators, df_ranks, df_theme_constituents, trading_dates = \
        preload_data(database.engine, start_date, end_date, refresh_cache=refresh_cache, data_source=data_source)

    # Validate configuration parameters（未知キーはエラー。CLI 経路は §1.3 の先行事例に倣い停止する）
    validation_errors = validate_strategies_config(strategies, df_indicators, df_prices, df_ranks, df_theme_constituents)
    if validation_errors:
        print_validation_warnings(validation_errors)
        raise ValueError(
            "Unknown filter keys found in strategies config:\n"
            + "\n".join(f"  - {e}" for e in validation_errors)
        )

    # Calculate VXV/VIX Ratio (using ^VIX3M and ^VIX)
    vxv_vix_series = {}
    vix_row = df_symbols[df_symbols['ticker'] == '^VIX']
    vix3m_row = df_symbols[df_symbols['ticker'] == '^VIX3M']
    if not vix_row.empty and not vix3m_row.empty:
        vix_id = vix_row.iloc[0]['id']
        vix3m_id = vix3m_row.iloc[0]['id']
        
        vix_prices = df_prices[df_prices['symbol_id'] == vix_id].set_index('date')['close']
        vix3m_prices = df_prices[df_prices['symbol_id'] == vix3m_id].set_index('date')['close']
        
        # Merge prices on date and calculate ratio
        merged_vix = pd.DataFrame({'vix': vix_prices, 'vix3m': vix3m_prices}).dropna()
        if not merged_vix.empty:
            merged_vix['ratio'] = merged_vix['vix3m'] / merged_vix['vix']
            vxv_vix_series = merged_vix['ratio'].to_dict()
            print(f"  Calculated VXV/VIX (VIX3M/VIX) ratio for {len(vxv_vix_series)} dates.")
        else:
            print("  Warning: No overlapping date records found between ^VIX and ^VIX3M.")
    else:
        missing = []
        if vix_row.empty: missing.append("^VIX")
        if vix3m_row.empty: missing.append("^VIX3M")
        print(f"  Warning: VXV/VIX Ratio cannot be calculated because {', '.join(missing)} symbol is missing in database.")
    
    exit_rules.vxv_vix_series = vxv_vix_series

    all_results = {}
    all_trades = {}

    # 型1（screening backtest）経路: consider_tax_optimization を読む（型2/型3の consider_tax とは分離）
    consider_tax = load_tax_rate(config, for_optimization=True)
    if consider_tax > 0.0:
        print(f"  [Tax] 適用税率: {consider_tax * 100:.1f}% (consider_tax_optimization={consider_tax})")
    else:
        print("  [Tax] 税なし (consider_tax_optimization=0.0)")
    entry_mode = config.get('general', {}).get('entry_mode', 'close')
    # 流動性ハード制約（最適化対象外・全戦略共通。戦略側の明示指定があればそちらを優先）
    liquidity_floor = load_min_avg_dollar_volume_21(config)
    from backend.indicators import screener_registry
    for strat in strategies:
        strat_name = strat.get('name', 'Strategy')
        strat = inject_liquidity_floor(strat, liquidity_floor)
        # applied_filters: 実際に適用されるフィルタキー一覧を実行開始時に1行だけログ出力する
        # （流動性床がサイレントに無効化されていた事故＝計画書 §7 P1-8 は、これがあれば
        # 結果を見た瞬間に発覚していた。日次ループの中では出さない）
        applied_filters = sorted(k for k in strat if not screener_registry.is_non_filter_key(k))
        print(f"  [{strat_name}] applied filters: {applied_filters}")
        metrics, trades = run_single_strategy(
            strat, df_indicators, df_prices, df_ranks, df_symbols, df_theme_constituents,
            trading_dates, exit_rules, show_progress=True, consider_tax=consider_tax,
            entry_mode=entry_mode
        )
        all_results[strat_name] = metrics
        all_trades[strat_name] = trades

    # Print comparison table
    print_comparison_table(all_results, start_date, end_date)

    # Save results
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
    save_results_json(all_results, all_trades, results_dir, start_date, end_date,
                       data_source_meta=_last_preload_meta)


def print_validation_warnings(warnings: list):
    """Print validation warnings in a prominent visual block."""
    if not warnings:
        return
    print()
    print("=" * 75)
    print("WARNING: Invalid or unsupported strategy parameters detected in config!")
    print("=" * 75)
    for w in warnings:
        print(f"  - {w}")
    print("=" * 75)
    print()


def validate_strategies_config(strategies: list, df_ind: pd.DataFrame, df_prices: pd.DataFrame, df_ranks: pd.DataFrame = None, df_theme_constituents: pd.DataFrame = None) -> list:
    """戦略設定の各フィルタキーがレジストリで解決できるかを検証する（純関数）。

    返すのは「警告」ではなく「エラー」として扱う（呼び出し側で空でなければ ValueError に変換して
    停止する。doc/completed/screener_filter_unification_plan.md §3.1.3 の U-1 決定）。
    df_ranks は wide 形式（SQLite と同一スキーマ）のまま渡されるが、ここではモデル定義を
    権威として使うため df_ranks 自体は参照しない。RelativeRank モデルの実カラムから
    ランク列集合を組み立てる。

    **カラム集合の権威はモデル定義**であり、引数の DataFrame は補助（派生列の追加）でしかない。
    これは**この関数がスキーマ検証（キー名が妥当か）を担うため**で、
    「実際に供給されているか」の検証は適用時の `MissingFilterColumnError` の責務。

    引数を DataFrame の列だけに依存させると、**空の DataFrame を渡す呼び出し側で
    全キーが「未知」になる**（`optimization_runner.py` が `pd.DataFrame()` を渡しており、
    2026-08-14 に本番で90件の誤検知として顕在化した。計画書 §7 P2-2）。
    """
    from backend.db.models import RelativeRank, Indicator, DailyPrice
    from backend.backtest.strategy_normalizer import normalize_strategy_keys
    from backend.indicators import screener_registry

    strategies = [normalize_strategy_keys(s) for s in strategies]

    # モデル定義（権威） + 渡された DataFrame の列（派生列などの補助） + 仮想カラム
    known_columns = (
        {c for c in Indicator.__table__.columns.keys()}
        | {c for c in DailyPrice.__table__.columns.keys()}
        | set(getattr(df_ind, 'columns', ()))
        | set(getattr(df_prices, 'columns', ()))
        | set(screener_registry.VIRTUAL_COLUMNS)
    )
    rank_columns = {
        col for col in RelativeRank.__table__.columns.keys()
        if col not in ('id', 'symbol_id', 'date', 'group_name')
    }

    errors = []

    for strat in strategies:
        strat_name = strat.get('name', 'Unknown')

        # 制御キー（METADATA_KEYS）と特殊フィルタの随伴パラメータ（ATTACHED_PARAM_KEYS。
        # is_vcp_breakout の pivot_tol 等）は、フィルタキーとして解決を試みない
        filter_keys = {k for k in strat if not screener_registry.is_non_filter_key(k)}
        # [strategy.optimization] サブ dict のキーも検証対象に含める
        opt = strat.get('optimization')
        if isinstance(opt, dict):
            filter_keys |= {k for k in opt if not screener_registry.is_non_filter_key(k)}

        for key in filter_keys:
            try:
                screener_registry.resolve_filter_spec(key, known_columns, rank_columns)
            except screener_registry.UnknownFilterKeyError:
                errors.append(f"Strategy '{strat_name}': Unknown parameter '{key}'.")

    return errors


def main():
    parser = argparse.ArgumentParser(description='Backtest Engine Runner')
    parser.add_argument('--config', type=str, default=None,
                        help='Path to backtest config TOML file')
    parser.add_argument('--strategy', type=str, default=None,
                        help='Run only a specific strategy by name')
    parser.add_argument('--start-date', type=str, default=None,
                        help='Override start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, default=None,
                        help='Override end date (YYYY-MM-DD)')
    parser.add_argument('--refresh-cache', action='store_true',
                        help='Force reload data from DB and refresh Parquet cache')
    parser.add_argument('--data-source', type=str, default=None,
                        help="バックテストが読む Parquet マスタの参照先。"
                             "'backup'（検証済みの最新バックアップ・常に本番）/ "
                             "'latest'（現在の data ディレクトリの最新世代）/ "
                             "バックアップのフォルダ名（例 '_bk_20260925_...')。"
                             "省略時は本番なら backup、それ以外（ワークツリー・sandbox）なら latest。")
    parser.add_argument('--db-path', type=str, default=None,
                        help='Path to a specific SQLite DB file to use for this run')
    parser.add_argument('--delete-db-after', action='store_true',
                        help='Delete the database file specified in --db-path after completion')
    args = parser.parse_args()

    # Default config path
    if args.config:
        config_path = args.config
    else:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'backtest_config.toml'
        )

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    config = load_config(config_path)
    
    # Overrides from CLI
    if args.start_date:
        config['general']['start_date'] = args.start_date
    if args.end_date:
        config['general']['end_date'] = args.end_date

    print("=" * 60)
    print("  Stock Backtest Engine")
    print("=" * 60)
    print(f"  Config: {config_path}")
    print(f"  Period: {config['general']['start_date']} ~ {config['general']['end_date']}")
    print(f"  Strategies: {len(config.get('strategy', []))}")
    if args.strategy:
        print(f"  Filter: {args.strategy}")
    if args.refresh_cache:
        print(f"  Cache: REFRESHING")
    else:
        print(f"  Cache: AUTO (use Parquet if available)")
    print("=" * 60)
    print()

    # --db-path を明示指定したのに data_source が既定の "backup"（本番なら常に本番の
    # バックアップ）になると、指定した DB の内容が無視されて黙って本番を読む（R3）。
    # --db-path 明示かつ --data-source 未指定なら "latest" を既定にする。
    effective_data_source = args.data_source
    if args.db_path and effective_data_source is None:
        effective_data_source = "latest"
        print(f"  --db-path 指定のため data_source の既定を 'latest' にします"
              f"（{args.db_path} の内容を使うため。'backup' のままだと本番バックアップを読んでしまう）。")

    run_backtest(
        config,
        strategy_filter=args.strategy,
        refresh_cache=args.refresh_cache,
        db_path_override=args.db_path,
        data_source=effective_data_source
    )

    # Optional cleanup
    if args.delete_db_after and args.db_path:
        if os.path.exists(args.db_path):
            print(f"\nCleaning up temporary database: {args.db_path}")
            # Close engine connection first to unlock the file
            database.engine.dispose()
            try:
                os.remove(args.db_path)
            except Exception as e:
                print(f"Warning: Failed to delete DB file: {e}")


if __name__ == '__main__':
    main()
