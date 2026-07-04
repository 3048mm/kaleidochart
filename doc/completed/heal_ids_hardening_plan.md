# heal_*_ids() 安全化・高速化 計画書

| 項目 | 内容 |
| :--- | :--- |
| **対象課題** | issue_list P1「`heal_*_ids()` の安全弁と実行タイミング見直し」（audit D-3 + I-7 事故の再発防止） |
| **実施者 (AI)** | Claude Code — モデル: `claude-fable-5` (Fable 5) |
| **開始日** | 2026-07-04 |
| **進め方** | 本計画書のアクションアイテム順に TDD で実施。進捗・課題は本書を随時更新 |
| **検証環境** | pytest（使い捨てDB）+ Sandbox（`STOCKTOOL_DB_PATH` と `STOCKTOOL_USER_DB_PATH` の**両方**を必ず設定） |

## 1. 背景（何が問題か）

`heal_watchlist_ids()` / `heal_portfolio_ids()` は「T1 再同期で symbols.id が変わっても、ticker/exchange を鍵に symbol_id を自己修復する」仕組みだが、以下の問題を持つ。

### 1.1 安全性（最重要 — I-7 事故の根本原因）
- 読み取り GET が **user_data.db を無条件に UPDATE・commit** する。
- 接続中の stocktool DB の symbols が不完全（Sandbox 誤接続、T1 同期途中、DB破損）だと、解決できない ticker の symbol_id を**全件 NULL 化**する。2026-07-04 に実際に watchlist 47件が NULL 化した（復旧済み。詳細: screener_refactoring_d1_d2.md I-7）。
- さらに、誤った DB に接続している場合は「正の再マッピング」も**誤った id 空間への書き換え**になり得る。

### 1.2 性能（audit D-3）
- GET リクエストのたびに全項目を走査。
- 項目ごとに `db.query(Symbol).filter_by(id=...)` を発行する **N+1 クエリ**（watchlist 47件なら 47+1 クエリ/GET）。
- `db.query(Symbol).all()` が全カラム ORM で全銘柄（約4,600行）を毎回ロード。

### 1.3 構造
- watchlist 版と portfolio 版（positions + history の2テーブル）がほぼ同一ロジックの**二重実装**（D-2 と同じ病理）。

## 2. 方針

### 2.1 安全弁 (Safety Valve)
- 修復対象の全項目のうち、**ticker が解決できない割合が閾値（30%）を超えた場合、一切の書き込みをスキップ**して `logger.warning` を出す。
  - 根拠: 正当な上場廃止・ticker変更は一度に1〜2件しか起きない。大量解決不能は「接続先 symbols が不完全」のシグナルであり、NULL 化も正マッピングも危険。
  - 正常運用（47件中0〜1件が解決不能）では従来通り修復される。
- 閾値は定数 `HEAL_MAX_UNRESOLVED_RATIO = 0.3` として共通モジュールに定義。

### 2.2 スロットル（性能・D-3）
- **「前回の heal が clean（修復ゼロ・安全弁非発動）だった場合のみ、TTL（60分）内の再実行をスキップ**」する。
  - 通常運用では常に clean なので、GET のたびの全件走査が事実上なくなる。
  - 修復が発生した直後や安全弁発動中は毎回実行される（異常状態の検知を止めない）。
  - この設計なら「同一テスト内で heal を2回期待する既存テスト」（`test_heal_portfolio_ids`: 1回目で position 修復 → 2回目で history 修復）も壊れない。
- スロットルキーは `(種別, stocktool DBパス, user DBパス)` — 接続先を切り替えたら別カウント（Sandbox/本番の取り違え防止）。
- テスト分離のため `reset_heal_throttle()` を提供し、`backend/tests/conftest.py`（新設）の autouse フィクスチャで毎テストリセット。

