# シナリオテスト結果への「1取引平均リターン」表示追加 計画書

- **ステータス**: ✅ 完了（2026-08-29 棚卸し）
- **実施者**: AI エージェント (Claude Opus 5) — オーケストレーター
- **開始日**: 2026-08-19 / **完了日**: 2026-08-29
- **作業ブランチ**: worktree-scenario-avg-trade
- **対象 issue / 関連ドキュメント**: `doc/backend_specification.md` §6.1（3種類のテストの役割分担）

## 1. 背景と目的

個別銘柄シナリオテスト（型3）の結果表示に **1取引あたりの平均リターン**が無い。

2026-08-19 の B6 の採否判断で、この指標が決め手になった:

| 指標 | B6 新ベスト | B6 旧パラメータ | 勝者 |
| :--- | ---: | ---: | :--- |
| 平均 CAGR | 38.24 | 44.42 | 旧 |
| 最大 DD | 26.6 | 30.5 | 新 |
| Profit Factor | 1.64 | 1.57 | 新 |
| **1取引平均リターン** | **+5.14%** | +4.39% | **新** |
| トレード数 | 12,917 | 17,803 | — |

CAGR だけ見ると旧の勝ちだが、**その差は取引数が27%多いことから来ている**。
ユーザーの評価基準は「取引数を上げたい訳ではない（現実の取引では税引きされる）。
1取引%が高く、高CAGR、低DDであるのがベスト」であり、
**1取引平均リターンを見ないと戦略の質を判断できない**。

現状この値は毎回 `tmp/` の使い捨てスクリプトでトレードログ CSV から算出しており、
画面からは見えない。**成功条件は、シナリオテスト結果の画面で他の指標と並んで見えること。**

## 2. スコープと設計判断

### 2.1 変更すること

- `scenario_reporter.py` が書く `scenario_summary.json` に `avg_trade_pnl_pct` を追加
- `/api/backtest/scenario/{name}/summary` が `avg_trade_pnl_pct` / `avg_trade_pnl_pct_avg` を返す
- フロント3画面の指標カード・比較テーブルに表示

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
| :--- | :--- | :--- |
| Monte Carlo の平均の取り方 | **B案: run ごとに平均を出してから平均**（ユーザー判断 2026-08-19） | 既存の `profit_factor_avg` / `win_rate_avg` と同じ扱いに揃える。A案（全 run のトレードを合算して単純平均）は取引数の多い run の重みが大きくなり、他指標と一貫しない |
| 既存結果の再実行 | **しない** | API 側に「JSON に無ければトレードログ CSV から算出」のフォールバックを置くことで、2026-08-18 以前の結果もそのまま表示できる |
| 指標の定義 | **トレードの `pnl_pct` の単純平均**（建玉に対するリターン） | 「純損益 ÷ 取引数 ÷ 初期資金」は建玉サイズと複利効果を混ぜてしまい、戦略の質を表さない |
| 型1（最適化バックテスト）側への追加 | **やらない** | 最適化側は既に Optuna の `avg_gain` として持っている。本計画は型3の画面表示が対象 |
| 税引き後の1取引リターン | **やらない** | `consider_tax` はシミュレーション側の設定であり、有効なら `pnl_pct` に既に反映される。表示指標を二重化しない |

## 3. 変更内容

### 3.1 `backend/backtest/scenario_reporter.py`

`_calculate_metrics()` の戻り dict に追加する。`profit_factor` の算出直後が自然。

```python
# --- 1取引あたり平均リターン（建玉に対する pnl_pct の単純平均） ---
avg_trade_pnl_pct = 0.0
if trade_history:
    pcts = [t['pnl_pct'] for t in trade_history if t.get('pnl_pct') is not None]
    if pcts:
        avg_trade_pnl_pct = round(sum(pcts) / len(pcts) * 100, 2)
```

> **単位に注意**: トレードログの `pnl_pct` は**小数**（-0.1406 = -14.06%）。
> `win_rate` も小数のまま返しているが、こちらは **% に直して返す**
> （画面の他の % 指標と揃え、フロント側での二重変換事故を防ぐ）。

### 3.2 `backend/api/backtest_router.py`

`get_scenario_summary()` に、run ごとの1取引平均を集める処理を追加する。

1. 各 run の `scenario_summary.json` に `avg_trade_pnl_pct` があればそれを使う
2. **無ければ同じ run ディレクトリの `scenario_trade_logs.csv` から算出**（既存結果対応）
3. Monte Carlo は **run ごとの値の平均**（B案）を `avg_trade_pnl_pct_avg` に入れる
4. 単一 run のときは `avg_trade_pnl_pct` のみ

CSV フォールバックはヘルパー `_avg_trade_pnl_pct_from_logs(run_path)` に切り出す。
CSV が無い / 読めない場合は `None` を返し、**例外にしない**（結果表示が落ちる方が困る）。

### 3.3 `backend/api/schemas.py`

`BacktestScenarioSummary` に2フィールド追加（どちらも `Optional[float] = None`）:
`avg_trade_pnl_pct` / `avg_trade_pnl_pct_avg`

