# バックエンド仕様書 (Backend Specification)

## 1. 概要
本プロジェクトのバックエンドは、株式およびインジケータデータの**「定期的な収集・算出バッチ（Pipeline）」**と、フロントエンド向けにデータを提供する**「APIサーバー」**の2つから構成されます。
データ分析に必要な指標計算を予めバッチで済ませてデータベースに蓄積することで、フロントエンドでの高速表示を実現しています。

## 2. ディレクトリ構成
全てのバックエンド資産は `backend/` フォルダ配下に整理されています。
*   **`backend/api/`**: FastAPI ベースのWeb APIサーバー
*   **`backend/db/`**: SQLAlchemy によるデータベースモデルと接続プール管理
*   **`backend/data_collection/`**: Google Spreadsheet 同期および `yfinance` 等を用いたデータ取得
*   **`backend/indicators/`**: 移動平均、ATR、RSI等のテクニカルおよび独自スコア計算ロジック
*   **`backend/scripts/`**: バッチ処理スクリプト (`update_pipeline.py`, `daily_sync_job.py` 等)

## 3. データベース設計 (SQLite)

構成データは、段階的（T1〜T6）に計算・生成されるテーブル群に保存されます。

### 3.1 T1: 銘柄メタデータ (`symbols`)
*   **特徴**: 株価データの主体となる銘柄そのものの定義。
*   **カラム**: `symbol_id` (PK), `ticker`, `exchange`, `name`, `category` (市場, セクタ, テーマ, 個別等), `asset_class`, `tags`, `active`

### 3.2 構成銘柄連携 (`theme_constituents`)
*   **特徴**: テーマや「仮想指数（Virtual Index）」を構成する銘柄の連携テーブル。
*   **カラム**: `theme_id` (FK), `symbol_id` (FK), `weight` (ウェイト)

### 3.3 T2: 日足データ (`daily_prices`)
*   **特徴**: yfinance等から取得した生の日足データ。仮想指数の場合は構成銘柄の平均騰落率から合成されます。
*   **カラム**: `symbol_id`, `date`, `open`, `high`, `low`, `close`, `volume`

### 3.4 T3: インジケータデータ (`indicators`)
*   **特徴**: T2の価格データを元に算出される各種テクニカル・モメンタム指標。
*   **主な指標**:
    *   **移動平均線**: `sma_5`, `sma_21`, `sma_50`, `sma_200`, `ema_21` など
    *   **特殊指標**: `td9` (TD Sequential), `atr_14`, `dist_sma50_atr` (ATR単位でのSMA50からの乖離度)
    *   **相対的強さ (RS)**: 対 SPY レラティブ・ストレングス、モメンタム、条件スコアなど
    *   **ファンダメンタルズ**: `market_cap`（時価総額：終値 × 発行済株式数）

### 3.5 T4: 相対評価データ (`relative_ranks`)
*   **特徴**: 同一カテゴリ内で特定指標（RSスコア等）を横並び比較し、パーセンタイル(0〜1)で順位付けしたデータ。
*   **カラム**: `symbol_id`, `date`, `group_name`, `indicator_name`, `percent_rank`

### 3.6 T5/T6: マーケットシグナル (`market_signals`, `fundamental_data`)
*   **T5**: S&P500の動向から算出される市場全体の方向性（`market_phase`）や「Follow Through Day（FTD）」などのシグナル。
*   **T6**: 銘柄に紐づく株数（shares）やEPS等の基礎データ。

## 4. バッチ処理フロー

日々のデータ更新は `run/run_daily_update.bat` から起動され、内部で `backend/scripts/daily_sync_job.py` および `backend/scripts/update_pipeline.py` が連動します。

1.  **Google Spreadsheet 同期**: 構成銘柄リスト、テーマ、監視対象をスプレッドシートから読み込み T1 を更新。
2.  **時系列データ取得**: `yfinance` を用いて不足分の日足データを取得し、T2 へ保存。
3.  **仮想指数合成**: VIRTUAL 指定のテーマに対し、タグで合致する構成銘柄を抽出し、その平均変動率から仮想的な基準価格を合成して T2 に登録。
4.  **テクニカル指標計算 (T3, インクリメンタル更新)**:
    - DBに保存済みの最終日を確認し、新規の日付分のみを計算・保存します。これにより、一意制約エラーを回避しつつ高速な日次更新を実現しています。
5.  **ファンダメンタルズ取得**: yfinance経由で発行済株式数を取得し、日ごとの `market_cap` を算出して保存。
6.  **相対ランク・マーケットフェーズ計算 (T4/T5)**: グループごとのパーセンタイルランク(T4)や S&P500ベースの相場フェーズ(T5)を決定。

### 4.1 管理用CLIオプション (`update_pipeline.py`)

