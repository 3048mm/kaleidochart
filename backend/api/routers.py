from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional
from datetime import date as dt_date, timedelta
from db.database import get_db
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning
from api import schemas

router = APIRouter()

# Dependency to get a direct DB Session for FastAPI
def get_api_db():
    with get_db() as db:
        yield db

@router.get("/symbols", response_model=List[schemas.SymbolResponse])
def get_symbols(db: Session = Depends(get_api_db)):
    """
    T1: Get all registered active symbols
    """
    symbols = db.query(Symbol).filter(Symbol.active == 1).order_by(Symbol.category, Symbol.ticker).all()
    return symbols

@router.get("/chart/{symbol_id}", response_model=List[schemas.ChartDataPoint])
def get_chart_data(symbol_id: int, db: Session = Depends(get_api_db)):
    """
    T2+T3: Get combined daily prices and indicators for rendering charts
    TradingView lightweight-charts expects data sorted by time ascending.
    """
    # Verify symbol exists
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    prices = db.query(DailyPrice).filter(DailyPrice.symbol_id == symbol_id).order_by(DailyPrice.date.asc()).all()
    indicators = db.query(Indicator).filter(Indicator.symbol_id == symbol_id).order_by(Indicator.date.asc()).all()
    
    # Map indicators by date string for fast merging
    ind_map = {ind.date.strftime('%Y-%m-%d'): ind for ind in indicators}
    
    # Pre-fetch all RelativeRanks for this symbol to populate the minimaps
    ranks = db.query(RelativeRank).filter(RelativeRank.symbol_id == symbol_id).all()
    # rank_map structure: { "YYYY-MM-DD": { "rs_ratio_21": 0.85, ... } }
    rank_map = {}
    for r in ranks:
        d_str = r.date.strftime('%Y-%m-%d')
        if d_str not in rank_map:
            rank_map[d_str] = {}
        rank_map[d_str][r.indicator_name] = r.percent_rank
    
    chart_data = []
    for p in prices:
        date_str = p.date.strftime('%Y-%m-%d')
        ind = ind_map.get(date_str)
        
        point = {
            "time": date_str,
            "open": p.open,
            "high": p.high,
            "low": p.low,
            "close": p.close,
            "volume": p.volume,
        }
        
        # Merge indicator data if present
        if ind:
            point.update({
                "sma_5": ind.sma_5,
                "sma_21": ind.sma_21,
                "sma_50": ind.sma_50,
                "sma_63": ind.sma_63,
                "sma_150": ind.sma_150,
                "sma_200": ind.sma_200,
                "ema_5": ind.ema_5,
                "ema_21": ind.ema_21,
                "ema_50": ind.ema_50,
                "ema_63": ind.ema_63,
                "ema_150": ind.ema_150,
                "ema_200": ind.ema_200,
                "td9": ind.td9,
                "atr_14": ind.atr_14,
                "atr_pct_14": ind.atr_pct_14,
                "adr_pct_21": ind.adr_pct_21,
                "dist_sma50_atr": ind.dist_sma50_atr,
                "relative_strength_spy": ind.relative_strength_spy,
                "rs_condition_14": ind.rs_condition_14,
                "rs_condition_21": ind.rs_condition_21,
                "rs_condition_63": ind.rs_condition_63,
                "rs_momentum_14": ind.rs_momentum_14,
                "rs_momentum_21": ind.rs_momentum_21,
                "rs_momentum_63": ind.rs_momentum_63,
                "rs_ratio_14": ind.rs_ratio_14,
                "rs_ratio_21": ind.rs_ratio_21,
                "rs_ratio_63": ind.rs_ratio_63,
                "vol_surge_21": ind.vol_surge_21,
                "rel_vol_vs_spy_21": ind.rel_vol_vs_spy_21,
                "pct_from_63d_high": ind.pct_from_63d_high,
                "pct_from_52w_high": ind.pct_from_52w_high,
                "trend_template_ok": ind.trend_template_ok
            })
            
        # Merge relative ranks if present
        r_data = rank_map.get(date_str)
        if r_data:
            point.update({
                "rank_rs_ratio_14": r_data.get("rs_ratio_14"),
                "rank_rs_ratio_21": r_data.get("rs_ratio_21"),
                "rank_rs_ratio_63": r_data.get("rs_ratio_63"),
                "rank_rs_momentum_14": r_data.get("rs_momentum_14"),
                "rank_rs_momentum_21": r_data.get("rs_momentum_21"),
                "rank_rs_momentum_63": r_data.get("rs_momentum_63"),
                "rank_rs_condition_14": r_data.get("rs_condition_14"),
                "rank_rs_condition_21": r_data.get("rs_condition_21"),
                "rank_rs_condition_63": r_data.get("rs_condition_63"),
            })
            
        chart_data.append(schemas.ChartDataPoint(**point))
        
    return chart_data

