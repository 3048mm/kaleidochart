# 型1最適化 目的関数の再設計（CAGR × DD × 検出件数）計画書

- **ステータス**: 🚧 進行中（コード実装・テスト完了 / 実データ校正・型3検証はユーザー実行待ち）
- **実施者**: AI エージェント（Claude Opus 4.8）
- **開始日**: 2026-07-06 / **完了日**: —
- **作業ブランチ**: worktree-objective-redesign
- **対象 issue / 関連ドキュメント**: `doc/backend_specification.md` §6.1, §6.5 / `backend/optimization_runner.py` / `backend/backtest/backtest_report.py`

## 1. 背景と目的

### きっかけ
E系のバックテスト中、B2/B4/B5 のバックテスト結果を入力にした**型3（有限資産）シナリオテスト**を回したところ、
評価値見直し後の結果を使うと**リターンが著しく低下**した。原因を型1最適化の目的関数まで遡って調査した結果、
**目的関数の設計が、資産成長ではなく「1トレードあたりの期待値」を最適化していた**ことが判明した。

### 根本原因（現状 `optimization_runner.py`）
- `objective()` が返すスコアは `calculate_custom_score` の期間平均。その**主指標は `expectancy_lcb`**
  （＝期待値の下側信頼限界＝1トレード単価の統計的下限）。
- CAGR・トータルリターン・検出件数は `trial.set_user_attr()` で**記録しているだけでスコアに一切効いていない**
  （`optimization_runner.py:445, 495-514`）。
- 結果として「たまに出る高期待値トレード」を選好し、**検出件数が少なくても LCB が高ければ勝つ**。
  型3（有限資産）では「検出が枯れる＝枠が現金で遊ぶ＝複利が回らない」ため、期待値が高くても CAGR が伸びず、
  型1で良く見えたパラメータが型3で崩れる。

### 目的（成功条件）
型1のスコアを **`expectancy_lcb`（トレード単価）から「複利での資産成長 × DD抑制 × 実用的な検出件数」へ再設計**し、
型1で高スコアなパラメータが型3の有限資産シナリオでも素直にリターンへ結びつくようにする。

## 2. スコープと設計判断

### 2.0 型1と型3の役割分担（再設計の前提。§6.1）

| | 型1: スクリーンバックテスト（本タスク対象） | 型3: 個別銘柄シナリオテスト |
|---|---|---|
| 目的 | スクリーン条件の最適化・シグナルの質評価 | 型1のスクリーンを入力にした戦略の比較・最適化 |
| 資金モデル | **無限資金**（全シグナル独立評価。固定枠・サイズ制約なし） | **有限資産**（固定枠・同時保有数制約あり） |
| レジーム | 認識しない（意図的） | する（MTS連動） |
| コスト | なし | あり |
| 検証の肝 | 純粋なスクリーンのみで、どこまで資産を伸ばしつつDDを抑えられるか | 有限資産での実運用リターン |

### 2.1 変更すること
- `calculate_custom_score` の**主指標を `expectancy_lcb` → 期間CAGR（無限資金・avg_slots正規化のまま）**へ差し替える。
- DDペナルティ（閾値付き2乗・`max_allowed_dd`）は流用しつつ、**CAGRに対して**掛ける（実質 Calmar 型）。
- **検出件数の実用帯係数 `detect_adequacy(avg_hits_per_day)`** を新設し、スコアに掛ける（多すぎず少なすぎず）。
- 検出件数まわりの旧アドホック割引（`avg_trades_per_day > 10` の平方根割引、`>25`/`>50` の減点）を
  `detect_adequacy` に一本化する。外側のハード境界（`min/max_avg_hits_per_day` の prune）は現状維持。
- `objective()` のサマリログ行と `main()` の best-trial 出力を新スコア構成に合わせて更新。

### 2.2 変更しないこと（確定した設計判断）

