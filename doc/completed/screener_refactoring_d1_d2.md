# D-1 / D-2 リファクタリング計画書: スクリーナー共通化と routers.py 分割

| 項目 | 内容 |
| :--- | :--- |
| **対象課題** | issue_list P0「フィルタ二重実装の共通化 (audit D-2)」+ P1「routers.py の分割 (audit D-1)」 |
| **実施者 (AI)** | Claude Code — モデル: `claude-fable-5` (Fable 5) |
| **開始日** | 2026-07-03 |
| **進め方** | 本計画書の Phase 順に1つずつ実施。各 Phase 完了時に全テスト実行。途中発生した課題は §5 課題ログに記録し、解決してから次へ進む |
| **検証環境** | pytest（使い捨てDB）+ `data/stocktool_sandbox.db`。本番 DB への書き込みは行わない |

## 1. 背景と調査結果

### 1.1 二重実装の現状
| ロジック | API 側 (SQLAlchemy) | バックテスト側 (Pandas) |
| :--- | :--- | :--- |
| RRG Leading/Lagging/Improving In | `routers.py` `_apply_rrg_*` + `/screener` 内インライン（計2重） | `screener_filters.py` `filter_rrg_*` |
| テーマ RS Ratio/Rank 比較 | `_apply_theme_*` 6種 + `/screener` 内インライン | `screener_filters.py` `filter_theme_*` 4種 + `backtest_screener.py` 内インライン |
| RS Trend / MACD 比較 | `_apply_rs_trend_*`, `_apply_rs_macd_hist_rising_21` | `backtest_screener.py` 内インライン |
| 汎用 min/max フィルタ | `_apply_filter`（SQL式） | `apply_filters_to_df` 内マスクエンジン |

つまり同一ロジックが**最大3箇所**（BOOLEAN_FILTER_HANDLERS / `/screener` インライン / Pandas側）に存在する。

### 1.2 発見済みの意味論差異（要注意）
調査で以下の**挙動差異**を発見。共通化時に「どちらを正とするか」の判断が必要:

| ID | 差異 | API 側の挙動 | バックテスト側の挙動 |
| :--- | :--- | :--- | :--- |
| S-1 | `min_market_cap` のテーマ扱い | market_cap が NULL のテーマは SQL 比較で除外される | `\| category=='テーマ'` でテーマを免除（通過） |
| S-2 | `is_rs_ratio_rank_e21_gt_e63` のテーマ扱い | `group_name=='個別'` サブクエリ限定 → テーマは常に除外 | 各行が自分のランクで比較 → テーマも通過し得る |

**方針**: `screener_filters.py`（Pandas側）の意味論を正とする。理由: バックテスト・Optuna 最適化で検証済みのロジックであり、「画面のスクリーナー結果とバックテストの銘柄抽出が一致する」ことが D-2 の目的そのものだから。差異が実データで結果に影響する場合は同値性テストで検知し、§5 に記録してユーザーへ報告する。

### 1.3 外部依存（移動時に更新が必要）
- `backtest_runner.py:442` が `api.routers` から `BOOLEAN_FILTER_HANDLERS`, `_INDICATOR_COLUMNS`, `_VIRTUAL_COLUMNS` を import（TOMLキー検証用）
- テスト5ファイルが `api.routers` から `router`, `get_api_db`, `get_api_user_db`, `get_chart_data` を import
- `test_special_removal.py` が `api.routers._load_presets` を7箇所で patch

## 2. Phase 計画

### Phase 1: スクリーナーの切り出し（純粋移動）
- `api/deps.py` 新設: `get_api_db`, `get_api_user_db`
- `api/screener_router.py` 新設: スクリーナーエンジン一式（`_load_presets`, `_apply_filter`, `BOOLEAN_FILTER_HANDLERS`, `_parse_expression_to_filter`, カラム定義群）+ `/screener/presets`, `/screener/meta`, `/screener/dashboard`, `/screener` の4エンドポイント
- `server.py` に新ルーター登録。`routers.py` には後方互換 re-export を残す
- テストの import / patch 対象を新モジュールへ機械的に更新（**検証内容は不変**）
- **完了条件**: 全テストパス。エンドポイントの挙動変更ゼロ

