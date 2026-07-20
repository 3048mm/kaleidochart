# stocktool プロジェクトの強制ルール

1. **Parquetデータの読込や並列実行（Optuna, モンテカルロ等）の変更を行う際の制約**:
   - バックテストやシナリオテストのキャッシュデータ読込、または並列処理の追加・変更を行う際は、開発前に必ず [project_knowhow.md](file:///C:/Users/crazy/.gemini/antigravity-ide/knowledge/agent_execution_rules/artifacts/project_knowhow.md) の **「6. バックテストキャッシュ (Parquet) 読込時のメモリ枯渇対策」** を読み込み、プッシュダウンフィルタ（日付バッファ含む）と並列数制限（max_workers=2）の設計を厳守すること。
