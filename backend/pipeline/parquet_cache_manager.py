import os
import sys
import re
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

class ParquetPointerUnreadableError(RuntimeError):
    """latest_master.json が存在するのに読めなかった（BOM 混入・JSON 破損・権限など）。

    「ポインタが無い（初回。正常）」と混同すると、呼び出し側が旧世代を
    「存在しない」と誤認して履歴を捨てるため、別物として扱う。
    """


def get_latest_master_files(pointer_file: str, *, strict: bool = False) -> dict | None:
    """Safely reads the latest Parquet master files path dict from pointer.

    ## 「無い」と「読めない」を区別する（2026-09-09）

    旧実装は、ポインタが**存在しない**場合と**存在するが読めない**場合を、どちらも
    警告なしの `None` で返していた。`rotate_and_archive_to_parquet()` はこの戻り値で
    マージ元の旧 Parquet を決めるため、`None` になると `old_paths` が空になり、
    **旧世代を読まずに SQLite の内容だけで新世代を公開する**。SQLite はホット
    キャッシュ（直近730日）しか持たないので、7年超の履歴を失った世代が無警告で公開される。

    実際に踏んだ例: `latest_master.json` を PowerShell で書き換えて BOM が付き、
    `json.load()` が落ちた（2026-08-30）。

        ValueError: Unexpected UTF-8 BOM (decode using utf-8-sig)

    10ms のリトライは `os.replace` 中の `PermissionError`（Windows）を吸収する
    正当な設計なので残す。変えたのは**リトライしても駄目だった後**の扱い。

    Args:
        strict: True なら「存在するが読めない」を `ParquetPointerUnreadableError` に
            する。データを壊しうる**書き込み経路**（ローテート）で使う。
            False（既定）でも `logger.error` は必ず出す。読み取り専用の経路
            （`chart_router` は SQLite にフォールバックする）を巻き添えにしないため、
            既定は従来どおり `None` を返す。

    Returns:
        ポインタが存在しない場合は `None`（初回。正常系）。

    Raises:
        ParquetPointerUnreadableError: `strict=True` で、存在するのに読めなかった場合。
    """
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
        except Exception as e:
            msg = (f"Parquet 世代ポインタが存在するのに読めません: {pointer_file} ({type(e).__name__}: {e})。"
                   f" BOM 混入・JSON 破損・権限を確認してください"
                   f"（PowerShell で書き換えると BOM が付きます。agent_execution_rules.md §5.1）。")
            if strict:
                raise ParquetPointerUnreadableError(msg) from e
            logging.getLogger(__name__).error(msg)
            return None

