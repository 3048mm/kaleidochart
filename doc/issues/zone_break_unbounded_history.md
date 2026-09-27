# zone_break_unbounded_history

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟠 **`zone_break` の必要履歴が有界でない — ホットウィンドウ計算では約1%の銘柄が不正確**（2026-09-22 実測）
  - **事象**: `zb_ssl` / `zb_bsl` / `is_zone_break_bull` / `is_zone_break_weak` は、
    確定した反転（BOS）ごとに状態がリセットされるが、**トレンドレッグの長さに上限が無い**
    （移植元 Pine Script の性質）。直前のリセットからの本数が数千本に及ぶ銘柄がある。
  - **実測（300銘柄・2026-09-22）**: 「末尾1本を全履歴計算と一致させるのに必要な生価格本数」

    | 列 | 中央値 | p90 | p99 | 最大 | 250本超 |
    |---|---|---|---|---|---|
    | `is_zone_break_bull` | 120 | 120 | 250 | 250 | 0/300 |
    | `zb_ssl` | 120 | 120 | 250 | **2,400** | 2/300 |
    | `zb_bsl` | 120 | 120 | 251 | **2,400** | 3/300 |
    | `is_zone_break_weak` | 120 | 120 | 250 | 400 | 1/300 |

    対照的に `structure_pivot`（`sp_*`）は最大120本で**有界**だった。
  - **現状の扱い**: lookback を 400 とし（`ZONE_BREAK_LOOKBACK`）、
    **厳密解ではないことを `doc/backend_specification.md` §3.4 に明記済み**（2026-09-22 ユーザー判断）。
    **これは増分化で生じた問題ではなく、現行実装でも同じ**（SQLite の504行で計算しているため）。
  - **実害**: `zb_*` は表示専用ではなく、`backtest_config.toml` の4戦略が使っている
    （`is_zone_break_bull_flip` / `is_zone_break_bull_breakout` / `is_zone_break_weak`）。
  - **対応案**: ①状態を列として保存する（スカラー6列＋可変長のフラクタル/FVGリストの
    シリアライズが要る。スキーマ変更＝種別 C）②`zone_break` の設計自体を見直す
    （`counter_trend` が同型の問題で本番昇格後に事故を起こした前例あり:
    `doc/completed/structure_pivot_screener_plan.md` §5.6）③現状を受容する
  - 関連: `doc/in_progress/t3_incremental_plan.md` §8、`doc/completed/zone_break_plan.md`
