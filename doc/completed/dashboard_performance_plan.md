# Dashboard 初回表示の高速化 計画書

- **実施者 AI**: Claude Code (claude-fable-5)
- **開始日**: 2026-07-04
- **完了日**: 2026-07-04
- **対象 issue**: P2 ① dashboard 初回表示の高速化（doc/issue_list.md）
- **ステータス**: ✅ 完了（4.66s/9,685クエリ → 0.30s/16クエリ、レスポンス完全一致）

## 1. 背景と実測ベースライン

`GET /api/dashboard` の初回表示が体感で重い。本番 DB（読み取り専用接続）に対する実測:

| 項目 | 値 |
|---|---|
| レスポンスタイム | **4.66 秒** |
| 発行クエリ数 | **9,685** |
| 表示アイテム数 | 108 件（indices=25, leading=8, sectors=15, themes_top=30, themes_bottom=30） |

計測方法: `tmp/measure_dashboard.py`（`mode=ro` の読み取り専用 URI 接続 + `before_cursor_execute` によるクエリカウント + レスポンス JSON の保存）。

## 2. 原因（特定済み）

`backend/api/dashboard_router.py` の `get_dashboard`:

1. **捨てるアイテムの生成**: `Symbol.active == 1` の全銘柄（約 2,900 件、うち個別株 約 2,800 件）をループし、
   全銘柄に対して `_build_panel_item()` を実行してからカテゴリ分岐（市場/指標/セクタ/テーマ）で
   個別株分を**破棄**していた。「指標」カテゴリも panel item を作ってから捨てて
   `_build_leading_item` / `_build_etf_feature` を別途呼んでいた。
2. **per-symbol N+1**: `panel_builders.py` の `_build_panel_item` は 1 銘柄あたり
   ①sparkline（relative_ranks 30行）②価格履歴（daily_prices 22行）③indicator（1行）の
   **3 クエリ**を発行する。約 2,880 銘柄 × 3 ≒ 8,600 クエリが無駄撃ち。

## 3. 方針: Plan A → Plan B の2段階

キャッシュ導入（Plan C 案）は「パイプライン更新後に古いキャッシュが残る」整合性リスクがあるため**採用しない**（ユーザー決定 2026-07-04）。

### Plan A: 捨てアイテムの排除 ✅

- `symbols` クエリをダッシュボードで使う 4 カテゴリ（市場/指標/セクタ/テーマ）に絞る
- 「指標」カテゴリでは `_build_panel_item` を呼ばない（結果を使っていないため）
- **出力への影響ゼロ**（破棄されていたアイテムを作らなくなるだけ）

### Plan B: panel_builders の N+1 をバルクプリロードで定数クエリ化 ✅

- `panel_builders.py` に一括ロード関数を追加し、`_build_panel_item` / `_build_leading_item` に
  `preload`（`PanelPreload` dataclass）を渡せるようにした（省略時は従来通り per-symbol クエリ = 互換維持）:
  - `preload_sparklines(db, symbol_ids, target_date, period, limit=30)`
  - `preload_close_histories(db, symbol_ids, target_date, limit=22)`
  - `preload_indicators(db, symbol_ids, target_date)`
  - `build_panel_preload(db, symbol_ids, target_date)` — 上記3つをまとめて実行
- **等価性の担保**: `ROW_NUMBER() OVER (PARTITION BY symbol_id ORDER BY date DESC)` で
  per-symbol の「日付降順・LIMIT n」を再現。さらに**直近 90 日の日付窓**で読む範囲を絞り、
  窓内で limit 件に満たなかった銘柄のみ per-symbol の元クエリで取り直す
  （窓内に limit 件ある銘柄は「最新 limit 件」が窓に完全に収まるため無制限版と一致 → 厳密等価）
- 適用先: `/dashboard`（メインループ + leading）、`/group_data`（構成銘柄ループ）、
  `/theme`（構成銘柄の sparkline のみ。他の per-constituent クエリは別 issue、§7 参照）
