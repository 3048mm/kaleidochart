# プロジェクト・課題リスト (Project Issue List)

本ドキュメントは、プロジェクト全体の「保留事項」「今後の課題」「継続的なタスク」を一元管理するためのものです。
会話中に発生した「後回し」の指示や、中長期的な改善項目をここに集約します。

優先度は P0（最優先）〜 P3（将来）の4段階。

> [!IMPORTANT]
> **このファイルは未完了項目の索引**（2026-09-28 再構成）。
> - 1項目は**5行以内**（見出し・症状・対応案・詳細リンク）。経緯・実測値・議論は
>   `doc/issues/<slug>.md` か対応する計画書（`doc/in_progress/`）に書く。
> - **完了した項目は `doc/issue_list_archive.md` へ移す**（原文のまま・ここには残さない）。
> - コード中のコメントにある「`doc/issue_list.md` P1 参照」等の参照先は、
>   本ファイル・`doc/issues/`・`doc/issue_list_archive.md` を合わせて grep すると見つかる。

---
## P0 — 最優先（データ信頼性・ツールの根幹に直結）

（現在なし。直近のP0は全件解消・検証済み。詳細は完了済みタスクを参照）

## P1 — 高（正確性・データ保全。P0 の次）

- [ ] 🟡 **分割・統合の検証手段 — 第1・2段階完了 / 第3段階（独立ソース）は実装・既知候補の照合まで完了**（2026-09-04 発見 / 2026-09-10 一部対応 / 2026-09-11 第3段階 moomoo 実装）
  - **症状**: 分割・併合の適用結果を独立に検証する手段の整備。第1〜3段階（moomoo 照合）は実装済みだが、上流が分割記録を持たない約299件は未照合
  - **対応案**: 残る299件規模のギャップを受容するか、網羅的に再スキャンするかを決める
  - **詳細**: [doc/issues/split_verification.md](issues/split_verification.md)

- [ ] **個別銘柄シナリオテストの「複数戦略で共有資本を奪い合う」という本来の割り当てメカニズムが、`run_scenario_batch.py`の単一戦略運用により実際には一度も使われていない（2026-07-16〜08-02、当初は「目的関数の乖離」として誤認していたが2026-08-02に再整理）**
  - **誤っていた当初の理解**: 最適化バックテスト（無限資金・avg_slots正規化でCAGRを評価）と個別銘柄シナリオテスト（有限資産・固定枠`max_positions`）の実測が乖離する現象（E1/E2で顕著）を、当初「目的関数の設計がズレている」問題として捉えていた。
  - **正しい理解**: 最適化バックテストが銘柄選定の目利きだけを測る特性関数であること自体は意図した設計であり、直す対象ではない（`backend_specification.md`§6.1）。本当の欠落は、**「有限の資本を複数戦略・複数シグナルにどう配分するか」という割り当ての責務がどこにも実装されていないこと**。`scenario_scorer.py::ScenarioScorer.score_signals()`は本来、複数戦略が同時にヒットしたシグナルをスコアリングし共有資本プールの中で競わせる設計（`min_score`で足切り）だが、`run_scenario_batch.py`は1ジョブにつき常に単一戦略（`strategy_code`）だけをpresetに書き出すため、**この「複数戦略で資本を奪い合う」メカニズムが実運用では一度も使われていない**（`score`は常に1に固定）。E1/E2のような低頻度戦略が「単独の8枠ポートフォリオ」という不自然な前提で評価され枠を持て余すのは、戦略自体の欠陥ではなく評価方法（単独実行）が本来の設計を使っていない副作用という可能性が高い。
  - **実測**: 2026-07-29〜08-01の流動性フィルタ・テーマ誤売買修正後に再最適化・全戦略再実行した結果、E1/E2は汚染除去後もCAGR2%台・トレード数最少（4年強で45〜52件）という低頻度特性が確定（勝率50%超・DDも1.8〜2.7%と極小のため、リスク当たりでは必ずしも劣らない）。
  - **対応案（未着手・ユーザー判断待ち）**: ①`run_scenario_batch.py`/`scenario_batch_jobs.toml`が複数`strategy_code`を1ジョブにまとめて渡せるようにし、複数戦略を同一資本プールで競わせる本来の設計を実際に検証する、②それでも枠が余る場合にのみ「検出頻度に応じた`max_positions`の動的化」等の追加対応を検討する（Little's Lawベースの案は「戦略を跨いで購入銘柄を追跡できない」等の理由で以前一度却下済み — ①を先に試す方が設計の手戻りが少ない）。

