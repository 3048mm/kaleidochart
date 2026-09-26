import time
import logging
import multiprocessing
from collections import Counter
from typing import Dict, List, Optional, Tuple
from datetime import date
from sqlalchemy import func
import pandas as pd

from pipeline.utils import sanitize_numeric
from db.database import init_db, get_db

# フォールバック理由の識別子（T3増分化計画 5-6b・§3.5。5-15b/5-15c で分類を改訂）。
#
# `_calculate_t3_worker` が state=None（全期間計算）にフォールバックしたとき、
# どの条件で落ちたかを呼び出し側（`sync_phase_t3_indicators`）へ返すための識別子。
# NULL列が理由の場合は `"{識別子}:列名,列名"` の形式で列名も付与する（集計時は
# ':' より前のカテゴリ部分だけを見て件数を数える。5-6b の実データ検証で
# 「フォールバックが無言」（誰も気づかないまま4日間不正確な値が書かれ続けた）
# ことが問題だったため、理由を可視化することが本設計の要点）。
#
# 5-15b（code-review指摘1・初版）: `NULL_RECURSIVE_COLUMN` は当初「供給履歴にNaNが
# 1つでもあれば理由を問わずこの識別子」としていたが、これだと「まだウォームアップ
# 中で正当にNULL」な列（履歴の浅い銘柄では大多数）まで「壊れている」扱いになり、
# ログのS/N比が悪化する（本番実測で83.6%は増分経路に乗れ、乗れない銘柄の大半は
# 履歴不足による正当なNULLだった）。そこで欠陥（`NULL_RECURSIVE_COLUMN`）と
# 正当なウォームアップ中（`WARMUP_IN_PROGRESS`）を分ける方針自体は正しかった。
#
# 5-15c（2回目の code-review 指摘1・5-15b の回帰）: 5-15b の実装は「その行の
# 絶対位置（SQLiteの保持期間内での位置）が warmup_bars を超えているか」で
# 判定していたが、SQLiteの保持行数（実測504）自体が一部の列の warmup_bars
# （`rs_roc_ema_200`=611、A-full前の実測値では511）より小さいため、この判定が
# **原理的に到達不能**だった（`position > warmup_bars` が届かない）。その結果、本番の
# 実際の欠陥（`rs_roc_ema_200` が全銘柄NULL）が `WARMUP_IN_PROGRESS`（正当）に
# 誤分類され、5-6b が塞いだはずの「誰も気づかない」穴が再び開いていた。
#
# 修正: 銘柄の真の履歴長はSQLite（ホットキャッシュ）からは分からないため、
# 「行の絶対位置」ではなく「増分ウィンドウ内で観測できる性質」だけで判定する。
#   1. **単調性の破れ**（ある行には値があるのに、より新しい行でNULLに戻る）は
#      正常系では起こり得ないため、無条件に欠陥（`NULL_RECURSIVE_COLUMN`）。
#      本番の実際の不具合（2026-09-14以前は値あり、09-15以降NULL）はこの形。
#   2. ウィンドウ全体がNULLで `warmup_bars < K`（増分ウィンドウ長）なら、
#      ウィンドウがどこから始まっていてもウィンドウ最終行の絶対位置は
#      必ず warmup_bars を超えるため、欠陥と断定できる。
#   3. ウィンドウ全体がNULLで `warmup_bars >= K` なら、正当なウォームアップ中
#      なのか欠陥なのかこの検査だけでは判別できない
#      （`WARMUP_UNDETERMINED`。`rs_roc_ema_200` がこれに該当する）。
#   4. ウィンドウ内でNULL→非NULLへ単調に移行している場合、最初の非NULL位置
#      （`first_valid_pos`）が `warmup_bars` を超えていても増分計算は継続する
#      （欠陥扱いにはしない。t3_fallback_lookback_window計画 5-7d・G3 2周目
#      R8/R9・案X）。理由:
#        - 欠陥扱いにするとSQLite保持本数（実測504）だけの全期間計算に回り、
#          611本必要な`rs_roc_ema_200`にNULLを書く→翌日「単調性の破れ」と
#          誤検出される連鎖（本計画が断ち切ろうとした経路そのもの）を再び
#          起こしてしまう（R8）。
#        - `warmup_bars` はA-fullの実測値であり、先頭入力がNaNの銘柄などでは
#          真の立ち上がりが後ろにずれるため、`first_valid_pos > warmup_bars`が
#          必ずしも欠陥を意味しない（誤判定の余地がある。R9）。
#      代わりに `warnings` としてフェーズ終了時に1回だけWARNINGで可視化し、
#      `--rebuild-from T3` を推奨する（銘柄ごとの個別ログは出さない）。
FALLBACK_REASON_NO_SAVED_ROWS = 'no_saved_rows'                    # 保存済みT3行が無い（新規上場・オンボード直後）
FALLBACK_REASON_INSUFFICIENT_ROWS = 'insufficient_saved_rows'      # 保存済み行数がK（max_lookback()）に満たない
FALLBACK_REASON_MULTI_DAY_GAP = 'multi_day_gap'                    # 新規に書く日付が2日ぶん以上ある（または0日）
FALLBACK_REASON_NULL_RECURSIVE_COLUMN = 'null_recursive_column'    # RECURSIVE型列が確実に欠陥（単調性の破れ、またはK>warmup_barsで全NULL）
FALLBACK_REASON_WARMUP_UNDETERMINED = 'warmup_undetermined'        # ウィンドウ全体がNULLかつK<=warmup_barsで判別不能（5-15c）
# 5-15c で追加した FALLBACK_REASON_WARMUP_IN_PROGRESS は t3_fallback_lookback_window
# 計画（5-3）で廃止した。ウィンドウ内でNULL→非NULLへ単調に移行し、最終供給行
# （前日）に値がある列は、増分計算（RECURSIVE型列は前日値をシードに最終行だけを
# 1歩計算する契約）自体は正しく継続できる。ただし「過去行のNULLは無関係」と
# 言えるのはこの1歩計算のシード（前日値）についてのみであり、WINDOW型列
# （`rs_ratio_eN`・`rs_momentum_eN` 等）はRECURSIVE型列に対する通常のrolling
# なので、供給履歴のNULL区間が真のウォームアップと一致していること
# （`first_valid_pos <= warmup_bars`）が正しさの前提になる。この上限は
# **保証されない**（5-7d で超過は WARNING のみ・増分継続に変更。超過した列を
# 持つ銘柄は WINDOW 型列に NaN・誤値を書きうる。根治はフォールバックの
# Parquet 基点化＝issue_list の案C）。単調移行自体はフォールバック理由ではない。