def find_latest_parquet_backup(data_dir: str) -> str | None:
    """`data_dir` 直下の `_bk_*` のうち、Parquet を含む最新バックアップの絶対パスを返す。

    バックアップは `_bk_YYYYMMDD` と `_bk_YYYYMMDD_HHMMSS` の2形式が混在している
    （`tools/backup_production_data.py`）。DB だけのバックアップ（
    `BACKUP_MANIFEST.json` に `parquet_generation` キーが無い）は候補から除外する。
    manifest が壊れている候補は警告ログを出してスキップする（他候補は生きる）。

    フォルダ名の文字列比較で新しい順に並べる。`_bk_YYYYMMDD_HHMMSS` は
    `_bk_YYYYMMDD` より必ず長い文字列になるため、同日でも時刻付きが後に来る。

    Returns:
        候補が1つも無ければ None。
    """
    logger = logging.getLogger(__name__)
    candidates = []
    for entry in sorted(glob.glob(os.path.join(data_dir, "_bk_*"))):
        if not os.path.isdir(entry):
            continue
        manifest_path = os.path.join(entry, "BACKUP_MANIFEST.json")
        if not os.path.exists(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.warning(f"バックアップの BACKUP_MANIFEST.json が壊れているためスキップします: "
                           f"{entry} ({type(e).__name__}: {e})")
            continue
        if not isinstance(manifest.get("parquet_generation"), dict):
            continue
        candidates.append(entry)

    if not candidates:
        return None
    # フォルダ名（末尾の日時部分）で新しい順に並べる
    candidates.sort(key=os.path.basename)
    return candidates[-1]


def get_backup_master_files(backup_dir: str) -> dict:
    """バックアップ内の `BACKUP_MANIFEST.json` を基準に Parquet マスタのパスを解決する。

    🔴 バックアップ内に `latest_master.json` が残っていても、それは本番
    `data/parquet_master/` を絶対パスで指しているため絶対に使わない
    （backtest_stable_data_plan.md §1）。必ず manifest の `parquet_generation`
    （ファイル名のみ）を `backup_dir` 基準で解決する。

    Raises:
        FileNotFoundError: manifest が無い／`parquet_generation` が無い／
            記載されたファイルが1つでも実体を欠く場合。本番パスへ黙って
            切り替えないため、ここでは例外にする。
    """
    manifest_path = os.path.join(backup_dir, "BACKUP_MANIFEST.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"BACKUP_MANIFEST.json が見つかりません: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    generation = manifest.get("parquet_generation")
    if not isinstance(generation, dict) or not generation:
        raise FileNotFoundError(
            f"{manifest_path} に parquet_generation がありません"
            f"（DB だけのバックアップの可能性があります）。")

    resolved = {}
    missing = []
    for key, fname in generation.items():
        full_path = os.path.join(backup_dir, os.path.basename(fname))
        resolved[key] = full_path
        if not os.path.exists(full_path):
            missing.append(fname)

    if missing:
        raise FileNotFoundError(
            f"バックアップ {backup_dir} に次のファイルが見つかりません: {', '.join(missing)}")

    return resolved


def _extract_parquet_generation(master_files: dict) -> str | None:
    """`prices_YYYYMMDD_HHMMSS.parquet` からファイル名だけの世代文字列を取り出す。"""
    prices_path = (master_files or {}).get("prices")
    if not prices_path:
        return None
    m = re.match(r"prices_(.+)\.parquet$", os.path.basename(prices_path))
    return m.group(1) if m else None


def resolve_backtest_data_source(data_source: str = "backup") -> tuple[dict, dict]:
    """バックテスト系が読む Parquet マスタの参照先を解決する（backtest_stable_data_plan.md §3-B）。

    daily update は1日に2回新しい世代を作るため、バックテスト・最適化・シナリオバッチが
    開始時にその時点の本番最新世代を読むと、比較のたびにデータ世代がずれる。
    そこで既定では「検証済みの最新バックアップ」（中身が二度と変わらない）を読む。

    Args:
        data_source: ``"backup"``（既定）/ ``"production"`` / バックアップのフォルダ名
            （例 ``"_bk_20260925_173413"``）。

    Returns:
        (Parquet マスタのファイル辞書, メタ情報)。メタ情報は
        ``{"data_source": "backup"|"production", "backup_name": str|None,
        "parquet_generation": str|None}``。

    Raises:
        FileNotFoundError: 名前指定のバックアップが存在しない、または本番の
            latest_master.json が読めない場合。
    """
    logger = logging.getLogger(__name__)
    import paths

    # バックアップの探索先は「本番の data ディレクトリ」に固定する（backtest_stable_data_plan.md
    # §3-B）。ワークツリーでも本番のバックアップを読み取りで使うため、
    # STOCKTOOL_DATA_ROOT / STOCKTOOL_ENV 等（get_data_root）の影響を受けない
    # get_prod_data_root() を優先する。
    prod_root = paths.get_prod_data_root() or paths.get_data_root()

    if data_source == "backup":
        backup_dir = find_latest_parquet_backup(prod_root)
        if backup_dir is None:
            logger.warning(
                f"Parquet を含むバックアップが {prod_root} 配下に見つかりません。"
                f" data_source='production'（本番の最新世代）にフォールバックします。"
            )
            return resolve_backtest_data_source("production")
        master_files = get_backup_master_files(backup_dir)
        meta = {
            "data_source": "backup",
            "backup_name": os.path.basename(backup_dir),
            "parquet_generation": _extract_parquet_generation(master_files),
        }
        return master_files, meta

    if data_source == "production":
        # "production" は既存の Sandbox 隔離（STOCKTOOL_DB_PATH）を優先する。
        # バックアップ探索と違い、production はサンドボックスで動作確認する経路が
        # 既にあるため（backtest_runner.resolve_backtest_db_path）、そちらを壊さない。
        env_db_path = os.environ.get("STOCKTOOL_DB_PATH")
        if env_db_path:
            parquet_dir = get_parquet_master_dir(env_db_path)
        else:
            parquet_dir = os.path.join(prod_root, "parquet_master")
        pointer_file = get_pointer_file_path(parquet_dir)
        master_files = get_latest_master_files(pointer_file, strict=True)
        if not master_files:
            raise FileNotFoundError(
                f"Parquet master cache files not found at {parquet_dir}!\n"
                f"  パイプラインを1回実行してマスタを生成するか、--refresh-cache を付けて実行してください。"
            )
        logger.warning(
            "data_source='production': 長時間の実行では daily update 後の自動削除"
            "（clean_old_parquet_versions、最新2世代を保持）で、開始時に固定した世代が"
            "実行中に消えることがあります。"
        )
        meta = {
            "data_source": "production",
            "backup_name": None,
            "parquet_generation": _extract_parquet_generation(master_files),
        }
        return master_files, meta

    # それ以外はバックアップ名として扱う
    backup_dir = os.path.join(prod_root, data_source)
    if not os.path.isdir(backup_dir):
        raise FileNotFoundError(
            f"指定されたバックアップフォルダが見つかりません: {backup_dir}"
        )
    master_files = get_backup_master_files(backup_dir)
    meta = {
        "data_source": data_source,
        "backup_name": data_source,
        "parquet_generation": _extract_parquet_generation(master_files),
    }
    return master_files, meta


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

def merge_timeseries_table(table_name: str, key_columns: list, df_sql, old_parquet_path,
                           logger) -> "pd.DataFrame":
    """時系列テーブル（prices / indicators / ranks / signals）を旧世代とマージする。

    SQLite はホット期間（730日）しか持たないので、**旧 Parquet との和集合**を取って
    全履歴マスタを作る。同一キーは SQLite 側を採用する（再計算の反映）。

    ## 失敗したら黙って SQLite の内容だけを返してはいけない

    旧実装は `except` で握りつぶして `return df_sql` していた。SQLite はホット期間
    しか無いので、**それはコールド側の全履歴を捨てることを意味する**。

        2026-09-01  indicators 6,076,932行 → 1,594,632行
        [WARNING] Failed to merge with old parquet cache for indicators:
          Unable to allocate 1.77 GiB ... Storing SQL only.

    警告1行だけでパイプラインは SUCCESS を返し、2021〜2024前半の指標が全滅して
    バックテストが動かなくなった。**マスタを壊すくらいなら公開しない**
    （`is_publishable_master` と同じ思想）ので、例外はそのまま送出する。

    同種の「激減」は全期間同期テーブル側では `resolve_full_sync_table`
    （2026-08-06 の fx_rates 7,717→23行）で既に塞がれていた。こちらにも防護柵を置く。

    Raises:
        RuntimeError: 旧世代の読み込み・マージに失敗した場合、または
            マージ結果が旧世代より行数が減った場合。
    """
    if not old_parquet_path or not os.path.exists(old_parquet_path):
        # 初回（旧世代が無い）。SQLite の内容がそのままマスタになる
        return df_sql

    try:
        df_old = pd.read_parquet(old_parquet_path)
    except Exception as e:
        raise RuntimeError(
            f"{table_name}: 旧 Parquet 世代の読み込みに失敗しました: {e}"
            f" — SQLite はホット期間しか持たないため、ここで先に進むと全履歴を失います。"
            f" マスタは更新していません。"
        ) from e

    if df_old.empty or df_sql.empty:
        # どちらかが空なら和集合を取る意味が無い。行数の多い方を残す
        return df_sql if len(df_sql) >= len(df_old) else df_old

    try:
        # キー列の型を揃える（date は文字列に寄せてタイムゾーン差異を避ける）
        for col in key_columns:
            if col in df_old.columns and col in df_sql.columns:
                if col == 'date':
                    df_old[col] = df_old[col].astype(str)
                    df_sql[col] = df_sql[col].astype(str)
                elif col in ('symbol_id', 'theme_id'):
                    df_old[col] = pd.to_numeric(df_old[col], errors='coerce').astype('Int64')
                    df_sql[col] = pd.to_numeric(df_sql[col], errors='coerce').astype('Int64')
                else:
                    try:
                        df_old[col] = df_old[col].astype(df_sql[col].dtype)
                    except Exception:
                        df_old[col] = df_old[col].astype(str)
                        df_sql[col] = df_sql[col].astype(str)

        df_merged = pd.concat([df_old, df_sql], ignore_index=True)
        df_merged = df_merged.drop_duplicates(subset=key_columns, keep='last')
    except Exception as e:
        raise RuntimeError(
            f"{table_name}: 旧世代とのマージに失敗しました: {e}"
            f" — マスタは更新していません。"
        ) from e

    # 防護柵: マージは和集合なので、行が減ることは原理的に無い
    if len(df_merged) < len(df_old):
        raise RuntimeError(
            f"{table_name}: マージ結果が旧世代より減っています"
            f"（{len(df_old):,}行 → {len(df_merged):,}行）。"
            f" 和集合で行が減ることは無いため、実装かデータの異常です。"
            f" マスタは更新していません。"
        )

    return df_merged


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
    
    # --- マージ元不明なら公開しない（fail-loud ガード / 2026-09-09） ----------
    #   ここで旧世代のパスを取り違えると old_paths が空になり、merge_timeseries_table が
    #   「初回（旧世代なし）」と判断して SQLite の内容だけを返す。SQLite はホット期間
    #   （730日）しか持たないので、7年超の履歴を失った世代がそのまま公開される。
    #   is_publishable_master / merge_timeseries_table と同じ思想で、
    #   **マスタを壊すくらいなら公開しない**。
    #
    #   1) ポインタが「存在するのに読めない」 → strict=True で例外
    #   2) ポインタが「存在しない」のに実体（prices_*.parquet）がある → 例外
    #      「初回だから旧世代が無い」と「ポインタだけ消えた／壊れた」は、ポインタの
    #      有無だけでは区別できない。実ファイルの有無で照合する。
    #      正規の完全再構築は archive_parquet_master.py が parquet_master/ ごと
    #      改名するため、この状態にはならない（誤爆しないことを 2026-09-09 に実測）。
    latest_pointers = get_latest_master_files(pointer_file, strict=True)
    if not latest_pointers:
        orphan_prices = glob.glob(os.path.join(parquet_dir, "prices_*.parquet"))
        if orphan_prices:
            raise ParquetPointerUnreadableError(
                f"世代ポインタ {pointer_file} がありませんが、Parquet の実体が "
                f"{len(orphan_prices)} 件残っています。マージ元を特定できないため中止します"
                f"（このまま続けると SQLite のホット期間だけの世代を公開し、全期間履歴を失います）。"
                f" 完全再構築なら backend/scripts/archive_parquet_master.py で "
                f"parquet_master/ ごと退避してから実行してください。")

    # 自己デッドロック防止: commit してロック解放後、読み取りエンジンで全量読み込む
    # （db.bind = write エンジン経由の read_sql は BEGIN IMMEDIATE で自セッションと衝突する）
    db.commit()
    from db.database import get_read_engine_for
    engine = get_read_engine_for(db)
    
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
    df_prices = merge_timeseries_table("daily_prices", ["symbol_id", "date"], df_prices_sql, old_paths.get('prices'), logger)
    df_indicators = merge_timeseries_table("indicators", ["symbol_id", "date"], df_indicators_sql, old_paths.get('indicators'), logger)
    df_ranks = merge_timeseries_table("relative_ranks", ["symbol_id", "date"], df_ranks_sql, old_paths.get('ranks'), logger)
    df_signals = merge_timeseries_table("market_signals", ["date"], df_signals_sql, old_paths.get('signals'), logger)

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
# SQLite ホットキャッシュが保持する日数。復元も purge もこの1つの値を見る。
# 以前は復元が config の default_start_date(2018-04-01) で切っていて、
# **復元 → 600万行 → purge で158万行へ削る（実測3.2時間）** という無駄が生じていた。
# T3 の再計算が Parquet 基点になった（recompute_parquet_indicators.py）ため、
# SQLite にホット期間より古い行を載せる理由はもう無い。
HOT_WINDOW_DAYS = 730

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
    """SQLite から 730日より古い DailyPrice / Indicator / RelativeRank を削除する。

    **VACUUM はしない。** ファイルサイズは縮まず、空きページが再利用されるだけ。
    物理的な回収は週次メンテナンス（`weekly_maintenance.py`）へ移管済み
    （下の "Reclaim SQLite unused pages" のコメント参照）。
    docstring にあった "Performs VACUUM" は実装と食い違っていた。
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
    cutoff_date = max_date - timedelta(days=HOT_WINDOW_DAYS)
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
    """SQLite のキャッシュテーブルを削除し、Parquet マスタから作り直す。

    **復元範囲はホット期間（直近 HOT_WINDOW_DAYS 日）のみ**（2026-09-03 変更）。

    以前は `config.toml` の `data_collection.default_start_date`（2018-04-01）を
    単一のカットオフにしていたが、T2 は2系統で取得している:

        index_start_date   = "2010-04-01"   # レバレッジ / 市場 / 指標
        default_start_date = "2018-04-01"   # 個別 / テーマ / セクタ

    そのため `index_start_date` 側の 49 銘柄（SPY を含む）の 2010〜2018 が
    SQLite に入らず、**この状態で T3 を再計算すると ETF だけ 2018 起点になって
    本来の値と食い違う**という不整合があった。

    ただし **T3 の再計算は Parquet 基点へ移した**ので（`recompute_parquet_indicators.py`）、
    SQLite にホット期間より古い行を載せる理由はもう無い。むしろ載せると:

      - 復元後 600万行 → purge で158万行へ削るのに実測 3.2時間かかる
      - その状態で rotate すると SQLite 600万行 × Parquet 600万行のマージになり、
        2026-09-01 に OOM して指標マスタが切り詰められた条件そのものになる

    よって **ホット期間（`HOT_WINDOW_DAYS`）だけを復元する**。purge の閾値と同じ値を
    見るので、復元直後に purge 対象の行が存在しない状態になる。

    なお docstring にあった "restoring only the latest 2 years" は実装と
    食い違っていた（実際は default_start_date 以降）。この誤記が
    「復元すれば730日になる」という誤解を生んでいた。今回、実装を docstring に合わせた。
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
        
    # ホット期間だけを復元する（docstring 参照）。purge と同じ HOT_WINDOW_DAYS を見るので、
    # 復元直後に purge 対象の行が無い＝復元→purge の往復(実測3.2時間)が不要になる。
    cutoff_str = (max_date - timedelta(days=HOT_WINDOW_DAYS)).isoformat()
    
    logger.info(f"    Parquet Master Latest Date: {max_date.isoformat()}")
    logger.info(f"    Restoring Cache Date Lookback: >= {cutoff_str} (ホット期間 {HOT_WINDOW_DAYS}日)")
    
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