- [ ] **未採用インジケーターの検証（2026-07-06 棚卸し / 2026-08-04 に区分Aの本命が解消）**
  - **症状**: フィルタ関数はあるが戦略で未採用の指標が残っている（区分A残り3種・区分B 5項目・区分C はランクの配管が前提）
  - **対応案**: 次は区分B を戦略 TOML で試す
  - **詳細**: [doc/issues/unused_indicators.md](issues/unused_indicators.md)

- [ ] 🟠 **`exit_type = "vxv_vix_ratio"` は最適化経路では黙って別物になる（2026-08-29 発見）**
  - **症状**: `exit_type = "vxv_vix_ratio"` を型1で回すと VXV/VIX 判定が一度も発火しない（警告・エラーなし）
  - **対応案**: `vxv_vix_series` の構築を両経路で共有する。最低限、系列が空なら落とすガードを入れる
  - **詳細**: [doc/issues/vxv_vix_exit_in_optimization.md](issues/vxv_vix_exit_in_optimization.md)

- [ ] 🟠 **`deploy_after_merge.py` は新規カラム追加時に730日ぶんしか埋めない（2026-08-29 登録 / 事象は 2026-08-26 に遭遇）**
  - **症状**: `deploy_after_merge.ps1 -RebuildFrom T3` はホット期間730日しか再計算せず、新規カラムの過去分が Parquet に埋まらない
  - **対応案**: `deploy_after_merge.py` に全期間モードを追加する。最低限、新規カラムを検出したら警告して止める
  - **詳細**: [doc/issues/deploy_after_merge_full_history.md](issues/deploy_after_merge_full_history.md)

- [ ] 🟠 **`output/scenario/` の既存結果は税の配線が入る前のもの（2026-08-29 発見）**
  - **症状**: `output/scenario/` の既存 750 run はすべて税の配線前の結果（`run_params` に `consider_tax` が無い）
  - **対応案**: 型3 バッチを回し直す（約4時間・ユーザー実行）。並列 MC への税率伝播の検証も兼ねる
  - **詳細**: [doc/issues/scenario_results_pre_tax.md](issues/scenario_results_pre_tax.md)

- [ ] 🔵 **`consider_tax_optimization` は現在の目的関数に一切影響しない（2026-08-29 発見）**
  - **症状**: 税は `strat_mult` にしか効かず、型1の目的関数が使う指標は税前のまま
  - **対応案**: 目的関数を税後の値にするか、「スコアには効かない」と仕様に明記するかを判断する
  - **詳細**: [doc/issues/tax_optimization_no_effect.md](issues/tax_optimization_no_effect.md)

- [ ] 🟠 **週次の T3 修復が「行の欠落」しか拾わず、しかも SQLite 基点で再計算する**（2026-09-20 発見 / 2026-09-28 書き直し）
  - **症状**: 値が誤っている・NULL の行は直らず、SQLite 窓の先頭付近を修復すると遡り不足の値を書き込む（R19 と同一）。手動の価格補正後の T3 再計算は問題なし
  - **対応案**: 修復入力を Parquet 基点にし、誤った値の検出手段を持つ。T3 フォールバック（案 C）・R19 と1計画にまとめる
  - **詳細**: [doc/issues/weekly_t3_repair_sqlite_base.md](issues/weekly_t3_repair_sqlite_base.md)

## P2 — 中（体感改善・保守性・運用安全性）

- [ ] 🟡 **ダッシュボードが `market_trend_score`/`distribution_days` の NULL を 0 に潰して表示する（2026-09-16 コードレビューで発見）**
  - **症状**: ダッシュボードが `market_trend_score` / `distribution_days` の NULL を 0 として表示する
  - **対応案**: NULL の扱いを層をまたいで設計してから、該当フィールドを Optional 化する
  - **詳細**: [doc/issues/dashboard_null_as_zero.md](issues/dashboard_null_as_zero.md)

- [ ] 🔵 **週次メンテに `scan_split_consistency.py` を組み込む（根本対応・2026-09-11 分離起票）**
  - **症状**: `scan_split_consistency.py` が週次メンテに入っていない（元は「分割記録の鮮度」issue の対応案の片方）
  - **対応案**: レート制限・実行時間・失敗時の扱いを決めて週次に組み込む
  - **詳細**: [doc/issues/weekly_split_consistency_scan.md](issues/weekly_split_consistency_scan.md)

