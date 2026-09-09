# Parquet ポインタ読み込み失敗の fail-loud 化 計画書

- **ステータス**: 🚧 実装完了・取り込み待ち（2026-09-09）— コミット `bba8118`（実装＋テスト＋issue更新）/ `a019d09`（architecture.md）
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-09-09 / **完了日**: —
- **作業ブランチ**: `fix/parquet-pointer-fail-loud`（ワークツリー `.claude/worktrees/parquet-pointer-fail-loud`）
- **対象 issue / 関連ドキュメント**: `doc/issue_list.md` P1「`get_latest_master_files()` が読み込み失敗を握り潰し、Parquet の全期間履歴を捨てる経路がある（2026-09-01 発見）」／
  `doc/completed/worktree_data_provisioning_plan.md` §7-2 ／ `doc/agent_execution_rules.md` §5.1・§5.2

## 1. 背景と目的

`pipeline/parquet_cache_manager.py:53` の `get_latest_master_files()` は、ポインタ
（`latest_master.json`）が**存在しない**場合と**存在するが読めない**場合を、どちらも `None` で返す。
警告も出ない。

```python
if not os.path.exists(pointer_file):
    return None                     # 初回。正常
try:
    ...json.load(f)
except Exception:
    time.sleep(0.01)                # os.replace 中の PermissionError を吸収する正当なリトライ
    try:
        ...
    except Exception:
        return None                 # ← 恒久的な失敗（BOM・JSON破損・権限）も同じ扱い
```

`rotate_and_archive_to_parquet()` はこの戻り値でマージ元の旧 Parquet を決めるため、
`None` になると `old_paths = {}` となり、**旧世代を読まずに SQLite の内容だけで新世代を公開する**。
SQLite はホットキャッシュ（直近730日）しか持たないので、7年超の履歴が消えた世代が
無警告で公開される。

### 着手前のコード実測（2026-09-09 確認）

issue 起票時から**穴が一段狭まっている**ことを確認した。計画はこの実測に基づく。

| 経路 | 起票時 | 現在 | 判定 |
|---|---|---|---|
| 旧 Parquet の**読み込み・マージが失敗**した | 警告1行で `df_sql` を返す | `merge_timeseries_table()` が `RuntimeError` を送出（L101-127、2026-09-01 に対処済み） | ✅ 塞がれている |
| 旧 Parquet の**パスが渡ってこない**（`old_paths` が空） | 無警告で `df_sql` を返す | 同左。`old_parquet_path` が falsy だと L128-130 で「初回」と判断して即 `return df_sql` | 🔴 **開いたまま。本計画の対象** |
| 公開直前の非空チェック | `require_non_empty` は「空でない」しか見ない | 同左 | 🔴 切り詰められた世代は普通に公開される |

つまり**残る穴は「ポインタが読めない → 全テーブルが初回扱いになる」の1経路に絞れている**。

### 完了時の成功条件

1. ポインタが「読めない」ときに、無警告で処理が続行しない（例外またはエラーログが必ず出る）
2. ポインタが読めない状態で `rotate_and_archive_to_parquet()` を呼んでも、**履歴を失った世代が公開されない**
3. 1・2 が sandbox で**実際に再現**され、修正後に再現しないことを確認できている
   （issue に「実際に履歴が切り詰められるところまでは再現していない」と明記されている宿題）

## 2. スコープと設計判断

### 2.1 変更すること

- `get_latest_master_files()` に `strict` モードを追加し、「無い」と「読めない」を分離する
- `rotate_and_archive_to_parquet()` に「マージ元不明なら公開しない」ガードを追加する
- `preload_data()` の**暗黙の**自動再生成を廃止する（明示指定 `--refresh-cache` は残す）
- 上記の回帰テストを追加する（TDD: red → green）

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
|---|---|---|
| 10ms のリトライを消すか | **残す** | `os.replace` 中の `PermissionError`（Windows）を吸収する正当な設計。今回の対象は「リトライしても駄目だった後」の扱い |
| `get_latest_master_files()` を**常に**例外送出にするか | **しない。`strict=True` の呼び出し側だけ例外にする** | backend 内の呼び出しが 30箇所超あり、大半は読み取り専用（`chart_router.py`・`scan_*.py`）。`chart_router.py:45` は `if latest_files:` で SQLite にフォールバックする設計で、ここで例外を投げるとチャート表示が 500 になる。**データを壊しうる書き込み経路だけを止める**のが目的に合う。ただし非 strict でも `logger.error` は必ず出す（issue の対応案1「最低限 logger.error」を満たす） |
| `require_non_empty` を「行数が減ったら止める」に拡張するか | **本計画ではやらない** | 世代間の行数比較は `resolve_full_sync_table` / `merge_timeseries_table` 側に既にあり、そちらは旧世代のパスが分かっている前提。パスが分からない今回のケースには効かない。§2.1 のガードで根本を塞ぐほうが直接的 |
| `preload_data()` の自動再生成をやめるか | **やめる。本計画に含める**（ユーザー判断 2026-09-09） | issue の対応案3。3-A・3-B はガードだが、これは「読み取り専用のはずのバックテストが本番 Parquet を書く」という筋の悪さ自体を消す。§3-C |
| `--refresh-cache` による**明示的な**再生成も禁じるか | **禁じない** | 明示指定は「壊してよい」というユーザーの意思表示。消すのは**暗黙の**再生成（ポインタが読めないという理由だけで書き込みに入る経路）に限る |

