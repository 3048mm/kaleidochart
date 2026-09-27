# min_periods_g3_leftovers

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **`min_periods_warmup` の G3 レビューで切り出した項目（2026-09-24 起票・いずれも正しさへの実害なし）**
  - **背景**: `doc/in_progress/min_periods_warmup_plan.md` §6.3 の仕分けで「実在するが本ブランチのスコープ外」としたもの。番号は同 §6.3 の R 番号。
  - **R4（RrgChart）**: `frontend/src/components/RrgChart.tsx:97-99` が履歴30点のランク系列の NULL を `|| 0` で 0（最悪）にする。NULL の点を描かない／線を切る等の表示設計が要る
  - **R8・R17 の実測（2026-09-25・本番昇格後）**: `db_health_check --all --check-nulls --check-warmup-nulls` が DFPH・^VIX・^VIX3M・S5FI・S5TH の5銘柄を NG にする。^VIX/S5FI/DFPH は出来高が常に 0（`vol_surge_21`・`up_down_vol_ratio_50` が 0/0 で NULL）、^VIX3M は出来高 NaN が14行あり `min_periods=window` で `avg_dollar_volume_21` 等が窓ぶん NULL（指標で売買対象外・個別株は出来高 NaN 0銘柄）。いずれも構造的 NULL の誤検知。通常の scan には出ない（`--check-warmup-nulls` 限定）
  - **R8・R17（増分レジストリの warmup_bars）**: `vol_surge_21`/`vol_surge_rel_spy_21`（73）・`up_down_vol_ratio_50`（76）は構造的下限（20/20/49）ではなく標本の最大値（`down_vol == 0`・出来高0 由来の NaN は固定本数で表せない）。連鎖 warmup（398/611/810 等）は `max(warmup(inputs)) + lookback - 1` でレジストリ自身から導出できるのに手書き定数で、テストが実計算と照合しない。`is_structurally_null_column` の SPY 列例外は `df_spy is None` 早期 return の副作用を列挙したもので、`^VIX`/`^VIX3M` 等の出来高0の銘柄には効かない。**`--check-warmup-nulls` の誤検知・見逃しの温床**
  - **R9（仮想指数の先頭20日）**: 構成銘柄の先頭20日は `surge.fillna(1.0)` で中立化される（`orchestrator.py` rebuild 経路と `parquet_recompute.py`）。§2.3 の「NULL は NULL のまま」と食い違う。合成指数固有の話（計画 §3.7「別系統」）
  - **R10（scenario_market_score のフォールバック）**: `backtest/scenario_market_score.py:91-107` が SPY の `sma_50/200`・`distribution_days` を `min_periods=1`、`atr_14` を `ffill().fillna(1.0)` で計算する。T3 が無いときのみ通る経路だが、T5 の MTS と食い違いうる（計画 §4-1 で「揃えない」と確定した範囲の続き）
  - **R11（性能）**: `parquet_recompute.py` の `percent_rank` が per-group の Python コールバック（`groupby.rank(method='min')` ＋ `transform('count')` でベクトル化できる。T4 Parquet 再構築が1〜2分遅くなる見積り）／`find_stale_input_gaps` が毎回 `df['date'].tolist()`（`isna().any()` で早期 return できる。銘柄ごとに呼ばれる）／`volume_and_trends.py` の `vol_sma_21` の二重 rolling／`compute_breadth_momentum` の集計 lambda
  - **R27（仮想指数の増分ブランチ）**: `orchestrator.py` の増分ブランチが730日全履歴の `rolling(21)` を毎日回す（seed_date 以降の数行しか使わない）。seed_date の約21営業日前までに絞れば同じ surge になる。R11 と同種の性能課題
  - **R12（重複・依存方向）**: 21日窓の5箇所複製・breadth の NaN 保持式の3箇所複製・`report_stale_input_gaps` を `market_signals`（T5）から `relative_strength`（T3）が import している依存・`chart_router` の指標再実装。統合先の案: `_dollar_volume_ma21` ヘルパ・`compute_breadth_momentum` を両シナリオランナーからも呼ぶ・`indicators/input_gaps.py` への切り出し
  - **R13（`vol_accum_days_5` の object dtype）**: `np.where(cond, None, int)` で object 列になる（`is_trend_template` と同じ既存パターン）。全 None 列を Parquet へ書く経路での型推論（null 型）と、既存の int64 列との concat での型崩れを別途確認する
  - **R19（T3 自己修復が SQLite 基点）**: `backend/scripts/weekly_maintenance.py` の T3 欠損修復（L380-430 付近）が `daily_prices` の SQLite 約504本だけで `calculate_indicators` を回して欠損日の行を挿入する。`min_periods=window` 統一後は、窓が足りない列（`sma_200`・`dist_52w_high_pct`・`is_trend_template`・`rs_roc_ema_200`=611・`rs_momentum_e200`=810 等）が、Parquet に正しい値があっても NULL で入る。**issue ②（T5 が SQLite 基点）と同じ種類**で、直し方は「Parquet 基点で計算する」。修復対象が SQLite の窓の左端に近い日付のときに限って起きる（**優先度 P1 相当・要判断**）
  - **R20（ログ）**: `relative_strength.py` の `report_stale_input_gaps(df, 'spy_close', 'SPY')` は全銘柄・全 T3 実行で呼ばれる。暦の違う銘柄で MTS 入力向けの WARNING が増え、本物の警告が埋もれる。銘柄ごとではなくバッチで1回にするか、閾値を分ける
  - **R20 の補足（メッセージの誤り）**: SPY の履歴が銘柄の先頭より後に始まる「先頭側のギャップ」も「前日値を持ち越し」と報告するが、ffill は先頭を埋められず `rs_value` は NaN のまま。文言が事実と違う
  - **R23（`RS_DOT_WARMUP_BARS`）**: `RS_DOT_WARMUP_BARS = 252` と kernel の `i < warmup`（`relative_strength.py:77,110`）は、新しい `rolling(252, min_periods=252)` に対して1本ずれる（窓が満ちる最初の bar は index 251）。コメント（「`min_periods=1` だからガードが要る」）も古い。既存 issue「既存ガード2件の要否を再検証する」と一緒に、撤去するかずらすかを決める
  - **R4 の補足（ScreenerResultPage のソート）**: `frontend/src/pages/ScreenerResultPage.tsx` L265-266 のソートは NULL を `?? -999999` で扱うため、降順では末尾だが昇順では先頭に来る（SummaryTable は昇降どちらでも末尾に直した）。揃える
  - **R10 の補足**: `scenario_market_score.py` には他にも代用値が残っている（`rolling(..., min_periods=1)`・`fillna(1.0)`・`ratio=1.15` のフォールバック）
  - 関連: `doc/in_progress/min_periods_warmup_plan.md` §6.3