@router.get("/ranking", response_model=List[schemas.RankingResponse])
def get_rankings(db: Session = Depends(get_api_db), limit: int = 20, asc: bool = False):
    """
    T4: Get the latest rankings across groups.
    By default gets the highest percent rank (descending). Set asc=True to get lowest.
    """
    # 1. We only want the latest date available in T4
    latest_date_row = db.query(RelativeRank.date).order_by(RelativeRank.date.desc()).first()
    if not latest_date_row:
        return []
        
    latest_date = latest_date_row.date
    
    order_col = RelativeRank.percent_rank.asc() if asc else RelativeRank.percent_rank.desc()
    
    # Join with Symbol to get ticker names
    results = db.query(RelativeRank, Symbol).join(
        Symbol, RelativeRank.symbol_id == Symbol.id
    ).filter(
        RelativeRank.date == latest_date
    ).order_by(order_col).limit(limit).all()
    
    rankings = []
    for rank_row, sym_row in results:
        rankings.append(schemas.RankingResponse(
            symbol_id=sym_row.id,
            ticker=sym_row.ticker,
            name=sym_row.name,
            group_name=rank_row.group_name,
            indicator_name=rank_row.indicator_name,
            percent_rank=rank_row.percent_rank,
            date=str(rank_row.date)
        ))
        
    return rankings

