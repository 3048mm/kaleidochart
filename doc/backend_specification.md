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

株価データの主体となる銘柄そのものの定義です。Googleスプレッドシートから同期されます。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。内部的なID管理に使用。 |
| `ticker` | STRING | 銘柄のティッカーシンボル（例: AAPL, SPY, _PHNC_）。 |
| `exchange` | STRING | 取引所コード（NYSE, NASDAQ 等）。仮想インデックスは `VIRTUAL`。 |
| `name` | STRING | 銘柄名称。 |
| `category` | STRING | 銘柄の分類（市場, 指標, セクタ, テーマ, 個別）。 |
| `asset_class` | STRING | 資産クラス・属性（Industryなど）。 |
| `theme_type` | STRING | 詳細タイプ（`etf`: 実在ETF, `virtual`: 仮想指数, `sector`: セクタ指標）。 |
| `tags` | STRING | カンマ区切りの属性タグ。仮想テーマの構成銘柄紐付けに利用。 |
| `active` | SMALLINT| ソフトデリートフラグ（1:有効, 0:無効）。 |

### 3.2 構成銘柄連携 (`theme_constituents`)

テーマETFや「仮想指数（Virtual Index）」を構成する個別銘柄群を管理する連携テーブルです。バックテストの「テーマモメンタム」条件などで利用されます。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `theme_id` | INTEGER | `symbols.id` への外部キー。親となるテーマ/指数のID。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。構成銘柄（個別銘柄等）のID。 |
| `weight` | FLOAT | 構成比率（現在は主に 1.0 = 均等ウェイト）。 |

**紐付けロジック:**
- 仮想テーマ（exchangeが `VIRTUAL` または ticker が `_` で囲まれている等）の場合、`symbols.tags` にテーマ名（PHNC等）を含む銘柄を自動的に抽出して紐付けます。
- 実在するETF（GDX, WCLD等）についても、同様にタグベースで構成銘柄を特定し、テーマ全体の強さを個別銘柄に波及させるために利用されます。

### 3.3 T2: 日足データ (`daily_prices`)

yfinance等から取得した生の日足データ、または合成された仮想指数の価格データです。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 |
| `date` | DATE | 取引日。 |
| `open` | FLOAT | 始値。当日騰落率（`gain_1d_pct`）の計算に使用。 |
| `high` | FLOAT | 高値。タイムストップやチャネルブレイク、ATRの計算に使用。 |
| `low` | FLOAT | 安値。損切り判定やATRの計算に使用。 |
| `close` | FLOAT | 終値。すべてのテクニカル指標計算のベース。 |
| `volume` | BIGINT | 出来高。出来高急増（`vol_surge`）の計算に使用。 |

### 3.4 T3: インジケータデータ (`indicators`)

T2の価格データを元に算出される各種テクニカル・モメンタム指標です。

