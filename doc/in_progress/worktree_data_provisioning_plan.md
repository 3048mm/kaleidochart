# ワークツリーのデータ・プロビジョニング整備 計画書

- **ステータス**: 🚧 進行中（計画レビュー完了 2026-08-31）
- **実施者**: AI エージェント（Claude Opus 5）+ ユーザーレビュー
- **開始日**: 2026-08-30 / **完了日**: —
- **作業ブランチ**: `worktree-data-provisioning`（Phase 1・2 を同一ブランチで実施 — §4-3）
- **対象 issue / 関連ドキュメント**:
  - `doc/agent_execution_rules.md` §10.3（本計画で全面書き換え）
  - `.claude/skills/sandbox-workflow/SKILL.md` §1.1（同）
  - `tools/check_worktrees.ps1` / `tools/hooks/git_guard.ps1`

---

## 1. 背景と目的

ワークツリーで作業するたびに「`.db` や Parquet が無い」状態に陥る。原因は運用の抜けではなく、
**パス解決の権威が4系統に分裂していて、どれを踏むかで向き先が変わる**という設計上の問題である。

### 1.1 実測ベースライン（2026-08-30、使い捨てワークツリーで再現）

新規ワークツリーの `data/` は git 管理の TOML 3件のみ:

```
data/  ->  price_corrections.toml / scenario_batch_jobs.toml / screener_presets.toml
```

この状態で `doc/agent_execution_rules.md` §10.3 が指示するとおり `STOCKTOOL_ENV=sandbox` を
設定して `tools/db_health_check.py` を読み込ませた結果:

```
DB_PATH       : ...\worktrees\wt-data-sim\data\stocktool.db
STOCKTOOL_ENV : sandbox                        <- 設定しても効かない
connect 後    : 存在する / 0 bytes / テーブル数 0   <- 黙って空DBを作る
```

既存ワークツリー `ipo-candidates` にも同じ痕跡が残っている:

```
data/parquet_master/         空ディレクトリ
data/sandbox/stocktool.db    139,264 B  <- スキーマだけの空DB
data/stocktool.db            139,264 B  <- 同上
```

### 1.2 パス解決の4系統

| 経路 | ワークツリーでの向き先 | 症状 |
| :--- | :--- | :--- |
| `config.toml` の `db_path`（**絶対パス**） | 本番 | 動くが**ワークツリーから本番に書ける** |
| 相対デフォルト `"data/stocktool.db"` | ワークツリー内・空 | `create_all` が黙って空DBを作る |
| `STOCKTOOL_ENV=sandbox` → `"data/sandbox/stocktool.db"` | ワークツリー内・空 | 同上。**§10.3 が推奨している手順そのもの** |
| `os.path.join(_PROJECT_ROOT, "data", ...)` | ワークツリー内・空 | **環境変数を一切見ない。14ファイルに散在** |

4系統目が主犯。`__file__` 起点でリポジトリルートを求めるため、環境変数も `config.toml` も
無視してワークツリー内の空ファイルを掴む。該当ファイル:

```
tools/db_health_check.py                     DB_PATH
backend/scripts/run_production_restore.py    prod_db_path
backend/scripts/run_production_migration.py
backend/scripts/run_local_rebuild.py
backend/scripts/import_universe.py           stocktool.db / universe.db
backend/scripts/retire_stale_symbols.py
backend/scripts/deploy_after_merge.py
backend/scripts/weekly_maintenance.py        maintenance_reports/
backend/scripts/archive_parquet_master.py
backend/scripts/backfill_symbol_history.py
backend/scripts/restore_truncated_symbol_history.py
backend/scripts/rebuild_backtest_data.py
backend/api/routers.py                       screener_presets.toml
backend/api/screener_router.py               screener_presets.toml
```

### 1.3 完了条件

1. ワークツリーでデータ不足が「黙って空DB」ではなく**即エラー＋復旧手順の提示**になる
2. 読み取り用途（バックテスト等）が**ワークツリーから本番 Parquet を参照して即座に動く**
3. 書き込み用途が**ワークツリー内の sandbox に完全隔離**され、本番に一切届かない
4. 本番 `data/` の構造・容量が変わらない（実測で担保）