### Phase 2: フィルタ共通化（D-2 本体）
設計: API は「基準日1日分のクロスセクション DataFrame」を構築し、特殊フィルタを `screener_filters.py` の純関数でマスク評価 → 通過 symbol_id 集合を SQL クエリに `IN` 適用する。数値 min/max フィルタは SQL のまま（汎用・低リスク）。

1. `screener_filters.py` に不足関数を追加（TDD）: `filter_rs_macd_hist_rising_21`, `filter_rs_trend_s21_lt_s63`, `filter_rs_trend_s14_lt_s21`, `filter_theme_rs_trend_rank_s14_gt_s21` 等 + 特殊フィルタキーのレジストリ `SPECIAL_FILTER_KEYS`
2. `backtest_screener.py` のインライン実装を新共通関数呼び出しへ置換（バックテスト側の回帰は既存テストで担保）
3. **同値性テスト作成**: 合成フィクスチャ DB で「旧 SQLAlchemy ハンドラ」vs「新 Pandas 経由」の通過銘柄集合が一致することを、S-1/S-2 差異ケースを除き検証
4. API 側に クロスセクション構築アダプタ（wide `relative_ranks` → `rs21_rank` 等の共通カラム名へ変換）を実装し、`BOOLEAN_FILTER_HANDLERS` と `/screener` インライン実装を共通関数呼び出しへ置換
5. 旧 SQLAlchemy ハンドラ群を削除。`backtest_runner.py` の検証 import をレジストリ参照へ変更
- **完了条件**: 全テストパス + 本番 DB 読み取り専用の新旧全プリセット突合（差異は S-1/S-2 由来のみ）

### Phase 3: 残りのルーター分割（D-1 仕上げ）
- `chart_router.py`: `/chart/{symbol_id}`, `/earnings/{symbol_id}`
- `dashboard_router.py`: `/dashboard`, `/available_dates`, `/theme/{symbol_id}`, `/group_data/{ticker}`, `/ranking` + 共有ビルダー（`_build_panel_item` 等）は `api/panel_builders.py` へ
- `watchlist_router.py`: `/watchlist` 系6エンドポイント
- `routers.py` 残留: `/ping`, `/system/info`, `/symbols`（コア）
- **完了条件**: 全テストパス + routers.py が 200 行未満

### Phase 4: 仕上げ
- 仕様書更新（backend_specification.md の API 構成）、issue_list.md 更新、audit_report.md へ完了追記
- Sandbox で API 起動スモーク確認

## 3. 進捗ログ

| 日時 | Phase | 状態 | メモ |
| :--- | :--- | :--- | :--- |
| 2026-07-03 | 調査 | ✅ 完了 | routers.py 構造マップ、二重実装の全容、意味差異 S-1/S-2 を特定 |
| 2026-07-03 | Phase 1 | ✅ 完了 | `api/deps.py` + `api/screener_router.py`(1120行) を新設、routers.py は 2947→1840行。テスト 241 passed（失敗1件は既存の本番DB依存テストで無関係）。Sandbox スモーク全 200 OK |
| 2026-07-04 | Phase 2 | ✅ 完了 | ①`screener_filters.py` に5関数+`SPECIAL_FILTER_KEYS` レジストリ追加(TDD)。②バックテスト側インライン実装を共通関数へ置換。③同値性テスト14件で新旧一致を証明（S-2の意図した差異を除く）後、API を `screener_cross_section.py` 経由に切替、旧 SQLAlchemy ハンドラ約180行を削除。④S-1(market_cap テーマ免除) も統一。⑤性能退行なし（新旧ともウォームで約0.9秒。初回73秒はコールドキャッシュ起因と確認済み）。⑥本番読み取り専用検証: プリセット items 89→92（S-1/S-2 統一によりテーマが通過するようになった意図した増分）。テスト 267 passed |
| 2026-07-04 | Phase 3 | ✅ 完了 | routers.py(1840行) を `chart_router.py`(800) / `dashboard_router.py`(620) / `panel_builders.py`(269) / `watchlist_router.py`(144) に分割。routers.py 残留は **67行**（/ping, /system/info, /symbols + 後方互換 re-export）。45 API ルート全登録確認、Sandbox スモーク 12 エンドポイント全 200、テスト 268 passed |
| 2026-07-04 | Phase 4 | ✅ 完了 | I-4 修正（fx_rates テストを専用一時DBへ分離）、仕様書・issue_list・audit_report 更新 |