| カラム名 | 型 | 説明・用途 | 計算式 / 論理 |
| :--- | :--- | :--- | :--- |
| `sma_n` | FLOAT | 5, 21, 50, 63, 150, 200日単純移動平均。トレンド判定に使用。 | `close.rolling(n).mean()` |
| `ema_n` | FLOAT | 5, 21, 50, 63, 150, 200日指数平滑移動平均。TradingView互換。 | `calculate_ema_tv(close, n)` (SMAをシードとした再帰計算) |
| `atr_14` | FLOAT | ボラティリティ指標（Average True Range）。 | 14日間の True Range の平均 |
| `atr_pct_14` | FLOAT | Closeに対するATRの割合(%)。 | `(atr_14 / close) * 100` |
| `adr_pct_21` | FLOAT | 21日間の平均日次レンジ(%)。ボラティリティの強さ判定に使用。 | `mean( (high - low) / low * 100 )` |
| `dist_sma50_atr` | FLOAT | SMA50からの距離をATRで正規化した値。 | `((close / sma_50 * 100) - 100) / atr_pct_14` |
| `td9` | INT | Tom DeMark Sequential。過熱感の判定に使用。 | 4日前の終値との比較による 1〜9 のカウントアップ/ダウン |
| `market_cap` | FLOAT | 日次時価総額（米ドル）。スクリーナーでのサイズ制限に使用。 | `close * shares_outstanding` |
| `relative_strength_spy` | FLOAT | SPYに対する単純相対強度。 | `close / spy_close` |
| `rs_condition_n` | FLOAT | RSのトレンド強度 (14, 21, 63)。 | `RS / SMA(RS, n)` |
| `rs_ema_n` | FLOAT | RSの平滑化 (14, 21, 63)。RRG計算の前処理に使用。 | `calculate_ema_tv(relative_strength_spy, n)` |
| `rs_ratio_n` | FLOAT | RSの正規化スコア (14, 21, 63)。RRGのX軸（Ratio）に相当。ただしオフセット100が無いので 0センター | `(rs_ema_n - mean(rs_ema_n, n)) / std(rs_ema_n, n)` |
| `rs_roc_ema_n` | FLOAT | RS-Ratioの14日間変化率(ROC)の平滑化。 | `calculate_ema_tv(ROC(rs_ratio_n + 100), n)` |
| `rs_momentum_n` | FLOAT | RS-Ratioの勢い (14, 21, 63)。RRGのY軸（Momentum）に相当。ただしオフセット100が無いので 0センター | `(rs_roc_ema_n - mean(rs_roc_ema_n, n)) / std(rs_roc_ema_n, n)` |
| `vol_surge_21` | FLOAT | 出来高急増倍率。 | `volume / mean(volume, 21)` |
| `rel_vol_vs_spy_21` | FLOAT | SPYに対する出来高の相対的な強さ。 | `vol_surge_21 / spy_vol_surge_21` |
| `pct_from_52w_high` | FLOAT | 52週（252日）高値からの下落率(%)。 | `(close - max(high, 252)) / max(high, 252) * 100` |
| `trend_template_ok` | SMALLINT | ミネルヴィニのトレンドテンプレート適合フラグ（1:適合, 0:不適合）。 | 右記5条件: ①close>sma50, ②sma50>sma150, ③sma150>sma200, ④sma200上昇中(20日前比), ⑤52週高値から30%以内 |

### 3.5 T4: 相対評価データ (`relative_ranks`)

同一カテゴリ内で特定指標（RSスコア等）を横並び比較し、パーセンタイル(0〜1)で順位付けしたデータです。

| カラム名 | 型 | 説明・用途 | 計算式 / 論理 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 | |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 | |
| `date` | DATE | 評価日。 | |
| `group_name` | STRING | 比較対象のグループ（`個別`, `テーマ` などの種類ごと）。 | |
| `indicator_name` | STRING | ランク付けの対象指標名。<br>現在、以下の T3 指標が対象：<br> - `relative_strength_spy`<br> - `rs_ratio_14 / 21 / 63`<br> - `rs_momentum_14 / 21 / 63`<br> - `rs_condition_14 / 21 / 63` | |
| `percent_rank` | FLOAT | そのグループ内でのパーセンタイル順位 (0.00 〜 1.00)。 | `group.rank(pct=True)`。1.0が最強。 |

### 3.6 T5: マーケットシグナル (`market_signals`)

S&P500（SPY）の動向や市場全体の統計から算出される、市場フェーズと健康度の指標です。

| カラム名 | 型 | 説明・用途 | 計算式 / 論理 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 | |
| `date` | DATE | 評価日。 | |
| `spy_above_sma200` | SMALLINT | 長期トレンド判定（1:SMA200より上）。 | |
| `distribution_days` | INTEGER | 過去25日間のディストリビューション・デーの数。 | 下落(-0.2%以下)かつ出来高増の日数 |
| `follow_through_day` | SMALLINT | フォロースルーデーの発生フラグ（1:発生）。 | 下落局面からの反発（+1.7%以上かつ出来高増） |
| `market_phase` | STRING | 市場のフェーズ（BULL, CORRECTION, BEAR 等）。 | SPYのトレンドと売りの圧力により判定 |
| `market_trend_score` | FLOAT | 市場全体の健康度を 0〜100 で数値化したもの。 | 右記4項目の加重合計: ①SPYトレンド(25), ②市場の幅(25), ③Momentum Ratio(25), ④VIX(25) |