---

## 2. スコープと設計判断

### 2.1 変更すること

- パス解決を `backend/paths.py` に一元化する
- 存在しない DB を黙って作らない（fail-fast）
- ワークツリーの data を明示的にプロビジョニングするスクリプトを用意する
- Parquet sandbox は**ハードリンク**で構築する（本番の最新世代を共有）
- ドキュメント（§10.3 / sandbox-workflow SKILL）を実態に合わせて書き換える

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| `data/` 全体をジャンクションする | **却下** | ① §1.2 の4系統目14ファイルが全部「本番を書き込みモードで」掴む（`run_production_restore.py` / `import_universe.py` 等）② `STOCKTOOL_ENV` による安全機構がこの系統には効かないため無効化される ③ 下記の削除事故 |
| `data/prod_ro/` だけジャンクションする | **却下** | `git worktree remove` がジャンクションを辿ってリンク先を全削除することを実測（§7-1）。本番 `data/` 全損の経路になる。得られる利便は「相対パスで `ls data/prod_ro/` できる」程度で釣り合わない |
| シンボリックリンクを使う | **却下** | 管理者権限または開発者モードが必須（実測: `WinError 1314`）。プロビジョニングが環境設定に依存するのは脆い |
| ワークツリー内 sandbox を `sandbox_xxxx` と個別命名する | **不要** | ジャンクションを使わないため sandbox は物理的にワークツリー内に閉じる。名前を変える必要がない（`.gitignore` の `data/sandbox/` もそのまま使える） |
| `git worktree remove` をフックでブロックする | **不要** | ジャンクションを置かない方針なので事故経路が消える。ハードリンクの削除は安全（実測済み） |
| `parquet_master` を実コピーする | **却下** | 3.8GB（過去には 9.6GB）。ハードリンクなら実測 4,096 bytes |
| 本番 `data/` の構造を変える | **しない** | 追加・移動なし。ハードリンクは本番側のリンクカウントが増えるだけでディレクトリエントリは増えない（実測済み） |
| `config.toml` の絶対パス記述を消す | **しない（本体では維持）** | 本体チェックアウトの現行動作を壊さない。ワークツリーでは `config.toml` を**無視**する（§3.1） |

---

## 3. 変更内容

### 3.1 `backend/paths.py` の新設（パス解決の唯一の権威）

**何を**: data ルートと各 DB / Parquet のパスを解決する単一モジュール。

**公開 API**:

```python
get_repo_root() -> str                  # __file__ 起点
is_worktree() -> bool                   # .git がファイルなら worktree（実測で確認済み）
get_data_root() -> str                  # 書き込み先
get_prod_data_root() -> str | None      # 本番 data（読み取り専用参照）
get_db_path(name: str) -> str           # stocktool / user_data / universe / optimization_trials
get_parquet_master_dir() -> str
require_existing(path: str) -> str      # 無ければ DataNotProvisionedError
is_production() -> bool
```

**`get_data_root()` の解決順**:

1. 環境変数 `STOCKTOOL_DATA_ROOT`（絶対パス）
2. `STOCKTOOL_ENV=sandbox` → `<repo_root>/data/sandbox` / `=test` → `<repo_root>/data/test`
3. `config.local.toml` の `[data] root`（プロビジョニングが書く。git 管理外）
4. **本体チェックアウトのみ**: `config.toml` の `[system] db_path` の親ディレクトリ（後方互換）
5. **本体チェックアウトのみ**: `<repo_root>/data`
6. **ワークツリーでここまで来たら `DataNotProvisionedError`**

> ワークツリーでは 4・5 を意図的に塞ぐ。`config.toml` は本番の絶対パスを持っており、
> これを尊重するとワークツリーから本番を書ける状態が残るため。ワークツリーは
> **常に明示的なプロビジョニングを要求する**。

**なぜ環境変数ではなく `config.local.toml` を主にするか**: 環境変数はシェルセッションごとに
設定が必要で、バックグラウンドジョブ・並列ワーカー・別セッションで漏れる。すでに
`config.local.toml`（git 管理外・SEC 連絡先を格納）という仕組みがあり、ワークツリーには
git 経由で来ない（実測確認済み）ため、プロビジョニングの成果物置き場としてちょうどよい。

