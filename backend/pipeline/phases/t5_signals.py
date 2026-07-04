import logging
import pandas as pd
from typing import Optional
from sqlalchemy import func

from db.models import Symbol, DailyPrice, Indicator, MarketSignal
from pipeline.utils import sanitize_numeric
from indicators.calculate import calculate_market_signals

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
        
        query_metrics = """
            SELECT dp.date, dp.symbol_id, dp.close, i.sma_50
            FROM daily_prices dp
            JOIN symbols s ON s.id = dp.symbol_id
            JOIN indicators i ON i.symbol_id = dp.symbol_id AND i.date = dp.date
            WHERE s.active = 1 AND s.category = '個別'
            ORDER BY dp.symbol_id, dp.date
        """
        # 自己デッドロック防止: commit してロック解放後、読み取りエンジンで読む
        db.commit()
        from db.database import get_read_engine_for
        raw_df = pd.read_sql(query_metrics, get_read_engine_for(db))
        
        if not raw_df.empty:
            raw_df['date'] = pd.to_datetime(raw_df['date'])
            raw_df['prev_close'] = raw_df.groupby('symbol_id')['close'].shift(1)
            raw_df['is_up'] = raw_df['close'] > raw_df['prev_close']
            
            raw_df['is_above_sma50'] = raw_df['close'] > raw_df['sma_50']
            
            metrics_df = raw_df.groupby('date').agg(
                breadth_sma50=('is_above_sma50', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5),
                momentum_ratio=('is_up', lambda x: x.mean(skipna=True) if not x.isna().all() else 0.5)
            ).reset_index()
            metrics_df = metrics_df.fillna(0.5)
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
            spy_above_sma200=int(row['spy_above_sma200']),
            spy_sma200_rising=int(row['spy_sma200_rising']) if sanitize_numeric(row, 'spy_sma200_rising') is not None else None,
            distribution_days=int(row['distribution_days']),
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
