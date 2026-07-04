"""ダッシュボード・テーマ・グループ詳細画面の共通アイテムビルダー（audit D-1 で routers.py から分割）。

None 安全な数値変換を行い、フロントエンドのレンダリングエラーを防止する。
"""
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import timedelta
import logging
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from api import schemas

logger = logging.getLogger(__name__)

def _get_sparkline_data(db: Session, sym_id: int, target_date: str, period: int = 21):
    col_attr = getattr(RelativeRank, f"rs_ratio_rank_e{period}", None)
    if col_attr is None:
        return [0.5] * 5
    ranks = db.query(col_attr).filter(
        RelativeRank.symbol_id == sym_id,
        col_attr.isnot(None),
        RelativeRank.date <= target_date
    ).order_by(desc(RelativeRank.date)).limit(30).all()
    
    vals = [r[0] for r in reversed(ranks)]
    if not vals:
        return [0.5] * 5
    return vals

def _build_panel_item(db: Session, sym: Symbol, dp: DailyPrice, rank_val_21: float, rank_val_63: float, target_date: str, 
                     rank_val_14: float = 0.0, rank_val_mom: float = 0.0, rank_val_mom63: float = 0.0,
                     rank_val_trend_14: float = 0.0, rank_val_trend_21: float = 0.0, rank_val_trend_63: float = 0.0):
    sparkline = _get_sparkline_data(db, sym.id, target_date)
    
    history = db.query(DailyPrice.close).filter(
        DailyPrice.symbol_id == sym.id,
        DailyPrice.date <= target_date
    ).order_by(desc(DailyPrice.date)).limit(22).all()
    
    hist_closes = [h[0] for h in history]
    
    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
        
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
        
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100
    elif len(hist_closes) > 1 and hist_closes[-1] > 0:
        change_1m_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100
        
    ind = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date == dp.date
    ).first()

    if ind:
        if ind.change_1d_pct is not None: change_1d_pct = ind.change_1d_pct
        if ind.change_1w_pct is not None: change_1w_pct = ind.change_1w_pct
        if ind.change_1m_pct is not None: change_1m_pct = ind.change_1m_pct

    dist_21ema_pct = 0.0
    if ind and ind.ema_21 and ind.ema_21 > 0:
        dist_21ema_pct = ((dp.close - ind.ema_21) / ind.ema_21) * 100

    return schemas.DashboardPanelItem(
        id=sym.id,
        ticker=sym.ticker,
        name=sym.name,
        category=sym.category,
        close=dp.close,
        change_pct=change_1d_pct,
        change_1w_pct=change_1w_pct,
        change_1m_pct=change_1m_pct,
        dist_21ema_pct=dist_21ema_pct,
        sparkline=sparkline if sparkline else [],
        intensity_score=float(rank_val_21 or 0.0),
        rs_ratio_rank_e21=float(rank_val_21 or 0.0),
        rs_ratio_rank_e63=float(rank_val_63 or 0.0),
        rs_ratio_rank_e14=float(rank_val_14 or 0.0),
        rs_momentum_rank_e21=float(rank_val_mom or 0.0),
        rs_momentum_rank_e63=float(rank_val_mom63 or 0.0),
        rs_trend_rank_s14=float(rank_val_trend_14 or 0.0),
        rs_trend_rank_s21=float(rank_val_trend_21 or 0.0),
        rs_trend_rank_s63=float(rank_val_trend_63 or 0.0),
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs_momentum_e21=ind.rs_momentum_e21 if ind else None,
        
        # Legacy fields for frontend compatibility
        rs_ratio_21_rank=float(rank_val_21 or 0.0),
        rs_ratio_63_rank=float(rank_val_63 or 0.0),
        rs_ratio_14_rank=float(rank_val_14 or 0.0),
        rs_momentum_21_rank=float(rank_val_mom or 0.0),
        rs_momentum_63_rank=float(rank_val_mom63 or 0.0),
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rs_momentum_21=ind.rs_momentum_e21 if ind else None
    )

def _build_leading_item(db: Session, sym: Symbol, dp: DailyPrice, target_date: str):
    history = db.query(DailyPrice.close).filter(
        DailyPrice.symbol_id == sym.id,
        DailyPrice.date <= target_date
    ).order_by(desc(DailyPrice.date)).limit(22).all()
    
    hist_closes = [h[0] for h in history]
    sparkline_raw = list(reversed(hist_closes))
    
    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100
        
    ind = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date == target_date
    ).first()
    dist_21ema_pct = 0.0
    if ind and ind.ema_21 and ind.ema_21 > 0:
        dist_21ema_pct = ((dp.close - ind.ema_21) / ind.ema_21) * 100
        
    return schemas.LeadingIndicatorItem(
        id=sym.id,
        ticker=sym.ticker,
        name=sym.name,
        category=sym.category,
        close=float(dp.close or 0.0),
        change_1d_pct=float(change_1d_pct or 0.0),
        change_1w_pct=float(change_1w_pct or 0.0),
        change_1m_pct=float(change_1m_pct or 0.0),
        dist_21ema_pct=float(dist_21ema_pct or 0.0),
        sparkline=sparkline_raw if sparkline_raw else []
    )

