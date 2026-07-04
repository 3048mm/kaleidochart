# 最適化パラメータの戦略内包化とシナリオバッチジョブTOML連携の実装計画書

本ドキュメントは、システムトレードにおける戦略定義（`[[strategy]]`）と最適化探索範囲（`[strategy.optimization]`）を統合し、パラメータ手動微調整および複数パターンの比較シミュレーションをノンプログラミングで制御できるようにするためのリファクタリング計画およびアクションアイテムです。

---

## 1. 背景と目的

現在、最適化パラメータの探索スペース定義は、戦略本体の定義とは別の `[optimization.<短縮コード>]` セクションに分かれて定義されています。
これを戦略本体の `[[strategy]]` ブロック直下にサブテーブル `[strategy.optimization]` として内包する構造へ移行し、探索スペースの定義を一体化します。
同時に、シナリオテストの一括実行を管理する設定ファイル `scenario_batch_jobs.toml` を導入し、任意のStudy（マニュアル上書きパラメータを含む）を並行してシミュレーション比較できる仕組みを構築します。

---

## 2. アクションアイテム

### Phase 1: 設定ファイルの再構成とパース修正

- [ ] **`backtest_config.toml` の再構成**:
  - `[optimization.*]` の全セクションを撤廃。
  - 各 `[[strategy]]` ブロックの下に `[strategy.optimization]` として探索範囲を移設。
- [ ] **`optimization_runner.py` の修正**:
  - ハードコードされていた `full_names`（短縮コードマッピング）を完全撤廃。
  - `--strategy` でフルネーム（例: `B4_rs_trend_with_theme`）を受け取れるように変更。
  - `parse_optimization_params` を修正し、対象の戦略辞書内の `strategy.get('optimization', {})` から探索パラメータ設定を直接取得するように変更。
  - 最適化Study名を `opt_strategy_{strategy_name}_multi_period` とする。
- [ ] **テストの更新 & GREEN検証 (TDD)**:
  - `backend/tests/test_optimization_runner.py` 内のテストを、内包された `optimization` セクションのパースに対応させるように修正。
  - テストを実行し、パラメータ取得が正しく機能することを確認。

### Phase 2: シナリオバッチ用ジョブ定義TOMLの導入

- [ ] **`data/scenario_batch_jobs.toml` の新規追加**:
  - 並列シナリオシミュレーションを実行するジョブ一覧を管理する指示書ファイルを新規作成する。
- [ ] **`run_scenario_batch.py` の修正**:
  - ハードコードされていた戦略リスト（`A`, `B1`等）を廃止。
  - `data/scenario_batch_jobs.toml` をロードして、定義された各ジョブに従って並列モンテカルロシミュレーションを実行する。
  - `source = "manual"` ジョブが指定された場合、ベースStudyの最良パラメータに `override_params` で指定された値をマージ（上書き）してシミュレーションを実行する。
  - 結果の出力先ディレクトリ名をジョブ名（`job.name`）にする。
- [ ] **テストの追加 & GREEN検証 (TDD)**:
  - ジョブ定義ファイルをロードし、マニュアル上書きパラメータが正しく結合されることを検証するテスト `backend/tests/test_scenario_batch_jobs.py` を追加・検証。

### Phase 3: 仕様書のアップデートと動作確認

- [ ] **仕様書への反映**:
  - `doc/backtest_specification.md` および `doc/scenario_test_specification.md` の「パラメータ定義」「実行方法」セクションを新仕様に更新。
  - `doc/optimization_manual.md` を、同一ブロック内包形式および `--strategy <フルネーム>` のコマンド仕様に更新。
- [ ] **統合テスト**:
  - 実際に最適化コマンドおよびシナリオ一括バッチを実行し、最後までエラーなく完了してフォルダが作成されることを確認する。