**影響範囲**: 新規ファイルのため既存への影響なし。移行は §3.5 で段階的に行う。

### 3.2 fail-fast（空DBを黙って作らない）

**何を**: 存在しない DB ファイルに対する暗黙の新規作成を止める。

- `db/database.py` / `database_user.py` / `database_universe.py` の `init_db(db_path)` に
  `allow_create: bool = False` を追加。ファイルが存在せず `allow_create` が False かつ
  環境変数 `STOCKTOOL_ALLOW_DB_CREATE` が未設定なら `DataNotProvisionedError` を送出する。
- 例外メッセージに**復旧コマンドを埋め込む**:

  ```
  DataNotProvisionedError: data/sandbox/stocktool.db が存在しません。
    ワークツリーでは先にプロビジョニングが必要です:
      python tools/provision_worktree_data.py . --mode write
    新規に DB を作るタスクの場合は STOCKTOOL_ALLOW_DB_CREATE=1 を設定してください。
  ```

- pytest は `backend/conftest.py` で `STOCKTOOL_ALLOW_DB_CREATE=1` を設定する（テストは空DBを作る）。

**なぜ**: `doc/issue_list.md` に並ぶ事故はすべて「サイレント成功 → 間違ったデータで結論」という
同型（`is_trend_template` の素通し、流動性床の未適用、yfinance が 404 と 429 を畳む）。
即死させるのが最も費用対効果が高い。

### 3.3 本番データへの書き込みガード

ファイルシステムでは読み取り専用にできない（reparse point も hardlink も自前の ACL を
持たない）ため、コード側で担保する。

- `init_db()` が `get_prod_data_root()` 配下のパスで **write_engine を作ろうとしたら例外**
  （ワークツリーからの実行時のみ。本体では従来どおり）
- `rotate_and_archive_to_parquet()` の書き込み先が `get_prod_data_root()` 配下なら例外（同上）

### 3.4 `tools/provision_worktree_data.py`（プロビジョニング）

**Python で書く**（PowerShell の `Set-Content -Encoding utf8` は BOM を付けるため。§7-2）。
薄いラッパー `tools/provision_worktree_data.ps1` を併置する。

```
python tools/provision_worktree_data.py <worktree_path> --mode read|write [--light] [--with-optuna]
```

**共通処理（両モード）**: ワークツリーに `config.local.toml` を生成する。

```toml
[sec]
contact = "<本体からコピー>"

[data]
# 書き込み先（read モードでは sandbox を作らないので data/ 直下のまま）
root = "D:/.../worktrees/<name>/data"
# 本番 data（読み取り専用参照。バックテスト等が使う）
prod_root = "D:/My Documents/Programing/stocktool/data"
```

**`--mode read`（既定）**: 上記のみ。本番 Parquet は `get_prod_data_root()` 経由で
読み取り専用参照する。書き込みは §3.3 のガードで止まる。
→ 対象: バックテスト、スクリーナー式の変更、API 読み取り、フロントエンド（変更種別 A）

**`--mode write`**: 加えて `data/sandbox/` を構築し、`[data] root` を `data/sandbox` にする。
→ 対象: スキーマ変更・indicator 追加・パイプライン変更（変更種別 B / C）

| 対象 | 方式 | 実測コスト |
| :--- | :--- | ---: |
| `parquet_master`（最新世代7ファイル） | **ハードリンク** + ポインタ書き換え | 4,096 bytes |
| `stocktool.db` 1.7GB | 実コピー（`--light` で `create_sandbox.py` の軽量抽出） | 数十秒 |
| `user_data.db` 92KB / `universe.db` 1.4MB | **実コピー**（ユーザー資産。本番を指させない） | 一瞬 |
| `optimization_trials.db` 40MB | `--with-optuna` 指定時のみ実コピー | 数秒 |

**ハードリンク時の必須処理**:

- `latest_master.json` は**絶対パスを持つ**ので、単純コピーでは sandbox のポインタが
  本番ファイルを指し続ける。**sandbox 側のパスに書き換える**（§7-3）
