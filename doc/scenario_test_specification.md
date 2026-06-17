# シナリオテスト仕様書 (Scenario Test Specification)

本ドキュメントは、`stocktool` における**ポートフォリオレベルのシナリオテストエンジン**の設計仕様を定義する。
既存のバックテストエンジン（単一戦略・無限資金・加算PnL）とは異なり、有限資金・複利ベースで複数スクリーナー条件を統合評価し、市場環境に応じた動的ポジション管理を行うシミュレーションエンジンである。

---

## 1. 目的

1. **スクリーナーの統合的な妥当性検証**: 複数のスクリーナープリセット（`screener_presets.toml` の Rise - Check グループ）を組み合わせ、「多くの条件に合致する銘柄ほど優秀か」をポートフォリオ運用レベルで検証する。
2. **Market Trend Score 計算式の最適化**: Market Trend Score の4構成要素（SPYトレンド、市場の幅、Momentum Ratio、VIX）の重み配分を変数化し、シナリオテスト結果を目的関数として最適な重みを探索する。
3. **実運用シミュレーション**: 有限資金・ポジション数制限・市場環境連動のキャッシュ管理を伴う、現実的な運用成績の推計。

---

## 2. 既存バックテストとの比較

| 項目 | 既存バックテスト | シナリオテスト |
| :--- | :--- | :--- |
| **資金モデル** | 無限（全シグナルに無条件でエントリー） | 有限（デフォルト $100,000） |
| **PnL 計算** | 加算ベース（Additive） | 複利ベース（Compounding） |
| **戦略** | 単一戦略（`[[strategy]]` 1件ずつ） | 複数プリセット統合（Voting/Ensemble） |
| **ポジション管理** | なし（同時保有制限なし） | あり（最大ポジション数・資金分割） |
| **市場環境連動** | なし | あり（Market Trend Score でエクスポージャー制御） |
| **出口ルール** | `backtest_simulator.py` | **同一関数を共用** |
| **評価基準** | Expectancy, Alpha, PF | 最終資産額, CAGR, SPY比較 |

---

## 3. アーキテクチャ

### 3.1 ディレクトリ構成

```
backend/backtest/
├── backtest_config.toml         # 既存バックテスト設定（変更なし）
├── backtest_simulator.py        # 出口ルール（シナリオテストと共用）
├── scenario_config.toml         # 【新設】シナリオテスト設定
├── scenario_runner.py           # 【新設】シナリオテスト メインエントリーポイント
├── scenario_portfolio.py        # 【新設】ポートフォリオ管理（資金・ポジション・エクスポージャー）
├── scenario_scorer.py           # 【新設】マルチプリセットスコアリング
├── scenario_market_score.py     # 【新設】Market Trend Score オンザフライ再計算
├── scenario_report.py           # 【新設】結果集計・ベンチマーク比較・レポート生成
├── cache/                       # 既存キャッシュ（共用）
└── results/                     # 結果出力先（共用）
```

### 3.2 データソース

既存のバックテスト Parquet キャッシュ（`backend/backtest/cache/`）を共用する。追加で必要なデータは以下の通り：

| データ | 用途 | 取得元 |
| :--- | :--- | :--- |
| `close`, `sma_50` (全銘柄) | Market Breadth 再計算 | indicators キャッシュ（既存） |
| `change_1d_pct` (全銘柄) | Momentum Ratio 再計算 | indicators キャッシュ（既存） |
| SPY の `close`, `ema_21`, `sma_50`, `sma_200` | SPY Trend 再計算 | indicators キャッシュ（既存） |
| VIX の `close` | VIX スコア再計算 | daily_prices キャッシュ（既存） |
| `screener_presets.toml` | プリセット条件読み込み | `data/screener_presets.toml` |

> **NOTE**: VXV（CBOE 3-Month Volatility Index）は将来的に VIX スコアの補完指標として追加検討する。現行スコープでは VIX のみを使用。

