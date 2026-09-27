# t5_daily_sqlite_lookback

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🔴 **T5 の日次も SQLite 基点のまま — T3 と同じ遡り不足が残っている**（2026-09-20 発見）
  - **事象**: `backend/pipeline/phases/t5_signals.py:109-117` が `db.query(DailyPrice)` で
    SQLite（730日＝実測504営業日）から読んで計算している。T3 の日次で見つかったのと
    **同じ病気**（`doc/in_progress/t3_incremental_plan.md` §1.3）。
  - **`SPY_LOOKBACK_MIN_BARS` のガードは警報であって治療ではない**。遡り不足を検知して
    警告するだけで、正しい値を計算するわけではない。
  - **同じ病気を3回別々に発見している**: 2026-09-04（T3 の再構築）、2026-09-11（T5 の
    リフレッシュ＝②）、2026-09-20（T3 の日次）。毎回「その経路だけ」を直しており、
    アーキテクチャの目標として宣言されていないため次の経路が残る。
  - **対応案**: T3 の日次で採った増分化（保存済み状態から継ぐ）と同型の対処。
    `doc/in_progress/t3_incremental_plan.md` の設計をそのまま適用できるはず。
  - 関連: `doc/in_progress/t3_incremental_plan.md`、`doc/completed/t5_parquet_rebuild_plan.md`
