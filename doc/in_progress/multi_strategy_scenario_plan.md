# 複数戦略の組み合わせによるシナリオテスト 計画書

- **ステータス**: 🚧 進行中
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-08-21 / **完了日**: —
- **作業ブランチ**: worktree-multi-strategy-scenario
- **関連**: `doc/backend_specification.md` §6.1（3種類のテストの役割分担）

## 1. 背景と目的

2026-08-21 の税込みシナリオテストで、**質の高い戦略ほど枠が空いている**ことが判明した。

| 戦略 | 年間取引 | 平均保有日 | **枠稼働率** | 1取引% | CAGR |
| :--- | ---: | ---: | ---: | ---: | ---: |
| D | 97.3 | 21.9 | **105.7%** | 0.11 | **-3.55** |
| G1 | 95.0 | 21.7 | **102.1%** | 0.75 | **-6.07** |
| B3 | 70.7 | 17.0 | 59.8% | 5.90 | 26.72 |
| B6 | 54.0 | 17.5 | 47.0% | 5.05 | 15.71 |
| **B5** | 41.4 | 16.1 | **33.0%** | **7.41** | 21.47 |
| **B2** | 29.5 | 19.3 | **28.3%** | 6.89 | 13.93 |

> 枠稼働率 = 年間の延べ保有銘柄日 ÷ (8枠 × 252営業日)。100% で常時満枠。

**稼働率と CAGR は相関しない**（D・G1 は満枠でも最下位、B5 は33%で21.47%）。質が支配的である。
一方、**同じ質の帯の中では取引数が効く**: B5(7.41%/41件) と B2(6.89%/29.5件) は質がほぼ同じで、
取引数の差がそのまま CAGR 21.47 vs 13.93 に出ている。B2 が税込み最適化で 237→125 件に半減し
CAGR が 25.21→13.93 に半減したのも同じ構図。

**仮説**: 質の高い戦略を組み合わせれば、**質を保ったまま取引数を増やせる**。
低質な戦略（D・G1）で枠を埋めるのとは本質的に異なる。

### 成功条件

組み合わせジョブの CAGR / Calmar が、構成する単独戦略のいずれをも上回ること。
上回らない場合も「なぜ上回らなかったか」（重複が多い／質が薄まる）をデータで説明できること。

## 2. スコープと設計判断

### 2.1 変更すること

- `run_scenario_batch.py` が**複数戦略の preset を生成**できるようにする
- `scenario_batch_jobs.toml` のジョブが**複数の戦略/study を指定**できるようにする
- トレードログ CSV に **`score`（何戦略が同じ銘柄を拾ったか）を出力**する

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| エンジン側（`ScenarioScorer` / `min_score` / `process_buy_candidate`） | **変更しない** | **既に多戦略前提で実装済み**。`ScenarioScorer.score_signals()` が銘柄ごとに戦略ヒット数を `score` として集約し、スコア降順（同点は `rs21_rank`）で並べる。`process_buy_candidate` も `position['score']` を保持している。塞がっているのは `generate_preset_toml()` が1戦略の preset しか吐かない点だけ |
| 組み合わせる戦略 | **B2 + B5 + B6**（ユーザー判断 2026-08-21） | B2・B5 は質が高く枠が空いている。B3 は「B2 の14日短期版」で性格が近く、組み合わせる意味が薄い。性格の異なる B6（RS-MACD 加速系）を採る |
| 和集合と合流の検証方法 | **`min_score=1`（和集合）で1回走らせ、`score` 別に成績を分解する** | 別々に2回走らせるより情報量が多い。1回のデータから「score 2以上だけ取っていたらどうなったか」が読める。閾値をいくつにすべきかもデータで決まる。`min_score=2` の実行は結果を見てから判断する |
| `SCENARIO_TARGET_PREFIX` の扱い | **変更しない** | 戦略名は `f"{section.capitalize()} - {group} - {name}"` で組まれ、prefix `'Rise - Check'` が全て拾う。`group = "Check"` を保てば複数戦略でもそのままスキャン対象になる |
| 単独戦略ジョブの既存挙動 | **完全に維持する** | 既存13ジョブの結果が変わってはならない。**単一 `strategy_code` の指定は従来どおり動くこと**を回帰テストで担保する |