- `_build_etf_feature`（SPY 1件のみ）は N+1 ではないため対象外。ただし `get_rank` を
  同一値で 6 回呼んでいた重複を 1 クエリ（3カラム射影）に削減

## 4. 検証方法（すべて実施済み）

1. **TDD**: `backend/tests/api/test_dashboard_router.py` + `test_panel_builders.py`（計9件、red→green で実装）
   - クエリ数が「個別」銘柄数に比例しないこと（Plan A: 修正前 58 vs 139 で red を確認）
   - クエリ数がパネルアイテム数（セクタ/テーマ数）にも比例しないこと（Plan B）
   - プリロード関数と per-symbol 実装の**結果等価性**（NULL 混在・件数不足・データなし・
     target_date より新しいデータ・日付窓より古いデータのみ、の各エッジケース含む）
2. **本番同値性**: `tmp/measure_dashboard.py` で修正前後のレスポンス JSON 完全一致を確認。
   `/group_data`（セクタ/テーマ各1件）と `/theme` は `tmp/verify_group_theme_equivalence.py`
   （preload を無効化するモンキーパッチとの A/B 比較）で完全一致を確認
3. 全テストスイート **289 passed**

## 5. 進捗チェックリスト

- [x] ベースライン再計測 + 修正前 JSON 保存（tmp/measure_dashboard.py）
- [x] Plan A: 失敗テスト作成（red）
- [x] Plan A: 実装（green）
- [x] Plan A: 本番同値性確認 + 計測記録
- [x] Plan B: プリロード関数のテスト作成(red) → 実装(green)
- [x] Plan B: /dashboard, /group_data, /theme(sparkline) への適用
- [x] Plan B: 本番同値性確認 + 計測記録
- [x] 全テストスイート green（289 passed）
- [x] issue_list.md への反映

## 6. 計測記録

| 時点 | クエリ数 | 時間 | 同値性 |
|---|---|---|---|
| 修正前（ベースライン） | 9,685 | 4.66s | — |
| Plan A 適用後 | 958 | 0.55s | ✅ JSON 完全一致 |
| Plan B 初版（日付窓なし・**不採用**） | 16 | **360s** | ✅ JSON 完全一致 |
| Plan B 最終版（日付窓 + フォールバック） | 16 | **0.30s** | ✅ JSON 完全一致 |

## 7. 途中発生した課題

### I-9: 日付下限なしの窓関数がコールドキャッシュの HDD で 360 秒（解決済み）

- **事象**: Plan B 初版（`date <= target` のみで日付下限なし）は本番でクエリ数 16 に減ったが
  360 秒かかった。`EXPLAIN QUERY PLAN` では `(symbol_id, date)` インデックスシークだが、
  窓関数が rn を数えるために**各銘柄の全履歴**（relative_ranks 586万行・daily_prices 590万行の
  テーブルから約73万行）を読み、ワイドテーブルの行取得がコールドキャッシュの SATA HDD で
  ランダム I/O になったため。診断スクリプト実行時はページキャッシュが温まっており 6 秒
  → **コールド/ウォームの差が60倍**で、初回表示の計測はコールド前提で行う必要がある。
- **対策**: 直近 90 日（`PRELOAD_DATE_MARGIN_DAYS`）の日付窓で読む範囲を絞り、
  不足銘柄のみ per-symbol フォールバック（厳密等価を維持）。0.30s に改善。
- **教訓**: 数百万行テーブルへの `ROW_NUMBER() OVER (PARTITION BY ...)` は必ず日付等で
  読む範囲を絞る。「クエリ数が減った」≠「速い」— 読み取る行数・ページ数で考える。

### 別 issue へ切り出し

- `/theme/{id}` の構成銘柄ループには sparkline 以外にも per-constituent クエリが残っている
  （価格履歴・indicator・126日チャート・ランクで銘柄あたり約6クエリ）。テーマ詳細画面は
  構成銘柄数が少なく（数件〜50件）実害が小さいため今回は対象外としたが、
  同じプリロード機構で対応可能。→ issue_list P3 に記録
