# deploy_after_merge.ps1（merge 後データ昇格の1コマンド化）計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: AI エージェント (Claude Code, claude-fable-5)
- **開始日**: 2026-07-09 / **完了日**: —
- **作業ブランチ**: worktree-claude-md-retry-rules（計画書のみ。実装は別ブランチ）
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

## 3. 変更内容（スクリプトの処理フロー案）

1. **前提チェック**: main チェックアウトで実行されているか / merge 済みか（`git status`・対象ブランチ）/ `update_pipeline.lock` なし / API サーバ停止（ポート8000の LISTEN 確認）/ ディスク空き容量
2. **世代記録**: 現在の `latest_master.json` の世代をロールバック用に記録
3. **再生成**: 本番 Parquet を一時ディレクトリへコピー → merge 済みコードでマイグレーション/再計算（`--rebuild-from` 相当。対象フェーズは引数指定可）
4. **swap**: 生成物を `data/parquet_master/` へ配置し `latest_master.json` を更新（旧世代ファイルは削除しない）
5. **restore**: `restore_sqlite_cache_from_parquet` で stocktool.db を再構築
6. **検証**: `tools/db_health_check.py --all --check-nulls`
7. **NG 時ロールバック**: `latest_master.json` を手順2の世代へ戻し restore を再実行、エラー内容を表示して終了
8. **完了表示**: 新世代タイムスタンプ・所要時間・「API 再起動可」の案内。旧世代の prune は行わない（次回 daily update または手動で）

## 4. ユーザー確認事項

1. **API 停止の扱い**: スクリプトが停止まで自動で行うか（プロセス kill）、起動中なら中断してユーザーに停止を促すか。→ 提案: **後者**（安全側）
2. **再計算の範囲指定**: 既定を `--rebuild-from T3`（indicator 変更の典型）とし、`-RebuildFrom T2|T4|T5` 引数で変更可、で良いか
3. **db_health_check の合格基準**: 既存の NG 判定をそのまま採用で良いか（追加チェックの要否）
4. **一時ディレクトリの場所**: `data/parquet_master_deploy_tmp/`（本番と同ドライブ、コピー高速）で良いか
5. **ロールバック後の扱い**: 自動で終了（原因調査はユーザー/エージェント）で良いか、それとも診断情報の自動収集まで行うか

## 5. 実装順序と進捗チェックリスト

- [ ] §4 のユーザー確認事項を解消し、本計画を確定する
- [ ] 既存部品のインターフェース調査（`create_sandbox.py` / `run_production_restore.py` / `restore_sqlite_cache_from_parquet` / `db_health_check.py` の引数・戻り値）
- [ ] Python 側の昇格ロジックにテストを追加（test-writer 委譲候補: 世代記録・swap・ロールバックの単体テスト）
- [ ] `deploy_after_merge.ps1` 本体の実装（implementer 委譲候補。ASCII のみ — PS5.1 の BOM なし CP932 誤読対策）
- [ ] sandbox 環境での模擬昇格テスト（本番相当のコピーに対して全手順＋意図的な health check 失敗でロールバック演習）
- [ ] 本番での初回実行はユーザー立ち会いのもと実施
- [ ] ドキュメント更新（sandbox-workflow スキルの手動手順を「非常用」に格下げ、§10 の参照を実体に合わせる）

### 作業中メモ

（未着手）

## 6. 検証プラン / 結果

- sandbox コピーに対する模擬昇格: 全手順成功 → health check 合格を確認
- ロールバック演習: health check を意図的に失敗させ（例: 欠損データ混入）、旧世代へ自動復帰し本番相当データが昇格前と一致することを確認
- 所要時間の計測（コピー / 再計算 / restore の内訳）を本欄に記録する

## 7. 途中発生した課題

（未着手）

## 8. スコープ外・残作業

- 種別 C（user_data.db の in-place マイグレーション）の共通フレームワーク化
- 長時間 run（最適化・バックテスト）ランチャー側での「昇格済み確認」自動化
- 旧世代 Parquet の保持世代数ポリシーと自動 prune