def _calculate_t3_worker(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virtual=False, spy_latest_date: Optional[date] = None):
    """Worker function to calculate T3 for a single ticker in a separate process using direct sqlite3 connection (fast, no ORM).

    増分化（T3増分化計画 5-6・§3.2）: 保存済みT3行から状態を復元できる場合は
    `calculate_indicators(df, spy_df, state=True)` を使い、直近 K+1 本
    （生価格 K+1 本 ＋ 保存済みT3列 K 本、K=`max_lookback()`）だけで
    新規1日分を計算する。以下のいずれかに該当する場合は state=None
    （全期間計算）にフォールバックする（§3.5。現行の全行読み込みがそのまま
    フォールバック経路になる）:
      - 保存済み T3 行が存在しない（t3_max が None。新規上場・オンボード直後）
      - 保存済み行数が K に満たない
      - 新規に書く日付がちょうど1日ぶんでない（0日・連休明け等の2日以上）
      - RECURSIVE型列（前日値を継ぐ列。SPYの `rs_*` は構造的にNULLが正常のため
        除外。5-15b）の供給履歴に NaN がある

    RECURSIVE型列にNaNがある場合、さらに「欠陥」（`FALLBACK_REASON_NULL_RECURSIVE_COLUMN`）・
    「判別不能」（`FALLBACK_REASON_WARMUP_UNDETERMINED`）・「増分継続（正当な
    ウォームアップ中）」の3つを区別する（5-15b・5-15c。2回目の code-review 指摘1。
    t3_fallback_lookback_window計画 5-3 で3つめの扱いを改訂）。

    5-15b の初版は「NaNの行の `daily_prices` 内での位置が `warmup_bars` を
    超えているか」で判定していたが、SQLiteの保持行数（実測504）自体が
    `rs_roc_ema_200` の `warmup_bars`（当時511。A-full後は611）より小さいため、この判定が
    **原理的に到達不能**だった（本番の実際の欠陥が誤って「正当」に分類され
    続けていた）。5-15c で以下の、増分ウィンドウ（K本）内だけで判定できる
    性質に基づく方式へ改めた（銘柄の真の履歴長はSQLiteからは分からないため）:

    1. **単調性の破れ** — ウィンドウ内のある行には値があるのに、より新しい
       行でNULLに戻っている場合、正常系では起こり得ないため無条件に欠陥
       （`NULL_RECURSIVE_COLUMN`）。本番の実際の不具合
       （2026-09-14以前は値あり、09-15以降NULL）はこの形で検出できる。
    2. **ウィンドウ全体がNULL・`warmup_bars < K`** — ウィンドウがどこから
       始まっていても最終行の絶対位置は必ず `warmup_bars` を超えるため、
       それでもNULLなら欠陥（`NULL_RECURSIVE_COLUMN`）。
    3. **ウィンドウ全体がNULL・`warmup_bars >= K`** — ウィンドウが銘柄の
       真の先頭から始まっている場合は正当なウォームアップ中でもNULLに
       なりうるため、判別不能（`WARMUP_UNDETERMINED`。`rs_roc_ema_200` が該当）。
    4. それ以外（ウィンドウ内でNULL→非NULLへ単調に移行し、最終供給行＝前日に
       値がある） — **フォールバックせず増分計算を継続する**
       （t3_fallback_lookback_window計画 5-3）。RECURSIVE型列は前日値
       （K-1行目）をシードに最終行だけを1歩計算する契約
       （`incremental_merge.prev_self_seed`）なので、シードとして使う前日値
       さえ実値であれば、それより前の行のNULLが「真のウォームアップと一致
       している」限り増分計算の正しさに影響しない。旧仕様（5-15c）は
       このケースも `WARMUP_IN_PROGRESS` としてフォールバックしていたが、
       そのフォールバック先の全期間計算がSQLiteの保持本数（実測504本）
       だけで行われるため、611本必要な `rs_roc_ema_200` にNULLを書いてしまい、
       翌日「値→NULLへ戻った」（単調性の破れ）と誤検出される連鎖を生んでいた
       （本計画が解消する不具合そのもの）。
       このうち `first_valid_pos > warmup_bars`（単調ではあるが、本来もっと
       早く値が出ているはずなのに出ていない）のケースは、5-7a では確実な
       欠陥として `NULL_RECURSIVE_COLUMN` に分類していたが、5-7d（G3 2周目
       R8/R9・案X）で **WARNING のみに変更し、増分計算は継続する**ように
       改めた（欠陥扱いにするとSQLite504本の全期間計算に回ってしまい、
       本計画が断ち切ろうとした`rs_roc_ema_200`のNULL連鎖を再び起こすため。
       また `warmup_bars` は実測値であり、先頭入力がNaNの銘柄では真の
       立ち上がりが後ろにずれて誤判定しうる）。該当した列は `warnings`
       としてワーカーの戻り値に含め、呼び出し側がフェーズ終了時に1回だけ
       集計してWARNINGログに出す。

    複数列で分類が割れた場合、1つの `fallback_reason` は
    欠陥 > 判別不能 の優先順位で選ぶ（増分継続の対象になる列は
    フォールバック理由に寄与しない。`first_valid_pos > warmup_bars` の列も
    増分継続の対象であり `warnings` に積まれるだけで `fallback_reason` には
    寄与しない）。

    **WINDOW型列（`rs_ratio_eN`・`rs_momentum_eN` 等）への影響**: WINDOW型列
    自体は毎回生価格から無条件に再計算されるため直接この判定対象ではないが、
    その入力（RECURSIVE型列）に対して通常の rolling（`min_periods=window`）を
    かけるだけなので、供給履歴の先頭のNULL区間が真のウォームアップと一致して
    いる（単調・最終行に値あり）限り、rolling が返すNaN/実値も真の値と一致する
    （詳細は `backend/indicators/incremental_merge.py` のモジュールdocstring）。
    `first_valid_pos > warmup_bars` の列がある場合はこの前提が破れている
    可能性があるため、上記のとおり WARNING で可視化する。

    戻り値は `(ticker, sid, records, fallback_reason, warnings)` の5要素
    タプル（5-6b で `fallback_reason` を追加、5-7d で `warnings` を追加）。
    `warnings` は文字列のリストで、増分経路・フォールバック経路のいずれでも
    返る（該当が無ければ空リスト）。各要素は
    `"null_prefix_exceeds_warmup:列名(first_valid_pos=N>warmup_bars=M)"`
    の形式で列名と実測値を含む。例外発生時は `(ticker, sid, exception, None, [])`。
    """
    try:
        import sqlite3
        import pandas as pd
        from datetime import date
        from indicators.calculate import calculate_indicators
        from indicators.incremental_state_registry import (
            columns_with_warmup_threshold, is_structurally_null_column,
            max_lookback, recursive_column_names, supplied_column_names,
        )

        conn = sqlite3.connect(db_path, timeout=60.0)
        try:
            query = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE symbol_id = ? ORDER BY date"
            df_price = pd.read_sql_query(query, conn, params=(sid,))

            if df_price.empty:
                return ticker, sid, [], None, []

            # sqlite3 returns date as string, parse to date object
            df_price['date'] = pd.to_datetime(df_price['date']).dt.date

            # 増分経路が使えるか判定する（§3.5 のフォールバック条件）。
            # 生価格は必ず daily_prices から読む（indicators 側は object dtype 汚染の
            # 実績があるため使わない。§3 注意点3）。
            # 不変条件: df_inc が None のときのみ fallback_reason を設定する
            # （増分経路が成立したら fallback_reason は必ず None のまま）。
            df_inc = None
            fallback_reason = None
            prefix_warnings: List[str] = []  # 5-7d: 欠陥ではなく警告に留める事象（列名・実測値付き）
            if t3_max is None:
                fallback_reason = FALLBACK_REASON_NO_SAVED_ROWS
            else:
                # ギャップ判定の窓は、実際に書く窓（delta_df。spy_latest_dateで
                # 上限を切る）と揃える（5-15c・2回目のcode-review指摘2）。
                # 揃えないと、T2がSPYより1日進んだ銘柄（フライングデータ）で
                # 「書く行は1行なのにlen(new_dates)==2」となりMULTI_DAY_GAPへ
                # 誤フォールバックし、以後フォールバックが継続してしまう。
                write_window_mask = df_price['date'] > t3_max
                if spy_latest_date is not None:
                    write_window_mask &= df_price['date'] <= spy_latest_date
                new_dates = df_price.loc[write_window_mask, 'date']
                if len(new_dates) != 1:
                    fallback_reason = FALLBACK_REASON_MULTI_DAY_GAP
                else:
                    K = max_lookback()
                    # 供給する T3 列はレジストリから機械的に導出する（手書きリスト禁止）。
                    # 5-6d: 全67列ではなく、増分計算の入力として実際に参照される列だけを読む。
                    # 読み出しコストは行数より列数が支配的（実測。§2.3）。
                    # 5-15c（2回目のcode-review指摘3）: WINDOW型列は読まれる前に必ず
                    # calculate_indicators 側で生価格から無条件に上書きされるため、
                    # RECURSIVE型21列のみに絞った（供給しない46列は出力専用または
                    # 読み捨てだったWINDOW型列で、上書き前提のため無害）。
                    ind_cols = list(supplied_column_names())
                    cols_sql = ", ".join(["date"] + ind_cols)
                    hist_query = (
                        f"SELECT {cols_sql} FROM indicators "
                        "WHERE symbol_id = ? AND date <= ? ORDER BY date DESC LIMIT ?"
                    )
                    df_hist = pd.read_sql_query(hist_query, conn, params=(sid, t3_max.isoformat(), K))
                    if len(df_hist) != K:
                        fallback_reason = FALLBACK_REASON_INSUFFICIENT_ROWS
                    else:
                        df_hist = df_hist.iloc[::-1].reset_index(drop=True)
                        df_hist['date'] = pd.to_datetime(df_hist['date']).dt.date

                        # RECURSIVE型列（前日値を継ぐ列）だけがマージ時にそのまま
                        # 供給履歴として使われる（WINDOW型列は raw price から毎回
                        # 上書き計算されるため NaN でも無害。incremental_merge.py 参照）。
                        # SPYの rs_* 列は calc_relative_strength が df_spy=None で
                        # 早期returnするため構造的に常にNULL（増分計算の状態が壊れて
                        # いるわけではない）。除外しないとSPYが毎日必ず全期間計算に
                        # フォールバックし続け、健全な状態でもWARNINGが消えない
                        # （5-15b・code-review指摘2。db_health_check.py と同じ除外を
                        # 共通関数 `is_structurally_null_column` に集約）。
                        rec_cols = [c for c in recursive_column_names()
                                    if not is_structurally_null_column(ticker, c)]
                        notna_per_col = (
                            df_hist[rec_cols].notna().all() if rec_cols else pd.Series(dtype=bool)
                        )
                        if not notna_per_col.all():
                            null_cols = sorted(c for c in rec_cols if not notna_per_col[c])
                            # 「壊れているから NULL」「まだ出ないから NULL（正当）」
                            # 「判別不能」を区別する（5-15b→5-15c。詳細は関数docstring）。
                            # 銘柄の真の履歴長はSQLite（ホットキャッシュ、実測504行）
                            # からは分からないため、絶対位置ではなく増分ウィンドウ
                            # （K本＝df_hist）内だけで観測できる性質だけで判定する。
                            warmup_thresholds = columns_with_warmup_threshold()
                            defect_cols = []
                            undetermined_cols = []
                            warmup_cols = []
                            for col in null_cols:
                                warmup_bars = warmup_thresholds.get(col)  # 5-7e R14: 分岐の外で1回だけ取得
                                notna_series = df_hist[col].notna()
                                if notna_series.any():
                                    first_valid_pos = notna_series.idxmax()
                                    # 単調性の破れ: 一度非NULLになった値が、より新しい
                                    # 行でNULLに戻っている（正常系では起こらない）。
                                    if not notna_series.iloc[first_valid_pos:].all():
                                        defect_cols.append(col)
                                    else:
                                        # 単調ではあるが、ウィンドウ内での最初の非NULL位置
                                        # （first_valid_pos）が warmup_bars を超えている
                                        # 場合、5-7aでは確実な欠陥としていたが、5-7d
                                        # （G3 2周目 R8/R9・案X）で WARNING のみに変更し
                                        # 増分計算は継続する（欠陥扱いにするとSQLite504本
                                        # の全期間計算に回ってしまい、本計画が断ち切ろうと
                                        # した rs_roc_ema_200 のNULL連鎖を再び起こすため。
                                        # また warmup_bars は実測値であり、先頭入力がNaNの
                                        # 銘柄では真の立ち上がりが後ろにずれて誤判定しうる）。
                                        # 等号（first_valid_pos == warmup_bars）は
                                        # ウィンドウが銘柄の真の先頭と一致する場合に
                                        # 起こりうる正常系。
                                        warmup_cols.append(col)
                                        if warmup_bars is not None and first_valid_pos > warmup_bars:
                                            prefix_warnings.append(
                                                f"null_prefix_exceeds_warmup:{col}"
                                                f"(first_valid_pos={first_valid_pos}>warmup_bars={warmup_bars})"
                                            )
                                else:
                                    # ウィンドウ全体がNULL。
                                    if warmup_bars is not None and warmup_bars < K:
                                        # ウィンドウがどこから始まっていても最終行の
                                        # 絶対位置は必ず warmup_bars を超えるため、
                                        # それでも全NULLなら欠陥と断定できる。
                                        defect_cols.append(col)
                                    else:
                                        # warmup_bars が未確定、またはウィンドウ長K以上
                                        # 必要（例: rs_roc_ema_200 は611 > K=400）
                                        # -> 正当なウォームアップ中か欠陥か判別できない。
                                        undetermined_cols.append(col)
                            if defect_cols:
                                fallback_reason = f"{FALLBACK_REASON_NULL_RECURSIVE_COLUMN}:{','.join(defect_cols)}"
                            elif undetermined_cols:
                                fallback_reason = f"{FALLBACK_REASON_WARMUP_UNDETERMINED}:{','.join(undetermined_cols)}"
                            # else: warmup_cols のみ（単調にNULL→非NULLへ移行し、
                            # 最終供給行＝前日に値がある）。これはフォールバック
                            # 理由ではなく増分継続の対象（t3_fallback_lookback_window
                            # 計画 §3.1・案B）。fallback_reason は None のまま
                            # 下の増分計算ブロックへ進む。

                        if fallback_reason is None:
                            new_date = new_dates.iloc[0]
                            price_hist = df_price[df_price['date'] <= t3_max].tail(K)
                            price_new = df_price[df_price['date'] == new_date]
                            if len(price_hist) != K or len(price_new) != 1:
                                fallback_reason = FALLBACK_REASON_INSUFFICIENT_ROWS
                            else:
                                history = pd.merge(price_hist, df_hist, on='date', how='inner')
                                if len(history) != K:
                                    fallback_reason = FALLBACK_REASON_INSUFFICIENT_ROWS
                                else:
                                    # 行 0..K-1 は生価格＋保存済みT3列（実値）、
                                    # 行K（新規計算対象）は生価格のみ
                                    # （concat後にT3列はNaNになる）— 5-4b の入力契約。
                                    df_inc = pd.concat([history, price_new], ignore_index=True, sort=False)
        finally:
            conn.close()

        spy_df_arg = spy_df if ticker != "SPY" else None

        if df_inc is not None:
            df_ind = calculate_indicators(df_inc, spy_df_arg, state=True)
        else:
            df_ind = calculate_indicators(df_price, spy_df_arg)

        if spy_latest_date:
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1))) & (df_ind['date'] <= spy_latest_date)]
        else:
            delta_df = df_ind[(df_ind['date'] > (t3_max if t3_max else date(2000, 1, 1)))]

        if delta_df.empty:
            return ticker, sid, [], None, []

        return ticker, sid, delta_df.to_dict('records'), fallback_reason, prefix_warnings

    except Exception as e:
        return ticker, sid, e, None, []

