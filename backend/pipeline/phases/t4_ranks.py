import logging
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import func, text

from db.models import RelativeRank, Indicator

def sync_phase_t4_ranks(db, spy_latest_date: Optional[date], logger: logging.Logger, default_start_date: Optional[date] = None):
    """Phase 4: Relative Ranks (T4) - Idempotent catch-up."""
    logger.info("--- Phase 4: Relative Rank calculation START ---")
    
    t3_max = db.query(func.max(Indicator.date)).scalar()
    t4_max = db.query(func.max(RelativeRank.date)).scalar()
    
    if not t3_max: 
        return
        
    min_allowed_date = default_start_date if default_start_date else date(2018, 4, 1)
    
    # t4_max が存在する場合でも、フライングデータ（為替など）により
    # 他の一般銘柄 of データが揃う前に t4_max が進んでしまう問題を回避するため、
    # 常に 7 日前まで遡って再計算を行う。
    start_date = (t4_max - timedelta(days=7)) if t4_max else min_allowed_date
    if start_date < min_allowed_date:
        start_date = min_allowed_date
    
    # SPY最新日 (spy_latest_date) を上限として計算対象の日付を制限する
    query = db.query(Indicator.date).distinct().filter(Indicator.date >= start_date)
    if spy_latest_date:
        query = query.filter(Indicator.date <= spy_latest_date)
    gap_dates = [r[0] for r in query.order_by(Indicator.date).all()]
    if not gap_dates: 
        return
        
    total_dates = len(gap_dates)
    logger.info(f"Phase 4: Processing relative ranks for {total_dates} dates.")
    
    # indicators テーブルの計算元カラム → relative_ranks テーブルのランクカラムのマッピング
    # (indicator_col, rank_col) の順
    indicators_to_rank = [
        ('rs_value',       'rs_value_rank'),
        ('rs_ratio_e5',    'rs_ratio_rank_e5'),
        ('rs_ratio_e14',   'rs_ratio_rank_e14'),
        ('rs_ratio_e21',   'rs_ratio_rank_e21'),
        ('rs_ratio_e63',   'rs_ratio_rank_e63'),
        ('rs_ratio_e200',  'rs_ratio_rank_e200'),
        ('rs_momentum_e5', 'rs_momentum_rank_e5'),
        ('rs_momentum_e14','rs_momentum_rank_e14'),
        ('rs_momentum_e21','rs_momentum_rank_e21'),
        ('rs_momentum_e63','rs_momentum_rank_e63'),
        ('rs_momentum_e200','rs_momentum_rank_e200'),
        ('rs_trend_s5',    'rs_trend_rank_s5'),
        ('rs_trend_s14',   'rs_trend_rank_s14'),
        ('rs_trend_s21',   'rs_trend_rank_s21'),
        ('rs_trend_s63',   'rs_trend_rank_s63'),
        ('rs_trend_s200',  'rs_trend_rank_s200'),
        ('rs_roc_ema_5',   'rs_roc_ema_rank_e5'),
        ('rs_roc_ema_14',  'rs_roc_ema_rank_e14'),
        ('rs_roc_ema_21',  'rs_roc_ema_rank_e21'),
        ('rs_roc_ema_63',  'rs_roc_ema_rank_e63'),
        ('rs_roc_ema_200', 'rs_roc_ema_rank_e200'),
        ('rs_macd_hist_21','rs_macd_hist_rank_21'),
    ]
    
    # Optimize for massive DML on SATA HDD:
    # NOTE: `PRAGMA synchronous` はトランザクション内で変更できない
    # ("Safety level may not be changed inside a transaction")。
    # write セッションは BEGIN IMMEDIATE で autobegin するためここでは設定せず、
    # 接続時の synchronous=NORMAL（WAL では fsync は checkpoint 時のみで十分軽い）のまま実行する。
    db.execute(text("PRAGMA cache_size = -524288;"))
    db.execute(text("PRAGMA temp_store = MEMORY;"))
    
    # Build single query to insert all indicators at once (wide format)
    rank_sql_parts = []
    for ind_col, rank_col in indicators_to_rank:
        rank_sql_parts.append(f"PERCENT_RANK() OVER(PARTITION BY s.category ORDER BY i.{ind_col} ASC) AS {rank_col}")
    
    rank_cols_joined = ", ".join(r for _, r in indicators_to_rank)
    ranks_joined = ", ".join(rank_sql_parts)

    
    query_template = f"""
        INSERT INTO relative_ranks (
            symbol_id, date, group_name,
            {rank_cols_joined}
        )
        SELECT
            i.symbol_id,
            i.date,
            s.category as group_name,
            {ranks_joined}
        FROM indicators i
        JOIN symbols s ON i.symbol_id = s.id
        WHERE i.date = :d AND s.category != 'レバレッジ'
    """
    
    for i, d in enumerate(gap_dates):
        if i % 10 == 0 or i == total_dates - 1:
            logger.info(f"Phase 4 Progress: {i+1}/{total_dates} (Date: {d})")
            
        db.query(RelativeRank).filter(RelativeRank.date == d).delete()
        db.execute(text(query_template), {"d": d})
        
        # Commit every 100 dates to reduce fsync write overhead
        if (i + 1) % 100 == 0:
            db.commit()
            
    db.commit()
    logger.info("Phase 4 COMPLETE.")