- [ ] 🟡 **エージェント実行中の「見返し」がワークフローに無い — 二次検証の段の新設（2026-09-07 起票）**
  - **症状**: エージェントの一次成果物を検算する二次の段がワークフローに無く、人のレビューは merge 時の1回だけ
  - **対応案**: 二次検証の段をワークフローに新設する
  - **詳細**: [doc/issues/agent_second_check_stage.md](issues/agent_second_check_stage.md)

- [ ] **年次 `avg_pnl_pct`（相加平均）をフロントで `geo_pnl_pct` に切り替えるか判断する**（2026-08-25 起票）
  - 2026-08-25 に `scenario_reporter` / `backtest_router` へ `geo_pnl_pct`（年次の幾何平均）を
    追加したが、**フロントの `Avg P&L%` 列は従来どおり `avg_pnl_pct`（相加平均）を表示している**
    （表示互換のため既存キーを残した）。
  - 切り替えるか、両方並べるかを決める。対象は
    `frontend/src/pages/ScenarioDetailView.tsx` の年度別テーブル。

- [ ] **フロントエンドのシナリオ一覧が `SCENARIO_STRATEGIES` のハードコードで、組み合わせジョブが表示されない**（2026-08-25 発見）
  - **症状**: `backtest_router.py` の `SCENARIO_STRATEGIES` が固定リストで、組み合わせジョブが一覧に出ない
  - **対応案**: `output/scenario/` を走査して列挙する
  - **詳細**: [doc/issues/scenario_list_hardcoded.md](issues/scenario_list_hardcoded.md)

- [ ] **スクリーナー戦略の「退役」運用を定める（単純削除にしない）**（2026-08-25 起票）
  - **症状**: 型3 CAGR が0以下の戦略（B4 / D / G1 / G3）の扱い。単純削除はしない（ユーザー判断）
  - **対応案**: 退役の判断基準と、機能しなかった理由を残す運用を決める
  - **詳細**: [doc/issues/strategy_retirement.md](issues/strategy_retirement.md)

- [ ] **Optuna の storage を並行書き込み可能なバックエンドへ移し、最適化の並列実行（`n_jobs>1`）を解禁する**（2026-08-18 起票）
  - **症状**: 最適化が `n_jobs=1` の完全逐次で、全16戦略×200トライアルに約12時間かかる
  - **対応案**: `JournalStorage` へ移行し、`n_jobs>1` を実測で検証する
  - **詳細**: [doc/issues/optuna_parallel_storage.md](issues/optuna_parallel_storage.md)

- [ ] 🟡 **`/api/system/info` の `is_production` 判定がワークツリーSandboxで機能しない（2026-09-20 発見）**
  - **事象**: `backend/api/routers.py:39` の `is_production = db_name == "stocktool.db"` は**ファイル名のみ**で判定しており、パスが `data/sandbox/` 配下かどうかを見ていない。`tools/provision_worktree_data.py --mode write`(2026-09-01導入)はファイル名を変えずディレクトリだけ`<worktree>/data/sandbox/`に変える方式のため、ワークツリーSandboxに対して `is_production: true` を返してしまう
  - **想定される影響**: `sandbox-workflow` スキルが検証手順として挙げている「フロントエンドのオレンジ色警告バッジ表示」が、同じ判定ロジックに依存しているなら**ワークツリーSandboxでは表示されない**可能性がある（未検証）。誤って本番だと思い込むリスクは低い（DBパス自体はSandboxを正しく指しているため実データは安全）が、視覚的な確認手順が機能しない
  - **発見の経緯**: `doc/completed/market_breadth_indicators_plan.md`(S5FI/S5TH取り込み)のSandbox検証中、`/api/system/info` が `is_production: true` を返したため気づいた。視覚的確認はダッシュボードAPIの応答内容で代替した
  - **対応案**（未着手）: `db_path` が `data/sandbox/` または `.claude/worktrees/` 配下かどうかも判定に含める。あるいは `paths.py` 側に「現在Sandboxを掴んでいるか」を返す既存ヘルパーがあれば流用する