## 4. 完了条件（ゴール）
1. RRG/テーマ/RS比較の特殊フィルタロジックの実体が `indicators/screener_filters.py` の**1箇所のみ**に存在する
2. `routers.py` が責務別に分割され、コア部分が 200 行未満
3. 全テストパス（既存 + 新規同値性テスト）
4. 意味論差異（S-1/S-2 等）の扱いが本書に記録され、ユーザーへ報告済み

## 5. 課題ログ（途中発生した課題と解決）

| ID | 発生 Phase | 課題 | 状態 | 解決内容 |
| :--- | :--- | :--- | :--- | :--- |
| I-1 | 調査 | S-1: `min_market_cap` のテーマ扱いが API とバックテストで異なる | ✅ 解決 | `_apply_filter` にテーマ免除を実装し統一。挙動テスト `test_min_market_cap_exempts_themes` で固定。本番プリセットで items 89→92 の増分として顕在化（意図した変更） |
| I-2 | 調査 | S-2: `is_rs_ratio_rank_e21_gt_e63` のテーマ扱いが異なる | ✅ 解決 | Pandas 側（テーマも自行比較で判定）に統一。`test_special_filter_behavior` で挙動固定。ダッシュボードプリセットにテーマが表示され得るようになる（ユーザー向け報告事項） |
| I-3 | 調査 | テストと `backtest_runner.py` が `api.routers` 内部に依存しており、移動で import が壊れる | ✅ 解決 | Phase 1 で import/patch 対象を新モジュールへ機械更新（テストの検証内容は不変、19件パス）。`backtest_runner` は暫定で `screener_router` 参照、Phase 2 でレジストリ参照へ |
| I-4 | Phase 1 | 既存テスト `test_fx_rates_refactoring.py` が `data/stocktool_sandbox.db` を drop_all でリセットするため、全テスト実行のたびに Sandbox 検証データが消える（今回の変更とは無関係の既存挙動） | ✅ 解決 | テストを専用の使い捨てDB `data/stocktool_pytest_fx.db` に分離。開発用 Sandbox はテスト実行の影響を受けなくなった |
| I-5 | Phase 2 | 作業ツリーのファイルが CRLF 改行（git autocrlf 環境）で、文字列置換スクリプトが不一致を起こした | ✅ 解決 | 編集時に LF へ正規化して書き戻す運用に統一（リポジトリは .gitattributes で LF 正規化されるため実害なし）。編集した Python ファイルは LF で統一された |
| I-6 | Phase 2 | 既存バグ: expression パーサが `true`/`false` リテラル非対応で、`trend_breakdown` プリセットの式全体が黙って無視され無フィルタ表示になっていた（API・バックテスト両側の既存問題） | ✅ 解決 | TDD で API パーサに true/false 対応を追加（true=1.0, false=0.0）。バックテスト側も pandas.query 実行前に True/False へ正規化し同一の式を受け付けるよう統一。本番データで trend_breakdown が正しく8件抽出されることを確認 |
| I-7 | Phase 3 | **インシデント**: Sandbox スモーク時に `STOCKTOOL_DB_PATH` のみ設定し user_data.db が本番のまま接続され、`heal_watchlist_ids()` が「Sandbox に存在しない ticker」の symbol_id を本番 watchlist 47件全てで NULL 化・コミットした | ✅ 解決・復旧済み | ①ticker/exchange 列は無傷のため、本番 stocktool.db に対する heal 再実行で 47件全ての symbol_id を復元（NULL=0 確認済み）。②再発防止: sandbox-workflow スキルに「`STOCKTOOL_USER_DB_PATH` 併用必須」の CAUTION を追記。以降のスモークは両DB分離で実施 |
| I-8 | Phase 3 | 既存バグ（I-7 で発覚）: watchlist の ticker が symbols から解決できない場合（上場廃止・ticker変更等）、`WatchlistItem.symbol_id: int` 必須のため **GET /api/watchlist 全体が 500** になる | ✅ 解決 | TDD で `symbol_id` を Optional 化（schemas.py + frontend/types.ts）。解決不能な項目は symbol_id=null で返り、リスト全体は 200 を維持。回帰テスト `test_api_get_watchlist_with_unresolvable_ticker_returns_200` 追加 |
