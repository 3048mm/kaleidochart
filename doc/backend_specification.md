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

## 3. データベース設計 (SQLite/Parquet ハイブリッドアーキテクチャ)

当システムでは、データベース（SQLite）の肥大化・Windowsのファイル共有ロック競合を解決し、バックテストを爆速化するため、**「ホット（SQLiteキャッシュ）/ コールド（Parquet全期間マスター）ハイブリッド設計」**を採用しています。

*   **本尊（真のマスターデータソース）**: **Parquetファイル (全期間歴史マスター)**
    - [data/parquet_master/](file:///d:/My%20Documents/Programing/stocktool/data/parquet_master/) にタイムスタンプ世代管理（MVCC方式）されたSnappy圧縮Parquet形式で永続保存されます。
    - バックテスト実行（OLAP）は SQLite を完全遮断し、この Parquet から直接一撃ロード（約1秒）します。
*   **仮置き場（画面・UI表示用）**: **ホット SQLiteデータベース (`stocktool.db`)**
    - Web APIからの超多頻度なランダムアクセス（OLTP）やユーザー設定の保存をミリ秒で処理するため、**「直近2年分（730日）の一時キャッシュ」**に徹します。
    - 定期バッチの末尾で自動的に2年以上前の古いデータがパージされ、物理サイズが約1.7 GB以下にスリム化されるため、ロック競合が完全に回避されます。
*   **`user_data.db`**: ウォッチリスト、ポートフォリオ、入出金履歴等の「ユーザー固有資産・設定データ」。再構築不可の永続データ。
※ 開発およびテストの際は、それぞれに対応するサンドボックス環境（`stocktool_sandbox.db`, `parquet_master_sandbox/`, `user_data_sandbox.db`）を使用します。

### 【第一部】 キャッシュ・派生データベース (`stocktool.db`)
構成データは、段階的（T1〜T5）に計算・生成されるテーブル群に保存されます。安定した並行処理（WALモード）が適用されています。

#### 3.1 T1: 銘柄メタデータ (`symbols`)

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
*※本番 SQLite データベース（`stocktool.db`）内には、**直近2年分（730日）のみ**がホットキャッシュとして保持され、それ以前の歴史データは Parquet マスターに永続退避された後、パージされます。*

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 |
| `date` | DATE | 取引日。 |
| `open` | FLOAT | 始値。当日中の騰落率（`change_intraday_pct`）の計算に使用。 |
| `high` | FLOAT | 高値。タイムストップやチャネルブレイク、ATRの計算に使用。 |
| `low` | FLOAT | 安値。損切り判定やATRの計算に使用。 |
| `close` | FLOAT | 終値。すべてのテクニカル指標計算のベース。 |
| `volume` | BIGINT | 出来高。出来高急増（`vol_surge`）の計算に使用。 |
| `market_cap` | FLOAT | 日次時価総額（米ドル）。yfinance から取得。スクリーナーでのサイズ制限に使用。 |

### 3.4 T3: インジケータデータ (`indicators`)

T2の価格データを元に算出される各種テクニカル・モメンタム指標です。
*※本番 SQLite データベース内には、**直近2年分（730日）のみ**がホットキャッシュとして保持され、それ以前の歴史データは Parquet マスターに永続退避された後、パージされます。*

| カラム名 | 型 | 説明・用途 | 計算式 / 論理 |
| :--- | :--- | :--- | :--- |
| `sma_n` | FLOAT | 5, 21, 50, 63, 150, 200日単純移動平均。トレンド判定に使用。 | `close.rolling(n).mean()` |
| `ema_n` | FLOAT | 5, 21, 50, 63, 150, 200日指数平滑移動平均。TradingView互換。 | `calculate_ema_tv(close, n)` (SMAをシードとした再帰計算) |
| `atr_14` | FLOAT | ボラティリティ指標（Average True Range）。 | 14日間の True Range の平均 |
| `atr_pct_14` | FLOAT | Closeに対するATRの割合(%)。 | `(atr_14 / close) * 100` |
| `adr_pct_21` | FLOAT | 21日間の平均日次レンジ(%)。ボラティリティの強さ判定に使用。 | `mean( (high - low) / low * 100 )` |
| `change_1d_pct` | FLOAT | 1日騰落率(%)。前日終値を基準とした1日の変化率。 | `(close - prev_close) / prev_close * 100` |
| `change_1w_pct` | FLOAT | 1週騰落率(%)。5営業日前（1週間）の終値を基準とした変化率。 | `(close - close_5d_ago) / close_5d_ago * 100` |
| `change_1m_pct` | FLOAT | 1月騰落率(%)。20営業日前（1か月）の終値を基準とした変化率。 | `(close - close_20d_ago) / close_20d_ago * 100` |
| `sma50_atr_mult` | FLOAT | SMA50からの距離をATRで正規化した値。 | `((close / sma_50 * 100) - 100) / atr_pct_14` |
| `td9` | INT | Tom DeMark Sequential。過熱感の判定に使用。 | 4日前の終値との比較による 1〜9 のカウントアップ/ダウン |
| `rs_value` | FLOAT | SPYに対する単純相対強度。 | `close / spy_close` |
| `rs_value_eN` | FLOAT | RSの平滑化 (e5, e14, e21, e63, e200)。RRG計算の前処理等に使用。 | `calculate_ema_tv(rs_value, n)` |
| `rs_ratio_eN` | FLOAT | RSの正規化スコア (e5, e14, e21, e63, e200)。RRGのX軸（Ratio）に相当。ただしオフセット100が無いので 0センター | `(rs_value_eN - mean(rs_value_eN, n)) / std(rs_value_eN, n)` |
| `rs_trend_sN` | FLOAT | RSのトレンド強度 (s5, s14, s21, s63, s200)。 単体で 1.0より高ければSPYより上昇。 期間別で比較する場合は、他のRSと異なり s21 < s63 の場合に上昇傾向なので注意. | `rs_value_e5 / SMA(rs_value, n)` |
| `rs_roc_ema_N` | FLOAT | RS-Ratioの14日間変化率(ROC)の平滑化 (5, 14, 21, 63, 200)。 | `calculate_ema_tv(ROC(rs_ratio_eN + 100), n)` |
| `rs_momentum_eN` | FLOAT | RS-Ratioの勢い (e5, e14, e21, e63, e200)。RRGのY軸（Momentum）に相当。ただしオフセット100が無いので 0センター | `(rs_roc_ema_N - mean(rs_roc_ema_N, n)) / std(rs_roc_ema_N, n)` |
| `rs_macd_line_21` | FLOAT | RSのMACDライン（短期相対強度と中期相対強度の差分）。 | `rs_value_e5 - rs_value_e21` |
| `rs_macd_signal_21` | FLOAT | RSのMACDシグナルライン（MACDラインの5日EMA平滑化）。 | `calculate_ema_tv(rs_macd_line_21, 5)` |
| `rs_macd_hist_21` | FLOAT | RSのMACDヒストグラム（加速・減速の定量化）。 | `rs_macd_line_21 - rs_macd_signal_21` |
| `vol_surge_21` | FLOAT | 出来高急増倍率。 | `volume / mean(volume, 21)` |
| `vol_surge_rel_spy_21` | FLOAT | SPYに対する出来高の相対的な強さ。 | `vol_surge_21 / spy_vol_surge_21` |
| `dist_52w_high_pct` | FLOAT | 52週（252日）高値からの下落率(%)。 | `(close - max(high, 252)) / max(high, 252) * 100` |
| `dist_63d_high_pct` | FLOAT | 63日高値からの下落率(%)。 | `(close - max(high, 63)) / max(high, 63) * 100` |
| `up_down_vol_ratio_50` | FLOAT | 50日間の上昇日出来高合計÷下落日出来高合計。機関投資家のAccumulationの強さを示す。1.5以上＝買い集め優勢。 | `sum(volume where close > prev_close, 50) / sum(volume where close < prev_close, 50)` |
| `is_rs_blue_dot` | SMALLINT | RS新高値先行フラグ（1:点灯, 0:非点灯）。RSが株価に先行して52週新高値を更新した場合に点灯。 | `(rs_value >= max(RS, 252)) AND (close < max(close, 252))` |
| `is_rs_red_dot` | SMALLINT | RS新安値先行フラグ（1:点灯, 0:非点灯）。RSが株価に先行して52週新安値を更新した場合に点灯。 | `(rs_value <= min(RS, 252)) AND (close > min(close, 252))` |
| `vcr` | FLOAT | Volatility Contraction Ratio。VCP（ベース形成）のスクイーズ度合いを定量化。0.5未満＝極度の収縮。 | `ATR(10) / ATR(50)` （True Rangeの単純移動平均として算出） |
| `is_trend_template` | SMALLINT | ミネルヴィニのトレンドテンプレート適合フラグ（1:適合, 0:不適合）。 | 右記5条件: ①close>sma50, ②sma50>sma150, ③sma150>sma200, ④sma200上昇中(20日前比), ⑤52週高値から30%以内 |

### 3.5 T4: 相対評価データ (`relative_ranks`)

同一カテゴリ内で特定指標（RSスコア等）を横並び比較し、パーセンタイル(0〜1)で順位付けしたデータです。
*※本番 SQLite データベース内には、**直近2年分（730日）のみ**がホットキャッシュとして保持され、それ以前の歴史データは Parquet マスターに永続退避された後、パージされます。*

| カラム名 | 型 | 説明・用途 | 計算式 / 論理 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 | |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 | |
| `date` | DATE | 評価日。 | |
| `group_name` | STRING | 比較対象のグループ（`個別`, `テーマ` などの種類ごと）。 | |
| `indicator_name` | STRING | ランク付けの対象指標名。<br>現在、以下の T3 指標が対象：<br> - `rs_value`<br> - `rs_ratio_e5 / e14 / e21 / e63 / e200`<br> - `rs_momentum_e5 / e14 / e21 / e63 / e200`<br> - `rs_trend_s5 / s14 / s21 / s63 / s200`<br> - `rs_macd_hist_21` | |
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
| `market_trend_score` | FLOAT | 市場全体の健康度を 0〜100 で数値化したもの。 | 右記5項目の等価20%合計: ①VXV/VIXレシオ, ②市場の幅, ③50EMA/ATR乖離, ④200EMA/ATR乖離, ⑤Distribution Days |

**Market Trend Score (MTS v3_B) の内訳:**
- **VXV/VIX レシオ (20pt)**: VXV/VIX 比率が 0.90〜1.25 の間で線形補完。
- **市場の幅 (20pt)**: SMA50を上回っている個別銘柄の割合。
- **SPY 50EMA/ATR 乖離 (20pt)**: 50EMAからの乖離が -4.0〜+8.0 ATRの間で線形補完。
- **SPY 200EMA/ATR 乖離 (20pt)**: 200EMAからの乖離が -4.0〜+16.0 ATRの間で線形補完。
- **Distribution Days (20pt)**: 過去25日間のディストリビューション・デー日数が 5日以下で満点、10日以上で0点、その間は線形減少。
- **RRG 特殊フィルタ (Leading In / Improving In)**: 
    スクリーナーおよびバックテストで使用される突入検知フラグは、以下の条件をオンザフライで計算して判定します：
    - **Leading In**: 当日が Leading 象限 (Ratio>0, Mom>0) かつ強度 (sqrt(R^2+M^2)) >= 閾値 かつ Mom加速中で、前日が「Leading 象限外」または「低強度」であった場合。
    - **Improving In**: 当日が Improving 象限 (Ratio<0, Mom>0) かつ強度 >= 閾値 かつ Mom加速中で、前日が「Lagging 象限 (Ratio<0, Mom<=0)」または「低強度かつRatio負」であった場合。

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

### 3.7 Market Trend Score (0-100) 算出ロジック (MTS v3_B)
市場の過熱感や健全性を定量化するため、以下の5つの独立した構成要素に基づき、各要素最大20点、合計100点満点で算出します：

1.  **VXV/VIX Ratio (20pts)**:
    - VXV/VIX 比率（^VIX3M / ^VIX）に基づく恐怖・過熱度の定量化。
    - `0.90` 以下であれば 0点、`1.25` 以上であれば 20点満点。その間は線形補間。
2.  **Market Breadth (20pts)**:
    - 個別銘柄（`category='個別'` かつ `active=1`）のうち、終値が50日移動平均線（SMA50）を上回っている銘柄の比率（0.0〜1.0）を 0〜20 点にスケーリング。
3.  **SPY 50EMA/ATR Distance (20pts)**:
    - SPYの終値と 50日指数平滑移動平均線（50EMA）の乖離幅を、14日平均トゥルーレンジ（ATR_14）で正規化した値（`(SPY - 50EMA) / ATR_14`）。
    - `-4.0` 以下であれば 0点、`+8.0` 以上であれば 20点満点。その間は線形補間。
4.  **SPY 200EMA/ATR Distance (20pts)**:
    - SPYの終値と 200日指数平滑移動平均線（200EMA）の乖離幅を、14日平均トゥルーレンジ（ATR_14）で正規化した値（`(SPY - 200EMA) / ATR_14`）。
    - `-4.0` 以下であれば 0点、`+16.0` 以上であれば 20点満点。その間は線形補間。
5.  **Distribution Days (20pts)**:
    - 過去25日間のディストリビューション・デー（売り抜け日）のカウントに基づく売り圧力の定量化。
    - 5日以下であれば 20点満点、10日以上であれば 0点。その間（6〜9日）は線形減少（1日増えるごとに4点減点）。

### 【第二部】 ユーザー固有データベース (`user_data.db`)

ユーザー操作によって生成され、システムが再構築されても保持されるべき永続データのテーブル群です。ウォッチリストとポートフォリオ関連を含みます。

#### 3.8 ウォッチリスト (`watchlist`)
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

#### 3.9 ポートフォリオ管理テーブル

ユーザーの資産全体を統括する「Total Portfolio」と、その配下にある用途別の「サブポートフォリオ」を管理するための構成です。

##### `total_portfolios` — 全体口座管理 (New)
*   **特徴**: ユーザーの資産全体の器。
*   **カラム**:
    *   `id` (PK, INTEGER): 主キー
    *   `name` (STRING): 全体口座名（例: "Main Account"）
    *   `currency` (STRING): ベース通貨 (`JPY` / `USD`)
    *   `created_at`, `updated_at` (DATETIME)

##### `transactions` — 入出金・資金移動履歴 (New)
*   **特徴**: Total Portfolio に対する入金/出金や、サブポートフォリオへの資金割り当て（Funding）を記録する。
*   **カラム**:
    *   `id` (PK, INTEGER): 主キー
    *   `total_portfolio_id` (FK → total_portfolios.id): 全体口座ID
    *   `portfolio_id` (FK → portfolios.id, NULL): サブポートフォリオID（資金移動時のみ）
    *   `transaction_type` (STRING): `'DEPOSIT'` | `'WITHDRAWAL'` | `'FUNDING'` | `'REFUND'`
    *   `amount` (FLOAT): 金額（プラスは入金、マイナスは出金等）
    *   `date` (DATE): 実行日
    *   `memo` (TEXT, NULL)
    *   `created_at` (DATETIME)

##### `portfolios` — サブポートフォリオ設定
*   **特徴**: 複数ポートフォリオを用途別（長期/スイング等）に管理。`total_portfolios` に属する。
*   **カラムの追加点**:
    *   `total_portfolio_id` (FK → total_portfolios.id): 親口座ID（追加）
    *   `id` (PK, INTEGER): 主キー
    *   `name` (STRING): ポートフォリオ名（例: 「スイング」「長期投資」）
    *   `currency` (STRING): 通貨 (`JPY` / `USD`)
    *   `total_capital` (FLOAT): 総投資資金
    *   `risk_pct` (FLOAT): リスク許容%（デフォルト 1.0）
    *   `default_stop_loss_pct` (FLOAT): デフォルト損切%（例: 8.0）
    *   `stop_loss_method` (STRING): `'fixed_pct'` | `'atr_multiple'`
    *   `atr_multiplier` (FLOAT, NULL): ATR倍率（method=atr_multiple 時）
    *   `profit_take_method` (STRING, NULL): 利確方式
    *   `max_positions` (INT): 最大同時保有数（デフォルト 8）
    *   `source` (STRING): `'manual'` | `'moomoo_api'`
    *   `status` (STRING): `'active'` | `'archived'`
    *   `created_at`, `updated_at` (DATETIME)

#### `portfolio_positions` — 保有銘柄
*   **特徴**: 各ポートフォリオの保有中ポジション。部分売却(Trim)で `shares` が減少する。
*   **カラム**:
    *   `id` (PK, INTEGER): 主キー
    *   `portfolio_id` (FK → portfolios.id): ポートフォリオID
    *   `symbol_id` (FK → symbols.id): 銘柄ID
    *   `entry_date` (DATE): 購入日
    *   `entry_price` (FLOAT): 購入価格（指定日の終値）
    *   `shares` (INT): 現在の保有株数（Trimで減少）
    *   `original_shares` (INT): 購入時の株数
    *   `stop_loss_pct` (FLOAT, NULL): 個別損切%（NULLはポートフォリオデフォルト継承）
    *   `custom_take_profit_pct` (FLOAT, NULL): 個別利確%
    *   `status` (STRING): `'open'` | `'partially_closed'`
    *   `memo` (TEXT, NULL): メモ
    *   `created_at` (DATETIME)

#### `position_history` — 売却履歴
*   **特徴**: 売却（全売却/Trim）ごとに1レコード生成。累積P&L算出の基盤。
*   **カラム**:
    *   `id` (PK, INTEGER): 主キー
    *   `portfolio_id` (FK → portfolios.id): ポートフォリオID
    *   `symbol_id` (FK → symbols.id): 銘柄ID
    *   `entry_date` (DATE), `entry_price` (FLOAT): 購入情報
    *   `entry_shares` (INT): この売却分の元株数
    *   `exit_date` (DATE), `exit_price` (FLOAT): 売却情報
    *   `exit_shares` (INT): 売却株数
    *   `exit_reason` (STRING): `'stop_loss'` | `'take_profit_trim'` | `'take_profit_full'` | `'trailing_stop'` | `'manual'`
    *   `pnl_pct` (FLOAT): 損益率
    *   `pnl_amount` (FLOAT): 損益額
    *   `holding_days` (INT): 保有日数
    *   `memo` (TEXT, NULL)
    *   `created_at` (DATETIME)

### 3.10 SQLite 運用設定 (Performance & Concurrency)
本プロジェクトの SQLite は、多数の API リクエストと大量のバッチ処理を並行させるため、以下の設定を適用している。

*   **Journal Mode: `WAL` (Write-Ahead Logging)**: 読み取りと書き込みの競合を大幅に軽減。
*   **Busy Timeout: `3600000` (1時間)**: `OperationalError: database is locked` を回避し、ロックが解放されるまで待機するように設定。
*   **Synchronous: `NORMAL` (or `OFF` during bulk)**: ディスク I/O 負荷を軽減し、特に HDD 環境での書き込み速度を確保。

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
*   **`GET /api/chart/{symbol_id}`**: 指定銘柄のヒストリカルデータ群（OHLCV + 各種インジケータ）を取得。
    - **クエリパラメータ**: `full_range: bool` (デフォルト: `false`)
        - `false` の場合、高速アクセスのため SQLite データベースの直近2年分の一時キャッシュからデータを取得して返却する。
        - `true` の場合、`data/parquet_master/` に格納されている Parquet Master キャッシュ（全期間履歴マスター）から `symbol_id` で直接高速フィルタリングし、2019年以前を含む全期間の時系列データを構築して返却する。
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
*   `latest_dist_sma50_atr`: T3 最新日の sma50_atr_mult
*   `rs_sparkline`: T4 の rs_ratio_rank_e21 直近30日分

### 5.3 ポートフォリオ API

| Method | Path | 説明 |
| :--- | :--- | :--- |
| `GET` | `/api/portfolio` | アクティブなポートフォリオ一覧取得 |
| `POST` | `/api/portfolio` | ポートフォリオ新規作成 |
| `GET` | `/api/portfolio/{id}` | ポートフォリオ設定取得 |
| `PUT` | `/api/portfolio/{id}` | ポートフォリオ設定更新 |
| `DELETE` | `/api/portfolio/{id}` | ポートフォリオのアーカイブ（論理削除） |
| `GET` | `/api/portfolio/{id}/positions` | 保有ポジション一覧取得（現在価格・損益・アラート状態等のメトリクス付） |
| `POST` | `/api/portfolio/{id}/positions` | 新規ポジション追加 |
| `POST` | `/api/portfolio/{id}/positions/{pid}/sell` | ポジションの売却・Trim（一部売却）。履歴への移動と株数減算処理を含む |
| `GET` | `/api/portfolio/{id}/history` | 売却履歴一覧取得（累積P&L付） |
| `GET` | `/api/portfolio/{id}/summary` | ポートフォリオのサマリー取得（投資額、リスク額等のダッシュボード用） |
| `GET` | `/api/portfolio/{id}/analytics` | パフォーマンス分析用データ取得（セクター分散、勝率、月次リターン、エクイティカーブ） |

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
min_rs_ratio_rank_e21 = 0.97
is_trend_template = true
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

### 6.6 パフォーマンス方針 (SQLite 完全遮断 & Parquet マスターロード)
バックテストエンジンは、長期間（5年分以上）かつ大量銘柄（数千件）の OLAP データスキャンを行うため、本番 SQLite キャッシュ DB へのアクセスを **「完全遮断（DB接続すらしない）」** します。

- **SQLite コネクションプール保護**: バックテストの実行中、SQLite への物理 I/O および接続数は一切増加しません。
- **Parquet 一撃ロード**: 全歴史が蓄積されている [data/parquet_master/](file:///d:/My%20Documents/Programing/stocktool/data/parquet_master/) の Parquet ファイル群（またはバックテスト用一時 Parquet）から `pd.read_parquet()` で一撃ロード（約1〜2秒）し、メモリ上で瞬時にシミュレーションを行います。これにより、並列バックテスト時でも本番 SQLite への Busy ロックは 100% 発生しません。
- **独立性**: バックテスト専用キャッシュおよびParquetマスターは、稼働中の本番 API サーバー（SQLite）とは完全に分離されており、双方の競合フリーな並行稼働が物理的に保証されます。

### 6.7 キャッシュ対象カラム (Cached Keys)
バックテスト時のメモリ（RAM）消費を最小限に抑え、最適化時のOOM（Out of Memory）を防ぐため、DB内の全カラムではなく**戦略評価に必須なカラムのみ**を選択的に抽出・キャッシュしています。

**1. `indicators` テーブル**
*   **抽出対象**: `symbol_id`, `date`, `sma_50`, `ema_21`, `atr_14`, `adr_pct_21`, `sma50_atr_mult`, `vol_surge_21`, `vol_surge_rel_spy_21`, `rs_ratio_e21`, `rs_ratio_e63`, `rs_momentum_e21`, `rs_trend_s21`, `is_trend_template`, `market_cap`, `td9`
*   **除外対象**: `sma_5/21/63/150/200` 等の別期間MA群、14日/63日の RS momentum/condition、`atr_pct_14`, `dist_52w_high_pct` 等（現状の戦略で直接使用しないもの）。※除外されているものは必要になったタイミングで `backtest_runner.py` に追記します。
*   **特記事項 (`market_cap`)**: 時価総額は過去の履歴が存在しないケースが多いため、切り取った期間内での穴埋めではなく「DB全期間の中から最新 of `market_cap` を取得し、過去の日付にグローバル・バックフィル（適用）」する特殊処理を施しています。

**2. `symbols` テーブル**
*   **抽出対象**: `id`, `ticker`, `name`, `category`, `active`
*   **除外対象**: `exchange`, `asset_class`, `theme_type`, `tags` など

**3. その他テーブル**
*   **`daily_prices`**: PK (`id`) 以外を全て抽出。
*   **`relative_ranks`**: 対象指標名が `rs_ratio_e21` および `rs_ratio_e63` のレコードのみに絞り、`symbol_id`, `indicator_name`, `date`, `percent_rank` のみを抽出（`group_name`, `id` を除外）。
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
    *   **TOML形式の出力**: 各 Trial の **Note** 欄および `params_toml` 属性に、そのまま `backtest_config.toml` やスクリーナープリセットに貼り付け可能な `key = value` 形式のパラメータセットを自動生成して保存します。これにより、最適化されたパラメータを即座に実運用へ反映できます。

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
min_dist_ema21_pct   = { type = "float", min = -4.0, max = -1.0, step = 0.5 }
max_dist_ema21_pct   = { type = "float", min = 0.5,  max = 4.0,  step = 0.5 }
max_sma50_atr_mult   = { type = "float", min = 2.0,  max = 6.0,  step = 0.5 }
min_rs_ratio_rank_e21 = { type = "float", min = 0.70, max = 0.95, step = 0.05 }
min_market_cap       = { type = "categorical", choices = [1e8, 3e8, 5e8, 1e9] }
is_trend_template    = { type = "categorical", choices = [true] }

[optimization.B]
min_change_1d_pct   = { type = "float", min = 1.0, max = 5.0, step = 0.5 }
min_vol_surge_21     = { type = "float", min = 0.5, max = 2.0, step = 0.1 }
min_adr_pct_21       = { type = "float", min = 2.0, max = 6.0, step = 0.5 }
max_sma50_atr_mult   = { type = "float", min = 3.0, max = 8.0, step = 0.5 }
min_market_cap       = { type = "categorical", choices = [1e7, 5e7, 1e8, 3e8, 5e8, 1e9] }
is_theme_rs_ratio_e21_gt_e63 = { type = "categorical", choices = [true, false] }
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

*   **PyArrow フィルターによるディスク段階絞り込み**: 読み込む日付期間が決まっている場合は、`pd.read_parquet` の `filters=[('date', '>=', cutoff_str)]` を指定してディスクロードの段階でデータサイズを絞り込みます。これにより、1.8GBの巨大な Parquet からでも、メモリ消費量をわずか **740 MB** に抑えつつ、**1.12秒** で爆速ロードが可能です。
*   **DBからのチャンク毎ロード**: `pandas.read_sql()` による SQLite 一括ロードによるメモリ肥大（データサイズの数十倍を消費する現象）を防ぐため、`chunksize` 単位（5万～10万行ずつ）での読み出しと結合を徹底しています。
*   **Parquet キャッシュ時の型安全**: 
    - `Timestamp` 型 (datetime64[ns]) から `datetime.date` への変換をロード直後に明示的に実行（`pd.to_datetime().dt.date`）。
    - これにより、Pandas 内部での「Timestamp と date の比較不全」によるシグナル誤検知やランタイムエラーを防止しています。
*   **ループ内外での走査分離**: `unique()` や `max()` のような DataFrame 全体の検索走査は、日付毎のループ内では極力使わず、ループ外での事前抽出によって O(N^2) のボトルネックを排除し、処理の破綻を防いでいます。
*   **動的データ供給**: スクリーン時に必要な `gain_1d_pct` （1日騰落率）等のカラムが DB に存在しない場合でも、 `backtest_screener.py` が始値・終値からオンザフライで算出・注入することで、データの欠損による `KeyError` を回避します。

### 6.11 Based ETF from SMA200 (汎用レバレッジ比例ポジション制御戦略)

本プロジェクトにおける「Based ETF from SMA200」は、単純な移動平均線クロスオーバーではなく、レバレッジETFのポテンシャルを最大化するために**原指数（ベース1倍ETF）のトレンドとレバレッジ倍率を組み合わせて最適化された汎用的なポジション制御戦略** です。

#### 6.11.1 設計思想
レバレッジETF（TQQQやUPROなど）は, 上昇トレンド時の複利効果が絶大である一方、レンジ相場での「ボラティリティ減価」や、長期ベアマーケットでの「致命的なドローダウン」を伴います。
本戦略は、取引対象のレバレッジETFそのものではなく、その「元になる1倍の原指数ETF（QQQやSPYなど）」のトレンド安定性を安全弁（シグナル）として用い、下落時の深刻なドローダウンを回避しつつ、下落過程での「レバレッジ倍率に比例した安値での段階的仕込み」と「トレンド確定後のフル投資」を組み合わせることで、ドローダウンを抑えながら Buy & Hold を大きく凌駕するリターンを達成するよう設計されています。

#### 6.11.2 売買およびポジション制御ルール

1.  **フルポジ化 (ポジション 100%)**:
    *   **条件**: 原指数（QQQまたはSPY）の終値が **`SMA200 × 1.05`**（バッファ上抜け）を上回ったとき。
    *   **アクション**: 翌営業日のオープンで資産の100%を取引対象ETFに投資する。
2.  **全売却 (ポジション 0% / キャッシュ退避)**:
    *   **条件**: 原指数（QQQまたはSPY）の終値が **`SMA200 × 0.97`**（バッファ下抜け）を下回ったとき。
    *   **アクション**: 翌営業日のオープンで取引対象ETFを全売却する。この時の取引対象の始値を **`「基準売却価格」`** として記録する。
3.  **大底買い戻し (ポジション 50% / 半ポジ構築)**:
    *   **条件**: 全売却後、原指数がSMA200を回復していないキャッシュ退避期間中に、取引対象ETFの終値が **`「基準売却価格」から 「8% × レバレッジ倍率」以上下落`** したとき。
        *   3倍レバレッジ (TQQQ, UPRO等) $\rightarrow$ **-24%** 下落でトリガー
        *   2倍レバレッジ (UGL等) $\rightarrow$ **-16%** 下落でトリガー
        *   1倍ETF (SPY, QQQ等) $\rightarrow$ **-8%** 下落でトリガー
    *   **アクション**: 翌営業日のオープンで資産の50%を取引対象ETFに買い戻す。この半ポジは、原指数が再度上の条件1を達成するまで維持し、下落の途中でこれ以上の追加購入や追加売却は一切行わない。

#### 6.11.3 なぜこのパラメータ（8% × レバレッジ倍率 / 半ポジ）が最適なのか
*   **レバレッジ比例下落の理由**: ボラティリティはレバレッジ倍率にほぼ比例して増幅するため、買い戻し閾値を倍率に比例させることで、1倍ETF of 適度な調整（-8%）から、3倍レバレッジ特有の深い調整（-24%）までを同一の方程式で最適に射抜くことができます。引きつけすぎることによる機会損失と、早すぎる買い戻しによる下落第2波の被弾を最も良好に防止できます。
*   **半ポジ制限の理由**: 長期ベア局面での「ボラティリティ減価」や「底抜け」のダメージを防ぎつつ、キャッシュを半分残すことで、上昇トレンド復帰確定時に最後の半分を安全に仕込むことができます。これにより、ベア長期化局面での安全性を最大化しつつ、上昇局面の波を確実にとらえられます。

## 7. 開発・検証プロセス (Development & Verification Process)

本プロジェクトでは、データの整合性と本番環境の安全性を担保するため、以下の検証フローを原則とします。

### 7.1 検証用サンドボックス環境と安全なデプロイフロー (Promotion Pipeline)
ロジックの変更、DBスキーマの拡張、または新規指標の導入を伴う作業を行う際は、本番サービス（FastAPI / Uvicorn）に影響を一切与えず、以下の **Sandbox 安全検証・アトミックデプロイフロー** を徹底します。

1.  **Sandbox データ準備**:
    *   本番の最新 Parquet マスターを `data/parquet_master_sandbox/` に丸ごとコピーしてテスト用の歴史マスターを用意します（数秒で完了）。
2.  **Sandbox 隔離テスト (Isolate & Test)**:
    *   環境変数 `STOCKTOOL_DB_PATH="data/stocktool_sandbox.db"` を指定し、接続先をサンドボックスDBに完全隔離します。
    *   この状態で新規計算スクリプトやパイプラインを走らせ、`parquet_master_sandbox/` および `stocktool_sandbox.db` に対して、意図した新指標がエラーや不整合なく正しく計算・反映されるかをテストします。
3.  **Verify ステージ (視覚的確認)**:
    *   APIサーバーをサンドボックスDBに向けた状態で起動し、フロントエンド画面左上に「オレンジ色の警告バッジ（`⚠️ DB: stocktool_sandbox.db`）」が表示されていることを目視確認します。
    *   UIのチャートやテーブルで新指標が正確にバグなくレンダリングできているかを確認します。
4.  **本番コールドマスターの更新 (Promote to Cold)**:
    *   Sandbox検証が 100% 成功したら、検証済みの `data/parquet_master_sandbox/` の Parquet ファイル群を本番の `data/parquet_master/` へアトミックに差し替え（上書き・ポインタ更新）ます。
5.  **本番ホットキャッシュの超高速同期 (Promote to Hot)**:
    *   本番用 SQLite DB (`stocktool.db`) の該当キャッシュテーブルをクリア。
    *   復旧・リストア処理（`restore_sqlite_cache_from_parquet`）を実行し、差し替えた本番 Parquet マスターから直近2年分を native bulk insert で爆速インポート（わずか 3分）し、本番同期を完了させます。

### 7.2 安全性の担保
- **APIによる確認**: `/api/system/info` エンドポイントを叩き、`is_production` フラグが意図した状態であるかを確認する。
- **視覚的警告**: フロントエンドは本番以外のDB接続を検知するとオレンジ色の警告バッジを常時表示し、環境の取り違えを防止する。
