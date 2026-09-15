import logging
import numpy as np
import pandas as pd

from db.models import Symbol, DailyPrice, Indicator, MarketSignal
from pipeline.utils import sanitize_numeric
from indicators.calculate import calculate_market_signals
from indicators.market_signals import compute_breadth_momentum, SPY_LOOKBACK_MIN_BARS


def _filter_insufficient_lookback_dates(gap_dates: list, spy_df: pd.DataFrame) -> tuple[list, list]:
    """`gap_dates` のうち、その日までの SPY 遡りが `SPY_LOOKBACK_MIN_BARS` に
    満たないものを外す（§7-7・§4-8・5-7c）。

    例外で止めるのはやめ、遡り不足の日付を「書き込み対象から外す」形にした。
    `spy_df` は日付昇順（呼び出し側で `order_by(DailyPrice.date)` 済み）なので、
    日付ごとにフルスキャンせず `np.searchsorted` で本数を数える。

    Returns:
        (kept, excluded) — いずれも `gap_dates` と同じ型（`date`）のリスト。
        昇順を維持する。
    """
    if not gap_dates:
        return [], []
    spy_dates_arr = spy_df['date'].to_numpy(dtype='datetime64[ns]')
    gap_dt_arr = np.array([pd.Timestamp(d) for d in gap_dates], dtype='datetime64[ns]')
    # side='right' → その日付「以前」（当日含む）の本数
    bar_counts = np.searchsorted(spy_dates_arr, gap_dt_arr, side='right')
    kept = [d for d, n in zip(gap_dates, bar_counts) if n >= SPY_LOOKBACK_MIN_BARS]
    excluded = [d for d, n in zip(gap_dates, bar_counts) if n < SPY_LOOKBACK_MIN_BARS]
    return kept, excluded