| 論点 | 判断 | 理由 |
|---|---|---|
| 固定枠（有限資産）CAGRの導入 | **型1には入れない** | 固定枠は型3の責務。型1に持ち込むのは分担違反（§6.1） |
| `strat_multiplier` / `max_drawdown_pct` の計算式（`backtest_report.py`） | **変更しない**（avg_slots正規化を維持） | 型1は無限資金。正規化は分担上正しい。合成は `optimization_runner` 内で行う |
| 手数料・税金 | 型1では導入しない | argmax を変えないため（§6.5.1） |
| 期間ごとの最低活動量ゲート（<5件）と per-period 採点→平均の構造 | 維持 | レジーム非依存の安定性を担保（各期間で独立に良CAGR/低DDを要求） |
| ホールドアウト検証（§6.5.2） | 無変更 | スコア計算に非関与。今回の変更の影響を受けない |
| `expectancy_lcb` 指標自体 | 残す（user_attr / ホールドアウト表示） | 主指標からは外すが「シグナルの質」の補助指標として有用 |

## 3. 変更内容

### 3.1 `calculate_custom_score`（`optimization_runner.py`）

新シグネチャ（案）:
```python
def calculate_custom_score(metrics, total_trading_days, max_allowed_dd=20.0, detect_band=(3.0, 12.0, 0.4)):
```

ロジック（案）:
1. `metrics` 無し → `-1000`。`total_trades < 5` → `-100 + trades*20`（勾配ゲート、現状維持）。
2. `avg_per_day = total_trades / total_trading_days`。
3. **成長項 = 期間CAGR**: `period_years = total_trading_days / 252`,
   `period_cagr = (metrics['strat_multiplier'] ** (1/period_years) - 1) * 100`。
4. `period_cagr <= 0` → `period_cagr - normalized_dd`（成長ゼロ以下はDDの深さでさらに沈める）。
5. **DDペナルティ**（既存の閾値付き2乗を流用）を `period_cagr` に適用: `score = period_cagr / penalty`。
6. **検出件数係数**を掛ける: `score *= detect_adequacy(avg_per_day, detect_band)`。

`detect_adequacy(x, (lo, hi, floor))`（案）: `lo<=x<=hi` で 1.0、`x<lo` は `max(floor, x/lo)`、
`x>hi` は `max(floor, hi/x)`。帯は戦略ごとに `backtest_config.toml` で調整可能にする（デフォルトはコード側）。

### 3.2 `objective()`（`optimization_runner.py`）
- `max_allowed_dd` に加え `detect_band` を `strat_base` / `[optimization_pruning]` から解決して `calculate_custom_score` に渡す。
- サマリログ行を「Score | CAGR | MaxDD | avg/day | Trades」構成へ更新。
- CAGR/DD/検出件数の user_attr 群は既存のものを流用（表示のみ、計算は既存）。

### 3.3 テスト（TDD）
- `backend/tests/backtest/test_optimization_score.py`（新規）: `calculate_custom_score` の純関数テスト。
  - CAGR単調性（DD一定でCAGR↑→score↑）
  - DDペナルティ（CAGR一定でDD>閾値→score急落）
  - detect_adequacy（帯内=1.0、過少・過多で減衰、帯の内側が最大）
  - <5件ゲート、CAGR<=0枝。
- 既存 `test_optimization_cagr.py` は CAGR user_attr の算出を検証するもので、主指標変更後も通るはず（要確認）。

### 3.4 ドキュメント
- `doc/backend_specification.md` §6.1.1 / §6.5 の「主指標=Expectancy LCB」記述を新スコア構成へ更新。
- 完了後この計画書を `doc/completed/` へ移動。

## 4. ユーザー確認事項（2026-07-06 確定）