## 3. 変更内容

### 3.1 `backend/backtest/scenario_reporter.py`

`export_trade_logs()` の出力列（L274-277 の空 CSV 用と L283-286 の本体）に **`score` を追加**する。

> `trade_record = {**pos, ...}`（`scenario_portfolio.py:359`）で **`score` は既にトレード記録まで
> 届いている**。列リストで落としているだけなので、リストに足すだけでよい。
> 位置は `ticker` の直後（銘柄と一緒に見たいため）。

### 3.2 `backend/backtest/run_scenario_batch.py`

**(a) `generate_preset_toml()` を複数戦略対応にする**

現在は単一の `strategy_name` / `best_params` を受け、`active_rise_ids` に1件だけ書く。
これを `[(strategy_name, best_params), ...]` のリストを受ける形に拡張する。

```
active_rise_ids = ["B2_..._opt", "B5_..._opt", "B6_..._opt"]
active_fall_ids = []

[[rise]]
id = "B2_..._opt"
name = "B2_..._opt"
group = "Check"        # ← 全ブロックで "Check" を維持すること（prefix 判定に必要）
...
[rise.filters]
...

[[rise]]   # B5, B6 も同様
```

**後方互換**: 単一戦略の呼び出しは従来と同一の TOML を出力すること。

**(b) ジョブ定義の拡張**

`scenario_batch_jobs.toml` の1ジョブが複数戦略を持てるようにする。既存の
`strategy_code` / `study_name`（単数・文字列）はそのまま動かし、新たに配列形式を受け付ける:

```toml
[[job]]
name = "B256_union"
strategy_codes = ["B2_theme_rsrank_momentum", "B5_rs_trend_with_theme", "B6_rs_macd_and_theme"]
study_names    = ["B2_theme_rsrank_momentum", "B5_rs_trend_with_theme", "B6_rs_macd_and_theme"]
source = "optuna"
  [job.portfolio]
  min_score = 1     # 和集合。どれか1つが拾えば買う
```

- `strategy_codes` / `study_names` は**同じ長さ**であること。違えばエラーで停止する
- `strategy_code`（単数）と `strategy_codes`（複数）の**併記はエラー**にする
- **未知キーは既に「エラーで停止」の方針**（`scenario_batch_jobs.toml` 冒頭のコメント参照）。
  この方針を崩さないこと

**(c) study が見つからない場合**

複数 study のうち**1つでも欠けたらそのジョブはエラーで停止**する。
現在の「1行 Skipping して continue」は今回のスコープ外だが、
**複数戦略ジョブでは一部だけ読めた状態で走らせてはいけない**（意図と違う組み合わせになるため）。

### 3.3 `data/scenario_batch_jobs.toml`

`B256_union` ジョブを追加する（上記 (b) の形）。既存ジョブは変更しない。

## 4. ユーザー確認事項

| 項目 | 判断 |
| :--- | :--- |
| 組み合わせる戦略 | **B2 + B5 + B6**（2026-08-21） |
| 和集合 / 合流 | **まず和集合 `min_score=1` を実行し、`score` 別に分解**。合流は結果を見て判断（2026-08-21） |
| 既存13ジョブ | **変更しない**（結果も変わってはならない） |

**未解決**: なし

## 5. 実装順序と進捗チェックリスト

- [x] `export_trade_logs()` に `score` 列を追加（テスト先行）
- [x] `generate_preset_toml()` の複数戦略対応（テスト先行。**単一戦略の出力が従来と同一**であることの回帰テストを必ず含める）
- [x] ジョブ定義の複数戦略対応（`strategy_codes` / `study_names`、長さ不一致・単複併記・study 欠落のエラー検証）
- [x] `scenario_batch_jobs.toml` に `B256_union` を追加
- [x] 全テスト（backend）
- [x] **単一戦略ジョブの回帰確認**（§6）
- [x] 仕様書更新（`backend_specification.md` §6.1 に組み合わせジョブと score の記述）
- [ ] 計画書を `doc/completed/` へ移動

