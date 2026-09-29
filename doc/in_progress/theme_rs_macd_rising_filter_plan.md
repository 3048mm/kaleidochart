# テーマ RS-MACD 加速フィルタ追加・銘柄版の判定式統一 計画書

- **ステータス**: 🚧 進行中
- **実施者**: Claude Code (Opus 5.5) オーケストレーター / 実装は implementer・test-writer に委譲予定
- **開始日**: 2026-09-30 / **完了日**: —
- **作業ブランチ**: `worktree-theme-rs-macd-rising`（予定・ワークツリー）
- **対象 issue / 関連ドキュメント**: `doc/backtest_config_spec.md` §条件一覧 / `tmp/b_strategies_summary_20260930.md` / `tmp/b6_dd_analysis.py`

## 1. 背景と目的

B6（`B6_rs_macd_and_theme`）の型1最大DD −105.1% の要因分析（`tmp/b6_dd_analysis.py`）で、
DD は 2022年1〜6月に集中し（35取引・勝率11%・31件が損切り）、エントリーが週1〜3件ペースで途切れず続く
「止まれない負け」だと判明した。21EMA 乖離との相関は無かった（中央値 DD区間 21.9 / 区間外 22.2）。

B6 は「銘柄の RS-MACD が加速している」ことが戦略の定義だが、テーマ側は RS trend 条件のみで
**テーマの RS-MACD 加速** を判定する手段が無い。テーマ水準条件 `min_theme_rs_macd_hist_21` は
動的解決で既に使えるが、加速（前日比の上昇）は前日値との比較が必要なため未実装。

テーマ水準条件だけの実験結果（型1・200 trial、現行 B6 = score 2.37 / LCB +0.76 / DD −105.1 / 298取引）:

| 版 | 内容 | score | LCB | 最大DD | 取引 |
|---|---|---|---|---|---|
| M1 | 現行 + テーマ MACD hist 下限 | 2.02 | +0.50 | −87.8 | 314 |
| M2 | テーマ trend 2条件を外し テーマ MACD hist 下限のみ | 1.68 | +0.38 | −66.9 | 394 |
| M3 | テーマ trend 2条件 → テーマ ratio rank e21>e63 + MACD 下限 | （実行中） | | | |

**成功条件**: `is_theme_rs_macd_hist_rising_21 = true` を TOML に書くと、バックテストとスクリーナー API の双方で
「テーマ行の RS-MACD hist が前日より上昇しているテーマ」と、その構成銘柄だけが通過する。
あわせて銘柄版 `is_rs_macd_hist_rising_21` も「前日より上昇」のみに揃え、水準（正負）は
`min_rs_macd_hist_21` / `min_theme_rs_macd_hist_21` で独立に指定・最適化できるようにする。
その後の B6 派生最適化（本計画の範囲外）が実行可能になる。

## 2. スコープと設計判断

### 2.1 変更すること

- 純関数 `filter_theme_rs_macd_hist_rising_21(merged, df_theme_constituents)` の追加
- 銘柄版 `filter_rs_macd_hist_rising_21` の判定式から「hist > 0」を外す（**意味の変更**）
- レジストリ `is_theme_rs_macd_hist_rising_21`（kind=special）の登録
- バックテスト・API 双方のディスパッチへの追加
- テスト追加、`doc/backtest_config_spec.md` への1行追記

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 |
|---|---|
| 判定式 | 銘柄版・テーマ版とも `hist > prev_hist` のみ（hist > 0 は含めない）。旧銘柄版は「hist > 0 かつ上昇」で、B6 の `min_rs_macd_hist_21`（≥0）と条件が重複し下限が無効化されていた。水準は min_ 側、加速は _rising 側と役割を分離する（2026-09-30 ユーザー合意） |
| 前日値なし時 | **通過させない**（上昇を確認できない＝不通過）。prev が NaN の行は比較で自然に False。prev 列自体が無い場合（期間初日など）は全行 False |
| B6 への影響 | 銘柄版を使うのは B6 のみ。B6 は min_rs_macd_hist_21=0.0（≥0）を併用しているため、差は hist==0 ちょうどの日と期間初日だけ。実装後に現行 best を再採点して score 2.37 付近で不変を確認する |
| 前日値の供給 | 既存の `prev_requires` 機構を使う。前日マージは全 symbol_id（テーマ行を含む）対象のため新規経路は不要 |
| テーマ→構成銘柄の展開 | 既存 `_theme_comparison_mask` と同じ流儀で、新ヘルパは作らず同形のマスクを書く（比較が「列A > 列B」でないため共通関数には載せない） |
| 名称 | `is_theme_rs_macd_hist_rising_21`（依頼時の綴り `is_themars_…` は既存命名規則に合わせて補正） |
| 指標計算・DB | 変更しない（`rs_macd_hist_21` はテーマ行にも既存）。データ種別 D（データ変更なし） |
| B6 の設定変更 | 本計画では行わない（実装後に tmp で最適化比較し、採否はユーザー判断） |

## 3. 変更内容

0. `backend/indicators/screener_filters.py` — `filter_rs_macd_hist_rising_21` を `hist > prev_hist` のみに変更。
   prev 列が無ければ全行 False、`rs_macd_hist_21` 列が無ければ全通過（既存 special の流儀）。docstring 更新。
