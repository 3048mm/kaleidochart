# stocktool プロジェクト固有ノウハウ (Project-Specific Know-How)

本ドキュメントは、stocktool プロジェクト特有の落とし穴・注意点をまとめたものです。
一般的なエージェント動作ルールは [agent_execution_rules.md](agent_execution_rules.md) を参照。

---

## 1. データベース未初期化エラー (`SessionLocal is None`)

### 問題
`backend/db/database.py` の `SessionLocal` はモジュールレベルでは `None` で初期化されており、`init_db()` を呼ぶまで使用できない。
CLI スクリプトや検証コードで直接 `from db.database import SessionLocal` とインポートしただけでは `TypeError: 'NoneType' object is not callable` が発生する。

### 対策ルール
- DB を使うスクリプトでは、**必ず `init_db(db_path)` を先に呼び出す**こと。
- 正しい順序:
  ```python
  from db.database import init_db, SessionLocal
  init_db('path/to/stocktool.db')
  db = SessionLocal()
  ```
- DB パスは `config.toml` の `system.db_path` から取得するか、プロジェクトルート直下の `stocktool.db` を指定する。

---

## 2. API エンドポイントのタイムアウト

### 問題
大規模テーブル（`relative_ranks` 等）に対する重いクエリを含む API エンドポイントが、データ蓄積に伴いタイムアウトする場合がある。
`urllib` のデフォルトタイムアウト（数秒）では応答が返らない。

### 背景
- `relative_ranks` テーブルは日次で全銘柄×複数指標のレコードが蓄積されるため、数百万行規模になる。
- `SELECT DISTINCT indicator_name FROM relative_ranks`（日付フィルタなし）のような全件走査クエリがタイムアウトの主因。
- 対策として `/api/screener/meta` では最新日のみに絞る改修を実施済み (2026-04-08)。

### 対策ルール
- API の動作確認を行う場合は、タイムアウトを **30秒以上** に設定する。
- タイムアウトが発生した場合は、API 内部のクエリを疑い、日付絞り込みの有無を確認する。
- API の検証よりも、**関数を直接インポートして単体テスト**する方が高速で確実（DB セッションを自分で作成して渡す）。

---

## 3. TOML パーサー (`tomli`) の注意点

### 問題
- TOML の真偽値は `true` / `false` (小文字)。Python の `True` / `False` を書くとパースエラー。
- バックエンド側で認識しないカスタムフィルタキーを `[*.filters]` セクションに追加すると、無視されて機能しない（`special` フィールドとして別途定義が必要）。

### 対策ルール
- TOML ファイルを編集した後は、パースが成功することを必ず確認する:
  ```python
  import tomli
  with open('path/to/file.toml', 'rb') as f:
      config = tomli.load(f)
  print(config)
  ```

---

## 4. バックテストキャッシュ (Parquet) の整合性

### 問題
- パイプライン更新後にバックテストキャッシュが古い状態のまま残り、新しいデータが反映されないことがある。
- `datetime.date` と `Timestamp` の型不一致で、フィルタリングが無言で 0 件ヒットになるケースがある。

### 対策ルール
- データパイプラインの更新後にバックテストを実行する場合は、`--refresh-cache` フラグを付けてキャッシュを更新する:
  ```powershell
  python backend/backtest/backtest_runner.py --refresh-cache
  ```
- DB とキャッシュの整合性は `verify_db_vs_cache.py` で検証できる。

---

## 5. 課題リストの方針 (doc/issue_list.md)

### 概要
プロジェクト全体の保留事項、中長期的な課題、会話中に発生した「後回し」のタスクは [doc/issue_list.md](issue_list.md) で一元管理する。

### 運用ルール
- **追記タイミング**: ユーザーからの「後回しにして」という指示や、将来的な懸案事項が発生した際、速やかに課題リストへ追記する。
- **更新フロー**: 各タスクの完了時には、項目を `[x]` に更新し、完了日を記載する。
- **事前確認**: エージェントは新しい作業の開始前にリストを確認し、関連する課題があればユーザーに優先順位を提案する。

---

## 6. 自己更新ルール

- 本ドキュメントに記載されていない **stocktool 固有の問題パターン**を発見した場合、発生状況と対策を含めて本ドキュメントに追記すること。
- 追記時は、問題の「症状」「原因（背景）」「対策ルール」の3点セットを必ず含めること。
- 更新履歴に日付と変更内容を記録すること。

---

## 7. 大規模ファイルに対する `grep_search` のタイムアウト

### 問題
`routers.py` や `DashboardPage.tsx` のような大規模なソースファイル（1000行超）に対し、`grep_search` を実行するとタイムアウトが発生し、エージェントが固まる（または応答が極端に遅くなる）ことがある。

### 原因
ブラウザツールやエージェントのバックエンド側での検索処理、またはファイル読み込みのオーバーヘッドが大きいため。