def _calculate_t3_worker_wrapper(args):
    """Wrapper function to unpack arguments for multiprocessing Pool."""
    return _calculate_t3_worker(*args)

def _log_fallback_summary(logger: logging.Logger, fallback_reasons: List[str]) -> None:
    """フォールバック理由のリストを集計してログ出力する
    （T3増分化計画 5-6b・5-15b・5-15c・5-15dで改訂）。

    5-6 の実データ検証で「フォールバックが無言」（本番で4日間気づかれなかった）
    ことが問題だったため、フェーズ終了時に必ず可視化する。0件のときはログを
    汚さないよう INFO に留める（本計画の前提どおり、フォールバックは
    「T3リフレッシュすべき」例外状態であり、0件が正常状態）。

    5-15b（code-review軽微2件目）: 当初は理由を問わず一律 WARNING かつ
    「書き込まれた値は遡り不足により不正確です」という文言だったが、これは
    `no_saved_rows`（新規上場）のような正常系には当てはまらない
    （全期間計算そのものは正確で、単に保存済み行が無いだけ）。
    `FALLBACK_REASON_NULL_RECURSIVE_COLUMN`（欠陥の可能性）が1件でもあれば
    WARNING で内訳とリフレッシュ推奨を出し、それ以外（新規上場・履歴不足・
    連休明け・判別不能）だけなら想定内として INFO に留める。単調に
    NULL→非NULLへ移行し前日行に値がある列はもはやフォールバック理由では
    ないため（t3_fallback_lookback_window計画 5-3）、ここには現れない。

    5-15c（2回目の code-review 指摘1）: `FALLBACK_REASON_WARMUP_UNDETERMINED`
    （判別不能）を新設した。判別不能は欠陥と確定したわけではないため
    `WARMUP_IN_PROGRESS` と同じ扱い（WARNING には昇格させない）とする。

    5-15d（2回目の code-review 指摘2）: 5-15c 時点でも、欠陥・判別不能のいずれも
    無いケース（`insufficient_saved_rows`/`multi_day_gap`/`warmup_in_progress` のみ）
    では「全期間計算そのものの結果は正確です」と言い切っていたが、これは誤り。
    `state=None`（全期間計算）は `daily_prices`（`t3_max` が None ではない限り、
    その時点で SQLite が保持する**全行**＝実測504本程度）を対象にするだけで、
    Parquet の真の全履歴を読むわけではない（§1.2）。`t3_max` が None ではない
    （＝以前のT3行が存在する＝銘柄がある程度以上の履歴を持つ）ケースでは、
    warmup_bars が SQLite の保持本数を超える列（rs_roc_ema_200等）はNULLにならず
    「もっともらしいが違う値」になりうる（§1.4: 12列が該当）。
    「全期間計算そのものの結果は正確です」と断定してよいのは
    `no_saved_rows`（`t3_max` が None＝新規上場。daily_prices全行がその銘柄の
    真の全履歴と一致する）のケースだけで、他のフォールバック理由は
    「遡り不足の可能性がある」という留保つきの文言にする（5-15bで一度この
    誤りを犯し、5-15cでは判別不能ぶんしか直しておらず、warmup_in_progress等の
    断定は直っていなかった。§7-6参照）。
    """
    if not fallback_reasons:
        logger.info("Phase 3: 全銘柄が増分経路で計算されました（フォールバックなし）。")
        return

    # detail（NULL列名などコロン以降の情報）を落として理由カテゴリだけで集計する。
    reason_counts = Counter(r.split(':', 1)[0] for r in fallback_reasons)
    breakdown = ', '.join(f'{reason}={count}' for reason, count in sorted(reason_counts.items()))
    total = len(fallback_reasons)
    defect_count = reason_counts.get(FALLBACK_REASON_NULL_RECURSIVE_COLUMN, 0)
    undetermined_count = reason_counts.get(FALLBACK_REASON_WARMUP_UNDETERMINED, 0)
    no_saved_rows_count = reason_counts.get(FALLBACK_REASON_NO_SAVED_ROWS, 0)
    # no_saved_rows（新規上場。t3_maxが無く daily_prices の全行がそのまま銘柄の
    # 全履歴）だけが「全期間計算＝全履歴計算」として正確と言い切れる。それ以外
    # （insufficient_saved_rows/multi_day_gap/warmup_undetermined。
    # warmup_in_progressはt3_fallback_lookback_window計画5-3で増分継続の対象に
    # なったため、ここにはもう現れない）は、いずれも state=None の全期間計算が
    # SQLiteの保持本数（実測504本程度）だけを対象にしたものであり、warmup_barsが
    # それを超える列では遡り不足により不正確な可能性がある。
    uncertain_count = total - defect_count - no_saved_rows_count

    if defect_count:
        logger.warning(
            f"Phase 3: 増分計算できず全期間計算にフォールバックした銘柄が {total} 件"
            f"（理由内訳: {breakdown}）。うち {defect_count} 件は RECURSIVE型列が"
            "演算上必要な履歴本数（warmup_bars）を超えているのにNULLでした"
            "（増分計算の状態が壊れている可能性があり、当該銘柄について書き込まれた"
            "値は不正確な可能性があります）。`--rebuild-from T3` によるリフレッシュを"
            "推奨します。"
        )
    elif undetermined_count:
        logger.info(
            f"Phase 3: 増分計算できず全期間計算にフォールバックした銘柄が {total} 件"
            f"（理由内訳: {breakdown}）。うち {undetermined_count} 件は演算上必要な"
            "履歴本数（warmup_bars）が増分ウィンドウ長を超える列（例: rs_roc_ema_200）が"
            "ウィンドウ全体でNULLでした。正当なウォームアップ中か欠陥かはこの検査だけでは"
            "判別できません（`warmup_undetermined`）。それ以外の理由（no_saved_rowsを除く。"
            f"{no_saved_rows_count} 件）も、SQLiteの保持本数だけを使った全期間計算のため、"
            "遡り不足により書き込まれた値が不正確な可能性があります。"
        )
    elif uncertain_count:
        logger.info(
            f"Phase 3: 増分計算できず全期間計算にフォールバックした銘柄が {total} 件"
            f"（理由内訳: {breakdown}）。欠陥（null_recursive_column）はありませんが、"
            f"うち {uncertain_count} 件（insufficient_saved_rows/multi_day_gap）"
            "は SQLite が保持する daily_prices 全行"
            "（実測504本程度）だけを使った全期間計算です。演算上必要な履歴本数が"
            "その保持本数を超える列（rs_roc_ema_200等）では遡り不足により書き込まれた"
            "値が不正確な可能性があります。"
            + (f" 残り {no_saved_rows_count} 件は新規上場（no_saved_rows）で、"
               "価格履歴自体がSQLiteの保持期間に収まるため正確です。"
               if no_saved_rows_count else "")
        )
    else:
        logger.info(
            f"Phase 3: 増分計算できず全期間計算にフォールバックした銘柄が {total} 件"
            f"（理由内訳: {breakdown}）。いずれも新規上場（no_saved_rows）で、"
            "価格履歴自体がSQLiteの保持期間に収まるため全期間計算がそのまま"
            "全履歴計算になり正確です。"
        )