### 作業中メモ

- **`output/scenario/` には 2026-08-21 の税込み結果がある。上書きしないこと。**
  検証用の実行は `--jobs` で絞り、出力先が既存ジョブと衝突しないジョブ名を使う。
- 実行は**ユーザーが行う**。ワーカーはフルバッチを回さないこと（4時間規模）。

## 6. 検証プラン / 結果

### 単体（実施済み・2026-08-21）
- `export_trade_logs()`: `score` を持つ/持たないトレード履歴の双方で落ちないことを確認
  （`backend/tests/backtest/test_scenario_reporter.py` に追加）
- `generate_preset_toml()`: **単一戦略の出力が変更前とバイト単位で一致**することを確認
  （`test_generate_preset_toml_single_strategy_byte_identical_to_legacy`）
- 複数戦略: `active_rise_ids` が3件、`[[rise]]` ブロックが3つ、**全て `group = "Check"`** であることを確認
  （`test_generate_preset_toml_multi_strategy`）
- ジョブ定義: 長さ不一致 / 単複併記 / study 欠落がいずれも**エラーで停止**することを確認
  （`resolve_job_strategy_specs()` のテスト群。study 欠落は `main()` 内で `is_multi=True` の場合に
  `sys.exit(1)` する分岐として実装。単体テストは長さ不一致・単複併記・キー欠如をカバー）
- 全テスト: `backend/tests/` 1008 passed, 1 known-env failure
  （`test_scenario_comparison.py::test_run_comparison_generates_outputs` — ワークツリーの
  `data/parquet_master/` が空のため。今回の変更と無関係、着手前から既知）

### 結合（軽量・実施済み・2026-08-21）
`tmp/validate_multi_strategy.py`（使い捨てスクリプト）で `B256_union` を
**1モデル(full_position) × 1run(run_0)** に絞って実行（出力先 `output/scenario_multitest/`、
本番 `output/scenario/` は無傷）。結果:
- `scenario_trade_logs.csv` に `score` 列が `ticker` の直後に出力され、値は 1〜3 の範囲
- score 別トレード数: score=1 が302件、score=2 が108件、score=3 が18件（**合計428件**）
  → **score 2以上のトレードは126件発生**しており、3戦略の重複は確認できた（組み合わせの前提は崩れていない）
- run 全体のサマリ: `cagr=46.89`, `total_trades=428`, `win_rate=0.3855`,
  `max_drawdown={pct: 42.99, amount: 45770.14, peak_date: 2022-03-10, trough_date: 2022-11-18}`
  （1run のみのため参考値。本実行の複数 run 平均で評価すること）

### 回帰（実施済み・2026-08-21）
単独戦略ジョブ `B2` を 1モデル(full_position) × 1run(run_0) で実行し（出力先
`output/scenario_multitest_regression/`）、`output/scenario/B2/full_position/run_0/scenario_summary.json`
と完全一致することを確認した:
`cagr=14.29`, `total_trades=182`, `win_rate=0.3681`,
`max_drawdown={pct: 26.19, amount: 31370.21, peak_date: 2022-04-18, trough_date: 2022-11-09,
trough_tickers: [SEI, LIND, CRSR]}` — 全項目一致。

### 本実行（ユーザーが実施）
`B256_union` をフル（5モデル × 10 run）で実行し、以下を分析する:
- 組み合わせの CAGR / Calmar が B2・B5・B6 単独のいずれをも上回るか
- **score 別の成績**（score=1 / 2 / 3 の 1取引% と勝率）。
  score が上がるほど成績が良ければ「合流に価値がある」ことの直接的な証拠になる
  （2026-08-21 のユーザー観察「7シグナルが重なった TEM が翌日も上昇」の体系的検証）
- 重複率（3戦略の延べシグナル数 ÷ ユニーク銘柄数）
