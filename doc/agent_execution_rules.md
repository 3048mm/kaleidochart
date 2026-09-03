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


### 1.1 Bash のヒアドキュメントは、クォートしないとバッククォートが実行される

Bash ツールで `python - <<EOF` の形を使うとき、**終端子をクォートするかどうかで
中身の扱いが変わる**。日本語ドキュメントを生成するコードは Markdown のインラインコードを
大量に含むため、ここを間違えると**エラーを出さずに本文が消える**。

| 書き方 | シェルの展開 | 使いどころ |
| :--- | :--- | :--- |
| `<<'EOF'`（クォートあり） | **一切しない**（リテラル） | **既定。ほぼ常にこちら** |
| `<<EOF`（クォートなし） | 変数・バッククォート・バックスラッシュを展開 | シェル変数を埋めたいときだけ |

クォートしないと、本文中のインラインコードが**コマンド置換として実行され、
その出力（＝通常は空）に置き換わる**。実際に出た症状:

```
/usr/bin/bash: line 31: [strategy.optimization]: command not found
doc/agent_execution_rules.md: line 13: syntax error near unexpected token
```

Python 側は「成功」を報告するため（`print("ok")` が出る）、**書き込んだファイルを
読み返すまで壊れたことに気付けない**。実例では Markdown の箇条書きから
インラインコード3箇所が消え、文が意味を成さなくなっていた。

#### 対策ルール

- **ヒアドキュメントの終端子は常にクォートする**（`<<'PYEOF'`）。
- シェル変数を渡したいときも、クォートしたまま**環境変数か `sys.argv` で渡す**。
- ファイルを書いたら**必ず読み返して確認する**（`tail` / `grep`）。
  これはエンコーディング問題（§5）と同じ「サイレント破壊」の型。

### 1.2 作業コピーの改行は CRLF、リポジトリは LF

`core.autocrlf=true` かつ `.gitattributes` が `* text=auto` のため、
**作業コピーの `.md` / `.toml` / `.py` は CRLF、index は LF** になっている
（`git ls-files --eol <file>` で `i/lf w/crlf` と確認できる）。

Python で `newline=""` を指定して読むと CR がそのまま入るため、
改行を含むアンカー文字列が**例外なしで一致しなくなる**（`AssertionError` だけが出る）。

- **読み込みは universal newlines**（`open(p, encoding="utf-8")`、`newline` 指定なし）。
- **書き込みは `newline="
"`**（CLAUDE.md の LF 規約どおり。index は LF なので差分は増えない）。

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

### 4.1 ノウハウ照会・追記フロー（3回打ち切り時に必ず実行）

打ち切りは「諦める」ことではない。**次のセッションが同じ穴に落ちないようにする**手続きとセットで行う。
新規セッションが自動で読むのは `CLAUDE.md` だけであり、本書と `project_knowhow.md` は「検索して初めて見つかる」。
したがって **検索でヒットする形で書き残すこと** が本フローの肝である。

#### 手順

1. **照会** — エラーメッセージの特徴的な語（例外名・識別子・原文の一部）で既存ノウハウを検索する。
   ```powershell
   Select-String -Path doc/agent_execution_rules.md, doc/project_knowhow.md -Pattern "database is locked"
   ```
   Bash なら `grep -n "database is locked" doc/agent_execution_rules.md doc/project_knowhow.md`。
   ドメインが明らかな場合（sandbox / SQLite / Parquet / パイプライン等）は対応する `.claude/skills/<name>/SKILL.md` も併せて検索する。
2. **適用** — ヒットしたら、その「対策ルール」を自己流の4回目のリトライより優先して試す。
   - 記載どおりに試して**なお失敗した**場合は、その条件（環境・前提の違い）を該当項目に追記して情報を更新する。古くなったノウハウを黙って放置しない。