def _log_warnings_summary(logger: logging.Logger, ticker_warnings: List[Tuple[str, str]]) -> None:
    """5-7d（G3 2周目 R8/R9・案X）: `first_valid_pos > warmup_bars` の WARNING を
    フェーズ終了時に1回だけ集計してログ出力する（銘柄ごとに1行ずつは出さない）。

    `ticker_warnings` は `(ticker, warning_str)` のタプルのリスト。`warning_str` は
    `"null_prefix_exceeds_warmup:列名(first_valid_pos=N>warmup_bars=M)"` の形式
    （`_calculate_t3_worker` の `warnings` 要素そのもの）。
    """
    if not ticker_warnings:
        return

    total = len(ticker_warnings)
    tickers = sorted({t for t, _ in ticker_warnings})
    ticker_sample = ', '.join(tickers[:20])
    if len(tickers) > 20:
        ticker_sample += f' 他{len(tickers) - 20}銘柄'
    # 列名部分（"列名(..." の直前まで）だけを抜き出して集計する。
    cols = sorted({w.split(':', 1)[1].split('(', 1)[0] for _, w in ticker_warnings if ':' in w})
    logger.warning(
        f"Phase 3: first_valid_pos が warmup_bars を超えている"
        f"（本来もっと早く値が出ているはずなのに出ていない）列の警告が {total} 件"
        "（該当銘柄は、他の理由で全期間計算にフォールバックしていなければ増分計算を継続）"
        f"（銘柄 {len(tickers)} 件: {ticker_sample}。列: {', '.join(cols)}）。"
        "warmup_bars は実測値のため誤判定の可能性がありますが、実際に増分計算の"
        "状態が壊れている場合はこの銘柄・列を対象に `--rebuild-from T3` による"
        "リフレッシュを推奨します。"
    )


