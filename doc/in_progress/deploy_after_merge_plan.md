# deploy_after_merge.ps1（merge 後データ昇格の1コマンド化）計画書

- **ステータス**: 🚧 進行中（2026-07-09 §4 レビュー完了・実装着手）
- **実施者**: AI エージェント (Claude Code, claude-fable-5)
- **開始日**: 2026-07-09 / **完了日**: —
- **作業ブランチ**: worktree-claude-md-retry-rules（§10 等の規定類と同時に merge する必要があるため同ブランチで実装）
- **対象 issue / 関連ドキュメント**: `doc/agent_execution_rules.md` §10、`.claude/skills/sandbox-workflow/SKILL.md`

## 1. 背景と目的

ワークツリー運用（§7 改訂）により、コードは「ブランチ → merge」で本体に反映されるが、データ形状に触れる変更（種別 B: スキーマ・indicator・パイプライン）は merge 後に本番データへの昇格が別途必要になる。本来 CI/CD が担う「merge 後のマイグレーション＆デプロイ」に相当する手順（停止 → 再生成 → swap → restore → 検証 → 再開）を、ローカル環境向けに **1コマンド `tools/deploy_after_merge.ps1`** に集約する。

成功条件: ユーザーの操作が「merge → `.\tools\deploy_after_merge.ps1`」の2手順になり、health check 不合格時は本番が昇格前の状態に自動復帰すること。

## 2. スコープと設計判断

### 2.1 変更すること
- `tools/deploy_after_merge.ps1` の新規作成（オーケストレーションのみ。重い処理は既存 Python スクリプトへ委譲）
- 必要に応じて既存部品（`create_sandbox.py` / `run_production_restore.py`）へのコピー先・入力パス引数の追加

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 |
|---|---|
| 検証済み sandbox の swap 転用 | しない。昇格は常に「その時点の本番 Parquet ＋ merge 済みコード」から再生成（鮮度問題の構造的回避） |
| user_data.db / optimization_trials.db | 本スクリプトの対象外（swap 禁止。種別 C は別途 in-place マイグレーション） |
| GitHub Actions 等の CI | 導入しない（データがローカルのみ・長時間ジョブ・個人環境） |
| 実行判断の自動化 | しない。実行はユーザーが merge 後に手動で起動（判断だけ人間に残す） |
| API サーバの自動 kill | しない。起動中を検知したら中断してユーザーに停止を促す（2026-07-09 ユーザー判断） |
| ロールバック後の診断自動収集 | しない。ロールバックの発生を明示表示して終了、原因調査は別途（2026-07-09 ユーザー判断） |

## 3. 変更内容（スクリプトの処理フロー案）

1. **前提チェック**: main チェックアウトで実行されているか / merge 済みか（`git status`・対象ブランチ）/ `update_pipeline.lock` なし / API サーバ停止（ポート8000の LISTEN 確認。**起動中なら中断してユーザーに停止を促す — 自動 kill しない**）/ ディスク空き容量
2. **世代記録**: 現在の `latest_master.json` の世代をロールバック用に記録
3. **再生成**: 本番 Parquet を一時ディレクトリ **`data/tmp/parquet_master_deploy/`** へコピー → merge 済みコードでマイグレーション/再計算。範囲は `-RebuildFrom T2|T3|T4|T5|All`（既定 **T3**。`All` は T2 起点の全フェーズ再構築）
4. **swap**: 生成物を `data/parquet_master/` へ配置し `latest_master.json` を更新（旧世代ファイルは削除しない）
5. **restore**: `restore_sqlite_cache_from_parquet` で stocktool.db を再構築
6. **検証**: `tools/db_health_check.py --all --check-nulls`（既存の NG 判定をそのまま合格基準とする）
7. **NG 時ロールバック**: `latest_master.json` を手順2の世代へ戻し restore を再実行し、**ロールバックが発生したことを明示表示**して終了（診断情報の自動収集はしない）
8. **完了表示**: 新世代タイムスタンプ・所要時間・「API 再起動可」の案内。旧世代の prune は行わない（次回 daily update または手動で）

> [!NOTE]
> **再計算範囲の制約（2026-07-09 調査で確定）**: `--rebuild-from T3` 等はホット期間（SQLite の約730日分）だけを再計算し、`rotate_and_archive_to_parquet` の `keep='last'` マージでその期間のみ Parquet が置き換わる。**それ以前の履歴は旧計算式の値のまま残る。** 全履歴（7年）に計算式変更を効かせたい場合は `-RebuildFrom All`（= `--re-calculate`。yfinance 全再取得込み・長時間）を使うこと。

## 4. ユーザー確認事項

2026-07-09 に全5件レビュー済み。判断結果（§2.2 / §3 に反映済み）:

1. API 停止の扱い → **中断してユーザーに停止を促す**（自動 kill しない）
2. 再計算の範囲指定 → 既定 `T3`、`-RebuildFrom T2|T4|T5|All` で変更可（**All 対応を追加**）
3. db_health_check の合格基準 → **既存の NG 判定をそのまま採用**
4. 一時ディレクトリ → **`data/tmp/` 階層**（`data/tmp/parquet_master_deploy/`）
5. ロールバック後 → **自動終了。ただしロールバック発生を明示表示する**

## 5. 実装順序と進捗チェックリスト