1. **成長項の定義** → ✅ **絶対CAGR**（対SPY超過ではなく資産成長そのもの）。
2. **期間CAGRの年率化** → ✅ **年率化する**（学習期間の長さ差を公平化）。
3. **検出件数の実用帯 `detect_band`** → ✅ **lo=1**（1件/日でも許容）。`floor`＝検出件数が帯外でも
   スコアをこれ以上割り引かない下限係数（ソフト選好。ハードな門番は prune 境界が担う）。
   **暫定デフォルト: `lo=1, hi=12, floor=0.4`（全て TOML で戦略別に可変）。**
   `hi` と `floor` の最終値は、ドライ実行で avg件/日 の実分布を見てから実測校正する。
4. **未コミット変更の扱い** → ✅ ユーザーが現状を `9123f6b [update] GUI の更新と最適化検討` として
   **コミット済み**（config.toml 再校正・runner/test 込み）。ワークトリーは同コミットへリベース済み。整合完了。

## 5. 実装順序と進捗チェックリスト

- [x] 計画レビュー・§4確定（2026-07-06 確定。絶対CAGR/年率化/lo=1・floor=0.4・hi=12暫定）
- [x] `test_optimization_score.py` を先に書く（RED）→ 12件 GREEN
- [x] `detect_adequacy` + `calculate_custom_score` を再実装（GREEN）
- [x] `objective()` の引数解決（detect_band）・サマリログ/best-trial 出力を CAGR 基準へ更新
- [x] `backtest_config.toml` `[optimization_pruning]` に `detect_lo/hi/floor` を追加（戦略別上書き可）
- [x] 旧LCBテスト（`test_optimization_runner.py::TestCustomScoreUsesLcb`）を撤去し移設コメント化
- [x] backtest 系テスト全通過確認（117 passed。`test_optimization_cagr.py` 含む）
- [x] `doc/backend_specification.md` §6.1.1 / §6.5 更新
- [ ] **（ユーザー実行）** 小規模 study で avg件/日 の実分布を見て `hi`/`floor` を実測校正
- [ ] **（ユーザー実行）** best params を型3シナリオ（B2/B4/B5）に流し、評価値見直し後もリターンが回復するか確認（本タスクの本来ゴール）
- [ ] 上記2つの結果を踏まえ、計画書を `doc/completed/` へ移動

### 作業中メモ
- いま: 実装・単体/結合テスト完了（117 passed）。コード側の再設計は一巡。
- 残: 実データでの校正（hi/floor）と型3での最終検証は**ユーザー実行**（本番 parquet/共有 Optuna DB に触れるためワークトリーからは実行しない）。
- 注意: スコアのスケールが変わったため、既存 `optimization_trials.db` の旧 study 値とは直接比較不可（§8）。新旧を並べる場合は study 名を分けるか作り直す。
- 参考ログ例: `[Trial N] Score: .. | PortCAGR: +XX.X% (vs SPY ..) | MaxDD: .. | Y.Y hits/day | Trades: .. | Win: ..% | AvgGain: ..%`

## 6. 検証プラン / 結果

- 単体: `python -m pytest backend/tests/backtest/test_optimization_score.py backend/tests/backtest/test_optimization_cagr.py -v`
- 統合: `run\run_optimization.bat`（または `--trials` 少数）で B系戦略を回し、
  新スコアの best trial が「高CAGR・低DD・実用的な検出件数」に寄るかを目視。
- 最終判定: 得られたパラメータを型3シナリオテスト（B2/B4/B5）に流し、**評価値見直し後でもリターンが
  回復するか**を確認（本タスクの本来のゴール）。
- （before/after 計測表は実行後に追記）

## 7. 途中発生した課題

（実装中に追記）

## 8. スコープ外・残作業

- 型3シナリオテスト側のロジック変更は本タスク対象外（今回は型1の目的関数のみ）。
- スコアのスケール変更に伴う `optimization_trials.db` の既存 study との非互換（旧スコアと新スコアは直接比較不可）。
  必要なら study 名を分ける／作り直す運用を別途検討。
