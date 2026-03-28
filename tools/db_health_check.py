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
    'sma_200', 'ema_21', 'relative_strength_spy', 'rs_ratio_21', 'rs_momentum_21'
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
        return
    
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
        
        # T2 (DailyPrice) 情報
        t2_info = conn.execute(f"SELECT count(*), max(date) FROM daily_prices WHERE symbol_id = {sym_id}").fetchone()
        t2_count = t2_info[0]
        t2_latest = t2_info[1]
        
        # T3 (Indicator) 情報
        t3_count = conn.execute(f"SELECT count(*) FROM indicators WHERE symbol_id = {sym_id}").fetchone()[0]
        
        # NULL チェック (オプション)
        null_info = {}
        if check_nulls and t3_count > 0:
            for col in CRITICAL_COLUMNS:
                null_count = conn.execute(f"SELECT count(*) FROM indicators WHERE symbol_id = {sym_id} AND {col} IS NULL").fetchone()[0]
                if null_count > 0:
                    null_info[col] = null_count

        # 判定
        is_synced = (t2_latest >= spy_latest) if t2_latest else False
        is_consistent = (t2_count == t3_count)
        has_nulls = len(null_info) > 0
        
        status = "OK" if is_synced and is_consistent and not has_nulls else "NG"
        
        res = {
            "Symbol": t,
            "Status": status,
            "T2_Count": t2_count,
            "T3_Count": t3_count,
            "T2_Latest": t2_latest,
            "Synced": "Yes" if is_synced else "No",
            "Consistent": "Yes" if is_consistent else "No (Diff: {})".format(t2_count - t3_count),
            "Nulls": null_info if has_nulls else "None"
        }
        results.append(res)
        
        if not all_active or status == "NG":
            print(f"[{t}] Status: {status}")
            print(f"  - T2最新日: {t2_latest} (同期: {res['Synced']})")
            print(f"  - 行数一致: {res['Consistent']} (T2:{t2_count}, T3:{t3_count})")
            if has_nulls:
                print(f"  - NULL検知: {null_info}")

    conn.close()
    
    if all_active:
        df_res = pd.DataFrame(results)
        ng_count = len(df_res[df_res['Status'] == 'NG'])
        print("-" * 60)
        print(f"スキャン完了: 全{len(df_res)}銘柄中、{ng_count}銘柄に異常あり。")
        if ng_count > 0:
            print("\n異常あり銘柄リスト（上位10件）:")
            print(df_res[df_res['Status'] == 'NG'].head(10))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Database Health Check Tool')
    parser.add_argument('--ticker', type=str, help='Ticker to check')
    parser.add_argument('--all', action='store_true', help='Check all active symbols')
    parser.add_argument('--check-nulls', action='store_true', help='Include NULL checks for critical columns in T3')
    args = parser.parse_args()
    
    check_symbol_health(ticker=args.ticker, all_active=args.all, check_nulls=args.check_nulls)
