# プロジェクト名・フロントエンド名を「KaleidoChart」に統一するリネーム作業 計画書

- **ステータス**: 🚧 計画レビュー中
- **実施者**: AI エージェント(Claude Sonnet 5)
- **開始日**: 2026-09-18 / **完了日**: —
- **作業ブランチ**: `rename/kaleidochart-branding`(ワークツリー推奨。§4参照)
- **対象 issue / 関連ドキュメント**: なし(会話内で決定。§6.2参照)

## 1. 背景と目的

プロジェクト名「stocktool」・フロントエンド表示名「Stock Analyzer」はいずれも初期の仮称で、
ユーザーが会話内でブランド名の検討を依頼し、複数ラウンドの命名ブレストの結果
**「KaleidoChart」**(kalos=美しい + eidos=形。トレンドテンプレート/パターン screening という
このツールの本質と合致)に決定した。npm レジストリ確認済み(`kaleidochart`/`kaleido-chart` は空き)。

完了時のゴール: **ユーザーの目に触れる表示名(README・フロントUI・API doc等)が
「KaleidoChart」に統一されている**こと。内部識別子(DBファイル名・環境変数・タスク名など)は
意図的にスコープ外とする(§2.2)。

ベースライン(着手前の現状、grep実測値・2026-09-18時点):
- `stocktool`(大小文字問わず)への言及: 397ファイル
- `STOCKTOOL_*` 環境変数名: 312箇所
- `stocktool.db` パス参照: 217ファイル
- 表示名としての `Stock Analyzer` / `StockTool`: README.md, frontend/index.html,
  frontend/src/App.tsx, backend/api/server.py, run/*.bat(7ファイル)

## 2. スコープと設計判断

### 2.1 変更すること

ユーザーの目に触れる「表示名」レイヤーのみ:

1. `README.md` — タイトル見出し
2. `frontend/index.html` — `<title>` / meta description
3. `frontend/src/App.tsx` — ヘッダー表示テキスト(UI上に実際に見える文言)
4. `backend/api/server.py` — FastAPI `title=` / ウェルカムメッセージ(`/docs` に表示される)
5. `frontend/package.json` — `"name"` フィールド(cosmetic、npm公開なし)
6. `run/*.bat`(7ファイル) — コンソール起動時の echo バナー文言のみ
7. (任意)`CLAUDE.md` — Project overview 節にブランド名を一行明記
8. (任意・低優先)favicon — 現状 `frontend/index.html` が参照する `/favicon.ico` は
   `frontend/public/` に実体が存在せず壊れている。KaleidoChart刷新のついでに作るかは別判断

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| リポジトリ/フォルダ名(`stocktool`)・GitHubリポジトリ名(`3048mm/stocktool`) | **今回は変更しない** | ブラスト半径が大きすぎる: メモリシステムのディレクトリキー(`d--My-Documents-Programing-stocktool`)、既存ワークツリー、IDEワークスペース、doc内の絶対パス(`file:///d:/.../stocktool/...`)リンクが全滅する。やるなら独立した別イベントとして計画する |
| `stocktool.db` ファイル名・コード内パス参照(217箇所) | **変更しない** | 本番データファイル名・内部識別子。ブランディングと無関係で、変更コストに対して得るものがない |
| `STOCKTOOL_ENV` / `STOCKTOOL_DB_PATH` 等の環境変数名(312箇所) | **変更しない** | 同上。内部識別子であり表示名ではない |
| Windows タスクスケジューラのタスク名(`StockTool_DailyUpdate` 等、`run/register_*.bat` の `TASK_NAME=`) | **変更しない**(表示バナーのみ変更) | 実機に登録済みの運用資産。`.bat`側だけ書き換えても実機のタスクは自動追従せず、手動の登録解除→再登録が要る。日次更新が止まるリスクがあるため別途ユーザー判断が要る(§4) |
| `.claude/worktrees/feat+moomoo-split-verification/` 配下 | **対象外** | 別タスクの作業中ワークツリー。このプランで触れると衝突する |
| `backend.x` / `api.x` などの import 規約・モジュール名 | **変更しない** | PYTHONPATH前提の内部規約で表示名と無関係 |
| `doc/` 配下の `stocktool.db` 等の技術的パス言及 | **変更しない** | §2.2の各判断と整合(DBファイル名・パスは維持するため、記述も現状のまま正しい) |

## 3. 変更内容

| # | ファイル | 変更 |
| :-- | :--- | :--- |
| 1 | `README.md` | 1行目 `# Stock Analyzer` → `# KaleidoChart` |
| 2 | `frontend/index.html` | `<title>Stock Analyzer</title>` → `<title>KaleidoChart</title>`。meta description も更新 |
| 3 | `frontend/src/App.tsx:172` | `<span>Stock Analyzer</span>` → `<span>KaleidoChart</span>` |
| 4 | `backend/api/server.py:27` | `FastAPI(title="Stock Analyzer API", ...)` → `title="KaleidoChart API"` |
| 5 | `backend/api/server.py:83` | ウェルカムメッセージ文言を同様に更新 |
| 6 | `frontend/package.json:2` | `"name": "stocktool-frontend"` → `"name": "kaleidochart-frontend"` |
| 7 | `run/register_daily_task.bat` 他6ファイル | echo バナー文中の `StockTool` → `KaleidoChart`(`TASK_NAME=` 変数値は据え置き) |
| 8 | (任意)`CLAUDE.md` | Project overview 冒頭にブランド名の一文を追記 |

影響範囲: いずれも文字列のみの変更でロジック変更なし。`frontend/package.json` の name は
private かつ未公開のため他コードから参照されていないことを確認済み(依存元なし)。

## 4. ユーザー確認事項

1. **Windows タスクスケジューラのタスク名は現状維持でよいか** → 維持を推奨(表示バナーのみ変更)。
   もし改称したい場合は別イベントとして「登録解除→新名称で再登録」の手順を計画する
2. **リポジトリ名 / GitHub リポジトリ名は今回のスコープに含めるか** → 含めない(§2.2)を推奨。
   含める場合はメモリシステムのディレクトリキー移行なども絡むため計画を作り直す
3. **favicon 刷新は今回に含めるか** → 後回し(低優先のスコープ外)を推奨。デザイン作業が発生するため
4. **`frontend/package.json` の name 変更は影響ゼロと判断してよいか** → 上記確認済みのとおり問題ない想定

→ 特に指定がなければ、上記いずれも「推奨」どおりに進める。

## 5. 実装順序と進捗チェックリスト

- [ ] README.md のタイトル変更
- [ ] frontend/index.html の title / meta description 変更
- [ ] frontend/src/App.tsx のヘッダー表示テキスト変更
- [ ] backend/api/server.py の FastAPI title / ウェルカムメッセージ変更
- [ ] frontend/package.json の name 変更
- [ ] run/*.bat(7ファイル)の echo バナー文言変更
- [ ] (任意)CLAUDE.md にブランド名の一文追記
- [ ] 動作確認(§6)
- [ ] grep で対象ファイルに旧名称の取りこぼしがないか最終確認
- [ ] ワークツリーでコミット(完了報告にブランチ名・SHA・取り込みコマンドを明記)

### 作業中メモ

(未着手。ユーザー指示: 他セッション・他タスクが一段落してから着手)

## 6. 検証プラン / 結果

- バックエンドを起動し、`http://127.0.0.1:8000/docs` のタイトルが「KaleidoChart API」になっていることを確認
- フロントエンドを `npm run dev` で起動し、ブラウザタブタイトルとヘッダー表示が「KaleidoChart」になっていることを確認
- `npm run build` が型エラーなく通ることを確認
- `npm test` / `pytest backend/tests/` が変更前と同じ結果(green)であることを確認(文字列変更のみなのでロジックへの影響はないはずだが念のため)

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

このタスクは「結論の正誤」を扱うものではなく単純な文字列置換のブランディング作業のため、
反証的に検証すべきは**「変更漏れがないか」**という点のみ。

- **この結論が誤りだとしたら観測されるはず**: 完了後に対象ファイル(§3の8ファイル)に対して
  `grep -in "stock analyzer\|stocktool"` を実行すると、`TASK_NAME=` 変数値・DBファイル名・
  環境変数名など §2.2 で意図的に残した箇所**以外**でヒットが残っている
- **独立経路での確認**: 完了後に対象ファイル一覧に対して上記 grep を実行し、ヒットが
  §2.2 の除外対象のみであることを目視確認する(未実施・完了時に実施)

### 6.2 転記の完全性

- **転記元**: 本会話(ユーザーとの命名ブレストの往復) / **元の件数**: 決定事項2点
  (「KaleidoChartに決定」「実作業アクションアイテムに落とし込む」) /
  **本計画書の件数**: 2点(§1決定事項として反映、§5チェックリストとして展開) /
  **差分の説明**: なし。issue_list.md に関連項目は存在しない(§1.1で確認済み、grep 0件)

## 7. 途中発生した課題

(なし・計画段階)

## 8. スコープ外・残作業

- リポジトリ名 / GitHub リポジトリ名のリネーム(§2.2) — 別イベントとして検討
- Windows タスクスケジューラのタスク名変更(§2.2) — 別イベント。実施する場合は
  登録解除→再登録の手順が必要
- favicon 刷新 — 低優先、別途デザインが必要
- `doc/` 配下のプロース内で「かつての仮称」として `stocktool`/`Stock Analyzer` に触れている
  過去の完了済みドキュメント(`doc/completed/` 配下等)は歴史的記録として不変更