データ破損時や特定の銘柄群のみを再計算したい場合、以下のオプションを指定して実行可能です。

- `--re-calculate`: 既存のインジケータ (T3) および Ranks (T4) の履歴を削除し、全期間のデータを最初から計算・再保存します。データの整合性が失われた際の復旧に使用します。
- `--category "カテゴリ名"`: 指定したカテゴリ（例: "セクタ,テーマ"）に属する銘柄のみを対象に処理を実行します。全件実行（約4000件以上）を避け、特定のグループのみを高速に更新・復旧する際に有効です。

## 5. バックエンド API 仕様 (FastAPI)

フロントエンドからのリクエストに応答するインターフェース群です。ポート `8000` で待機します。
全てのエンドポイントは `/api/` プレフィックスを持ちます。

*   **`GET /api/market_signal`**: 最新の T5 データ（市場フェーズや FTD）を取得。
*   **`GET /api/dashboard_data`**: 指定日付の Dashboard 用データパック（各種 T3, T4）を一括取得。
*   **`GET /api/chart_data/{ticker}`**: 指定銘柄のヒストリカルデータ群（OHLCV + 各種インジケータ）を取得。
    - **レスポンス構造**: `ChartResponse` 型。`data` (時系列配列) に加え、`metadata` (銘柄基本情報) および `themes` (関連テーマ情報の配列) を含みます。
    - **テーマ解決ロジック**: `theme_constituents` テーブルによる直接の紐付けに加え、`symbols` テーブルの `tags` カラムに含まれるカンマ区切りのタグもテーマとして解決し、リンク可能な情報を返却します。
*   **`GET /api/screener_data`**: スクリーナー用のカスタムフィルタ（「SMA50より上」「時価総額 10M以上」等）に合致する銘柄群と各指標値を返却。

## 6. バックテストエンジン

### 6.1 目的
スクリーナーの各種フィルタ条件セット（戦略）の有効性を、過去5年分のヒストリカルデータに対してシミュレーションし、**Expectancy**（期待値）と **Profit Factor**（総利益/総損失）を主軸に定量的に評価・比較する。パラメータの調整→再実行を繰り返す反復的なワークフローを前提とした設計。

### 6.2 ディレクトリ構成
```
backend/backtest/
├── backtest_config.toml   # 戦略パラメータセット定義
├── backtest_runner.py     # CLI エントリーポイント
├── backtest_screener.py   # 日付ごとのシグナルスキャナー
├── backtest_simulator.py  # トレードシミュレーター（出口ルール適用）
├── backtest_report.py     # 結果集計・比較テーブル出力
└── results/               # 実行結果出力先
```

### 6.3 設定ファイル (`backtest_config.toml`)
TOML形式でパラメータセットを定義。`[[strategy]]` 配列を追加するだけで新しい条件セットを試行可能。

```toml
[general]
start_date = "2021-03-26"
end_date   = "2026-03-25"
failsafe_max_days = 120

[[strategy]]
name = "A_momentum_breakout"
min_1d_gain_pct = 4.0
min_vol_surge_21 = 1.5
min_adr_pct_21 = 4.0
max_dist_sma50_atr = 6.0
min_market_cap = 1e9
rs_rank_21_gt_63 = true
```

### 6.4 出口ルール（固定）

| ルール | 条件 |
|---|---|
| **エントリー** | スクリーン該当日の終値で買い |
| **損切り** | エントリー価格から -8% |
| **1/3利確** | +20%超え or SMA50/ATR% >= 8 → 残りの損切りラインをエントリー価格に引き上げ |
| **全利確** | EMA21を終値で2日連続下回る or SMA50/ATR% >= 11 |
| **タイムストップ** | 7営業日の高値-安値 < 1ATR → 強制退出 |
| **フェイルセーフ** | 120営業日で未決済 → 強制退出 |

### 6.5 評価指標

| 指標 | 説明 |
|---|---|
| **Expectancy** | 1トレードあたりの期待値 = (WR × AvgWin) - ((1-WR) × AvgLoss) |
| **Profit Factor** | 総利益 / 総損失 |
| **Win Rate** | 勝ちトレード数 / 全トレード数（補助指標） |
| **Avg Holding Days** | 平均保有日数（補助指標） |

### 6.6 パフォーマンス方針
5年 × 約250営業日 × 複数戦略のスクリーン実行が必要となるため、全期間の Indicator + DailyPrice + RelativeRank データを**事前にメモリへ一括ロード**し、pandas 上でフィルタリング処理を行う。DB への都度クエリは行わない。

### 6.7 実行方法
```bash
# 全戦略実行
python backend/backtest/backtest_runner.py

# 特定戦略のみ
python backend/backtest/backtest_runner.py --strategy A_momentum_breakout

# 設定ファイル指定
python backend/backtest/backtest_runner.py --config path/to/custom_config.toml
```
