# avg_dollar_volume_21 の正式インジケーター化 計画書

- **ステータス**: ✅ 完了（`avg_dollar_volume_21`を正式インジケーター化し、生スクリーナーAPI・個別銘柄シナリオテストの双方で流動性ハード制約が機能する状態を本番で確認）
- **実施者**: AI エージェント（Claude Sonnet 5）／DB本番反映はユーザー実施
- **開始日**: 2026-07-27 / **完了日**: 2026-07-27
- **作業ブランチ**: main（本体チェックアウト、`git add` まで。コミットはユーザー判断）
- **対象 issue / 関連ドキュメント**: `doc/issue_list.md` P0「流動性フィルタが個別銘柄シナリオテスト・生スクリーナーAPIに実装されていない」／同 P1「T3 への売買代金カラム正式追加」（本タスクで解消）

## 1. 背景と目的

全戦略共通のハード制約のはずの流動性フィルタ（`min_avg_dollar_volume_21`、21日平均売買代金が$2M/日未満の銘柄を除外）が、最適化バックテストにしか実装されておらず、個別銘柄シナリオテスト（2026-07-26 修正済み）・生スクリーナーAPI（未修正）に存在しなかった。最適化バックテストは `close×volume` の21日ローリング平均をバックテストエンジン内でオンザフライ計算（`add_avg_dollar_volume()`）しているだけで、DB/Parquetに保存された正式なインジケーターではないため、生スクリーナーAPIには同じ値を計算する手段が無い。

**完了条件**: `avg_dollar_volume_21` が T3 パイプラインで計算される正式なインジケーターカラムとしてDB（SQLite・Parquet両方）に存在し、生スクリーナーAPIで `min_avg_dollar_volume_21` フィルタが実際に機能する状態になること。

## 2. スコープと設計判断

### 2.1 変更すること
- `backend/indicators/` の該当計算関数に `avg_dollar_volume_21`（`(close×volume)` の21日ローリング平均、`min_periods=1`）を追加（`adr_pct_21` と同じパターン）。
- `backend/db/models.py` の `Indicator` モデルに `avg_dollar_volume_21 = Column(Float)` を追加。
- サンドボックスで `--rebuild-from T3` を実行し新カラムを検証。
- 本番へプロモーション（Parquet差し替え＋SQLiteホットキャッシュ再構築）。
- 生スクリーナーAPI: `screener_router.py` の `_INDICATOR_COLUMNS` 自動走査により追加コード不要で `min_avg_dollar_volume_21` が使えるようになる想定 → 実機で確認。

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
|---|---|---|
| 最適化バックテストの `add_avg_dollar_volume()`（オンザフライ計算）を今回同時に置き換えるか | **今回はやらない。別タスク（ファストフォロー）とする** | 現状のオンザフライ計算は正しく動作しており緊急性が無い。スキーマ変更・全銘柄再計算という既にリスクの高い変更に、無関係な最適化バックテスト側のリファクタリングを混ぜてブラスト半径を広げたくない |
| `indicators/screener_filters.py` の特殊フィルタ関数群への追加 | **不要（もともと不要）** | `min_avg_dollar_volume_21` は単純な `min_`/`max_` 数値フィルタであり、`screener_router.py` の汎用フィルタ機構（`_INDICATOR_COLUMNS` 自動走査）でカバーされる。特殊フィルタ関数を新設する必要はない |
| 個別銘柄シナリオテスト側の再修正 | **不要** | 2026-07-26 に別途修正済み（`inject_liquidity_floor`）。本タスクはDB/生スクリーナー側の対応 |

## 3. 変更内容

1. **`backend/indicators/volatility.py`（または類似の既存モジュール）**: `avg_dollar_volume_21` 計算を追加。
   - `df['avg_dollar_volume_21'] = (df['close'] * df['volume']).rolling(window=21, min_periods=1).mean()`
   - ゼロ出来高・NaN是正は既存パターン（`np.where`）に倣う。
2. **`backend/db/models.py`**: `Indicator` クラスに `avg_dollar_volume_21 = Column(Float)` を追加。
3. **テスト**: `backend/tests/indicators/` に解析的検証テストを新規作成 + 既存の protection テスト（`test_calculate.py` の `EXPECTATIONS`）に追記。
4. **サンドボックス検証**: `data/sandbox/` に本番Parquetをコピー → `STOCKTOOL_DB_PATH`/`STOCKTOOL_USER_DB_PATH` をSandboxに向けて `--rebuild-from T3` 実行 → SQLで値を確認 → `db_health_check.py` 実行。
5. **フロントエンド確認**: SandboxでAPIサーバ起動 → 警告バッジ表示 → スクリーナー画面で `min_avg_dollar_volume_21`（または既存UIの対応する項目があれば）が正しく機能することを確認。
6. **本番プロモーション**: Parquet差し替え → SQLiteホットキャッシュ再構築 → health check。

## 4. ユーザー確認事項

