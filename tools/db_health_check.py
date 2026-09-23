import sqlite3
import pandas as pd
import argparse
import os
import sys
from datetime import datetime

# プロジェクトルートと backend/ をパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (project_root, os.path.join(project_root, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import paths
from indicators.incremental_state_registry import (
    columns_with_warmup_threshold,
    is_structurally_null_column,
    max_lookback,
    recursive_column_names,
)

# `__file__` 起点で `data/stocktool.db` を組み立てると、ワークツリーでは
# 存在しない DB を指し、`sqlite3.connect()` が**0 バイトの空DBを黙って作る**。
# その状態でヘルスチェックを回すと「異常なし」に見えてしまうため paths.py に一任する。
DB_PATH = paths.get_db_path("stocktool")

# --check-warmup-nulls が銘柄の「真の履歴本数」を読みに行く Parquet マスタのディレクトリ
# （T3増分化計画 5-15d・2回目のcode-review指摘1）。DB_PATH と同様にモジュール変数として
# 持ち、`--db-path` 指定時や単体テストから `check_symbol_health(parquet_dir=...)` /
# モジュール属性の書き換えで差し替えられるようにする。
PARQUET_DIR = paths.get_parquet_master_dir()

CRITICAL_COLUMNS = [
    'sma_200', 'ema_21', 'rs_value', 'rs_ratio_e21', 'rs_momentum_e21'
]

# 「完全な履歴を持ちながら SPY より遅れているだけ」を NG と区別するための下限行数。
# これ以下は履歴そのものが足りない＝要調査（NG）扱いにする。
# weekly_maintenance.classify_symbol_freshness の LOW_HISTORY_ROWS と同じ意図。
LOW_HISTORY_ROWS = 20

# --check-nulls が見る「直近N行」の N。SQL の LIMIT と短履歴除外の閾値計算
# （t2_count <= warmup_bars + N）で同じ値を共有し、両者が食い違わないようにする。
RECENT_NULL_WINDOW = 5

def get_connection():
    return sqlite3.connect(DB_PATH)


def _load_parquet_history_counts(parquet_dir: str) -> dict:
    """Parquetマスタ（コールドの全履歴）の `prices` から、銘柄ごとの真の価格履歴本数を読む
    （T3増分化計画 5-15d・2回目のcode-review指摘1）。

    `--check-warmup-nulls` は「演算上必要な日数（warmup_bars）がある銘柄なら、その列は
    NULLにならない」という一般則を検査する。5-6c の初版実装は比較対象にホットキャッシュ
    （SQLite の `indicators` テーブル行数。実測最大503）を使っていたが、これは
    `rs_roc_ema_200`（warmup_bars=511）・`rs_momentum_e200`（同610）に対して構造的に
    到達不能だった——**この2列こそが本計画の発端となった不具合（§1.1）の当事者**であり、
    到達不能なせいで実際の欠陥が「異常なし」と誤って報告されていた（2回目のcode-review指摘1）。

    銘柄の真の履歴本数はコールドマスタ（Parquet）にしかない。`symbol_id` 列だけを選択して
    読むため、全履歴（本番実測で684万行）でも軽量（列指向フォーマットの利点。実測1.9秒）。

    `latest_master.json` が見つからない・`prices` が読めない場合は空 dict を返す。
    呼び出し側は空dictなら `t3_count`（ホットキャッシュ行数）へフォールバックする
    （比較が保守的になる＝旧来どおり到達不能な列が出うるだけで、検査自体は落ちない）。
    """
    pointer_file = os.path.join(parquet_dir, "latest_master.json")
    if not os.path.exists(pointer_file):
        return {}
    try:
        import json
        with open(pointer_file, 'r', encoding='utf-8') as f:
            latest_files = json.load(f)
        prices_path = latest_files.get('prices')
        if not prices_path:
            return {}
        abs_path = os.path.join(parquet_dir, os.path.basename(prices_path))
        if not os.path.exists(abs_path):
            return {}
        df = pd.read_parquet(abs_path, columns=['symbol_id'])
        return df['symbol_id'].value_counts().to_dict()
    except Exception as e:
        print(f"⚠️  Parquet履歴本数の読み込みに失敗しました（--check-warmup-nulls は"
              f"t3_countで代替します）: {e}")
        return {}