**Market Trend Score の内訳:**
- **SPYトレンド (25pt)**: 終値が EMA21, SMA50, SMA200 を上回っているか。
- **市場の幅 (25pt)**: SMA50を上回っている銘柄の割合。
- **Momentum Ratio (25pt)**: 市場全体の RS Momentum のポジティブ率。
- **VIXスコア (25pt)**: VIXが12（25pt）〜35（0pt）の間で線形補完。

#### 3.6.1 各定性的シグナルの定義
ダッシュボードに表示される市場の健康状態は、S&P500 (SPY) の日足データから以下のロジックで判定されます。

*   **Distribution Day (売り抜け日)**: 機関投資家の資金流出を示唆する警戒シグナル。
    *   **判定条件**: `SPYの終値が前日比で -0.2% 以下（下落）` かつ `出来高が前日より増加` した場合。
    *   **カウント**: `distribution_days` カラムは、直近25営業日中に発生した上記シグナルの**合計日数**を記録します。
*   **Follow Through Day (FTD; フォロースルー日)**: 下落トレンドからの反転を示唆する買いシグナル。
    *   **判定条件**: `SPYの終値が前日比で +1.7% 以上（上昇）` かつ `出来高が前日より増加` した場合。（※バックエンドコード上は、市場の底打ち確認日として記録されます）
*   **Market Phase (市場局面)**: 以下の優先順位に従って判定されます。
    1.  **BULL (強気相場)**: `SPY > SMA200` かつ `Distribution Days <= 3`（健全な上昇トレンド）
    2.  **CORRECTION (調整局面)**: `SPY > SMA200` かつ `Distribution Days >= 5`（上昇トレンドだが売り圧力が強まっている）
    3.  **RALLY_ATTEMPT (反発の試み)**: `SPY < SMA200` だが当日にFTDが発生した、あるいは `SMA200` 自体はまだ上向きを維持している。
    4.  **BEAR (弱気相場)**: `SPY < SMA200` かつ `SMA200` も下落傾向にある。
    *   *(上記以外の曖昧な状態(例: SPY>SMA200 だが Dist Daysが4) については一時的なバッファとして BULL が維持されます)*

### 3.7 Market Trend Score (0-100) 算出ロジック
市場の過熱感や健全性を定量化するため、以下の4つの独立した構成要素に基づき、各要素最大25点、合計100点満点で算出します：
1.  **SPY Trend (25pts)**: 以下の4項目（各6.25点）が真であれば加算。
    - Price > EMA21
    - Price > SMA50
    - Price > SMA200
    - SMA200 is rising (過去5日間平均の比較)
2.  **Market Breadth (25pts)**: 個別銘柄（category='個別' 且つ active=1）のうち、終値が 50日移動平均線（SMA50）を上回っている銘柄の比率を 0〜25 点にスケーリング。
3.  **Momentum Ratio (25pts)**: 全体銘柄のうち、前日比でプラスとなった銘柄の比率を 0〜25 点にスケーリング（短期的な買いの勢いを測定）。
4.  **Volatility (25pts)**: VIX 指数の絶対値による評価。
    - 12.0以下であれば 25点満点。
    - 35.0以上であれば 0点。
    - その間は線形補間（Greedで満点、Fearで減点）。

### 3.8 ウォッチリスト (`watchlist`)
*   **特徴**: ユーザーが注目する銘柄とその登録基準日を管理するテーブル。T1～T6 のパイプラインとは独立した「ユーザー操作データ」。
*   **カラム**:
    *   `id` (PK, INTEGER): 主キー
    *   `symbol_id` (FK, INTEGER): `symbols.id` への外部キー。UNIQUE 制約（1銘柄1レコード）
    *   `entry_date` (DATE): 指定日（パフォーマンス基準日）
    *   `entry_price` (FLOAT): 指定日の終値スナップショット
    *   `status` (STRING): `'active'` | `'removed'`
    *   `added_at` (DATETIME): 登録操作日時
    *   `removed_at` (DATETIME, NULL): 解除操作日時
    *   `removed_price` (FLOAT, NULL): 解除時の最新終値スナップショット
