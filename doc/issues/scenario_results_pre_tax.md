# scenario_results_pre_tax

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟠 **`output/scenario/` の既存結果は税の配線が入る前のもの（2026-08-29 発見）**
  - **事実**: 現在 `output/scenario/` にある 750 run すべてで、
    `scenario_summary.json` の `run_params` に **`consider_tax` キーが無い**。
    同キーを書き出すのは `scenario_runner.py` L654 で、入ったコミットは
    `9f380fc`（2026-08-25 08:36）。一方、出力ファイルの更新時刻は **同日 02:09** ＝ 6時間半前。
  - **影響**: `[general] consider_tax = 0.2` を前提に読むと**成績を過大評価する**。
    型3（個別銘柄シナリオ）は実運用シミュレーションなので、税なしの結果を
    税ありのつもりで解釈すると、最終資産・CAGR がそのぶん楽観に振れる。
  - **対応**: 型3 バッチを回し直す。その際
    `run_params.consider_tax = 0.2` が記録されることを確認すれば、
    `tax_rate_wiring_plan.md` §6 が「本丸」とした
    **並列 MC ワーカーへの税率伝播の検証そのもの**になる（一石二鳥）。
    バッチは4時間規模なのでユーザー実行。
  - **関連**: `doc/in_progress/tax_rate_wiring_plan.md` §6