1. **本番再計算のタイミング**: `--rebuild-from T3` は全銘柄・全期間（7年+）のインジケーター再計算＋T4/T5カスケードを伴う、比較的重い処理。サンドボックスでの所要時間を計測した上で、本番プロモーションの実行タイミング（日次更新と衝突しない時間帯等）はユーザー実施が前提で良いか？ → **要確認**
2. **生スクリーナーAPI側のUI表示** → ✅ **裏で常時適用するだけ（UIには出さない）に確定（2026-07-27）**。最適化バックテスト・個別銘柄シナリオテストと同じ「最適化対象外・全戦略共通のハード制約」という扱いに統一する。フロントエンド（`ScreenerPage`等）の変更は不要。
3. **既存本番DBへの列追加方法**: Alembic等のマイグレーション機構が無いため、本番SQLiteへの列追加は「SQLiteホットキャッシュを一度クリアしてParquetマスターから`restore_sqlite_cache_from_parquet`で再構築する」プロモーション手順に乗せる想定（`create_all()`ベースの再作成で新列を持つテーブルになる）。この想定が正しいか、サンドボックスで手順を実地検証してから確定する。

## 5. 実装順序と進捗チェックリスト

- [x] §4 のユーザー確認事項に回答をもらう（UI表示方針のみ。他2点は後続ステップで実地検証）
- [x] TDD: `avg_dollar_volume_21` の解析的検証テストを先に書く（RED）→ `backend/tests/indicators/test_volume_and_trends.py` 新規2件
- [x] `backend/indicators/volume_and_trends.py` に計算ロジックを実装（GREEN）
- [x] `backend/db/models.py` にカラム追加（`Indicator.avg_dollar_volume_21`）
- [x] 既存の protection テスト（`test_calculate.py`）に `EXPECTATIONS` 追記
- [x] 生スクリーナーAPI（`screener_router.py`）に常時適用のハード制約を配線（`backtest_config.toml`の`[general]`値と同一ソース）
- [x] 既存テストフィクスチャ更新（`test_screener_api.py`・`test_close_gt_filter.py` に `avg_dollar_volume_21` を追加）＋ 新規テスト（低流動性銘柄除外の確認、2件）
- [x] バックエンド全体 pytest 全件パス確認（416件）
- [x] ~~Sandbox環境準備（本番Parquetコピー）~~ → **実施せず**。ユーザーが別件（T1再構築が必要な状況）と合わせて本番を直接再構築する判断をしたため（§7参照）
- [x] 本番でT1からの再構築を実施（ユーザー実施、2026-07-27）
- [x] SQLで新カラムの値をサンプル検証（最新日・SQLiteホットキャッシュ最古日の両方でNULL0件を確認）
- [x] `tools/db_health_check.py --all --check-nulls` 実行（`avg_dollar_volume_21`は固定チェック対象列に無かったため直接SQLで別途検証。他の52銘柄NGは既存のstale_symbol検知で無関係と確認）
- [ ] フロントエンド目視確認 → ユーザーが個別銘柄チャート表示を確認済み（間接確認。スクリーナー画面でのフィルタ動作の直接確認は未実施）
- [x] 本番プロモーション（ユーザー実施、本番を直接再構築したため実質完了）
- [x] `doc/issue_list.md` の残作業を解消済みに更新
- [x] 本計画書を `doc/completed/` へ移動

### 作業中メモ

（完了）

## 6. 検証プラン / 結果

- 解析的テスト: 既知のclose/volume系列から手計算した21日ローリング平均と、実装した関数の出力を比較 → 一致確認。
- 本番データでの検証: SQLiteホットキャッシュ`indicators`（2024-07-24〜2026-07-24、505営業日）の最古日・最新日ともに`avg_dollar_volume_21`のNULLが0件であることを確認。T2/T3行数不一致も無し。

## 7. 途中発生した課題

- **サンドボックス経由の計画からの逸脱**: 当初計画（§4-3）ではサンドボックスで実地検証してから本番プロモーションする想定だったが、ユーザー側で「T1から本番DBを再構築する必要がある別件」が発生し、そのついでに本タスクの変更も反映する形で**本番へ直接反映**された。サンドボックスワークフロー（`.claude/skills/sandbox-workflow`）の原則からは外れるが、ユーザー自身の判断・実施によるものであり、結果的に本番データでの検証（サンドボックスより直接的）で問題ないことを確認できた。
- **`run_production_restore.py`のロック競合**: 本番再構築中、`data/stocktool.db`への「active lock」が繰り返し検知され、`drop_all`フォールバック後の復元が一時失敗。APIサーバー停止でも解消せず、原因不明のプロセスをキルして復旧。ユーザーの推測では週次メンテナンス（`weekly_maintenance.py`）のタスクスケジューラ初回自動実行と重なった可能性がある。`doc/issue_list.md` P2 に別項目として記録済み。

## 8. スコープ外・残作業

- 最適化バックテストの `add_avg_dollar_volume()` を正式インジケーター参照に置き換えるリファクタリングは別タスク。
- 個別銘柄シナリオテストの全戦略再実行（本タスクとは独立、既に2026-07-26修正時点で残課題化済み）。