1. `backend/indicators/screener_filters.py` — `filter_theme_rs_macd_hist_rising_21` 追加（判定は 0 と同一の式をテーマ行に適用）。
   テーマ行（`category == 'テーマ'`）で条件を満たす theme_id を選び、テーマ行自身と構成銘柄（`category == '個別'`）を通す。
   `rs_macd_hist_21` 列が無ければ全通過（既存 special と同じ）。
2. `backend/indicators/screener_registry.py` — `EXPLICIT_SPECS['is_theme_rs_macd_hist_rising_21']`：
   `requires=('rs_macd_hist_21',)`, `prev_requires=('rs_macd_hist_21',)`。銘柄版と併用しても前日マージは1回（required.prev は集合）。
3. `backend/backtest/backtest_screener.py` — import 2箇所（try/except の両側）と、銘柄版加速フィルタ直後にディスパッチ追加。
4. `backend/api/screener_cross_section.py` — import と `elif key == "is_theme_rs_macd_hist_rising_21"` 追加。
5. `backend/backtest/strategy_normalizer.py` — 旧名エイリアスは不要（新規キー）。変更なしを確認するのみ。
6. `doc/backtest_config_spec.md` — 条件一覧に1行追加。

影響範囲: 新キーを指定しない既存戦略の挙動は不変。

## 4. ユーザー確認事項

- 名称: `is_theme_rs_macd_hist_rising_21`（命名規則に合わせる）→ **合意済み**
- 判定式: 銘柄版・テーマ版とも「前日より上昇」のみ、hist > 0 を外す → **合意済み**（銘柄版の意味変更を含む）
- 前日値なし: 通過させない → **合意済み**

## 5. 実装順序と進捗チェックリスト

- [ ] G1: 本計画書のユーザー合意
- [ ] ワークツリー作成・計画書コミット
- [ ] test-writer: 失敗するテスト追加（red）
  - `backend/tests/indicators/test_screener_filters.py`: 銘柄版の既存テスト（hist>0 前提の箇所 L399-413 付近）を新仕様に修正。
    テーマ版: 加速/非加速/hist負でも上昇なら通過/prev NaN は不通過/prev 列なしは全不通過/テーマ非所属
  - `backend/tests/backtest/test_backtest_screener_refactoring.py`: apply_filters 経由（前日マージ込み）
  - `backend/tests/api/test_screener_special_filter_behavior.py`: テーマ版の期待集合を追加、銘柄版の期待集合 `{1, 3, 100}` を新仕様で見直し
- [ ] implementer: §3 の 1〜4 を実装（green）
- [ ] 全件テスト（`pytest backend/tests/`）
- [ ] `doc/backtest_config_spec.md` 追記
- [ ] G2 検収（/accept）→ G3 ブランチレビュー（/code-review）
- [ ] G4 merge（ユーザー）
- [ ] B6 現行 best の再採点（銘柄版の意味変更で不変を確認）
- [ ] （範囲外・後続）B6 派生の型1最適化: 現行 B6 + テーマ加速 / M系との比較

### 作業中メモ

なし

## 6. 検証プラン / 結果

- 単体・結合テスト全件パス: `$env:PYTHONPATH="backend"; .\venv\Scripts\python.exe -m pytest backend/tests/ -v`
- 実データでの疎通: tmp スクリプトで B6 + `is_theme_rs_macd_hist_rising_21=true` の FixedTrial を1本実行し、
  取引数が現行 B6（298）以下に減ることと、例外が出ないことを確認。

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか

- **この結論が誤りだとしたら観測されるはず**: テーマ行に前日値が付いていなければ、全テーマが「hist > 0」だけで判定され、
  `min_theme_rs_macd_hist_21 = 0` と同じ取引集合になる（新仕様では前日値なし＝不通過のため、逆に取引が0件近くまで激減する形でも現れる）。
- **独立経路での確認**: 実データの任意の1日で、テーマ行の `rs_macd_hist_21` を当日・前日で Parquet から直接読み、
  手計算で通過テーマ集合を作ってフィルタ結果と突き合わせる（実装後に実施）。
  加えて、`min_theme_rs_macd_hist_21 = 0` 単独時と取引数が異なること（加速で絞れていること）を確認する。

### 6.2 転記の完全性

- **転記元**: 会話上の実装案（5項目: filters / registry / 振り分け2箇所 / テスト / doc）＋合意事項3件（名称・判定式統一・前日値なし不通過） / **元の件数**: 8 / **本計画書の件数**: 8（§3 の 0〜4, 6・テスト・§4 の3件） / **差分の説明**: normalizer 確認を追加（変更なし想定）

### 6.3 レビュー記録（G3）

| 周 | 指摘 | 重大度 | 仕分け | 対応・理由 |
| :--- | :--- | :--- | :--- | :--- |

## 7. 途中発生した課題

## 8. スコープ外・残作業

- B6 への採用判断と最適化（実装後に tmp で実施）
- B6 の `min_rs_macd_hist_21` 探索範囲を負側へ拡張するか（例 −0.3〜0.5）は B6 比較実験時に判断
- 他のテーマ系「前日比」条件（RRG 等のテーマ版）は今回作らない
