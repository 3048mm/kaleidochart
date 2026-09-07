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
| **Sandbox** | `data/sandbox/stocktool_sandbox.db` | `data/sandbox/parquet_master/` |

- 切り替えは環境変数 `STOCKTOOL_DB_PATH` で行う。Parquet ディレクトリは DB パスから自動解決される（`parquet_cache_manager.get_parquet_master_dir()` — **DB と同じディレクトリの `parquet_master/`**）。

> [!CAUTION]
> **Sandbox は必ず専用ディレクトリ（`data/sandbox/` 等）に置くこと。** 旧手順の `data/stocktool_sandbox.db`（本番と同じ `data/` 直下）は、Parquet 解決が `data/parquet_master/` = **本番 Parquet** を指すため、パイプライン実行終盤の `rotate_and_archive_to_parquet` が **Sandbox の計算結果で本番 Parquet の新世代を書いてしまう**（2026-07-09 のコード調査で判明。`parquet_master_sandbox` という名前を参照するコードは存在しない）。
- `user_data.db`（ウォッチリスト・ポートフォリオ）はユーザー永続データであり、Sandbox 検証の対象外。**絶対にクリア・再構築しない。**

> [!CAUTION]
> **`STOCKTOOL_DB_PATH` を設定するときは、必ず `STOCKTOOL_USER_DB_PATH`（例: `data/sandbox/user_data_sandbox.db`）も併せて設定すること。**
> API サーバーの watchlist/portfolio には `heal_*_ids()` という自己修復処理があり、GET のたびに ticker→symbol_id を接続中の stocktool DB と突合して **user_data.db を破壊的に UPDATE・commit する**。
> stocktool 側だけ Sandbox に向けると、Sandbox に存在しない銘柄の symbol_id が本番 `user_data.db` で全て NULL 化される事故が起きる（2026-07-04 に実際に発生。ticker 列は無傷のため本番 DB に対する heal 再実行で復旧済み）。

### 1.1 ワークツリーから利用する場合（バックグラウンドジョブ・並列ワーカー）

**環境変数を手で設定するのではなく、プロビジョニングスクリプトを1回実行する**（2026-09-01 変更）。

```powershell
# データ形状に触れる検証（スキーマ変更・indicator 追加・パイプライン変更）
.\venv\Scripts\python.exe tools\provision_worktree_data.py <worktree> --mode write
```

これで `<worktree>/data/sandbox/` に検証環境が揃い、`config.local.toml` に所在が記録される。
以降そのワークツリーでは環境変数なしで正しい Sandbox を掴む。

- **Parquet はハードリンク**で張られる（同一ボリューム・特権不要）。実測でディスク消費
  **4,096 バイト**（見かけ 3.56GB）。Parquet の MVCC は新しいタイムスタンプ名で書き、
  ポインタは `os.replace` で差し替えるため、**sandbox 側の書き込みが本番へ抜けることはない**。
  ただし「既存ファイル名への in-place 上書き」を新たに書くと本番に書き抜けるので**禁止**
  （回帰テストで担保済み）。
- `user_data.db` / `universe.db` は**実コピー**。ユーザー資産なので本番を指させない。
- **未プロビジョニングのまま実行しても空DBはできない**。`backend/paths.py` の
  `DataNotProvisionedError` で即座に停止し、メッセージに復旧コマンドが出る。
  「存在するが中身が空」（pytest が残す 139,264 バイトのスキーマだけの DB）も弾く。
- 本番配下への書き込みは `ensure_writable()` が `ProductionWriteError` で拒否する。
- Sandbox は**ワークツリー内に閉じる**ので、複数ワーカーが共有 Sandbox を取り合わない。
- ワークツリー内 Sandbox は**昇格の元ネタにしない**（昇格は merge 後に本番データから再生成 — §2 Step 4 参照）。ワークツリー削除と同時に破棄する。
- `git add` は明示パスのみ（Sandbox の Parquet を誤コミットしない）。データ運用全般の規定は `doc/agent_execution_rules.md` §10.3。

> [!CAUTION]
> **`data/` にジャンクション（`mklink /J`）を張ってはいけない。**
> `git worktree remove` は**ジャンクションを辿ってリンク先の中身を全削除する**
> （2026-08-30 に実測。`rm -rf` / `git clean -xdf` / `Remove-Item -Recurse` は辿らないが、
> `git worktree remove` だけは辿る）。本番 `data/` を指したまま実行すると全損する。
> シンボリックリンクは管理者権限または開発者モードが必要なので、そもそも作れない
> （`WinError 1314`）。**ファイル単位のハードリンクだけが安全な共有手段。**

## 2. 検証フロー（5ステップ）

### Step 1: Sandbox データ準備
本番の最新 Parquet マスターを **専用ディレクトリへ** コピーする（数秒で完了）:
```powershell
New-Item -ItemType Directory -Force data\sandbox | Out-Null
Copy-Item -Recurse -Force data\parquet_master data\sandbox\parquet_master
```
軽量な SQLite Sandbox が必要な場合は `python backend/scripts/create_sandbox.py`（主要銘柄+テーマ、直近300日分を抽出）も利用可能。

### Step 2: 隔離テスト (Isolate & Test)
```powershell
$env:STOCKTOOL_DB_PATH="data/sandbox/stocktool_sandbox.db"
$env:STOCKTOOL_USER_DB_PATH="data/sandbox/user_data_sandbox.db"
python backend/scripts/update_pipeline.py   # 例: 新指標を含むパイプライン実行
```
実行後、`data/sandbox/` の SQLite と `parquet_master/` に対して:
- 新カラムが期待通りの値で計算されているか **SQL で直接検証**する（「処理が通った」だけでは不十分。カラムの値・NULL有無まで確認する）
- `python tools/db_health_check.py --all --check-nulls` で整合性チェック

### Step 3: 視覚的確認 (Verify)
API サーバーを Sandbox DB に向けて起動し、以下を確認:
- フロントエンド画面左上に**オレンジ色の警告バッジ**（`⚠️ DB: stocktool_sandbox.db`）が表示されること（= 本番を触っていない証拠）
- `/api/system/info` の `is_production` フラグが `false` であること
- チャート・テーブルで新指標がエラーなくレンダリングされること

### Step 4: 本番コールドの更新 (Promote to Cold)

> [!NOTE]
> コード変更が merge を経由する運用（ワークツリー開発）では、Step 4-5 は merge 直後に `tools/deploy_after_merge.ps1` として1コマンド実行に集約する（前提チェック → 本番 Parquet に新コードで再適用 → swap → restore → health check → NG 時ロールバック。計画書: `doc/in_progress/deploy_after_merge_plan.md`）。検証に使った Sandbox のデータはそのまま swap **せず**、常に「その時点の本番データ＋merge 済みコード」から再生成する — 検証と昇格の間に daily update が走っていても鮮度問題が起きない。

手動で行う場合: 検証が 100% 成功したら、`parquet_master_sandbox/` の検証済み Parquet ファイル群を本番 `data/parquet_master/` へアトミックに差し替える（タイムスタンプ付きファイルのコピー + `latest_master.json` ポインタ更新）。**health check 合格まで旧世代ファイルを prune しない**（MVCC 旧世代がロールバック用バックアップを兼ねる）。

### Step 5: 本番ホットの同期 (Promote to Hot)
本番 SQLite の該当キャッシュテーブルをクリアし、`restore_sqlite_cache_from_parquet`（`backend/pipeline/parquet_cache_manager.py`）で本番 Parquet マスターから直近2年分をバルクリストアする（約3分/200万行）。昇格中は daily update と API サーバを停止すること。

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