---

## 4. 設定ファイル (`scenario_config.toml`)

```toml
# ============================================================
# Scenario Test Configuration
# ============================================================

[general]
initial_capital = 100000.0       # 初期資金 (USD)
start_date = "2022-01-01"
end_date = "2026-03-26"
max_positions = 8                # 最大同時保有数
max_buy_per_day = 2              # 1日あたりの最大購入銘柄数
stop_loss_pct = -8.0             # 固定損切率 (%) ※ポジションサイジングのリスク計算にも使用

# --- スクリーナープリセット設定 ---
[presets]
source = "data/screener_presets.toml"
group = "Check"                  # Rise セクションの group="Check" のみを対象
# 各プリセットの重み（キー = プリセットのid, 値 = 重み）
# 未定義のプリセットはデフォルト重み 1.0 が適用される
[presets.weights]
# thema_momentum = 1.0
# momentum_breakout = 1.0
# check_1d_gain = 1.0
# (初期値は全て 1.0 のため、コメントアウトで省略可能)
```

> **NOTE: `special` フィールドの廃止と boolean フィルタの統合について**
>
> `screener_presets.toml` において、以前は `special = "theme_rs21_gt_63"` などのディスパッチ用キーを用いていた複合計算ロジック（RRG系やRS Rank系など）は、**`special` キー自体が完全に廃止**されました。
> 
> 現在は、これらすべての特殊条件も `[rise.filters]` / `[fall.filters]` セクション内の boolean フラグ（例：`rrg_improving_in = true`、`is_rs_ratio_rank_e21_gt_e63 = true`）として定義するように統一されました。
> 
> バックエンド内では、`backend/backtest/backtest_screener.py`（pandas版）および `backend/api/routers.py`（SQLAlchemy版）に定義された boolean フィルタハンドラ（`BOOLEAN_FILTER_HANDLERS`）を介して、それぞれ透過的にフィルタリングが適用されます。

```toml

# --- タイブレーク設定 ---
[tiebreak]
sort_column = "rs21_rank"        # スコア同点時のソート基準
sort_ascending = false           # false = 降順（高い方が優先）

# --- エクスポージャー管理 ---
[exposure]
full_bet_score = 80              # このスコア以上で最大ポジション数
full_cash_score = 20             # このスコア以下でポジション数 = 0
max_position_change_per_day = 2  # 1日あたりの最大ポジション増減数（ダンパー）
reduction_priority = "unrealized_loss"  # 削減時の優先順位 ("unrealized_loss")

# --- Market Trend Score 重み (合計 = 100) ---
[market_score_weights]
spy_trend = 25.0                 # SPYトレンド (EMA21, SMA50, SMA200上抜け, SMA200上昇)
market_breadth = 25.0            # 市場の幅 (SMA50上抜け率)
momentum_ratio = 25.0            # Momentum Ratio (前日比プラス率)
vix = 25.0                       # VIX (12-35 線形補間)

# --- 出口ルール (backtest_config.toml と同一構造) ---
[exit_rules]
stop_loss_pct = -8.0
partial_take_profit_pct = 20.0
partial_take_profit_sma50_atr = 8.0
partial_ratio = 0.333
full_exit_ema21_consecutive_days = 2
full_exit_sma50_atr = 11.0
time_stop_days = 7
```

---

## 5. シミュレーションロジック

### 5.1 全体フロー（日次ループ）

```
初期化: capital = initial_capital, cash = initial_capital, positions = [], history = []

FOR each trading_day in [start_date ... end_date]:

  1. Market Trend Score を再計算（カスタム重みで）
  2. 目標ポジション数を算出（エクスポージャー管理）
  3. 保有ポジションの出口判定（既存出口ルール適用）
     → 損切・利確・トレンド崩れ等で決済 → cash に戻す
     → 決済時に capital を確定損益で更新（capital += realized_pnl）
  4. エクスポージャー調整（ポジション過多なら含み損順に強制売却）
  5. 新規購入判定（現在のポジション数 < 目標ポジション数の場合のみ）
     a. 全プリセットでスクリーニング実行（Rise - Check）
     b. マルチプリセットスコアリング（Voting）
     c. 購入可能枠（目標 - 現在）とキャッシュ（cash >= position_size）の確認
     d. 上位銘柄を購入（最大 max_buy_per_day 件）
  6. 日次スナップショット記録（資産推移・保有状況）

END FOR
```

