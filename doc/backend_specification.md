# バックエンド仕様書 (Backend Specification)

## 1. 概要
本プロジェクトのバックエンドは、株式およびインジケータデータの**「定期的な収集・算出バッチ（Pipeline）」**と、フロントエンド向けにデータを提供する**「APIサーバー」**の2つから構成されます。
データ分析に必要な指標計算を予めバッチで済ませてデータベースに蓄積することで、フロントエンドでの高速表示を実現しています。

> [!NOTE]
> **関連仕様書**
> `universe_db_specification.md`（銘柄定義マスター）/ `db_recovery_procedure.md`（DB 復旧・再構築）/
> `architecture.md`（ホット/コールド二層）/ `backtest_specification.md`

## 2. ディレクトリ構成
全てのバックエンド資産は `backend/` フォルダ配下に整理されています。
*   **`backend/api/`**: FastAPI ベースのWeb APIサーバー
*   **`backend/db/`**: SQLAlchemy によるデータベースモデルと接続プール管理（`stocktool.db` / `user_data.db` / `universe.db` の3系統）
*   **`backend/data_collection/`**: 外部データ取得。`yfinance`（`fetcher.py`）/ 銘柄定義の同期（`universe_sync.py`・`sheet_importer.py`）/ **SEC EDGAR**（`sec_client.py`・`sec_corporate_actions.py`）
*   **`backend/indicators/`**: 移動平均、ATR、RSI等のテクニカルおよび独自スコア計算ロジック（価格アノマリー分類 `price_anomaly.py` を含む）
*   **`backend/pipeline/`**: T1〜T5 のオーケストレータと各フェーズ、Parquet 世代管理（`parquet_cache_manager.py`）、排他ロック（`pipeline_lock.py`）
*   **`backend/scripts/`**: バッチ処理スクリプト (`update_pipeline.py`, `daily_sync_job.py` 等) と運用ツール

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

株価データの主体となる銘柄そのものの定義です。**`data/universe.db`（`symbols_master` / `theme_members`）から同期されます。**

> [!IMPORTANT]
> **同期は `(ticker, exchange)` を自然キーとした upsert で行い、既存の `symbols.id` を必ず温存します。**
> `daily_prices` / `indicators` / `relative_ranks`（各約157万行）と Parquet マスター全期間が
> `symbols.id` の整数FKで紐付いているため、id が振り直されると価格履歴が孤児化します。
> `universe.db` は独自の id 空間を持ちますが、**その id は一切持ち込みません**。
>
> 実装: `backend/data_collection/universe_sync.py` の `sync_symbols_from_universe()`
>
> `exchange` が変わると自然キーが外れて新 id が採番されるため、同一 ticker の既存行が
> 一意に定まる場合は**新規採番せず `exchange` を更新して id を温存**します（警告ログを出力）。
> 2026-07-28 に `GBTC` が `(GBTC,'US')` → `(GBTC,'NASDAQ')` となり、この救済が無かったため
> id=3256 と id=3258 の重複行が実際に発生しました。
>
> 旧経路（Google スプレッドシート同期 `orchestrator.sync_symbols_to_db()`）はロールバック用に
> 残していますが、通常経路では使用しません。`config.toml` の `extra_symbols` による銘柄注入は
> 廃止されました（注入時の `exchange` が `'US'` 固定で、上記の重複事故の原因になったため）。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。内部的なID管理に使用。 |
| `ticker` | STRING | 銘柄のティッカーシンボル（例: AAPL, SPY, _PHNC_）。 |
| `exchange` | STRING | 取引所コード（NYSE, NASDAQ 等）。仮想インデックスは `VIRTUAL`。 |
| `name` | STRING | 銘柄名称。 |
| `category` | STRING | 銘柄の分類（市場, 指標, セクタ, テーマ, 個別, レバレッジ）。 |
| `asset_class` | STRING | 資産クラス・属性（Industryなど）。 |
| `theme_type` | STRING | 詳細タイプ（`etf`: 実在ETF, `virtual`: 仮想指数, `sector`: セクタ指標, `theme`: テーマETF, NULL: 個別・レバレッジ）。**導出は `data_collection/symbol_classify.derive_theme_type()` に一元化**（後述）。 |
| `tags` | STRING | **カテゴリごとに意味が異なる多義カラム**（後述）。 |
| `active` | SMALLINT| ソフトデリートフラグ（1:有効, 0:無効）。 |
| `next_earnings_date` | DATE | 次回決算発表予定日。 |
| `updated_at` | DATETIME | 最終更新日時。 |

##### `theme_type` の導出規則

判定はかつて `spreadsheet_sync` / `universe_router` / `import_universe` の3箇所に分散し、
実データに不整合（IBIT / CPER が `theme` ではなく `etf`）を生んでいたため、
**`backend/data_collection/symbol_classify.py` の `derive_theme_type()` に一元化**しています。

```
exchange == 'VIRTUAL' もしくは ticker が `_..._` 形式 → 'virtual'
category == 'セクタ'                                  → 'sector'
category == 'テーマ'                                  → 'theme'
category in ('市場', '指標')                          → 'etf'
それ以外（個別 / レバレッジ）                          → None
```

> [!WARNING]
> ティッカーパターン（`_..._`）を第2の判定材料に加えているのは、`exchange` の設定漏れで
> 仮想テーマが実在銘柄として扱われる事故を防ぐためです。`_MRAD_` は `exchange='NYSE'` で
> 登録されていたため `theme_type='theme'` となり、**T2 は存在しないティッカーを yfinance に
> 取りに行って失敗し、仮想指数合成は `theme_type == 'virtual'` で対象を選ぶため対象外**という
> 板挟みで、構成銘柄12件を持ちながら価格系列が空のまま放置されていました。

##### `tags` のカテゴリ別意味論

`tags` は単一の意味を持たず、`category` によって格納される内容が変わります。

| category | `tags` の内容 | 例 |
| :--- | :--- | :--- |
| 個別 | **所属テーマのティッカーを CSV で列挙** | `AAPL` → `_AIED66_, _HRDW5E_, _VRLTC4_` |
| テーマ | **親セクタETF**（1件） | `IBIT` → `BLOK` / `GLD` → `GLTR` |
| 指標 | **分類ラベル** | `^VIX` → `Risk` / `TLT` → `Bond` / `UUP` → `Currency` |
| レバレッジ | **原資産ETF** | `TQQQ` → `QQQ` / `SOXL` → `SOXX` |
| 市場 / セクタ | 空（未使用） | — |

テーマの構成銘柄解決に使われるのは **個別カテゴリの `tags` のみ**です（§3.2 参照）。

##### `universe.db` 側の SEC 安定キー（`symbols_master`）

ティッカーは変わりますが、**CIK と classId は変わりません**。改称・上場廃止を機械的に
追跡するため、編集マスタである `universe.db` の `symbols_master` に
`cik` / `sec_class_id` / `sec_checked_at` の3列を持たせています
（`stocktool.db` の `symbols` には同期しません。運用判断用のメタデータのため）。

> [!NOTE]
> **列定義・キーの選び方・解決率・初回付与の手順は `doc/universe_db_specification.md` §4。**
> 検知ロジックと自動適用のガードは本書 §8.4。

### 3.2 構成銘柄連携 (`theme_constituents`)

テーマETFや「仮想指数（Virtual Index）」を構成する個別銘柄群を管理する連携テーブルです。バックテストの「テーマモメンタム」条件などで利用されます。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `theme_id` | INTEGER | `symbols.id` への外部キー。親となるテーマ/指数のID。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。構成銘柄（個別銘柄等）のID。 |
| `weight` | FLOAT | 構成比率（現在は主に 1.0 = 均等ウェイト）。 |

**紐付けロジック:**
- 親となるのは `category='テーマ'` の銘柄のみです。仮想テーマ（`theme_type='virtual'`）も実在ETF（GDX, WCLD 等）も同じ仕組みで扱い、テーマ全体の強さを個別銘柄に波及させるために利用されます。
- 構成銘柄は、**個別カテゴリの `symbols.tags` にテーマのティッカーが含まれる銘柄**を抽出して決定します（`tags` の多義性については §3.1 参照）。
- `weight` は `1 / 構成銘柄数` で均等配分されます。

> [!NOTE]
> `tags` は自由記述の CSV であるため、重複（`ANET` の `_HRDW4F_` が2回）や自己参照
> （`BODI` が `BODI` を含む）といった不正データが混入しうる欠点があります。
> 銘柄マスタの `universe.db` 移行に伴い、構成銘柄の正は正規化テーブル
> （`universe.db` の `theme_members`）へ移行し、`tags` はそこから逆生成する方針です。

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

#### 3.3.1 四半期決算データ (`earnings`)

個別銘柄の四半期ごとの財務データ（ファンダメンタルズ）です。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 |
| `period_date` | DATE | 決算期の基準日。 |
| `eps_basic` | FLOAT | 基本一株当たり利益（EPS）。 |
| `eps_diluted` | FLOAT | 希薄化後一株当たり利益（EPS）。 |
| `revenue` | FLOAT | 売上高。 |
| `net_income` | FLOAT | 純利益。 |

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

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `symbol_id` | INTEGER | `symbols.id` への外部キー。 |
| `date` | DATE | 評価日。 |
| `group_name` | STRING | 比較対象のグループ（`個別`, `テーマ` などの種類ごと）。 |
| `rs_value_rank` | FLOAT | `rs_value` のグループ内パーセンタイル順位 (0.00 〜 1.00)。 |
| `rs_ratio_rank_eN` | FLOAT | `rs_ratio_e5 / e14 / e21 / e63 / e200` のグループ内パーセンタイル順位。 |
| `rs_momentum_rank_eN` | FLOAT | `rs_momentum_e5 / e14 / e21 / e63 / e200` のグループ内パーセンタイル順位。 |
| `rs_trend_rank_sN` | FLOAT | `rs_trend_s5 / s14 / s21 / s63 / s200` のグループ内パーセンタイル順位。 |
| `rs_roc_ema_rank_eN` | FLOAT | `rs_roc_ema_e5 / e14 / e21 / e63 / e200` のグループ内パーセンタイル順位。 |
| `rs_macd_hist_rank_21` | FLOAT | `rs_macd_hist_21` のグループ内パーセンタイル順位。 |

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