def check_symbol_health(ticker: str = None, all_active: bool = False, check_nulls: bool = False,
                         check_recursive_state: bool = False, check_warmup_nulls: bool = False,
                         parquet_dir: str = None):
    """
    1. SPYの最新日と比較してT2（日足）が揃っているか確認
    2. T2の行数とT3（インジケーター）の行数が一致しているか確認
    3. (Optional) 特定カラムのNULLチェック
    4. (Optional) RECURSIVE型状態列（T3増分化計画）のNULLチェック
    5. (Optional) ウォームアップ本数超過NULLチェック（T3増分化計画5-6c・5-15d）。
       比較対象は銘柄の真の履歴本数（Parquetマスタ。無ければ t3_count にフォールバック）。
    """
    conn = get_connection()

    # RECURSIVE型列の一覧・必要遡り本数はレジストリから機械的に取得する（手書き禁止）。
    # --check-recursive-state 指定時のみ使う（未指定時は従来どおりレジストリに触れない）。
    recursive_cols = recursive_column_names() if check_recursive_state else ()
    recursive_lookback = max_lookback() if check_recursive_state else 0

    # ウォームアップ本数（列ごとに異なる閾値）もレジストリから機械的に取得する。
    # --check-recursive-state（増分計算の「状態」が壊れていないか）とは目的が異なり、
    # こちらは「指標そのものが出るべき値を出しているか」（銘柄の真の履歴本数が演算上
    # 必要な本数を超えているのに最新行がNULLになっていないか）を見る（5-6c・ユーザー提案）。
    warmup_thresholds = columns_with_warmup_threshold() if check_warmup_nulls else {}
    # 比較対象は銘柄の真の履歴本数（Parquetマスタ由来）。ホットキャッシュの行数
    # （t3_count。実測最大503）は rs_roc_ema_200（warmup_bars=511）等に対して
    # 構造的に到達不能だったため、5-15d でParquetから読むよう変更した
    # （2回目のcode-review指摘1。詳細は `_load_parquet_history_counts` docstring）。
    parquet_history_counts = (
        _load_parquet_history_counts(parquet_dir or PARQUET_DIR) if check_warmup_nulls else {}
    )

    # SPYの最新日を取得
    spy_latest = conn.execute("SELECT max(date) FROM daily_prices WHERE symbol_id = (SELECT id FROM symbols WHERE ticker='SPY')").fetchone()[0]
    if not spy_latest:
        print("Error: SPY data not found in daily_prices.")
        return ['<SPY_MISSING>']  # SPY 欠損は NG 扱い（restore 失敗の典型症状）
    
    print(f"基準日 (SPY Latest): {spy_latest}")
    print("-" * 60)
    
    # 対象銘柄のリストアップ
    if all_active:
        symbols = pd.read_sql("SELECT id, ticker FROM symbols WHERE active = 1", conn)
    elif ticker:
        symbols = pd.read_sql(f"SELECT id, ticker FROM symbols WHERE ticker = '{ticker}'", conn)
    else:
        print("Usage: Specify --ticker or --all")
        return

    results = []
    for _, sym in symbols.iterrows():
        sym_id = sym['id']
        t = sym['ticker']
        
        # 1. FX/為替レート (=Xなど) のスキップ
        if "=X" in t or t == "JPY=X":
            res = {
                "Symbol": t,
                "Status": "OK",
                "T2_Count": 0,
                "T3_Count": 0,
                "T2_Latest": "FX (System)",
                "Synced": "Yes",
                "Consistent": "Yes",
                "Nulls": "None"
            }
            results.append(res)
            continue
            
        # T2 (DailyPrice) 情報
        t2_info = conn.execute(f"SELECT count(*), max(date) FROM daily_prices WHERE symbol_id = {sym_id}").fetchone()
        t2_count = t2_info[0]
        t2_latest = t2_info[1]
        
        # T3 (Indicator) 情報
        t3_count = conn.execute(f"SELECT count(*) FROM indicators WHERE symbol_id = {sym_id}").fetchone()[0]
        
        # 2. 空の仮想テーマの検知 (_BLCKB6_ など、銘柄削除により構成銘柄が0件になったもの)
        is_empty_virtual_theme = t.startswith('_') and t.endswith('_') and t2_count == 0
        
        # NULL チェック (オプション) - 直近5営業日にNULLがある場合のみ異常と判定（ウォームアップ期間による過去の正常なNULLは許容）
        null_info = {}
        if check_nulls and t3_count > 0 and not is_empty_virtual_theme:
            # 銘柄ごとのウォームアップ除外条件を定義
            exclude_cols = []
            if t == 'SPY':
                # SPY自身に対する相対強度は計算対象外のためNULLが正常
                exclude_cols.extend(['rs_value', 'rs_ratio_e21', 'rs_momentum_e21'])
            
            # データ期間が短い新規株はウォームアップ期間中のためNULLを許容する。
            # 閾値は手書きせず、レジストリの warmup_bars（銘柄の先頭から何本目(0始まり)で
            # 非NULLになるか）から導出する（min_periods_warmup 計画 5-22。手書き値は
            # 実測 warmup_bars とずれ、新規上場銘柄が誤って NG になっていた）。
            # この検査は「直近 RECENT_NULL_WINDOW 行に NULL があるか」を見るため、
            # 直近行がすべてウォームアップ明けになる t2_count > warmup_bars + RECENT_NULL_WINDOW
            # まで除外する（`<= warmup_bars` だと、境界付近で直近窓の古い側の行が
            # NULL のまま NG になる）。
            warmup_bars_by_col = columns_with_warmup_threshold()
            for col in CRITICAL_COLUMNS:
                warmup_bars = warmup_bars_by_col.get(col)
                if warmup_bars is not None and t2_count <= warmup_bars + RECENT_NULL_WINDOW:
                    exclude_cols.append(col)

            for col in CRITICAL_COLUMNS:
                if col in exclude_cols:
                    continue
                    
                # 直近5日間のデータを取得
                recent_nulls = conn.execute(f"""
                    SELECT count(*) FROM (
                        SELECT "{col}" FROM indicators 
                        WHERE symbol_id = {sym_id} 
                        ORDER BY date DESC LIMIT {RECENT_NULL_WINDOW}
                    ) WHERE "{col}" IS NULL
                """).fetchone()[0]
                if recent_nulls > 0:
                    null_info[col] = recent_nulls

        # RECURSIVE型状態列のNULLチェック (オプション・T3増分化計画5-6b)
        #
        # T3の増分計算（incremental_state_registry.py）は、直近K行（K=max_lookback()）の
        # RECURSIVE型列（ema_*/rs_value_e*/rs_roc_ema_*等）にNaNが無いことを前提にしている。
        # ここにNULLがある銘柄は増分経路が発動せず、日次T3が全期間再計算に
        # フォールバックし続ける（=不正確な値が書かれ続ける。t3_indicators.py参照）。
        # 「うるさいから閾値でごまかす」ことをしないため、SPYのRS系列（構造的にNULLが
        # 正常）以外の除外は行わない。除外ロジックは `_calculate_t3_worker`
        # （t3_indicators.py）とも共有するため `is_structurally_null_column` に
        # 集約している（5-15b）。
        recursive_null_cols = []
        if check_recursive_state and t3_count > 0 and not is_empty_virtual_theme:
            cols_to_check = [c for c in recursive_cols if not is_structurally_null_column(t, c)]
            if cols_to_check:
                col_sql = ', '.join(f'"{c}"' for c in cols_to_check)
                rows = conn.execute(f"""
                    SELECT {col_sql} FROM indicators
                    WHERE symbol_id = {sym_id}
                    ORDER BY date DESC LIMIT {recursive_lookback}
                """).fetchall()
                for idx, col in enumerate(cols_to_check):
                    if any(row[idx] is None for row in rows):
                        recursive_null_cols.append(col)

        # ウォームアップ本数超過NULLチェック (オプション・T3増分化計画5-6c・ユーザー提案)
        #
        # 「演算上必要な日数がある銘柄なら、その列は NULL にならない」という一般則の検査。
        # 銘柄の真の履歴本数（Parquetマスタ。無ければ t3_count にフォールバック）が
        # 列ごとの warmup_bars（レジストリ実測値）を超えているのに最新行がNULLの列を
        # 検出する。上の --check-recursive-state（増分計算の「状態」が壊れていないか）
        # とは目的が異なり、こちらは「指標そのものが出るべき値を出しているか」を見る。
        # イベント駆動で閾値を置けない列（sp_pivot/sp_hl/sp_counter）は
        # columns_with_warmup_threshold() が機械的に除外している。
        #
        # 比較対象を t3_count（ホットキャッシュ行数。実測最大503）のままにしていると、
        # warmup_bars が503を超える列（rs_roc_ema_200=511・rs_momentum_e200=610）には
        # 条件が永久に成立せず、この検査自体が本不具合の当事者に到達できなかった
        # （5-15d・2回目のcode-review指摘1）。
        warmup_null_cols = []
        if check_warmup_nulls and t3_count > 0 and not is_empty_virtual_theme:
            true_history_count = parquet_history_counts.get(sym_id, t3_count)
            cols_to_check = {
                c: w for c, w in warmup_thresholds.items()
                if not is_structurally_null_column(t, c)
            }
            if cols_to_check:
                col_sql = ', '.join(f'"{c}"' for c in cols_to_check)
                latest_row = conn.execute(f"""
                    SELECT {col_sql} FROM indicators
                    WHERE symbol_id = {sym_id}
                    ORDER BY date DESC LIMIT 1
                """).fetchone()
                if latest_row:
                    for idx, (col, warmup_bars) in enumerate(cols_to_check.items()):
                        if true_history_count > warmup_bars and latest_row[idx] is None:
                            warmup_null_cols.append(col)

        # 判定
        is_synced = (t2_latest >= spy_latest) if t2_latest else False
        is_consistent = (t2_count == t3_count)
        has_nulls = len(null_info) > 0 or len(recursive_null_cols) > 0 or len(warmup_null_cols) > 0

        if is_empty_virtual_theme:
            status = "NG"
            synced_str = "No"
            consistent_str = "Yes"
            null_str = "Constituent count is 0"
        else:
            synced_str = "Yes" if is_synced else "No"
            consistent_str = "Yes" if is_consistent else "No (Diff: {})".format(t2_count - t3_count)
            null_str = null_info if len(null_info) > 0 else "None"

            # NG と STALE を分ける。
            #
            # 上流(Yahoo)が銘柄レコードを作り直して系列を切り落とすと、その銘柄は
            # **恒久的に SPY へ追いつけない**（2026-08-02 に17銘柄で発生）。
            # これを NG のまま扱うと常時 NG が20件以上出続け、本当に対応が要る
            # 「T2/T3 件数不一致」「NULL 混入」が埋もれる。
            #
            # 判定は「こちらで直せるか」で切る:
            #   NG    … 件数不一致・NULL 混入・履歴不足 → こちら側の問題。要対応
            #   STALE … 完全な履歴があり最新日だけ遅れている → 上流の供給状況。対応不能
            # 詳細と切り分け手順: .claude/skills/upstream-data-diagnosis/SKILL.md
            if is_consistent and not has_nulls and not is_synced and t2_count > LOW_HISTORY_ROWS:
                status = "STALE"
            else:
                status = "OK" if is_synced and is_consistent and not has_nulls else "NG"

        res = {
            "Symbol": t,
            "Status": status,
            "T2_Count": t2_count,
            "T3_Count": t3_count,
            "T2_Latest": t2_latest,
            "Synced": synced_str,
            "Consistent": consistent_str,
            "Nulls": null_str,
            "RecursiveNulls": recursive_null_cols if recursive_null_cols else "None",
            "WarmupNulls": warmup_null_cols if warmup_null_cols else "None",
        }
        results.append(res)

        if not all_active or status in ("NG", "STALE"):
            print(f"[{t}] Status: {status}")
            if is_empty_virtual_theme:
                print(f"  - ⚠️ WARNING: Virtual theme index has no constituent symbols. Check spreadsheet theme tags.")
            else:
                print(f"  - T2最新日: {t2_latest} (同期: {res['Synced']})")
                print(f"  - 行数一致: {res['Consistent']} (T2:{t2_count}, T3:{t3_count})")
                if len(null_info) > 0:
                    print(f"  - NULL検知: {null_info}")
                if recursive_null_cols:
                    print(f"  - RECURSIVE状態列NULL検知（T3増分化計画5-6b。--rebuild-from T3推奨）: {recursive_null_cols}")
                if warmup_null_cols:
                    print(f"  - ウォームアップ超過NULL検知（T3増分化計画5-6c。--rebuild-from T3推奨）: {warmup_null_cols}")

    conn.close()

    # STALE は上流起因で恒久的に解消しないため NG リストに含めない。
    # 含めると deploy_after_merge のベースライン比較が常時「NG 20件超」になり、
    # 昇格で新たに壊れた銘柄との差分が読めなくなる。
    ng_tickers = [r['Symbol'] for r in results if r['Status'] == 'NG']
    stale_tickers = [r['Symbol'] for r in results if r['Status'] == 'STALE']

    if all_active:
        df_res = pd.DataFrame(results)
        ng_count = len(ng_tickers)
        print("-" * 60)
        print(f"スキャン完了: 全{len(df_res)}銘柄中、要対応 {ng_count}銘柄 / "
              f"上流待ち {len(stale_tickers)}銘柄。")
        if ng_count > 0:
            print("\n要対応（NG）— 件数不一致・NULL 混入・履歴不足（上位10件）:")
            print(df_res[df_res['Status'] == 'NG'].head(10))
        if stale_tickers:
            print(f"\n上流待ち（STALE）— 履歴は完全だが最新日が SPY に届いていない {len(stale_tickers)}件:")
            print(f"  {', '.join(sorted(stale_tickers))}")
            print("  上流が系列を切り落とした銘柄はこちらでは解消できません。")
            print("  切り分け手順: .claude/skills/upstream-data-diagnosis/SKILL.md")

    return ng_tickers