### 5.2 マルチプリセットスコアリング (`scenario_scorer.py`)

#### スコア算出
```python
def calculate_signal_scores(
    day_data: pd.DataFrame,       # その日の全銘柄データ
    presets: list[dict],          # Rise-Check プリセット群
    weights: dict[str, float],   # プリセットID → 重み
    held_tickers: set[str],      # 既に保有中のティッカー
) -> pd.DataFrame:
    """
    各銘柄に対し、ヒットしたプリセット数 × 重みの合計スコアを算出。
    既に保有中の銘柄は除外する。
    戻り値: [ticker, score, rs21_rank, ...] の DataFrame（スコア降順）
    """
```

#### タイブレーク
```python
def rank_candidates(
    scored: pd.DataFrame,
    sort_column: str = "rs21_rank",
    sort_ascending: bool = False,
) -> pd.DataFrame:
    """
    スコアが同点の銘柄を sort_column で順位付け。
    戻り値: 購入優先度順にソートされた DataFrame
    """
```

> **IMPORTANT**: スコアリングとタイブレークは**独立した純粋関数**として実装し、単体テストで期待値検証を行いやすくする。

### 5.3 ポートフォリオ管理 (`scenario_portfolio.py`)

#### ポジションサイジングと資金管理
```
position_size = capital / max_positions
```
- `capital` は**確定損益ベースで更新される運用資金**。含み益・含み損は反映せず、利確・損切等で決済が確定した時点の実現損益のみを反映する。
  - 初期値: `capital = initial_capital`
  - 更新: `capital += realized_pnl`（決済時）
- `position_size` は `capital` に連動して変化する。勝ち続ければサイズが大きくなり、負けが続けばサイズが縮小する。
- 購入判定は `cash >= position_size` で行う。

#### 部分利確と再投資
- 部分利確（+20%到達時に保有の1/3を売却）で得た資金は `cash` に加算され、同時に `capital` も確定利益分だけ更新される。
- `cash >= position_size` が成立した時点で、新規購入の資金源として利用可能。

#### エクスポージャー管理

```python
def calculate_target_positions(
    score: float,
    max_positions: int,
    full_bet_score: float,     # デフォルト: 80
    full_cash_score: float,    # デフォルト: 20
) -> int:
    """
    Market Trend Score から目標ポジション数を算出。
    score >= full_bet_score → max_positions
    score <= full_cash_score → 0
    その間は線形補間。
    """
    if score >= full_bet_score:
        return max_positions
    if score <= full_cash_score:
        return 0
    ratio = (score - full_cash_score) / (full_bet_score - full_cash_score)
    return round(ratio * max_positions)
```

```python
def apply_damper(
    target: int,
    current: int,
    max_change: int,           # デフォルト: 2
) -> int:
    """
    急激なポジション増減を抑制するダンパー。
    |target - current| > max_change の場合、max_change 分だけ近づける。
    """
    if target > current:
        return min(target, current + max_change)
    elif target < current:
        return max(target, current - max_change)
    return current
```

#### ポジション削減（強制売却）

```python
def select_positions_to_cut(
    positions: list[Position],
    num_to_cut: int,
    current_prices: dict,
) -> list[Position]:
    """
    含み損の大きい（最も悪い）順に num_to_cut 件を選択。
    含み損 = (current_price - entry_price) / entry_price * 100
    """
```

### 5.4 Market Trend Score 再計算 (`scenario_market_score.py`)