#### 3.6.1 為替レート (`fx_rates`)

ポートフォリオの円換算に使用する為替レートです。**日足テーブル（T2）ではなく専用テーブルで管理**します。

| カラム名 | 型 | 説明・用途 |
| :--- | :--- | :--- |
| `id` | INTEGER | 主キー。 |
| `currency_pair` | STRING(10) | 通貨ペア。現在は `USD/JPY` のみ。 |
| `date` | DATE | 対象日。 |
| `rate` | FLOAT | レート（1ドルあたりの円）。 |

`(currency_pair, date)` に一意制約があります。SQLite に全期間永続保持し（軽量なため 730日パージの対象外）、**Parquet マスターにも含めます**（`latest_master.json` の `fx` キー）。Parquet 対象外だった頃は完全再構築のたびに為替履歴が消えていました（2026-08-01 修正、`doc/issue_list.md` 参照）。

**不変条件: 土日（非営業日）の行は存在しません。** 為替は日曜 17:00 ET 〜 金曜 17:00 ET に連続取引されるため、日足バーは月〜金にしか出ません。**米国の祝日は除外しません**（為替は米国休場日でも動くため、株式の取引日カレンダーと一致させてはいけません）。判定は `indicators/fx_calendar.py` の `is_fx_trading_day()` に一元化されており、書き込み側・読み取り側・バックフィルが同じ定義を使います。

**取得フロー（`pipeline/phases/t2_prices.py`）:**
- `sync_fx_rates()` が yfinance から `JPY=X` を取得し `USD/JPY` として格納します。取り込み時に非営業日のバーを除外します。
- 次の取得開始日（`max(date) + 1日`）が未来の場合はフェッチ自体をスキップします。yfinance は開始日 > 終了日を `possibly delisted; no price data found` として返すため、そのままだと毎回 ERROR がログに出て本当の障害が埋もれます。
- **`JPY=X` は T2（`daily_prices`）から明示的に除外**されており（`sync_phase_t2_prices` の `real_items` 構築時）、銘柄マスタにも登録しません。`sync_fx_rates()` はティッカーをハードコードで保持し `symbols` を参照しません。
- `tools/db_health_check.py` はティッカーに `=X` を含む銘柄をチェック対象外にします。

**参照側:** いずれも `indicators/fx_calendar.py` の `resolve_fx_rate()` を経由します。
- `api/portfolio_service.py` の `get_historical_fx_rate(db, target_date)` … `date <= target_date` で**最新の営業日**のレート。無い場合は最古の営業日レートにフォールバック。
- 同 `get_total_portfolio_summary()` … 最新の営業日レートで `total_equity_jpy` / `current_exchange_rate` を算出。

土日行が万一混入しても読み飛ばします。これは単なる防御ではなく**意味的に正しい**動作です — 土曜の取引に適用すべきレートは金曜終値であり、土曜行があってもそれを使ってはいけません。加えて `scripts/weekly_maintenance.py` の監査項目6が土日行を検出します（fix モードで除去）。書き込み側の除外は「混入しても壊れない」ことを保証しますが「混入していない」ことは保証しないため、新たな流入経路に気付くための3層目です。

> [!WARNING]
> **`--skip-fetch` 実行時のダミー値を本番 DB に書き込んではいけません。** 本テーブルは
> `(currency_pair, date, rate)` しか持たず、投入後にダミーと実データを区別できません。
> 2026-07-27 に実際に混入し、31行すべてがダミー値（`155.0 + day%5*0.2`）となって
> ポートフォリオの円換算が実勢 163.6 に対し 155.x で動き続けました。
> 現在は `_is_production_db()` により、接続先が `stocktool.db` の場合はダミー投入を
> スキップします（回帰テスト: `backend/tests/pipeline/test_fx_rates_refactoring.py`）。

> [!TIP]
> 全期間の再取得には `backend/scripts/backfill_fx_rates.py` を使用します（冪等・`--dry-run` 対応）。
> `yf.download` は HTTP 429 を "possibly delisted" として握り潰すため、本スクリプトは
> Yahoo chart API を直接呼びます。日付変換は `meta.exchangeTimezoneName`（`Europe/London`）で
> 行うこと。**UTC で変換すると金曜バーが土日にずれ込み、為替に存在しない土日行が生成されます。**

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
- **VCP ブレイクアウト特殊フィルタ (`is_vcp_breakout`)**: 「収縮した高値圏の土台からの、出来高を伴う大陽線ブレイクアウト」を検出するイベントフィルタ。**2026-07-14 現在、どの戦略にも使われていない**（E ファミリーは下記の通り状態版に復元済み。理由: 個別銘柄シナリオテストでイベント版が大敗（PF 0.7〜1.3）した一方、旧・状態版が最良だった実測。詳細: `doc/completed/e5_vcp_state_restore_plan.md`）。テスト済みの汎用フィルタとして温存（将来「ブレイク通知」用途で使う可能性）。条件（`high_window` = 63日 or 252日=52週で近傍ゲートを切替）:
    - 前日まで収縮: `prev_vcr <= vcr_contraction_max`
    - 高値圏の浅い土台: `prev_dist_52w_high_pct >= -base_high_tol`
    - 当日は N日高値の近傍: `dist_{W}_high_pct >= -near_high_tol`（緩いゲート）
    - ブレイクの大陽線: `change_1d_pct >= breakout_change`（イベントトリガー）／ 出来高膨張: `vol_surge_21 >= breakout_vol_mult`／ 地合い: `is_trend_template == 1`
    - **真のピボットクロス（`pivot_tol` 指定時のみ・2026-07-09 追加）**: 今日の終値が「昨日までの」N日最大高値の `(1 - pivot_tol/100)` 倍以上。`dist_Nd_high_pct` は当日を含むローリング最大で計算されるため直接使えないが、`change_1d_pct`（終値の前日比）から前日終値を復元すると等価式 `(1 + change_1d_pct/100) × (1 + prev_dist_{W}_high_pct/100) >= 1 - pivot_tol/100` で判定できる。土台内のリバウンド陽線や、ブレイク後の上昇継続中の大陽線での再発火を排除する。
    - **土台のドライアップ（`base_vol_dry_max` 指定時のみ・2026-07-09 追加）**: `prev_vol_surge_21 <= base_vol_dry_max`（前日の出来高が21日平均比で枯れていた）。枯れ→膨張のコントラストが VCP の核心。
    - **deny-by-default**: 前日カラムが無い場合は全 False（イベントは前日比較が本質で、他の prev 依存フィルタの no-op フォールバックとは意図的に挙動を変える）。`pivot_tol` / `base_vol_dry_max` 有効時に必要な prev カラムが無い場合も同様。
    - **設計メモ**: 当初は `dist_Nd_high_pct` のピボット距離クロスで検出したが、この指標はローリング最大に当日を含むため新高値を付ける強いブレイク日ほど終値が乖離して落ち、検出数が過少になった（2026-07-05 診断）。ブレイク検出を `change_1d_pct` の大陽線に切り替え、`dist_{W}` は緩い近傍ゲートに降格した。2026-07-09、比較対象を「前日までの」最大に取ることで欠陥を回避した真のクロス条件を `pivot_tol` として追加（詳細: `doc/completed/e4_vcp_pivot_dryup_plan.md`）。
    - 付随パラメータ（`is_vcp_breakout=true` に随伴。RRG の intensity_threshold と同じ扱い）: `breakout_high_window / vcr_contraction_max / base_high_tol / near_high_tol / breakout_change / breakout_vol_mult / pivot_tol / base_vol_dry_max`（後2者は未指定なら無効＝後方互換）。
- **E ファミリー（E1/E2）は状態スクリーン（2026-07-14 復元）**: `is_vcp_breakout` を使わず、通常の `min_/max_/is_` フィルタで「収縮した土台に**居続けている**銘柄」を継続的に検出する。条件（`backend/backtest/backtest_config.toml` 参照）:
    - `is_trend_template = true`（ミネルヴィニのトレンドテンプレート）
    - `max_vcr`（収縮度。ATR10/ATR50）／ `max_adr_pct_21`（値幅の静けさ）
    - `max_vol_surge_21`（出来高ドライアップ。21日平均比でスパイクが無いこと。e4 のイベント版ドライアップ条件を状態版に移植したもの）
    - `min_dist_63d_high_pct`（E1・中期）または `min_dist_52w_high_pct`（E2・52週）で高値近接度を指定
    - `min_up_down_vol_ratio_50`（買い集め傾向）
    - E2 のみ `is_close_gt_ema63 = true`
    - **設計判断の経緯**: 2026-07-05 にイベント版へ転換したが、個別銘柄シナリオテスト（実運用シナリオ）でブレイク日の引けを追いかける方式が機能せず（PF 0.7〜1.3、SPY 大幅アンダーパフォーム）、一方で誤って実行されていた旧状態版が最良の結果（PF 3.66）を示したため状態版へ復元。イベント化の当初動機（同一土台の重複カウントで LCB が水増しされる問題）は、2026-07-05 に別途導入された「同一銘柄は exit まで再エントリー禁止」ブロックで既に解消済みだったため、製品定義を変える必要が無かったと判明。

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
    *   `shares` (FLOAT): 現在の保有株数（Trimで減少）
    *   `original_shares` (FLOAT): 購入時の株数
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
    *   `entry_shares` (FLOAT): この売却分の元株数
    *   `exit_date` (DATE), `exit_price` (FLOAT): 売却情報
    *   `exit_shares` (FLOAT): 売却株数
    *   `exit_reason` (STRING): `'stop_loss'` | `'take_profit_trim'` | `'take_profit_full'` | `'trailing_stop'` | `'manual'`
    *   `pnl_pct` (FLOAT): 損益率
    *   `pnl_amount` (FLOAT): 損益額
    *   `holding_days` (INT): 保有日数
    *   `memo` (TEXT, NULL)
    *   `created_at` (DATETIME)