3. **追記** — ヒットせず、その後（自力・ユーザーの助言・別アプローチのいずれかで）解決できたら、**解決した時点で**該当ドキュメントに追記してから本来の作業に戻る。「後でまとめて」は忘れるので禁止。
4. **未解決のまま終える場合は追記しない** — 未検証の推測をノウハウとして書くと、次のセッションが誤った対策を信じて時間を失う。代わりに `doc/issue_list.md` に1行残し、ユーザーへの報告に「エラー原文」と「試した3アプローチ」を明記する。

#### 追記先の振り分け

| 判定基準 | 追記先 |
| :--- | :--- |
| このリポジトリの知識がなくても起きる（OS・シェル・venv・パス・文字コード・Git・エージェント運用） | **本書** — 新しい `## <番号>.` を採番して追加 |
| stocktool 固有（DB スキーマ・パイプライン・指標・API・フロントエンド・データ構造） | **`doc/project_knowhow.md`** — A〜E の該当カテゴリ配下 |
| 既に体系化されたドメイン手順の中の抜け（sandbox / SQLite WAL / Parquet / パイプライン修復 等） | 対応する **`.claude/skills/<name>/SKILL.md`** |

迷ったら「**このリポジトリ以外でも同じ失敗が起きるか？**」で判定する。起きるなら本書、起きないなら `project_knowhow.md`。

#### 追記フォーマット（§8 の3点セット + 検索キー）

- 見出しは**症状が想像できる名前**にする（原因名ではなく、次に困る人が探す言葉で）
- **エラーメッセージ原文をコードブロックで最低1行**入れる ★省略禁止
  - これが手順1をヒットさせる唯一の検索キーである。要約した日本語だけでは次のセッションは絶対に引けない
  - 可変部（パス・ティッカー・行番号）は `<...>` に置換し、固定部分は原文どおり残す
- **症状 / 原因 / 対策ルール** の3点を書く（対策は「実際に成功したコマンド・手順」を具体的に）
- 末尾の更新履歴に日付と1行サマリを追加する

````markdown
### <症状が想像できる見出し>

#### 症状
```
sqlite3.OperationalError: database is locked
```
<いつ・どの操作で出るか>

#### 原因
<なぜ起きるか>

#### 対策ルール
- <実際に成功した手順・コマンド>
````

#### サブエージェント（implementer / test-writer）の場合

ワーカーは手順1（照会）と2（適用）までを行い、**ドキュメントへの追記は行わない**。作業コピーの衝突と、検証されていない知見の混入を避けるためである。
解決可否にかかわらず完了報告に「エラー原文・試した3アプローチ・判明した回避手段」を含め、追記するか否かはオーケストレーターが判断する。

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

### 5.1 BOM は「例外」ではなく「サイレント失敗」として現れることがある

`Set-Content -Encoding utf8`（Windows PowerShell 5.1）は **BOM 付き**で書き出す。
JSON を書いた場合、読み手が `json.load()` すると次で落ちる:

```
json.decoder.JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
```

**厄介なのは、呼び出し側がこの例外を握り潰していると無関係な場所で落ちる点**。
実例（2026-08-30、`latest_master.json` を PowerShell で書き換えた）:

```
TypeError: 'NoneType' object is not subscriptable
```

`get_latest_master_files()` が例外を捨てて `None` を返すため、
**BOM が原因だと気づけない**。設定ファイル・JSON を生成するスクリプトは
**PowerShell ではなく Python で書き、`encoding="utf-8", newline="\n"` を明示する**こと。
先頭バイトの確認: `head -c 3 <file> | od -An -tx1` → `ef bb bf` なら BOM 付き。

### 5.2 読み込み側も同じ。`Get-Content` は `-Encoding UTF8` を明示する

PowerShell 5.1 の `Get-Content` は **BOM なし UTF-8 を CP932 として読む**。
日本語コメントを含む TOML / 設定ファイルでは、誤デコードが**改行を飲み込み**、
次の行が前の行に連結される。2026-09-01 に実際に踏んだ例（`config.local.toml`）:

```
# 誤: Get-Content $conf -Raw
...蜆ｪ蜈医＆繧後ｋ縲・[sec]      <- 改行が消えて [sec] が同じ行になる
...邱ｨ髮・＠縺ｪ縺・％縺ｨ縲・[data]  <- 同上

# 正: Get-Content $conf -Raw -Encoding UTF8
[sec]
[data]
```

このため `-match '(?m)^\s*\[data\]'` のような**行頭アンカーの判定が黙って失敗する**
（例外は出ず、単に「セクションが無い」と判定される）。
§6.1 の「`.bat` の日本語コメントが次の行を壊す」と同じ現象が、読み込み側でも起きる。
**PowerShell から設定ファイルを読むときは常に `-Encoding UTF8` を付けること。**

---

## 6. `.bat` にマルチバイト文字を書かない / `schtasks` は成功を報告しても信用しない

### 6.1 `.bat` の日本語コメントは「次の行」を壊す

cmd はバッチファイルをバイトオフセットで追いながら1行ずつ実行する。UTF-8 の
マルチバイト行があるとこの位置管理がずれ、**次の行が飛ばされる／行の途中から
コマンドとして解釈される**。`chcp 65001` があると起きやすいが、無くても起きる。

2026-08-24 に実測で再現した。`REM` の日本語コメント1行を置いただけで:

```
'前回の確認は同一行で' is not recognized as an internal or external command,
operable program or batch file.
```

過去には日次パイプラインがこれで取得ゼロのまま完走している（2026-08-07）。
**コメントが無視されるだけ**なら実害は無いが、飛ばされるのは**次の実行行**なので危険。

**ルール**: `.bat` は ASCII のみで書く。日本語の説明が要るなら `REM` に英語で書くか、
`.md` 側に書く。既存 `.bat` を編集したら以下で検査する。

```bash
grep -nP '[^\x00-\x7F]' run/*.bat        # 何も出なければ OK
```

### 6.2 `schtasks /create` は空のコマンドでも SUCCESS を返す

`/tr` に渡す変数が空だと、**実行コマンドが空文字列のタスクが黙って作られる**。

```
SUCCESS: The scheduled task "..." has successfully been created.
```

しかし登録内容は `<Command>""</Command>` で、実行時にこうなる:

```
Last Result: 2147942487   (0x80070057 = ERROR_INVALID_PARAMETER)
```

`schtasks /query` は同じ値を符号付きで出すので、検索キーとしては両方を覚えておく。

```
Last Result:                          -2147024809
```

タスクは「起動して即死」を毎回繰り返すだけなので、**ログにも何も残らない**。
2026-08-01 に週次メンテナンスがこの状態で登録され、**3週間（08-09 / 08-16 / 08-23）
誰にも気付かれずに実行されなかった**。

**ルール**: タスクを登録したら**必ず読み戻して検証する**。`schtasks /create` の
終了コードは根拠にならない。

```bat
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"target_script.bat" >nul
if errorlevel 1 goto :err_verify
```

`/fo TABLE` はパスを途中で切るので **`/fo LIST` を使う**。判定はローカライズされる
ラベル（`Task To Run` / `実行するタスク`）ではなく**スクリプト名**に当てる。

登録側の `%SCRIPT_PATH%` も、使う前に `if not exist` で存在確認する。
実装例: `run/register_weekly_maintenance.bat`

> [!CAUTION]
> `%~dp0` と `%SCRIPT_PATH%` は **cmd の中でしか展開されない**。`.bat` の中身を
> PowerShell や Git Bash に貼って実行すると、空文字列や未展開の文字列がそのまま
> タスクに登録される。**`.bat` はファイルとして実行する**（`cmd /c "<path>"`）。

### 6.3 同一行での `%errorlevel%` 展開は常に古い値になる

cmd は行全体を実行前に一度で解析するため、`&` や `|` で繋いだ後段の `%errorlevel%`
には**前の行の値**が入る。パイプの結果を判定したつもりが常に 0 を見ることになる。

```bat
REM NG: 常に実行前の値
cmd /c 'foo | findstr bar >nul & echo %errorlevel%'

REM OK: 次の行で if errorlevel を使う
foo | findstr bar >nul
if errorlevel 1 goto :fail
```

