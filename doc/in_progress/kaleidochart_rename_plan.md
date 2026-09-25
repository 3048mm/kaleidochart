# プロジェクト名・フロントエンド名を「KaleidoChart」に統一するリネーム作業 計画書

- **ステータス**: 🚧 進行中(ワークツリー作業完了・G3 レビュー待ち → merge 後に実機移行)
- **実施者**: AI エージェント(Claude)
- **開始日**: 2026-09-18 / **完了日**: —
- **作業ブランチ**: `rename/kaleidochart-branding`(ワークツリー)
- **着手条件**: 他セッション・他タスクが一段落してから(ユーザー指示)
- **変更種別**: A(コード・文書の文字列のみ)＋ **実機タスクスケジューラの移行(手動操作・merge 後)**
- **対象 issue / 関連ドキュメント**: なし(会話内で決定。§6.2)

## 1. 背景と目的

プロジェクト名「stocktool」・フロントエンド表示名「Stock Analyzer」はいずれも初期の仮称。
命名検討の結果 **「KaleidoChart」**(ギリシャ語 kalos=美しい + eidos=形。「美しい形のチャートを
見つける」＝トレンドテンプレート/パターンのスクリーニングという本質に合致)に決定した。
npm レジストリ確認済み(`kaleidochart` / `kaleido-chart` は空き。単体の `kaleido` は npm・PyPI とも別用途で使用済み)。

完了時のゴール:
- ユーザーの目に触れる表示名(README・フロントUI・API doc・起動バナー)が「KaleidoChart」に揃っている
- Windows タスクスケジューラのタスク名が `KaleidoChart_*` に揃い、**旧 `StockTool_*` タスクが残っていない**
  (日次更新が二重実行されない・欠落しない)