### 3.4 フロントエンド

| ファイル | 箇所 |
| :--- | :--- |
| `frontend/src/pages/ScenarioDetailView.tsx` | 指標カード（PF の隣, L379 付近）／モデル別比較テーブル（L801 付近） |
| `frontend/src/pages/BacktestResultPage.tsx` | 同上（L538 付近 / L1029 付近） |
| `frontend/src/pages/RegimeComparisonPage.tsx` | レジーム比較の指標（L87 付近） |

- 表示は `+5.14%` のように符号付き小数2桁
- 色分けは PF と同じ流儀で、**0 を境に** `--accent-green` / `--accent-red`
- Monte Carlo のときは PF と同様に `(平均: x.xx)` を併記
- 値が `null`/`undefined` のときは `—` を表示して落とさない（古い結果対応）

## 4. ユーザー確認事項

| 項目 | 判断 |
| :--- | :--- |
| Monte Carlo の平均の取り方 | **B案で確定**（2026-08-19） |
| 既存結果を再実行するか | **しない**（CSV フォールバックで対応）— 2026-08-19 時点で税込みシナリオテストが実行中のため、再実行は現実的でない |
| 表示位置 | Profit Factor の隣（質を表す指標同士を並べる） |

**未解決**: なし

## 5. 実装順序と進捗チェックリスト

- [x] `scenario_reporter.py` に `avg_trade_pnl_pct` を追加（テスト先行）
- [x] `backtest_router.py` に CSV フォールバック `_avg_trade_pnl_pct_from_logs()` を追加（テスト先行）
- [x] `get_scenario_summary()` の Monte Carlo 集計に B案で組み込み（テスト先行）
- [x] `schemas.py` に2フィールド追加
- [x] フロント3画面に表示追加 + vitest
- [x] バックエンド全テスト + `npm test` + `npm run build`
- [x] 実データ（`output/scenario_without_tax/B6` と `B6_prev_params`）で API 応答値が
      手集計（新 +5.14% / 旧 +4.39%、B案なので多少ずれる）と整合するか確認
- [x] 仕様書更新（`backend_specification.md` の scenario summary 項）
- [x] 計画書を `doc/completed/` へ移動（2026-08-29）

### 作業中メモ

**2026-08-19 時点の注意**:
- **ユーザーが税込みシナリオテストを実行中**。重いバックグラウンドジョブを走らせないこと
  （過去に並行実行で最適化 trial を落とした前例あり）。バックエンド全テストは実行が
  落ち着いてから回す。
- 昨夜の税なし結果は `output/scenario_without_tax/` に退避済み。検証にはこちらを使う
  （`output/scenario/` は税込みで上書きされる）。
- `consider_tax` は現在 `20.0`（未コミット）。`[general]` なので**最適化にも効く**。
  シナリオテスト後に 0.0 へ戻す必要がある（本計画の対象外だが失念注意）。

## 6. 検証プラン / 結果

- 単体: `scenario_reporter` の平均算出（空リスト / `pnl_pct` 欠損 / 正負混在） — 実施済み。
  `backend/tests/backtest/test_scenario_reporter.py` に3テスト追加、全て pass。
- 単体: CSV フォールバック（CSV 無し → `None`、壊れた行はスキップ） — 実施済み。
  `backend/tests/api/test_backtest_api.py` に `_avg_trade_pnl_pct_from_logs` /
  `_resolve_avg_trade_pnl_pct` の単体テスト6件を追加、全て pass。
- 結合: Monte Carlo 集計が **run ごとの平均の平均**になっていること（A案との差が出る
  非対称なフィクスチャで検証する。両案で同値になるフィクスチャでは検証にならない）
  — 実施済み。`test_get_scenario_summary_monte_carlo_uses_b_case_average` で
  run_0(1件, +10%) / run_1(3件, -10%×3) の非対称フィクスチャを使用。
  B案 = 0.0%、A案（全トレード合算）= -5.0% となり、B案の値のみが得られることを確認。
- 実データ: 退避済みの `B6` / `B6_prev_params` で新旧の大小関係が再現すること — 実施済み。
  実装済みの `_resolve_avg_trade_pnl_pct` を直接呼び出し、5モデル×10run を集計。
  **全モデル・全run（50run）を対象にした B案集計: 新(B6) 5.27% > 旧(B6_prev_params) 4.41%**
  で、手集計（A案・全トレード合算: 新 +5.14% / 旧 +4.39%）と近い値かつ大小関係も一致。
  （参考: モデル別内訳では `spy_sma63` のみ旧が僅かに上回ったが（4.42% vs 4.10%）、
  全体集計では新が上回っており、崩れの兆候はなし）
- フロント: 値が `null` のとき `—` 表示で落ちないこと — 実施済み。
  `ScenarioDetailView.test.tsx` に `avg_trade_pnl_pct` 未定義時の `—` 表示確認テストと、
  値あり時の `+5.14%` / `(平均: +5.14%)` 表示確認テストを追加。
  `RegimeComparisonPage.test.tsx` にも同様のテスト2件を追加。全て pass。
