# バックテスト設定変数仕様書 (Backtest Config Specification)

本ドキュメントは、`backend/backtest/backtest_config.toml` で指定可能な各変数の定義、計算論理、およびデータベース（DB）上の参照元カラムを定義するリファレンスです。

---

## 1. スクリーナー・パラメータ (`[[strategy]]`)

銘柄の抽出条件を定義するパラメータです。

### 1.0 汎用抽出・Top-N設定 (メタパラメータ)
| 変数名 | 計算論理・説明 |
| :--- | :--- |
| `max_hits_per_day` | **最大抽出件数 (Top-N)**: 1日あたりの抽出件数がこれを超えた場合、後述の `sort_column` の順序で上位N件のみを残します。 |
| `sort_column` | **ソート基準カラム**: Top-Nを抽出する際に基準とするカラム名（例: `rs21_rank`, `gain_1d_pct`等）。 |
| `sort_ascending` | **昇順ソート**: `true` の場合は小さい順、`false` (デフォルト) の場合は大きい順でTop-Nを取得します。 |

> **汎用プレフィックス（自動バインディング）について**:
> 本システムでは、パラメータ名（キー名）の先頭に特定のプレフィックスを付けることで、Python側のコード変更なしに汎用的なフィルタリングを適用できます。
> * `min_〇〇` : カラム `〇〇` に対して `>=` 評価を行います。
> * `max_〇〇` : カラム `〇〇` に対して `<=` 評価を行います。
> * `is_〇〇`, `has_〇〇`, `bool_〇〇` または設定値が boolean 型 : 完全一致 (`==`) 評価を行います。

### 1.1 価格・トレンド関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_change_1d_pct` | `indicators.change_1d_pct` | **1日騰落率（下限）**: 前日終値に対する当日終値の上昇率(%)。 |
| `max_change_1d_pct` | 同上 | **1日騰落率（上限）**。急騰しすぎた銘柄を除外する場合などに使用。 |
| `min_change_intraday_pct` | `daily_prices.open/close` | **当日中騰落率（下限）**: 当日始値に対する終値の上昇率(%)。旧 `1d_gain_pct`。 |
| `min_dist_21ema_pct` | `indicators.ema_21`<br>`daily_prices.close` | **EMA21乖離率（下限）**: `(close - ema_21) / ema_21 * 100`。 |
| `max_dist_21ema_pct` | 同上 | **EMA21乖離率（上限）**。 |
| `close_gt_sma50` | `indicators.sma_50`<br>`daily_prices.close` | **SMA50上抜け**: `close > sma_50` の場合に真。 |
| `trend_template_ok` | `indicators.trend_template_ok` | **トレンドテンプレート適合**: ミネルヴィニのトレンドテンプレート（SMA200の上昇、SMA50/150/200の位置等）を全合格しているか（1 or 0）。 |

### 1.2 ボラティリティ・出来高関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_vol_surge_21` | `indicators.vol_surge_21` | **出来高急増**: 当日の出来高が過去21日間の平均出来高の何倍か。 |
| `min_adr_pct_21` | `indicators.adr_pct_21` | **平均日次レンジ％**: 過去21日間の `(High-Low)/Close` の平均値。銘柄固有のボラティリティを示す。 |
| `max_adr_pct_21` | 同上 | **平均日次レンジ％（上限）**。 |
| `min_dist_sma50_atr` | `indicators.dist_sma50_atr` | **SMA50距離(ATR調整済み)**: `(Close - SMA50) / ATR14`。SMA50から平均的な値動きの何倍離れているか。 |
| `max_dist_sma50_atr` | 同上 | **SMA50距離（上限）**。離れすぎ（過熱）を防ぐために使用。 |

### 1.3 時価総額・相対強度 (RS) 関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_market_cap` | `indicators.market_cap` | **時価総額（下限）**: 米ドル単位（例: 1e9 = 1B）。※`category='テーマ'` の銘柄は判定から除外される。 |
| `min_rs_condition_21` | `indicators.rs_condition_21` | **RS Condition**: `RS値 / SMA21(RS値)`。RS自体が自身の移動平均を上回っているか（相対的な加速状態）。 |
| `min_rs_ratio_21_rank`| `relative_ranks.percent_rank` | **RS 21日ランク**: カテゴリ内でのRS強さのパーセンタイル順位 (0.0~1.0)。`indicator_name='rs_ratio_21'` を参照。 |
| `rs_rank_21_gt_63` | `relative_ranks.percent_rank` | **RS短期加速**: 21日ランクが63日ランクを上回っているか（短期的な相対強度が向上しているか）。 |
| `theme_rs21_gt_63` | `indicators.rs_ratio_21/63`<br>`theme_constituents` | **テーマ主導**: 所属するテーマ自体のRS21 > RS63であるか。個別銘柄の場合は、その銘柄を構成員に持つテーマのいずれかが合格していれば真。 |

