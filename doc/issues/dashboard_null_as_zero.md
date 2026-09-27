# dashboard_null_as_zero

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟡 **ダッシュボードが `market_trend_score`/`distribution_days` の NULL を 0 に潰して表示する（2026-09-16 コードレビューで発見）**
  - **先に決めるべき設計論点がある**（2026-09-17 ユーザー指摘: 「そもそもこの課題はきっちり直すのにどうあるべきか議論が必要」）。**`doc/completed/t5_parquet_rebuild_plan.md` §8 に論点と選択肢を整理済み**: NULL がデータ層から UI へ流れる過程で各層が別々のフォールバックを発明しており（`"UNKNOWN"` / BEAR / `0.0`＝最も弱気 / `0`＝最良）、**同じ画面に「判定不能」「最悪」「最良」が同時に並ぶ**。決めるべきは ①どの層が表示責任を持つか ②折れ線での欠損の描き方 ③既存の `or 0` 系の洗い出しと統一。**① の計画（`min_periods` 統一）でスクリーナー・チャートにも波及するため、個別対応の前に原則を決める方が安い**
  - **事象**: `dashboard_router.py` の `market_trend_score or 0.0` / `distribution_days or 0` が、
    T5の遡り不足で意図的にNULLになっている日付（フェーズ「判定不能」）を「MTS 0.0（最も弱気）・
    Distribution Days 0（最良）」という矛盾した値で表示してしまう。`trend_score_history`の折れ線も
    その区間だけ0に落ちて「暴落」に見える
  - **対応案**: `DashboardResponse`の該当2フィールドをOptional化し、フロントのメーター・折れ線を
    中立表示にする
  - **スコープ拡大のため保留**: APIスキーマ＋フロントの両方に影響するため、`t5_parquet_rebuild_plan.md`
    （5-9e）ではスコープ外として切り出した。別セッションで対応予定
  - 関連: `backend/api/dashboard_router.py`、`doc/completed/t5_parquet_rebuild_plan.md` §7-9(3)