- [x] §4 のユーザー確認事項を解消し、本計画を確定する（2026-07-09）
- [x] 既存部品のインターフェース調査（2026-07-09。判明事項は §7 参照）
- [x] Python 側の昇格ロジック `backend/pipeline/deploy_promotion.py` をテスト先行で実装（`backend/tests/pipeline/test_deploy_promotion.py` 10件 green）
- [x] オーケストレーター `backend/scripts/deploy_after_merge.py` と ラッパー `tools/deploy_after_merge.ps1` を実装（ASCII のみ）
- [x] 既存部品の小改修: `db_health_check.py`（NG 時 exit 1・`--db-path` 追加）/ `run_production_restore.py`（`--db-path` 追加・環境変数横取りガード）/ `create_sandbox.py`（既定出力を `data/sandbox/` へ）
- [x] 前提チェックのネガティブテスト（ポインタ無し・lock 存在 → exit 1 で本番無変更を確認）
- [ ] 実データでの `-DryRun` 実行（swap なし・ワークスペース検証まで）→ **ユーザー立ち会い時に実施**（2026-07-09 は別の長時間 Python ジョブ稼働中のため見送り）
- [ ] ロールバック演習（health check を意図的に失敗させ旧世代へ自動復帰することを確認）→ 同上
- [ ] 本番での初回実行はユーザー立ち会いのもと実施
- [x] ドキュメント更新（sandbox-workflow スキル: 手動手順を deploy_after_merge 参照に更新＋sandbox 配置バグの CAUTION 追記）

### 作業中メモ

実装完了・単体検証済み。残りは実データ検証（-DryRun → ロールバック演習 → 本番初回）で、いずれもユーザー立ち会いを推奨。実行手順:
```powershell
.\tools\deploy_after_merge.ps1 -DryRun          # 本番無変更で全経路を検証
.\tools\deploy_after_merge.ps1                  # 本番昇格（T3）
```

## 6. 検証プラン / 結果

| 検証 | 結果 |
|---|---|
| deploy_promotion 単体テスト（コピー/昇格/ロールバック/世代抽出） | ✅ 10件 green（2026-07-09） |
| 前提チェック: 本番ポインタ無し → exit 1・本番無変更 | ✅ 確認（2026-07-09） |
| 前提チェック: update_pipeline.lock 存在 → exit 1 | ✅ 確認（2026-07-09） |
| 実データ -DryRun（コピー→SQLite復元→パイプライン→health check） | ⏳ ユーザー立ち会い時 |
| ロールバック演習（意図的 health check 失敗 → 旧世代復帰） | ⏳ ユーザー立ち会い時 |
| 所要時間の計測 | ⏳ -DryRun 時に記録 |

## 7. 途中発生した課題

1. **`--rebuild-from` はホット期間のみ**（§3 の NOTE 参照）→ `All` オプションを全履歴用として仕様化
2. **【重要・別件バグ】sandbox-workflow スキルの旧手順が本番 Parquet を汚染し得る**: `data/stocktool_sandbox.db`（`data/` 直下）を指定すると Parquet 解決が本番 `data/parquet_master/` を指し、パイプライン終盤の archive が本番へ新世代を書く。コードに `parquet_master_sandbox` の参照は存在しない。→ スキルを `data/sandbox/` 専用ディレクトリ方式に修正、`create_sandbox.py` の既定値も変更
3. `db_health_check.py` が NG でも exit 0 だった → NG 件数で非ゼロ終了するよう改修（SPY 欠損も NG 扱い）
4. `run_production_restore.py` が `STOCKTOOL_DB_PATH` 環境変数に横取りされ「Parquet は A・書き込みは B」の不整合になり得た → 関数内で環境変数を解除するガードを追加
5. 実データ dry-run は別の長時間 Python ジョブ（7GB 級）稼働中のため見送り（リソース競合回避）
6. **【初回実行で発覚・修正済み】lock 残骸による誤中断**（2026-07-09）: `update_pipeline.py` の `release_lock` はロック解除のみでファイルを削除しないため、`update_pipeline.lock` は正常終了後も常に残る。前提チェックが「存在」で判定していたため、初回実行が誤中断した。→ `is_pipeline_lock_held()`（msvcrt 非ブロッキングロックの取得可否で判定）を `deploy_promotion.py` に追加し、テスト3件で担保。実環境の残骸 lock に対して held=False を確認済み
7. **【2回目実行で発覚・修正済み】未作成ワークスペースで disk_usage が FileNotFoundError**（2026-07-09）: 空き容量チェックが未作成の `data/tmp/` を `shutil.disk_usage` に渡していた。→ `nearest_existing_dir()`（存在する祖先まで遡って解決）を追加しテスト2件で担保。実環境パスで preflight が None（OK）になることを確認済み
8. **【3回目実行で発覚・修正済み／既存バグ】restore のバルクインサートが `database is locked`**（2026-07-09）: `bulk_insert_df_to_sqlite` が `PRAGMA journal_mode = MEMORY` を実行していたが、WAL からの journal_mode 変更は**他の接続が1つでも開いていると即失敗**する（sqlite-wal-handling スキルの既知パターン）。restore は同一プロセス内に SQLAlchemy セッション＋raw 接続を持つため構造的に失敗する。**deploy 固有ではなく `run_production_restore.py`・パイプラインの restore 経路全体に影響する既存バグ**。→ クロージャをモジュール関数へ抽出し、journal_mode を変更せず WAL 維持＋`busy_timeout`＋接続ローカルな `synchronous=OFF` のみに変更。redフェーズで本番と同一エラーの再現を確認した回帰テスト3件を追加（`test_parquet_cache_manager.py`）。全スイート 343 passed

## 8. スコープ外・残作業

- 種別 C（user_data.db の in-place マイグレーション）の共通フレームワーク化
- 長時間 run（最適化・バックテスト）ランチャー側での「昇格済み確認」自動化
- 旧世代 Parquet の保持世代数ポリシーと自動 prune