### 1.4 RRG (Relative Rotation Graph) 関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `rrg_leading_in` | `indicators.rs_ratio_21`<br>`indicators.rs_momentum_21` | **Leading入り**: 前日に Leading 象限 (Ratio>0, Mom>0) 以外にいた銘柄が、当日に Leading 象限に入った瞬間。モメンタムは Ratio の 14日 ROC をベースとしており、トレンド転換を先行して示唆する。 |
| `rrg_lagging_in` | 同上 | **Lagging入り**: 前日に Lagging 象限 (Ratio<0, Mom<0) 以外から Lagging に入った瞬間。 |

### 1.5 Accumulation・先行指標・ベース形成関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_up_down_vol_ratio_50` | `indicators.up_down_vol_ratio_50` | **Up/Down Volume Ratio（下限）**: 過去50日間の上昇日出来高合計 ÷ 下落日出来高合計。1.5以上で機関投資家のAccumulation（買い集め）が優勢であることを示す。 |
| `rs_blue_dot` | `indicators.rs_blue_dot` | **RS Blue Dot**: RS（相対強度）が252日新高値を更新しているが、株価自体は252日新高値に到達していない状態。株価に先行してRSが強さを示すリーディングシグナル。 |
| `rs_red_dot` | `indicators.rs_red_dot` | **RS Red Dot**: RS（相対強度）が252日新安値を更新しているが、株価自体は252日新安値に耐えている状態。株価に先行してRSが弱さを示す弱気シグナル。 |
| `max_vcr` | `indicators.vcr` | **Volatility Contraction Ratio（上限）**: `ATR(10) / ATR(50)`。0.5以下で極度のボラティリティ収縮（VCPの第3〜4次収束）を示す。Trend Template適合 + 高RSランクと組み合わせることで、ブレイクアウト直前のベース形成銘柄を特定する。 |

---

## 2. 出口ルール設定 (`[exit_rules]`)

全戦略で共通して適用される、エグジット（手仕舞い）のロジック設定です。

| 変数名 | 関連 DB カラム | 説明 |
| :--- | :--- | :--- |
| `stop_loss_pct` | `daily_prices.close` | **固定損切り率**: エントリー価格からの下落率(%)。 |
| `partial_take_profit_pct` | `daily_prices.close` | **部分利確開始ライン**: この利益率に達すると 1/3 等の売却を行う。 |
| `partial_take_profit_sma50_atr`| `indicators.dist_sma50_atr` | **オーバーエクステンション利確**: 利益率に関わらず、SMA50からATRのN倍以上乖離した場合に部分利確。 |
| `partial_ratio` | - | **利確割合**: 部分利確時に売却する保有量の比率（例: 0.333 = 1/3）。 |
| `full_exit_ema21_consecutive_days`| `daily_prices.close`<br>`indicators.ema_21` | **トレンド崩れ決済**: 終値が EMA21 を連続して N 営業日下回った場合に全決済。 |
| `full_exit_sma50_atr` | `indicators.dist_sma50_atr` | **極端な過熱決済**: SMA50からATRのN倍（例: 11倍）以上離れた場合に全決済（吹き値売り）。 |
| `time_stop_days` | `daily_prices.high/low`<br>`indicators.atr_14` | **タイムストップ日数**: N日間停滞（レンジ幅が 1 ATR未満）した場合に強制手仕舞い。 |

---

## 3. 一般設定 / 最適化設定

| 変数名 | 説明 |
| :--- | :--- |
| `general.failsafe_max_days` | **強制決済期限**: エグジット条件を満たさないまま N 営業日経過した場合に強制決済。 |
| `optimization_periods` | Optuna で評価対象とする特定の相場期間（ブル/ベア等）の定義。 |
| `optimization_pruning` | 一定のヒット率やトレード頻度を満たさないパラメータを早期に切り捨てるための基準値。 |