---

## 7. Git 操作時の合意形成ルール

### 問題
エージェントが自律的に `git commit` を行うと、ユーザーが意図しない形式やタイミングで履歴が作られてしまい、管理が煩雑になる。一方で、ワークツリーで作業した成果を未コミットのまま放置すると、gitから見えない「取り残し作業」が発生する（実例: 2026-07-08 のワークツリー内計画書の本体未反映）。

### 対策ルール
- **本体チェックアウト（メイン作業コピー）でのコミットは禁止**。`git add <files>` までとし、コミットはユーザーが行う。
- **ワークツリー（`.claude/worktrees/` 配下）では、そのワークツリーのブランチへのコミットを許可する**。
  - コミットメッセージは既存規約（`[update]` / `[change]` / `[fix]` 等のプレフィックス）に従う。
  - 粒度は実装計画書のチェックリスト1項目=1コミット程度。
  - 完了報告にブランチ名・コミットSHA・差分サマリを明記し、本体への取り込みコマンド（`git diff main..<branch>` → `git merge <branch>`）を提示する。
- **場所を問わず禁止**: main への直接コミット・push・マージ、`--amend`、force-push。リモートへの push と PR 作成は都度ユーザーの明示的な指示による。
- **計画書の取り残し禁止**: ワークツリーで `doc/in_progress/` の計画書を作成・更新した場合は、その時点でコミットし、報告で本体への取り込みを促す（計画書は引き継ぎ書であり、未コミットのままワークツリーに置き残さない）。
- 取り込み後の掃除（マージ済みブランチ・ワークツリーの削除）はユーザーが行う。棚卸しは `tools/check_worktrees.ps1` で確認できる。

---

## 8. 自己更新ルール

- 追記のトリガーは2つ。①本ドキュメントに記載されていない**新しい一般的な問題パターン**を発見したとき、②**§4.1 の3回打ち切りフロー**で既存ノウハウに該当がなく、その後解決できたとき。
- 追記時は、問題の「症状」「原因」「対策ルール」の3点セットを必ず含めること。
- 症状には**エラーメッセージ原文をコードブロックで**含めること（次セッションが検索で到達するための唯一のキー。書式は §4.1「追記フォーマット」）。
- 未解決・未検証の推測は追記しないこと（`doc/issue_list.md` に回す）。
- 更新履歴に日付と変更内容を記録すること。


## 9. 新しい機能の追加実装

- 新しい機能を実装する場合は、以下の手順を行うこと
1. `doc/in_progress/_TEMPLATE.md` をコピーして `doc/in_progress/<機能名>_plan.md` を作成し、各セクションを記入する
2. 着手前に §4「ユーザー確認事項」を中心にユーザーとレビューし、内容を合意する
3. 作業中は進捗チェックリスト・作業中メモ・途中発生した課題を随時更新する（別セッションへの引き継ぎ・中断からの復元に使われる前提で書く）
4. 全てのタスク（ドキュメント更新まで含む）が完了したら、検証結果とステータスを記入して `doc/completed/` へ移動する

---

## 10. データ（DB / Parquet）の取り扱いとワークツリー運用

コードは git 経路（ワークツリー → ブランチ → merge）で本体に反映されるが、**データは git に乗らない**。データの本番反映は merge とは別の「昇格」イベントとして扱う。

### 10.1 データの3分類

| 分類 | 対象 | 本番反映の方式 |
| :--- | :--- | :--- |
| 再生成可能 | `stocktool.db`、Parquet の T3〜T5 | 本番 Parquet から新コードで再生成 → swap |
| 原本 | Parquet の T2 価格系列 | swap 可。ただし health check 合格まで旧世代を prune 禁止（MVCC 旧世代がバックアップを兼ねる） |
| ユーザー資産 | `user_data.db`、`optimization_trials.db`、`universe.db` | **swap・クリア・再構築は禁止**。バックアップ取得 → 本番ファイルへの冪等な in-place マイグレーションのみ |