### 3.10 SQLite 運用設定 (Performance & Concurrency)
本プロジェクトの SQLite は、多数の API リクエストと大量のバッチ処理を並行させるため、以下の設定を適用している。

*   **Journal Mode: `WAL` (Write-Ahead Logging)**: 読み取りと書き込みの競合を大幅に軽減。
*   **読み取り/書き込みエンジンの分離**: 読み取り（API, `get_db()`）は `DEFERRED` + busy_timeout 30秒、書き込み（パイプライン, `get_write_db()`）は `BEGIN IMMEDIATE` + busy_timeout 3600秒。SELECT が書き込みロックを要求して長時間バッチにブロックされる事故を構造的に防止（詳細: architecture.md §10.2）。
*   **write セッションの3つの禁止事項**: ① `pd.read_sql(query, db.bind)`（自己デッドロック。`get_read_engine_for(db)` を使う）② `PRAGMA synchronous` の実行 ③ `VACUUM` の実行（素の sqlite3 接続で行う）。詳細と根拠: architecture.md §10.2。
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
| **Phase 6** | `fundamental_data` (未実装) | yfinance API | - | **定期リフレッシュ (未実装)**: `market_cap` 等は `DailyPrice` テーブルに直接統合され日次更新されており、独立した T6 フェーズとしては未実装。 |

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
*   **`GET /api/screener`**: スクリーナー用のカスタムフィルタ（「SMA50より上」「時価総額 10M以上」等）に合致する銘柄群と各指標値を返却。フィルタはクエリパラメータで直接指定する（プリセットはフロントエンド側の概念で、このエンドポイントに `preset` 引数は無い）。
*   **`GET /api/screener/dashboard`**: `data/screener_presets.toml` の全プリセットを一括評価し、Rise / Fall のカテゴリ別にまとめて返却。
*   **`GET /api/screener/presets` / `GET /api/screener/meta`**: プリセット定義とフィルタ項目のメタ情報を返却（UI のフォーム構築用）。
*   **`GET /api/group_data/{ticker}`**: セクタまたはテーマの「グループ詳細画面」用データ。
    - **クエリパラメータ**: `date: str | None`（`YYYY-MM-DD`）。指定日以前の最新営業日にフォールバックする。
    - **レスポンス構造**: `GroupDataResponse` 型。ETF 自体の詳細情報（`feature`）と、構成要素（テーマまたは銘柄）のリック（`constituents`）を含みます。
*   **`GET /api/theme/{symbol_id}`**: テーマ詳細（構成銘柄一覧つき）。グループ詳細画面はテーマの場合、`group_data` を取得したあとこの API のレスポンスで上書きするため、**画面に見えている構成銘柄の値はこちらが出処**。
    - **クエリパラメータ**: `date: str | None`（`YYYY-MM-DD`）。指定するとその日以前の最新営業日時点のスナップショットを返す（休場日は直前の営業日にフォールバック）。省略時は最新営業日。
    - **基準日の一貫性**: テーマ本体と構成銘柄の価格・指標・ランク・チャートはすべて同一の `target_date` を基準に取得する。構成銘柄側は `preload_constituent_details(db, ids, target_date=...)` と `preload_sparklines(db, ids, str(target_date))` に基準日を渡すことで担保している（渡し忘れると構成銘柄だけ最新日の値が混ざる）。
    - **`chart_data` の並び順は日付昇順（末尾が最新）** — 本体・構成銘柄とも。これは `/api/chart/{symbol_id}` や `group_data` の `feature` を含む全 API 共通の規約であり、フロントの `RrgChart` が `slice(-trailLength)` と `isLast = i === points.length - 1` で**末尾を最新として扱う**ため、降順にすると軌跡が最古 N 日になりティッカーラベル付きの現在位置が過去の点に打たれる。`preload_constituent_details` の `price_history_126` は既に昇順なので、消費側で `reversed()` してはならない（2026-07-17〜2026-08-19 の間、この二重反転で構成銘柄の RRG が半年前の位置を表示していた）。

### 5.0 スクリーンフィルタ仕様レジストリ (`indicators/screener_registry.py`)

スクリーン条件は **① フロントのスクリーナー（SQLite/SQLAlchemy）／② 最適化バックテスト（Parquet/pandas）／
③ 個別銘柄シナリオテスト（②と同一エンジン）** の3経路で評価される。
D-2（2026-07-04）で特殊ブールフィルタの実体は `indicators/screener_filters.py` に一本化されたが、
**キーの解釈と必要カラムの宣言が依然として4箇所ずつに分散**しており、
「片側だけ実装／黙って素通し」という障害が繰り返し発生していた。

2026-08-10、**フィルタキーの唯一の定義場所**として `indicators/screener_registry.py` を新設した。

#### 責務と設計制約

| 項目 | 内容 |
| :--- | :--- |
| **役割** | フィルタキー文字列（`"min_vol_surge_21"` 等）を `FilterSpec` に解決する唯一の場所 |
| **純粋性** | `indicators/` の定義（純粋な計算モジュール群）を守るため、**pandas も SQLAlchemy も import しない**。「どのカラムが実在するか」は呼び出し側が渡す `known_columns` / `rank_columns`（文字列集合）に委ねる |
| **利点** | 「宣言されている」だけでなく **「実際に供給されている」ことを検査できる**。バックテスト側は `merged.columns` を渡すため、マージ衝突や列欠落がその場で露見する |

#### 主な公開要素

| 要素 | 内容 |
| :--- | :--- |
| `FilterSpec` | `kind`（`numeric` / `rank` / `theme_numeric` / `theme_rank` / `bool_column` / `close_gt` / `special`）・`column` / `op` / `requires` / `prev_requires` / `params` |
| `RequiredColumns` | `today` / `prev` / `ranks`（**ハード要求**。欠けたら失敗）＋ `optional`（**ソフト要求**。あれば引くが無くても失敗させない。典型は `sort_column`） |
| `resolve_filter_spec()` | キー1つを解決。解決順序は EXPLICIT → theme_rank → theme_numeric → rank → numeric → bool_column |
| `resolve_required_columns()` | 戦略 dict 全体から必要カラム集合を導出。データ供給側はこの結果だけを見ればよい |
| `METADATA_KEYS` / `ATTACHED_PARAM_KEYS` / `is_non_filter_key()` | フィルタではない制御キー（`sort_column` 等）と特殊フィルタの随伴パラメータ（`pivot_tol` 等）。**除外集合もフィルタ集合と同じく1箇所に集約する** |
| `RANK_FRAME_ALIASES` / `to_frame_column()` | 正準名（`rs_ratio_rank_e21`）↔ フレーム内名（`rs21_rank`）の対応 |
| `OUTPUT_EXCLUDED_CATEGORIES` | 最終出力から常に除外するカテゴリ（`テーマ`）。**フィルタ処理中は保持し出力直前でだけ落とす**（リーディングテーマ判定と構成銘柄への波及に必要なため） |
| `VIRTUAL_COLUMNS` | 仮想（計算）カラム名。SQL 式は `screener_router`、pandas の導出は `apply_filters_to_df` にあり、レジストリは名前だけを持つ |

#### fail-loud の契約

| 検査 | 例外 | 経路ごとの扱い |
| :--- | :--- | :--- |
| キーがどの kind にも解決できない | `UnknownFilterKeyError` | **CLI**（バックテスト／最適化／シナリオ）は `ValueError` で停止。**API** は該当プリセットのみ `items=[]` ＋ `error` を返し、他は正常表示 |
| 必要カラムが実際には供給されていない | `MissingFilterColumnError` | 同上 |

いずれも `ValueError` のサブクラス（`report_strategy_scan_coverage` 等、既存の fail-loud 実装と契約を揃えるため）。

> [!IMPORTANT]
> **新しいフィルタを追加するときは、レジストリへの登録が必須。** 登録しないキーは
> 「未知のキー」として実行時に停止する。これは意図した設計で、**黙って無視されるより
> 止まる方が安全**という判断に基づく（過去、`is_trend_template` が実列名との不一致で
> バックテストにおいて完全に no-op のまま長期間放置された事故がある）。

#### 単一フィルタエンジンとフレーム契約（2026-08-18 / Phase 3）

レジストリで**キーの解釈**を一本化したのに続き、**評価エンジンそのもの**も一本化した。
`/screener` と `/screener/dashboard` は SQL でフィルタを組み立てるのをやめ、
基準日1営業日分の断面を DataFrame（**ScreenerFrame**）として構築して
`backtest/backtest_screener.py::apply_filters_to_df()` に渡す。
これは最適化バックテスト・個別銘柄シナリオテストが呼ぶのと**同一の関数**である。

