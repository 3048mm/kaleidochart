# vxv_vix_exit_in_optimization

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟠 **`exit_type = "vxv_vix_ratio"` は最適化経路では黙って別物になる（2026-08-29 発見）**
  - **現象**: `[exit_rules] exit_type = "vxv_vix_ratio"` を指定して型1（最適化バックテスト）を
    回すと、VXV/VIX 判定が**一度も発火しない**。警告もエラーも出ない。
  - **原因**: 判定に使う `vxv_vix_series` を計算しているのは
    `backtest_runner.run_backtest()`（L503-527）だけで、`run_single_strategy()` の中ではない。
    そして `run_backtest()` を呼ぶのは CLI の `__main__` 1箇所のみ。
    `optimization_runner.objective()` は `run_single_strategy()` を直接呼ぶため、
    `ExitRules.vxv_vix_series` が**空の dict のまま**になる（`optimization_runner` に
    `vxv` の文字列は1つも無い）。

    ```python
    ratio = rules.vxv_vix_series.get(curr_d)   # 常に None
    if ratio is not None and ratio < rules.vxv_vix_threshold:  # 発火しない
    ```

  - **影響**: `vxv_vix_ratio` のブロックが持つのは「ストップロス + failsafe」だけで、
    `fixed` にある部分利確・EMA21 2日連続割れ・SMA50/ATR 決済・タイムストップは含まれない。
    つまり **「-8% ストップ + 120日」だけの別システムとして測られる**。
    現在は `exit_type = "fixed"` なので実害は出ていないが、切り替えた瞬間に
    「意図と違うものを測っているのに気付けない」状態になる。
  - **対応案**（どちらか）:
    1. `vxv_vix_series` の構築を `run_single_strategy()` 側（または `preload_data` の戻り値）へ寄せ、
       両経路で同じデータが揃うようにする
    2. 最低限、`exit_type == "vxv_vix_ratio"` かつ `vxv_vix_series` が空なら**落とす**ガードを入れる
       （fail-loud。黙って別物を測るよりは止める）
  - **関連**: 同種の「経路によってデータが揃わない」問題。`structure_pivot` 出口の検討時に
    `simulate_trade()` の中でデータを用意する設計にしたため同じ罠は踏まなかったが、
    それは偶然だった（`doc/in_progress/structure_pivot_screener_plan.md` §5.5）。