## 3. 変更内容

### 3-A. `get_latest_master_files()` に `strict` を追加

- **対象**: `backend/pipeline/parquet_cache_manager.py:53-67`
- **何を**: シグネチャを `get_latest_master_files(pointer_file, *, strict: bool = False)` にする
  - ポインタが**存在しない**: 従来どおり `None`（初回。正常系）
  - ポインタが**存在するが読めない**:
    - `strict=False`（既定）: `logger.error` を出したうえで `None`。既存の呼び出し側の挙動は変わらない
    - `strict=True`: `ParquetPointerUnreadableError`（新設、`RuntimeError` 派生）を送出
  - リトライ（10ms 待って2回目）は維持する
- **なぜ**: 「無い」と「読めない」が呼び出し側から区別できないことが問題の根。既定を変えないことで
  読み取り専用経路への巻き添えを避ける
- **影響範囲**: 既定値のままなので既存の呼び出し 30箇所超は無変更で動く。**追加されるのはログ出力だけ**

### 3-B. `rotate_and_archive_to_parquet()` の「マージ元不明なら公開しない」ガード

- **対象**: `backend/pipeline/parquet_cache_manager.py:219`（`latest_pointers = get_latest_master_files(pointer_file)`）
- **何を**: 2段のガードを入れる
  1. `strict=True` で呼ぶ。ポインタが読めなければそこで停止する（issue 対応案2）
  2. **ポインタが「存在しない」場合も、`parquet_dir` に `prices_*.parquet` が実在するなら停止する**。
     「初回だから旧世代は無い」と「ポインタだけ消えた／壊れた」は前者だけでは区別できない。
     実ファイルの有無で照合する
- **なぜ**: 2 が本命。BOM 混入で `json.load()` が落ちるケースは 1 で止まるが、
  ポインタが**消えた**ケース（`os.replace` の二重失敗・アンチウイルスによる削除）は 1 では止まらず、
  「初回」と誤認して同じ切り詰めが起きる
- **影響範囲**: 真の初回（parquet_dir が空）は従来どおり通る。**完全再構築のフローが
  「parquet を消してから回す」手順になっていないか**を実装前に確認する（§5 の最初の項目）

### 3-C. `preload_data()` の暗黙の自動再生成を廃止（issue 対応案3 / ユーザー判断 2026-09-09）

- **対象**: `backend/backtest/backtest_runner.py:113-124`
- **現状**:

  ```python
  latest_files = get_latest_master_files(pointer_file)
  if not latest_files or refresh_cache:              # ← ポインタが読めないだけで書き込みに入る
      log("  Master Parquet cache not found or refresh requested. Generating ...")
      try:
          with database.get_db() as db:
              rotate_and_archive_to_parquet(db, db_path, logging.getLogger())
          latest_files = get_latest_master_files(pointer_file)
      except Exception as e:
          log(f"  Error: Failed to dynamically generate initial Parquet master: {e}")  # ← 握り潰す
  if not latest_files:
      raise FileNotFoundError(f"Parquet master cache files not found at {parquet_dir}! ...")
  ```

- **何を**:
  1. 再生成の条件を `if refresh_cache:` **のみ**にする。ポインタが読めない／無いだけでは書き込みに入らない
  2. その場合は `get_latest_master_files(..., strict=True)` の例外をそのまま上げるか、
     ポインタ不在なら「パイプラインを1回回すか `--refresh-cache` を付けてください」という
     **正しい案内**の `FileNotFoundError` を出す
  3. `except Exception` でログだけ出して握り潰すのをやめ、**元例外を `raise ... from e` で連鎖**させる。
     現状は再生成の失敗理由が捨てられ、後段で「ファイルが無い」という**誤誘導のメッセージ**に化ける
     （ファイルは実在するのに「無い」と言う。issue に記録された症状そのもの）