パイプライン（T5）で事前計算された値を使用せず、**T3/T4 データからオンザフライで再計算**する。これにより重み配分の変更実験が可能。

```python
def calculate_market_trend_score(
    date: datetime.date,
    spy_row: dict,                  # SPY の T3 指標（close, ema_21, sma_50, sma_200 等）
    spy_row_prev: dict,             # 前日の SPY データ (SMA200 上昇判定用)
    all_indicators: pd.DataFrame,   # 当日の全銘柄 T3 (close, sma_50, change_1d_pct)
    vix_close: float,               # 当日の VIX 終値
    weights: dict,                  # {spy_trend, market_breadth, momentum_ratio, vix}
) -> tuple[float, dict]:
    """
    戻り値: (total_score, sub_scores_dict)
    sub_scores_dict: {"spy_trend": x, "market_breadth": y, "momentum_ratio": z, "vix": w}
    """
```

#### サブスコア計算仕様（既存 T5 ロジックと同一）

| 構成要素 | 配点基準 (正規化前) | 計算式 |
| :--- | :--- | :--- |
| **SPY Trend** | 4項目 × 各1.0pt = 4.0pt 満点 | ①close>EMA21, ②close>SMA50, ③close>SMA200, ④SMA200上昇(5日前比較) |
| **Market Breadth** | 0.0〜1.0 (比率) | `個別銘柄のうち close > sma_50 の割合` |
| **Momentum Ratio** | 0.0〜1.0 (比率) | `個別銘柄のうち change_1d_pct > 0 の割合` |
| **VIX** | 0.0〜1.0 (正規化値) | `clamp((35 - vix) / (35 - 12), 0, 1)` |

最終スコア:
```
score = (spy_trend / 4.0) * w.spy_trend
       + breadth * w.market_breadth
       + momentum * w.momentum_ratio
       + vix_norm * w.vix
```
ここで `w.spy_trend + w.market_breadth + w.momentum_ratio + w.vix = 100`

---

## 6. ベンチマーク比較

シナリオテストの結果を、以下のベンチマークおよびインデックス指標と比較する。

### 6.1 SPY 一括購入 (Lump Sum)
- 期間初日に `initial_capital` 全額で SPY を購入し、期間末日に評価。
- `final_value = initial_capital * (spy_close_end / spy_close_start)`

### 6.2 SPY 定期積立 (DCA: Dollar-Cost Averaging)
- 毎月初営業日に均等額（`initial_capital / 期間月数`）を SPY に投資。
- 各購入分の株数を累積し、期間末日の SPY 終値で評価。

### 6.3 SPY + Market Phase タイミング
- SPY のみを対象とし、シナリオテストと**同一の Market Trend Score とエクスポージャーロジック**で売買。
- 「SPY × タイミング力」を分離評価することで、スクリーナーの銘柄選定力の寄与度を測定。

### 6.4 主要指数ベンチマーク (QQQ / TQQQ / SOXL)
- ポートフォリオの資産推移 (Equity Curve) を、テック系主要インデックスおよびそのレバレッジ商品と比較可能にするため、以下の指数との動的スケーリング比較をAPIおよびフロントエンドでサポートする。
  - **QQQ** (Nasdaq 100 ベンチマーク)
  - **TQQQ** (Nasdaq 100 レバレッジ3倍)
  - **SOXL** (Direxion デイリー半導体株ブル3倍)
- これらは開始日の評価額をシナリオの初期資金にスケーリングし、日次の資産推移としてグラフ上に重ねて描画される。

### 6.5 出力指標