*   **登録/解除ロジック**（時刻基準: UTC）:
    *   *解除時*: `utcnow() - added_at < 1時間` → 物理 DELETE（誤登録の即時取消）。それ以外 → 論理削除（`status='removed'`, `removed_price` スナップショット）。
    *   *登録時*:
        *   既存 `removed` レコードが `utcnow() - removed_at < 1時間` → `entry_date`/`entry_price` を維持して復活（`added_at` は更新しない）。
        *   1時間超過 → 新しい `entry_date`/`entry_price` で上書き（`added_at` は更新しない）。
        *   レコードなし → 新規 INSERT（`added_at = utcnow()`）。
    *   **`added_at` 不変ルール**: `added_at` を更新するのは新規INSERT時のみ。再登録（removed → active）時は元の値を維持する。これにより「再登録→即解除→物理DELETE」のループを防止する。

## 4. バッチ処理フロー (Pipeline Logic)
日々のデータ更新は `update_pipeline.py` によって管理され、各階層 (T1〜T6) は「ソース」と「ターゲット」の最大日付を比較して不足分を補完する **独立したキャッチアップ・ロジック** を持ちます。

### 4.1 パイプライン各階層の更新仕様

| 階層 | テーブル名 | ソース (Source) | 最新基準 (Standard) | 追いつき判定・更新ロジック |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | `symbols` | Spreadsheet | - | **全件名寄せ (Full Sync)**: 銘柄情報の Upsert。新規銘柄検出時は T2 フル取得モードをトリガー。 |
| **Phase 2** | `prices` (Real) | yfinance API | **SPY** | **SPY主導のバッチ取得**: SPYを最新化し、各銘柄の `MAX(date)` との差分を 50件単位のバッチで yf から取得。 |
| **Phase 2+** | `prices` (Virtual) | 構成銘柄の T2 | 構成銘柄の最新 | **内部合成**: 構成銘柄の T2 が揃った最新日までテーマ指数の価格を再合成。 |
| **Phase 3** | `indicators` | `daily_prices` | T2 最新日 | **銘柄別計算**: `T2.MAX(date) > T3.MAX(date)` 的差分を算出。RS計算のため SPY の T3 を最優先。 |
| **Phase 4** | `relative_ranks` | `indicators` | T3 最新日 | **日付別計算**: `T3.MAX(date) > T4.MAX(date)` 的不足日を **Delete-Insert** で一括生成。 |
| **Phase 5** | `market_signals` | T3/T4 | T4 最新日 | **日付別概況**: 市場フェーズ・スコア等を算出。NULL欠損時は過去に遡りバックフィルを実施。 |
| **Phase 6** | `fundamental_data` | yfinance API | - | **定期リフレッシュ**: 前回の取得から 24時間以上経過した銘柄の `market_cap` 等を取得。 |

### 4.2 堅牢性とパフォーマンスの設計 (Key Design Principles)
- **べき等性 (Idempotency)**: T4/T5 等の集計テーブルは、不整合回避のために対象日を一度物理削除してから挿入することで、重複エラー (`IntegrityError`) を防止し、ジョブの再試行を常に安全にします。また、新規指標（カラム）の追加時には NULL レコードを自動検知して補完するロジックを備えています。
- **バッチ取得による負荷軽減**: yfinance へのリクエストは 50〜100件ずつのマルチ・ティッカー・バッチで一括実行し、API 制限の回避とパフォーマンス向上を両立します。
- **SPY 主導の同期**: SPY カレンダーを全銘柄の共通の到達点 (Target) とし、既存データの無駄な再スキャンを最小限に抑えます。

### 4.3 強制再計算オプション (--rebuild-from)
不具合発覚時や指標計算のロジック変更時に備え、特定の日付まで遡ってデータを再生成する機能を搭載します。
- **対象**: `daily_prices` (T2), `indicators` (T3), `relative_ranks` (T4), `market_signals` (T5)
- **連鎖的な更新 (Downstream Refresh)**: 
    - 例えば `--rebuild-from T3` を指定した場合、 T3 の再計算に加え、それに依存する T4 および T5 も整合性を保つため自動的に再計算（Delete-Insert）が実行されます。
    - 株価自体を再取得する場合は `--rebuild-from T2` を指定し、以降の全テーブルを刷新します。