def sync_phase_t5_signals(db, logger: logging.Logger):
    """Phase 5: Market Signals (T5) - Idempotent catch-up."""
    logger.info("--- Phase 5: Market Signal calculation START ---")
    spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    vix_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "^VIX").scalar()
    vxv_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "^VIX3M").scalar()
    
    t3_dates = {r[0] for r in db.query(Indicator.date).distinct().filter(Indicator.symbol_id == spy_sym_id).all()}
    t5_completed_dates = {r[0] for r in db.query(MarketSignal.date).filter(MarketSignal.market_trend_score.is_not(None)).all()}
    gap_dates = sorted(list(t3_dates - t5_completed_dates))
    
    if not gap_dates:
        logger.info("No gaps or missing scores detected in Phase 5.")
        return

    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "high": r.high, "low": r.low, "volume": r.volume} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()])
    if spy_df.empty:
        logger.error("SPY price data missing.")
        return
    spy_df['date'] = pd.to_datetime(spy_df['date'])

    # 遡り不足ガード（§7-7・§4-8・5-7c）: 例外で止めるのではなく、
    # 遡りが足りない gap 日付を書き込み対象から外し、警告するだけにする。
    #
    # 例外で止める形（5-7b まで）は、ホット期間の古い日付に
    # `market_trend_score` NULL が1件あるだけの正常な修復ケース
    # （SKILL.md が「再実行でバックフィルされる」と案内している挙動）や、
    # `--rebuild-from T2 --category` / `--re-calculate` の全削除経路でも
    # 必ず発火し、毎晩 T5 で落ちて rotate に到達せず Parquet の更新が
    # 止まっていた（§7-7）。5-9 により遡り不足の日付は
    # calculate_market_signals() 側で None（NaN）になるため、この日付を
    # 書き込まなければ偽の値が書かれるリスクも無い。
    gap_dates, excluded_gap_dates = _filter_insufficient_lookback_dates(gap_dates, spy_df)
    if excluded_gap_dates:
        logger.error(
            f"T5: SPY の遡りが {SPY_LOOKBACK_MIN_BARS} 本に満たない日付が "
            f"{len(excluded_gap_dates)} 件あるため、書き込み対象から除外します"
            f"（範囲: {min(excluded_gap_dates)} 〜 {max(excluded_gap_dates)}）。"
            f"残る対象日数は {len(gap_dates)} 件です。SQLite はホット期間"
            "（直近730日程度）しか保持していないため、この状態で全日付を"
            "再計算すると sma_200 の窓の先頭で SPY の遡りが不足します。"
            "正しい値を入れるには `--rebuild-from T5` など Parquet 基点の"
            "再構築手順を使ってください。"
        )
    if not gap_dates:
        logger.error(
            "T5: 書き込み対象の日付が残っていません（全て SPY の遡り不足で除外"
            "されました）。`--rebuild-from T5` など Parquet 基点の再構築手順を"
            "使ってください。"
        )
        return

    vix_df = pd.DataFrame()
    if vix_sym_id:
        vix_df = pd.DataFrame([{"date": r.date, "close": r.close} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == vix_sym_id).order_by(DailyPrice.date).all()])
        if not vix_df.empty:
            vix_df['date'] = pd.to_datetime(vix_df['date'])

    vxv_df = pd.DataFrame()
    if vxv_sym_id:
        vxv_df = pd.DataFrame([{"date": r.date, "close": r.close} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == vxv_sym_id).order_by(DailyPrice.date).all()])
        if not vxv_df.empty:
            vxv_df['date'] = pd.to_datetime(vxv_df['date'])
    
    active_stock_ids = [r[0] for r in db.query(Symbol.id).filter(Symbol.active == 1, Symbol.category == '個別').all()]
    
    if not active_stock_ids:
        logger.warning("No active '個別' stocks found for metrics calculation.")
        metrics_df = pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])
    else:
        logger.info(f"Phase 5: Vectorizing breadth and momentum calculations for {len(gap_dates)} dates...")
        
        # Optimize query by filtering on the earliest gap date (with 10-day buffer for shift(1))
        # to prevent loading the entire 5.8+ million rows history.
        from datetime import timedelta
        min_gap_date = min(gap_dates)
        start_filter_date = min_gap_date - timedelta(days=10)
        start_date_str = start_filter_date.strftime('%Y-%m-%d')
        
        query_metrics = f"""
            SELECT dp.date, dp.symbol_id, dp.close, i.sma_50
            FROM daily_prices dp
            JOIN symbols s ON s.id = dp.symbol_id
            JOIN indicators i ON i.symbol_id = dp.symbol_id AND i.date = dp.date
            WHERE s.active = 1 AND s.category = '個別' AND dp.date >= '{start_date_str}'
            ORDER BY dp.symbol_id, dp.date
        """
        # 自己デッドロック防止: commit してロック解放後、読み取りエンジンで読む
        db.commit()
        from db.database import get_read_engine_for
        raw_df = pd.read_sql(query_metrics, get_read_engine_for(db))

        if not raw_df.empty:
            raw_df['date'] = pd.to_datetime(raw_df['date'])
            metrics_df = compute_breadth_momentum(raw_df)
        else:
            metrics_df = pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])

    ms_df = calculate_market_signals(spy_df, vix_df, vxv_df, metrics_df)
    
    gap_dt = [pd.to_datetime(d) for d in gap_dates]
    ms_df = ms_df[ms_df['date'].isin(gap_dt)]
    
    if ms_df.empty:
        logger.info("No signals found for the target gap dates.")
        return

    # Build a date-indexed lookup for breadth_sma50
    breadth_by_date = {}
    if not metrics_df.empty and 'breadth_sma50' in metrics_df.columns:
        for _, r in metrics_df.iterrows():
            d = r['date']
            d_key = d.date() if hasattr(d, 'date') else d
            breadth_by_date[d_key] = float(r['breadth_sma50'])

    t5_recs = []
    for _, row in ms_df.iterrows():
        row_date = row['date'].date()
        t5_recs.append(MarketSignal(
            date=row_date,
            # spy_above_sma200 / distribution_days は SPY の遡りが足りない先頭区間で
            # None（判定不能）になりうる（indicators/market_signals.py 参照）。
            # spy_sma200_rising と同じ sanitize_numeric によるガードを掛ける。
            spy_above_sma200=int(row['spy_above_sma200']) if sanitize_numeric(row, 'spy_above_sma200') is not None else None,
            spy_sma200_rising=int(row['spy_sma200_rising']) if sanitize_numeric(row, 'spy_sma200_rising') is not None else None,
            distribution_days=int(row['distribution_days']) if sanitize_numeric(row, 'distribution_days') is not None else None,
            is_distribution_day=int(row['is_distribution_day']),
            follow_through_day=int(row['follow_through_day']),
            market_phase=row['market_phase'],
            market_trend_score=float(row['market_trend_score']) if sanitize_numeric(row, 'market_trend_score') is not None else None,
            vxv_vix_ratio=float(row['vxv_vix_ratio']) if sanitize_numeric(row, 'vxv_vix_ratio') is not None else None,
            breadth_sma50=breadth_by_date.get(row_date)
        ))
    
    db.query(MarketSignal).filter(MarketSignal.date.in_([r.date for r in t5_recs])).delete(synchronize_session=False)
    db.bulk_save_objects(t5_recs)
    db.commit()
    logger.info(f"Phase 5 COMPLETE: Saved {len(t5_recs)} signal records.")