- **なぜ**: 3-A・3-B が入れば「壊れた世代の公開」は止まるが、**読み取り専用のはずの処理が
  書き込みトランザクションに入る**構造自体は残る。バックテストを1本走らせるだけで
  本番 Parquet のローテートが走る設計を消す
- **影響範囲（要確認）**: `--refresh-cache` を付けない運用で「初回にキャッシュが自動生成される」ことに
  依存している手順があると、そこが手動実行に変わる。§5 で以下を実測する:
  - `run/*.bat` / `run_optimization.bat` / `run_scenario_batch.py` が `--refresh-cache` 無しで
    Parquet 未生成状態から回されることがあるか
  - `etf_single_runner.py:68/159/206` と `scripts/run_local_rebuild.py:71` に**同型の経路**が無いか
    （あれば同じ扱いに揃える。`run_local_rebuild.py` は再構築が本務なので対象外の可能性が高い）

### 3-D. 回帰テスト

- **対象**: `backend/tests/pipeline/test_parquet_cache_manager.py`（既存ファイルに追記）
- **何を**:
  1. `get_latest_master_files()`: ポインタ不在 → `None` ／ BOM 付きファイル → `strict=False` で `None`+error ログ、`strict=True` で例外
  2. `rotate_and_archive_to_parquet()`: 旧世代 parquet を置いたうえでポインタを壊す → **例外で停止し、新世代が公開されていない**ことを確認
  3. 同上でポインタを**削除**する（3-B の 2 のガード）→ 停止することを確認
  4. 真の初回（parquet_dir が空）は従来どおり成功することを確認（退行防止）
  5. `preload_data()`（3-C）: ポインタが読めない状態で `refresh_cache=False` なら
     **`rotate_and_archive_to_parquet()` が呼ばれない**ことを確認（モックで呼び出し回数を検証）。
     `refresh_cache=True` なら従来どおり呼ばれる
- **なぜ**: 2 が「履歴が切り詰められる経路の再現」そのもの。issue の宿題を消化する。
  5 は「読み取り専用の処理が書き込みに入らない」ことの回帰固定

## 4. ユーザー確認事項

判断結果（2026-09-09 レビュー）。以降の議論はこの結論を前提にする。

| # | 確認事項 | 判断 |
|---|---|---|
| 1 | 作業場所 | **ワークツリー（`.claude/worktrees/` 配下）で作業する**。sandbox データは `tools/provision_worktree_data.py --mode write` で用意。本体 `main` の未コミット変更と干渉させない |
| 2 | issue 対応案3（`preload_data()` の自動再生成の廃止）を含めるか | **含める**。§3-C として実施（当初のエージェント推奨は「別途判断」だったが、ユーザー判断で本計画に取り込んだ） |
| 3 | 3-B の 2 のガードで完全再構築が止まらないか | **未確定 — §5 の1項目目で実測する**。「Parquet を全消しして作り直す」運用が `parquet_dir` にファイルを残す形なら、3-B の 2 は設計変更が要る |

**2 を含めたことによる計画への影響**: §2.2 に「`--refresh-cache` による明示的な再生成は禁じない」を追加した。
消すのは**暗黙の**再生成のみ。`--refresh-cache` はユーザーの明示的な意思表示なので残す。

## 5. 実装順序と進捗チェックリスト

- [x] 完全再構築フロー（`run_local_rebuild.py` / `deploy_after_merge.py` / `--rebuild-from`）が
      `parquet_dir` に旧ファイルを残したまま `rotate_and_archive_to_parquet()` を呼ぶかを実測し、
      3-B の 2 のガードが誤爆しないことを確認する（**誤爆するなら 3-B の 2 は設計変更**）
      → **誤爆しない。§7-1 に根拠**
- [x] `--refresh-cache` 無しで Parquet 未生成状態から回される運用が無いかを実測する
      （`run/*.bat` / `run_optimization.bat` / `run_scenario_batch.py`）。
      あれば 3-C は手順変更を伴う（§3-C の影響範囲）
      → **依存している運用は無い。§7-2 に根拠**