- 書き出しは **BOM なし UTF-8 / LF**（§7-2）
- ハードリンクが張れない場合（別ボリューム等）は**フォールバックせずエラーで止める**
  （黙って実コピーして数GB消費する事故を防ぐ）

**冪等**: 再実行可能にする（既存リンク・既存コピーはスキップ）。

### 3.5 既存コードの移行（14ファイル）

§1.2 の4系統目を `paths.py` 経由に寄せる。段階的に行い、1ファイルずつテストで担保する。

### 3.6 フック・棚卸し

- **`SessionStart` フック（新規）**: cwd が `.claude/worktrees/` 配下で
  `config.local.toml` に `[data]` が無ければ**警告を出す**（自動実行はしない。
  read / write のモード判断はタスク内容に依存するため人間/オーケストレーターが決める）。
- **`git_guard.ps1`**: 変更なし（ジャンクションを使わないため `git worktree remove` の
  ガードは不要）。
- **`check_worktrees.ps1`**: 各ワークツリーのプロビジョニング状態（read / write / 未実施）を
  表示する。加えて「本番に既に存在しない Parquet 世代をハードリンクで掴んでいる」
  ワークツリーを検出する（下記 phantom disk 対策）。

> **phantom disk**: 古いワークツリーが、本番で prune 済みの世代をハードリンクで
> 掴んだままだと、その分のディスクが解放されない。本番の構造は無傷だが容量が戻らない。

### 3.7 ドキュメント更新

- `doc/agent_execution_rules.md` §10.3 を全面書き換え（現行の「`STOCKTOOL_ENV=sandbox` を
  設定して起動」という記述が空DBの直接の原因になっている）
- `.claude/skills/sandbox-workflow/SKILL.md` §1.1 を同様に書き換え
- `doc/project_knowhow.md` に §7 の罠を**エラー原文つき**で追記（次セッションの検索キー）

---

## 4. ユーザー確認事項

すべて 2026-08-31 にユーザー判断済み。

| # | 確認事項 | 判断結果 |
| :-- | :--- | :--- |
| 1 | `--mode write` の `stocktool.db` の既定 | ✅ **提案どおり**。フルコピー（1.7GB / 数十秒）を既定、`--light` で `create_sandbox.py` の軽量抽出。軽量版は主要銘柄+テーマ・直近180日しか入らず、指標検証で母集団が足りない事故が起きうるため既定にしない |
| 2 | 既存15ワークツリーの扱い | ✅ **既存は無視する**。一括での掃除は行わない。次に触るときにプロビジョニングし直す |
| 3 | Phase 分割の粒度 | ✅ **Phase 1 と Phase 2 を同一ブランチで実施してよい** |
| 4 | `config.local.toml` への相乗り | ✅ **課題が無いことを確認したうえで採用**（下記） |

### 4.1 `config.local.toml` 相乗りの調査結果（2026-08-31）

読み手は2箇所のみで、どちらも `tomli.load()` 後に自分のセクションだけを引く。
`[data]` セクションの追加は**完全に無害**:

| 参照元 | 参照セクション |
| :--- | :--- |
| `backend/data_collection/sec_client.py:62` `resolve_contact()` | `[sec] contact` |
| `backend/data_collection/tls_trust.py` | `[tls] ca_bundle` |

**副次的な効果**: 現状ワークツリーには `config.local.toml` が存在しない（git 管理外のため
チェックアウトされない）ので、`resolve_contact()` が `ValueError` を送出し、
**SEC を参照するスクリプトはワークツリーで軒並み動作しない**。
プロビジョニングが本体の `config.local.toml` をコピーする設計により、これも同時に解消する。

→ プロビジョニングは**本体の `config.local.toml` を丸ごとコピーしたうえで `[data]` を
付加/更新する**（`[sec]` `[tls]` の内容を取りこぼさないため）。

---

## 5. 実装順序と進捗チェックリスト

### Phase 1 — 実害を止める

