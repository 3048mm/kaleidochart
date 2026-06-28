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

> **汎用プレフィックス（自動バインディング）および特殊汎用フィルタについて**:
> 本システムでは、パラメータ名（キー名）の先頭に特定のプレフィックスを付けることで、Python側のコード変更なしに汎用的なフィルタリングを適用できます。
> * `min_〇〇` : 個別銘柄のカラム `〇〇` に対して `>=` 評価を行います。
> * `max_〇〇` : 個別銘柄のカラム `〇〇` に対して `<=` 評価を行います。
> * `min_theme_〇〇` : 当該テーマ自体のカラム `〇〇` に対して `>=` 評価を行い、条件に合致するテーマの構成銘柄（個別銘柄）およびテーマ自体を抽出します（生値・ランク両対応）。
> * `max_theme_〇〇` : 当該テーマ自体のカラム `〇〇` に対して `<=` 評価を行い、同様に構成銘柄およびテーマ自体を抽出します。
> * `is_〇〇`, `has_〇〇`, `bool_〇〇` または設定値が boolean 型 : 完全一致 (`==`) 評価を行います。
> * `close_gt_〇〇` : `close` (終値) が任意の移動平均線 `〇〇` を上回っているか (`close > 〇〇`) の判定を行います。
>   * 例: `close_gt_ema21 = true` (終値 > EMA21)
>   * 例: `close_gt_ema50 = true` (終値 > EMA50)
>   * 例: `close_gt_ema63 = true` (終値 > EMA63)
>   * 例: `close_gt_sma50 = true` (終値 > SMA50)
>   * 例: `close_gt_sma200 = true` (終値 > SMA200)
>   * (※ `ema21` のようにアンダースコアがない記述も、内部で自動的に `ema_21` に正規化されて処理されます)

### 1.1 価格・トレンド関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_change_1d_pct` | `indicators.change_1d_pct` | **1日騰落率（下限）**: 前日終値に対する当日終値の上昇率(%)。 |
| `max_change_1d_pct` | 同上 | **1日騰落率（上限）**。急騰しすぎた銘柄を除外する場合などに使用。 |
| `min_change_intraday_pct` | `daily_prices.open/close` | **当日中騰落率（下限）**: 当日始値に対する終値の上昇率(%)。旧 `1d_gain_pct`。 |
| `min_dist_21ema_pct` | `indicators.ema_21`<br>`daily_prices.close` | **EMA21乖離率（下限）**: `(close - ema_21) / ema_21 * 100`。 |
| `max_dist_21ema_pct` | 同上 | **EMA21乖離率（上限）**。 |
| `close_gt_〇〇` | `indicators.〇〇`<br>`daily_prices.close` | **動的・移動平均線の上抜け**: `close > 〇〇` の場合に真。利用可能な移動平均の全主要バリエーションに対して動的に動作（例: `close_gt_ema21`, `close_gt_ema50`, `close_gt_ema63`, `close_gt_sma50`, `close_gt_sma200` 等）。 |
| `is_trend_template` | `indicators.is_trend_template` | **トレンドテンプレート適合**: ミネルヴィニのトレンドテンプレート（SMA200の上昇、SMA50/150/200の位置等）を全合格しているか（1 or 0）。 |

### 1.2 ボラティリティ・出来高関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_vol_surge_21` | `indicators.vol_surge_21` | **出来高急増**: 当日の出来高が過去21日間の平均出来高の何倍か。 |
| `min_adr_pct_21` | `indicators.adr_pct_21` | **平均日次レンジ％**: 過去21日間の `(High-Low)/Close` の平均値。銘柄固有のボラティリティを示す。 |
| `max_adr_pct_21` | 同上 | **平均日次レンジ％（上限）**。 |
| `min_sma50_atr_mult` | `indicators.sma50_atr_mult` | **SMA50距離(ATR調整済み)**: `(Close - SMA50) / ATR14`。SMA50から平均的な値動きの何倍離れているか。 |
| `max_sma50_atr_mult` | 同上 | **SMA50距離（上限）**。離れすぎ（過熱）を防ぐために使用。 |

### 1.3 時価総額・相対強度 (RS) 関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_market_cap` | `indicators.market_cap` | **時価総額（下限）**: 米ドル単位（例: 1e9 = 1B）。※`category='テーマ'` の銘柄は判定から除外される。 |
| `min_rs_trend_s21` | `indicators.rs_trend_s21` | **RS Condition**: `rs_value_e5 / SMA21(rs_value)`。RS自体が自身の移動平均を上回っているか（相対的な加速状態）。 |
| `is_rs_trend_s21_gt_s63` | `indicators.rs_trend_s21`/`s63` | **個別トレンド加速**: 個別銘柄の21日トレンド生値が63日トレンド生値を上回っているか。 |
| `min_rs_ratio_rank_e21`| `relative_ranks.percent_rank` | **RS 21日ランク**: カテゴリ内でのRS強さのパーセンタイル順位 (0.0~1.0)。`indicator_name='rs_ratio_e21'` を参照。 |
| `is_rs_ratio_rank_e21_gt_e63` | `relative_ranks.percent_rank` | **RS短期加速**: 21日ランクが63日ランクを上回っているか（短期的な相対強度が向上しているか）。 |
| `is_theme_rs_ratio_e21_gt_e63` | `indicators.rs_ratio_e21/e63`<br>`theme_constituents` | **テーマ主導 (21 vs 63)**: 所属するテーマ自体のRS21 > RS63であるか。個別銘柄の場合は、その銘柄を構成員に持つテーマのいずれかが合格していれば真。 |
| `is_theme_rs_ratio_e14_gt_e21` | `indicators.rs_ratio_e14/e21`<br>`theme_constituents` | **テーマ主導 (14 vs 21)**: 所属するテーマ自体のRS14 > RS21であるか。 |
| `min_theme_rs_ratio_rank_e14` / `min_theme_rs_ratio_rank_e21` | `relative_ranks.percent_rank`<br>`theme_constituents` | **テーマRSランク下限**: 所属するテーマのRS比率ランク (0.0~1.0) が指定値以上か。 |
| `min_theme_rs_trend_s21` / `min_theme_rs_trend_rank_s21` | `indicators.rs_trend_s21`<br>`relative_ranks.percent_rank`<br>`theme_constituents` | **テーマトレンド下限**: 所属するテーマのトレンド生値 / トレンドランクが指定値以上か。 |
| `is_theme_rs_trend_rank_s14_gt_s21` | `relative_ranks.percent_rank`<br>`theme_constituents` | **テーマトレンド加速**: 所属するテーマの14日トレンドランクが21日トレンドランクを上回っているか。 |