- [x] sandbox 環境を用意し、**修正前に切り詰めを再現**する（成功条件3の前半）
      → ワークツリーへ `--mode write` でプロビジョニング済み。再現はユニットテスト
      `test_rotate_aborts_when_pointer_unreadable_instead_of_truncating` が実関数で行う（§7-6）
- [x] テスト作成（3-D の 1〜5）— red を確認（3件が想定どおり失敗）
- [x] 3-A 実装 — `strict` と `ParquetPointerUnreadableError` の追加
- [x] 3-B 実装 — 2段ガード
- [x] 3-C 実装 — `preload_data()` の暗黙の再生成廃止＋例外連鎖。
      `etf_single_runner.py` は**同型経路なし**（rotate を呼ばず raise / スキップするだけ）で対象外と確定。
      併せて `run_local_rebuild.py` の案内メッセージを修正（§7-5）
- [x] テスト green + pytest 全件実行 → **1534 passed**（2026-09-09）
- [x] sandbox で再現手順を再実行し、**切り詰めが起きない**ことを確認（成功条件3の後半）
      → `tmp/verify_pointer_guard_sandbox.py` で **6件すべて PASS**（§6 の結果表）
- [x] `doc/issue_list.md` の該当項目を完了に更新（対応案1・2・3 をすべて実施）。
      派生課題2件（§7-3 / §7-4）を P2 に起票
- [x] `doc/architecture.md` §11.2 に「マージ元不明なら公開しない」を項目5として追記。
      `agent_execution_rules.md` は §5.1（BOM）で既にカバー済みのため追記不要と判断
- [ ] 本計画書を `doc/completed/` へ移動（**main への取り込み後**）

### 作業中メモ

着手前。§5 の1項目目（完全再構築フローの実測）が未了で、3-B の 2 の設計はそこで確定する。

## 6. 検証プラン / 結果

| 検証 | 手順 | 期待 |
|---|---|---|
| ユニット | `.\venv\Scripts\python.exe -m pytest backend/tests/pipeline/test_parquet_cache_manager.py -v` | 追加4ケースが green |
| 全体 | `$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v` | All green |
| sandbox 再現 | `$env:STOCKTOOL_ENV="sandbox"` で本番 Parquet のコピーを用意 → `latest_master.json` に BOM を付けて `rotate_and_archive_to_parquet()` → 修正前は行数激減、修正後は例外で停止 | 修正後に**新世代が生成されていない**（`prices_*.parquet` が増えない） |
| 誤爆確認 | sandbox で正常なポインタのまま通常のローテートを実行 | 従来どおり成功する |

### 6.x 実行結果（2026-09-09）

| 検証 | 結果 |
|---|---|
| ユニット（`test_parquet_cache_manager.py`） | **30 passed**（新規6件を含む） |
| ユニット（`test_preload_data_no_regenerate.py`、新規） | **4 passed** |
| 全体 `pytest backend/tests/` | **1534 passed**（3分06秒） |
| sandbox 統合（`tmp/verify_pointer_guard_sandbox.py`） | **6/6 PASS**（下表） |

sandbox は本番 Parquet をハードリンクしたワークツリー隔離環境
（`data/sandbox/stocktool.db`、prices **6,739,935行**）。実行時に
`DATABASE ENVIRONMENT: NON-PRODUCTION` のバナーが出ることも確認済み。

```
  [PASS] preload_data がポインタ破損で停止する  - ParquetPointerUnreadableError
  [PASS] preload_data が新世代を作っていない  - 1 件（実行前 1 件）
  [PASS] rotate がマージ元不明で停止する  - ParquetPointerUnreadableError
  [PASS] rotate が新世代を作っていない  - 1 件（実行前 1 件）
  [PASS] ポインタの内容が復元されている
  [PASS] prices の行数が変化していない  - 6,739,935 → 6,739,935
```

**修正前との対比**: 同じ手順（ポインタに BOM 混入 → ローテート）を修正前のコードで
辿ると、ユニットテスト `test_rotate_aborts_when_pointer_unreadable_instead_of_truncating`
が示すとおり**履歴を失った新世代が公開された**。修正後は世代ファイルが1件も増えない。

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**:
  「`old_paths` が空になると履歴が切り詰められる」がコード読解の誤りなら、
  sandbox で BOM を仕込んで `rotate_and_archive_to_parquet()` を回しても
  **新世代の `prices` 行数が旧世代と同等のまま**になる。
  逆に、切り詰めが起きるのに行数チェックで検出できないなら、想定と違う場所で
  別のガードが効いている（＝この issue の前提が古い）。