### 対策ルール
- **チャンク読込の活用**: 検索が失敗する場合は `grep` を諦め、`view_file` で 500〜800 行ずつチャンクとして読み進める。
- **PowerShell の利用**: `run_command` を使い、OS レベルの `Select-String` (grep 相当) を実行して行番号を特定する。
  ```powershell
  $OutputEncoding = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8;
  Select-String -Path "backend/api/routers.py" -Pattern "@router.get\(\"/dashboard\""
  ```
- **ノウハウの蓄積**: 一度特定した重要パスや行番号はメモし、再検索の手間を省く。

---

## 8. 環境間でのデータベース・スキーマの不整合 (Schema Drift)

### 問題
`models.py` に新しいカラムを追加しても、既存の `stocktool.db` や `stocktool_sandbox.db` には自動反映されない（SQLite のため）。
別環境や古いブランチから持ち込んだ DB ファイルを使用すると、新機能アクセス時に `sqlalchemy.exc.OperationalError: no such column: ...` が発生し、API が 500 エラーを返す。

### 対策ルール
- **スキーマ変更時の必須ルール** : 最初は本番 DB ではなく sandbox DB を使用すること。 全ての検証完了後に最終確認として本番 DB にも適用することをタスクリストに追加すること。
- **検証前のチェック**: API が 500 エラーを出し、ログに `no such column` が出た場合は、まず DB のスキーマを確認する。
- **サンドボックスの初期化**: 迷った場合は、`backend/scripts/update_pipeline.py` を使ってサンドボックス DB を最新スキーマで再作成する。

---

## 9. フロントエンドにおける数値フィールドの Null-safety

### 問題
バックエンドから新しい指標や、未計算のデータが `null` で返ってきた場合、フロントエンドで `.toFixed()` や比較演算を行うとランタイムエラー（クラッシュ）が発生し、画面が空白になる。

### 対策ルール (Backend)
- API レスポンスを構築するヘルパー関数（`_build_panel_item` 等）では、必ず `float(val or 0.0)` のように **None を数値にキャスト**してから返すこと。これにより、フロントエンドでの型不一致を最小限に抑える。

### 対策ルール (Frontend)
- 数値を表示するコンポーネント内では、`(value || 0).toFixed(2)` のように、**常に fallback 値（0など）を持たせる**こと。
- 共通コンポーネント（`SummaryTable`, `EtfFeaturePanel` 等）を修正する際は、既存の全画面に影響が及ぶため、特に厳格な Null チェックを行う。

---

## 10. サンドボックスDBを使用したテスト実行ルール (Sandbox Testing Protocol)

### 原則
本作業環境では、本番データ (`data/stocktool.db`) を保護するため、実験や破壊的なテストにはサンドボックスDB (`data/stocktool_sandbox.db`) を使用する。その際、**`config.toml` は書き換えず、環境変数によって動的に切り替えること。**

### ルール
- **環境変数の利用**: テスト実行時のみ、一時的な環境変数 `STOCKTOOL_DB_PATH` をセットして実行する。
  - PowerShell 例: `$env:STOCKTOOL_DB_PATH = "data/stocktool_sandbox.db"; python backend/api/server.py`
  - 完了後は必ずセッションを閉じるか、環境変数をクリア (`$env:STOCKTOOL_DB_PATH = $null`) する。
- **グローバル設定の禁止**: OS のシステム環境変数や、プロジェクトの `config.toml` にサンドボックスのパスを永続的に書き込んではならない。
- **ログによる判別**: `init_db()` 実行時に、参照している DB パスが本番以外である場合は、コンソールに警告を表示するように設計する。

---

## 11. 接続中データベースの視覚的・プログラム的確認方法 (Environment Identity Verification)

### 問題
サンドボックス DB と本番 DB を切り替えて検証を行う際、現在どちらの DB に書き込みや読み込みを行っているかの判断ミス（ヒューマンエラー）が発生しやすい。

### 対策ルール (Visual & Programmatic Check)
1.  **フロントエンドの警告バッジ**:
    - 本番 DB (`stocktool.db`) 以外に接続されている場合、画面ヘッダー左上に **オレンジ色の警告バッジ（例: `⚠️ DB: stocktool_sandbox.db`）** が表示される。
    - 検証作業中は、このバッジが意図した DB 名を表示していることを必ず確認する。
2.  **システム情報 API による確認**:
    - Web ブラウザや `curl` 等で直接 `/api/system/info` を叩くことで、接続状況の JSON を取得できる。
    - レスポンス例: `{"db_name": "stocktool_sandbox.db", "is_production": false, ...}`
3.  **環境変数の優先適用**:
    - 迷った場合は、環境変数 `STOCKTOOL_DB_PATH` を明示的にセットしてプロセスを起動する（CLI / Server 双方有効）。

---

## 更新履歴
- 2026-04-10: 初版作成
- 2026-04-11: 課題リストの方針 (doc/issue_list.md) を追記
- 2026-04-12: 大規模ファイルに対する `grep_search` のタイムアウト対策を追記
- 2026-04-18: 環境間スキーマ不整合 (Schema Drift) および Null-safety ルールの追記
- 2026-04-18: サンドボックスDB使用ルールの明文化、およびDB識別バッジ・システム情報APIによる確認手順の追記