ベースライン(grep 実測・2026-09-18):
- `stocktool` への言及 397 ファイル / `STOCKTOOL_*` 環境変数 312 箇所 / `stocktool.db` 参照 217 ファイル(いずれも対象外。§2.2)
- 表示名 `Stock Analyzer`: README.md, frontend/index.html, frontend/src/App.tsx, backend/api/server.py
- 表示名/タスク名 `StockTool`: run/*.bat(12ファイル)、doc 2件、skill 1件(＋ .agents ミラー)

実機タスクスケジューラ(2026-09-25 `Get-ScheduledTask` 実測):

| 現在のタスク名 | 状態 | 備考 |
| :--- | :--- | :--- |
| `StockTool_DailyUpdate` | Ready | **手作業で作成(2026-05-28)**。単一タスクに火〜土 07:00 / 13:00 の週次トリガー2本。`register_daily_task.bat` とは**既に不一致** |
| `StockTool_RunServer` | Ready | `register_run_server.bat` で作成(起動時) |
| `StockTool_WeeklyMaintenance_Sunday_0200` | Ready | `register_weekly_maintenance.bat` で作成 |

## 2. スコープと設計判断

### 2.1 変更すること

1. **表示名**: README 見出し、frontend `<title>` / meta description / ヘッダー、FastAPI `title` とウェルカムメッセージ、`frontend/package.json` の name
2. **起動バナー**: `run/*.bat` の echo 文言 `StockTool - ...` → `KaleidoChart - ...`
3. **タスク名**: `run/register_*.bat` のタスク名を `KaleidoChart_*` へ。各スクリプトに**旧名タスクの削除**を追加
4. **`register_daily_task.bat` を実機の構成に合わせる**(単一タスク・火〜土 07:00/13:00 週次)。
   改名で再登録が必要になる以上、実機と食い違うスクリプトを残すと次に使った人が二重実行を起こす
5. **実機タスクの移行**(merge 後・手動操作): 既存タスクを XML でエクスポート → 新名で登録 → 旧名を削除
6. **タスク名を参照する文書**: `doc/db_recovery_procedure.md`、`.claude/skills/parquet-data-quality/SKILL.md`(→ `tools/sync_skills.py --apply` で `.agents` 同期)
7. `CLAUDE.md` の Project overview にブランド名を一文追記

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| GitHub リポジトリ名 | **ユーザーが GitHub 側で実施**(本計画では触らない) | ユーザー回答。GitHub は旧 URL をリダイレクトするので `origin` は壊れないが、必要なら `git remote set-url` もユーザーが実施 |
| ローカルのフォルダ名(`D:\...\stocktool`) | **変更しない** | メモリのディレクトリキー、既存ワークツリー、IDE ワークスペース、doc 内の `file:///d:/.../stocktool/` リンク、**タスクスケジューラに登録された実行パス**が全て壊れる。表示名の統一という目的に対して得るものがない |
| `stocktool.db` ファイル名・パス参照 | **変更しない** | 本番データファイル名(内部識別子)。ブランディングと無関係 |
| `STOCKTOOL_*` 環境変数名 | **変更しない** | 同上 |
| `backend.x` / `api.x` の import・モジュール名 | **変更しない** | 内部規約 |
| favicon | **今回は作らない** | ユーザー回答「元の favicon があるなら」。リポジトリ・git 履歴・`frontend/public/` のいずれにも実体が無かった(`index.html` の `/favicon.ico` 参照は元から404)。§8 へ |
| `doc/completed/` 配下の記述(`bynd_promotion_runbook.md` 等) | **変更しない** | 完了済みの歴史的記録。当時のタスク名で正しい |
| `.claude/worktrees/` 配下 | **対象外** | 別タスクの作業中ワークツリー。merge 時に衝突したらそちらで解消 |
| 移行方法として `register_*.bat` の再実行だけに頼る | **採らない** | 日次タスクは手作業構成でスクリプトと一致しないため、XML エクスポート/インポートで**実機の設定をそのまま複製**する。スクリプト修正は今後の再登録用 |

## 3. 変更内容

| # | ファイル | 変更 |
| :-- | :--- | :--- |
| 1 | `README.md` | `# Stock Analyzer` → `# KaleidoChart` |
| 2 | `frontend/index.html` | `<title>` → `KaleidoChart`。meta description 更新 |
| 3 | `frontend/src/App.tsx:172` | ヘッダー `Stock Analyzer` → `KaleidoChart` |
| 4 | `backend/api/server.py:27,83` | `title="KaleidoChart API"`、ウェルカムメッセージ |
| 5 | `frontend/package.json` | `"name": "kaleidochart-frontend"`(private・未公開・参照元なし) |
| 6 | `run/run_*.bat`・`run/tool/refresh_*.bat`(10ファイル) | echo バナーのみ |
| 7 | `run/register_run_server.bat` | `TASK_NAME=KaleidoChart_RunServer`、旧 `StockTool_RunServer` の削除を追加、バナー |
| 8 | `run/register_weekly_maintenance.bat` | `TASK_NAME=KaleidoChart_WeeklyMaintenance_Sunday_0200`、旧名削除を追加、バナー |
| 9 | `run/register_daily_task.bat` | 単一タスク `KaleidoChart_DailyUpdate`・火〜土 07:00/13:00 に作り直す(schtasks は1タスク複数トリガーを作れないため PowerShell `Register-ScheduledTask` を呼ぶ)。旧 `StockTool_DailyUpdate` と旧スクリプト名 `StockTool_DailyUpdate_0700/_1300` の削除を追加。冒頭の不一致警告コメントを撤去。**ASCII ONLY 制約は維持** |
| 10 | `doc/db_recovery_procedure.md:26` | タスク名 |
| 11 | `.claude/skills/parquet-data-quality/SKILL.md:162` | タスク名 → `sync_skills.py --apply` |
| 12 | `CLAUDE.md` | Project overview に「本ツールの名称は KaleidoChart(旧称 stocktool)」の一文 |

**実機移行手順(merge 後に実施。§4-1 の時間帯制約を守る)**:
```powershell
# 1. 退避(ロールバック用)
foreach ($t in 'StockTool_DailyUpdate','StockTool_RunServer','StockTool_WeeklyMaintenance_Sunday_0200') {
  Export-ScheduledTask -TaskName $t | Out-File "data\maintenance_reports\task_backup_$t.xml" -Encoding unicode
}
# 2. 新名で複製登録(XML をそのまま流用＝トリガー・実行パス・実行ユーザーが完全一致)
$map = @{ 'StockTool_DailyUpdate'='KaleidoChart_DailyUpdate'; 'StockTool_RunServer'='KaleidoChart_RunServer';
          'StockTool_WeeklyMaintenance_Sunday_0200'='KaleidoChart_WeeklyMaintenance_Sunday_0200' }
foreach ($old in $map.Keys) {
  Register-ScheduledTask -TaskName $map[$old] -Xml (Get-Content "data\maintenance_reports\task_backup_$old.xml" -Raw -Encoding Unicode)
}
# 3. 新タスクの存在とトリガーを確認してから旧タスクを削除
Get-ScheduledTask -TaskName 'KaleidoChart_*' | Select TaskName, State, @{n='Triggers';e={$_.Triggers.Count}}
Unregister-ScheduledTask -TaskName 'StockTool_DailyUpdate','StockTool_RunServer','StockTool_WeeklyMaintenance_Sunday_0200' -Confirm:$false
```
ロールバック: 新タスクを削除し、手順1の XML から旧名で再登録。

## 4. ユーザー確認事項(回答済み・2026-09-25)

1. タスクスケジューラ名 → **揃える**(ユーザー回答)。§2.1-3〜5 に反映。
   実機移行は**日次更新(07:00 / 13:00、所要時間中)と週次メンテ(日曜 02:00)に重ならない時間帯**に行う。
   `KaleidoChart_RunServer` は起動時トリガーなので、移行しても再起動までサーバーは旧タスク起動のまま動き続ける(問題なし)
2. リポジトリ名 → **GitHub 側はユーザーが実施**。ローカルのフォルダ名は変えない(§2.2)
3. favicon → 元ファイルが存在しないため**見送り**(§2.2・§8)
4. `package.json` の name → **影響なし**(ユーザー確認済み)

残る確認事項: なし。実機移行(§5 後半)は G4 merge 後に、実施タイミングをユーザーに一声かけてから行う。

## 5. 実装順序と進捗チェックリスト

**ワークツリー内(コード・文書)**
- [x] README / frontend(index.html・App.tsx・package.json)/ backend/api/server.py の表示名
- [x] run/*.bat・run/tool/*.bat の echo バナー(10ファイル)
- [x] register_run_server.bat / register_weekly_maintenance.bat のタスク名＋旧名削除
- [x] register_daily_task.bat を実機構成(単一タスク・火〜土 07:00/13:00)に作り直し
- [x] doc/db_recovery_procedure.md・parquet-data-quality skill のタスク名、`sync_skills.py --apply`
- [x] CLAUDE.md にブランド名の一文
- [ ] 検証(§6)・G3 ブランチレビュー → ワークツリーでコミット

**merge 後(実機)**
- [ ] 実機タスクの XML 退避 → 新名で登録 → 確認 → 旧名削除(§3 手順)
- [ ] 翌営業日の 07:00 実行が `KaleidoChart_DailyUpdate` で成功したこと(LastRunTime / LastTaskResult=0)を確認
- [ ] 計画書を `doc/completed/` へ移動

### 作業中メモ

- 2026-09-26: ワークツリー `.claude/worktrees/rename+kaleidochart`(`--mode write`)で実装。
  pytest 2016 passed / 1 skipped、vitest 69 passed、`npm run build` 成功(dist の title=KaleidoChart)、.bat 非ASCII 0件。
- `register_daily_task.bat` の登録コマンドのみを一時名 `KaleidoChart_DailyUpdate_DRYRUN` で実機登録し、本番
  `StockTool_DailyUpdate` と比較 → コマンド・ログオン(Interactive/tk)・MultipleInstances(IgnoreNew)・
  トリガー(07:00/13:00・DaysOfWeek=124=火〜土・毎週)が一致。一時タスクは削除済み。
  **注意: このバッチ自体を実行すると旧 `StockTool_DailyUpdate` を削除する**(移行は §3 の XML 手順で行う)。
- 次: G3 ブランチレビュー → G4 merge(ユーザー)→ 実機移行。

## 6. 検証プラン / 結果

- `npm run build` が通る / `npm test` green / `pytest backend/tests/` が変更前と同じ結果
- バックエンド起動 → `http://127.0.0.1:8000/docs` のタイトルが「KaleidoChart API」
- `npm run dev` → タブタイトルとヘッダーが「KaleidoChart」
- `.bat` は ASCII のみであること(`grep -P '[^\x00-\x7F]' run/*.bat run/tool/*.bat` が0件)・CRLF 維持
- `register_daily_task.bat` は実機に登録せず、生成されるタスク定義を確認する(`-WhatIf` 相当が無いため、一時名 `KaleidoChart_DailyUpdate_DRYRUN` で登録→`Export-ScheduledTask` で本番 XML とトリガーを比較→即削除)

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **誤りなら観測されるはず**:
  - 表示名: 変更対象ファイルに `Stock Analyzer` / `StockTool` が残る(§2.2 の除外箇所以外で)
  - タスク移行: 移行後に `Get-ScheduledTask -TaskName 'StockTool*'` が1件以上返る(二重実行)、
    または `KaleidoChart_DailyUpdate` のトリガーが旧タスクと異なる(曜日・時刻)、
    または翌営業日の `LastTaskResult` が 0 以外/未実行
- **独立経路での確認**: grep は「書いた側」と同じ情報源なので、タスクについては**実機の実行結果**
  (翌営業日の `Get-ScheduledTaskInfo` と `stocktool.db` の最新日付が進んでいること)で確認する。
  測る契機: 実機移行の翌営業日 07:00 以降の最初のセッション。

### 6.2 転記の完全性

- **転記元**: 本会話 / **元の件数**: 決定1(名称) + §4 回答4 / **本計画書の件数**: 5(§1・§4-1〜4) / **差分**: なし。issue_list.md に関連項目なし(grep 0件)

## 7. 途中発生した課題

- (計画段階・2026-09-25)`register_daily_task.bat` が実機の日次タスクと既に不一致であることが判明
  (ファイル冒頭に警告コメントあり)。改名で再登録が必要になるため、本計画で実機に合わせて作り直す(§3-9)。

## 8. スコープ外・残作業

- favicon の新規作成(元ファイル無し。作るならデザインから)
- ローカルのフォルダ名変更(§2.2)
- GitHub リポジトリ名変更・`git remote set-url`(ユーザー実施)
