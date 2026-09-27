# tax_optimization_no_effect

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🔵 **`consider_tax_optimization` は現在の目的関数に一切影響しない（2026-08-29 発見）**
  - **事実**: 税は `backtest_report.py` L210-211 で **`strat_mult`（複利倍率）を積むときだけ**
    勝ちトレードに適用される。型1の目的関数が使う `geo_mean_gain` / `avg_gain` /
    `expectancy_lcb` は**素の `pnl_pct` から計算**されるため、税を変えてもスコアは動かない。
  - **実測**: B2 / 2024-06〜2025-12 で税率 0.0 → 0.2 にしても
    1取引平均は +12.816% のまま変化なし。複利倍率だけ 18.18 → 8.23 に低下。
  - **影響**: 現在 `consider_tax_optimization = 0.0` なので実害は無い。
    ただし設定キーが「型1専用の税率」として存在し仕様書にも書かれているため、
    **非ゼロにすれば効くと誤解する**（スコアは動かず `port_cagr` の記録値だけ変わる）。
  - **対応案**: (a) 目的関数が税後の値を使うようにする、または
    (b) 設定キーのコメントと仕様書に「スコアには効かない」と明記する。
    2026-08-24 に目的関数を CAGR から幾何平均へ変えた際の副作用なので、
    どちらが意図かは要判断（`doc/completed/objective_quality_first_plan.md` §3.2）。
