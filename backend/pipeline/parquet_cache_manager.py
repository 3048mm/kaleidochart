import os
import sys
import time
import json
import glob
import shutil
import logging
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import text, func

# Ensure modules in backend are resolvable
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from db.database import get_db
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent, MarketSignal, FxRate

def get_parquet_master_dir(db_path: str) -> str:
    """Returns the Parquet master storage directory path relative to the active db_path."""
    db_dir = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(db_dir, "parquet_master")

def get_pointer_file_path(parquet_dir: str) -> str:
    """Returns the absolute path of the latest_master.json file."""
    return os.path.join(parquet_dir, "latest_master.json")

def update_pointer_with_retry(pointer_file: str, new_files_dict: dict, logger: logging.Logger, max_retries=10, delay=0.005) -> bool:
    """Atomically updates the latest_master.json file with exponential backoff on Windows."""
    temp_pointer = pointer_file + ".tmp"
    try:
        with open(temp_pointer, 'w', encoding='utf-8') as f:
            json.dump(new_files_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to write temporary pointer file: {e}")
        return False
        
    for attempt in range(max_retries):
        try:
            os.replace(temp_pointer, pointer_file)
            return True
        except OSError:
            time.sleep(delay * (2 ** attempt)) # Exponential backoff
            
    try:
        os.remove(temp_pointer)
    except:
        pass
    logger.error("Failed to update latest_master.json pointer after max retries due to lock contention.")
    return False

def get_latest_master_files(pointer_file: str) -> dict | None:
    """Safely reads the latest Parquet master files path dict from pointer."""
    if not os.path.exists(pointer_file):
        return None
    try:
        with open(pointer_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        # Fallback with simple retry if another process is writing the pointer at this millisecond
        time.sleep(0.01)
        try:
            with open(pointer_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

def clean_old_parquet_versions(parquet_dir: str, logger: logging.Logger, keep_count=2):
    """Cleans up older timestamps of Parquet masters, keeping only the latest versions,
    with a 15-minute grace period to prevent deleting files currently read by long-running jobs.
    """
    all_pointers = sorted(glob.glob(os.path.join(parquet_dir, "data_version_*.json")))
    if len(all_pointers) <= keep_count:
        return
        
    pointers_to_delete = all_pointers[:-keep_count]
    for ptr_file in pointers_to_delete:
        try:
            # Check modification time to enforce 15-minute grace period
            mtime = os.path.getmtime(ptr_file)
            age_seconds = time.time() - mtime
            if age_seconds < 900:  # 15 minutes
                logger.info(f"Skipping cleanup of recent version pointer {os.path.basename(ptr_file)} (age: {age_seconds/60:.1f} mins < 15 mins)")
                continue

            with open(ptr_file, 'r', encoding='utf-8') as f:
                version_files = json.load(f)
            # Delete associated parquet files
            for file_path in version_files.values():
                abs_path = os.path.join(parquet_dir, os.path.basename(file_path))
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            # Delete pointer JSON file itself
            os.remove(ptr_file)
            logger.info(f"Cleaned up old Parquet version pointer and files: {os.path.basename(ptr_file)}")
        except Exception as e:
            # Skip if file is currently open/locked by other processes. It will be removed in subsequent cleanups.
            logger.debug(f"Skipped cleanup of {os.path.basename(ptr_file)} due to: {e}")

def rotate_and_archive_to_parquet(db, db_path: str, logger: logging.Logger,
                                  require_non_empty: bool = True) -> dict:
    """
    Reads all historical data from SQLite and merges/archives them into Parquet Master files.
    This acts as the single source of truth for the entire historical data (past 7+ years).
    Uses MVCC-style version files to prevent Windows PermissionError on parallel backtests.

    Args:
        require_non_empty: 価格・指標・銘柄が空の世代を公開せず `RuntimeError` にする。
            **本番経路では必ず既定の True を使うこと**（`is_publishable_master` 参照）。
            最小フィクスチャで一部テーブルだけを検証するテストのみ False を渡す。
    """
    logger.info("Initializing Hot/Cold Data Archiver...")
    t0 = time.time()
    
    parquet_dir = get_parquet_master_dir(db_path)
    if not os.path.exists(parquet_dir):
        os.makedirs(parquet_dir, exist_ok=True)
        
    pointer_file = get_pointer_file_path(parquet_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    version_id = f"data_version_{timestamp}"
    
    # Destination timestamped paths
    files = {
        'symbols': os.path.join(parquet_dir, f"symbols_{timestamp}.parquet"),
        'prices': os.path.join(parquet_dir, f"prices_{timestamp}.parquet"),
        'indicators': os.path.join(parquet_dir, f"indicators_{timestamp}.parquet"),
        'ranks': os.path.join(parquet_dir, f"ranks_{timestamp}.parquet"),
        'tc': os.path.join(parquet_dir, f"theme_constituents_{timestamp}.parquet"),
        'signals': os.path.join(parquet_dir, f"market_signals_{timestamp}.parquet"),
        # fx_rates は長らく SQLite のみだったが、完全再構築（空DBから作り直し）で
        # 為替履歴が丸ごと失われる事故が起きたため Parquet 対象に加えた（2026-08-01）。
        # 実際 2026-07-30 の再構築で 7,711行(1996-2026) が 22行(直近30日) に消えた。
        'fx': os.path.join(parquet_dir, f"fx_rates_{timestamp}.parquet"),
    }
    
    latest_pointers = get_latest_master_files(pointer_file)
    # 自己デッドロック防止: commit してロック解放後、読み取りエンジンで全量読み込む
    # （db.bind = write エンジン経由の read_sql は BEGIN IMMEDIATE で自セッションと衝突する）
    db.commit()
    from db.database import get_read_engine_for
    engine = get_read_engine_for(db)
    
    def process_and_merge_table(table_name: str, key_columns: list[str], df_sql: pd.DataFrame, old_parquet_path: str | None) -> pd.DataFrame:
        """Helper to load old parquet, append new SQL data, and drop duplicates safely."""
        if old_parquet_path and os.path.exists(old_parquet_path):
            try:
                df_old = pd.read_parquet(old_parquet_path)
                if not df_old.empty and not df_sql.empty:
                    # Align column types to prevent concat failures (dates to string to avoid timezone/dt conflicts)
                    for col in key_columns:
                        if col in df_old.columns and col in df_sql.columns:
                            if col == 'date':
                                df_old[col] = df_old[col].astype(str)
                                df_sql[col] = df_sql[col].astype(str)
                            elif col in ('symbol_id', 'theme_id'):
                                # Guarantee numerical IDs are cast to standard Int64 (nullable integer)
                                df_old[col] = pd.to_numeric(df_old[col], errors='coerce').astype('Int64')
                                df_sql[col] = pd.to_numeric(df_sql[col], errors='coerce').astype('Int64')
                            else:
                                try:
                                    target_type = df_sql[col].dtype
                                    df_old[col] = df_old[col].astype(target_type)
                                except:
                                    df_old[col] = df_old[col].astype(str)
                                    df_sql[col] = df_sql[col].astype(str)
                    
                    df_merged = pd.concat([df_old, df_sql], ignore_index=True)
                    df_merged = df_merged.drop_duplicates(subset=key_columns, keep='last')
                    return df_merged
            except Exception as e:
                logger.warning(f"Failed to merge with old parquet cache for {table_name}: {e}. Storing SQL only.")
        return df_sql

    logger.info("  1. Querying active SQLite data to archive...")
    # Load all records currently in SQLite
    df_symbols_sql = pd.read_sql("SELECT * FROM symbols", engine)
    df_prices_sql = pd.read_sql("SELECT * FROM daily_prices", engine)
    df_indicators_sql = pd.read_sql("SELECT * FROM indicators", engine)
    df_ranks_sql = pd.read_sql("SELECT * FROM relative_ranks", engine)
    df_tc_sql = pd.read_sql("SELECT * FROM theme_constituents", engine)
    df_signals_sql = pd.read_sql("SELECT * FROM market_signals", engine)
    df_fx_sql = pd.read_sql("SELECT * FROM fx_rates", engine)

    # Resolve previous versions for incremental merges
    old_paths = latest_pointers if latest_pointers else {}
    
    logger.info("  2. Merging SQL updates into Parquet masters...")
    # Merge and update df master instances
    # --- 全期間同期テーブル: SQLite を正として「置換」する ---------------
    #   symbols / theme_constituents は SQLite 側が常に完全集合を持つ
    #   （architecture.md §11.1 の「全期間（同期）」）。
    #   ここでマージすると SQLite での削除が Parquet に伝播せず、
    #   Parquet を直読みするバックテストが古いテーマ構成を見続ける。
    #   実例: universe.db 移行で CPER のテーマ構成3ペア・孤児4ペア・重複12ペアを
    #   削除したが、マージのままでは Parquet 側に残り続けた。
    #   fx_rates も同じく SQLite が全期間を持つ（ホット期間のパージ対象外）。
    #   マージにすると「ダミー値を消して実データに入れ替える」ような修正が
    #   Parquet に伝播しない。
    df_symbols = df_symbols_sql
    df_tc = df_tc_sql
    df_fx = df_fx_sql

    # --- 時系列テーブル: SQLite はホット期間のみのため「マージ」する ------
    df_prices = process_and_merge_table("daily_prices", ["symbol_id", "date"], df_prices_sql, old_paths.get('prices'))
    df_indicators = process_and_merge_table("indicators", ["symbol_id", "date"], df_indicators_sql, old_paths.get('indicators'))
    df_ranks = process_and_merge_table("relative_ranks", ["symbol_id", "date"], df_ranks_sql, old_paths.get('ranks'))
    df_signals = process_and_merge_table("market_signals", ["date"], df_signals_sql, old_paths.get('signals'))

    # 安全弁: 全期間同期テーブルが「空」または「激減」したら置換しない。
    #   空       → SQLite が壊れた等。旧世代を維持する
    #   激減     → 再構築でサンドボックスの空 DB から始まった等。旧世代と和集合にする
    # 「空なら維持」だけでは足りなかった: 2026-08-06 の再構築で fx_rates が
    # 7,717行 → 23行（直近30日のみ）になり、空ではないので素通りした。
    # 通常の削除（ダミー行の除去など）は数行〜数%なので replace のまま通る。
    _full_sync = {
        "symbols": ("symbols", ["id"]),
        "theme_constituents": ("tc", ["theme_id", "symbol_id"]),
        "fx_rates": ("fx", ["currency_pair", "date"]),
    }
    for name, (old_key, keys) in _full_sync.items():
        df_new = {"symbols": df_symbols, "theme_constituents": df_tc, "fx_rates": df_fx}[name]
        old_path = old_paths.get(old_key)
        df_old = pd.read_parquet(old_path) if old_path and os.path.exists(old_path) else None
        resolved, action = resolve_full_sync_table(df_new, df_old, keys)
        if action != "replace":
            logger.warning(f"  {name}: {len(df_new):,}行 は旧世代 {len(df_old):,}行 から"
                           f"激減しているため置換せず {action} しました"
                           f" → {len(resolved):,}行")
        if name == "symbols":
            df_symbols = resolved
        elif name == "theme_constituents":
            df_tc = resolved
        else:
            df_fx = resolved
    
    # Save the updated full history masters
    logger_fn = logger.info
    logger_fn(f"  Writing updated masters - Symbols: {len(df_symbols)}, Prices: {len(df_prices)}, "
              f"Indicators: {len(df_indicators)}, Ranks: {len(df_ranks)}, ThemeConstituents: {len(df_tc)}, "
              f"MarketSignals: {len(df_signals)}, FxRates: {len(df_fx)}")

    # **空の世代を公開しない。** 上流の取得が全滅しても T2〜T5 は 0行のまま完走するため、
    # ここで止めないとポインタが空マスタを指してしまう（2026-08-06 に実際に発生）。
    publishable, reason = is_publishable_master(
        prices_rows=len(df_prices), indicators_rows=len(df_indicators),
        symbols_rows=len(df_symbols))
    if require_non_empty and not publishable:
        logger.error(f"Parquet マスタの公開を中止しました: {reason}")
        logger.error("  上流（yfinance / ネットワーク / 証明書）を確認してください。"
                     " 既存の latest_master.json は変更していません。")
        raise RuntimeError(f"空の Parquet マスタは公開できません: {reason}")

    df_symbols.to_parquet(files['symbols'], index=False)
    df_prices.to_parquet(files['prices'], index=False)
    df_indicators.to_parquet(files['indicators'], index=False)
    df_ranks.to_parquet(files['ranks'], index=False)
    df_tc.to_parquet(files['tc'], index=False)
    df_signals.to_parquet(files['signals'], index=False)
    df_fx.to_parquet(files['fx'], index=False)
    
    # Save version-specific pointer list
    version_json_path = os.path.join(parquet_dir, f"{version_id}.json")
    with open(version_json_path, 'w', encoding='utf-8') as f:
        json.dump(files, f, ensure_ascii=False, indent=2)
        
    # Atomically replace latest_master.json pointer
    success = update_pointer_with_retry(pointer_file, files, logger)
    if success:
        logger.info(f"Parquet full history master successfully archived & pointed to version {version_id}.")
    else:
        logger.error("Critical: Failed to update latest_master.json pointer!")
        raise OSError("Failed to update Parquet pointer due to locks.")
        
    # Safely delete historical Parquets beyond the latest 2 timestamps
    clean_old_parquet_versions(parquet_dir, logger)
    
    logger.info(f"Hot/Cold Archiver completed successfully in {time.time()-t0:.2f}s.")
    return files

# 全期間同期テーブルが旧世代のこの割合を下回ったら「置換」ではなく「和集合」に倒す。
# 通常の削除（ダミー行の除去など）は数行〜数%なので影響しない。
FULL_SYNC_SHRINK_RATIO = 0.5


def resolve_full_sync_table(df_new, df_old, key_columns):
    """全期間同期テーブル（symbols / theme_constituents / fx_rates）の確定値を決める。

    これらは **SQLite を正として Parquet を置換する**。マージにすると SQLite での削除が
    Parquet に伝播せず、Parquet を直読みするバックテストが古い構成を見続けるため。

    ところが**全期間再構築はサンドボックスの空 DB から始まる**ので、この前提が崩れる。
    FX 同期は直近30日しか取りに行かず、その少数行が30年分を置き換えてしまった。

        2026-07-30 の再構築  fx_rates 7,711行 → 22行
        2026-08-06 の再構築  fx_rates 7,717行 → 23行   ← 「空なら維持」の安全弁を素通り

    そこで**通常の削除は通し、再構築由来の激減だけ弾く**。

    Returns:
        (確定した DataFrame, 採用した方針)
        方針は ``replace`` / ``union`` / ``keep_old``。
    """
    if df_old is None or len(df_old) == 0:
        return df_new, "replace"
    if df_new is None or len(df_new) == 0:
        return df_old, "keep_old"
    if len(df_new) >= len(df_old) * FULL_SYNC_SHRINK_RATIO:
        return df_new, "replace"

    keys = [c for c in key_columns if c in df_new.columns and c in df_old.columns]
    old = df_old.copy()
    new = df_new.copy()
    for col in keys:
        if col == "date":
            old[col] = old[col].astype(str)
            new[col] = new[col].astype(str)
    merged = pd.concat([old, new], ignore_index=True)
    if keys:
        merged = merged.drop_duplicates(subset=keys, keep="last")
        merged = merged.sort_values(keys).reset_index(drop=True)
    return merged, "union"


def is_publishable_master(prices_rows: int, indicators_rows: int,
                          symbols_rows: int) -> tuple[bool, str]:
    """この世代を Parquet マスタとして公開してよいか。

    **上流の取得が全滅してもパイプラインは 0行のまま完走する。**
    2026-08-06 の全期間再構築では、Norton の TLS 傍受で `curl_cffi`（yfinance が
    cookie/crumb 取得に使う）が証明書検証に失敗し、SPY を含む全銘柄が
    「possibly delisted; no price data found」になった。それでも T2〜T5 は
    エラーを出さずに通過し、ほぼ空の世代でポインタが上書きされた。

        Writing updated masters - Symbols: 3220, Prices: 0, Indicators: 0, Ranks: 0

    再構築時は旧世代を退避してからやるためマージ相手が居ない。
    「全期間同期テーブルが空なら旧世代を維持する」既存の安全弁では防げないため、
    **行数そのものを公開条件にする。**

    Returns:
        (公開可否, 拒否理由). 公開できるときの理由は空文字列。
    """
    if symbols_rows <= 0:
        return False, "symbols が 0 行"
    if prices_rows <= 0:
        return False, "prices が 0 行（上流の取得が全滅した可能性）"
    if indicators_rows <= 0:
        return False, "indicators が 0 行（T3 が計算できていない）"
    return True, ""


def purge_sqlite_cache_older_than_2_years(db, db_path: str, logger: logging.Logger):
    """
    Deletes all DailyPrices, Indicators, and RelativeRanks older than 2 years from SQLite.
    Performs VACUUM to physically reduce SQLite file size.
    """
    logger.info("Initializing SQLite cache shrink (Daily Purge)...")
    t0 = time.time()
    
    initial_db_size = os.path.getsize(db_path)
    logger.info(f"  Initial SQLite File Size: {initial_db_size / 1024 / 1024:.2f} MB")
    
    max_date_str = db.query(func.max(DailyPrice.date)).scalar()
    if not max_date_str:
        logger.info("  No daily price records found in SQLite. Skipping purge.")
        return
        
    max_date = pd.to_datetime(max_date_str).date()
    cutoff_date = max_date - timedelta(days=730)
    cutoff_str = cutoff_date.isoformat()
    
    logger.info(f"  Latest date in SQLite: {max_date_str}")
    logger.info(f"  Purge lookback threshold: {cutoff_str} (Older than 2 years will be removed)")
    
    prices_before = db.query(DailyPrice).count()
    indicators_before = db.query(Indicator).count()
    ranks_before = db.query(RelativeRank).count()
    
    # Remove older records
    deleted_prices = db.query(DailyPrice).filter(DailyPrice.date < cutoff_str).delete(synchronize_session=False)
    deleted_indicators = db.query(Indicator).filter(Indicator.date < cutoff_str).delete(synchronize_session=False)
    deleted_ranks = db.query(RelativeRank).filter(RelativeRank.date < cutoff_str).delete(synchronize_session=False)
    
    db.commit()
    
    prices_after = db.query(DailyPrice).count()
    indicators_after = db.query(Indicator).count()
    ranks_after = db.query(RelativeRank).count()
    
    logger.info(f"  Deletion Summary:")
    logger.info(f"    DailyPrices: {prices_after} left (Deleted {deleted_prices} records)")
    logger.info(f"    Indicators: {indicators_after} left (Deleted {deleted_indicators} records)")
    logger.info(f"    RelativeRanks: {ranks_after} left (Deleted {deleted_ranks} records)")
    
    # Reclaim SQLite unused pages physically via VACUUM
    # (週次メンテナンスバッチ weekly_maintenance.py へ移行されたため、日次パイプラインでの実行はスキップします)
    final_db_size = os.path.getsize(db_path)
    reclaimed = initial_db_size - final_db_size
    logger.info(f"  Final SQLite File Size: {final_db_size / 1024 / 1024:.2f} MB "
                f"(Reclaimed: {reclaimed / 1024 / 1024:.2f} MB, {reclaimed / initial_db_size * 100:.1f}% space reclaimed)")
    logger.info(f"SQLite cache shrink completed in {time.time()-t0:.2f}s.")

def bulk_insert_df_to_sqlite(engine, df: pd.DataFrame, table_name: str, logger: logging.Logger):
    """Parquet 復元用の高速バルクインサート（raw DBAPI executemany）。"""
    if df.empty:
        return

    df_to_insert = df.copy()

    # Drop auto-incrementing ID column for tables other than 'symbols' to prevent UNIQUE constraint failures.
    # 'symbols' ID must be preserved because it is referenced as a foreign key by other tables.
    if 'id' in df_to_insert.columns and table_name != 'symbols':
        df_to_insert = df_to_insert.drop(columns=['id'])

    # Get column names of the SQLAlchemy table dynamically
    # Map table name to the respective Model class
    from db.models import Symbol, ThemeConstituent, DailyPrice, Indicator, RelativeRank
    model_map = {
        "symbols": Symbol,
        "theme_constituents": ThemeConstituent,
        "daily_prices": DailyPrice,
        "indicators": Indicator,
        "relative_ranks": RelativeRank
    }

    model_cls = model_map.get(table_name)
    if model_cls:
        valid_cols = {c.name for c in model_cls.__table__.columns}
        # Filter df columns to keep only those that exist in the database table
        cols_to_keep = [c for c in df_to_insert.columns if c in valid_cols]
        df_to_insert = df_to_insert[cols_to_keep]

    cols = df_to_insert.columns.tolist()
    col_str = ", ".join([f'"{c}"' for c in cols])
    placeholders = ", ".join(["?"] * len(cols))
    query = f'INSERT INTO "{table_name}" ({col_str}) VALUES ({placeholders})'

    # Replace NaN with None for SQL NULL compatibility
    df_clean = df_to_insert.where(pd.notnull(df_to_insert), None)
    records = [tuple(x) for x in df_clean.to_numpy()]

    # Access raw DBAPI connection for raw executemany and PRAGMA settings
    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        # journal_mode は変更しない: WAL からの切り替えは他の接続が1つでも
        # 開いていると database is locked で即失敗する（同一プロセス内の
        # SQLAlchemy セッション/プール接続で常に該当。2026-07-09 に実発生）。
        # synchronous は接続ローカルなのでロック不要で高速化できる。
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA synchronous = OFF")

        cursor.executemany(query, records)
        connection.commit()

        cursor.execute("PRAGMA synchronous = NORMAL")
    except Exception as e:
        connection.rollback()
        logger.error(f"Failed bulk insert into {table_name}: {e}")
        raise e
    finally:
        connection.close()


def restore_sqlite_cache_from_parquet(db, db_path: str, logger: logging.Logger):
    """
    Physically deletes and rebuilds SQLite cache tables,
    restoring only the latest 2 years from the Parquet master.
    Optimized with PyArrow filters and native SQLite bulk insert.
    """
    logger.info("Initializing SQLite Cache Restore from Parquet master...")
    t0 = time.time()
    
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)
    
    latest_files = get_latest_master_files(pointer_file)
    if not latest_files:
        logger.error("No active Parquet master file pointer found!")
        raise FileNotFoundError("Pointer latest_master.json is missing or corrupted.")
        
    logger.info("  1. Clearing SQLite Cache tables...")
    # Delete tables to keep schema metadata (this is safer than deleting SQLite file itself during active backend executions)
    db.query(MarketSignal).delete(synchronize_session=False)
    db.query(DailyPrice).delete(synchronize_session=False)
    db.query(Indicator).delete(synchronize_session=False)
    db.query(RelativeRank).delete(synchronize_session=False)
    db.query(Symbol).delete(synchronize_session=False)
    db.query(ThemeConstituent).delete(synchronize_session=False)

    # fx_rates は「復元元が存在するときだけ」クリアする。
    # 'fx' キーを持たない旧世代の Parquet から復元する場合、無条件にクリアすると
    # 復元されずに為替履歴が全滅する（Parquet 対象化は 2026-08-01 から）。
    _fx_source = latest_files.get('fx') if latest_files else None
    _fx_restorable = bool(_fx_source) and os.path.exists(_fx_source)
    if _fx_restorable:
        db.query(FxRate).delete(synchronize_session=False)
    else:
        logger.warning(
            "  fx_rates は復元元の Parquet が無いため、現在の SQLite の内容を維持します"
            "（旧世代の Parquet から復元しています）"
        )
    db.commit()

    logger.info("  2. Loading historical records from Parquet...")
    
    # Load only 'date' column first to find the latest date efficiently
    logger.info("    Determining latest date in Parquet master...")
    df_prices_dates = pd.read_parquet(latest_files['prices'], columns=['date'])
    max_date_val = df_prices_dates['date'].max()
    
    if isinstance(max_date_val, str):
        max_date = pd.to_datetime(max_date_val).date()
    else:
        max_date = max_date_val
        
    try:
        import tomli
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(project_root, "config.toml")
        with open(config_path, "rb") as f:
            config = tomli.load(f)
        default_start_date = config.get("data_collection", {}).get("default_start_date", "2018-04-01")
        cutoff_date = pd.to_datetime(default_start_date).date()
    except Exception as e:
        logger.warning(f"Failed to load config.toml in restore_sqlite_cache_from_parquet, using default 2018-04-01: {e}")
        cutoff_date = pd.to_datetime("2018-04-01").date()
    cutoff_str = cutoff_date.isoformat()
    
    logger.info(f"    Parquet Master Latest Date: {max_date.isoformat()}")
    logger.info(f"    Restoring Cache Date Lookback: >= {cutoff_str}")
    
    # Load non-historical dimension tables
    logger.info("    Loading symbols & theme constituents...")
    df_symbols = pd.read_parquet(latest_files['symbols'])
    df_tc = pd.read_parquet(latest_files['tc'])
    
    # Load historical transaction tables, filtered by date to restore historical data
    logger.info("    Loading daily prices...")
    df_prices_cached = pd.read_parquet(latest_files['prices'], filters=[('date', '>=', cutoff_str)])
    
    logger.info("    Loading indicators...")
    df_indicators_cached = pd.read_parquet(latest_files['indicators'], filters=[('date', '>=', cutoff_str)])
    
    logger.info("    Loading ranks...")
    df_ranks_cached = pd.read_parquet(latest_files['ranks'], filters=[('date', '>=', cutoff_str)])
    
    logger.info(f"  3. Bulk-importing records to SQLite...")
    engine = db.bind

    # Import symbols & constituents (full list)
    logger.info("    Importing symbols...")
    bulk_insert_df_to_sqlite(engine, df_symbols, "symbols", logger)
    logger.info("    Importing theme constituents...")
    bulk_insert_df_to_sqlite(engine, df_tc, "theme_constituents", logger)
    
    # Import hot-db cached rows (latest 2 years)
    logger.info("    Importing daily prices...")
    bulk_insert_df_to_sqlite(engine, df_prices_cached, "daily_prices", logger)
    logger.info("    Importing indicators...")
    bulk_insert_df_to_sqlite(engine, df_indicators_cached, "indicators", logger)
    logger.info("    Importing relative ranks...")
    bulk_insert_df_to_sqlite(engine, df_ranks_cached, "relative_ranks", logger)
    
    # Import market signals (full history - small table, no date filtering needed)
    if 'signals' in latest_files and os.path.exists(latest_files['signals']):
        logger.info("    Loading & importing market signals...")
        df_signals_cached = pd.read_parquet(latest_files['signals'], filters=[('date', '>=', cutoff_str)])
        bulk_insert_df_to_sqlite(engine, df_signals_cached, "market_signals", logger)
    else:
        logger.warning("    market_signals Parquet not found in pointer - will be recalculated on next pipeline run.")

    # Import fx_rates (full history - small table, SQLite holds every row)
    # ホット期間でパージされないため date フィルタは掛けない。
    if _fx_restorable:
        logger.info("    Loading & importing fx rates...")
        df_fx_cached = pd.read_parquet(latest_files['fx'])
        bulk_insert_df_to_sqlite(engine, df_fx_cached, "fx_rates", logger)
    else:
        logger.warning(
            "    fx_rates Parquet not found in pointer - 既存の SQLite の内容をそのまま維持しました。"
            "履歴が不足している場合は backend/scripts/backfill_fx_rates.py で復旧できます。"
        )

    db.commit()
    
    logger.info(f"SQLite Cache restored successfully in {time.time()-t0:.2f}s!")