def sync_phase_t3_indicators(db, sheet_data: List[Dict], symbol_id_map: Dict, spy_latest_date: Optional[date], skip_fetch: bool, db_path: str, logger: logging.Logger):
    """Phase 3: Indicators (T3) - Per-ticker catch-up using T2 price data with Parallel Processing."""
    logger.info("--- Phase 3: Indicator calculation START (Parallel) ---")
    if not spy_latest_date: return
    
    from db.models import Symbol, DailyPrice, Indicator

    spy_sym_id = symbol_id_map.get(("SPY", "NYSE" if ("SPY", "NYSE") in symbol_id_map else "AMEX")) or db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    spy_all = db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()
    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "volume": r.volume} for r in spy_all])
    
    p_max_map = {sid: mdt for sid, mdt in db.query(DailyPrice.symbol_id, func.max(DailyPrice.date)).group_by(DailyPrice.symbol_id).all()}
    i_max_map = {sid: mdt for sid, mdt in db.query(Indicator.symbol_id, func.max(Indicator.date)).group_by(Indicator.symbol_id).all()}
    
    tasks_map = {}
    for item in sheet_data:
        ticker, sid = item['ticker'], symbol_id_map.get((item['ticker'], item['exchange']))
        if not sid: continue
        if sid in tasks_map: continue
        
        t2_max, t3_max = p_max_map.get(sid), i_max_map.get(sid)
        if t2_max and (not t3_max or t3_max < t2_max):
            is_virt = (item.get('exchange') == 'VIRTUAL')
            tasks_map[sid] = (sid, ticker, t3_max, is_virt)
    
    tasks = list(tasks_map.values())
    
    if not tasks:
        logger.info("Phase 3: No tickers need indicator update.")
        return

    num_workers = min(4, multiprocessing.cpu_count() // 2)
    if num_workers < 1: num_workers = 1
    logger.info(f"Phase 3: Spawning {num_workers} parallel workers for {len(tasks)} tickers.")
    
    # Prepare arguments for multiprocessing
    pool_args = [(sid, ticker, t3_max, db_path, spy_df, skip_fetch, is_virt, spy_latest_date) for sid, ticker, t3_max, is_virt in tasks]
    
    update_count = 0
    completed = 0
    indicator_cols = [c.name for c in Indicator.__table__.columns if c.name not in ('id', 'symbol_id', 'date')]

    # Use multiprocessing Pool to run in parallel
    pending_recs = []
    chunk_size = 50  # Write to DB every 50 tickers to minimize commit/fsync overhead
    fallback_reasons: List[str] = []  # フォールバックした銘柄の理由（5-6b。フェーズ終了時に集計してログ出力）
    ticker_warnings: List[Tuple[str, str]] = []  # (ticker, warning_str) のリスト（5-7d。フェーズ終了時に集計してログ出力）

    with multiprocessing.Pool(processes=num_workers) as pool:
        results = pool.imap_unordered(_calculate_t3_worker_wrapper, pool_args)

        for res_ticker, res_sid, records, fallback_reason, res_warnings in results:
            try:
                if isinstance(records, Exception):
                    logger.error(f"[{res_ticker}] Worker exception: {records}")
                    continue

                if records:
                    for row in records:
                        kwargs = {'symbol_id': res_sid, 'date': row['date']}
                        for col in indicator_cols:
                            val = row.get(col)
                            if col in ('td9', 'trend_template_ok', 'rs_blue_dot_age', 'rs_red_dot_age'):
                                kwargs[col] = int(val) if val is not None else None
                            else:
                                kwargs[col] = val
                        pending_recs.append(Indicator(**kwargs))
                    update_count += 1
                    # フォールバックで実際に行を書いた場合のみ集計対象にする
                    # （新規に書く日付が0日の縮退ケースは records が空になり不正確な値を
                    # 書いていないため対象外）。
                    if fallback_reason:
                        fallback_reasons.append(fallback_reason)
                    if res_warnings:
                        ticker_warnings.extend((res_ticker, w) for w in res_warnings)

                completed += 1
                if completed % chunk_size == 0:
                    if pending_recs:
                        db.bulk_save_objects(pending_recs)
                        db.commit()
                        db.expunge_all()  # Clear SQLAlchemy identity map to free memory
                        pending_recs.clear()
                    logger.info(f"Phase 3 Progress: {completed}/{len(tasks)}")
                    
            except Exception as e:
                logger.error(f"[{res_ticker}] Parent db insert exception: {str(e)}")
                db.rollback()
                pending_recs.clear()
                
        # Commit any remaining records
        if pending_recs:
            try:
                db.bulk_save_objects(pending_recs)
                db.commit()
                db.expunge_all()
                pending_recs.clear()
            except Exception as e:
                logger.error(f"Failed to commit final batch: {e}")
                db.rollback()

    _log_fallback_summary(logger, fallback_reasons)
    _log_warnings_summary(logger, ticker_warnings)
    logger.info(f"Phase 3 COMPLETE: Updated {update_count} tickers.")
