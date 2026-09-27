# t3_not_recomputed_after_price_fix

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

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