@router.get("/dashboard", response_model=schemas.DashboardResponse)
def get_dashboard(
    date: Optional[str] = Query(None, description="ISO Format Date YYYY-MM-DD"),
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

    resp = schemas.DashboardResponse(
        date=target_date,
        market_phase=signal.market_phase,
        distribution_days=signal.distribution_days or 0,
        leading=[],
        indices=[],
        sectors=[],
        themes_top=[],
        themes_bottom=[]
    )

    # Helper function to get N days history for sparklines
    def get_sparkline_data(sym_id: int):
        ranks = db.query(RelativeRank.percent_rank).filter(
            RelativeRank.symbol_id == sym_id,
            RelativeRank.indicator_name == "rs_ratio_21",
            RelativeRank.date <= target_date
        ).order_by(desc(RelativeRank.date)).limit(30).all()
        
        # reverse back to chron order; values are already 0-1
        vals = [r[0] for r in reversed(ranks)]
        if not vals:
            return [0.5] * 5  # fallback
        return vals

    # Helper function to build PanelItem
    def build_panel_item(sym: Symbol, dp: DailyPrice, rank_val_21: float, rank_val_63: float):
        sparkline = get_sparkline_data(sym.id)
        
        # We need historical prices up to target_date for 1W/1M
        history = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sym.id,
            DailyPrice.date <= target_date
        ).order_by(desc(DailyPrice.date)).limit(21).all()
        
        hist_closes = [h[0] for h in history]
        
        change_1d_pct = 0.0
        change_1w_pct = 0.0
        change_1m_pct = 0.0
        
        if len(hist_closes) > 1 and hist_closes[1] > 0:
            change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
            
        if len(hist_closes) >= 5 and hist_closes[4] > 0:
            change_1w_pct = ((dp.close - hist_closes[4]) / hist_closes[4]) * 100
            
        if len(hist_closes) >= 21 and hist_closes[20] > 0:
            change_1m_pct = ((dp.close - hist_closes[20]) / hist_closes[20]) * 100
        elif len(hist_closes) > 1 and hist_closes[-1] > 0:
            change_1m_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100
            
        # Get dist_21ema_pct
        ind = db.query(Indicator).filter(
            Indicator.symbol_id == sym.id,
            Indicator.date == dp.date
        ).first()
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
            sparkline=sparkline,
            intensity_score=rank_val_21,
            rs_ratio_21_rank=rank_val_21,
            rs_ratio_63_rank=rank_val_63,
            rs_ratio_21=ind.rs_ratio_21 if ind else None,
            rs_ratio_63=ind.rs_ratio_63 if ind else None,
            rs_momentum_21=ind.rs_momentum_21 if ind else None
        )

    # Helper function to build LeadingIndicatorItem
    def build_leading_item(sym: Symbol, dp: DailyPrice):
        # We need historical prices up to target_date
        history = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sym.id,
            DailyPrice.date <= target_date
        ).order_by(desc(DailyPrice.date)).limit(21).all()
        
        hist_closes = [h[0] for h in history]
        sparkline_raw = list(reversed(hist_closes)) # chron order
        
        change_1d_pct = 0.0
        change_1w_pct = 0.0
        change_1m_pct = 0.0
        
        if len(hist_closes) > 1 and hist_closes[1] > 0:
            change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
            
        if len(hist_closes) >= 5 and hist_closes[4] > 0:
            change_1w_pct = ((dp.close - hist_closes[4]) / hist_closes[4]) * 100
            
        if len(hist_closes) >= 21 and hist_closes[20] > 0:
            change_1m_pct = ((dp.close - hist_closes[20]) / hist_closes[20]) * 100
        elif len(hist_closes) > 1 and hist_closes[-1] > 0:
            change_1m_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100
            
        # Get dist_21ema_pct
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
            close=dp.close,
            change_1d_pct=change_1d_pct,
            change_1w_pct=change_1w_pct,
            change_1m_pct=change_1m_pct,
            dist_21ema_pct=dist_21ema_pct,
            sparkline=sparkline_raw
        )

    # Helper function for SPY
    def build_spy_item(sym: Symbol, dp: DailyPrice):
        # Fetch 1 year of history (approx 252 days)
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
        if len(hist_closes) >= 5 and hist_closes[4] > 0:
            change_1w_pct = ((dp.close - hist_closes[4]) / hist_closes[4]) * 100
        if len(hist_closes) >= 21 and hist_closes[20] > 0:
            change_1m_pct = ((dp.close - hist_closes[20]) / hist_closes[20]) * 100
        elif len(hist_closes) > 1 and hist_closes[-1] > 0:
            change_1m_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100
            
        if len(hist_closes) >= 252 and hist_closes[251] > 0:
            change_1y_pct = ((dp.close - hist_closes[251]) / hist_closes[251]) * 100
        elif len(hist_closes) > 1 and hist_closes[-1] > 0:
            change_1y_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100
            
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
                
        # Chart Data (6 months = approx 126 days)
        chart_data = []
        six_m_hist = history[:126]
        # TradeView expects chronological order
        for h in reversed(six_m_hist):
            chart_data.append(schemas.ChartDataPoint(
                time=str(h.date),
                open=h.open,
                high=h.high,
                low=h.low,
                close=h.close,
                volume=h.volume
            ))
            
        return schemas.SpyFeatureItem(
            id=sym.id,
            ticker=sym.ticker,
            name=sym.name,
            close=dp.close,
            change_1d_pct=change_1d_pct,
            change_1w_pct=change_1w_pct,
            change_1m_pct=change_1m_pct,
            change_1y_pct=change_1y_pct,
            dist_sma5_pct=dist_sma5_pct,
            dist_sma21_pct=dist_sma21_pct,
            dist_sma63_pct=dist_sma63_pct,
            sma21_sma63_pct=sma21_sma63_pct,
            chart_data=chart_data
        )

    # 3. Get Indices, Sectors, Themes
    symbols = db.query(Symbol).filter(Symbol.active == 1).all()
    sym_dict = {s.id: s for s in symbols}
    
    # Get Prices for target date
    prices = db.query(DailyPrice).filter(DailyPrice.date == target_date).all()
    price_dict = {p.symbol_id: p for p in prices}

    # Get Relative Ranks for target date
    ranks_21 = db.query(RelativeRank).filter(
        RelativeRank.date == target_date, 
        RelativeRank.indicator_name == "rs_ratio_21"
    ).all()
    rank_21_dict = {r.symbol_id: r.percent_rank for r in ranks_21}

    ranks_63 = db.query(RelativeRank).filter(
        RelativeRank.date == target_date, 
        RelativeRank.indicator_name == "rs_ratio_63"
    ).all()
    rank_63_dict = {r.symbol_id: r.percent_rank for r in ranks_63}

    for sym_id, s in sym_dict.items():
        if sym_id not in price_dict:
            continue
            
        dp = price_dict[sym_id]
        r21_rank = rank_21_dict.get(sym_id, 0.5)
        r63_rank = rank_63_dict.get(sym_id, 0.5)
        
        if s.category == "市場":
            resp.indices.append(build_panel_item(s, dp, r21_rank, r63_rank))
        elif s.category == "指標":
            if s.ticker == "SPY":
                resp.spy_feature = build_spy_item(s, dp)
            else:
                resp.leading.append(build_leading_item(s, dp))
        elif s.category == "セクタ":
            resp.sectors.append(build_panel_item(s, dp, r21_rank, r63_rank))
        elif s.category == "テーマ":
            item = build_panel_item(s, dp, r21_rank, r63_rank)
            resp.themes_top.append(item)
            
    # Sort and slice
    resp.sectors.sort(key=lambda x: x.intensity_score, reverse=True)
    themes_sorted = sorted(resp.themes_top, key=lambda x: x.intensity_score, reverse=True)
    
    resp.themes_top = themes_sorted[:30]
    resp.themes_bottom = themes_sorted[-30:] if themes_sorted else []

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

    # RS Sparklines (last 30 trading days)
    def get_rs_sparkline(sym_id, period):
        col_name = f'rs_ratio_{period}'
        rows = db.query(Indicator).filter(
            Indicator.symbol_id == sym_id,
            Indicator.date <= target_date
        ).order_by(desc(Indicator.date)).limit(30).all()
        vals = [getattr(r, col_name) for r in reversed(rows) if getattr(r, col_name) is not None]
        return vals

    rs14_spark = get_rs_sparkline(symbol_id, 14)
    rs21_spark = get_rs_sparkline(symbol_id, 21)
    rs63_spark = get_rs_sparkline(symbol_id, 63)

    # 6-Month chart data (approx 126 trading days) — with indicator RS values for RRG
    six_m_hist = list(reversed(history[:126]))
    theme_inds = db.query(Indicator).filter(
        Indicator.symbol_id == symbol_id,
        Indicator.date.in_([h.date for h in six_m_hist])
    ).all()
    theme_ind_dict = {str(r.date): r for r in theme_inds}
    chart_data = []
    for h in six_m_hist:
        i = theme_ind_dict.get(str(h.date))
        chart_data.append(schemas.ChartDataPoint(
            time=str(h.date),
            open=h.open or 0.0,
            high=h.high or 0.0,
            low=h.low or 0.0,
            close=h.close,
            volume=h.volume or 0,
            rs_ratio_14=i.rs_ratio_14 if i else None,
            rs_ratio_21=i.rs_ratio_21 if i else None,
            rs_ratio_63=i.rs_ratio_63 if i else None,
            rs_momentum_14=i.rs_momentum_14 if i else None,
            rs_momentum_21=i.rs_momentum_21 if i else None,
            rs_momentum_63=i.rs_momentum_63 if i else None,
            rs_condition_14=i.rs_condition_14 if i else None,
            rs_condition_21=i.rs_condition_21 if i else None,
            rs_condition_63=i.rs_condition_63 if i else None,
        ))

    # Get constituent stocks
    # Strategy 1: ThemeConstituent table (virtual themes)
    mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == symbol_id).all()
    constituent_symbols = [db.query(Symbol).filter(Symbol.id == m.symbol_id).first() for m in mappings]
    constituent_symbols = [s for s in constituent_symbols if s is not None]

    # Strategy 2: Tag-based lookup for ETF themes (stocks tagged with the ETF ticker)
    if not constituent_symbols and sym.theme_type == 'etf':
        constituent_symbols = db.query(Symbol).filter(
            Symbol.active == 1,
            Symbol.category == '個別',
            Symbol.tags.like(f'%{sym.ticker}%')
        ).order_by(Symbol.ticker).all()

    constituents = []

    for c_sym in constituent_symbols:
        c_id = c_sym.id
        c_dp = db.query(DailyPrice).filter(DailyPrice.symbol_id == c_id).order_by(desc(DailyPrice.date)).first()
        if not c_dp:
            continue

        c_hist = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == c_id,
            DailyPrice.date <= c_dp.date
        ).order_by(desc(DailyPrice.date)).limit(21).all()
        c_closes = [h[0] for h in c_hist]

        c_1d = pct(c_dp.close, c_closes[1]) if len(c_closes) > 1 else 0.0
        c_1w = pct(c_dp.close, c_closes[4]) if len(c_closes) >= 5 else 0.0
        c_1m = pct(c_dp.close, c_closes[20]) if len(c_closes) >= 21 else 0.0

        c_ind = db.query(Indicator).filter(
            Indicator.symbol_id == c_id,
            Indicator.date == c_dp.date
        ).first()

        c_rs14 = c_ind.rs_ratio_14 if c_ind else None
        c_rs21 = c_ind.rs_ratio_21 if c_ind else None
        c_rs63 = c_ind.rs_ratio_63 if c_ind else None
        c_rsmom21 = c_ind.rs_momentum_21 if c_ind else None

        # RS sparkline for constituent
        c_rs_spark = get_rs_sparkline(c_id, 21)

        # Full chart history for RRG
        c_full_hist = db.query(DailyPrice).filter(
            DailyPrice.symbol_id == c_id,
            DailyPrice.date <= c_dp.date
        ).order_by(desc(DailyPrice.date)).limit(126).all()
        c_inds = db.query(Indicator).filter(
            Indicator.symbol_id == c_id,
            Indicator.date.in_([h.date for h in c_full_hist])
        ).all()
        c_ind_dict = {str(r.date): r for r in c_inds}
        c_chart_data = []
        for h in reversed(c_full_hist):
            i = c_ind_dict.get(str(h.date))
            c_chart_data.append(schemas.ChartDataPoint(
                time=str(h.date),
                open=h.open or 0.0,
                high=h.high or 0.0,
                low=h.low or 0.0,
                close=h.close,
                volume=h.volume or 0,
                rs_ratio_14=i.rs_ratio_14 if i else None,
                rs_ratio_21=i.rs_ratio_21 if i else None,
                rs_ratio_63=i.rs_ratio_63 if i else None,
                rs_momentum_14=i.rs_momentum_14 if i else None,
                rs_momentum_21=i.rs_momentum_21 if i else None,
                rs_momentum_63=i.rs_momentum_63 if i else None,
                rs_condition_14=i.rs_condition_14 if i else None,
                rs_condition_21=i.rs_condition_21 if i else None,
                rs_condition_63=i.rs_condition_63 if i else None,
            ))

        constituents.append(schemas.ThemeConstituentItem(
            id=c_sym.id,
            ticker=c_sym.ticker,
            name=c_sym.name,
            close=c_dp.close,
            change_1d_pct=c_1d,
            change_1w_pct=c_1w,
            change_1m_pct=c_1m,
            rs_ratio_14=c_rs14,
            rs_ratio_21=c_rs21,
            rs_ratio_63=c_rs63,
            rs_momentum_21=c_rsmom21,
            rs_sparkline=c_rs_spark,
            chart_data=c_chart_data,
        ))

    return schemas.ThemeDetailResponse(
        id=sym.id,
        ticker=sym.ticker,
        name=sym.name,
        close=dp.close,
        change_1d_pct=change_1d,
        change_1w_pct=change_1w,
        change_1m_pct=change_1m,
        dist_sma5_pct=dist_sma5,
        dist_sma21_pct=dist_sma21,
        dist_sma63_pct=dist_sma63,
        sma21_sma63_pct=sma21_sma63,
        rs_ratio_14=ind.rs_ratio_14 if ind else None,
        rs_ratio_21=ind.rs_ratio_21 if ind else None,
        rs_ratio_63=ind.rs_ratio_63 if ind else None,
        rs_momentum_14=ind.rs_momentum_14 if ind else None,
        rs_momentum_21=ind.rs_momentum_21 if ind else None,
        rs_momentum_63=ind.rs_momentum_63 if ind else None,
        rs_condition_14=ind.rs_condition_14 if ind else None,
        rs_condition_21=ind.rs_condition_21 if ind else None,
        rs_condition_63=ind.rs_condition_63 if ind else None,
        adr_pct_21=ind.adr_pct_21 if ind else None,
        dist_sma50_atr=ind.dist_sma50_atr if ind else None,
        rs14_sparkline=rs14_spark,
        rs21_sparkline=rs21_spark,
        rs63_sparkline=rs63_spark,
        chart_data=chart_data,
        constituents=constituents,
    )

