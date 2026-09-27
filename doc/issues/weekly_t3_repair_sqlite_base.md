# weekly_t3_repair_sqlite_base

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。
> 2026-09-28 に書き直した（旧 slug `t3_not_recomputed_after_price_fix`。経緯は末尾）。

- [ ] 🟠 **週次の T3 修復が「行の欠落」しか拾わず、しかも SQLite 基点で再計算する**（2026-09-20 発見 / 2026-09-28 書き直し）
  - **事象1（値の誤りを拾わない）**: `backend/scripts/weekly_maintenance.py:378` は
    `Indicator.id.is_(None)`、すなわち**行が存在しないケースしか拾わない**。
    行はあるが値が誤っている・NULL の行は対象外。
    実例: `rs_momentum_e200` の全損（2026-09-15〜18、全銘柄 NULL）は行が存在するため週次では直らなかった。
  - **事象2（遡り不足で書く）**: 同 `:393-416` は `db.query(DailyPrice)`（SQLite の約504本）から
    `calculate_indicators` で再計算する。SQLite 窓の先頭に近い日付を修復すると、
    `min_periods=window` の列（`sma_200`・`rs_roc_ema_200`=611・`rs_momentum_e200`=810 等）は NULL、
    EMA 系は再シードでずれた値のまま書き込まれる。`min_periods_g3_leftovers.md` の **R19 と同一**。
  - **対応案**: ①修復の入力を Parquet 基点にする（`parquet_maintenance.recompute_and_publish` と同じ形）
    ②「値が誤っている行」を検出する手段を持つ（例: Parquet 基点の再計算値との突き合わせ、または `--check-warmup-nulls`）。
    ① は T3 フォールバックの Parquet 化（`t3_fallback_parquet_base.md`・案 C）・R19 と同じ直し方なので**1計画にまとめる**
  - **対象外（確認済み・2026-09-28）**: 手動の価格補正後の T3 は再計算される。
    `adjust_symbol_split.py` は 2026-08-25（`f4de258`）以降 `recompute_and_publish` で
    対象銘柄・所属仮想テーマの T3 と T4 を Parquet 全期間で再計算し SQLite の `indicators` も差し替える（`:326`）。
    `resync_price_scale.py` は実行後に `--rebuild-from T3`（Parquet 基点）を回す前提。
  - **別件（検知の問題）**: 分割で段差が出ても自動補正は無く、週次レポートを人が見て補正するまで
    T3 は段差入りの系列で計算され続ける。→ `weekly_split_consistency_scan.md` の領分
  - 関連: `doc/completed/t3_incremental_plan.md` §8、`doc/issues/min_periods_g3_leftovers.md`（R19）、`doc/issues/t3_fallback_parquet_base.md`

## 書き直しの経緯（2026-09-28）

起票時の本文は「`adjust_symbol_split.py` で過去の close を補正しても、T3 の過去行は古いまま残る」を主事象にしていたが、
起票時点（2026-09-20）で既に `adjust_symbol_split.py` が T3 を Parquet 全期間で再計算していた（上記「対象外」）。
コードを確認せずに書かれた記述と判断し、実在する週次修復の欠陥に絞った。
同日に完了扱いにした「T5 の日次も SQLite 基点」（`doc/issue_list_archive.md`）と同じ型の誤り。

起票時の本文:

- [ ] 🟠 **遡及的な価格修正のあとに T3 が再計算されない — ウィークリー修復自体にも欠陥がある**（2026-09-20 発見）
  - **事象**: `adjust_symbol_split.py` で過去の close を補正しても、T3 の過去行は古いまま残る。
    T3 の日次は `date > t3_max` の行しか書かないため、**過去の行は二度と見直されない**。
  - **さらに、現在のウィークリー修復自体が同じ欠陥を持つ**:
    - `backend/scripts/weekly_maintenance.py:365` は `Indicator.id.is_(None)`、すなわち
      **行が存在しないケースしか拾わず、値が誤っている行は対象外**
    - 同 `:403` は `db.query(DailyPrice)`（SQLite の504本）から再計算するため、
      「修復」として**遡り不足の行を書き込む**
  - **実例**: `rs_momentum_e200` の全損（2026-09-15〜18、全銘柄 NULL）は行が存在して
    値が NULL のため、**ウィークリーでは直らなかった**。
  - **対応案**: ①補正した銘柄の T3 を Parquet 基点で再計算する仕組みを週次に入れる
    ②ウィークリー修復の入力を Parquet 基点にする ③「値が誤っている行」を検出する手段を持つ
  - 関連: `doc/in_progress/t3_incremental_plan.md` §8
