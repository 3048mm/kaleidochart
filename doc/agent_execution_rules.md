# エージェント一般動作ルール (General Agent Execution Rules)

本ドキュメントは、AI エージェントがコマンド実行・ファイル操作を行う際に、プロジェクトに関わらず共通して発生する問題パターンと対策ルールをまとめたものです。
エージェントはこのドキュメントのルールを遵守し、無駄なリトライを回避すること。

---

## 1. PowerShell での Python ワンライナー (`python -c`) の制限

### 問題
PowerShell 上で `python -c "..."` を使って複数文の Python コードを記述する際、以下の問題が頻発する。

| 症状 | 原因 |
| :--- | :--- |
| `SyntaxError: unterminated string literal` | f-string 内のクォート (`'`, `"`) が PowerShell のクォート処理と衝突 |
| `SyntaxError: invalid syntax` (try/except) | `try/except` ブロックは Python の `-c` モードではセミコロン区切りで記述不可 |
| 意図しないエスケープ | PowerShell が `\"` を二重展開し、Python に渡る文字列が壊れる |

### 対策ルール
- **3行以下の単純なコード**: `python -c` を使用してよい。ただし f-string 内にクォートを含めない。
- **4行以上、または try/except / for / if を含むコード**: **必ずスクリプトファイル（`.py`）を作成**してから `python script.py` で実行する。`-c` での無理な記述を禁止する。
- **スクリプトファイルの配置場所**: プロジェクト内の `tmp/` ディレクトリに一時ファイルとして作成する（例: `tmp/debug_check.py`）。

---

## 2. PowerShell のパス・ワーキングディレクトリの誤り

### 問題
コマンド実行時に作業ディレクトリ (`Cwd`) のパスにタイプミスがあると、ツール自体がエラーを返す。

### 対策ルール
- `Cwd` は常にプロジェクトのルートパスを正確にコピー&ペーストで指定する。手動入力しない。
- パス内のスペースを含むパスは、必要に応じてクォートで囲む。

---

## 3. 依存パッケージの未インストール

### 問題
`pytest` 等のツールが venv にインストールされていない状態で実行し、`No module named ...` エラーが発生する。

### 対策ルール
- 新しいツール（`pytest`, `optuna-dashboard` 等）を使用する前に、`pip list` または `pip show <package>` で**インストール済みかを確認**する。
- 未インストールの場合は `pip install` を実行してから本来のコマンドを実行する。

---

## 4. 同じエラーでのリトライ上限

### 問題
同一のエラー（エンコーディング、パス、構文エラー等）で何度もリトライし、時間を浪費するケースがある。

### 対策ルール（ユーザー設定より）
- **同じエラーで 3回 リトライしたら打ち切り**、エラー内容をユーザーに報告して判断を仰ぐ。
- リトライ時は**必ず前回と異なるアプローチ**を試みる（例: `-c` → スクリプトファイル化、`urllib` → `requests`、直接実行 → pytest 経由）。

---

## 5. Windows 文字コード問題 (CP932 / UTF-8 衝突)

### 問題
Windows のデフォルトエンコーディング (Shift-JIS / CP932) と、Python・ツール類が期待する UTF-8 の不一致により、以下の問題が発生する。

| 症状 | 原因 |
| :--- | :--- |
| `UnicodeDecodeError` / `UnicodeEncodeError` | Python の stdout が CP932 で、日本語や特殊文字を含む出力が失敗 |
| エージェント実行の強制終了 (`Agent execution terminated`) | コマンド出力に CP932 で解釈不能なバイト列が含まれる |
| ファイル書き込み時の BOM 混入 | PowerShell の `Set-Content` / `Out-File` がデフォルトで UTF-8 with BOM を出力 |

### 対策ルール
- **Python 実行時**: 必ず以下の環境変数を先頭に付与する。
  ```powershell
  $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"; python script.py
  ```
- **PowerShell コマンド全般**: 必ず以下をコマンド先頭に付与する。
  ```powershell
  $OutputEncoding = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8;
  ```
- **ファイル書き込み**: PowerShell の `>` リダイレクトや `Set-Content` を使わない。代わりに `write_to_file` ツールまたは .NET の `UTF8Encoding($False)` を使用して **BOM なし UTF-8** で書き込む。
- **CMD (Batch)**: `chcp 65001 > nul &&` をコマンド先頭に付与する。

---

## 6. 自己更新ルール

- 本ドキュメントに記載されていない**新しい一般的な問題パターン**を発見した場合、発生状況と対策を含めて本ドキュメントに追記すること。
- 追記時は、問題の「症状」「原因」「対策ルール」の3点セットを必ず含めること。
- 更新履歴に日付と変更内容を記録すること。

---

## 更新履歴
- 2026-04-10: 初版作成（プロジェクト固有の問題を分離、5項目 + 自己更新ルール）
