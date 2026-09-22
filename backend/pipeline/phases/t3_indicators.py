import time
import logging
import multiprocessing
from collections import Counter
from typing import Dict, List, Optional
from datetime import date
from sqlalchemy import func
import pandas as pd

from pipeline.utils import sanitize_numeric
from db.database import init_db, get_db

# フォールバック理由の識別子（T3増分化計画 5-6b・§3.5）。
#
# `_calculate_t3_worker` が state=None（全期間計算）にフォールバックしたとき、
# どの条件で落ちたかを呼び出し側（`sync_phase_t3_indicators`）へ返すための識別子。
# NULL列が理由の場合は `"{識別子}:列名,列名"` の形式で列名も付与する（集計時は
# ':' より前のカテゴリ部分だけを見て件数を数える。5-6b の実データ検証で
# 「フォールバックが無言」（誰も気づかないまま4日間不正確な値が書かれ続けた）
# ことが問題だったため、理由を可視化することが本設計の要点）。
FALLBACK_REASON_NO_SAVED_ROWS = 'no_saved_rows'                    # 保存済みT3行が無い（新規上場・オンボード直後）
FALLBACK_REASON_INSUFFICIENT_ROWS = 'insufficient_saved_rows'      # 保存済み行数がK（max_lookback()）に満たない
FALLBACK_REASON_MULTI_DAY_GAP = 'multi_day_gap'                    # 新規に書く日付が2日ぶん以上ある（または0日）
FALLBACK_REASON_NULL_RECURSIVE_COLUMN = 'null_recursive_column'    # RECURSIVE型列の供給履歴にNaNがある

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
      - RECURSIVE型列（前日値を継ぐ列）の供給履歴に NaN がある

    戻り値は `(ticker, sid, records, fallback_reason)` の4要素タプル
    （5-6b で `fallback_reason` を追加。増分経路が使われた場合は None、
    フォールバックした場合は上記 `FALLBACK_REASON_*` のいずれか）。
    例外発生時は `(ticker, sid, exception, None)`。
    """
    try:
        import sqlite3
        import pandas as pd
        from datetime import date
        from indicators.calculate import calculate_indicators
        from indicators.incremental_state_registry import (
            INDICATOR_COLUMN_REGISTRY, max_lookback, recursive_column_names,
        )

        conn = sqlite3.connect(db_path, timeout=60.0)
        try:
            query = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE symbol_id = ? ORDER BY date"
            df_price = pd.read_sql_query(query, conn, params=(sid,))

            if df_price.empty:
                return ticker, sid, [], None

            # sqlite3 returns date as string, parse to date object
            df_price['date'] = pd.to_datetime(df_price['date']).dt.date

            # 増分経路が使えるか判定する（§3.5 のフォールバック条件）。
            # 生価格は必ず daily_prices から読む（indicators 側は object dtype 汚染の
            # 実績があるため使わない。§3 注意点3）。
            # 不変条件: df_inc が None のときのみ fallback_reason を設定する
            # （増分経路が成立したら fallback_reason は必ず None のまま）。
            df_inc = None
            fallback_reason = None
            if t3_max is None:
                fallback_reason = FALLBACK_REASON_NO_SAVED_ROWS
            else:
                new_dates = df_price.loc[df_price['date'] > t3_max, 'date']
                if len(new_dates) != 1:
                    fallback_reason = FALLBACK_REASON_MULTI_DAY_GAP
                else:
                    K = max_lookback()
                    # 供給する T3 列はレジストリから機械的に導出する（手書きリスト禁止）。
                    ind_cols = sorted(INDICATOR_COLUMN_REGISTRY.keys())
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
                        rec_cols = list(recursive_column_names())
                        notna_per_col = df_hist[rec_cols].notna().all()
                        if not notna_per_col.all():
                            null_cols = sorted(c for c in rec_cols if not notna_per_col[c])
                            fallback_reason = f"{FALLBACK_REASON_NULL_RECURSIVE_COLUMN}:{','.join(null_cols)}"
                        else:
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
            return ticker, sid, [], None

        return ticker, sid, delta_df.to_dict('records'), fallback_reason

    except Exception as e:
        return ticker, sid, e, None

def _calculate_t3_worker_wrapper(args):
    """Wrapper function to unpack arguments for multiprocessing Pool."""
    return _calculate_t3_worker(*args)

def _log_fallback_summary(logger: logging.Logger, fallback_reasons: List[str]) -> None:
    """フォールバック理由のリストを集計してログ出力する（T3増分化計画 5-6b）。

    5-6 の実データ検証で「フォールバックが無言」（本番で4日間気づかれなかった）
    ことが問題だったため、フェーズ終了時に必ず可視化する。0件のときはログを
    汚さないよう INFO に留める（本計画の前提どおり、フォールバックは
    「T3リフレッシュすべき」例外状態であり、0件が正常状態）。
    """
    if not fallback_reasons:
        logger.info("Phase 3: 全銘柄が増分経路で計算されました（フォールバックなし）。")
        return

    # detail（NULL列名などコロン以降の情報）を落として理由カテゴリだけで集計する。
    reason_counts = Counter(r.split(':', 1)[0] for r in fallback_reasons)
    breakdown = ', '.join(f'{reason}={count}' for reason, count in sorted(reason_counts.items()))
    logger.warning(
        f"Phase 3: 増分計算できず全期間計算にフォールバックした銘柄が {len(fallback_reasons)} 件"
        f"（理由内訳: {breakdown}）。書き込まれた値は遡り不足により不正確です。"
        "`--rebuild-from T3` によるリフレッシュを推奨します。"
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

    with multiprocessing.Pool(processes=num_workers) as pool:
        results = pool.imap_unordered(_calculate_t3_worker_wrapper, pool_args)

        for res_ticker, res_sid, records, fallback_reason in results:
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
    logger.info(f"Phase 3 COMPLETE: Updated {update_count} tickers.")