## 5. バックエンド API 仕様 (FastAPI)

フロントエンドからのリクエストに応答するインターフェース群です。ポート `8000` で待機します。
全てのエンドポイントは `/api/` プレフィックスを持ちます。

*   **`GET /api/market_signal`**: 最新の T5 データ（市場フェーズや FTD）を取得。
*   **`GET /api/dashboard_data`**: 指定日付の Dashboard 用データパック（各種 T3, T4）を一括取得。
*   **`GET /api/chart_data/{ticker}`**: 指定銘柄のヒストリカルデータ群（OHLCV + 各種インジケータ）を取得。
    - **レスポンス構造**: `ChartResponse` 型。`data` (時系列配列) に加え、`metadata` (銘柄基本情報) および `themes` (関連テーマ情報の配列) を含みます。
    - **テーマ解決ロジック**: `theme_constituents` テーブルによる直接の紐付けに加え、`symbols` テーブルの `tags` カラムに含まれるカンマ区切りのタグもテーマとして解決し、リンク可能な情報を返却します。
*   **`GET /api/screener_data`**: スクリーナー用のカスタムフィルタ（「SMA50より上」「時価総額 10M以上」等）に合致する銘柄群と各指標値を返却。
*   **`GET /api/group_data/{ticker}`**: セクタまたはテーマの「グループ詳細画面」用データ。
    - **レスポンス構造**: `GroupDataResponse` 型。ETF 自体の詳細情報（`feature`）と、構成要素（テーマまたは銘柄）のリック（`constituents`）を含みます。

### 5.1 表示ロジックの共通化 (Indicator Building Helpers)
ダッシュボード、ウォッチリスト、およびグループ詳細画面間での指標表示の整合性を保つため、バックエンド側で以下の共通ヘルパー関数を定義しています。
- **`_build_etf_feature`**: ETF の主要騰落率、SMA 乖離率、およびミニチャート用時系列データを構築。
- **`_build_panel_item`**: 銘柄一覧（Sectors, Themes, Stocks）の 1 行分のデータ（RS ランク、スパークライン、1D/1W/1M 騰落率）を構築。
- **`_build_leading_item`**: 先行指標用のコンパクトなメトリクスを構築。

これらの関数は内部で **None 安全な数値変換 (float coercion)** を行い、フロントエンドでのレンダリングエラーを防止しています。

### 5.2 ウォッチリスト API

| Method | Path | 説明 |
| :--- | :--- | :--- |
| `GET` | `/api/watchlist` | 全ウォッチリスト取得（active/removed 両方 + メトリクス算出） |
| `POST` | `/api/watchlist` | 登録（3日ルール付き） |
| `DELETE` | `/api/watchlist/{ticker}` | 解除（当日登録分は物理削除、それ以外は論理削除） |
| `PUT` | `/api/watchlist/{ticker}` | 指定日変更（entry_date と entry_price を更新） |
| `DELETE` | `/api/watchlist/removed/clear` | 解除済みの一括物理削除 |
| `GET` | `/api/watchlist/tickers` | active な ticker リストのみ返却（★ボタン状態判定用、軽量） |