- **独立経路での確認（実施済み・2026-09-09）**: コード読解に依存しない実測で確認した。
  判定は**世代ファイル数と `prices` の行数**（`pyarrow` のメタデータから取得）で行い、
  `rotate_and_archive_to_parquet()` の戻り値やログ（＝被験対象と同じ情報源）を
  根拠にしていない。
  - **前提が正しいことの確認**: ユニットテストで修正前の挙動を再現し、
    ポインタ破損時に **2020-01-02 の履歴が消えた世代が公開される**ことを観測した。
    「old_paths が空だと履歴が切り詰められる」はコード読解の誤りではなかった。
  - **修正後の確認**: sandbox 実データ（prices 6,739,935行）で
    **世代ファイルが増えず、行数も変化しない**ことを確認した。
  - **残る限界**: 「ポインタが消えた」ケース（3-B の 2）は sandbox では試していない
    （ユニットテスト `test_rotate_aborts_when_pointer_missing_but_parquet_files_exist`
    のみ）。実データでの再現は世代ファイルの退避を伴うため見送った。

### 6.2 転記の完全性

- **転記元**: `doc/issue_list.md` P1 該当項目（L77-149）
- **元の件数**: 対応案 3件（1: 例外分離 ／ 2: マージ元不明なら中止 ／ 3: `preload_data` の自動再生成廃止）＋ 未検証事項1件（sandbox 再現）
- **本計画書の件数**: 4件すべてを採用。対応案 1 → §3-A、2 → §3-B、3 → §3-C（2026-09-09 ユーザー判断で取り込み）、未検証事項 → §5 と §6.1
- **差分の説明**: 差分なし（4/4）。当初は対応案3を §4 の判断事項として保持していたが、レビューで「含める」と決まったため §3-C に昇格し、§8 の残作業からは外した

## 7. 途中発生した課題

### 7-1. 3-B の 2 のガードは誤爆しない（2026-09-09 実測・§4-3 の宿題）

`parquet_dir` に `prices_*.parquet` が実在するのにポインタが無い状態を、
**正規の運用で作る経路は存在しない**ことを確認した。

| フロー | `parquet_dir` の扱い | 判定 |
|---|---|---|
| `archive_parquet_master.py`（完全再構築の前段。`db_recovery_procedure.md:123` が必須と規定） | `shutil.move` で **`parquet_master/` ディレクトリごと** `parquet_master_pre_rebuild_<ts>/` へ改名（L69） | ✅ ディレクトリ自体が消えるので「ファイルだけ残る」状態にならない |
| `deploy_after_merge.py` → `copy_generation()` | ワークスペースへ**ファイルをコピーした直後に同一プロセスでポインタを書く**（`deploy_promotion.py:56-58`）。書けなければ `OSError` で中断 | ✅ 途中状態で rotate が走る窓が無い |
| `promote_generation()` | 本番へコピー → `data_version_*.json` → ポインタ差し替えの順。ポインタ更新失敗は `OSError` | ✅ 同上 |
| `provision_worktree_data.py --mode write` → `link_parquet_master()` | ハードリンク後に sandbox のポインタを書く（L172-175） | ✅ 同上 |
| `clean_old_parquet_versions()` | 旧世代の実体を消すが**現行ポインタは触らない** | ✅ |

**なお、`copy_generation` / `promote_generation` がポインタ書き込みに失敗して中断した場合は
「ファイルだけ残る」状態になるが、そこで次の rotate を止めるのは 3-B の 2 の意図どおり**
（マージ元が特定できないので公開してはいけない）。誤爆ではなく正しい発火。

### 7-2. 3-C は運用手順の変更を伴わない（2026-09-09 実測）

`preload_data(refresh_cache=...)` の呼び出しを全数確認した。

| 呼び出し元 | 値 |
|---|---|
| `optimization_runner.py:42` / `run_scenario_batch.py:237,255` / `scenario_runner.py:238` / `scenario_comparison_runner.py:115` | **明示的に `False`** |
| `backtest_runner.py` CLI `--refresh-cache` / `scenario_runner.py` CLI `--refresh-cache` | ユーザーの明示指定 |
| `verify_db_vs_cache.py:26` | 意図的に `True`（キャッシュと DB の突き合わせが目的） |
| `run/*.bat` | `--refresh-cache` の指定は**1件も無い** |

つまり**「暗黙の自動再生成に依存している運用は存在しない」**。3-C を入れても手順は変わらない。

