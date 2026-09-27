# warmup_null_check_threshold

> `doc/issue_list.md` から移設（2026-09-28）。優先度・未完了/完了の状態は `issue_list.md` 側が正。完了したら本ファイルの内容ごと `doc/issue_list_archive.md` へ移す。

- [ ] 🟡 **ウォームアップ検査（`--check-warmup-nulls`）の閾値が未確定 — 検出された40銘柄の調査も要る**（2026-09-23 起票）
  - **背景**: 「演算上必要な日数がある銘柄なら、その列は NULL にならない」という検査を
    `tools/db_health_check.py` に opt-in フラグとして追加した（ユーザー提案）。
    今回の `rs_momentum_e200` 全損は、これがあれば**初日に3,056銘柄で検出できた**。
  - **未確定の理由**: 本番の全3,362銘柄に当てたところ偽陽性ゼロにならなかった。内訳:
    1. **構造的に NULL（除外が必要）** — SPY の `rs_*` 全列、`^VIX`/`^VIX3M`/SPY の
       出来高由来列（`vol_surge_21` / `vol_surge_rel_spy_21` / `up_down_vol_ratio_50`）
    2. **閾値の誤り** — `atr_14`/`atr_pct_14` を 0 としたが、価格が12〜18行しかない
       新規上場7銘柄で NULL。Wilder 実装は14本未満で例外を投げて NaN になる。
       標本を「2,400本以上の履歴を持つ120銘柄」に限ったことによる見落とし
    3. **本物の異常（別途調査）** — `rs_roc_ema_200` が NULL の**40銘柄**
       **【2026-09-26 原因判明】** T3 の `warmup_in_progress` フォールバックが SQLite 504本で全期間計算して NULL を書く仕組みによるもの（`doc/in_progress/t3_fallback_lookback_window_plan.md` §1.1）。昇格後に 95 銘柄へ拡大して表面化。案 B で再発防止、残るケースは上の「T3 のフォールバック…（案 C）」へ
       （個別25・テーマ12・指標2・市場1）。511本以上の履歴があるのに値が無い。
       **偽陽性ではなく、この検査が掘り当てた実在の異常**
  - **残作業**: ①1 と 2 の対処（除外規則と閾値の修正）②3 の40銘柄を調査
    ③週次メンテナンスに組み込むか判断
  - 関連: `doc/in_progress/t3_incremental_plan.md` §7-5、`backend/indicators/incremental_state_registry.py`
