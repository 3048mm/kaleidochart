# t3_fallback_parquet_base

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟡 **T3 のフォールバック（全期間計算）が SQLite の保持本数（約504本）だけで計算され、Parquet の正しい値を上書きする（案 C・2026-09-26 起票）**
  - **背景**: `doc/in_progress/t3_fallback_lookback_window_plan.md`（案 B）で、`warmup_in_progress` による不要なフォールバックは止めた。残るフォールバック（`warmup_undetermined`＝前日行が NULL、`insufficient_saved_rows`、`multi_day_gap`、欠陥）は引き続き **SQLite の約504本だけ**で全期間計算する
  - **症状1（確定値が書かれない）**: 前日行が NULL のまま真の履歴で 611 本目を越える銘柄は、`rs_roc_ema_200`（warmup 611）・`rs_momentum_e200`（810）が **NULL のまま永続化**する。「判別不能」は 2026-09-26 時点で本番 74 件、約200本の区間に分布 → **およそ週2銘柄**が新たに該当（見積もり）
  - **症状2（値がずれる）**: sandbox 検証（同計画 §6・§7-2）で、判別不能フォールバックの 245 銘柄中 **208 銘柄の `ema_150`・`ema_200`・`rs_value_e200` 等が Parquet 基点の値と不一致**（504本の先頭で EMA を再シードするため）
  - **対応案**: フォールバックの全期間計算を **Parquet 基点**にする（issue ② の T5 と同じ直し方）。対象は1日数百銘柄なので、全銘柄分の Parquet を1回読んで銘柄ごとに渡す形にすればコストは抑えられる見込み。**R19（`weekly_maintenance` の T3 自己修復が SQLite 基点）と同じ直し方なので1計画にまとめる**
  - **代替案（G3 指摘・2026-09-26）**: 判別不能の列が1つあると銘柄ごと全期間計算に回すのをやめ、**判別不能の列だけ NaN のまま増分計算を続ける**。症状2（他の EMA 系のずれ）は Parquet を読まずに直る（症状1は残る）。前提として、シード欠落時に各カーネルが増分ウィンドウの先頭から再シードしない（`t3_incremental_plan.md` 5-15d で対処済みとされる）ことを検証する必要がある
  - **修復手段（当面）**: `--rebuild-from T3`（Parquet 基点）で一時的に正しい値に戻る。症状1の銘柄は翌日以降も NULL が続く
  - 関連: `doc/in_progress/t3_fallback_lookback_window_plan.md` §1.4・§6・§7-2、下の「`rs_roc_ema_200` が NULL の40銘柄」
