# rs_momentum_e200_redesign

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **`rs_momentum_e200` の積み方が過剰かもしれない — 退役または再設計の検討**（2026-09-21 起票）
  - **事象**: 200期間の処理を4段積んでおり（EMA200 シード → 200日窓 z-score →
    14日 ROC → ROC の EMA200 シード → 200日窓 z-score）、必要遡りが **610本（約2年5ヶ月）**。
    `min_periods` を窓幅に揃える① を適用すると **810本（約3年3ヶ月）**になる。
  - **利用状況**: 表示専用（`chart_router.py` の RRG / `dashboard_router.py` /
    `panel_builders.py`）。`backtest_config.toml` に `e200` の参照は無く、
    バックテスト・最適化には使われていない。
  - **論点**: 3年3ヶ月の履歴を要求する指標が必要か。RRG の200日窓を残すにしても、
    14日 ROC を200日 EMA で平滑化してさらに z 化する積み方は過剰かもしれない。
  - 関連: `doc/in_progress/min_periods_warmup_plan.md` §4-7、`doc/in_progress/t3_incremental_plan.md` §8
