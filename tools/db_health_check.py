import sqlite3
import pandas as pd
import argparse
import os
import sys
from datetime import datetime

# プロジェクトルートをパスに追加
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

DB_PATH = os.path.join(project_root, "data", "stocktool.db")

CRITICAL_COLUMNS = [
    'sma_200', 'ema_21', 'rs_value', 'rs_ratio_e21', 'rs_momentum_e21'
]

def get_connection():
    return sqlite3.connect(DB_PATH)

def check_symbol_health(ticker: str = None, all_active: bool = False, check_nulls: bool = False):
    """
    1. SPYの最新日と比較してT2（日足）が揃っているか確認
    2. T2の行数とT3（インジケーター）の行数が一致しているか確認
    3. (Optional) 特定カラムのNULLチェック
    """
    conn = get_connection()
    
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
            
            # データ期間が短い新規株はウォームアップ期間中のためNULLを許容
            if t2_count < 75:
                exclude_cols.append('rs_momentum_e21')
            if t2_count < 30:
                exclude_cols.append('rs_ratio_e21')
            if t2_count < 21:
                exclude_cols.append('ema_21')
                
            for col in CRITICAL_COLUMNS:
                if col in exclude_cols:
                    continue
                    
                # 直近5日間のデータを取得
                recent_nulls = conn.execute(f"""
                    SELECT count(*) FROM (
                        SELECT "{col}" FROM indicators 
                        WHERE symbol_id = {sym_id} 
                        ORDER BY date DESC LIMIT 5
                    ) WHERE "{col}" IS NULL
                """).fetchone()[0]
                if recent_nulls > 0:
                    null_info[col] = recent_nulls

        # 判定
        is_synced = (t2_latest >= spy_latest) if t2_latest else False
        is_consistent = (t2_count == t3_count)
        has_nulls = len(null_info) > 0
        
        if is_empty_virtual_theme:
            status = "NG"
            synced_str = "No"
            consistent_str = "Yes"
            null_str = "Constituent count is 0"
        else:
            status = "OK" if is_synced and is_consistent and not has_nulls else "NG"
            synced_str = "Yes" if is_synced else "No"
            consistent_str = "Yes" if is_consistent else "No (Diff: {})".format(t2_count - t3_count)
            null_str = null_info if has_nulls else "None"
        
        res = {
            "Symbol": t,
            "Status": status,
            "T2_Count": t2_count,
            "T3_Count": t3_count,
            "T2_Latest": t2_latest,
            "Synced": synced_str,
            "Consistent": consistent_str,
            "Nulls": null_str
        }
        results.append(res)
        
        if not all_active or status == "NG":
            print(f"[{t}] Status: {status}")
            if is_empty_virtual_theme:
                print(f"  - ⚠️ WARNING: Virtual theme index has no constituent symbols. Check spreadsheet theme tags.")
            else:
                print(f"  - T2最新日: {t2_latest} (同期: {res['Synced']})")
                print(f"  - 行数一致: {res['Consistent']} (T2:{t2_count}, T3:{t3_count})")
                if has_nulls:
                    print(f"  - NULL検知: {null_info}")

    conn.close()

    ng_tickers = [r['Symbol'] for r in results if r['Status'] == 'NG']
    if all_active:
        df_res = pd.DataFrame(results)
        ng_count = len(df_res[df_res['Status'] == 'NG'])
        print("-" * 60)
        print(f"スキャン完了: 全{len(df_res)}銘柄中、{ng_count}銘柄に異常あり。")
        if ng_count > 0:
            print("\n異常あり銘柄リスト（上位10件）:")
            print(df_res[df_res['Status'] == 'NG'].head(10))
    # 呼び出し元（deploy_after_merge 等）が合否判定・差分比較できるよう NG 銘柄リストを返す
    return ng_tickers

def check_parquet_health(parquet_dir_override: str = None):
    """
    Parquetマスタのデータ件数、最新日付、カラムのデータ型をチェックする。
    特にID列（symbol_id, theme_id）が文字列型(object)に汚染されていないかを検出する。
    """
    print("-" * 60)
    print("📋 PARQUET MASTER CACHE HEALTH CHECK")
    print("-" * 60)

    # configからdb_pathを取得
    try:
        import tomllib
        config_path = os.path.join(project_root, "config.toml")
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
            db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
    except Exception:
        db_path = os.path.join(project_root, "data", "stocktool.db")

    parquet_dir = parquet_dir_override or os.path.join(os.path.dirname(db_path), "parquet_master")
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
        ng = check_symbol_health(ticker=args.ticker, all_active=args.all, check_nulls=args.check_nulls)
    else:
        # Default behavior: if no symbol arguments, check SPY as standard check
        ng = check_symbol_health(ticker='SPY', all_active=False, check_nulls=args.check_nulls)

    # NG 銘柄リストの書き出し（BOM なし UTF-8。NG ゼロでも空ファイルを書き「実行済み」を示す）
    if args.ng_out:
        with open(args.ng_out, 'w', encoding='utf-8', newline='\n') as f:
            for t in ng:
                f.write(f"{t}\n")

    # NG があれば非ゼロ終了（deploy_after_merge の合否ゲート）
    sys.exit(1 if ng else 0)
