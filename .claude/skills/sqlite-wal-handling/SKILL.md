---
name: sqlite-wal-handling
description: Best practices for managing SQLite with WAL mode, avoiding database locks, and resolving deadlocks on Windows environments.
---

# SQLite WAL & デッドロック対策ガイド (sqlite-wal-handling)

SQLite の WAL (Write-Ahead Logging) モードは、読み込みと書き込みを並行して実行できる非常に強力な機能ですが、設定を誤ると**「プロセスが完全にフリーズする（デッドロック）」**または**「SQLITE_BUSY (Database is locked)」**エラーを多発させます。
特に Windows 環境（OneDrive などの同期フォルダ下）ではファイルロックの競合でフリーズしやすくなります。

この Skill は、エージェントが SQLite を使用したコード（標準 `sqlite3` または `SQLAlchemy`）を記述・修正・デバッグする際に、**フリーズを100%回避するための強固な実装ルール**を定めたものです。

---

## 1. なぜ WAL モードでフリーズ（ロック）するのか？

### 原因 A: トランザクションロックの「昇格（Upgrade）」によるデッドロック（最重要）
デフォルトのトランザクション（`DEFERRED`）では、最初は「読み込み（共有ロック）」の状態で開始します。その後、同じトランザクション内で「書き込み（排他ロック）」が発生した際、他の接続も同様に「読み込みから書き込みへの昇格」を待ち合わせていると、**お互いの読み込みロック解除を無限に待ち続ける「デッドロック」が発生してフリーズします。**

### 原因 B: `busy_timeout` の未設定
競合が発生した際、デフォルトでは即座にエラーを吐くか、待機せずにロックがかかります。ミリ秒単位の待機時間を設けないと、並行処理で簡単にフリーズします。

### 原因 C: Windows / クラウド同期（OneDrive 等）の干渉
`d:\My Documents` などのフォルダが OneDrive などのクラウド同期対象になっている場合、SQLite が WAL モード時に作成する一時ファイル（`.shm` 共有メモリファイル、`.wal` ログファイル）に同期プログラムがファイルロックをかけてしまい、SQLite のプロセスごと完全にフリーズします。

### 原因 D: Checkpoint の餓死（トランザクションの閉じ忘れ）
読み込みトランザクションが1つでも開いたままだと、WAL からメインDBへのデータの書き戻し（Checkpoint）が完了せず、WAL ファイルが肥大化し、最終的に全スレッドがフリーズします。

---

## 2. 解決のための「黄金の3大設定」

SQLite 接続時、すべての接続で以下の Pragma を必ず実行してください。

1.  **`PRAGMA journal_mode = WAL;`**（並行読書を許可）
2.  **`PRAGMA busy_timeout = 5000;`**（ロック競合時、5000ms ＝ 5秒間自動で待機してリトライ）
3.  **`PRAGMA synchronous = NORMAL;`**（WALモードでは NORMAL が最も安全かつ高速。ディスク同期の過度なフリーズを防止）

---

## 3. Python 標準 `sqlite3` での実装パターン

標準の `sqlite3` を使用して、デッドロックを起こさない書き込みトランザクションを実行するコード例です。

```python
import sqlite3
import contextlib

@contextlib.contextmanager
def get_db_connection(db_path: str):
    # isolation_level=None にすることで、自動トランザクションを無効化し
    # 手動で明示的なトランザクション制御（BEGIN IMMEDIATE）を行えるようにします。
    conn = sqlite3.connect(db_path, isolation_level=None)
    
    # 黄金設定の適用
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")  # 5秒待機
    
    try:
        yield conn
    finally:
        conn.close()

# --- 安全な書き込み操作 of 実行例 ---
def safe_write_operation(db_path: str, data: dict):
    with get_db_connection(db_path) as conn:
        # 【超重要】書き込みを行う際は BEGIN IMMEDIATE で開始し、
        # 最初から書き込みロックを確保してデッドロックを防ぐ。
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                "INSERT INTO stocks (ticker, name) VALUES (?, ?);", 
                (data["ticker"], data["name"])
            )
            conn.execute("COMMIT;")  # 成功したら即座にコミット
        except Exception as e:
            conn.execute("ROLLBACK;")  # エラー時はロールバック
            raise e
```

---

## 4. SQLAlchemy 2.0 での実装パターン

SQLAlchemy で SQLite + WAL モードを使用する場合、イベントリスナーを活用して自動的に設定を適用するのが最もエレガントで確実です。

```python
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# 1. timeout 引数で busy_timeout (秒単位) を指定
engine = create_engine(
    "sqlite:///stocktool.db",
    connect_args={"timeout": 30}  # 30秒間ビジータイムアウトを待機
)

# 2. 接続確立時に WAL モードと同期設定を自動適用
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")  # ミリ秒指定
    cursor.close()

# 3. トランザクション開始時（begin）に自動で "BEGIN IMMEDIATE" を発行
# これにより、SQLAlchemy のセッションがデッドロックを起こすのを完璧に防ぎます。
@event.listens_for(engine, "begin")
def do_begin(conn):
    conn.exec_driver_sql("BEGIN IMMEDIATE")

# セッションファクトリの定義
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

---

## 5. エージェント用チェックリスト（デバッグ・コード生成時）

SQLite を扱うコードを修正する際は、以下のチェックリストをすべて満たしているか必ず確認してください：

- [ ]  **すべての接続で `busy_timeout` が 5000ms 以上に設定されているか？**（未設定は厳禁）
- [ ]  **`synchronous = NORMAL` になっているか？**（`FULL` だと書き込み時にフリーズしたようになります）
- [ ]  **書き込みが発生するトランザクションは、`BEGIN IMMEDIATE` で開始されているか？**（デフォルトの `BEGIN` はデッドロックの元）
- [ ]  **読み取り専用のセッションに `BEGIN IMMEDIATE` を適用していないか？**（SELECT が書き込みロックを要求してしまい、長時間バッチにブロックされて WAL の並行読み取りの利点が失われる。本プロジェクトでは `database.py` で読み取り用 `get_db()`＝DEFERRED と書き込み用 `get_write_db()`＝IMMEDIATE のエンジンを分離している）
- [ ]  **BEGIN IMMEDIATE セッション（write セッション）内で以下の3つを実行していないか？**（2026-07-04 のパイプライン障害の実例。いずれも autobegin で「トランザクション内」になるため発火する）
    1. `pd.read_sql(query, db.bind)` — pandas の新規接続も IMMEDIATE を発行し、自セッションの RESERVED ロックと**自己デッドロック**（busy_timeout まで無音ハング）。→ `db.commit()` 後に読み取りエンジン（`get_read_engine_for(db)`）で読む
    2. `PRAGMA synchronous` の変更 — "Safety level may not be changed inside a transaction" → 接続確立時（connect イベント）でのみ設定
    3. `VACUUM` — "cannot VACUUM from within a transaction" → `db.commit()` 後に素の `sqlite3.connect(db_path)` で実行
- [ ]  **トランザクションは `with` ブロックや `finally` を使って、処理終了後ただちに `commit/rollback` または `close` されているか？**（開きっぱなしのリード接続は WAL チェックポイントを阻害します）
- [ ]  **DBファイルが OneDrive 等の同期フォルダ配下にある場合は、環境上の警告メッセージを出すか、同期除外を推奨するドキュメントがあるか？**

---
このルールは、エージェントがプロジェクト内で SQLite / DB 関連の操作を行う際に最優先で適用されます。