| 指標 | 説明 |
| :--- | :--- |
| `final_capital` | 期間終了時の総資産（cash + 保有時価） |
| `total_return_pct` | `(final_capital - initial_capital) / initial_capital * 100` |
| `cagr` | 年率複利リターン |
| `max_drawdown_pct` | 期間中の最大ドローダウン（時価ベース） |
| `sharpe_ratio` | リスク調整済みリターン（日次リターンから算出） |
| `total_trades` | 総取引数 |
| `win_rate` | 勝率 |
| `avg_holding_days` | 平均保有日数 |
| `spy_lump_return_pct` | SPY一括購入のトータルリターン率 |
| `spy_dca_return_pct` | SPY DCA のトータルリターン率 |
| `spy_timing_return_pct` | SPY+Phase切替のトータルリターン率 |
| `vs_spy_lump_ratio` | シナリオ / SPY一括 のリターン比率（例: 1.5 = 1.5倍） |
| `vs_spy_dca_ratio` | シナリオ / SPY DCA のリターン比率 |
| `vs_spy_timing_ratio` | シナリオ / SPY+Phase のリターン比率 |
| `qqq_equity` | 各日程における QQQ ベンチマーク評価額 (スケーリング後) |
| `tqqq_equity` | 各日程における TQQQ ベンチマーク評価額 (スケーリング後) |
| `soxl_equity` | 各日程における SOXL ベンチマーク評価額 (スケーリング後) |
| `exit_reasons` | 決済理由別内訳（回数・比率・平均損益・保有日数）の集計辞書データ |

### 6.5 売買ログ CSV 出力

各シナリオテスト実行時に、全取引の詳細ログを CSV ファイルとして `backend/backtest/results/` に出力する。

**ファイル名**: `scenario_trades_YYYYMMDD_HHMMSS.csv`

| カラム | 説明 |
| :--- | :--- |
| `ticker` | 銘柄ティッカー |
| `buy_date` | 購入日 |
| `buy_price` | 購入価格（終値） |
| `sell_date` | 売却日 |
| `sell_price` | 売却価格 |
| `pnl_pct` | 損益率 (%) |
| `exit_reason` | 売却理由: `STOP_LOSS`, `TAKE_PROFIT_TRIM`, `TAKE_PROFIT_FULL`, `EMA21_BREAK`, `TIME_STOP`, `EXPOSURE_REDUCTION`, `END_OF_PERIOD` |
| `capital` | 売却確定後の運用資金（確定損益ベース。含み益・含み損は含まない） |

---

## 7. 将来拡張（現行スコープ外）

以下の機能は初期実装に含めず、シナリオテストの基盤が安定した後に段階的に追加する。

- [ ] **Overhead Sign による利確**: Rise - Overhead sign にヒットした保有銘柄を利確候補として扱う
- [ ] **Fall Warning による購入フィルタ**: Fall - Warning にヒットした銘柄を購入対象から除外する
- [ ] **Follow Through Day (FTD) / Distribution Day (DD) の統合**: 市場シグナル（FTD, DD）をエクスポージャー管理の追加判断材料として組み込む
- [ ] **ATRベースの動的ポジションサイジング**: ボラティリティに応じた資金配分
- [ ] **VXV/VIX 比率の Market Score 統合**: VXV を第5の構成要素として追加
- [ ] **Optuna による Market Score 重み自動最適化**: 既存 Optuna インフラとの統合

---

## 8. 実行方法

```bash
# 基本実行（デフォルト設定で全期間）
python backend/backtest/scenario_runner.py

# 期間の上書き
python backend/backtest/scenario_runner.py --start-date 2023-01-01 --end-date 2025-12-31

# Market Score の重み変更
python backend/backtest/scenario_runner.py --spy-trend 30 --breadth 30 --momentum 20 --vix 20

# キャッシュのリフレッシュ（既存バックテストキャッシュを更新）
python backend/backtest/scenario_runner.py --refresh-cache
```

---

## 9. テスト計画 (TDD)

### 9.0 実装方針

