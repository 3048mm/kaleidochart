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

## 5. 自己更新ルール

- 本ドキュメントに記載されていない **stocktool 固有の問題パターン**を発見した場合、発生状況と対策を含めて本ドキュメントに追記すること。
- 追記時は、問題の「症状」「原因（背景）」「対策ルール」の3点セットを必ず含めること。
- 更新履歴に日付と変更内容を記録すること。

---

## 更新履歴
- 2026-04-10: 初版作成（DB未初期化、APIタイムアウト、TOMLパーサー注意点、キャッシュ整合性の4項目）
