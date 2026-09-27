# min_periods_leftover_guards

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **`min_periods` 統一後に残った既存ガード2件の要否を再検証する（2026-09-24 起票。旧 issue①の残作業）**
  - **背景**: `doc/completed/min_periods_warmup_plan.md`（旧issue①）でA-core（`min_periods=window`統一）
    が完了し、`has_breadth`（`market_signals.py`の日付ハードコード）は同計画の5-8b④で撤去済み。
    残る `RS_DOT_WARMUP_BARS = 252`（`relative_strength.py:77`、rolling(252, min_periods=1)由来の
    偽点灯を止めるための本数ガード）は、A-core適用後は原理的に不要になったはずだが、
    **「撤去しても偽点灯が復活しないこと」の独立検証が必要なため、計画のスコープを
    膨らませないよう据え置いた**（同計画 §4-7・§8）。
  - **対応案**: sandboxで`RS_DOT_WARMUP_BARS`を撤去した場合と現状維持の場合で
    `rs_blue_dot_age`/`rs_red_dot_age`の点灯パターンを突き合わせ、差分が無いことを
    確認してから撤去する。撤去してもコード量が減るだけで実害は無いため優先度は低い。
  - 関連: `doc/completed/min_periods_warmup_plan.md` §4-7・§8