- [ ] 🟡 **`tools/deploy_after_merge.ps1` が新規銘柄の追加を伴う種別Bの変更を想定していない（2026-09-20 発見）**
  - **事象**: `backend/scripts/deploy_after_merge.py` は `update_pipeline.py --skip-sync` で実行する設計（`-RebuildFrom All` でも `--skip-sync` は付いたまま）。`--skip-sync` は T1(`universe.db`→`symbols`同期)を常にスキップするため、**merge前に `universe.db` へ新規銘柄を登録していても、このスクリプトは本番へ反映しない**
  - **想定される設計意図**: 「既存銘柄の指標ロジック変更」の昇格に特化し、`symbols`/`theme_constituents`はワークスペースのParquetスナップショットのまま保持することで、昇格対象を純粋な計算ロジック差分に絞る意図と推測される（未確認）
  - **実害**: 新規銘柄の追加を伴う種別Bの変更のたびに、`deploy_after_merge.ps1`とは別に手動オンボード作業（対象銘柄限定でT1〜T5を直接実行 → Parquetアーカイブ → SQLiteパージ）が必要になる。AIGオンボード時（日次パイプラインの自律チェックが新規銘柄を想定していない問題）と同型の設計ギャップ
  - **発見の経緯**: `doc/completed/market_breadth_indicators_plan.md`(S5FI/S5TH取り込み)の本番昇格時。`-DryRun`実行後に気づき、新規銘柄反映は手動オンボードで代替した（同計画書§7に手順の詳細）
  - **対応案**（未着手）: `deploy_after_merge.py`に「新規銘柄も同期する」オプション（`--skip-sync`を外す、または新規銘柄だけT1を通す）を追加する。設計判断が要るため`doc/in_progress/deploy_after_merge_plan.md`側で検討

- [ ] 🟠 **`zone_break` の必要履歴が有界でない — ホットウィンドウ計算では約1%の銘柄が不正確**（2026-09-22 実測）
  - **症状**: `zone_break` 系列はトレンドレッグの長さに上限が無く、ホットウィンドウ計算では約1%の銘柄が不正確
  - **対応案**: 状態を列として保存するなどの方式を検討する
  - **詳細**: [doc/issues/zone_break_unbounded_history.md](issues/zone_break_unbounded_history.md)

- [ ] 🟡 **ウォームアップ検査（`--check-warmup-nulls`）の閾値が未確定 — 検出された40銘柄の調査も要る**（2026-09-23 起票）
  - **症状**: `--check-warmup-nulls` の除外規則と閾値が未確定で、検出された40銘柄も未調査
  - **対応案**: 除外規則と閾値を直し、40銘柄を調べ、週次に入れるか判断する
  - **詳細**: [doc/issues/warmup_null_check_threshold.md](issues/warmup_null_check_threshold.md)

- [ ] 🟡 **仮想テーマ指数のT3全期間再計算で、SPY参照データの供給不足による警告が大量発生する（2026-09-24 発見・未調査）**
  - **症状**: 仮想テーマ指数の T3 全期間再計算で、SPY 参照データの不足による警告が大量に出る（未調査）
  - **対応案**: ホットキャッシュ経路と仮想テーマ合成経路の SPY 参照範囲を揃える
  - **詳細**: [doc/issues/theme_index_spy_gap_warnings.md](issues/theme_index_spy_gap_warnings.md)

- [ ] 🟡 **T3 のフォールバック（全期間計算）が SQLite の保持本数（約504本）だけで計算され、Parquet の正しい値を上書きする（案 C・2026-09-26 起票）**
  - **症状**: T3 のフォールバック（全期間計算）が SQLite の約504本だけで計算され、長期指標が NULL のまま残る・EMA 系がずれる
  - **対応案**: フォールバックを Parquet 基点にする（代替: 判別不能の列だけ NaN のまま増分計算を続ける）
  - **詳細**: [doc/issues/t3_fallback_parquet_base.md](issues/t3_fallback_parquet_base.md)

- [ ] **`min_periods` 統一後に残った既存ガード2件の要否を再検証する（2026-09-24 起票。旧 issue①の残作業）**
  - **症状**: `min_periods` 統一後に残った既存ガード2件（`RS_DOT_WARMUP_BARS` など）が要るか未検証
  - **対応案**: sandbox で撤去した場合と現状維持を比べる
  - **詳細**: [doc/issues/min_periods_leftover_guards.md](issues/min_periods_leftover_guards.md)

- [ ] **`bars_available`（実遡り本数）を診断用の列として追加する（2026-09-24 起票。旧 issue①の残作業）**
  - **症状**: 「この銘柄は何本遡れるか」を毎回スクリプトで計算し直している
  - **対応案**: T3 に `bars_available` 列を診断用に追加する
  - **詳細**: [doc/issues/bars_available_column.md](issues/bars_available_column.md)

