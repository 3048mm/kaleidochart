# deploy_after_merge_full_history

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟠 **`deploy_after_merge.py` は新規カラム追加時に730日ぶんしか埋めない（2026-08-29 登録 / 事象は 2026-08-26 に遭遇）**
  - **現象**: `tools/deploy_after_merge.ps1 -RebuildFrom T3` は**ホット期間730日のみ**を再計算する
    （`backend/scripts/deploy_after_merge.py` L113 のコメントに明記）。既存カラムの値を
    直すだけなら問題ないが、**新規カラムでは残りの Parquet 履歴が NULL のまま残る**。
  - **影響**: バックテスト期間は 2021-03〜2026-03 の5年なので、**大半が欠損したまま
    最適化に入る**。エラーにはならず、検出件数が減るだけなので気付きにくい。
  - **実例**: `sp_pivot` / `sp_hl` の T3 追加（2026-08-26）。専用の
    `backend/scripts/backfill_structure_pivot.py` を書いて回避した
    （Parquet を row-group 単位でストリームし、既存63列に触れず2列を追記）。
    **同じ罠は次のカラム追加でも踏む。**
  - **対応案**: `deploy_after_merge.py` 側に (a) 全期間モード、または最低限
    (b) 「対象カラムが Parquet の古い世代に存在しない＝新規カラム」を検出したら
    警告して止めるガードを入れる。
  - **関連**: `doc/completed/deploy_after_merge_plan.md`、
    `doc/in_progress/structure_pivot_screener_plan.md` §3.5
