# unused_indicators

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] **未採用インジケーターの検証（2026-07-06 棚卸し / 2026-08-04 に区分Aの本命が解消）**
  - **区分Aは G1/G2/G3 でほぼ消化済み**（`td9`→G1・G2、`change_1m_pct`→G1・G2・G3、`change_1w_pct`→G2、`is_rs_blue_dot`→G3）。残るのは下記3種。
    | 指標 | 位置づけ |
    | :--- | :--- |
    | `is_rs_red_dot` | **枯渇シグナル**。ロング専用スクリーナーでは「除外フィルタ」としての利用になり、他とは用途が違う |
    | `vol_surge_rel_spy_21` | 市場対比の出来高サージ。`vol_surge_21`（絶対）との差分に意味があるかが論点 |
    | `rs_momentum_e5/e14/e63/e200`（`e21` も未採用） | 期間違いの相対モメンタム。全期間を試すのではなく1〜2本に絞る |
  - **次の優先は区分B**（フィルタ関数は実装済みだが戦略 TOML で未採用。5項目すべて未使用のまま）:
    `is_rs_trend_rank_s21_gt_s63` / `is_theme_rs_trend_rank_s21_gt_s63` / `is_theme_rs_ratio_e14_gt_e21` / `is_theme_rs_ratio_e21_gt_e63` / `min_rs_macd_hist_rank_21`
  - **区分C は配管作業が前提**（`apply_filters_to_df` にランク未接続）。現状マージは `rs_ratio_rank_e14/e21/e63` と `rs_trend_rank_s14/s21/s63` の6種のみ。
    対象: `rs_momentum_rank_e*`(5) / `rs_roc_ema_rank_e*`(5) / `rs_value_rank` / `rs_ratio_rank_e5,e200` / `rs_trend_rank_s5,s200`