| 要素 | 場所 | 役割 |
| :--- | :--- | :--- |
| `ScreenerFrame` の契約 | `indicators/screener_frame.py` | 同一性列（`symbol_id`/`ticker`/`name`/`category`/`active`）の存在と、数値列が `object` dtype でないことを `assert_frame_contract()` で検査 |
| SQLite → フレーム | `api/screener_cross_section.py::load_cross_section()` | `Indicator` 全列 ＋ `RelativeRank` 全列（フレーム内名へリネーム）＋ 前日列 ＋ 価格・同一性列。母集団は `active=1` かつ `category in ('テーマ','個別')` |
| Parquet → フレーム | `backtest/backtest_runner.py::preload_data()` | §6.7 参照。母集団の絞り込みは `apply_filters_to_df` 冒頭で同一条件を適用 |
| フィルタ評価 | `backtest/backtest_screener.py::apply_filters_to_df()` | **唯一のフィルタエンジン**。SQL 側に同等のロジックは存在しない |

これにより、API 側にあった `_apply_filter()` / `_parse_expression_to_filter()` /
`_RANK_COLUMN_ALIASES` 等の**SQL 版フィルタ実装は全て削除**された。
「片側にだけ実装した」という障害は、実装先が1つしか無くなったことで構造的に発生しない。

**ランクは全経路で wide 形式**（`rs_ratio_rank_e21` 等が列名）。
`indicator_name` / `percent_rank` という long 形式は Phase 3d で廃止された。
正準名とフレーム内名の対応は `screener_registry.to_frame_column()` が一手に担う。

#### 全経路共通のルール

| ルール | 実装 | 適用先 |
| :--- | :--- | :--- |
| 流動性ハード制約 `min_avg_dollar_volume_21` | `backtest/common_constraints.py`（値の解決と注入を一本化） | `backtest_runner` / `optimization_runner` / `scenario_runner` / `/screener` / `/screener/dashboard` |
| テーマ行の最終出力除外 | `screener_registry.OUTPUT_EXCLUDED_CATEGORIES` | `apply_filters_to_df` / `/screener` / `/screener/dashboard` |

#### `applied_filters`（実際に適用されたフィルタの記録）

`GET /api/screener/dashboard` のレスポンスは、カテゴリごとに `applied_filters`（適用キーのソート済みリスト）を返す。
常時適用の制約（`min_avg_dollar_volume_21` / `exclude_theme_category`）も含む。
バックテストは戦略ごとに実行開始時へ1行ログを出す。

> 「設定したのに効いていない」という失敗は、結果を見ても分からないのが最大の問題だった
> （流動性床は2度にわたり無効化されていた）。適用キーを結果に残すことで、
> 数値を読む前に気づけるようにしている。
>
> **制約**: `GET /api/screener` はレスポンスが `List[ScreenerResultItem]` で包み構造を持たず、
> エンベロープ化がフロントエンドの破壊的変更になるため、こちらは `logger.info` への出力のみ。

### 5.1 表示ロジックの共通化 (Indicator Building Helpers)
ダッシュボード、ウォッチリスト、およびグループ詳細画面間での指標表示の整合性を保つため、バックエンド側で以下の共通ヘルパー関数を定義しています。
- **`_build_etf_feature`**: ETF の主要騰落率、SMA 乖離率、およびミニチャート用時系列データを構築。
- **`_build_panel_item`**: 銘柄一覧（Sectors, Themes, Stocks）の 1 行分のデータ（RS ランク、スパークライン、1D/1W/1M 騰落率）を構築。
- **`_build_leading_item`**: 先行指標用のコンパクトなメトリクスを構築。**相対ランクを引数に取りません**（`_build_panel_item` との差異）。先行指標は資産クラスがばらばらで相互のパーセンタイル比較に意味がないためです。

> [!NOTE]
> leading パネルの対象は **`category='指標'` のみ**です。かつて IBIT / CPER をティッカー
> 直指定でテーマと兼任させていましたが、暗号資産は GBTC、銅は CPER 自身を正式に
> `category='指標'` へ移したためハードコードは撤廃しました（`dashboard_router.py` /
> `universe_router.py`）。

これらの関数は内部で **None 安全な数値変換 (float coercion)** を行い、フロントエンドでのレンダリングエラーを防止しています。

### 5.1.1 symbol_id 自己修復 (heal) の共通仕様

ウォッチリスト・ポートフォリオの各項目は ticker/exchange を永続キーとし、`symbol_id` は「接続中の stocktool DB に対するキャッシュ」として扱われます。T1 再同期で symbols.id が変わった場合、読み取り API が自動修復（heal）します。実体は `backend/api/symbol_heal.py` の共通コアです（2026-07-04 導入）。

| 機構 | 仕様 |
| :--- | :--- |
| **修復** | `symbol_id` が現行 symbols と不一致の項目を、ticker+exchange（フォールバック: ticker のみ）で再解決。解決不能は `symbol_id=NULL`（API レスポンスでは `symbol_id: null` として返り、リスト全体は 200 を維持） |
| **改称の追随** | ticker で解決できなかったとき、**NULL 化する前に `universe.db.ticker_history` を引いて現行ティッカーへ辿る**（多段改称 A→B→C も追跡、循環は `MAX_RENAME_HOPS=10` で打ち切り）。解決できたら `symbol_id` と併せて **`ticker` 文字列も現行へ書き換える**。`ticker_history` の読み込みは**解決不能が出たときだけ**実行され、正常時のリクエストパスに universe.db 接続を持ち込まない |
| **安全弁** | 解決不能率が **30% (`HEAL_MAX_UNRESOLVED_RATIO`) を超えた場合、一切書き込まず WARNING ログ**を出す。大量解決不能は「接続先 symbols が不完全」（Sandbox 誤接続・T1 同期途中）のシグナルであり、NULL 化も再マッピングも破壊的になるため |
| **スロットル** | 前回の heal が clean（修復ゼロ・安全弁非発動）だった場合、**60分 (`HEAL_CLEAN_TTL_SECONDS`) 間は再実行をスキップ**。修復発生直後・安全弁発動中は毎回実行される。キーは（種別, stocktool DB, user DB）の組で、接続先を切り替えると独立にカウント |

#### ティッカー変更への追随（push + pull の2層）

ticker を永続キーにする設計は「DB を作り直しても追随できる」ためのものですが、
**ticker 自体が変わると全段が外れます**。2026-08-06 に `ATLN`（→ `CIRC`）が実際に宙に浮きました。
そこで2つの経路で追随します。

| 経路 | いつ効くか | 実装 |
| :--- | :--- | :--- |
| **push** | 改称を適用したその場。次の画面表示から正しいティッカーで出る | `rename_symbol.rename_user_data_references()` — `watchlist` / `portfolio_positions` を更新 |
| **pull** | API の heal 実行時。取りこぼし・過去分の保険。多段改称も辿る | 上表「改称の追随」 |

> [!IMPORTANT]
> **`position_history` の `ticker` は書き換えません。**
> あれは「その時どの銘柄を売買したか」の記録であり、後から現行ティッカーへ書き換えると
> 取引履歴として不正確になります。`symbol_id` だけを現行へ解決します
> （`symbol_heal.TICKER_IMMUTABLE_TYPES`）。

改称の検知自体は SEC EDGAR との週次突合が担います（§8.4）。CIK / classId を
ウォッチリスト側にも持たせる案は見送りました。`ticker_history` は改称を適用する
同じコード（`rename_symbol.py` と Universe Manager の両方）が必ず書くため守備範囲が重なり、
指数・仮想テーマ（CIK なし）の例外処理が増える割に得るものが少ないためです。

> [!WARNING]
> Sandbox 検証時は、設定漏れによる本番汚染（片方だけ指定し heal が本番 user_data.db に向く事故）を防ぐため、**単一の環境変数 `STOCKTOOL_ENV=sandbox` を使用することを強く推奨します。**
> 個別のレガシー変数を使う場合は `STOCKTOOL_DB_PATH` と `STOCKTOOL_USER_DB_PATH` を**必ずセットで**指定すること。安全弁はこの誤設定に対する最終防衛線であり、頼る前提で運用しないこと。

### 5.1.2 構造ピボット API (`GET /chart/{symbol_id}/structure_pivot`)

チャート描画用に **LL-HL 構造ピボット**（TradingView 公開スクリプト
`Structure Pivot (LL-HL / HH-LH)` のロング側）をリクエスト時に計算して返す。

| 項目 | 内容 |
| :--- | :--- |
| **実体** | `indicators/structure_pivot.py`（pandas 非依存の純 numpy 関数） |
| **ルータ** | `api/chart_router.py::build_structure_pivot_response()` ＋ 同名の薄いルータ |
| **クエリ** | `full_range`（既定 false。true で Parquet マスターの全期間）／`min_len`（既定2）／`max_len`（既定10） |
| **レスポンス** | `metadata` ＋ `structures`（履歴）＋ `current`（生存中の構造 or null） |

**T3 (`indicators`) にはカラムを持たない。** 1銘柄あたり最大2,000本程度で
計算コストが無視できるため事前計算しない。スクリーナー／バックテストにも
結線していない（実測の結果、採用を見送った。根拠は
`doc/completed/structure_pivot_chart_plan.md` §1）。

#### 検出ロジックと2つの落とし穴

1. **確定遅延（先読み防止）**: `ta.pivotlow(low, L, L)` は左右 L 本を見る中心窓なので、
   ある足がピボットだと確定するのは **L 本先**。本実装は
   `confirmed_index = hl_index + length` 以降にしか構造を返さない。
   Pine はチャート上で過去バーの位置に描くため「その時点で分かっていた」ように
   見えるが実際には分かっていない。**将来スクリーナーへ転用する場合、ここを崩すと
   黙って成績が良くなる**（型1バックテストは検出数と質を最適化するため気付けない）。