- [ ] **`min_periods_warmup` の G3 レビューで切り出した項目（2026-09-24 起票・いずれも正しさへの実害なし）**
  - **症状**: `min_periods_warmup` の G3 で切り出した R4（RrgChart の NULL を0表示）・R19（T3 自己修復が SQLite 基点）ほか
  - **対応案**: 個別に対処する（R19 は T3 フォールバックの Parquet 化と同じ計画で扱う）
  - **詳細**: [doc/issues/min_periods_g3_leftovers.md](issues/min_periods_g3_leftovers.md)

## P3 — 低（将来フェーズ・プロセス系）

- [ ] **moomoo API 知見の活用アイデア（2026-09-12 起票、未検証・要判断）**
  - **症状**: moomoo API の知見を他の用途に使うアイデア集（未検証・要判断）
  - **対応案**: 個別に要否を判断する
  - **詳細**: [doc/issues/moomoo_usage_ideas.md](issues/moomoo_usage_ideas.md)

- [ ] **ワークフロー変更の効果を測る手段が無い（2026-09-07 起票）**
  - **症状**: ルールを足す／削る判断をしているが、効いたかを測る指標が無い
  - **対応案**: ワークフロー変更の効果を測る手段を用意する
  - **詳細**: [doc/issues/workflow_change_measurement.md](issues/workflow_change_measurement.md)

- [ ] **戦略の「評価記録」の置き場がワークフローに無い — 戦略カルテ形式を採るか見極める**（2026-09-05 起票）
  - **症状**: 戦略の評価記録の置き場が無く、計画書の §6 / §7 に埋もれる
  - **対応案**: 戦略カルテ形式を採るか見極める（まだタスクにしない: ユーザー判断 2026-09-05）
  - **詳細**: [doc/issues/strategy_evaluation_record.md](issues/strategy_evaluation_record.md)

- [ ] **個別銘柄シナリオテストに ATR 連動指値エントリーの約定モデルを追加**（2026-07-05 バックテスト設計レビューから）
  - 「シグナル翌日に `基準価格 − k×ATR` の指値を置き、Low が指値以下なら約定」を再現する。約定ルールは標準規約に従う: 寄付が指値以下なら**寄付価格**で約定、そうでなければ Low ≤ 指値で指値約定。
  - 指値方式は約定率が 100% でなくなり逆選択（弱い個体だけ刺さる）が起きるため、**未約定シグナルのその後のリターン（機会損失）を併記**して指値の深さ k を評価できるようにする。
  - 押し目系（D 系）に特に適合。ブレイクアウト系は寄付成行/買いストップとの比較も検討。

- [ ] **ポートフォリオ機能（将来フェーズ）**
  - [ ] **moomoo 証券 API 連携**: `portfolios.source = 'moomoo_api'` のポートフォリオで保有銘柄を OpenD (Python SDK / WebSocket) 経由で自動同期。API Doc: https://openapi.moomoo.com/moomoo-api-doc/
  - [ ] **チャート画面へのポジション情報統合**: 個別チャート画面 (`ChartPage`) に購入価格（青）、損切ライン（赤）、利確ライン（緑）の水平ラインを描画し、テクニカル分析と保有管理を一体化。
  - [ ] **税金の概算計算**: 売却時のキャピタルゲイン税（日本: 約20.315%）の概算表示。取引履歴画面で税引後損益も表示。

- [ ] **UI改善**
  - [ ] ローカライズ

- [ ] **プロセス定着（`.claude/skills/` と CLAUDE.md で実質カバー済み。運用しながら定着）**
  - [ ] Git コミットフロー（`git add` 後に報告してユーザーがコミット）の定着
  - [ ] **検証プロセスの厳格化 (True TDD)**: バッチ処理やDBスキーマを変更する際は、「処理が通るか」だけでなく「DB内のデータ（カラム、NULL有無）が完全に期待通りか」をSQL等で自動・手動検証する仕組みを定着させる。

- [ ] **`rs_momentum_e200` の積み方が過剰かもしれない — 退役または再設計の検討**（2026-09-21 起票）
  - **症状**: `rs_momentum_e200` は200期間の処理を4段重ねており、必要な遡りが810本（約3年3ヶ月）。表示専用
  - **対応案**: 退役するか、積み方を再設計する
  - **詳細**: [doc/issues/rs_momentum_e200_redesign.md](issues/rs_momentum_e200_redesign.md)
