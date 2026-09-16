# Monte Carlo CAGR 集計の幾何平均・中央値対応 計画書

- **ステータス**: ✅ 完了（MC CAGR・最終資産の幾何平均化、中央値・相加平均併記、UIラベル更新、世代間比較スクリプト更新完了）
- **実施者**: AI エージェント (Gemini 3.8 Flash)
- **開始日**: 2026-09-11 / **完了日**: 2026-09-12
- **作業ブランチ**: main
- **対象 issue / 関連ドキュメント**:
  - [`doc/issue_list.md:1117`](file:///d:/My%20Documents/Programing/stocktool/doc/issue_list.md#L1117-L1135) 「Monte Carlo の CAGR 集計が相加平均になっている」
  - [`doc/completed/objective_quality_first_plan.md`](file:///d:/My%20Documents/Programing/stocktool/doc/completed/objective_quality_first_plan.md)（型1 の `geo_mean_gain` への切り替え経緯）
  - [`backend/api/backtest_router.py`](file:///d:/My%20Documents/Programing/stocktool/backend/api/backtest_router.py)
  - [`backend/api/schemas.py`](file:///d:/My%20Documents/Programing/stocktool/backend/api/schemas.py)
  - [`frontend/src/pages/ScenarioDetailView.tsx`](file:///d:/My%20Documents/Programing/stocktool/frontend/src/pages/ScenarioDetailView.tsx)

---

## 1. 背景と目的

### 1.1 背景
- `backend/api/backtest_router.py:478` の `cagr_avg = float(np.mean(cagrs))` において、**10本の Monte Carlo (MC) run の CAGR が相加平均（Arithmetic Mean）で集計**されている。
- CAGR（年平均成長率）は複利（乗算）の指標であるため、相加平均を取ると相加・相乗平均の不等式（AM-GM 不等式）により、**run 間の分散が大きい戦略（特に戦略 A など）ほど典型的に得られる値よりも過大評価**される（期待値 vs 典型値の乖離）。
- 型1（単一銘柄バックテスト・Optuna 最適化）では、2026-08-24 に目的関数を相加平均（`avg_gain`）から幾何平均（`geo_mean_gain`）へ切り替え済みであるが、型3（ポートフォリオ・シナリオテスト MC）の集計だけが相加平均のまま取り残されている。

### 1.2 ベースライン（着手前の実測値）
現行 `output/scenario/` の 13 戦略（各 10 runs）での比較実測値：

| 戦略 | 相加平均 CAGR (現行) | 幾何平均 CAGR (改善後) | 中央値 CAGR | 乖離 (相加 - 幾何) |
| :--- | :---: | :---: | :---: | :---: |
| **A** | **12.06%** | **11.81%** | **10.74%** | **+0.25%** |
| **B1** | 8.25% | 8.18% | 7.82% | +0.07% |
| **B2** | 15.39% | 15.12% | 13.78% | +0.27% |
| **B3** | 18.29% | 18.05% | 19.28% | +0.24% |
| **B4** | 1.43% | 1.31% | 0.57% | +0.12% |
| **B5** | 24.51% | 24.11% | 23.44% | +0.40% |
| **B6** | 22.51% | 22.20% | 21.22% | +0.31% |
| **D** | -0.20% | -0.23% | -0.36% | +0.03% |
| **E1** | -0.05% | -0.06% | -0.00% | +0.00% |
| **E2** | 2.39% | 2.38% | 2.25% | +0.00% |
| **G1** | -1.86% | -2.08% | -2.46% | +0.21% |
| **G2** | 13.55% | 13.28% | 13.55% | +0.27% |
| **G3** | -2.07% | -2.13% | -1.97% | +0.06% |

※ 過去世代（`output/scenario_taxopt_cagrobj/` や `output/scenario_tax_preopt/`）でも最大 +0.66% の過大評価が確認されている。

### 1.3 目的（完了の定義）
1. 10本の MC run における代表 CAGR（`summary.cagr`）を、複利成長と整合する「**幾何平均（典型値）**」として算出・返却する。
2. 外れ値に強い「**中央値（`cagr_med`）**」および従来比較用「**相加平均（`cagr_avg`）**」も併せて提供し、多面的かつ公平に戦略を評価できるようにする。
3. `final_capital` の代表値も幾何平均（`final_capital_geo`）とし、初期資産・年数との間で $(F_{\text{geo}} / I)^{1/Y} - 1 = CAGR_{\text{geo}}$ の代数的一致を保証する。
4. フロントエンド（`ScenarioDetailView` 等）の表示で幾何平均であることを明記し、ツールチップ等で中央値・相加平均も参照可能にする。
5. 過去世代のデータ比較スクリプト（`tmp/three_gen_comparison.py` 等）を新基準に更新し、世代間比較の歪みを解消する。

---

## 2. スコープと設計判断

### 2.1 変更すること
- `backend/api/backtest_router.py`: MC 集計処理（`cagr_geo`, `cagr_med`, `cagr_avg` および `final_capital_geo`, `final_capital_med`, `final_capital_avg` の算出、破産セーフティガード）
- `backend/api/schemas.py`: `BacktestScenarioSummary` への `cagr_geo`, `cagr_med`, `final_capital_geo`, `final_capital_med` フィールド追加
- `backend/backtest/run_scenario_batch.py`: バッチ完了時コンソール出力の CAGR 表示更新
- `frontend/src/api/backtest.ts`: TypeScript 型定義の更新
- `frontend/src/pages/ScenarioDetailView.tsx`, `BacktestResultPage.tsx`, `RegimeComparisonPage.tsx`: 表示ラベルおよび詳細表示の更新
- `tmp/three_gen_comparison.py`: 世代間比較スクリプトの幾何平均化
- `doc/issue_list.md`: 課題ステータスの更新

### 2.2 変更しないこと(確定した設計判断)

| 論点 | 判断 | 理由 |
|---|---|---|
| **シナリオテストの再実行** | **再実行しない** | 各 run の `scenario_summary.json` は保存済みであり、API レスポンス時に動的集計されるため、コード改修のみで過去分を含め即座に新基準で算出できる |
| **単一 run（非MC）の CAGR 計算式** | **変更しない** | 単発 run の CAGR は $CAGR = (F / I)^{1/Y} - 1$ であり、すでに複利年率として数学的に正しい |
| **相加平均フィールド（`cagr_avg`）の廃止** | **廃止しない（残す）** | 既存コード・外部連携・過去ログとの互換性を保ち、期待値（相加）と典型値（幾何）の両方を分析可能にするため |
| **DB / Parquet データの変更** | **一切変更しない** | 本タスクは API / 集計 / 表示レイヤーの改修であり、Parquet マスターや SQLite は読み取り専用であるためデータ変更は発生しない |

---

## 3. 変更内容

### 3.1 数学的定義
各 run $i \in \{1, \dots, N\}$（通常 $N=10$）について、
各 run の年率成長倍率を $R_i = 1 + cagr_i$ とする（$cagr_i$ は小数表記、例: $+10\% \to 0.10$）。

1. **幾何平均 (Geometric Mean CAGR)**:
   $$CAGR_{\text{geo}} = \exp\left(\frac{1}{N}\sum_{i=1}^N \ln(\max(1 + cagr_i, 10^{-4}))\right) - 1$$
   ※ 万が一の元本全損・破産（$1 + cagr_i \le 0$）に対して $10^{-4}$（-99.99%）の下限クリップガードを設ける。
2. **中央値 (Median CAGR)**:
   $$CAGR_{\text{med}} = \text{median}(cagr_1, \dots, cagr_N)$$
3. **相加平均 (Arithmetic Mean CAGR)**:
   $$CAGR_{\text{avg}} = \frac{1}{N}\sum_{i=1}^N cagr_i$$
4. **最終資産の幾何平均 (Geometric Final Capital)**:
   $$F_{\text{geo}} = \exp\left(\frac{1}{N}\sum_{i=1}^N \ln(\max(F_i, 1.0))\right)$$

### 3.2 モジュール別の変更点

#### (1) `backend/api/backtest_router.py`
- `get_scenario_result(name)` の MC 集計部（lines 478〜570）：
  - `cagr_geo`, `cagr_med`, `cagr_avg` を計算。
  - `final_capital_geo`, `final_capital_med`, `final_capital_avg` を計算。
  - `summary_obj.cagr = cagr_geo`（主指標を幾何平均に差し替え）。
  - `summary_obj.final_capital = final_capital_geo`。
  - `summary_obj.__dict__` に `cagr_geo`, `cagr_med`, `cagr_avg`, `final_capital_geo`, `final_capital_med` を設定。

#### (2) `backend/api/schemas.py`
- `BacktestScenarioSummary` に以下を追加：
  ```python
  cagr_geo: Optional[float] = None
  cagr_med: Optional[float] = None
  final_capital_geo: Optional[float] = None
  final_capital_med: Optional[float] = None
  ```

#### (3) `backend/backtest/run_scenario_batch.py`
- バッチ完了時のサマリログ（line 603）を更新：
  - `df_runs['cagr']` の幾何平均と相加平均を併記。

#### (4) フロントエンド (`frontend/src/`)
- `frontend/src/api/backtest.ts`: `cagr_geo`, `cagr_med` などの型を追加。
- `frontend/src/pages/ScenarioDetailView.tsx`:
  - KPI カードのタイトルを `CAGR (10回幾何平均)` に更新。
  - ツールチップまたはサブテキストで `(中央値: XX.X% / 相加平均: XX.X%)` などを確認できるようにする。
- `frontend/src/pages/BacktestResultPage.tsx`: 同様にカードタイトルと詳細表示を更新。
- `frontend/src/pages/RegimeComparisonPage.tsx`: `cagr`（幾何平均）を優先して表示。

#### (5) スクリプト (`tmp/three_gen_comparison.py`)
- `agg()` 関数内の CAGR 集計を幾何平均に改修し、新旧世代（質主軸、税込CAGR主軸、税抜CAGR主軸）を同一基準で公平に比較できるようにする。

---

## 4. ユーザー確認事項

| # | 確認事項 | 推奨案 | 判断 |
| :--- | :--- | :--- | :--- |
| **4-1** | **主指標の選択** | 代表値 `summary.cagr` を **幾何平均（`cagr_geo`）** とし、中央値（`cagr_med`）と相加平均（`cagr_avg`）を併記するハイブリッド案 | **OK** |
| **4-2** | **UI 表示ラベル** | KPI カードの表示を `CAGR (10回幾何平均)` とし、サブ情報に最良/最悪/中央値を添える（正確性と透明性を重視） | **OK** |
| **4-3** | **作業ブランチ** | DB スキーマ変更や Parquet 変更を伴わない純粋なロジック改修のため、`main` で直接実施（単体テスト＋フロントエンドテストでリグレッション防止） | **OK** |

---

## 5. 実装順序と進捗チェックリスト

- [x] **Step 1: バックエンド集計ロジックとスキーマの改修**
  - [x] `backend/api/schemas.py` に `cagr_geo`, `cagr_med`, `final_capital_geo`, `final_capital_med` を追加
  - [x] `backend/api/backtest_router.py` の MC 集計部を改修（幾何平均・中央値・相加平均の算出および破産ガード）
  - [x] `backend/tests/api/test_backtest_api.py` に幾何平均・中央値の検証テストを追加
  - [x] pytest を実行して全テストのパスを確認（19 passed）
- [x] **Step 2: バッチログ表示の更新**
  - [x] `backend/backtest/run_scenario_batch.py` のログ出力を幾何平均主体に更新
- [x] **Step 3: フロントエンドの型・UI表示の更新**
  - [x] `frontend/src/api/backtest.ts` に型定義を追加
  - [x] `frontend/src/pages/ScenarioDetailView.tsx` の表示ラベルと詳細表示を更新
  - [x] `frontend/src/pages/BacktestResultPage.tsx` の表示ラベルを更新
  - [x] `frontend/src/pages/RegimeComparisonPage.tsx` の参照を更新
  - [x] `frontend/src/pages/__tests__/ScenarioDetailView.test.tsx` のテストを更新・パス確認（全50 passed）
- [x] **Step 4: 世代間比較スクリプトの更新と実データ検証**
  - [x] `tmp/three_gen_comparison.py`, `tmp/final_geo_scenario.py` の集計を幾何平均に改修して実行確認
- [x] **Step 5: ドキュメントの完了更新**
  - [x] `doc/issue_list.md:1208` のステータスを完了（`[x]`）にし、経緯を追記
  - [x] 本計画書を作業結果で更新し、`doc/completed/` へ移動

### 作業中メモ
- なし（全ステップ完了）

---

## 6. 検証プラン / 結果

### 6.1 反証 — この結論が誤りだとしたら、何が観測されるはずか
- **この結論が誤りだとしたら観測されるはず**:
  - 成長率にばらつきがある戦略において、幾何平均が相加平均よりも大きくなる（AM-GM 不等式に反する事象）。
  - 最終資産の幾何平均 $F_{\text{geo}}$ から逆算した CAGR と、各 run の $1 + CAGR$ の幾何平均から算出した CAGR が一致しない。
  - $1 + CAGR \le 0$ の破産 run が存在した際に API が `ValueError: math domain error` 等で 500 エラーを返す。
- **独立経路での確認**:
  - `numpy` / `math` を使った Python 独立スクリプトで現行全 13 戦略（`output/scenario/`）を検算し、すべての戦略で $CAGR_{\text{arith}} \ge CAGR_{\text{geo}}$ が成立することを確認済み（§1.2 参照）。
  - 破産ガードのユニットテストを独立テストケースとして実装して検証する。

### 6.2 転記の完全性
- **転記元**: [`doc/issue_list.md:1117-1135`](file:///d:/My%20Documents/Programing/stocktool/doc/issue_list.md#L1117-L1135)
- **元の指摘項目数**: 5項目（相加平均の弊害、2026-08 報告の型3 CAGR への注意、幾何平均/中央値の対応案、期待値 vs 典型値の設計判断、過去世代再集計の必要性）
- **本計画書の項目数**: 5項目すべて網羅
- **差分の説明**: なし

---

## 7. 途中発生した課題
- 計画段階: 年数が異なる run が同一グループ内に混在した場合（例: B2 の一部 run でテスト期間が異なっていた過去データ）、最終資産から全体年数で逆算すると歪む問題を発見。→ 各 run が自身の年数で算出した年率 CAGR（$1 + cagr_i$）から幾何平均を取る方式を採用することで解決。

---

## 8. スコープ外・残作業
- `doc/issue_list.md:1136` の「年次 `avg_pnl_pct`（相加平均）をフロントで `geo_pnl_pct` に切り替えるか判断する」は別 issue として扱う（本タスクは Monte Carlo 全体 CAGR の集計に集中する）。