### 7-3. 【計画外の発見】`refresh_cache` が FastAPI のクエリパラメータとして外部に露出している

`backend/api/backtest_router.py:1285` のレジーム比較エンドポイントは `refresh_cache: bool = False` を
**クエリパラメータとして受け付け**、`BackgroundTasks` 経由で `preload_data` へ渡している。
つまり **`?refresh_cache=true` を付けた1回の HTTP リクエストで、本番 Parquet のローテートが走る**。

- 既定は `False` なので通常の画面操作では発火しない
- 3-A・3-B が入れば「壊れた世代の公開」は止まるので、履歴消失には至らない
- ただし「Web リクエストが本番マスタを書き換えられる」構造は 3-C の趣旨（読み取り専用の処理が
  書き込みに入らない）と同じ問題
- **フロント側は確認済み**: `frontend/src/api/backtest.ts:234` の `runScenarioComparison()` は
  `refresh_cache` を送れる定義になっているが、**この関数を呼んでいる箇所がフロントに1つも無い**
  （デッドコード）。よって画面からは発火せず、**パラメータを削除しても UI に影響しない**。
  ただしスコープ外の変更なので §8 に残し、ユーザー判断を仰ぐ

### 7-4. 【計画外の発見】`rebuild_backtest_data.py` にも同型の握り潰しがある

`backend/scripts/rebuild_backtest_data.py:125-128` は `rotate_and_archive_to_parquet()` の例外を
`logger.error` だけで飲み込み、そのまま後続の `purge_sqlite_cache_older_than_2_years()` へ進む。
**アーカイブに失敗したのに SQLite の古いレコードをパージする**という最悪の順序になっている。
本計画のスコープ外だが §8 に残す。

### 7-6. 切り詰めの再現は sandbox ではなくユニットテストで行った（2026-09-09）

計画では「sandbox で修正前の切り詰めを再現する」としていたが、**実関数を使った
ユニットテストのほうが再現として厳密**だったのでそちらを本体にした。

`test_rotate_aborts_when_pointer_unreadable_instead_of_truncating` は
`rotate_and_archive_to_parquet()` / `merge_timeseries_table()` の実体を呼び、
「ホット期間のパージ → ポインタに BOM 混入 → 再ローテート」という事故と同じ順序を辿る。
修正前はここで 2020-01-02 の履歴が消えた新世代が公開され、修正後は例外で停止して
**新世代のファイルが1つも増えない**ことを世代ファイル数で確認している。

sandbox 側は「本番規模の実データでもガードが（重い処理に入る前に）発火するか」の
統合確認として `tmp/verify_pointer_guard_sandbox.py` で別途実施する。
判定は**ログではなく Parquet 世代の実体（ファイル数・prices 行数）**で行う。

### 7-5. 【計画外の発見・軽微】`run_local_rebuild.py` のエラーメッセージが実在しないバッチを案内する

`backend/scripts/run_local_rebuild.py:73` は失敗時に `Run refresh_All.bat first.` と出すが、
`run/` に `refresh_All.bat` は存在しない。3-C で例外メッセージを整える際に併せて直す。

## 8. スコープ外・残作業

- `backtest_router.py:1285` の `refresh_cache` クエリパラメータ露出（§7-3）— 要判断。
  フロントは呼んでいない（デッドコード）ので削除しても UI 影響なし
- `rebuild_backtest_data.py:125-128` の rotate 失敗握り潰し＋パージ続行（§7-4）
- `require_non_empty` を「行数が減ったら止める」に拡張する案 — §2.2 のとおり本計画では扱わない

## 9. 取り込み手順（main への反映）

```powershell
# 差分の確認（rules §7 が義務づけるレビューゲート）
git diff main..fix/parquet-pointer-fail-loud

# 取り込み
git merge --no-ff fix/parquet-pointer-fail-loud

# 撤収（素の git worktree remove は禁止。ジャンクションを辿ってリンク先を消す）
.	ools
emove_worktree.ps1 parquet-pointer-fail-loud -DeleteBranch
```

- **変更種別: D**（コード・ドキュメントのみ）。データ形状もスキーマも変えないため、
  `tools/deploy_after_merge.ps1` によるデータ昇格は**不要**。
- 本計画書（`doc/in_progress/parquet_pointer_fail_loud_plan.md`）は**本体チェックアウト側**に
  ステージ済み（本体ではコミットしない規約のためユーザーがコミットする）。
  取り込み後に `doc/completed/` へ移動する。
