# theme_index_spy_gap_warnings

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟡 **仮想テーマ指数のT3全期間再計算で、SPY参照データの供給不足による警告が大量発生する（2026-09-24 発見・未調査）**
  - **背景**: `min_periods_warmup_plan.md` の5-6bで `relative_strength.py` の無言ffillに
    `report_stale_input_gaps()`（`market_signals.py`の`^VIX`/`^VIX3M`と同じ確立済みパターン）を
    追加したところ、5-11（sandboxでのT3→T4→T5全期間再計算）実行時に、**仮想テーマ指数の
    T3再計算経路で`spy_close`/`spy_volume`の供給ギャップに対する新規WARNINGが大量に出た**
    （計算結果自体は5-6b追加時点では不変。ログのみの追加のため、この発見はログが新設された
    副産物であり、5-6b自体の不具合ではない）。
  - **推定原因（未検証）**: SQLiteホットキャッシュの730日パージが、SPY本体と仮想テーマ指数
    （テーマ構成銘柄から合成する指数）とで非対称に効いている可能性がある
    （仮想テーマ指数の合成経路がSPYの全期間データを前提にしているのに対し、
     ホットウィンドウ側は730日しか保持しないため、全期間Parquet基点の再計算時に
     ギャップとして検出される、という仮説）。**根本原因の特定はこの発見の時点では未実施**。
  - **影響範囲の見積り（未実施）**: 計算結果が実際に不正確になっているのか、単に
    ffillで吸収されて実害が無いログノイズなのかは未確認。実害があるなら仮想テーマ指数の
    RS系列の精度に波及する可能性がある。
  - **追記（2026-09-26・`t3_fallback_lookback_window_plan.md` §7-1）**: `--rebuild-from T3` で仮想テーマ指数 171 本が `no_saved_rows` 扱いで **SQLite の保持範囲だけで再計算**され、`rs_value_e200` 等の窓の先頭がウォームアップ（NULL）になることを観測。ログは「SQLiteの保持期間に収まるため正確」と出すが、仮想テーマ指数には当てはまらない
  - **再現方法**: sandbox環境で `update_pipeline.py --rebuild-from T3 --skip-fetch --skip-sync`
    を実行し、`report_stale_input_gaps` のWARNINGログを仮想テーマ指数のsymbol_idで絞り込む。
  - **対応案**: ①ホットキャッシュ購入経路と仮想テーマ合成経路のSPY参照範囲を揃える
    ②実害（値のズレ）があるかをまず測定してから優先度を決める
  - 関連: `doc/completed/min_periods_warmup_plan.md`（5-6b・5-11完了ノート）
