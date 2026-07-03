---
name: parquet-data-quality
description: Data quality checks and high-performance patterns for the Parquet master / pandas layer. Use when reading, writing, merging, or validating Parquet master files (data/parquet_master/), diagnosing dtype corruption, or optimizing large DataFrame loads to avoid OOM.
---

# Parquet データ品質・高速化ガイド (parquet-data-quality)

本プロジェクトの真のマスターデータは `data/parquet_master/` の Parquet ファイル群である（SQLite は直近2年のキャッシュに過ぎない）。
Parquet を読み書き・マージ・検証するコードを書く際は、以下のルールを最優先で適用すること。
実装の実体は `backend/pipeline/parquet_cache_manager.py` にあり、変更時はこのファイルのパターンに合わせる。

---

## 1. MVCC 世代管理の絶対ルール

Windows のファイル共有ロック（WinError 32/5）を回避するため、Parquet マスターは**上書き禁止**。

1. **常に新しいタイムスタンプ付きファイルとして書く**: `prices_YYYYMMDD_HHMMSS.parquet` 形式。既存ファイルへの `to_parquet` 上書きは絶対に行わない。
2. **ポインタ経由で読む**: 最新版のファイルパスは `latest_master.json` から解決する（`get_latest_master_files()`）。ファイル名をハードコードしたり `glob` の最新を直接掴んだりしない。
3. **ポインタ更新はアトミック**: 一時ファイルに書いてから `os.replace()`。競合時は指数バックオフでリトライ（`update_pointer_with_retry()`）。
4. **旧世代の削除は非同期・ベストエフォート**: 他プロセスが掴んでいたらスキップして次回に委ねる（`clean_old_parquet_versions()`、直近2世代保持）。

## 2. 読み込みの高速化・OOM 回避（必須パターン）

数百万行規模のデータを扱うため、以下を徹底する:

- **カラム射影**: 必要なカラムだけ読む。
  ```python
  df = pd.read_parquet(path, columns=['symbol_id', 'date', 'close'])
  ```
- **PyArrow 日付フィルタ**: 期間が決まっているならディスク読み込みの段階で絞る（1.8GB → 740MB / 約1秒でロード可能）。
  ```python
  df = pd.read_parquet(path, filters=[('date', '>=', cutoff_str)])
  ```
- **最新日付だけ知りたい場合**: `columns=['date']` で date 列のみロードして `max()`（全カラムロード禁止）。
- **SQLite からの大量読み出し**: `pd.read_sql(..., chunksize=50_000〜100_000)` でチャンク分割。一括ロードはメモリがデータサイズの数十倍に膨れる。
- **ループ内での全走査禁止**: `unique()` / `max()` 等の DataFrame 全走査は日付ループの外で事前計算する（O(N²) 化を防ぐ）。

## 3. 型汚染の防止（マージ時の最重要ポイント）

Parquet 同士・Parquet と SQL のマージ（`concat` + `drop_duplicates`）では型不整合が頻発する。`parquet_cache_manager.py` の `process_and_merge_table()` に準拠:

- **`date` カラム**: マージ前に両側とも `str` に揃える（Timestamp と `datetime.date` の混在は比較不全・シグナル誤検知の原因）。
- **ID カラム (`symbol_id`, `theme_id`)**: `pd.to_numeric(..., errors='coerce').astype('Int64')`（nullable Int64）に統一。**文字列型 (object) への汚染は重大バグ** — `tools/db_health_check.py` の `check_parquet_health()` が検出する。
- **重複排除**: テーブルごとの自然キーで `drop_duplicates(subset=key_columns, keep='last')`（新しいデータを優先）。
  - `daily_prices` / `indicators` / `relative_ranks`: `["symbol_id", "date"]`
  - `symbols`: `["ticker", "exchange"]`
  - `theme_constituents`: `["theme_id", "symbol_id"]`
  - `market_signals`: `["date"]`
- **バックテスト側でのロード直後**: `pd.to_datetime(df['date']).dt.date` で明示変換してから比較に使う。

## 4. データ品質の検証手順

Parquet やパイプラインに変更を加えたら、必ず以下で検証する:

```powershell
# Parquet マスター全体の健全性（行数・日付範囲・ID型汚染チェック）
python tools/db_health_check.py --parquet

# 特定銘柄の T2/T3 整合性 + 重要カラムの NULL チェック
python tools/db_health_check.py --ticker AAPL --check-nulls

# 全アクティブ銘柄のスキャン
python tools/db_health_check.py --all --check-nulls
```

**合格基準（データ完全性ポリシー）**:
1. 全アクティブ銘柄の T2 最新日 >= SPY の最新日
2. 銘柄ごとに T2 行数 == T3 行数（完全一致）
3. ID カラムが数値型であること（object 型は NG）
4. 重要カラム（`sma_200`, `ema_21`, `rs_value`, `rs_ratio_e21`, `rs_momentum_e21`）の直近5営業日に NULL がないこと
   - 例外: SPY 自身の RS 系カラム、データ期間が短い新規銘柄のウォームアップ期間（ema_21 は 21日未満、rs_ratio_e21 は 30日未満、rs_momentum_e21 は 75日未満）

## 5. SQLite への大量書き込み（リストア/修復時）

`restore_sqlite_cache_from_parquet()` のパターンに準拠:

- ネイティブ `sqlite3` の `executemany` を使う（SQLAlchemy ORM の行単位 INSERT は数十倍遅い）。
- バルク中のみ `PRAGMA synchronous = OFF` / `journal_mode = MEMORY`、**完了後に必ず WAL / NORMAL へ戻す**（API サーバー稼働中に戻し忘れると全体がフリーズする）。
- `NaN` は `df.where(pd.notnull(df), None)` で None（SQL NULL）に変換してから挿入。
- 自動採番の `id` カラムは `symbols` 以外では drop する（`symbols.id` は他テーブルの FK なので保持必須）。
- DataFrame のカラムをモデル定義の有効カラムでフィルタしてから挿入（スキーマ差分による失敗防止）。