### 2.3 N+1 解消と共通化
- symbols のロードを「1クエリ・必要カラムのみ（id, ticker, exchange）」に変更し、`by_id` / `by_(ticker,exchange)` / `by_ticker` の3マップを構築。項目ごとの `filter_by(id=...)` クエリを廃止。
- 共通コアを **`backend/api/symbol_heal.py`** に新設し、watchlist / portfolio(positions+history) の両方がこれを呼ぶ形に一本化。

### 2.4 変えないこと（互換性）
- 修復ロジックの意味論（ticker+exchange 優先 → ticker のみでフォールバック、解決不能は NULL）は正常時は従来と同一。
- 呼び出し箇所（watchlist 2箇所、portfolio 5箇所）のシグネチャは不変。

## 3. アクションアイテム

| # | 内容 | 状態 |
| :--- | :--- | :--- |
| A1 | `backend/api/symbol_heal.py` 新設: マップ構築・修復コア・安全弁・スロットル（TDD: `backend/tests/api/test_symbol_heal.py` 8件） | ✅ |
| A2 | `watchlist_service.heal_watchlist_ids` を共通コアへ移行 | ✅ |
| A3 | `portfolio_service.heal_portfolio_ids` を共通コアへ移行 | ✅ |
| A4 | `backend/tests/conftest.py` 新設（スロットル autouse リセット）+ 既存テスト全パス確認 | ✅ |
| A5 | Sandbox で I-7 シナリオ再現テスト: stocktool のみ Sandbox に向けた状態で GET しても user_data が NULL 化されない（安全弁発動）ことを実証 | ✅ |
| A6 | 仕様書（backend_specification.md §5.2 付近）・issue_list.md 更新 | ✅ |

## 4. 進捗ログ

| 日時 | 項目 | 状態 | メモ |
| :--- | :--- | :--- | :--- |
| 2026-07-04 | 調査・計画 | ✅ 完了 | heal 2関数・呼出7箇所・既存テストの期待値（同一テスト内2回heal）を確認。スロットルは「clean 時のみ TTL スキップ」方式に決定 |
| 2026-07-04 | A1 | ✅ 完了 | TDD（Red→Green）で `symbol_heal.py` 実装。ユニットテスト8件（再マッピング/単発NULL化/安全弁/境界値30%/スロットル3種/空リスト） |
| 2026-07-04 | A2-A4 | ✅ 完了 | 両サービスを共通コアへ移行（watchlist は3行、portfolio は positions+history 合算で判定）。conftest 新設。全テスト 276 passed（失敗1件は既存の本番DB依存テスト） |
| 2026-07-04 | A5 | ✅ 完了 | **I-7 再現実証**: stocktool=Sandbox(351銘柄)/user=47件コピーの誤設定で GET → 安全弁発動（`SAFETY VALVE — 47/47件`警告）、**watchlist は before/after とも NULL=0 で無傷**、API は 200 応答（修正前: 47件全NULL化+500） |
| 2026-07-04 | A6 | ✅ 完了 | 仕様書・issue_list 更新。**全アクションアイテム完了** |

## 5. 課題ログ

| ID | 課題 | 状態 | 解決内容 |
| :--- | :--- | :--- | :--- |
| H-1 | 単純な TTL スロットルは既存テスト `test_heal_portfolio_ids`（同一テスト内で heal 2回を期待）を壊す | ✅ 解決（設計で回避） | 「前回 clean の場合のみスキップ」方式を採用。修復が発生した回はスロットル記録しないため、連続修復シナリオが成立する |
| H-2 | I-8 の回帰テスト（watchlist 2件中1件が解決不能=50%）が新しい安全弁の保護対象になり失敗 | ✅ 解決 | テストの意図（解決不能項目があっても 200 + symbol_id=null）は維持したまま、フィクスチャを4件中1件=25%（閾値以下）に調整。安全弁の発動側は `test_safety_valve_blocks_mass_nulling` が別途担保 |
| H-3 | スロットル記録がテスト間で漏れて2件のテストが失敗（in-memory DB の URL がテスト間で同一のため） | ✅ 解決 | `backend/tests/conftest.py` の autouse フィクスチャで毎テスト `reset_heal_throttle()` を実行 |
