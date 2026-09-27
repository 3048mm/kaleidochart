# bars_available_column

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **`bars_available`（実遡り本数）を診断用の列として追加する（2026-09-24 起票。旧 issue①の残作業）**
  - **背景**: `doc/completed/min_periods_warmup_plan.md`（旧issue①）の調査時、「この銘柄は
    何本遡れるか」を都度スクリプトで再計算していた（`tmp/check_minperiods_impact.py`等）。
    T3に「上場からの営業日カウント」を1列持たせれば、以後は同じ調査をSQLで即座に行える
    （例: `WHERE bars_available < 200` でウォームアップ未了の銘柄を一覧できる）。
  - **想定用途**: ①ウォームアップ由来のNULLと「本物の異常によるNULL」の切り分けの高速化
    （`--check-warmup-nulls`の閾値調整にも使える）②将来のIPO特化スクリーナー
    （同計画 §8で「本計画では作らない」とされたアイデア）の土台
  - **優先度が低い理由**: 診断・将来機能のための追加列であり、現行機能に不足があるわけではない。
    追加にはスキーマ変更（種別C）と全期間再計算が伴う。
  - 関連: `doc/completed/min_periods_warmup_plan.md` §8