def _build_etf_feature(db: Session, sym: Symbol, dp: DailyPrice, target_date: str):
    history = db.query(DailyPrice).filter(
        DailyPrice.symbol_id == sym.id,
        DailyPrice.date <= target_date
    ).order_by(desc(DailyPrice.date)).limit(252).all()
    
    hist_closes = [h.close for h in history]
    
    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    change_1y_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100
    if len(hist_closes) >= 252 and hist_closes[251] > 0:
        change_1y_pct = ((dp.close - hist_closes[251]) / hist_closes[251]) * 100
        
    ind = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date == target_date
    ).first()
    
    dist_sma5_pct = 0.0
    dist_sma21_pct = 0.0
    dist_sma63_pct = 0.0
    sma21_sma63_pct = 0.0
    
    if ind:
        if ind.sma_5 and ind.sma_5 > 0:
            dist_sma5_pct = ((dp.close - ind.sma_5) / ind.sma_5) * 100
        if ind.sma_21 and ind.sma_21 > 0:
            dist_sma21_pct = ((dp.close - ind.sma_21) / ind.sma_21) * 100
        if ind.sma_63 and ind.sma_63 > 0:
            dist_sma63_pct = ((dp.close - ind.sma_63) / ind.sma_63) * 100
        if ind.sma_21 and ind.sma_63 and ind.sma_63 > 0:
            sma21_sma63_pct = ((ind.sma_21 - ind.sma_63) / ind.sma_63) * 100
            
    rs14_spark = _get_sparkline_data(db, sym.id, target_date, 14)
    rs21_spark = _get_sparkline_data(db, sym.id, target_date, 21)
    rs63_spark = _get_sparkline_data(db, sym.id, target_date, 63)

    # Latest Ranks
    def get_rank(sym_id, ind_name, t_date):
        col_attr = getattr(RelativeRank, ind_name, None)
        if col_attr is None:
            return None
        r = db.query(col_attr).filter(
            RelativeRank.symbol_id == sym_id,
            RelativeRank.date == t_date
        ).first()
        return r[0] if r else None

    # Mini chart (6 months = 126 days)
    six_m_hist = list(reversed(history[:126]))
    start_date = six_m_hist[0].date
    end_date = six_m_hist[-1].date
    
    theme_inds = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date >= start_date,
        Indicator.date <= end_date
    ).all()
    theme_ind_dict = {i.date.strftime('%Y-%m-%d'): i for i in theme_inds}
    
    chart_data = []
    for h in six_m_hist:
        ds = h.date.strftime('%Y-%m-%d')
        i = theme_ind_dict.get(ds)
        chart_data.append(schemas.ChartDataPoint(
            time=ds,
            open=h.open or 0.0,
            high=h.high or 0.0,
            low=h.low or 0.0,
            close=h.close,
            volume=int(round(h.volume)) if h.volume else 0,
            sma_63=i.sma_63 if i else None,
            sma_200=i.sma_200 if i else None,
            rs_value=i.rs_value if i else None,
            rs_value_e5=i.rs_value_e5 if i else None,
            rs_value_e14=i.rs_value_e14 if i else None,
            rs_value_e21=i.rs_value_e21 if i else None,
            rs_value_e63=i.rs_value_e63 if i else None,
            rs_ratio_e14=i.rs_ratio_e14 if i else None,
            rs_ratio_e21=i.rs_ratio_e21 if i else None,
            rs_ratio_e63=i.rs_ratio_e63 if i else None,
            rs_momentum_e14=i.rs_momentum_e14 if i else None,
            rs_momentum_e21=i.rs_momentum_e21 if i else None,
            rs_momentum_e63=i.rs_momentum_e63 if i else None,
            rs_trend_s14=i.rs_trend_s14 if i else None,
            rs_trend_s21=i.rs_trend_s21 if i else None,
            rs_trend_s63=i.rs_trend_s63 if i else None,
        ))
    
    return schemas.EtfFeatureItem(
        id=sym.id, ticker=sym.ticker, name=sym.name, close=float(dp.close or 0.0),
        change_1d_pct=float(change_1d_pct or 0.0), change_1w_pct=float(change_1w_pct or 0.0),
        change_1m_pct=float(change_1m_pct or 0.0), change_1y_pct=float(change_1y_pct or 0.0),
        dist_sma5_pct=float(dist_sma5_pct or 0.0), dist_sma21_pct=float(dist_sma21_pct or 0.0),
        dist_sma63_pct=float(dist_sma63_pct or 0.0), sma21_sma63_pct=float(sma21_sma63_pct or 0.0),
        rs_ratio_e14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs14_sparkline=rs14_spark,
        rs21_sparkline=rs21_spark,
        rs63_sparkline=rs63_spark,
        rs_ratio_rank_e14=get_rank(sym.id, 'rs_ratio_rank_e14', target_date),
        rs_ratio_rank_e21=get_rank(sym.id, 'rs_ratio_rank_e21', target_date),
        rs_ratio_rank_e63=get_rank(sym.id, 'rs_ratio_rank_e63', target_date),
        chart_data=chart_data,
        
        # Legacy fields for frontend compatibility
        rs_ratio_14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rank_rs_ratio_14=get_rank(sym.id, 'rs_ratio_rank_e14', target_date),
        rank_rs_ratio_21=get_rank(sym.id, 'rs_ratio_rank_e21', target_date),
        rank_rs_ratio_63=get_rank(sym.id, 'rs_ratio_rank_e63', target_date)
    )