### 1.4 RRG (Relative Rotation Graph) 関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `rrg_leading_in` | `indicators.rs_ratio_e21`<br>`indicators.rs_momentum_e21` | **Leading入り**: 強度（中心からの距離）が `rrg_intensity_threshold` 以上、かつモメンタムが加速している状態で Leading 象限に入った瞬間。 |
| `rrg_improving_in`| 同上 | **Improving入り**: 強度が閾値以上、かつモメンタムが加速している状態で、Lagging から Improving 象限に入った瞬間。 |
| `rrg_lagging_in` | 同上 | **Lagging入り**: Lagging 象限以外から Lagging に入った瞬間。 |
| `rrg_intensity_threshold` | - | **RRG強度閾値**: 中心 (0,0) からの最小距離 `sqrt(ratio^2 + mom^2)`。デフォルト 0.0。ノイズ除去には 0.5 前後を推奨。 |

### 1.5 Accumulation・先行指標・ベース形成関連
| 変数名 | DB 参照元 (Table.Column) | 計算論理・説明 |
| :--- | :--- | :--- |
| `min_up_down_vol_ratio_50` | `indicators.up_down_vol_ratio_50` | **Up/Down Volume Ratio（下限）**: 過去50日間の上昇日出来高合計 ÷ 下落日出来高合計。1.5以上で機関投資家のAccumulation（買い集め）が優勢であることを示す。 |
| `is_rs_blue_dot` | `indicators.is_rs_blue_dot` | **RS Blue Dot**: RS（相対強度）が252日新高値を更新しているが、株価自体は252日新高値に到達していない状態。株価に先行してRSが強さを示すリーディングシグナル。 |
| `is_rs_red_dot` | `indicators.is_rs_red_dot` | **RS Red Dot**: RS（相対強度）が252日新安値を更新しているが、株価自体は252日新安値に耐えている状態。株価に先行してRSが弱さを示す弱気シグナル。 |
| `max_vcr` | `indicators.vcr` | **Volatility Contraction Ratio（上限）**: `ATR(10) / ATR(50)`。0.5以下で極度のボラティリティ収縮（VCPの第3〜4次収束）を示す。Trend Template適合 + 高RSランクと組み合わせることで、ブレイクアウト直前のベース形成銘柄を特定する。 |

---

## 2. 出口ルール設定 (`[exit_rules]`)

全戦略で共通して適用される、エグジット（手仕舞い）のロジック設定です。

| 変数名 | 関連 DB カラム | 説明 |
| :--- | :--- | :--- |
| `exit_type` | - | **手仕舞い戦略のタイプ**: `"fixed"` (従来の固定出口ルール)、`"hold"` (バイ・アンド・ホールド＝期間制限まで売却しない)、`"vxv_vix_ratio"` (VXV/VIX比率が閾値を下回った際に手仕舞いする)のいずれか。 |
| `vxv_vix_threshold` | - | **VXV/VIX手仕舞い閾値**: `exit_type = "vxv_vix_ratio"` の場合に使用されるVXV/VIX比率の閾値（例: 1.0）。 |
| `stop_loss_pct` | `daily_prices.close` | **固定損切り率**: エントリー価格からの下落率(%)。 |
| `partial_take_profit_pct` | `daily_prices.close` | **部分利確開始ライン**: この利益率に達すると 1/3 等の売却を行う。 |
| `partial_take_profit_sma50_atr_mult`| `indicators.sma50_atr_mult` | **オーバーエクステンション利確**: 利益率に関わらず、SMA50からATRのN倍以上乖離した場合に部分利確。 |
| `partial_ratio` | - | **利確割合**: 部分利確時に売却する保有量の比率（例: 0.333 = 1/3）。 |
| `full_exit_ema21_consecutive_days`| `daily_prices.close`<br>`indicators.ema_21` | **トレンド崩れ決済**: 終値が EMA21 を連続して N 営業日下回った場合に全決済。 |
| `full_exit_sma50_atr_mult` | `indicators.sma50_atr_mult` | **極端な過熱決済**: SMA50からATRのN倍（例: 11倍）以上離れた場合に全決済（吹き値売り）。 |
| `time_stop_days` | `daily_prices.high/low`<br>`indicators.atr_14` | **タイムストップ日数**: N日間停滞（レンジ幅が 1 ATR未満）した場合に強制手仕舞い。 |

---

## 3. 一般設定 / 最適化設定

| 変数名 | 説明 |
| :--- | :--- |
| `general.failsafe_max_days` | **強制決済期限**: エグジット条件を満たさないまま N 営業日経過した場合に強制決済。 |
| `optimization_periods` | Optuna で評価対象とする特定の相場期間（ブル/ベア等）の定義。 |
| `optimization_pruning` | 一定のヒット率やトレード頻度を満たさないパラメータを早期に切り捨てるための基準値。 |