2. **`Query()` 既定値の truthy 問題**: FastAPI のルータ関数を
   テストから直接呼ぶと `Query(False)` が Query オブジェクトのまま渡り、
   **truthy なので `if full_range:` が常に真になる**。実装中、テストが
   ユニット DB ではなく本番 Parquet を読んでいた。ロジックは素の引数を取る
   純関数側（`build_structure_pivot_response`）に置き、ルータは委譲だけにする。

`Structure` の項目: `length` / `ll_date` / `ll_price` / `hl_date` / `hl_price`（＝損切り候補）/
`pivot_date` / `pivot_price`（＝ブレイクアウト・トリガー）/ `confirmed_date` / `end_date` /
`invalidated` / `is_current` / `broken_at_confirmation`。

> [!NOTE]
> `broken_at_confirmation` は「構造が確定した時点で既に終値がピボットを超えていた」ケース。
> 本番 Parquet 5年の実測で **27〜28%**（長さ帯によらずほぼ一定）が該当する。
> 確定遅延の実害を示す指標として残している。

### 5.2 ウォッチリスト API

| Method | Path | 説明 |
| :--- | :--- | :--- |
| `GET` | `/api/watchlist` | 全ウォッチリスト取得（active/removed 両方 + メトリクス算出） |
| `POST` | `/api/watchlist` | 登録（1時間ルール付き） |
| `DELETE` | `/api/watchlist/{ticker}` | 解除（1時間以内は物理削除、それ以外は論理削除） |
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

### 6.1 テスト体系における位置づけ（3種類のテストの役割分担）

当システムの検証は**役割の異なる3種類のテスト**で構成される。本章（§6）が扱うのは最適化バックテストのみ。
それぞれ「何を最適化するか」「何を意図的に無視するか」が異なるため、
片方の設計判断をもう片方の基準で評価しないこと（例: 最適化バックテストに手数料がないのは欠陥ではなく分担）。

| | 最適化バックテスト | ETFシナリオテスト | 個別銘柄シナリオテスト |
|---|---|---|---|
| **目的** | スクリーン条件（フィルタパラメータ）の最適化と「シグナルの質」の評価 | ETF に対するトレード戦略の見極め | 最適化バックテストで最適化したスクリーンを入力に、トレード戦略の比較・最適化 |
| **主なモジュール** | `backtest_runner.py` / `optimization_runner.py` | `etf_single_runner.py` 系 | `scenario_runner.py` / `scenario_portfolio.py` / `run_scenario_batch.py` 系 |
| **資金モデル** | **無限資金**（全シグナルを独立トレードとして評価） | 有限資産 | **有限資産**（ポジションサイズ・同時保有数の制約あり） |
| **レジーム認識** | **しない（意図的）**。ベアは事後にしか分からないため、スクリーン自体はレジーム非依存に安定して機能するものを選抜する | 戦略による | **する**。Market Trend Score（MTS）等を参照してポジションサイズを調整 |
| **コスト（手数料・税金）** | **なし**（2026-08-24 以降。`[general] consider_tax_optimization` の既定 0.0。理由は下記）。手数料も依然なし。流動性連動コストのみ売買代金ハード制約で代替 | 導入可 | **あり**（`[general] consider_tax`。既定 0.2 = 20%） |
| **出口ルール** | 固定（§6.4）。スクリーン間の比較条件を揃えるため | 戦略の一部として比較 | 戦略の一部として比較・最適化 |
| **主指標** | **1トレードあたりの幾何平均リターン**（`geo_mean_gain`）。検出件数はゲート（§6.5） | 戦略比較指標 | ポートフォリオ資産曲線・CAGR・MDD |

**分担の帰結（最適化バックテストに導入しないもの）**: ポジションサイズ、資金制約、レジーム連動の出入り、
手数料。これらは全て個別銘柄シナリオテストの責務。逆に「シグナルそのものの統計的な質」
（期待値の信頼性・レジーム横断の勝率安定性・執行依存度）は最適化バックテストでしか評価できない。
**税金は型1（最適化）と型2・型3（実運用シミュレーション）で分離している**（2026-08-24〜）。
型1 は `consider_tax_optimization`（既定 **0.0**）、型2・型3 は `consider_tax`（既定 **0.2**）を読む。

> 2026-08-20 に一度は「3種のテスト全経路で同一値を共有」としたが、実測で覆った。
> 税込みで最適化するとパラメータが変わった10戦略のうち**8戦略で型3 の Calmar が悪化**した。
> 質（1取引あたりのリターン）は上がるものの、税が取引回数を罰するため探索が
> 「取引を絞る」方向へ強く引かれ、**有限資産・8枠の型3 では枠が埋まらず回転不足になる**。
> 税は実運用の現実であって**スクリーンの良し悪しを測る物差しではない**、というのが結論。
> 詳細: `doc/completed/objective_quality_first_plan.md`

**検証の階層**: 最適化バックテストは内部にホールドアウト検証（§6.5.2）を持つ。個別銘柄シナリオテストの5年通し実行も
実質的な検証として機能するが、期間が最適化バックテストの学習期間を内包するため、
未学習期間での汎化確認は最適化バックテストのホールドアウトが担う。

#### 個別銘柄シナリオテストがスキャンする戦略の範囲

**ロング専用**です。`ScenarioPortfolio` が買い建てしか行わないため、`Fall`（下落・売り目線）の
preset は `load_scenario_config` がロードはしますが、シグナルスキャンでは扱いません。

スキャン対象は戦略名が `Rise - Check` で始まるものだけです。戦略名は
`f"{section.capitalize()} - {group} - {name}"` で組み立てられるため、**preset TOML の
`group` が `"Check"` 以外だとスキャン対象外**になります。

この除外は以前は無言で行われており、新しい preset を足すと気づかないまま無視される状態でした
（2026-07-20 発見）。現在は `scenario_runner.report_strategy_scan_coverage()` が
除外対象を理由つきで報告し、**対象が1件も無ければ例外で停止**します
（「実行できたがシグナル0件」を正常な結果として受け取らないため）。
接頭辞は `SCENARIO_TARGET_PREFIX` に一元化されており、`ScenarioScorer` も同じ値を使います。

#### 個別銘柄シナリオテストの結果指標（`scenario_summary.json` / API）

`backend/backtest/scenario_reporter.py` の `generate_summary()` が run ごとに書き出す
`scenario_summary.json` には、CAGR・Profit Factor・Max Drawdown・Win Rate 等に加えて
**`avg_trade_pnl_pct`（1取引あたり平均リターン%）** を含む。トレードの `pnl_pct`
（建玉に対する損益率、小数）の単純平均を % に換算した値（例: `5.14` = +5.14%）。
CAGR は取引数が多いほど複利効果で膨らみやすいため、「取引数を増やさずに1回あたりの
質が高い戦略」を見分ける指標として、Profit Factor と並べて使う（2026-08-19 追加。
判断根拠は B6 パラメータ改定時の事例: CAGR だけでは取引数27%増の旧パラメータが
勝って見えるが、1取引平均リターンでは新パラメータが上回っていた）。

API (`GET /api/backtest/scenario/{name}/summary`) は以下のルールで解決する:
- 単一 run: `scenario_summary.json` に値があればそれを使う。**無ければ**同じ run
  ディレクトリの `scenario_trade_logs.csv` の `pnl_pct` 列から動的に算出する
  （2026-08-18 以前の結果に `avg_trade_pnl_pct` が無いための後方互換フォールバック。
  CSV も無い/壊れている場合は例外にせず `null` を返す）。
- Monte Carlo グループ（複数 run の統合）: **run ごとに平均を算出してから、その平均を
  取る**（B案）。`profit_factor_avg` 等の既存の Monte Carlo 集計と同じ流儀に揃えるため。
  全 run のトレードを合算してから単純平均する A案は、取引数の多い run の影響が
  過大になり他の指標と一貫しなくなるため採用しない。API レスポンスは
  `avg_trade_pnl_pct`（= `avg_trade_pnl_pct_avg` と同値）と `avg_trade_pnl_pct_avg` の
  両方を返す（`profit_factor` / `profit_factor_avg` と同じパターン）。

#### 複数戦略の組み合わせ（和集合）ジョブ

`run_scenario_batch.py` は1ジョブに**複数の戦略/study を束ねた preset**を生成できる
（2026-08-21 追加、`doc/completed/multi_strategy_scenario_plan.md` 参照）。

**背景**: 質の高い戦略ほど枠稼働率が低い（=8枠を使い切れていない）ことが判明した。
低質な戦略で枠を埋めるのではなく、**質の高い戦略同士を組み合わせて取引数を増やせるか**
を検証するために導入した。

**`generate_preset_toml()` の入力形式**: `[(strategy_name, best_params), ...]` のリストを
受け、戦略の数だけ `[[rise]]` ブロックを生成する。全ブロックとも `group = "Check"` を
維持する（`SCENARIO_TARGET_PREFIX = 'Rise - Check'` が拾えなくなるため — 上記
「スキャンする戦略の範囲」参照）。要素数1（単一戦略）で呼び出した場合の出力は、
このリスト対応版になる前とバイト単位で同一（既存ジョブの結果に影響しない）。

**`scenario_batch_jobs.toml` のジョブ定義**: 単数形 `strategy_code` / `study_name`
（従来どおり）に加え、複数形 `strategy_codes` / `study_names`（配列、同じ長さ）を
指定できる。単複の併記はエラー、長さ不一致もエラーで停止する。

```toml
[[job]]
name = "B256_union"
strategy_codes = ["B2_theme_rsrank_momentum", "B5_rs_trend_with_theme", "B6_rs_macd_and_theme"]
study_names    = ["B2_theme_rsrank_momentum", "B5_rs_trend_with_theme", "B6_rs_macd_and_theme"]
source = "optuna"
  [job.portfolio]
  min_score = 1     # 和集合。どれか1戦略でも拾えば買う
```

