import logging
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import func, text

from db.models import RelativeRank, Indicator

def sync_phase_t4_ranks(db, spy_latest_date: Optional[date], logger: logging.Logger):
    """Phase 4: Relative Ranks (T4) - Idempotent catch-up."""
    logger.info("--- Phase 4: Relative Rank calculation START ---")
    
    t3_max = db.query(func.max(Indicator.date)).scalar()
    t4_max = db.query(func.max(RelativeRank.date)).scalar()
    
    if not t3_max: 
        return
        
    # t4_max が存在する場合でも、フライングデータ（為替など）により
    # 他の一般銘柄のデータが揃う前に t4_max が進んでしまう問題を回避するため、
    # 常に 7 日前まで遡って再計算を行う。
    start_date = (t4_max - timedelta(days=7)) if t4_max else date(2000, 1, 1)
    
    # SPY最新日 (spy_latest_date) を上限として計算対象の日付を制限する
    query = db.query(Indicator.date).distinct().filter(Indicator.date >= start_date)
    if spy_latest_date:
        query = query.filter(Indicator.date <= spy_latest_date)
    gap_dates = [r[0] for r in query.order_by(Indicator.date).all()]
    if not gap_dates: 
        return
        
    total_dates = len(gap_dates)
    logger.info(f"Phase 4: Processing relative ranks for {total_dates} dates.")
    
    indicators_to_rank = [
        'relative_strength_spy', 
        'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63', 
        'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63', 
        'rs_condition_14', 'rs_condition_21', 'rs_condition_63',
        'rs_roc_ema_14', 'rs_roc_ema_21', 'rs_roc_ema_63'
    ]
    
    # Optimize for massive DML on SATA HDD:
    db.execute(text("PRAGMA cache_size = -4000000;"))
    db.execute(text("PRAGMA synchronous = OFF;"))
    db.execute(text("PRAGMA temp_store = MEMORY;"))
    
    for i, d in enumerate(gap_dates):
        if i % 10 == 0 or i == total_dates - 1:
            logger.info(f"Phase 4 Progress: {i+1}/{total_dates} (Date: {d})")
            
        db.query(RelativeRank).filter(RelativeRank.date == d).delete()
        for ind_col in indicators_to_rank:
            query = f"""
                INSERT INTO relative_ranks (symbol_id, date, group_name, indicator_name, percent_rank)
                SELECT
                    i.symbol_id,
                    i.date,
                    s.category as group_name,
                    '{ind_col}' as indicator_name,
                    PERCENT_RANK() OVER(
                        PARTITION BY s.category 
                        ORDER BY i.{ind_col} ASC
                    ) as percent_rank
                FROM indicators i
                JOIN symbols s ON i.symbol_id = s.id
                WHERE i.date = :d
                  AND i.{ind_col} IS NOT NULL
            """
            db.execute(text(query), {"d": d})
        db.commit()
        
    logger.info("Phase 4 COMPLETE.")