- [ ] `backend/tests/test_paths.py` を先に書く（TDD red）: 解決順・worktree 判定・fail-fast
- [ ] `backend/paths.py` を実装する
- [ ] `db/database.py` / `database_user.py` / `database_universe.py` に `allow_create` と fail-fast を追加
- [ ] `backend/conftest.py` に `STOCKTOOL_ALLOW_DB_CREATE=1` を設定
- [ ] 本番データへの書き込みガード（§3.3）を実装
- [ ] `tools/provision_worktree_data.py` を実装（`tmp/provision_worktree_data.py` が試作）
- [ ] `.gitignore` を確認（`data/sandbox/` は既存、`config.local.toml` も既存。追加は不要の見込み）
- [ ] 使い捨てワークツリーで read / write 両モードの通しリハーサル

### Phase 2 — 既存コードの移行

- [ ] `tools/db_health_check.py` を `paths.py` 経由に
- [ ] `backend/scripts/` の11ファイルを順次移行
- [ ] `backend/api/routers.py` / `screener_router.py` の presets パスを移行
- [ ] `backend/backtest/` の相対デフォルト（`backtest_runner.py` / `etf_single_runner.py` / `scenario_*.py`）を移行

### Phase 3 — 運用の整備

- [ ] `SessionStart` フックを追加（未プロビジョニング警告）
- [ ] `check_worktrees.ps1` にプロビジョニング状態表示と phantom disk 検出を追加
- [ ] `doc/agent_execution_rules.md` §10.3 を書き換え
- [ ] `.claude/skills/sandbox-workflow/SKILL.md` §1.1 を書き換え
- [ ] `doc/project_knowhow.md` に §7 の罠を追記
- [ ] `tmp/provision_worktree_data.py`（試作）を削除

### 作業中メモ

未着手。計画レビュー中。

---

## 6. 検証プラン / 結果

### 6.1 自動テスト

```powershell
$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v
```

新規テスト:

- `test_paths.py`: 解決順 / worktree 判定 / ワークツリーで `config.toml` を無視すること
- `test_database.py` 追加: 存在しない DB で `DataNotProvisionedError`、`allow_create=True` で作成成功
- Parquet の**既存ファイル名への in-place 上書きが存在しないこと**を保証する回帰テスト
  （ハードリンク運用の前提。§7-4）

### 6.2 手動リハーサル（使い捨てワークツリー）

```powershell
git worktree add .claude/worktrees/wt-verify -b worktree-wt-verify
.\venv\Scripts\python.exe tools\provision_worktree_data.py .claude\worktrees\wt-verify --mode write
```

確認項目（すべて 2026-08-30 のリハーサルで測定方法を確立済み）:

| 項目 | 期待値 |
| :--- | :--- |
| 本番 `parquet_master` のファイル数 / バイト数 | 変化なし（10 / 3,823,433,169） |
| ハードリンクのディスク実消費 | 数KB（見かけ 3.56GB） |
| sandbox からの読み取り | `prices` 6,076,934 行 / 2010-04-01〜 |
| sandbox への新世代書き込み | 本番ディレクトリ・本番ポインタとも不変 |
| `git status` | クリーン（0.1秒未満） |
| プロビジョニング前の DB アクセス | `DataNotProvisionedError` で即死 |
| 撤去後の本番 | ファイル数・バイト数・リンクカウントが原状復帰 |

### 6.3 結果

（実装後に記入）

---

## 7. 途中発生した課題

計画段階（2026-08-30）の実測で判明した事項。すべて設計に反映済み。

### 7-1. `git worktree remove` はジャンクションを辿ってリンク先を削除する 🔴

デコイディレクトリを指すジャンクションをワークツリー内に置き、ジャンクションを外さずに
`git worktree remove --force` を実行した結果、**リンク先の中身が全削除された**:

```
error: failed to delete 'D:/.../.claude/worktrees/wt-data-sim': Permission denied
```

```
$ ls -la /d/tmp_decoy/
total 20
drwxr-xr-x  .
drwxr-xr-x  ..          <- keepme.txt / parquet_master/ が消滅
```

しかも途中で `Permission denied` で停止するため、ワークツリー側は残骸として残る。

**コマンドによって挙動が異なる**（すべて同一条件で実測）:

| 操作 | ジャンクションを辿るか | リンク先 |
| :--- | :--- | :--- |
| `Remove-Item -Recurse -Force` | 辿らない | 無傷 |
| Git Bash `rm -rf` | 辿らない | 無傷 |
| `git clean -xdf` | 辿らない | 無傷 |
| **`git worktree remove --force`** | **辿る** | **全削除** |