> **IMPORTANT**: 全モジュールの実装は**適切な粒度の関数に分割**し、各関数が独立して単体テスト可能な純粋関数（副作用なし）となるように設計すること。特に以下の点を厳守する：
> - スコアリング、タイブレーク、エクスポージャー計算、ポジション削減選択は**それぞれ独立した関数**として実装
> - 日次ループの各ステップ（Market Score 再計算、出口判定、購入判定等）も可能な限り関数化
> - 特殊フィルタ（RRG, theme_rs21_gt_63 等）は `backend/indicators/screener_filters.py` に集約し、backtest / scenario の両方から呼び出せるようにする

### 9.1 テストファイル配置

| テスト対象 | テストファイル |
| :--- | :--- |
| `scenario_scorer.py` | `backend/tests/backtest/test_scenario_scorer.py` |
| `scenario_portfolio.py` | `backend/tests/backtest/test_scenario_portfolio.py` |
| `scenario_market_score.py` | `backend/tests/backtest/test_scenario_market_score.py` |
| `scenario_report.py` | `backend/tests/backtest/test_scenario_report.py` |
| `indicators/screener_filters.py` | `backend/tests/indicators/test_screener_filters.py` |

### 9.2 重点テスト項目

#### scenario_scorer.py
- [ ] 単一プリセットにヒットした銘柄のスコアが 1.0 × 重みであること
- [ ] 複数プリセットにヒットした銘柄のスコアが合算されること
- [ ] 重み付けが正しく適用されること（1:2:1 等の非均等重み）
- [ ] 既に保有中の銘柄がスコア結果から除外されること
- [ ] タイブレーク（RS Rank 21 降順）が正しく動作すること
- [ ] `filters` 内の boolean カスタムフィルタ（RRG系, RS Rank比較等）を持つプリセットが正しく適用されること

#### scenario_portfolio.py
- [ ] `calculate_target_positions`: Score=100 → max_positions, Score=0 → 0, Score=50 → 線形補間
- [ ] `apply_damper`: ±2 を超える変動が抑制されること
- [ ] `select_positions_to_cut`: 含み損の大きい順に正しく選択されること
- [ ] 部分利確後の cash と capital の更新が正しいこと
- [ ] cash が position_size に達した時のみ新規購入が許可されること
- [ ] 現在のポジション数 < 目標ポジション数の場合のみ新規購入が実行されること
- [ ] `capital` が確定損益のみで更新されること（含み益は反映されない）

#### scenario_market_score.py
- [ ] 全項目が満点の場合、スコア = 100 であること
- [ ] 全項目が最低の場合、スコア = 0 であること
- [ ] 重みの変更がスコアに正しく反映されること
- [ ] VIX = 12.0 → VIXサブスコア満点、VIX = 35.0 → 0点であること

#### indicators/screener_filters.py (共通関数)
- [ ] `filter_theme_rs21_gt_63`: テーマRS21 > RS63 の判定が正しいこと
- [ ] `filter_rs_rank_21_gt_63`: RS Rank 21 > 63 の比較が正しいこと
- [ ] `filter_rrg_leading_in`: RRG Leading 転換の判定が正しいこと
- [ ] `filter_rrg_improving_in`: RRG Improving 転換の判定が正しいこと
- [ ] `filter_rrg_lagging_in`: RRG Lagging 転換の判定が正しいこと

---

## 更新履歴
- 2026-06-17: 決済（売却）理由別統計機能を追加。各決済理由の回数・比率・平均損益・平均保有日数をシミュレーションレポート（`scenario_summary.json`）に集計・記録し、フロントエンドに「Exit Reason Statistics」セクションとして統合表示する機能を追加。
- 2026-06-17: `special` キーを廃止し、RRG系やRS Rank系のカスタムフィルタを `filters` 内の boolean キーに統一（TDDによるリファクタリングの実施）
- 2026-06-09: QQQ/TQQQ/SOXL ベンチマークとの資産推移スケーリング比較機能の追加
- 2026-05-09: コメントフィードバック反映（売買ログCSV、SPYリターン比率、special共通化、FTD/DD、関数粒度方針、購入前提条件、確定損益capital）
- 2026-05-09: 初版作成