**単一戦略ジョブとのエラー処理の違い**: 単一戦略ジョブは `study_name` 欠落や Optuna
best params 未取得を「その戦略だけ Skip して次のジョブへ continue」で扱う（従来どおり
変更なし）。一方、複数戦略ジョブは構成する study の**1つでも欠けたらバッチ全体を
エラーで停止**する（`sys.exit(1)`）。一部だけ読めた状態で走らせると、意図した組み合わせ
と異なる（=戦略が抜け落ちた）まま実行されてしまうため。

**`score`（トレードログの列）**: `ScenarioScorer.score_signals()` は同一銘柄を何戦略が
同時に拾ったか（1〜組み合わせ戦略数）を `score` として候補ごとに算出し、
`ScenarioPortfolio` がポジション・トレード記録までそのまま引き継ぐ。
`scenario_trade_logs.csv` には `ticker` の直後に `score` 列として出力される
（単独戦略ジョブのトレード記録には無いため、その場合は列自体が出力されない）。
`min_score` を上げると「score 2 以上（=複数戦略が重複して拾った銘柄）だけ採用」という
合流条件に切り替えられる。組み合わせの価値検証は `score` 別に成績（1取引%・勝率）を
分解して行う。

### 6.1.1 最適化バックテストの目的
スクリーナーの各種フィルタ条件セット（戦略）の有効性を、過去のヒストリカルデータに対して
シミュレーションし、**1トレードあたりの質（avg_gain）× DD の回復コスト**を主軸に、
**検出件数はゲート**（実用帯の内側なら影響なし・外側なら失格）として定量的に評価・比較する
（2026-08-22 再設計。旧: 期間CAGR 合成 → さらに旧: Expectancy LCB 主軸）。

**なぜ CAGR 主軸をやめたか**: 型1 の `period_CAGR` は質と量を混ぜた指標であり、
「量を増やしてもスコアが伸びる」経路を残していた。一方**枠を埋めるのは組み合わせの仕事**であって
個々の戦略の仕事ではない（複数戦略の和集合ジョブが単独を大きく上回ることを 2026-08-22 に実証。§6.1）。
個々の戦略に求めるのは **「質が高いこと」と「最低限の検出があること」の2つ**でよい。

無限資金・avg_slots 正規化のままで、固定枠・コスト・レジーム連動は個別銘柄シナリオテストの
責務（§6.1）。**最終評価が CAGR と DD であることは変わらない** — 変えたのは
「型1 が何を目指すか」だけであり、CAGR と DD は型3 で測る。
パラメータの調整→再実行を繰り返す反復的なワークフローを前提とした設計。

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
consider_tax = 0.2              # 単位は率（0.2=20%）。型2・型3（実運用シミュレーション）専用
consider_tax_optimization = 0.0 # 型1（最適化バックテスト）専用。既定は税なし（§6.1 参照）