→ **これが `data/prod_ro/` ジャンクション案を却下した決定的理由**（§2.2）。
本番を指したまま実行していれば `parquet_master` 3.8GB・`stocktool.db` 1.7GB・
`universe.db`・`user_data.db`・全バックアップが消えていた。

### 7-2. PowerShell 5.1 の `Set-Content -Encoding utf8` は BOM を付ける → サイレント失敗

`latest_master.json` を PowerShell で書き換えたところ先頭に `ef bb bf` が付き、
`get_latest_master_files()` が**例外を投げずに `None` を返した**。無関係な場所で落ちる:

```
json.decoder.JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
```

```
TypeError: 'NoneType' object is not subscriptable
```

→ プロビジョニングは Python で書く（§3.4）。
→ **別課題**: `get_latest_master_files()` が失敗理由を握り潰す点は §8 に切り出す。

### 7-3. `latest_master.json` は絶対パスを持つ

```json
"prices": "D:\\...\\data\\parquet_master\\prices_20260829_144251.parquet"
```

単純コピーすると sandbox のポインタが本番ファイルを指し続ける。ハードリンクを張っても
意味がなく、**読めてしまうので気づけない**。→ プロビジョニングでパス書き換えが必須（§3.4）。

### 7-4. ハードリンクの書き込み挙動（実測）

| 書き方 | 本体への影響 |
| :--- | :--- |
| 既存ファイル名に in-place で truncate 書き込み | **本体に書き抜ける**（`ORIGINAL` → `OVERWRITTEN`） |
| `os.replace` / `Move-Item` で差し替え | 本体は無傷（リンクが切れる） |

現行コードは Parquet が新規タイムスタンプ名（`to_parquet`）、ポインタが `os.replace`
（`update_pointer_with_retry`）なので**全経路が安全側**。将来の退行を防ぐ回帰テストを §6.1 に追加。

### 7-5. Windows のリンク種別ごとの特権要件（実測・非管理者／開発者モード off）

| 種類 | 作成可否 | 備考 |
| :--- | :--- | :--- |
| ハードリンク | **可** | 同一ボリューム必須（D:→C: は `Access is denied`）。ディレクトリ不可 |
| ジャンクション | 可 | ディレクトリ専用 |
| シンボリックリンク | **不可** | `Administrator privilege required` / `WinError 1314` |
| Git Bash `ln -s` | **exit 0 だが実体はコピー** | MSYS のフォールバック。サイレント失敗 |

```
ln: failed to create symbolic link 'x': Operation not permitted
[WinError 1314] クライアントは要求された特権を保有していません
```

### 7-6. `.gitignore` に `data/prod_ro/` が必要（→ 本方針では不要になった）

ジャンクション案の検証中、`data/prod_ro/` が `?? data/prod_ro/` として未追跡表示され、
全ワークツリーが常時 dirty になることを確認した。`check_worktrees.ps1` の
「未取り込み作業あり」警告が意味を失う。**方針 B ではジャンクションを置かないため
この対応は不要**だが、将来ジャンクションを再検討する場合の記録として残す。

---

## 8. スコープ外・残作業

- **`get_latest_master_files()` のサイレント失敗**（§7-2）: 失敗理由を握り潰して `None` を
  返す。本計画では触らず、`doc/issue_list.md` に起票して別途対応する。
- **既存15ワークツリーのゴミ掃除**: 未取り込み作業の棚卸しが先。§4-2 のとおり
  「次に触るときにプロビジョニングし直す」運用とし、一括処理はしない。
- **`config.toml` の絶対パス記述**: 本体では現行動作を維持する（§2.2）。ワークツリーでは
  無視するため実害はない。将来 `paths.py` へ完全移行する際に再検討。
- **昇格フロー（`deploy_after_merge.ps1`）との相互作用**: 本計画では変更しない。
  昇格は本体で本番データから再生成する設計のため、ワークツリー sandbox とは独立している。
- **phantom disk の自動回収**: 検出のみ実装し（§3.6）、自動削除はしない。
