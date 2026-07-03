---
name: sandbox-workflow
description: Sandbox isolation, verification, and production promotion workflow for schema changes, new indicators, or pipeline logic changes. Use BEFORE modifying DB schema, adding indicator columns, or changing pipeline/calculation logic that touches stocktool.db or data/parquet_master/.
---

# Sandbox 検証・本番プロモーション手順 (sandbox-workflow)

DBスキーマ変更・新規指標の追加・パイプラインロジック変更を行う際は、**本番サービス（Uvicorn/API）を無停止・ノーリスクで維持したまま Sandbox 環境で検証**する。本番データ（`data/stocktool.db`, `data/parquet_master/`）に対して未検証のスキーマ変更・再計算を直接実行することは**禁止**。

---

## 1. 環境の構成

| 環境 | ホットDB (SQLite) | コールドマスター (Parquet) |
| :--- | :--- | :--- |
| **本番** | `data/stocktool.db` | `data/parquet_master/` |
| **Sandbox** | `data/stocktool_sandbox.db` | `data/parquet_master_sandbox/` |

- 切り替えは環境変数 `STOCKTOOL_DB_PATH` で行う。Parquet ディレクトリは DB パスから自動解決される（`parquet_cache_manager.get_parquet_master_dir()` — DB と同じディレクトリの `parquet_master/`）ため、Sandbox 用 DB パスを指定すれば Parquet も分離される。
- `user_data.db`（ウォッチリスト・ポートフォリオ）はユーザー永続データであり、Sandbox 検証の対象外。**絶対にクリア・再構築しない。**

## 2. 検証フロー（5ステップ）

### Step 1: Sandbox データ準備
本番の最新 Parquet マスターを Sandbox にコピーする（数秒で完了）:
```powershell
Copy-Item -Recurse -Force data\parquet_master data\parquet_master_sandbox
```
軽量な SQLite Sandbox が必要な場合は `python backend/scripts/create_sandbox.py`（主要銘柄+テーマ、直近300日分を抽出）も利用可能。

### Step 2: 隔離テスト (Isolate & Test)
```powershell
$env:STOCKTOOL_DB_PATH="data/stocktool_sandbox.db"
python backend/scripts/update_pipeline.py   # 例: 新指標を含むパイプライン実行
```
実行後、`stocktool_sandbox.db` と `parquet_master_sandbox/` に対して:
- 新カラムが期待通りの値で計算されているか **SQL で直接検証**する（「処理が通った」だけでは不十分。カラムの値・NULL有無まで確認する）
- `python tools/db_health_check.py --all --check-nulls` で整合性チェック

### Step 3: 視覚的確認 (Verify)
API サーバーを Sandbox DB に向けて起動し、以下を確認:
- フロントエンド画面左上に**オレンジ色の警告バッジ**（`⚠️ DB: stocktool_sandbox.db`）が表示されること（= 本番を触っていない証拠）
- `/api/system/info` の `is_production` フラグが `false` であること
- チャート・テーブルで新指標がエラーなくレンダリングされること

### Step 4: 本番コールドの更新 (Promote to Cold)
検証が 100% 成功したら、`parquet_master_sandbox/` の検証済み Parquet ファイル群を本番 `data/parquet_master/` へアトミックに差し替える（タイムスタンプ付きファイルのコピー + `latest_master.json` ポインタ更新）。

### Step 5: 本番ホットの同期 (Promote to Hot)
本番 SQLite の該当キャッシュテーブルをクリアし、`restore_sqlite_cache_from_parquet`（`backend/pipeline/parquet_cache_manager.py`）で本番 Parquet マスターから直近2年分をバルクリストアする（約3分/200万行）。

## 3. チェックリスト

作業前:
- [ ] この変更はスキーマ・指標・パイプラインロジックに触れるか？ → Yes なら本フロー必須
- [ ] `STOCKTOOL_DB_PATH` が Sandbox を指しているか？（未設定のまま実行すると本番に書き込まれる）

プロモーション前:
- [ ] pytest 全件パス（`$env:PYTHONPATH="backend"; python -m pytest backend/tests/ -v`）
- [ ] `db_health_check.py` で T2/T3 行数一致・SPY 同期・NULL なしを確認
- [ ] 新カラムの値を SQL でサンプル検証済み（期待値との突合）
- [ ] フロントエンドで警告バッジと新指標の表示を目視確認済み

プロモーション後:
- [ ] `/api/system/info` で `is_production: true` を確認
- [ ] 本番フロントエンドで警告バッジが**消えている**こと、データが最新であることを確認
