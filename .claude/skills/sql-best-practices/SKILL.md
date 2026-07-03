---
name: sql-best-practices
description: Best practices for writing high-performance, maintainable, and secure SQL queries and ORM interactions.
---

# SQL & リレーショナルデータベース設計ベストプラクティス (sql-best-practices)

この Skill は、エージェントがプロジェクト内で SQL クエリの記述、データベース設計、ORM (SQLAlchemy 等) の操作を行う際に従うべき**「パフォーマンス」「保守性」「安全性」**を最大化するためのベストプラクティスを定めたものです。

---

## 1. クエリパフォーマンスの最適化ルール

### 1-1. `SELECT *` の禁止とカラム指定の徹底
*   **原則**: 必要最小限のカラムのみを明示的に選択してください。
*   **理由**: 不要なカラム（特にテキスト型やバイナリ型）をフェッチすると、メモリ使用量とネットワーク/ディスクのI/O帯域が無駄になります。
*   **例外**: データ構造が動的な分析用スクリプトや一時的なテストクエリ。

### 1-2. N+1 クエリ問題の回避
*   **原則**: 関連するテーブル（親テーブルと子テーブルなど）をループ処理しながら1回ずつクエリするコード（N+1問題）を絶対に書かないでください。
*   **対策 (SQLAlchemy)**: `joinedload` や `selectinload` を使用して、必要なリレーションを事前に一括でロード（Eager Loading）します。
    ```python
    # 悪い例 (N+1問題が発生する)
    stocks = session.query(Stock).all()
    for stock in stocks:
        print(stock.theme.name)  # 各ループで個別にクエリが走る
    
    # 良い例 (一括でロード)
    from sqlalchemy.orm import joinedload
    stocks = session.query(Stock).options(joinedload(Stock.theme)).all()
```

### 1-3. インデックスの有効活用
*   **原則**: `WHERE`, `JOIN` (ON句), `ORDER BY`, `GROUP BY` で頻繁に使用されるカラムには必ずインデックスを設計してください。
*   **注意**: 複合インデックスを設計する場合は、インデックスの順序（最左プレフィックスルール）に留意してください。

---

## 2. 安全性とセキュリティ (セキュリティガードレール)

### 2-1. SQLインジェクションの徹底排除
*   **原則**: 動的クエリを組み立てる際、文字列結合 (`+` や f-strings) による SQL 文字列の生成は**厳禁**です。
*   **対策**: 必ずデータベースドライバまたは ORM が提供するパラメータ化クエリ（プレースホルダー）を使用してください。
    ```python
    # 厳禁（インジェクションの脆弱性）
    query = f"SELECT * FROM stocks WHERE ticker = '{user_input}';"
    
    # 推奨（パラメータ化）
    conn.execute("SELECT * FROM stocks WHERE ticker = ?;", (user_input,))
```

### 2-2. 破壊的クエリのガードレール
*   **原則**: `DELETE` または `UPDATE` を実行する際は、必ず `WHERE` 句が存在することを確認してください。
*   **対策**: テーブル全体のデータを誤って削除・更新してしまうことを防ぐため、エージェントは引数や条件なしの破壊的クエリを実行する前に、必ず人間の承認を得る必要があります。

---

## 3. データベース設計と命名規則

### 3-1. 命名規則の統一
*   **テーブル名 / カラム名**: `snake_case` (小文字かつアンダースコア区切り) で統一し、複数形/単数形のルールをプロジェクト内で一貫させます（例: `stocks`, `stock_prices`）。
*   **外部キー名**: 参照先テーブル名_参照カラム名（例: `stock_id`）に統一します。

### 3-2. 日付・時刻のデータ型
*   **原則**: タイムゾーンの扱いを一貫させるため、日付時刻データは `UTC` で統一して保存することを基本とします。
*   **SQLite の注意点**: SQLite には厳密な日付型がないため、ISO 8601 形式の文字列（`YYYY-MM-DD HH:MM:SS`）または UNIX タイムスタンプ（`INTEGER`）のいずれか一方にプロジェクト全体で統一してください。

---

## 4. 可読性とクエリフォーマット

### 4-1. 予約語の大文字表記
*   SQL のキーワード（`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `FROM`, `JOIN`, `WHERE`, `GROUP BY`, `ORDER BY`, `LIMIT` など）は、可読性を高めるためにすべて大文字で記述します。

### 4-2. 複雑なクエリでの CTE (WITH 句) の使用
*   サブクエリが何重にもネストされた複雑なクエリは避け、**CTE (Common Table Expressions / `WITH` 句)** を使用してクエリを論理的なステップに分割してください。
    ```sql
    WITH target_stocks AS (
        SELECT id, ticker FROM stocks WHERE sector = 'Technology'
    ),
    recent_prices AS (
        SELECT stock_id, MAX(date) AS latest_date FROM stock_prices GROUP BY stock_id
    )
    SELECT ts.ticker, rp.latest_date
    FROM target_stocks ts
    JOIN recent_prices rp ON ts.id = rp.stock_id;
    ```

---
このルールは、エージェントがプロジェクト内で SQL クエリの作成、DBスキーマの設計、マイグレーションファイルの構築を行う際に最優先で適用されます。