> [!NOTE]
> `universe.db`（銘柄定義の編集マスター）がユーザー資産に入るのは、手動編集と
> `ticker_history` を再生成できないため。T1 同期の下流（`stocktool.db` の `symbols` /
> `theme_constituents` と Parquet）は「再生成可能」に分類される。
> 詳細: `universe_db_specification.md` / 位置づけの要約: `architecture.md` §11.1.1

> [!WARNING]
> **「再生成可能」は「いつでも作り直してよい」という意味ではない。**
> 全期間再構築は Yahoo から取り直すため、手元にしか無いもの（上流が返さなくなった銘柄の履歴・
> `fx_rates`・手作業の切り詰め）を失い、`symbols.id` も再採番される。
> 実行前に必ず `db_recovery_procedure.md` §4 と
> `.claude/skills/parquet-data-quality/SKILL.md` §9 を読むこと。

### 10.2 変更の4種別（エージェントは完了報告に必須記載）

| 種別 | 例 | merge 後に必要な処理 |
| :--- | :--- | :--- |
| A: コードのみ | API・フロントエンド・バックテストロジック | なし |
| B: データ形状に触れる | スキーマ変更・indicator 追加・パイプライン変更 | データ昇格（`tools/deploy_after_merge.ps1`） |
| C: ユーザー資産 DB に触れる | user_data.db のスキーマ変更 | バックアップ + in-place マイグレーション |
| D: 設定・ドキュメントのみ | CLAUDE.md・TOML・計画書 | なし |

### 10.3 ワークツリーでのデータアクセス

ワークツリーの `data/` は git 管理の TOML 数件しか無い。**そのまま実行してもエラーにならず、
空の DB が新規作成される**という罠があったため、2026-09-01 に
「使う前にプロビジョニングする」方式へ変更した。

#### 手順（作業前に1回だけ）

タスクの性質でモードを選ぶ。**本体チェックアウトでは不要**（`data/` が本番そのもの）。

```powershell
# 本番データを読むだけ（スクリーナー式の変更・API 読み取り・フロントエンド = 種別 A）
.\venv\Scripts\python.exe tools\provision_worktree_data.py <worktree> --mode read

# データ形状に触れる（スキーマ変更・indicator 追加・パイプライン変更 = 種別 B / C）
# **バックテスト・最適化もこちら**（下記の注意を参照）
.\venv\Scripts\python.exe tools\provision_worktree_data.py <worktree> --mode write
```

> [!IMPORTANT]
> **バックテスト／最適化は「読むだけ」だが `--mode read` では起動できない**（2026-09-03 実測）。
> `backtest_runner.preload_data()` は Parquet しか読まないが、
> `optimization_runner.main()` が冒頭で `init_db()` を呼ぶため、
> `stocktool.db` が無いと fail-fast で止まる:
>
> ```
> paths.DataNotProvisionedError: システムDB (stocktool.db)が存在しません: ...
> ```
>
> さらに **`--light` は `stocktool.db` を作らない**（「`create_sandbox.py` で別途構築してください」と
> 表示して終わる）。`create_sandbox.py` を本体で叩くと本番の `data/sandbox/` を触りにいくので、
> **`--light` を付けず実コピー（約1.6GB / 十数秒）するのが確実**。
> Parquet はハードリンクなので、実コストはこの SQLite コピーだけ。
>
> **`pytest backend/tests/` 全体を回す場合も同じ**（2026-09-04 実測）。
> `--mode read` のワークツリーでは `test_scenario_comparison.py` が落ちる:
>
> ```
> FileNotFoundError: Parquet master cache files not found at ...\data\parquet_master!
> ```
>
> `preload_data()` を通る経路はワークツリー自身の `data/parquet_master` を見にいくため、
> `--mode read`（`config.local.toml` を書くだけ）では足りない。
> **コードの不具合と紛らわしいので、テストの赤を見たらまずプロビジョニングのモードを疑う。**

`--mode write` の内訳:

