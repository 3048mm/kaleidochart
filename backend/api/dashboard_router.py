"""Dashboard API router: /ranking, /available_dates, /dashboard, /theme, /group_data (audit D-1 で分割)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, select
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, re, logging
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning
from api import schemas
from api.deps import get_api_db, get_api_user_db

logger = logging.getLogger(__name__)
router = APIRouter()

from api.panel_builders import (
    _get_sparkline_data, _build_panel_item, _build_leading_item, _build_etf_feature,
    build_panel_preload, preload_sparklines, preload_constituent_details,
)

@router.get("/ranking", response_model=List[schemas.RankingResponse])
def get_rankings(db: Session = Depends(get_api_db), limit: int = 20, asc: bool = False):
    """
    T4: Get the latest rankings across groups.
    By default gets the highest percent rank (descending). Set asc=True to get lowest.
    """
    # 1. We only want the latest date available in T4
    latest_date_row = db.query(RelativeRank.date).order_by(desc(RelativeRank.date)).first()
    if not latest_date_row:
        return []
        
    latest_date = latest_date_row.date
    
    # We return grouped by indicator
    rank_indicators = [
        'rs_value_rank',
        'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63',
        'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63',
        'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63',
        'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63',
    ]
    
    resp = []
    for ind_name in rank_indicators:
        col_attr = getattr(RelativeRank, ind_name, None)
        if col_attr is None:
            continue
            
        order_col = col_attr.asc() if asc else col_attr.desc()
        
        # Join with Symbol to get ticker names
        results = db.query(RelativeRank, Symbol).join(
            Symbol, RelativeRank.symbol_id == Symbol.id
        ).filter(
            RelativeRank.date == latest_date,
            col_attr.isnot(None)
        ).order_by(order_col).limit(limit).all()
        
        items = []
        for rank_row, sym_row in results:
            items.append(schemas.RankingItem(
                symbol_id=sym_row.id,
                ticker=sym_row.ticker,
                name=sym_row.name,
                group_name=rank_row.group_name,
                indicator_name=ind_name,
                percent_rank=getattr(rank_row, ind_name),
                date=str(rank_row.date)
            ))
        
        if items:
            resp.append(schemas.RankingResponse(
                indicator_name=ind_name,
                items=items
            ))
            
    return resp



@router.get("/available_dates", response_model=schemas.AvailableDatesResponse)
def get_available_dates(db: Session = Depends(get_api_db)):
    """
    Get all unique dates available for the dashboard from MarketSignal table.
    Sorted descending (latest first).
    """
    rows = db.query(MarketSignal.date).order_by(desc(MarketSignal.date)).distinct().all()
    # MarketSignal.date is a date object, convert to string
    date_strings = [str(r[0]) for r in rows]
    return schemas.AvailableDatesResponse(dates=date_strings)

@router.get("/dashboard", response_model=schemas.DashboardResponse)
def get_dashboard(
    date: Optional[str] = Query(None, description="ISO Format Date YYYY-MM-DD"),
    rank_type: str = Query("rs_trend", pattern="^(rs_trend|rs_ratio)$", description="Ranking sort type: rs_trend (default) or rs_ratio"),
    db: Session = Depends(get_api_db)
):
    """
    Get aggregated dashboard data for a specific date (defaults to latest).
    """
    # 1. Determine target date
    if date:
        target_date = date
    else:
        latest = db.query(func.max(MarketSignal.date)).scalar()
        if not latest:
            raise HTTPException(status_code=404, detail="No Market Data available")
        target_date = str(latest)

    # 2. Get T5 Market Signal
    signal = db.query(MarketSignal).filter(MarketSignal.date == target_date).first()
    if not signal:
        # Fallback to closest previous date if not exact
        signal = db.query(MarketSignal).filter(MarketSignal.date <= target_date).order_by(desc(MarketSignal.date)).first()
        if not signal:
            raise HTTPException(status_code=404, detail="No Market Signal found")
        target_date = str(signal.date)

    # 2.1 Get Trend Score History
    history_signals = db.query(MarketSignal).filter(
        MarketSignal.date <= target_date
    ).order_by(desc(MarketSignal.date)).limit(100).all()
    
    trend_history = [
        schemas.MarketTrendScoreHistoryItem(
            date=str(s.date), 
            score=s.market_trend_score or 0.0,
            vxv_vix_ratio=s.vxv_vix_ratio,
            distribution_days=s.distribution_days,
            is_distribution_day=s.is_distribution_day,
            follow_through_day=s.follow_through_day
        )
        for s in reversed(history_signals)
    ]

    resp = schemas.DashboardResponse(
        date=target_date,
        market_phase=signal.market_phase,
        distribution_days=signal.distribution_days or 0,
        market_trend_score=signal.market_trend_score or 0.0,
        vxv_vix_ratio=signal.vxv_vix_ratio,
        trend_score_history=trend_history,
        leading=[],
        indices=[],
        sectors=[],
        themes_top=[],
        themes_bottom=[]
    )

    # --- Dashboard Data fetching logic follows ---

    # 3. Get Indices, Sectors, Themes
    # ダッシュボードに表示するカテゴリのみ取得する（個別株 約2,800件を含む全銘柄を
    # ループして panel item を作ってから捨てると 9,000 クエリ超になる）
    DASHBOARD_CATEGORIES = ["市場", "指標", "セクタ", "テーマ"]
    symbols = db.query(Symbol).filter(
        Symbol.active == 1,
        Symbol.category.in_(DASHBOARD_CATEGORIES)
    ).all()
    sym_dict = {s.id: s for s in symbols}
    
    # Get Prices for target date
    prices = db.query(DailyPrice).filter(DailyPrice.date == target_date).all()
    price_dict = {p.symbol_id: p for p in prices}

    # Get Relative Ranks for target date (highly optimized single query!)
    ranks = db.query(
        RelativeRank.symbol_id, 
        RelativeRank.rs_ratio_rank_e14, 
        RelativeRank.rs_ratio_rank_e21, 
        RelativeRank.rs_ratio_rank_e63,
        RelativeRank.rs_trend_rank_s14,
        RelativeRank.rs_trend_rank_s21,
        RelativeRank.rs_trend_rank_s63
    ).filter(
        RelativeRank.date == target_date
    ).all()
    rank_14_dict = {r.symbol_id: r.rs_ratio_rank_e14 for r in ranks if r.rs_ratio_rank_e14 is not None}
    rank_21_dict = {r.symbol_id: r.rs_ratio_rank_e21 for r in ranks if r.rs_ratio_rank_e21 is not None}
    rank_63_dict = {r.symbol_id: r.rs_ratio_rank_e63 for r in ranks if r.rs_ratio_rank_e63 is not None}
    trend_rank_14_dict = {r.symbol_id: r.rs_trend_rank_s14 for r in ranks if r.rs_trend_rank_s14 is not None}
    trend_rank_21_dict = {r.symbol_id: r.rs_trend_rank_s21 for r in ranks if r.rs_trend_rank_s21 is not None}
    trend_rank_63_dict = {r.symbol_id: r.rs_trend_rank_s63 for r in ranks if r.rs_trend_rank_s63 is not None}

    # 全パネルアイテムぶんの sparkline / 価格履歴 / indicator を 3 クエリで一括取得
    # （per-symbol だと 1 アイテム 3 クエリの N+1 になる）
    panel_ids = [sym_id for sym_id in sym_dict if sym_id in price_dict]
    preload = build_panel_preload(db, panel_ids, target_date)

    for sym_id, s in sym_dict.items():
        if sym_id not in price_dict:
            continue

        dp = price_dict[sym_id]

        # QQQ も SPY と同様に etf_feature として別格扱いにする
        if s.ticker == "QQQ":
            resp.qqq_feature = _build_etf_feature(db, s, dp, target_date)
            continue

        # SPY は category にかかわらず別格扱い (spy_feature) とする
        if s.ticker == "SPY":
            resp.spy_feature = _build_etf_feature(db, s, dp, target_date)
            continue

        # 「指標」カテゴリは leading パネルへ。
        # かつて IBIT / CPER をティッカー直指定で兼任させていたが、GBTC / CPER を
        # 正式に category='指標' へ移したためハードコードは不要になった（W1 / W2b）。
        if s.category == "指標":
            resp.leading.append(_build_leading_item(db, s, dp, target_date, preload=preload))
            continue

        r14_rank = rank_14_dict.get(sym_id, 0.0) or 0.0
        r21_rank = rank_21_dict.get(sym_id, 0.0) or 0.0
        r63_rank = rank_63_dict.get(sym_id, 0.0) or 0.0
        rt14_rank = trend_rank_14_dict.get(sym_id, 0.0) or 0.0
        rt21_rank = trend_rank_21_dict.get(sym_id, 0.0) or 0.0
        rt63_rank = trend_rank_63_dict.get(sym_id, 0.0) or 0.0

        item = _build_panel_item(
            db, s, dp, r21_rank, r63_rank, target_date,
            rank_val_14=r14_rank,
            rank_val_trend_14=rt14_rank,
            rank_val_trend_21=rt21_rank,
            rank_val_trend_63=rt63_rank,
            preload=preload
        )

        if s.category == "市場":
            resp.indices.append(item)
        elif s.category == "セクタ":
            resp.sectors.append(item)
        elif s.category == "テーマ":
            resp.themes_top.append(item)
            
    # Sort and slice based on rank_type
    if rank_type == "rs_trend":
        sort_key = lambda x: x.rs_trend_rank_s21
    else:
        sort_key = lambda x: x.rs_ratio_rank_e21

    resp.sectors.sort(key=sort_key, reverse=True)
    themes_sorted = sorted(resp.themes_top, key=sort_key, reverse=True)
    
    # Calculate VXV/VIX ratio if available (fallback calculation if not in signal)
    vix_item = next((item for item in resp.leading if item.ticker == "^VIX"), None)
    vxv_item = next((item for item in resp.leading if item.ticker == "^VIX3M"), None)
    if vix_item and vxv_item and vix_item.close > 0:
        calculated_ratio = vxv_item.close / vix_item.close
        if resp.vxv_vix_ratio is None:
            resp.vxv_vix_ratio = calculated_ratio
        
    # Sort leading: Put ^VIX, ^VIX3M at top, then the rest by ticker
    def leading_sort_key(item):
        if item.ticker == "^VIX": return "0000"
        if item.ticker == "^VIX3M": return "0001"
        return item.ticker
    resp.leading.sort(key=leading_sort_key)

    resp.themes_top = themes_sorted[:30]
    resp.themes_bottom = themes_sorted[-30:] if themes_sorted else []
    # Sort themes_bottom as weakest first
    resp.themes_bottom.reverse()

    return resp

@router.get("/theme/{symbol_id}", response_model=schemas.ThemeDetailResponse)
def get_theme_detail(
    symbol_id: int,
    db: Session = Depends(get_api_db)
):
    """
    Get detailed theme data including constituent stocks and chart data for the Theme Detail View.
    """
    sym = db.query(Symbol).filter(Symbol.id == symbol_id).first()
    if not sym:
        raise HTTPException(status_code=404, detail="Theme not found")

    # Get latest price for theme
    dp = db.query(DailyPrice).filter(DailyPrice.symbol_id == symbol_id).order_by(desc(DailyPrice.date)).first()
    if not dp:
        raise HTTPException(status_code=404, detail="No price data for theme")

    target_date = dp.date

    # Get last 252 days of price history
    history = db.query(DailyPrice).filter(
        DailyPrice.symbol_id == symbol_id,
        DailyPrice.date <= target_date
    ).order_by(desc(DailyPrice.date)).limit(252).all()
    hist_closes = [h.close for h in history]

    def pct(new, old): return ((new - old) / old * 100) if old and old > 0 else 0.0

    change_1d = pct(dp.close, hist_closes[1]) if len(hist_closes) > 1 else 0.0
    change_1w = pct(dp.close, hist_closes[4]) if len(hist_closes) >= 5 else 0.0
    change_1m = pct(dp.close, hist_closes[20]) if len(hist_closes) >= 21 else 0.0

    # Get latest indicator
    ind = db.query(Indicator).filter(
        Indicator.symbol_id == symbol_id,
        Indicator.date == target_date
    ).first()

    dist_sma5 = pct(dp.close, ind.sma_5)  if ind and ind.sma_5  else 0.0
    dist_sma21 = pct(dp.close, ind.sma_21) if ind and ind.sma_21 else 0.0
    dist_sma63 = pct(dp.close, ind.sma_63) if ind and ind.sma_63 else 0.0
    sma21_sma63 = pct(ind.sma_21, ind.sma_63) if ind and ind.sma_21 and ind.sma_63 else 0.0
    dist_sma200 = pct(dp.close, ind.sma_200) if ind and ind.sma_200 else 0.0

    rs14_spark = _get_sparkline_data(db, symbol_id, target_date, 14)
    rs21_spark = _get_sparkline_data(db, symbol_id, target_date, 21)
    rs63_spark = _get_sparkline_data(db, symbol_id, target_date, 63)

    # 6-Month chart data (approx 126 trading days)
    six_m_hist = list(reversed(history[:126]))
    start_date = six_m_hist[0].date
    end_date = six_m_hist[-1].date
    
    theme_inds = db.query(Indicator).filter(
        Indicator.symbol_id == symbol_id,
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
            relative_strength_spy=i.rs_value if i else None,
            rs_value=i.rs_value if i else None,
            rs_ema_14=i.rs_value_e14 if i else None,
            rs_value_e14=i.rs_value_e14 if i else None,
            rs_ema_21=i.rs_value_e21 if i else None,
            rs_value_e21=i.rs_value_e21 if i else None,
            rs_ema_63=i.rs_value_e63 if i else None,
            rs_value_e63=i.rs_value_e63 if i else None,
            rs_value_e200=i.rs_value_e200 if i else None,
            rs_ratio_e5=i.rs_ratio_e5 if i else None,
            rs_ratio_14=i.rs_ratio_e14 if i else None,
            rs_ratio_e14=i.rs_ratio_e14 if i else None,
            rs_ratio_21=i.rs_ratio_e21 if i else None,
            rs_ratio_e21=i.rs_ratio_e21 if i else None,
            rs_ratio_63=i.rs_ratio_e63 if i else None,
            rs_ratio_e63=i.rs_ratio_e63 if i else None,
            rs_ratio_e200=i.rs_ratio_e200 if i else None,
            rs_momentum_e5=i.rs_momentum_e5 if i else None,
            rs_momentum_14=i.rs_momentum_e14 if i else None,
            rs_momentum_e14=i.rs_momentum_e14 if i else None,
            rs_momentum_21=i.rs_momentum_e21 if i else None,
            rs_momentum_e21=i.rs_momentum_e21 if i else None,
            rs_momentum_63=i.rs_momentum_e63 if i else None,
            rs_momentum_e63=i.rs_momentum_e63 if i else None,
            rs_momentum_e200=i.rs_momentum_e200 if i else None,
            rs_condition_14=i.rs_trend_s14 if i else None,
            rs_trend_s14=i.rs_trend_s14 if i else None,
            rs_condition_21=i.rs_trend_s21 if i else None,
            rs_trend_s21=i.rs_trend_s21 if i else None,
            rs_condition_63=i.rs_trend_s63 if i else None,
            rs_trend_s63=i.rs_trend_s63 if i else None,
            rs_trend_s5=i.rs_trend_s5 if i else None,
            rs_trend_s200=i.rs_trend_s200 if i else None,
            vol_surge_rel_spy_21=i.vol_surge_rel_spy_21 if i else None,
            rel_vol_vs_spy_21=i.vol_surge_rel_spy_21 if i else None,
        ))

    # Get constituent stocks
    mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == symbol_id).all()
    c_ids_mapped = [m.symbol_id for m in mappings]
    if c_ids_mapped:
        constituent_symbols = db.query(Symbol).filter(Symbol.id.in_(c_ids_mapped)).all()
    else:
        constituent_symbols = []

    # Tag-based lookup for ETF themes
    if not constituent_symbols and sym.theme_type == 'etf':
        constituent_symbols = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.category == '個別',
            Symbol.tags.like(f'%{sym.ticker}%')
        ).order_by(Symbol.ticker).all()

    # 構成銘柄の sparkline を一括取得。上限日付なし = 「各銘柄の全期間から最新30件」であり、
    # ランク行は価格行より新しい日付を持たないため per-symbol の date <= c_dp.date と等価
    c_spark_preload = preload_sparklines(db, [s.id for s in constituent_symbols], None)

    # Preload details (prices and indicators) for all constituents in bulk
    c_ids = [s.id for s in constituent_symbols]
    c_details_preload = preload_constituent_details(db, c_ids)

    constituents = []
    for c_sym in constituent_symbols:
        c_id = c_sym.id
        c_dp = c_details_preload["latest_price"].get(c_id)
        if not c_dp:
            continue

        c_closes = c_details_preload["recent_closes_21"].get(c_id, [])

        c_1d = pct(c_dp.close, c_closes[1]) if len(c_closes) > 1 else 0.0
        c_1w = pct(c_dp.close, c_closes[4]) if len(c_closes) >= 5 else 0.0
        c_1m = pct(c_dp.close, c_closes[20]) if len(c_closes) >= 21 else 0.0

        c_ind = c_details_preload["latest_indicator"].get(c_id)
        c_rs14 = c_ind.rs_ratio_e14 if c_ind else None
        c_rs21 = c_ind.rs_ratio_e21 if c_ind else None
        c_rs63 = c_ind.rs_ratio_e63 if c_ind else None
        c_rsmom21 = c_ind.rs_momentum_e21 if c_ind else None
        c_rs_spark = _get_sparkline_data(db, c_id, str(c_dp.date), 21, preloaded=c_spark_preload)

        # short history for RRG
        c_full_hist = c_details_preload["price_history_126"].get(c_id, [])
        c_ind_dict = c_details_preload["indicator_history_126"].get(c_id, {})
        c_chart_data = []
        for h in reversed(c_full_hist):
            ds = str(h.date)
            i = c_ind_dict.get(ds)
            c_chart_data.append(schemas.ChartDataPoint(
                time=ds, open=h.open or 0.0, high=h.high or 0.0, low=h.low or 0.0, close=h.close,
                volume=int(round(h.volume)) if h.volume else 0, 
                relative_strength_spy=i.rs_value if i else None,
                rs_value=i.rs_value if i else None,
                rs_ema_14=i.rs_value_e14 if i else None,
                rs_value_e14=i.rs_value_e14 if i else None,
                rs_ema_21=i.rs_value_e21 if i else None,
                rs_value_e21=i.rs_value_e21 if i else None,
                rs_ema_63=i.rs_value_e63 if i else None,
                rs_value_e63=i.rs_value_e63 if i else None,
                rs_value_e5=i.rs_value_e5 if i else None,
                rs_value_e200=i.rs_value_e200 if i else None,
                rs_ratio_14=i.rs_ratio_e14 if i else None,
                rs_ratio_e14=i.rs_ratio_e14 if i else None,
                rs_ratio_21=i.rs_ratio_e21 if i else None,
                rs_ratio_e21=i.rs_ratio_e21 if i else None,
                rs_ratio_63=i.rs_ratio_e63 if i else None,
                rs_ratio_e63=i.rs_ratio_e63 if i else None,
                rs_ratio_e5=i.rs_ratio_e5 if i else None,
                rs_ratio_e200=i.rs_ratio_e200 if i else None,
                rs_momentum_14=i.rs_momentum_e14 if i else None,
                rs_momentum_e14=i.rs_momentum_e14 if i else None,
                rs_momentum_21=i.rs_momentum_e21 if i else None,
                rs_momentum_e21=i.rs_momentum_e21 if i else None,
                rs_momentum_63=i.rs_momentum_e63 if i else None,
                rs_momentum_e63=i.rs_momentum_e63 if i else None,
                rs_momentum_e5=i.rs_momentum_e5 if i else None,
                rs_momentum_e200=i.rs_momentum_e200 if i else None,
                rs_trend_s5=i.rs_trend_s5 if i else None,
                rs_trend_s14=i.rs_trend_s14 if i else None,
                rs_trend_s21=i.rs_trend_s21 if i else None,
                rs_trend_s63=i.rs_trend_s63 if i else None,
                rs_trend_s200=i.rs_trend_s200 if i else None,
                vol_surge_rel_spy_21=i.vol_surge_rel_spy_21 if i else None,
                rel_vol_vs_spy_21=i.vol_surge_rel_spy_21 if i else None,
            ))

        # Match preloaded RelativeRank for constituent
        r_row = c_details_preload["latest_rank"].get(c_id)
        
        rank_14 = (r_row.rs_ratio_rank_e14 or 0.0) if r_row else 0.0
        rank_21 = (r_row.rs_ratio_rank_e21 or 0.0) if r_row else 0.0
        rank_63 = (r_row.rs_ratio_rank_e63 or 0.0) if r_row else 0.0
        rank_mom21 = (r_row.rs_momentum_rank_e21 or 0.0) if r_row else 0.0
        rank_mom63 = (r_row.rs_momentum_rank_e63 or 0.0) if r_row else 0.0

        constituents.append(schemas.ThemeConstituentItem(
            id=c_id, ticker=c_sym.ticker, name=c_sym.name,
            close=c_dp.close,
            change_1d_pct=c_1d,
            change_1w_pct=c_1w,
            change_1m_pct=c_1m,
            rs_ratio_14=c_rs14,
            rs_ratio_21=c_rs21,
            rs_ratio_63=c_rs63,
            rs_ratio_e14=c_rs14,
            rs_ratio_e21=c_rs21,
            rs_ratio_e63=c_rs63,
            rs_momentum_21=c_rsmom21,
            rs_momentum_e21=c_rsmom21,
            rank_rs_ratio_14=rank_14,
            rank_rs_ratio_21=rank_21,
            rank_rs_ratio_63=rank_63,
            rs_ratio_rank_e14=rank_14,
            rs_ratio_rank_e21=rank_21,
            rs_ratio_rank_e63=rank_63,
            rank_rs_momentum_21=rank_mom21,
            rank_rs_momentum_63=rank_mom63,
            rs_momentum_rank_e21=rank_mom21,
            rs_momentum_rank_e63=rank_mom63,
            rs_sparkline=c_rs_spark,
            chart_data=c_chart_data
        ))

    # Fetch ranks for theme (single query)
    theme_r = db.query(RelativeRank).filter(
        RelativeRank.symbol_id == symbol_id,
        RelativeRank.date == target_date
    ).first()

    return schemas.ThemeDetailResponse(
        id=sym.id, ticker=sym.ticker, name=sym.name, close=dp.close,
        change_1d_pct=change_1d, change_1w_pct=change_1w, change_1m_pct=change_1m,
        dist_sma5_pct=dist_sma5, dist_sma21_pct=dist_sma21, dist_sma63_pct=dist_sma63,
        sma21_sma63_pct=sma21_sma63,
        dist_sma200_pct=dist_sma200,
        rs_ratio_14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rs_ratio_e14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs_ratio_e5=ind.rs_ratio_e5 if ind else None,
        rs_ratio_e200=ind.rs_ratio_e200 if ind else None,
        rs_roc_ema_5=ind.rs_roc_ema_5 if ind else None,
        rs_roc_ema_14=ind.rs_roc_ema_14 if ind else None,
        rs_roc_ema_21=ind.rs_roc_ema_21 if ind else None,
        rs_roc_ema_63=ind.rs_roc_ema_63 if ind else None,
        rs_roc_ema_200=ind.rs_roc_ema_200 if ind else None,
        rs_momentum_14=ind.rs_momentum_e14 if ind else None,
        rs_momentum_21=ind.rs_momentum_e21 if ind else None,
        rs_momentum_63=ind.rs_momentum_e63 if ind else None,
        rs_momentum_e14=ind.rs_momentum_e14 if ind else None,
        rs_momentum_e21=ind.rs_momentum_e21 if ind else None,
        rs_momentum_e63=ind.rs_momentum_e63 if ind else None,
        rs_momentum_e5=ind.rs_momentum_e5 if ind else None,
        rs_momentum_e200=ind.rs_momentum_e200 if ind else None,
        rs_condition_14=ind.rs_trend_s14 if ind else None,
        rs_condition_21=ind.rs_trend_s21 if ind else None,
        rs_condition_63=ind.rs_trend_s63 if ind else None,
        rs_trend_s5=ind.rs_trend_s5 if ind else None,
        rs_trend_s14=ind.rs_trend_s14 if ind else None,
        rs_trend_s21=ind.rs_trend_s21 if ind else None,
        rs_trend_s63=ind.rs_trend_s63 if ind else None,
        rs_trend_s200=ind.rs_trend_s200 if ind else None,
        rs_macd_line_21=ind.rs_macd_line_21 if ind else None,
        rs_macd_signal_21=ind.rs_macd_signal_21 if ind else None,
        rs_macd_hist_21=ind.rs_macd_hist_21 if ind else None,
        rs_value_e5=ind.rs_value_e5 if ind else None,
        rs_value_e14=ind.rs_value_e14 if ind else None,
        rs_value_e21=ind.rs_value_e21 if ind else None,
        rs_value_e63=ind.rs_value_e63 if ind else None,
        rs_value_e200=ind.rs_value_e200 if ind else None,
        rs_ema_14=ind.rs_value_e14 if ind else None,
        rs_ema_21=ind.rs_value_e21 if ind else None,
        rs_ema_63=ind.rs_value_e63 if ind else None,
        adr_pct_21=ind.adr_pct_21 if ind else None,
        vol_accum_days_5=ind.vol_accum_days_5 if ind else None,
        sma50_atr_mult=ind.sma50_atr_mult if ind else None,
        dist_sma50_atr=ind.sma50_atr_mult if ind else None,
        rs14_sparkline=rs14_spark, rs21_sparkline=rs21_spark, rs63_sparkline=rs63_spark,
        rank_rs_ratio_14=theme_r.rs_ratio_rank_e14 if theme_r else None,
        rank_rs_ratio_21=theme_r.rs_ratio_rank_e21 if theme_r else None,
        rank_rs_ratio_63=theme_r.rs_ratio_rank_e63 if theme_r else None,
        rs_ratio_rank_e5=theme_r.rs_ratio_rank_e5 if theme_r else None,
        rs_ratio_rank_e14=theme_r.rs_ratio_rank_e14 if theme_r else None,
        rs_ratio_rank_e21=theme_r.rs_ratio_rank_e21 if theme_r else None,
        rs_ratio_rank_e63=theme_r.rs_ratio_rank_e63 if theme_r else None,
        rs_ratio_rank_e200=theme_r.rs_ratio_rank_e200 if theme_r else None,
        rank_rs_momentum_14=theme_r.rs_momentum_rank_e14 if theme_r else None,
        rank_rs_momentum_21=theme_r.rs_momentum_rank_e21 if theme_r else None,
        rank_rs_momentum_63=theme_r.rs_momentum_rank_e63 if theme_r else None,
        rs_momentum_rank_e5=theme_r.rs_momentum_rank_e5 if theme_r else None,
        rs_momentum_rank_e14=theme_r.rs_momentum_rank_e14 if theme_r else None,
        rs_momentum_rank_e21=theme_r.rs_momentum_rank_e21 if theme_r else None,
        rs_momentum_rank_e63=theme_r.rs_momentum_rank_e63 if theme_r else None,
        rs_momentum_rank_e200=theme_r.rs_momentum_rank_e200 if theme_r else None,
        rank_rs_condition_14=theme_r.rs_trend_rank_s14 if theme_r else None,
        rank_rs_condition_21=theme_r.rs_trend_rank_s21 if theme_r else None,
        rank_rs_condition_63=theme_r.rs_trend_rank_s63 if theme_r else None,
        rs_trend_rank_s5=theme_r.rs_trend_rank_s5 if theme_r else None,
        rs_trend_rank_s14=theme_r.rs_trend_rank_s14 if theme_r else None,
        rs_trend_rank_s21=theme_r.rs_trend_rank_s21 if theme_r else None,
        rs_trend_rank_s63=theme_r.rs_trend_rank_s63 if theme_r else None,
        rs_trend_rank_s200=theme_r.rs_trend_rank_s200 if theme_r else None,
        rs_macd_hist_rank_21=theme_r.rs_macd_hist_rank_21 if theme_r else None,
        chart_data=chart_data, constituents=constituents,
    )

@router.get("/group_data/{ticker}", response_model=schemas.GroupDataResponse)
def get_group_data(
    ticker: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_api_db)
):
    """
    Get metadata, ETF feature, and constituents for a Sector or Theme group.
    """
    sym = db.query(Symbol).filter(Symbol.ticker == ticker).first()
    if not sym:
        raise HTTPException(status_code=404, detail="Group symbol not found")

    # 1. Determine target date
    if date:
        target_date = date
    else:
        latest = db.query(func.max(DailyPrice.date)).filter(DailyPrice.symbol_id == sym.id).scalar()
        if not latest:
            raise HTTPException(status_code=404, detail="No price data available for group")
        target_date = str(latest)

    # 2. Get ETF feature (Header)
    dp = db.query(DailyPrice).filter(DailyPrice.symbol_id == sym.id, DailyPrice.date == target_date).first()
    if not dp:
        # Fallback to closest previous
        dp = db.query(DailyPrice).filter(DailyPrice.symbol_id == sym.id, DailyPrice.date <= target_date).order_by(desc(DailyPrice.date)).first()
        if not dp:
             raise HTTPException(status_code=404, detail="No price data found for group on or before target date")
        target_date = str(dp.date)
    
    feature = _build_etf_feature(db, sym, dp, target_date)

    # 3. Get Constituents (List)
    constituents = []
    if sym.category == "セクタ":
        group_type = "sector"
        # Find themes that have this sector ticker in tags
        child_themes = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.category == "テーマ",
            Symbol.tags.like(f"%{ticker}%")
        ).all()
        child_ids = [s.id for s in child_themes]
    elif sym.category == "テーマ":
        group_type = "theme"
        # Find individual stocks via theme_constituents
        child_mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == sym.id).all()
        child_ids = [m.symbol_id for m in child_mappings]
    else:
        raise HTTPException(status_code=400, detail="Requested ticker is not a Sector or Theme group")

    # Fetch metrics for children
    if child_ids:
        c_prices = db.query(DailyPrice).filter(DailyPrice.date == target_date, DailyPrice.symbol_id.in_(child_ids)).all()
        c_price_dict = {p.symbol_id: p for p in c_prices}
        
        # Fetch ranks for children (single optimized query!)
        c_ranks = db.query(
            RelativeRank.symbol_id, 
            RelativeRank.rs_ratio_rank_e14, 
            RelativeRank.rs_ratio_rank_e21, 
            RelativeRank.rs_ratio_rank_e63, 
            RelativeRank.rs_momentum_rank_e21, 
            RelativeRank.rs_momentum_rank_e63
        ).filter(
            RelativeRank.date == target_date, 
            RelativeRank.symbol_id.in_(child_ids)
        ).all()
        c_rank_14_dict = {r.symbol_id: r.rs_ratio_rank_e14 for r in c_ranks if r.rs_ratio_rank_e14 is not None}
        c_rank_21_dict = {r.symbol_id: r.rs_ratio_rank_e21 for r in c_ranks if r.rs_ratio_rank_e21 is not None}
        c_rank_63_dict = {r.symbol_id: r.rs_ratio_rank_e63 for r in c_ranks if r.rs_ratio_rank_e63 is not None}
        c_rank_mom_dict = {r.symbol_id: r.rs_momentum_rank_e21 for r in c_ranks if r.rs_momentum_rank_e21 is not None}
        c_rank_mom63_dict = {r.symbol_id: r.rs_momentum_rank_e63 for r in c_ranks if r.rs_momentum_rank_e63 is not None}

        c_symbols = db.query(Symbol).filter(Symbol.id.in_(child_ids)).all()

        # 構成銘柄全件ぶんを 3 クエリで一括取得（per-symbol N+1 回避）
        c_panel_ids = [cs.id for cs in c_symbols if cs.id in c_price_dict]
        c_preload = build_panel_preload(db, c_panel_ids, target_date)

        for cs in c_symbols:
            if cs.id in c_price_dict:
                constituents.append(_build_panel_item(
                    db, cs, c_price_dict[cs.id],
                    c_rank_21_dict.get(cs.id, 0.0),
                    c_rank_63_dict.get(cs.id, 0.0),
                    target_date,
                    rank_val_14=c_rank_14_dict.get(cs.id, 0.0),
                    rank_val_mom=c_rank_mom_dict.get(cs.id, 0.0),
                    rank_val_mom63=c_rank_mom63_dict.get(cs.id, 0.0),
                    preload=c_preload
                ))

    # Sort constituents by intensity score (default)
    constituents.sort(key=lambda x: x.intensity_score, reverse=True)

    return schemas.GroupDataResponse(
        ticker=sym.ticker,
        name=sym.name,
        group_type=group_type,
        feature=feature,
        constituents=constituents
    )