def check_parquet_health(parquet_dir_override: str = None):
    """
    Parquetマスタのデータ件数、最新日付、カラムのデータ型をチェックする。
    特にID列（symbol_id, theme_id）が文字列型(object)に汚染されていないかを検出する。
    """
    print("-" * 60)
    print("📋 PARQUET MASTER CACHE HEALTH CHECK")
    print("-" * 60)

    # Parquet の所在は paths.py に一任する（config.toml の直読みをやめる）。
    parquet_dir = parquet_dir_override or paths.get_parquet_master_dir()
    pointer_file = os.path.join(parquet_dir, "latest_master.json")
    
    if not os.path.exists(pointer_file):
        print("❌ Warning: Parquet pointer file (latest_master.json) not found.")
        print(f"   Expected path: {pointer_file}")
        return
        
    try:
        import json
        with open(pointer_file, 'r', encoding='utf-8') as f:
            latest_files = json.load(f)
    except Exception as e:
        print(f"❌ Error reading Parquet pointer: {e}")
        return
        
    print(f"Parquetマスタディレクトリ: {parquet_dir}")
    
    for key, path in latest_files.items():
        abs_path = os.path.join(parquet_dir, os.path.basename(path))
        if not os.path.exists(abs_path):
            print(f"❌ File not found: {abs_path}")
            continue
            
        file_size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        
        try:
            df = pd.read_parquet(abs_path)
            row_count = len(df)
            
            # 日付情報
            date_info = ""
            if 'date' in df.columns:
                min_date = df['date'].min()
                max_date = df['date'].max()
                date_info = f" ({min_date} ~ {max_date})"
                
            print(f"  • {key.upper():<10}: {row_count:>7} rows | Size: {file_size_mb:5.2f} MB{date_info}")
            
            # IDの型チェック
            id_cols = [c for c in ['symbol_id', 'theme_id', 'id'] if c in df.columns]
            for col in id_cols:
                dtype = df[col].dtype
                # もし object (文字列型) の場合は警告を表示
                if dtype == 'object' or str(dtype).startswith('str'):
                    print(f"    ⚠️  WARNING: Column '{col}' in '{key}' is type '{dtype}' (string/object)! It should be integer.")
                else:
                    print(f"    ✓ Column '{col}' is valid type '{dtype}' (numeric)")
        except Exception as e:
            print(f"❌ Error inspecting parquet {key}: {e}")
            
    print("-" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Database & Parquet Health Check Tool')
    parser.add_argument('--ticker', type=str, help='Ticker to check')
    parser.add_argument('--all', action='store_true', help='Check all active symbols')
    parser.add_argument('--check-nulls', action='store_true', help='Include NULL checks for critical columns in T3')
    parser.add_argument('--check-recursive-state', action='store_true',
                         help='RECURSIVE型状態列（ema_*/rs_value_e*/rs_roc_ema_*等）の直近保存行にNULLがないか検出する'
                              '（T3増分化計画5-6b）。該当銘柄は日次T3の増分経路が発動せず不正確な値が書かれ続ける。'
                              '--rebuild-from T3 でのリフレッシュ前は大量検出するのが正常な挙動')
    parser.add_argument('--check-warmup-nulls', action='store_true',
                         help='銘柄の真の履歴本数（Parquetマスタ由来。無ければt3_countにフォールバック）が'
                              '列ごとの演算上必要な本数（warmup_bars。レジストリ実測値）を'
                              '超えているのに最新行がNULLの列を検出する（T3増分化計画5-6c・5-15d・ユーザー提案）。'
                              '--check-recursive-state（増分計算の状態チェック）とは目的が異なり、'
                              '指標そのものが出るべき値を出しているかを見る。'
                              'sp_pivot/sp_hl/sp_counterはイベント駆動のため対象外')
    parser.add_argument('--parquet', action='store_true', default=True, help='Check Parquet Master Cache health')
    parser.add_argument('--db-path', type=str, help='Check a specific SQLite DB (default: data/stocktool.db). Parquet dir is resolved next to it.')
    parser.add_argument('--ng-out', type=str, help='NG 銘柄ティッカーを1行1件で書き出すファイルパス（deploy_after_merge のベースライン比較用）')
    args = parser.parse_args()

    # --db-path 指定時は検査対象 DB と Parquet ディレクトリを差し替える
    # （deploy_after_merge のワークスペース検証・模擬昇格テストで使用）
    parquet_dir_override = None
    if args.db_path:
        DB_PATH = os.path.abspath(args.db_path)
        parquet_dir_override = os.path.join(os.path.dirname(DB_PATH), "parquet_master")

    if args.parquet:
        check_parquet_health(parquet_dir_override)

    if args.ticker or args.all:
        ng = check_symbol_health(ticker=args.ticker, all_active=args.all, check_nulls=args.check_nulls,
                                  check_recursive_state=args.check_recursive_state,
                                  check_warmup_nulls=args.check_warmup_nulls,
                                  parquet_dir=parquet_dir_override)
    else:
        # Default behavior: if no symbol arguments, check SPY as standard check
        ng = check_symbol_health(ticker='SPY', all_active=False, check_nulls=args.check_nulls,
                                  check_recursive_state=args.check_recursive_state,
                                  check_warmup_nulls=args.check_warmup_nulls,
                                  parquet_dir=parquet_dir_override)

    # NG 銘柄リストの書き出し（BOM なし UTF-8。NG ゼロでも空ファイルを書き「実行済み」を示す）
    if args.ng_out:
        with open(args.ng_out, 'w', encoding='utf-8', newline='\n') as f:
            for t in ng:
                f.write(f"{t}\n")

    # NG があれば非ゼロ終了（deploy_after_merge の合否ゲート）
    sys.exit(1 if ng else 0)