| 対象 | 方式 | 実測コスト |
| :--- | :--- | ---: |
| `parquet_master` の最新世代 | **ハードリンク**（同一ボリューム・特権不要） | 4,096 バイト（見かけ 3.56GB） |
| `stocktool.db` | SQLite バックアップ API で実コピー（`--light` で省略） | 約 1.6GB |
| `user_data.db` / `universe.db` | **実コピー**（ユーザー資産。本番を指させない） | 一瞬 |
| `optimization_trials.db` | `--with-optuna` 指定時のみ | 約 40MB |

生成物はワークツリーの `config.local.toml`（`[data] root` と `[data] prod_root`）と
`data/sandbox/`。どちらも `.gitignore` 済み。本体の `[sec]` / `[tls]` も引き継ぐため、
SEC を参照するスクリプトがワークツリーで動くようになる副次効果もある。

#### パス解決は `backend/paths.py` が唯一の権威

- **ワークツリーでは `config.toml` を意図的に無視する**（本番の絶対パスを持っており、
  尊重すると「ワークツリーから本番を書ける」経路が残るため）。
- 未プロビジョニングのまま DB にアクセスすると `DataNotProvisionedError` で**即座に停止**する。
  例外メッセージに復旧コマンドが入っている。**黙って空 DB を作ることはもう無い。**
- **「存在するが中身が空」も弾く**（`require_populated()`）。pytest が残す
  139,264 バイトのスキーマだけの DB を掴んで「0 件」を正常な結果として受け取る事故を防ぐ。
- 本番配下への書き込みは `ensure_writable()` が拒否する（`ProductionWriteError`）。
  ファイルシステムでは読み取り専用にできない（reparse point も hardlink も自前の ACL を
  持たない）ため、**ガードはコード側にしか置けない**。

> [!WARNING]
> **旧手順の `STOCKTOOL_ENV=sandbox` だけを設定する方法は使わないこと。**
> ワークツリーの `data/sandbox/` は誰も作り込んでいないため、**ルールに素直に従うほど
> 空 DB を掴む**という状態だった（この記述自体が事故の直接の原因だった）。
> 現在は fail-fast で止まるので実害は無いが、正しい手順は上のプロビジョニングである。

#### そのほかの規則

- 本番データへは**読み取りのみ**（sandbox のコピー元、バックテストの入力）。
  ワークツリーからの本番書き込みは禁止。
- sandbox はワークツリー内に使い捨てで作る。**昇格の元ネタにはしない**。
  ワークツリー削除と同時に破棄する（`git worktree remove` で sandbox ごと消える。
  ハードリンクの削除は本番に影響しない）。
- `data/` に**リンクを張らない**。特にジャンクションは
  **`git worktree remove` が辿ってリンク先を全削除する**（2026-08-30 に実測。
  `rm -rf` と `git clean -xdf` は辿らないが、`git worktree remove` だけは辿る）。
  シンボリックリンクは管理者権限が要るので、そもそも作れない。
- 本番で prune 済みの世代をハードリンクで掴んだままの古いワークツリーがあると、
  **その分のディスクが解放されない**（本番の構造は無傷）。`tools/check_worktrees.ps1` が検出する。
- 軽量な SQLite が欲しい場合は `backend/scripts/create_sandbox.py`（主要銘柄＋テーマ・
  直近180日）も使えるが、**母集団が足りず指標検証で誤った結論を出しうる**ため既定にはしない。
- 設計と実測の詳細: `doc/completed/worktree_data_provisioning_plan.md`

### 10.4 バックテスト評価は merge 前に行う

- **Case 1（実行時計算の式）**: エントリー/イグジット条件・スクリーナー条件（`screener_filters.py`）・スコアリングの変更は、本番 Parquet を読み取り専用参照してワークツリーでそのまま実行できる（バックテストは Parquet/SQLite に書き込まない。結果・キャッシュはワークツリー内に閉じる）。
- **Case 2（Parquet に事前計算される式 = T3 indicator）**: sandbox に T3 再計算してから実行する。
- 順序: 評価 → 採用判断 → merge → （Case 2 なら）昇格。新旧比較は同一データ世代で行う（間に daily update を挟まない）。