[[strategy]]
name = "F_elite_momentum97"
min_rs_ratio_rank_e21 = 0.97
is_trend_template = true
min_market_cap = 3e8
```

### 6.4 エントリー・出口ルール

| ルール | 条件 |
|---|---|
| **エントリー** | `[general] entry_mode` で選択: `"close"`（デフォルト。スクリーン該当日の終値で買い。24時間取引口座での「引け後データ更新→オーバーナイト発注」運用と整合）/ `"next_open"`（シグナル翌営業日の寄付で買い。保守側の感度計測用。翌営業日データが無い銘柄はシグナル破棄） |
| **再エントリー** | **禁止**（建玉存続中の同一銘柄シグナルはスキップ、exit 翌営業日から可）。急騰継続銘柄の連日シグナルで1つのムーブが複数トレードに水増しされるのを防ぐ（2026-07-05 改修） |
| **流動性足切り** | `[general] min_avg_dollar_volume_21`（最適化対象外・全戦略共通のハード制約。デフォルト $2M/日）。21日平均売買代金未満の銘柄はシグナル対象外 |
| **損切り** | エントリー価格から -8%（**終値ベース評価** = 「-8%割れ確認→引け成行」運用の忠実な再現。ギャップダウンは実際の終値で記録される） |
| **1/3利確** | +20%超え or SMA50/ATR% >= 8 → 残りの損切りラインをエントリー価格に引き上げ |
| **全利確** | EMA21を終値で2日連続下回る |
| **タイムストップ** | 7営業日のレンジ（高値-安値） < 1ATR → 強制退出 |
| **フェイルセーフ** | 120営業日で未決済 → 強制退出 |

### 6.5 評価指標

| 指標 | 説明 |
|---|---|
| **最適化スコア** | **主指標は `geo_mean_gain`（1トレードあたりの幾何平均リターン%）**（2026-08-24 改訂）。期間ごとに算出し、全学習期間で平均する。<br>・**検出件数ゲート**: `avg件/日` が `quality_gate=(lo, hi)` の**外側なら失格**（大きな負値）。**内側ならスコアに一切影響しない** — 「量を増やして点を稼ぐ」経路を断つため、旧版の `detect_adequacy` による掛け算をやめてゲート化した。下限は `strat.quality_gate_min_hits_per_day ?? min(グローバル既定 0.3, strat.min_avg_hits_per_day)`。**戦略が自分より低い下限を宣言していればそちらを尊重する**（引き締めにならない流儀）。<br>・**赤字のガード**: 幾何平均が0以下なら**掛け算の経路に入れず素の値を返す**。掛け算のペナルティは負のスコアでは「罰」ではなく「改善」として働くため（詳細 §6.5.1）。<br>・**lcb_gate**: `expectancy_lcb ≤ 0` のとき `lcb_gate_penalty`（既定 0.2）で割り引く。<br>**DD 項は無い**（2026-08-23 削除。理由は §6.5.1）。実装: `optimization_runner.calculate_custom_score`。設計経緯: `doc/completed/objective_quality_first_plan.md`。 |
| **Expectancy** | 1トレードあたりの期待値 = (WR × AvgWin) + ((1-WR) × AvgLoss)。補助指標。 |
| **Expectancy LCB** | 期待値の下側信頼限界 = expectancy − 2×SE（SE = 標本標準偏差/√n、n<2 は expectancy にフォールバック）。**補助指標＝シグナルの質**（少数トレード×高分散の「まぐれ」を炙り出す）。2026-07-05〜07-06 の間はスコア主軸だったが、資産成長との相関が弱く主軸を CAGR 合成へ移し、2026-08-22 にさらに avg_gain（質）主軸へ移した。現在も `lcb_gate` として減点に使われている。 |
| **Avg Gain** | 全トレードの PnL% の**算術**平均。補助指標。**分散に無関心**で、「95%が負けで上位5%が全部稼ぐ」構成と「安定して勝つ」構成を同じ数字にする。 |
| **Geo Mean Gain** | 1トレードあたりの**幾何**平均リターン% = `exp(mean(ln(1+r)))−1`。**2026-08-24 より最適化スコアの主軸**。積なので大きな負けを強く罰し、**勝率を内在的に要求する**（勝率を明示的に掛ける必要がない＝二重計上の回避）。CAGR（`strat_multiplier`）も積＝幾何平均であり、旧 CAGR 主軸が結果として1取引%と勝率を両立させていたのはこの性質による。本指標は CAGR から `avg_slots` 正規化と年率化を外し、**取引数の寄与を含まない純粋な質**にしたもの。 |
| **Avg SPY Gain** | 各トレードと**同一の保有期間**で SPY を購入した場合の平均リターン%。ベンチマーク。 |
| **Alpha** | Avg Gain - Avg SPY Gain。正の値 = 戦略が市場平均を上回っている（超過リターン）。 |
| **Max Drawdown** | **日次 mark-to-market エクイティカーブ**の最高到達点からの最大下落幅（Little's Law の平均同時保有数で正規化。2026-07-05 改修）。保有中の含み損の谷・シグナル集中期の同時被弾を反映する。部分利確後は確定分（1/3）を固定し残り 2/3 のみ時価評価。 |
| **Max Drawdown (legacy)** | 旧方式（exit日ソートの確定損益累積）の DD。比較用に `max_drawdown_legacy_pct` として併記。 |
| **Profit Factor** | 総利益 / 総損失 |
| **Win Rate** | 勝ちトレード数 / 全トレード数（補助指標） |
| **Avg Holding Days** | 平均保有日数（補助指標） |

### 6.5.1 設計判断（意図された挙動）
- **期間ごとの最低活動量ゲート**（トレード5件未満/活動量不足のペナルティ）は意図された設計。「ベアは事後にしか分からない」ため、スクリーナ自体はレジーム非依存に安定して機能すべき（ベア期間でも勝率が安定するスクリーンを選抜する）。レジーム認識とポジションサイズ調整は個別銘柄シナリオテスト（MTS 参照）の責務。
- **定額手数料は最適化バックテストでは導入しない**。全パラメータ候補の期待値を一律に下げるだけで argmax（順位）を変えないため。流動性連動コストのみ min_avg_dollar_volume_21 のハード制約で対処。
- **税は型1だけ 0.0 に戻す（経路分離）**（2026-08-22〜）。型1（最適化バックテスト・`optimization_runner.py` /
  `backtest_runner.py`）は `[general] consider_tax_optimization`（既定 0.0=税なし）を読み、
  型2・型3（実運用シミュレーション）は従来どおり `consider_tax`（既定 0.2）を読む。
  `common_constraints.load_tax_rate(config, for_optimization=...)` が経路を分離しつつ、
  単位検証（`1.0` 超は %表記との取り違えとして `InvalidTaxRateError`）は両経路に共通で効かせる。
  2026-08-20〜22 に税込みで最適化した結果、型1のスコア（当時は期間CAGR主軸）は改善しても
  取引数の減少が上回り、型3側の Calmar が10戦略中8戦略で悪化した実測があったため、
  型1は「純粋なスクリーン条件の質」を測る役割へ戻した（下記の目的関数再設計と対）。
  過去に `consider_tax=20.0`（%のつもりで書かれた値）が全経路共有だった時代に静かに歪めていた
  事故は `tax_rate_wiring_plan.md` で修正済み。
- **検出件数はゲート、1トレードあたりの幾何平均リターンを主軸にした目的関数**（2026-08-24 確定）。
  旧版（2026-07-06〜）の `period_CAGR / dd_penalty × detect_adequacy(hits/day) × lcb_gate` は
  検出件数を **掛け算** で実用帯へ寄せていたため、「量を増やせばスコアが伸びる」経路が残っていた。

  ```
  検出件数(avg件/日) が quality_gate=(lo, hi) の外 → 失格（大きな負値）
  幾何平均が 0 以下                                → その値をそのまま返す（掛け算しない）
  それ以外                                          → score = geo_mean_gain
                                                       （expectancy_lcb <= 0 なら lcb_gate_penalty 倍）
  ```

  帯の中にいる限り取引数はスコアに一切影響しない。**枠を埋めるのは個々の戦略ではなく
  組み合わせ運用の役割**、というのが本設計の立場（§6.1）。
    - **なぜ幾何平均か**: 算術平均（`avg_gain`）は分散に無関心で、「95%が負けで上位5%が
      全部稼ぐ」構成と「安定して勝つ」構成を同じ点数にする。実際、算術平均を主軸にした
      世代では型3 で**上位5%トレードの利益寄与が105%**（利益の全部以上を上位5%が稼ぐ）まで
      歪んだ。幾何平均は積なので大きな負けを強く罰し、**勝率を内在的に要求する**
      （勝率を明示的に掛ける必要がない＝二重計上の回避。`avg_gain` は既に勝率を内包している）。
      実測（型3の39観測）: 勝率との整合 算術 +0.333 → 幾何 +0.498 /
      分散との結びつき 算術 +0.907 → 幾何 +0.719。
    - **CAGR 主軸が結果として1取引%と勝率を両立させていたのは偶然ではない**。
      `strat_multiplier` も積＝幾何平均であり、同じ性質による。本指標は CAGR から
      `avg_slots` 正規化と年率化を外し、**取引数の寄与を含まない純粋な質**にしたもの。
    - **★ 赤字のガード（削除しないこと）**: 幾何平均が0以下なら掛け算の経路に入れず素の値を返す。
      **掛け算のペナルティは、スコアが負のとき「罰」ではなく「ご褒美」になる**
      （負の値に係数<1 を掛けるとゼロに近づく＝改善する）。スコアは複数の学習期間の平均なので、
      これを放置すると「片方で大勝ち・片方で赤字かつ罰あり」の構成が最適解として選ばれる。
      実際に発生し、3世代・約30時間分の最適化結果を汚染した。
    - **DD 項は持たない**（2026-08-23 削除）。型1 DD を型3 相当へ換算する式
      （`|DD| × √avg_slots / 9.1`）自体は正しく、型3 DD との順位相関を +0.533 → +0.896 に
      改善する。しかし**型1 DD は型3 の CAGR と +0.770 の正相関**を持つ（DD が大きい戦略ほど
      CAGR が高い）ため、これで減点すると良い戦略ほど罰せられる。実測でも DD 項ありは
      型3 CAGR との相関が +0.722 → +0.375 と半減した。**DD の管理は型3 の責務**とする。
      なお `avg_slots` は将来の分析のため trial 属性に記録し続ける。
    - **ゲート下限の解決順序**:
      `strat.quality_gate_min_hits_per_day ?? min(グローバル既定 0.3, strat.min_avg_hits_per_day)`。
      一律 0.3 を課すと B1/B2/B3/B5/B6 等の上位戦略がほぼ全滅する（26観測中15件が失格）ため
      既存の `min_avg_hits_per_day`（実用帯の下限=既定1.0）は転用せず別キーにした。
      一方、**戦略が自分より低い下限を宣言している場合はそちらを尊重する**
      （`resolve_prune_floor` と同じ「引き締めにならない」流儀）。E1(0.05)/E2(0.02) は
      VCP ブレイクアウトで検出が稀なのが仕様であり、一律 0.3 を課したときは条件を緩めるしかなくなり
      1取引% が +1.44 → -0.29 / +3.39 → -0.46 とマイナスに転落した。
    - **`lcb_gate`（`expectancy_lcb <= 0` で `lcb_gate_penalty`＝既定 0.2 倍割引）は残す**。
      単価エッジの下限が無いスクリーン（少数の巨大勝ちに依存＝生存者バイアス疑い）を
      ソフトに減点する意味は、幾何平均を主軸にした後も変わらない。
  実装: `optimization_runner.calculate_custom_score`。
  設計経緯と捨てた案（DD 項・√勝率の掛け算）: `doc/completed/objective_quality_first_plan.md`。

### 6.5.2 ホールドアウト検証（`[[optimization_validation.sets]]`）
最適化スコアの計算に**一切使わない**未学習期間で、study 完了後に best-5 trial を自動評価する（勝者の呪いによる劣化率の可視化）。セット内の全窓のトレードを**プールしてから**指標計算する（短い窓単体に活動量ゲートを当てると静かなスクリーンが不当に沈むため）。結果はコンソール表 + `backend/backtest/results/holdout_{strategy}.json`。

| セット | 期間 | 検証する性質 |
|---|---|---|
| stress_bear | 2018-10〜12 + 2020-02〜05 | 引き締め型ベアへの汎化 + 事故型クラッシュのテール耐性（勝率の安定性） |
| calm_recent | 2023 + 2026-01〜06 | レンジ相場での無駄撃ち + 学習期間より後の疑似フォワード |

**注意**: 学習期間（2022 / 2024-06〜2025-12）と重なる期間は検証に使用禁止（2025-02〜04 は Bull 2024-25 に内包）。2021 は温存（将来のブル側検証候補）。

### 6.6 パフォーマンス方針 (SQLite 完全遮断 & Parquet マスターロード)
バックテストエンジンは、長期間（5年分以上）かつ大量銘柄（数千件）の OLAP データスキャンを行うため、本番 SQLite キャッシュ DB へのアクセスを **「完全遮断（DB接続すらしない）」** します。

- **SQLite コネクションプール保護**: バックテストの実行中、SQLite への物理 I/O および接続数は一切増加しません。
- **Parquet 一撃ロード**: 全歴史が蓄積されている [data/parquet_master/](file:///d:/My%20Documents/Programing/stocktool/data/parquet_master/) の Parquet ファイル群（またはバックテスト用一時 Parquet）から `pd.read_parquet()` で一撃ロード（約1〜2秒）し、メモリ上で瞬時にシミュレーションを行います。これにより、並列バックテスト時でも本番 SQLite への Busy ロックは 100% 発生しません。
- **独立性**: バックテスト専用キャッシュおよびParquetマスターは、稼働中の本番 API サーバー（SQLite）とは完全に分離されており、双方の競合フリーな並行稼働が物理的に保証されます。

### 6.7 プリロードするデータの範囲 (Preloaded Data)

> **2026-08-18 改訂**（Phase 3d）。旧版は「戦略評価に必須なカラムのみを選択的に抽出する」
> という**カラム・ホワイトリスト方式**を記述していたが、これは既に実装から失われている。
> 加えて `relative_ranks` を long 形式（`indicator_name` / `percent_rank`）と記述していたが、
> Parquet も SQLite も**一貫して wide 形式**である。旧記述は現行実装と一致しない。

`backtest_runner.py::preload_data()` は Parquet マスターの各ファイルを
**カラムを絞らずそのまま読み込み**、**日付のプッシュダウンフィルタのみ**で
メモリ量を制御します。

| テーブル | 列数 | 読み込み範囲 |
| :--- | ---: | :--- |
| `symbols` | 11 | 全件（日付を持たないため全行） |
| `daily_prices` | 9 | `start_date - 45日` 〜 `end_date`（21日ローリング計算のバッファ） |
| `indicators` | 63 | `start_date` 〜 `end_date` |
| `relative_ranks` | 26 | `start_date` 〜 `end_date` |
| `theme_constituents` | 4 | 全件 |
| `market_signals` / `fundamental_data` | — | **読み込まない**（バックテストエンジンは未使用） |

**カラム選択をやめた理由**: ホワイトリストは「新しいフィルタを追加したのに列が来ていない」
という取りこぼし（計画書 §1.2 の F4「データ供給の差」）を構造的に生む。実際、
流動性フィルタが使う `avg_dollar_volume_21` は旧ホワイトリストに一度も載らないまま
運用されていた（実装側が先に列選択をやめていたため実害には至らず、
仕様書だけが取り残された）。現在は
`indicators/screener_registry.py` の `resolve_required_columns()` が
**戦略から必要カラムを宣言的に導出**し、欠けていれば `MissingFilterColumnError` で
即座に停止する（fail-loud）ため、事前の列間引きは不要かつ有害である。

**メモリ**: 列を絞らない代わりに、**ランクの long 形式への `melt` を廃止**した（Phase 3d）。
melt は行数を16倍に膨らませており、これがメモリ消費の主因だった。

| 学習期間 | melt あり | wide（現行） |
| :--- | ---: | ---: |
| Bear 2022 | 1,376 MB | **210 MB** |
| Bull 2024-25 | 2,315 MB | **354 MB** |

**`market_cap` の特記事項**: 時価総額は過去の履歴が存在しないケースが多いため、
切り取った期間内での穴埋めではなく「全期間の中から最新の `market_cap` を取得し、
過去の日付にグローバル・バックフィルする」特殊処理を施しています。
なお `market_cap` は `indicators` ではなく **`daily_prices` 側の列**です。

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
    *   **カスタムスコア（2026-08-24 確定。主指標は `geo_mean_gain` = 1トレードあたりの幾何平均リターン%）**: 検出件数（avg件/日）が `quality_gate=(lo, hi)` の外なら失格、幾何平均が0以下ならその値をそのまま返し（掛け算しない）、それ以外は `score = geo_mean_gain`（`expectancy_lcb <= 0` なら `lcb_gate_penalty` 倍）。**DD 項は持たない**。旧「期間CAGR / dd_penalty × detect_adequacy(hits/day) × lcb_gate」（2026-07-07版）から差し替え。詳細 §6.5.1。
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
min_dist_21ema_pct   = { type = "float", min = -4.0, max = -1.0, step = 0.5 }
max_dist_21ema_pct   = { type = "float", min = 0.5,  max = 4.0,  step = 0.5 }
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
    *   環境変数 `STOCKTOOL_ENV="sandbox"` を指定し、接続先をサンドボックス環境（`data/sandbox/` 下の各DB）に完全隔離します。
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

## 8. 週次定期バッチ (Weekly Maintenance)

週末の非取引時間帯に、データベースの物理・論理整合性のクリーンアップを一括して行う定期メンテナンスバッチです。

### 8.1 動作仕様
- **スクリプト**: `backend/scripts/weekly_maintenance.py`
- **トリガーバッチ**: `run/run_weekly_maintenance.bat`
- **タスク登録バッチ**: `run/register_weekly_maintenance.bat`（毎週日曜日 AM 02:00 に実行する Windows タスクスケジューラ登録用バッチ）
- **排他制御**: `update_pipeline.lock` によるファイルロックを共有するため、日次更新処理と同時に起動した場合は競合検知で安全に終了します。
- **コマンドライン引数**:
  - `--dry-run`: 物理的な改変（REINDEX/VACUUM）をスキップし、不整合データのスキャンおよびレポートの生成のみを行います。
  - `--fix`: インデックス再構築・領域圧縮を適用し、不整合（無効な構成銘柄紐づけなど）の自動修復・指標欠損の再計算バックフィルを実行します。

### 8.2 物理メンテナンス機能
1.  **PRAGMA integrity_check**: データベースファイルが物理的に破損していないかを高速にチェック。NGの場合は例外を送出してロールバックします。
2.  **REINDEX**: 肥大化や追加・削除で断片化したSQLiteインデックスを再構築し、画面描画時のクエリ速度を最適に維持します。
3.  **VACUUM**: データベースの未使用ページを解放し、ファイルサイズを最小化します（日次バッチ側から `VACUUM` を週次に集約することで、日次バッチ処理時間の短縮を図っています）。

### 8.3 論理監査と自己修復 (Self-Healing)
1.  **上場廃止・データ供給停止の自動検知**:
    - 主体指標である `SPY` の最新価格日付より5営業日以上データが古い `active=1` 銘柄を「上場廃止候補」として自動検知します。
    - 検出結果は `data/maintenance_reports/delisting_recommendations.csv` に出力し、`backend/scripts/retire_stale_symbols.py --from-report` で `universe.db` に反映します（T1 のソースはスプレッドシートから `universe.db` へ移行済み）。
    - **SEC が「現ティッカーで上場中」と言う銘柄は自動退役の対象から外します**（`split_by_sec_verdict()`）。供給停止（Yahoo 側の問題）を上場廃止と取り違えて健在な銘柄を消さないためで、レポートの `7-c2. HELD FROM AUTO-RETIREMENT` に残します。`RSHO` は SEC 上健在なのに価格供給だけが止まり、毎週退役候補に挙がり続けていました。
2.  **空テーマの検出**: 構成銘柄数 `0` 件となった active なテーマ/指標を検知してレポートします。
3.  **不正なテーマ構成銘柄の自動削除**:
    - `active = 0` (無効化済み) や存在しない symbol_id をターゲットに持つ `ThemeConstituent` の不正リンクをスキャンし、`--fix` モード実行時に自動でデリートパージします。
4.  **指標（Indicator）データの不整合検知と自動再計算**:
    - 直近 2年間 (730日) について、価格データ (`DailyPrice`) が存在するにもかかわらず対応する指標データ (`Indicator`) が無い中間欠損日付を走査します。
    - 欠損を検知した場合、該当銘柄の過去全取引期間を指標計算ロジック（`calculate_indicators`）に通し、正確に算出した指標レコードを自動的に補完インサートします。
5.  **株式分割・併合の疑い検出**:
    - 過去730日の全銘柄の終値前日比を走査し、比率が `0.61` 以下 (40%以上の急落) または `1.79` 以上 (80%以上の急騰) の異常値を持つ箇所を検知します。
    - **段差の検出だけでは実用にならない**ため（実測1,081件中の大半がバイオのイベントや COVID 暴落）、`indicators/price_anomaly.py` の共通分類器で `virtual` / `market_wide` / `low_liquidity` / `split_suspect` / `real_move` / `undecided` に仕分け、要対応のみをレポートします。
    - 全期間（Parquet 直読み）を対象とする場合は `backend/scripts/scan_price_anomalies.py` を使います（SQLite には一切接続しないためロック競合が起きません）。
6.  **SEC コーポレートアクション同期**（§8.4）:
    - `universe.db` の SEC キーと SEC マスタを突合し、改称・上場廃止を検知して反映します。
    - ネットワーク依存のため、失敗しても週次メンテ全体は落とさずレポートに理由を残します。`--skip-sec` でスキップ可能。

### 8.4 SEC コーポレートアクション同期

**背景**: 2026年6〜7月に15件（改称6・上場廃止9）のコーポレートアクションを2ヶ月見逃しました。
Yahoo は「データが少ない」としか返さず**改称と上場廃止を区別できない**ため、
「上流のデータ不具合」と誤診して復旧に丸一日を要しました。

- **スクリプト**: `backend/scripts/sync_sec_corporate_actions.py`（純粋ロジックは `backend/data_collection/sec_corporate_actions.py`、HTTP は `backend/data_collection/sec_client.py`）
- **対象 DB**: `universe.db`（ユーザー資産。実行前に自動でバックアップを取得）
- **反映のラグ**: `universe.db` への反映は翌日のデイリー（T1 同期）で `stocktool.db` に伝わります。

#### 処理の流れ

```
[0] SEC キーが未解決の銘柄を解決して保存   ← 新規追加銘柄が追跡対象外に落ちるのを防ぐ
[1] SEC マスタ3ファイルを取得（3リクエスト）
[2] universe.db の (sec_key, ticker) と突合 → 改称候補 / 廃止候補
[3] 候補だけ submissions API で確定（通常 0〜数件）
[4] 退役は自動適用。改称はガード3条件を満たせば自動適用
```

個別に submissions を引くと3,000リクエストになりますが、**マスタの差分なら3リクエストで
全銘柄をスクリーニングできます**。

#### 判定の要点（すべて実測の誤検知から導いた条件）

| 事象 | 素朴な判定 | 正しい判定 |
| :--- | :--- | :--- |
| Form 25 / 25-NSE の提出がある | 上場廃止 | **取引所の移管でも提出される。** `AEP` は 2023-08-14 に 25-NSE を出した後も 10-Q を提出中（NYSE→Nasdaq 移管）。`UUP` は2008年の Form 25 を持つ現役 ETF。提出から400日以内で、かつ**後続の定期報告が無い**ことを要求する |
| 一括マスタにキーが無い | 上場廃止 | **`company_tickers.json` は10,398件で全登録企業を網羅していない。** 定期報告が継続していれば `master_gap`（対応不要）とする |
| 同じ CIK が別ティッカーになった | 改称 | **1つの CIK に複数証券がぶら下がる。** `VWDRY`(ADR)/`VWSYF`(原株) は両方が取引中＝`coexisting`（対応不要）。`BNY`/`BNY-PK` のように普通株と優先株が並ぶこともある |
| submissions の `tickers` が旧のまま | 改称ではない | **submissions は遅れる。** `GAMB`→`GRSD` は社名が GRANDSTAND Ltd に変わり一括マスタも更新済みなのに `tickers=['GAMB']` のままだった。弱い根拠として残しガードに委ねる |

#### 自動適用のガード（改称は3条件すべて必須）

```
① SEC キー（cik / class_id）が一致すること
② 新ティッカーが実際に履歴を持つこと（20行以上）  ← 空振りへの付け替え事故を防ぐ
③ 旧ティッカーの取引が止まっていること
```

③は**新旧の最終取引日の差**（5日以上）で測ります。「直近1ヶ月の行数」で見ると、
改称直後に旧ティッカーへ残る数日分の残骸を「まだ生きている」と誤判定します
（`GAMB` は最終 2026-07-29 で6行あり、行数基準では改称を取りこぼしました）。
「今日から何日前か」で測らないのは、連休・祝日・取得タイミングでぶれるためです。

**退役は自動適用します**（根拠が硬く、`active=1` に戻せば価格履歴ごと復元できる可逆な操作）。
改称はガードを満たしたときのみ自動適用し、欠ければ `sec_pending_actions.csv` に出して人間に回します。

#### SEC の規約

- **連絡先入りの User-Agent が必須**、**10 req/sec** が上限。
- 連絡先は `STOCKTOOL_SEC_CONTACT` → `config.local.toml` の `[sec] contact` の順で解決し、
  **見つからなければ起動時エラー**にします（UA 無しで黙って叩くと弾かれていることに気づけないため）。
  `config.local.toml` は git 管理外です。