@router.get("/screener/dashboard", response_model=schemas.ScreenerDashboardResponse)
def get_screener_dashboard(
    db: Session = Depends(get_api_db),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD")
):
    # Determine the date to use
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()
        
    if not latest_date_result:
        return schemas.ScreenerDashboardResponse(rise=[], fall=[])

    # Base query wrapper function
    def q_base():
        return db.query(Symbol.id, Symbol.ticker, Symbol.name, DailyPrice.open, DailyPrice.close).join(
            Indicator, Symbol.id == Indicator.symbol_id
        ).join(
            DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
        ).filter(Symbol.active == True, Indicator.date == latest_date_result)

    def fetch_top_8(query, order_col="Indicator.rs_ratio_21.desc()"):
        # For simple sorting we can order by close/open change or indicator fields
        results = query.limit(8).all()
        items = []
        for r in results:
            chg = ((r.close - r.open) / r.open * 100) if r.open else 0.0
            items.append(schemas.ScreenerDashboardItem(id=r.id, ticker=r.ticker, name=r.name, change_pct=chg))
        return items

    # Rise - [Check] (5 new presets)
    _GAIN_1B = 1_000_000_000
    _1d_gain = q_base().filter(
        ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) >= 4.0,
        Indicator.rel_vol_vs_spy_21 >= 1.0,
        Indicator.dist_sma50_atr <= 6.0,
        Indicator.adr_pct_21 >= 4.0,
        Indicator.market_cap >= _GAIN_1B,
        Indicator.trend_template_ok == 1
    ).order_by(desc((DailyPrice.close - DailyPrice.open) / DailyPrice.open))

    _vol_surge = q_base().filter(
        Indicator.vol_surge_21 >= 1.5,
        Indicator.rel_vol_vs_spy_21 >= 1.2,
        ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) >= 0.0,
        Indicator.adr_pct_21 >= 4.0,
        Indicator.market_cap >= _GAIN_1B,
        Indicator.dist_sma50_atr <= 6.0
    ).order_by(desc(Indicator.vol_surge_21))

    _21ema = q_base().filter(
        ((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) >= -2.0,
        ((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) <= 2.0,
        Indicator.dist_sma50_atr <= 6.0,
        Indicator.adr_pct_21 >= 4.0,
        Indicator.market_cap >= _GAIN_1B,
        Indicator.trend_template_ok == 1
    ).order_by(desc(Indicator.rs_ratio_21))

    # Momentum 97 requires RelativeRank join
    _latest_rank_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    if _latest_rank_date:
        _momentum97 = db.query(Symbol.id, Symbol.ticker, Symbol.name, DailyPrice.open, DailyPrice.close).join(
            Indicator, Symbol.id == Indicator.symbol_id
        ).join(
            DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
        ).join(
            RelativeRank,
            (Symbol.id == RelativeRank.symbol_id) &
            (RelativeRank.indicator_name == 'rs_ratio_21') &
            (RelativeRank.date == _latest_rank_date)
        ).filter(
            Symbol.active == 1,
            Indicator.date == latest_date_result,
            RelativeRank.percent_rank >= 0.97,
            Indicator.trend_template_ok == 1
        ).order_by(desc(RelativeRank.percent_rank))
    else:
        _momentum97 = q_base().filter(False)

    _vcp = q_base().filter(
        Indicator.adr_pct_21 < 3.0,
        DailyPrice.close > Indicator.sma_50,
        Indicator.rs_condition_21 > 1.0,
        Indicator.market_cap >= _GAIN_1B
    ).order_by(desc(Indicator.rs_ratio_21))

    # Rise - [Overhead sign]
    climax = q_base().filter((Indicator.dist_sma50_atr > 3.0) | (Indicator.td9 == -9)).order_by(desc(Indicator.dist_sma50_atr))
    dist8 = q_base().filter(Indicator.dist_sma50_atr > 8.0).order_by(desc(Indicator.dist_sma50_atr))
    td9_overhead = q_base().filter(Indicator.td9 >= 8).order_by(desc(Indicator.td9))

    # Fall - [Warning]
    tb = q_base().filter(Indicator.trend_template_ok == 0, Indicator.rs_ratio_63 < -1.0, Indicator.sma_50 < Indicator.sma_200).order_by(Indicator.rs_ratio_63)
    hvd = q_base().filter(Indicator.vol_surge_21 > 1.5, ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) < -2.0).order_by(desc(Indicator.vol_surge_21))

    # Fall - [Rebound sign]
    osr = q_base().filter(Indicator.td9 == 9, Indicator.rs_ratio_14 < 0.2).order_by(Indicator.rs_ratio_14)
    td9_rebound = q_base().filter(Indicator.td9 <= -8).order_by(Indicator.td9)

    return schemas.ScreenerDashboardResponse(
        rise=[
            schemas.ScreenerDashboardCategory(id="check_1d_gain", name="1D% Gain", group="Check", items=fetch_top_8(_1d_gain)),
            schemas.ScreenerDashboardCategory(id="check_volume_surge", name="Volume Surge", group="Check", items=fetch_top_8(_vol_surge)),
            schemas.ScreenerDashboardCategory(id="check_21ema", name="21EMA Pullback", subtitle="(-2%~+2%)", group="Check", items=fetch_top_8(_21ema)),
            schemas.ScreenerDashboardCategory(id="check_momentum97", name="Momentum 97", group="Check", items=fetch_top_8(_momentum97)),
            schemas.ScreenerDashboardCategory(id="check_vcp", name="VCP", group="Check", items=fetch_top_8(_vcp)),
            schemas.ScreenerDashboardCategory(id="climax_top", name="Climax Top / Overextended", group="Overhead sign", items=fetch_top_8(climax)),
            schemas.ScreenerDashboardCategory(id="dist_sma50_atr_8", name="SMA50/ATR%", subtitle="> 8%", group="Overhead sign", items=fetch_top_8(dist8)),
            schemas.ScreenerDashboardCategory(id="td9_overhead", name="TDR9", subtitle=">= 8", group="Overhead sign", items=fetch_top_8(td9_overhead)),
        ],
        fall=[
            schemas.ScreenerDashboardCategory(id="trend_breakdown", name="Trend Breakdown", group="Warning", items=fetch_top_8(tb)),
            schemas.ScreenerDashboardCategory(id="high_vol_dist", name="High Volume Distribution", group="Warning", items=fetch_top_8(hvd)),
            schemas.ScreenerDashboardCategory(id="oversold_rebound", name="Oversold Rebound", group="Rebound sign", items=fetch_top_8(osr)),
            schemas.ScreenerDashboardCategory(id="td9_rebound", name="TDR9", subtitle="<= -8", group="Rebound sign", items=fetch_top_8(td9_rebound)),
        ]
    )


@router.get("/screener", response_model=List[schemas.ScreenerResultItem])
def get_screener(
    db: Session = Depends(get_api_db),
    preset: Optional[str] = Query(None, description="Preset name"),
    category: Optional[str] = Query(None, description="Category filter"),
    min_change_pct: Optional[float] = Query(None, description="Min 1D Change %"),
    min_rs_ratio_21: Optional[float] = Query(None, description="Min RS Ratio 21"),
    min_vol_surge_21: Optional[float] = Query(None, description="Min Volume Surge 21"),
    max_adr_pct_21: Optional[float] = Query(None, description="Max ADR % 21"),
    trend_template_ok: Optional[int] = Query(None, description="Trend Template Flag"),
    min_dist_sma50_atr: Optional[float] = Query(None, description="Min Dist 50SMA ATR"),
    max_dist_sma50_atr: Optional[float] = Query(None, description="Max Dist 50SMA ATR"),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD"),
    min_market_cap: Optional[float] = Query(None, description="Min Market Cap"),
    max_market_cap: Optional[float] = Query(None, description="Max Market Cap"),
    require_positive_eps: Optional[bool] = Query(None, description="Require latest EPS > 0"),
    # New filter params
    min_1d_gain_pct: Optional[float] = Query(None, description="Min 1D Gain %"),
    max_1d_gain_pct: Optional[float] = Query(None, description="Max 1D Gain %"),
    min_rel_vol: Optional[float] = Query(None, description="Min Relative Vol vs SPY21"),
    max_rel_vol: Optional[float] = Query(None, description="Max Relative Vol vs SPY21"),
    rs_rank_21_gt_63: Optional[bool] = Query(None, description="RS21 rank > RS63 rank"),
    min_adr_pct_21: Optional[float] = Query(None, description="Min ADR % 21"),
    min_dist_21ema_pct: Optional[float] = Query(None, description="Min Distance from 21EMA %"),
    max_dist_21ema_pct: Optional[float] = Query(None, description="Max Distance from 21EMA %"),
    min_rs_ratio_21_rank: Optional[float] = Query(None, description="Min RS21 Percentile Rank 0-1"),
    max_rs_ratio_21_rank: Optional[float] = Query(None, description="Max RS21 Percentile Rank 0-1"),
    close_gt_sma50: Optional[bool] = Query(None, description="Close > SMA50"),
    min_dist_sma50_pct: Optional[float] = Query(None, description="Min Dist from SMA50 %"),
    max_dist_sma50_pct: Optional[float] = Query(None, description="Max Dist from SMA50 %"),
    min_rs_condition_21: Optional[float] = Query(None, description="Min RS Condition 21"),
    max_rs_condition_21: Optional[float] = Query(None, description="Max RS Condition 21"),
    min_td9: Optional[int] = Query(None, description="Min TDR9"),
    max_td9: Optional[int] = Query(None, description="Max TDR9"),
    max_vol_surge_21: Optional[float] = Query(None, description="Max Volume Surge 21")
):
    # Determine the date to use for indicators
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()
        
    if not latest_date_result:
        return []

    # Base query for active symbols
    query = db.query(Symbol, Indicator, DailyPrice).join(
        Indicator, Symbol.id == Indicator.symbol_id
    ).join(
        DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
    ).filter(Symbol.active == 1)

    # Filter by the determined date
    query = query.filter(Indicator.date == latest_date_result)

    # Base Presets (Apply these first)
    if preset == "trend_template":
        query = query.filter(Indicator.trend_template_ok == 1)
        # We don't artificially restrict to 0.8 here if the user can override, but as a preset, let's limit it by default unless overridden?
        # A simpler way is to just set these in the query. For a true hybrid approach,
        # the frontend would pass the individual params when a preset is selected!
        # BUT if they just pass preset="trend_template", we add the base here.
    elif preset == "vcp":
        query = query.filter(
            Indicator.adr_pct_21 < 3.0,
            DailyPrice.close > Indicator.sma_50,
            Indicator.rs_condition_21 > 1.0
        )
    elif preset == "volume_surge":
        query = query.filter(
            Indicator.vol_surge_21 > 1.5,
            Indicator.rel_vol_vs_spy_21 > 1.2,
            ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) > 1.0
        )
    elif preset == "pullback_21ema":
        query = query.filter(
            Indicator.trend_template_ok == 1,
            ((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) >= -1.0,
            ((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) <= 2.0
        )
    elif preset == "climax_top":
        query = query.filter(
            ((Indicator.dist_sma50_atr > 3.0) | (Indicator.td9 == -9))
        )
    elif preset == "dist_sma50_atr_8":
        query = query.filter(Indicator.dist_sma50_atr > 8.0)
    elif preset == "td9_overhead":
        query = query.filter(Indicator.td9 >= 8)
    elif preset == "trend_breakdown":
        query = query.filter(
            Indicator.trend_template_ok == 0,
            Indicator.rs_ratio_63 < -1.0,
            Indicator.sma_50 < Indicator.sma_200
        )
    elif preset == "high_vol_dist":
        query = query.filter(
            Indicator.vol_surge_21 > 1.5,
            ((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) < -2.0
        )
    elif preset == "oversold_rebound":
        query = query.filter(
            Indicator.td9 == 9,
            Indicator.rs_ratio_14 < 0.2
        )
    elif preset == "td9_rebound":
        query = query.filter(Indicator.td9 <= -8)

    # Custom Filters (AND applied on top)
    if category is not None and category != "":
        query = query.filter(Symbol.category == category)
    if min_change_pct is not None:
        # change_pct is approximately (close-open)/open or from previous close. We use dailyprice open for now or we can use previous close if we had it.
        # Wait, the DB does not have immediate previous close easily available in this single row Query. 
        # But Indicator has pct_from_52w_high etc. Let's just use 1D gain calculation from today's open for filtering:
        query = query.filter(((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) >= min_change_pct)
    if min_rs_ratio_21 is not None:
        query = query.filter(Indicator.rs_ratio_21 >= min_rs_ratio_21)
    if min_vol_surge_21 is not None:
        query = query.filter(Indicator.vol_surge_21 >= min_vol_surge_21)
    if max_adr_pct_21 is not None:
        query = query.filter(Indicator.adr_pct_21 <= max_adr_pct_21)
    if trend_template_ok is not None:
        query = query.filter(Indicator.trend_template_ok == trend_template_ok)
    if min_dist_sma50_atr is not None:
        query = query.filter(Indicator.dist_sma50_atr >= min_dist_sma50_atr)
    if max_dist_sma50_atr is not None:
        query = query.filter(Indicator.dist_sma50_atr <= max_dist_sma50_atr)
    if min_market_cap is not None:
        query = query.filter(Indicator.market_cap >= min_market_cap)
    if max_market_cap is not None:
        query = query.filter(Indicator.market_cap <= max_market_cap)

    # New individual filter logic
    if min_1d_gain_pct is not None:
        query = query.filter(((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) >= min_1d_gain_pct)
    if max_1d_gain_pct is not None:
        query = query.filter(((DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100) <= max_1d_gain_pct)
    if min_rel_vol is not None:
        query = query.filter(Indicator.rel_vol_vs_spy_21 >= min_rel_vol)
    if max_rel_vol is not None:
        query = query.filter(Indicator.rel_vol_vs_spy_21 <= max_rel_vol)
    if max_vol_surge_21 is not None:
        query = query.filter(Indicator.vol_surge_21 <= max_vol_surge_21)
    if min_adr_pct_21 is not None:
        query = query.filter(Indicator.adr_pct_21 >= min_adr_pct_21)
    if min_dist_21ema_pct is not None:
        query = query.filter(((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) >= min_dist_21ema_pct)
    if max_dist_21ema_pct is not None:
        query = query.filter(((DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100) <= max_dist_21ema_pct)
    if close_gt_sma50:
        query = query.filter(DailyPrice.close > Indicator.sma_50)
    if min_dist_sma50_pct is not None:
        query = query.filter(((DailyPrice.close - Indicator.sma_50) / Indicator.sma_50 * 100) >= min_dist_sma50_pct)
    if max_dist_sma50_pct is not None:
        query = query.filter(((DailyPrice.close - Indicator.sma_50) / Indicator.sma_50 * 100) <= max_dist_sma50_pct)
    if min_rs_condition_21 is not None:
        query = query.filter(Indicator.rs_condition_21 >= min_rs_condition_21)
    if max_rs_condition_21 is not None:
        query = query.filter(Indicator.rs_condition_21 <= max_rs_condition_21)
    if min_td9 is not None:
        query = query.filter(Indicator.td9 >= min_td9)
    if max_td9 is not None:
        query = query.filter(Indicator.td9 <= max_td9)

    if rs_rank_21_gt_63 or min_rs_ratio_21_rank is not None or max_rs_ratio_21_rank is not None:
        _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
        if _rk_date:
            rank21_subq = db.query(
                RelativeRank.symbol_id,
                RelativeRank.percent_rank.label('r21')
            ).filter(
                RelativeRank.date == _rk_date,
                RelativeRank.indicator_name == 'rs_ratio_21'
            ).subquery()
            query = query.join(rank21_subq, Symbol.id == rank21_subq.c.symbol_id)
            if min_rs_ratio_21_rank is not None:
                query = query.filter(rank21_subq.c.r21 >= min_rs_ratio_21_rank)
            if max_rs_ratio_21_rank is not None:
                query = query.filter(rank21_subq.c.r21 <= max_rs_ratio_21_rank)
            if rs_rank_21_gt_63:
                rank63_subq = db.query(
                    RelativeRank.symbol_id,
                    RelativeRank.percent_rank.label('r63')
                ).filter(
                    RelativeRank.date == _rk_date,
                    RelativeRank.indicator_name == 'rs_ratio_63'
                ).subquery()
                query = query.join(rank63_subq, Symbol.id == rank63_subq.c.symbol_id).filter(
                    rank21_subq.c.r21 > rank63_subq.c.r63
                )

    if require_positive_eps:
        target_eval_date = target_date if target_date else dt_date.today().strftime('%Y-%m-%d')
        latest_earnings_subq = db.query(
            Earning.symbol_id, 
            func.max(Earning.period_date).label('max_date')
        ).filter(Earning.period_date <= target_eval_date).group_by(Earning.symbol_id).subquery()

        eps_filter_subq = db.query(Earning.symbol_id).join(
            latest_earnings_subq,
            (Earning.symbol_id == latest_earnings_subq.c.symbol_id) & 
            (Earning.period_date == latest_earnings_subq.c.max_date)
        ).filter(Earning.eps_basic > 0).subquery()
        
        query = query.filter(Symbol.id.in_(eps_filter_subq))

    results = query.all()
    
    # We also need Historical prices for sparkline, and ranks, but fetching ranks for hundreds of items one-by-one is N+1 problem.
    # Therefore, let's bulk fetch T4 Relative Rank for ALL of them.
    if not results:
        return []
        
    symbol_ids = [r.Symbol.id for r in results]
    
    # Ranks must be synced with the indicator date
    latest_rank_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    
    ranks = db.query(RelativeRank).filter(
        RelativeRank.date == latest_rank_date,
        RelativeRank.symbol_id.in_(symbol_ids)
    ).all() if latest_rank_date else []
    
    # Map ranks
    rank_map_21 = {r.symbol_id: r.percent_rank for r in ranks if r.indicator_name == 'rs_ratio_21'}
    rank_map_63 = {r.symbol_id: r.percent_rank for r in ranks if r.indicator_name == 'rs_ratio_63'}
    
    # Sparkline data: Need the past 21 days for these symbols
    start_date_sparkline = latest_date_result - timedelta(days=40) # Ensure we get ~21 trading days
    history = db.query(DailyPrice.symbol_id, DailyPrice.close, DailyPrice.date).filter(
        DailyPrice.symbol_id.in_(symbol_ids),
        DailyPrice.date >= start_date_sparkline,
        DailyPrice.date <= latest_date_result
    ).order_by(DailyPrice.date.asc()).all()
    
    history_map = {}
    for h in history:
        if h.symbol_id not in history_map:
            history_map[h.symbol_id] = []
        history_map[h.symbol_id].append(h.close)
        
    # Build response
    out = []
    for sym, ind, dp in results:
        hist_prices = history_map.get(sym.id, [])
        hist_prices = hist_prices[-21:] if len(hist_prices) > 21 else hist_prices
        
        # approximate 1w and 1m using hist_prices
        close_1w = hist_prices[-6] if len(hist_prices) >= 6 else hist_prices[0] if hist_prices else dp.close
        close_1m = hist_prices[-21] if len(hist_prices) >= 21 else hist_prices[0] if hist_prices else dp.close
        
        c_1d = ((dp.close - dp.open) / dp.open * 100) if dp.open else 0.0
        c_1w = ((dp.close - close_1w) / close_1w * 100) if close_1w else 0.0
        c_1m = ((dp.close - close_1m) / close_1m * 100) if close_1m else 0.0
        
        d_21ema = ((dp.close - ind.ema_21) / ind.ema_21 * 100) if ind.ema_21 else 0.0
        
        sparkline_data = []
        if hist_prices:
            m_min, m_max = min(hist_prices), max(hist_prices)
            rng = m_max - m_min
            if rng > 0:
                sparkline_data = [(p - m_min) / rng for p in hist_prices]
            else:
                sparkline_data = [0.5 for _ in hist_prices]
                
        out.append(schemas.ScreenerResultItem(
            id=sym.id,
            ticker=sym.ticker,
            name=sym.name,
            category=sym.category,
            close=dp.close,
            change_pct=c_1d,
            change_1w_pct=c_1w,
            change_1m_pct=c_1m,
            dist_21ema_pct=d_21ema,
            rs_ratio_21_rank=rank_map_21.get(sym.id, 0.0),
            rs_ratio_63_rank=rank_map_63.get(sym.id, 0.0),
            rs_ratio_21=ind.rs_ratio_21,
            rs_ratio_63=ind.rs_ratio_63,
            rs_momentum_21=ind.rs_momentum_21,
            sparkline=sparkline_data,
            vol_surge_21=ind.vol_surge_21,
            adr_pct_21=ind.adr_pct_21,
            dist_sma50_atr=ind.dist_sma50_atr,
            trend_template_ok=ind.trend_template_ok,
            market_cap=ind.market_cap
        ))
        
    # Sort out primarily by 1M change or something, but the frontend can sort. Let's sort by RS 21 rank by default.
    out.sort(key=lambda x: x.rs_ratio_21_rank, reverse=True)
    return out