### 10.5 昇格（種別 B）の手順

merge 直後に本体で `tools/deploy_after_merge.ps1` を実行する（1コマンド化。計画書: `doc/in_progress/deploy_after_merge_plan.md`）。処理内容: 前提チェック → 本番 Parquet のコピーに新コードでマイグレーション/再計算 → swap → `restore_sqlite_cache_from_parquet` → `db_health_check` → NG なら旧世代へロールバック。

- マイグレーションは**冪等（再実行可能）**に書く（昇格時に最新の本番データへ再適用されるため）。
- 昇格中は daily update と API サーバを停止する。
- **APIサーバーの再起動**: backendコードを変更した場合は、適用後に本番の API サーバープロセス（`run_server.bat`等）を再起動して反映すること。
- データに触れるブランチ（B/C）の merge は直列化する: merge → 昇格 → 次の merge（並行ワーカーの変更の「組み合わせ状態」は誰も検証していないため）。

### 10.6 git にデータを乗せない

- `git add` は**明示パスのみ**。`git add -A` / `git add .` は禁止（ワークツリー内 sandbox の Parquet を誤コミットする事故防止。`data/_bk/` への Parquet 誤コミットの前例あり）。
- `.gitignore` の除外を前提に横着しない（多層防御）。

---

## 11. Windows のリンクと `git worktree remove` の削除挙動

すべて 2026-08-30 に非管理者・開発者モード無効の環境で実測した結果。
ワークツリーへ大きなデータを持ち込む方法を検討する際の前提になる。

### 11.1 作成できるリンクとできないリンク

| 種類 | 非管理者で作成 | 備考 |
| :--- | :--- | :--- |
| **ハードリンク** | **可** | ファイル専用。**同一ボリューム必須**（別ドライブは `Access is denied`） |
| **ジャンクション** (`mklink /J`) | 可 | ディレクトリ専用 |
| **シンボリックリンク** | **不可** | 管理者権限または開発者モードが必要 |

ディレクトリにハードリンクは張れない（`New-Item -ItemType HardLink`:
`A file is required for the operation.` / `mklink /H`・`fsutil hardlink create`:
`Access is denied.`）。NTFS の制限ではなく、親子関係が循環しうるための意図的な禁止。

シンボリックリンク作成時のエラー原文:

```
New-Item : Administrator privilege required for this operation.
mklink   : You do not have sufficient privilege to perform this operation.
Python   : OSError: [WinError 1314] クライアントは要求された特権を保有していません
```

### 11.2 Git Bash の `ln -s` は成功を報告してコピーを作る

**最も危険**。exit code 0 を返すが、実体は symlink ではなく**ただのコピー**になる。

```
$ ln -s src/a.txt link.txt ; echo $?
0
$ test -L link.txt && echo symlink || echo "NOT a symlink"
NOT a symlink
```

MSYS2 の `winsymlinks` フォールバック。`MSYS=winsymlinks:nativestrict` を付けると
正直に失敗する:

```
ln: failed to create symbolic link 'x': Operation not permitted
```

**リンクを張ったつもりで数GBが実コピーされる**ので、`ln -s` の成功を信用しないこと。

### 11.3 `git worktree remove` だけがジャンクションを辿って中身を消す 🔴

同一条件で比較した結果、**コマンドによって挙動が違う**:

| 操作 | ジャンクションを辿るか | リンク先 |
| :--- | :--- | :--- |
| `Remove-Item -Recurse -Force` | 辿らない | 無傷 |
| Git Bash `rm -rf` | 辿らない | 無傷 |
| `git clean -xdf` | 辿らない | 無傷 |
| **`git worktree remove --force`** | **辿る** | **全削除** |

実測時の出力（リンク先の中身が消え、しかも途中で停止してワークツリー側は残骸になる）:

```
error: failed to delete 'D:/.../.claude/worktrees/<name>': Permission denied
```