**`GET /api/watchlist` のレスポンスに含まれる算出項目:**
*   `latest_close`: T2 最新日の close
*   `latest_ema_21`: T3 最新日の ema_21
*   `gain_pct`: `(latest_close - entry_price) / entry_price * 100`
*   `max_gain_pct`: entry_date～最新日の `daily_prices.close` の最大値から算出
*   `min_gain_pct`: entry_date～最新日の `daily_prices.close` の最小値から算出
*   `latest_adr_pct`: T3 最新日の adr_pct_21
*   `latest_dist_sma50_atr`: T3 最新日の dist_sma50_atr
*   `rs_sparkline`: T4 の rs_ratio_21 ランク直近30日分

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
├── cache/                 # 高速化用 Parquet キャッシュ (backtest専用)
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
name = "F_elite_momentum97"
min_rs_ratio_21_rank = 0.97
trend_template_ok = 1
min_market_cap = 3e8
```

### 6.4 出口ルール（固定）

| ルール | 条件 |
|---|---|
| **エントリー** | スクリーン該当日の終値で買い |
| **損切り** | エントリー価格から -8% |
| **1/3利確** | +20%超え or SMA50/ATR% >= 8 → 残りの損切りラインをエントリー価格に引き上げ |
| **全利確** | EMA21を終値で2日連続下回る |
| **タイムストップ** | 7営業日のレンジ（高値-安値） < 1ATR → 強制退出 |
| **フェイルセーフ** | 120営業日で未決済 → 強制退出 |

### 6.5 評価指標

| 指標 | 説明 |
|---|---|
| **Expectancy** | 1トレードあたりの期待値 = (WR × AvgWin) + ((1-WR) × AvgLoss)。**最適化スコアの主軸**。 |
| **Avg Gain** | 全トレードの PnL% の単純平均。トレード回数に依存しない「1件あたりの平均品質」。 |
| **Avg SPY Gain** | 各トレードと**同一の保有期間**で SPY を購入した場合の平均リターン%。ベンチマーク。 |
| **Alpha** | Avg Gain - Avg SPY Gain。正の値 = 戦略が市場平均を上回っている（超過リターン）。 |
| **Max Drawdown** | 累積 PnL の最高到達点からの最大下落幅（加算ベース、ポイント単位）。 |
| **Profit Factor** | 総利益 / 総損失 |
| **Win Rate** | 勝ちトレード数 / 全トレード数（補助指標） |
| **Avg Holding Days** | 平均保有日数（補助指標） |

### 6.6 パフォーマンス方針
5年 × 約250営業日 × 複数戦略のスクリーン実行が必要となるため、**Parquet キャッシュ**を採用しています。

- **メモリへの一括ロード**: 実行時に全期間のデータを pandas へロード。
- **静的キャッシュ**: 初回アクセス時または明示的な更新時に DB から抽出したデータを `backend/backtest/cache/*.parquet` に保存。2回目以降は秒単位での読込を実現（5年分で約10〜15秒、3ヶ月分で0.4秒）。
- **独立性**: キャッシュはバックテスト専用であり、稼働中の DB 更新（API/Pipeline）とは干渉しません。

### 6.7 キャッシュ対象カラム (Cached Keys)
バックテスト時のメモリ（RAM）消費を最小限に抑え、最適化時のOOM（Out of Memory）を防ぐため、DB内の全カラムではなく**戦略評価に必須なカラムのみ**を選択的に抽出・キャッシュしています。

**1. `indicators` テーブル**
*   **抽出対象**: `symbol_id`, `date`, `sma_50`, `ema_21`, `atr_14`, `adr_pct_21`, `dist_sma50_atr`, `vol_surge_21`, `rel_vol_vs_spy_21`, `rs_ratio_21`, `rs_ratio_63`, `rs_momentum_21`, `rs_condition_21`, `trend_template_ok`, `market_cap`, `td9`
*   **除外対象**: `sma_5/21/63/150/200` 等の別期間MA群、14日/63日の RS momentum/condition、`atr_pct_14`, `pct_from_52w_high` 等（現状の戦略で直接使用しないもの）。※除外されているものは必要になったタイミングで `backtest_runner.py` に追記します。
*   **特記事項 (`market_cap`)**: 時価総額は過去の履歴が存在しないケースが多いため、切り取った期間内での穴埋めではなく「DB全期間の中から最新の `market_cap` を取得し、過去の日付にグローバル・バックフィル（適用）」する特殊処理を施しています。

**2. `symbols` テーブル**
*   **抽出対象**: `id`, `ticker`, `name`, `category`, `active`
*   **除外対象**: `exchange`, `asset_class`, `theme_type`, `tags` など

**3. その他テーブル**
*   **`daily_prices`**: PK (`id`) 以外を全て抽出。
*   **`relative_ranks`**: 対象指標名が `rs_ratio_21` および `rs_ratio_63` のレコードのみに絞り、`symbol_id`, `indicator_name`, `date`, `percent_rank` のみを抽出（`group_name`, `id` を除外）。
*   **`market_signals`, `fundamental_data`**: 現状のバックテストエンジンでは利用していないため、完全に除外。

### 6.8 実行方法
```bash
# 全戦略実行（デフォルト期間・キャッシュ優先）
python backend/backtest/backtest_runner.py

# 特定戦略のみ
python backend/backtest/backtest_runner.py --strategy F_elite_momentum97

# 期間の上書き
python backend/backtest/backtest_runner.py --start-date 2025-01-01 --end-date 2026-03-01

# キャッシュをリフレッシュ（DBから最新を取得し直す）
python backend/backtest/backtest_runner.py --refresh-cache
```

### 6.9 自動パラメータ最適化 (Optuna)

指定したベース戦略（例: `B_theme_momentum`）の各種パラメータの探索範囲を定義し、ベイズ最適化を用いて最も実運用に適した閾値を探索します。

*   **実行スクリプト**: `backend/optimization_runner.py`
*   **ストレージ**: `data/optimization_trials.db` (中断・再開に対応した SQLite ベースの Optuna DB)
    *   **カスタムスコアとペナルティ**: 
        *   単純な総利益へのカーブフィッティングを防ぐため、「大きすぎるドローダウン」や「トレード頻度が多すぎる（1日平均25件以上など）」設定に対してスコア減点（罰則）を与えるように設計。
        *   **勾配を持たせたペナルティ**: トレードが極端に少ない（5件未満）場合、単純な足切り（一律マイナス）ではなく、トレード回数に応じたスコアの「勾配」を設けることで、AIが正解のパラメータ方向を探れるよう誘導。
    *   **マルチ期間学習**: 特定のトレンドに過学習しないよう、弱気（ベア）相場と強気（ブル）相場の複数期間で同じパラメータを並行評価し、その平均スコアを最大化する目的関数を採用。
    *   **UIダッシュボード**: `optuna-dashboard` と連動し、Webブラウザ上で探索過程やパラメータごとの重要度（Hyperparameter Importance）をリアルタイム可視化。

#### 6.9.1 探索空間の宣言的定義 (TOML ベース)

探索パラメータのレンジ（範囲・刻み幅）は、Pythonコードにハードコーディングせず、**`backtest_config.toml` の `[optimization.<戦略短縮名>]` セクションで宣言的に定義**する。`optimization_runner.py` はこのTOML定義を動的にパースし、Optuna の `trial.suggest_*` APIに変換して探索を実行する。

**サポートするパラメータ型:**

| `type` | Optuna API | TOML 必須キー | 説明 |
| :--- | :--- | :--- | :--- |
| `"float"` | `trial.suggest_float()` | `min`, `max`, `step` | 連続値の範囲探索（ステップ刻み） |
| `"int"` | `trial.suggest_int()` | `min`, `max`, `step` | 整数値の範囲探索（ステップ刻み） |
| `"categorical"` | `trial.suggest_categorical()` | `choices` | 離散値リストからの選択 |

**TOML 記法例:**

```toml
# backtest_config.toml 内に追加

[optimization.D]
min_dist_21ema_pct   = { type = "float", min = -4.0, max = -1.0, step = 0.5 }
max_dist_21ema_pct   = { type = "float", min = 0.5,  max = 4.0,  step = 0.5 }
max_dist_sma50_atr   = { type = "float", min = 2.0,  max = 6.0,  step = 0.5 }
min_rs_ratio_21_rank = { type = "float", min = 0.70, max = 0.95, step = 0.05 }
min_market_cap       = { type = "categorical", choices = [1e8, 3e8, 5e8, 1e9] }
trend_template_ok    = { type = "categorical", choices = [1] }

[optimization.B]
min_1d_gain_pct      = { type = "float", min = 1.0, max = 5.0, step = 0.5 }
min_vol_surge_21     = { type = "float", min = 0.5, max = 2.0, step = 0.1 }
min_adr_pct_21       = { type = "float", min = 2.0, max = 6.0, step = 0.5 }
max_dist_sma50_atr   = { type = "float", min = 3.0, max = 8.0, step = 0.5 }
min_market_cap       = { type = "categorical", choices = [1e7, 5e7, 1e8, 3e8, 5e8, 1e9] }
theme_rs21_gt_63     = { type = "categorical", choices = [true, false] }
```

**パース仕様:**
- `optimization_runner.py` は `config["optimization"][strategy_short_name]` を読み込み、各キーの `type` フィールドに応じて対応する `trial.suggest_*` を呼び出す。
- TOML に `[optimization.X]` が未定義の戦略で `--strategy X` を実行した場合は、明確なエラーメッセージとともに即座に終了する。
- TOML で定義されていないパラメータ（`[[strategy]]` セクション内のベース値）は変更されず、そのまま継承される。

#### 6.9.2 マルチ期間設定の外部化

最適化時の評価対象期間も `backtest_config.toml` で宣言的に定義する。

```toml
[optimization_periods]
periods = [
    { start = "2022-01-01", end = "2022-12-31", label = "Bear 2022" },
    { start = "2024-06-01", end = "2025-12-31", label = "Bull 2024-25" },
]
```

これにより、評価期間の追加・変更時にPythonコードの修正が不要になる。

### 6.10 大規模データのメモリ安全設計 (OOM回避)

バックテストおよびキャッシュの生成時には、数百万件規模の価格データ・指標データを扱うため、システムレベルでのメモリ不足（OOM）を防ぐ設計を適用しています。

*   **DBからのチャンク毎ロード**: `pandas.read_sql()` による SQLite の一括ロードによるメモリ肥大（データサイズの数十倍を消費する現象）を防ぐため、`chunksize` 単位（5万～10万行ずつ）での読み出しと結合を徹底しています。
*   **Parquet キャッシュ時の型安全**: 
    - `Timestamp` 型 (datetime64[ns]) から `datetime.date` への変換をロード直後に明示的に実行（`pd.to_datetime().dt.date`）。
    - これにより、Pandas 内部での「Timestamp と date の比較不全」によるシグナル誤検知やランタイムエラーを防止しています。
*   **ループ内外での走査分離**: `unique()` や `max()` のような DataFrame 全体の検索走査は、日付毎のループ内では極力使わず、ループ外での事前抽出によって O(N^2) のボトルネックを排除し、処理の破綻を防いでいます。
*   **動的データ供給**: スクリーン時に必要な `gain_1d_pct` （1日騰落率）等のカラムが DB に存在しない場合でも、 `backtest_screener.py` が始値・終値からオンザフライで算出・注入することで、データの欠損による `KeyError` を回避します。

## 7. 開発・検証プロセス (Development & Verification Process)

本プロジェクトでは、データの整合性と本番環境の安全性を担保するため、以下の検証フローを原則とします。

### 7.1 検証用サンドボックス環境の活用
ロジックの変更、DBスキーマの拡張、または大規模なデータリフレッシュを伴う作業を行う際は、必ず**サンドボックスDB (`data/stocktool_sandbox.db`)** を使用して検証を行います。

1.  **Sandbox ステージ**:
    - 新しいロジックの実装後、サンドボックスDBに対してマイグレーションおよびデータ更新を実行。
    - ユニットテスト (`pytest`) を実行し、ロジックの正当性を確認。
2.  **Verify ステージ (視覚的確認)**:
    - APIサーバーの接続先を一時的にサンドボックスに切り替え（環境変数 `STOCKTOOL_DB_PATH` を推奨）。
    - フロントエンドの「Data View」等で、実際に意図した数値が表示されているかを目視確認。
    - **完了条件**: 画面ヘッダーの「DB識別バッジ」が対象のサンドボックスDB名を表示していることを確認すること。
3.  **Production ステージ**:
    - 上記すべての確認が完了した後、本番DB (`data/stocktool.db`) に対して同一の手順（マイグレーション・データリフレッシュ）を適用。

### 7.2 安全性の担保
- **APIによる確認**: `/api/system/info` エンドポイントを叩き、`is_production` フラグが意図した状態であるかを確認する。
- **視覚的警告**: フロントエンドは本番以外のDB接続を検知するとオレンジ色の警告バッジを常時表示し、環境の取り違えを防止する。