**対策**: ワークツリーの中に、消えては困る場所を指すジャンクションを置かない。
本番 `data/` を指すジャンクションを張ったまま `git worktree remove` を打つと
**本番データが全損する**。データを共有したい場合は**ファイル単位のハードリンク**を使う
（リンクを1本消しても実体は残るため削除が安全。§10.3 参照）。

---

## 更新履歴
- 2026-04-10: 初版作成（プロジェクト固有の問題を分離、5項目 + 自己更新ルール）
- 2026-04-11: Git 操作時の合意形成ルールを追記
- 2026-07-05: §9 を実装計画書テンプレート（doc/in_progress/_TEMPLATE.md）ベースの運用に更新（完了時は削除ではなく doc/completed/ へ移動）
- 2026-07-09: §7 を改訂 — コミット禁止を本体チェックアウトのみに限定し、ワークツリー内の自ブランチへのコミットを許可（未コミット取り残しの防止）。計画書の取り残し禁止と棚卸しスクリプトを追記
- 2026-07-09: §10 を新設 — データの3分類・変更の4種別・ワークツリーでのデータアクセス・merge 前バックテスト評価・昇格手順（deploy_after_merge.ps1）・git add の明示パス限定
- 2026-07-16: §10.5 昇格項目に API サーバー再起動ルールを追記。
- 2026-08-20: §4.1「ノウハウ照会・追記フロー」を新設 — 3回打ち切り時の 照会→適用→追記 の手順、追記先の振り分け表、エラー原文（検索キー）の必須化、サブエージェントの扱い。§8 に検索キー必須とトリガー2種を反映
- 2026-09-01: §5.2 を新設 — `Get-Content` が BOM なし UTF-8 を CP932 として読み、誤デコードが改行を飲み込んで行頭アンカーの判定を黙って失敗させる（`-Encoding UTF8` 必須）
- 2026-09-01: §11 を新設 — Windows のリンク種別ごとの特権要件、Git Bash の `ln -s` が exit 0 でコピーを作る件、**`git worktree remove` だけがジャンクションを辿ってリンク先を全削除する**件（すべて実測・エラー原文つき）。§5.1 を新設 — BOM が「例外」ではなく無関係な `TypeError` として現れる経路
- 2026-09-01: §10.3 を全面改訂 — ワークツリーのデータは `tools/provision_worktree_data.py` で明示的にプロビジョニングする方式へ。旧記述（`STOCKTOOL_ENV=sandbox` を設定するだけ）は誰も `data/sandbox/` を作り込んでいないため**ルールに従うほど空DBを掴む**状態であり、事故の直接の原因だった。`backend/paths.py` による fail-fast（`DataNotProvisionedError` / `require_populated` / `ensure_writable`）と、`data/` にリンクを張らない理由（`git worktree remove` がジャンクションを辿ってリンク先を全削除する実測）を追記
- 2026-08-24: §6 を新設（欠番だった） — `.bat` にマルチバイト文字を書かない（次の行が飛ぶ）、`schtasks /create` は空コマンドでも SUCCESS を返すので読み戻して検証する、同一行での `%errorlevel%` 展開。週次メンテナンスが3週間実行されていなかった件の再発防止
- 2026-09-03: §1.1 / §1.2 を新設 — Bash のヒアドキュメントを `<<EOF` とクォート無しで書くと本文中のインラインコードがコマンド置換として実行され、**エラーなく Markdown 本文が消える**（実例つき）。作業コピーが CRLF・index が LF のため、`newline=""` で読むと改行を含むアンカーが黙って一致しなくなる件も併記
- 2026-09-03: §10.3 を修正 — バックテスト／最適化は「読むだけ」だが `optimization_runner` が冒頭で `init_db()` を呼ぶため `--mode read` では `DataNotProvisionedError` で起動できない。`--light` は `stocktool.db` を作らない点も明記
- 2026-09-04: §10.3 に追記 — `pytest backend/tests/` 全体もワークツリーでは `--mode write` が要る（`--mode read` だと `test_scenario_comparison.py` が `Parquet master cache files not found` で落ちる）
