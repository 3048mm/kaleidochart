from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, Column as SAColumn
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, re, logging, tomli
from db.database import get_db, engine
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning
from api import schemas

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/ping")
def ping():
    return {"ping": "pong"}

@router.get("/system/info", response_model=schemas.SystemInfoResponse)
def get_system_info():
    """Return current system configuration details, primarily for DB verification."""
    from db import database
    # Access database.engine instead of a stale import
    full_path = str(database.engine.url.database) if database.engine else "unknown"
    db_name = os.path.basename(full_path)
    is_production = db_name == "stocktool.db"
    
    return schemas.SystemInfoResponse(
        db_path=full_path,
        db_name=db_name,
        is_production=is_production
    )

# ============================================================
# Screener Engine: External Config & Dynamic Filter
# ============================================================

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PRESETS_PATH = os.path.join(_PROJECT_ROOT, "data", "screener_presets.toml")

def _load_presets() -> dict:
    """Load screener presets from TOML (re-read on every call for hot-reload)."""
    try:
        with open(_PRESETS_PATH, "rb") as f:
            return tomli.load(f)
    except Exception as e:
        logger.error(f"Failed to load screener presets: {e}")
        return {"rise": [], "fall": []}

# --- Indicator column whitelist (built from SQLAlchemy model) ---
_INDICATOR_COLUMNS: Dict[str, SAColumn] = {}
_INDICATOR_COLUMN_TYPES: Dict[str, str] = {}
for _col_name, _col_obj in Indicator.__table__.columns.items():
    if _col_name in ("id", "symbol_id", "date"):
        continue
    _INDICATOR_COLUMNS[_col_name] = getattr(Indicator, _col_name)
    _python_type = "int" if "Integer" in str(type(_col_obj.type)) or "SmallInteger" in str(type(_col_obj.type)) else "float"
    _INDICATOR_COLUMN_TYPES[_col_name] = _python_type

# Add DailyPrice columns that or functionally like indicators (like market_cap)
_INDICATOR_COLUMNS["market_cap"] = DailyPrice.market_cap
_INDICATOR_COLUMN_TYPES["market_cap"] = "float"

# --- Virtual (computed) columns ---
_VIRTUAL_COLUMNS = {
    "change_intraday_pct": lambda: (DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100,
    "dist_21ema_pct": lambda: (DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100,
    "dist_sma50_pct": lambda: (DailyPrice.close - Indicator.sma_50) / Indicator.sma_50 * 100,
}

# --- Column category mapping for frontend ---
_COLUMN_CATEGORIES = {
    "Price & Trend": ["sma_5", "sma_21", "sma_50", "sma_63", "sma_150", "sma_200",
                      "ema_5", "ema_21", "ema_50", "ema_63", "ema_150", "ema_200",
                      "trend_template_ok", "change_1d_pct", "change_1w_pct", "change_1m_pct",
                      "change_intraday_pct", "dist_21ema_pct", "dist_sma50_pct",
                      "pct_from_63d_high", "pct_from_52w_high"],
    "Volume & Volatility": ["atr_14", "atr_pct_14", "adr_pct_21", "dist_sma50_atr",
                            "td9", "vol_surge_21", "rel_vol_vs_spy_21", "up_down_vol_ratio_50", "vcr"],
    "Momentum & RS": ["relative_strength_spy",
                      "rs_condition_14", "rs_condition_21", "rs_condition_63",
                      "rs_ema_14", "rs_ema_21", "rs_ema_63",
                      "rs_momentum_14", "rs_momentum_21", "rs_momentum_63",
                      "rs_ratio_14", "rs_ratio_21", "rs_ratio_63",
                      "rs_blue_dot", "rs_red_dot"],
    "Fundamentals": ["market_cap"],
}
_COL_TO_CATEGORY = {}
for _cat, _cols in _COLUMN_CATEGORIES.items():
    for _c in _cols:
        _COL_TO_CATEGORY[_c] = _cat

def _resolve_column(name: str):
    """Resolve a filter key to a SQLAlchemy column expression, or None."""
    if name in _INDICATOR_COLUMNS:
        return _INDICATOR_COLUMNS[name]
    if name in _VIRTUAL_COLUMNS:
        return _VIRTUAL_COLUMNS[name]()
    return None

def _apply_filter(query, key: str, value, db: Session, latest_date):
    """Apply a single filter key=value to a query. Returns modified query or original on failure."""
    try:
        # Rank filters: min_<col>_rank / max_<col>_rank
        rank_match = re.match(r'^(min|max)_(.+)_rank$', key)
        if rank_match:
            direction, indicator = rank_match.group(1), rank_match.group(2)
            _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date).scalar()
            if not _rk_date:
                return query
            col_attr = getattr(RelativeRank, indicator, None)
            if col_attr is None:
                return query
            rank_subq = db.query(
                RelativeRank.symbol_id,
                col_attr.label('rank_val')
            ).filter(
                RelativeRank.date == _rk_date
            ).subquery(name=f"rank_{indicator}_{direction}")
            query = query.join(rank_subq, Symbol.id == rank_subq.c.symbol_id, isouter=False)
            if direction == 'min':
                query = query.filter(rank_subq.c.rank_val >= float(value))
            else:
                query = query.filter(rank_subq.c.rank_val <= float(value))
            return query

        # Standard min/max filters
        match = re.match(r'^(min|max)_(.+)$', key)
        if match:
            direction, col_name = match.group(1), match.group(2)
            col = _resolve_column(col_name)
            if col is None:
                return query
            if direction == 'min':
                query = query.filter(col >= float(value))
            else:
                query = query.filter(col <= float(value))
            return query

        # Exact match
        col = _resolve_column(key)
        if col is not None:
            query = query.filter(col == float(value))
            return query

    except Exception as e:
        logger.warning(f"Screener filter '{key}={value}' skipped due to error: {e}")
    return query

# --- Expression parser for OR/complex conditions ---
_EXPR_OPS = {
    '>': lambda a, b: a > b,
    '>=': lambda a, b: a >= b,
    '<': lambda a, b: a < b,
    '<=': lambda a, b: a <= b,
    '==': lambda a, b: a == b,
    '!=': lambda a, b: a != b,
}

def _parse_expression_to_filter(expr_str: str):
    """Parse a simple expression string into SQLAlchemy filter clause.
    Supports: column op value [and|or column op value ...]
    Returns a filter clause or None on error.
    """
    try:
        # Tokenize: split by 'and' / 'or' as logical connectors
        parts = re.split(r'\b(and|or)\b', expr_str)
        conditions = []
        connectors = []

        for part in parts:
            part = part.strip()
            if part in ('and', 'or'):
                connectors.append(part)
                continue
            if not part:
                continue

            # Parse "column op value" or "column op column"
            m = re.match(r'^(\w+)\s*(>=|<=|!=|==|>|<)\s*(-?[\d.]+)$', part)
            if m:
                col_name, op, val_str = m.group(1), m.group(2), m.group(3)
                col = _resolve_column(col_name)
                if col is None:
                    logger.warning(f"Expression: unknown column '{col_name}'")
                    return None
                val = float(val_str)
                conditions.append(_EXPR_OPS[op](col, val))
            else:
                # Try column-to-column comparison: "col1 op col2"
                m2 = re.match(r'^(\w+)\s*(>=|<=|!=|==|>|<)\s*(\w+)$', part)
                if not m2:
                    logger.warning(f"Expression parse failed for segment: '{part}'")
                    return None
                col_name_l, op, col_name_r = m2.group(1), m2.group(2), m2.group(3)
                col_l = _resolve_column(col_name_l)
                col_r = _resolve_column(col_name_r)
                if col_l is None:
                    logger.warning(f"Expression: unknown column '{col_name_l}'")
                    return None
                if col_r is None:
                    logger.warning(f"Expression: unknown column '{col_name_r}'")
                    return None
                conditions.append(_EXPR_OPS[op](col_l, col_r))

        if not conditions:
            return None

        # Combine with connectors
        result = conditions[0]
        for i, conn in enumerate(connectors):
            next_cond = conditions[i + 1] if i + 1 < len(conditions) else None
            if next_cond is None:
                break
            if conn == 'or':
                result = or_(result, next_cond)
            else:
                result = and_(result, next_cond)

        return result
    except Exception as e:
        logger.error(f"Expression parse error for '{expr_str}': {e}")
        return None


# Dependency to get a direct DB Session for FastAPI
def get_api_db():
    with get_db() as db:
        yield db

# Dependency for user data DB
def get_api_user_db():
    from db.database_user import get_user_db
    with get_user_db() as user_db:
        yield user_db

# ============================================================
# Screener Preset / Meta Endpoints
# ============================================================

@router.get("/screener/presets", response_model=schemas.ScreenerPresetsResponse)
def get_screener_presets():
    """Return all screener presets from the external TOML config."""
    raw = _load_presets()
    def _to_items(raw_list):
        items = []
        for p in raw_list:
            items.append(schemas.ScreenerPresetItem(
                id=p.get("id", ""),
                name=p.get("name", ""),
                subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", ""),
                filters=p.get("filters", {}),
                expression=p.get("expression"),
                special=p.get("special"),
            ))
        return items
    return schemas.ScreenerPresetsResponse(
        rise=_to_items(raw.get("rise", [])),
        fall=_to_items(raw.get("fall", [])),
    )

@router.get("/screener/meta", response_model=schemas.ScreenerMetaResponse)
def get_screener_meta(db: Session = Depends(get_api_db)):
    """Return all available filter columns (T3 Indicator + virtual) and T4 rank indicators."""
    columns = []
    for col_name, col_type in _INDICATOR_COLUMN_TYPES.items():
        cat = _COL_TO_CATEGORY.get(col_name, "Other")
        label = col_name.replace("_", " ").title()
        step = 1.0 if col_type == "int" else 0.1
        columns.append(schemas.ScreenerColumnMeta(name=col_name, label=label, category=cat, type=col_type, step=step))

    # Virtual columns
    virtual = []
    for vc_name in _VIRTUAL_COLUMNS.keys():
        cat = _COL_TO_CATEGORY.get(vc_name, "Price & Trend")
        label = vc_name.replace("_", " ").title()
        virtual.append(schemas.ScreenerColumnMeta(name=vc_name, label=label, category=cat, type="float", step=0.1))

    # T4 rank indicator names (Wide schema columns)
    rank_names = [
        'relative_strength_spy',
        'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
        'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
        'rs_condition_14', 'rs_condition_21', 'rs_condition_63',
        'rs_roc_ema_14', 'rs_roc_ema_21', 'rs_roc_ema_63'
    ]

    return schemas.ScreenerMetaResponse(columns=columns, rank_indicators=rank_names, virtual_columns=virtual)


@router.get("/symbols", response_model=List[schemas.SymbolResponse])
def get_symbols(db: Session = Depends(get_api_db)):
    """
    T1: Get all registered active symbols
    """
    symbols = db.query(Symbol).filter(Symbol.active == 1).order_by(Symbol.category, Symbol.ticker).all()
    return symbols

@router.get("/chart/{symbol_id}")
def get_chart_data(symbol_id: int, db: Session = Depends(get_api_db)):
    """T2+T3: Get combined daily prices and indicators for rendering charts (High speed)"""
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id, Symbol.active == 1).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    # Fetch all prices
    prices = db.query(DailyPrice).filter(DailyPrice.symbol_id == symbol_id).order_by(DailyPrice.date.asc()).all()
    if not prices:
        return JSONResponse(content={"data": [], "themes": []})

    dates = [p.date for p in prices]
    date_strs = [d.strftime('%Y-%m-%d') for d in dates]

    # Indicators
    # Fetch related indicators (More efficient query without large IN clause)
    start_date = prices[0].date
    end_date = prices[-1].date
    indicators = db.query(Indicator).filter(
        Indicator.symbol_id == symbol_id,
        Indicator.date >= start_date,
        Indicator.date <= end_date
    ).all()
    ind_map = {i.date.strftime('%Y-%m-%d'): i for i in indicators}
    
    # RS Ranks (Optimized range query for all timeframes and types)
    ranks = db.query(RelativeRank).filter(
        RelativeRank.symbol_id == symbol_id,
        RelativeRank.date >= start_date,
        RelativeRank.date <= end_date
    ).all()
    
    # Organize ranks by date and indicator. 
    # If multiple groups exist for a symbol, prioritize '個別' or just take the latest found.
    rank_indicators = [
        'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63',
        'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63',
        'rs_condition_14', 'rs_condition_21', 'rs_condition_63',
        'relative_strength_spy'
    ]
    rank_map_nested = {}
    for r in ranks:
        ds = r.date.strftime('%Y-%m-%d')
        if ds not in rank_map_nested:
            rank_map_nested[ds] = {}
        
        is_kobetsu = (r.group_name == '個別')
        for ind_name in rank_indicators:
            val = getattr(r, ind_name, None)
            if val is not None:
                if is_kobetsu or ind_name not in rank_map_nested[ds]:
                    rank_map_nested[ds][ind_name] = val

    # Themes
    theme_meta = []
    if symbol.tags:
        tag_list = [t.strip() for t in symbol.tags.split(',') if t.strip()]
        tag_themes = db.query(Symbol).filter(Symbol.ticker.in_(tag_list)).all()
        for t in tag_themes:
            theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})
    
    tc_themes = db.query(Symbol).join(ThemeConstituent, Symbol.id == ThemeConstituent.theme_id).filter(ThemeConstituent.symbol_id == symbol_id).all()
    for t in tc_themes:
        if not any(tm['ticker'] == t.ticker for tm in theme_meta):
            theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})

    # Optimized mapping to dict (bypasses Pydantic validation for speed)
    chart_data = []
    for p in prices:
        d_str = str(p.date)
        ind = ind_map.get(d_str)
        point = {
            "time": d_str, "open": p.open, "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
            "market_cap": p.market_cap
        }
        if ind:
            point.update({
                "sma_5": ind.sma_5,
                "sma_21": ind.sma_21, "sma_50": ind.sma_50, "sma_63": ind.sma_63,
                "sma_150": ind.sma_150, "sma_200": ind.sma_200,
                "ema_5": ind.ema_5, "ema_21": ind.ema_21, "ema_50": ind.ema_50,
                "ema_63": ind.ema_63, "ema_150": ind.ema_150, "ema_200": ind.ema_200,
                "td9": ind.td9, "atr_14": ind.atr_14, "atr_pct_14": ind.atr_pct_14,
                "adr_pct_21": ind.adr_pct_21, "dist_sma50_atr": ind.dist_sma50_atr,
                "change_1d_pct": ind.change_1d_pct,
                "change_1w_pct": ind.change_1w_pct,
                "change_1m_pct": ind.change_1m_pct,
                "relative_strength_spy": ind.relative_strength_spy,
                "rs_condition_14": ind.rs_condition_14,
                "rs_condition_21": ind.rs_condition_21,
                "rs_condition_63": ind.rs_condition_63,
                "rs_ema_14": ind.rs_ema_14,
                "rs_ema_21": ind.rs_ema_21,
                "rs_ema_63": ind.rs_ema_63,
                "rs_momentum_14": ind.rs_momentum_14,
                "rs_momentum_21": ind.rs_momentum_21,
                "rs_momentum_63": ind.rs_momentum_63,
                "rs_ratio_14": ind.rs_ratio_14,
                "rs_ratio_21": ind.rs_ratio_21,
                "rs_ratio_63": ind.rs_ratio_63,
                "rs_roc_ema_14": ind.rs_roc_ema_14,
                "rs_roc_ema_21": ind.rs_roc_ema_21,
                "rs_roc_ema_63": ind.rs_roc_ema_63,
                "vol_surge_21": ind.vol_surge_21,
                "rel_vol_vs_spy_21": ind.rel_vol_vs_spy_21,
                "up_down_vol_ratio_50": ind.up_down_vol_ratio_50,
                "pct_from_63d_high": ind.pct_from_63d_high,
                "pct_from_52w_high": ind.pct_from_52w_high,
                "rs_blue_dot": ind.rs_blue_dot, "rs_red_dot": ind.rs_red_dot,
                "vcr": ind.vcr, "trend_template_ok": ind.trend_template_ok,
                "bb_upper": (ind.sma_21 + 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
                "bb_lower": (ind.sma_21 - 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
            })
            # Include all ranks for RRG minimaps
            r_data = rank_map_nested.get(d_str, {})
            for r_name in rank_indicators:
                point[f"rank_{r_name}"] = r_data.get(r_name)
            
            # Include legacy key for RsLineChart
            point["rs_ratio"] = r_data.get("rs_ratio_21")
        
        chart_data.append(point)

    import json
    from fastapi import Response

    # Custom encoder/cleaner to handle NaN/Inf which are invalid in standard JSON
    def clean_data(obj):
        if isinstance(obj, float):
            if obj != obj or obj == float('inf') or obj == float('-inf'):
                return None
        return obj

    # Apply cleaning to the entire data structure
    cleaned_data = {
        "metadata": {"id": symbol.id, "ticker": symbol.ticker, "name": symbol.name, "category": symbol.category},
        "themes": theme_meta,
        "data": [
            {k: clean_data(v) for k, v in point.items()}
            for point in chart_data
        ]
    }

    return Response(
        content=json.dumps(cleaned_data, allow_nan=False),
        media_type="application/json"
    )

@router.get("/earnings/{symbol_id}", response_model=List[schemas.EarningResponse])
def get_earnings_data(symbol_id: int, db: Session = Depends(get_api_db)):
    """
    T6: Get quarterly earnings fundamental data for a symbol.
    """
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    earnings = db.query(Earning).filter(Earning.symbol_id == symbol_id).order_by(Earning.period_date.desc()).all()
    
    resp = []
    for e in earnings:
        resp.append(schemas.EarningResponse(
            period_date=str(e.period_date),
            eps_basic=e.eps_basic,
            eps_diluted=e.eps_diluted,
            revenue=e.revenue,
            net_income=e.net_income
        ))
    return resp


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
        'relative_strength_spy', 
        'rs_ratio_14', 'rs_ratio_21', 'rs_ratio_63', 
        'rs_momentum_14', 'rs_momentum_21', 'rs_momentum_63', 
        'rs_condition_14', 'rs_condition_21', 'rs_condition_63',
        'rs_roc_ema_14', 'rs_roc_ema_21', 'rs_roc_ema_63'
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

# --- Helper functions for dashboard & group pages ---

def _get_sparkline_data(db: Session, sym_id: int, target_date: str, period: int = 21):
    col_attr = getattr(RelativeRank, f"rs_ratio_{period}", None)
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
                     rank_val_14: float = 0.0, rank_val_mom: float = 0.0, rank_val_mom63: float = 0.0):
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
        rs_ratio_21_rank=float(rank_val_21 or 0.0),
        rs_ratio_63_rank=float(rank_val_63 or 0.0),
        rs_ratio_14_rank=float(rank_val_14 or 0.0),
        rs_momentum_21_rank=float(rank_val_mom or 0.0),
        rs_momentum_63_rank=float(rank_val_mom63 or 0.0),
        rs_ratio_21=ind.rs_ratio_21 if ind else None,
        rs_ratio_63=ind.rs_ratio_63 if ind else None,
        rs_momentum_21=ind.rs_momentum_21 if ind else None
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
            relative_strength_spy=i.relative_strength_spy if i else None,
            rs_ema_14=i.rs_ema_14 if i else None,
            rs_ema_21=i.rs_ema_21 if i else None,
            rs_ema_63=i.rs_ema_63 if i else None,
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
    
    return schemas.EtfFeatureItem(
        id=sym.id, ticker=sym.ticker, name=sym.name, close=float(dp.close or 0.0),
        change_1d_pct=float(change_1d_pct or 0.0), change_1w_pct=float(change_1w_pct or 0.0),
        change_1m_pct=float(change_1m_pct or 0.0), change_1y_pct=float(change_1y_pct or 0.0),
        dist_sma5_pct=float(dist_sma5_pct or 0.0), dist_sma21_pct=float(dist_sma21_pct or 0.0),
        dist_sma63_pct=float(dist_sma63_pct or 0.0), sma21_sma63_pct=float(sma21_sma63_pct or 0.0),
        rs_ratio_14=ind.rs_ratio_14 if ind else None,
        rs_ratio_21=ind.rs_ratio_21 if ind else None,
        rs_ratio_63=ind.rs_ratio_63 if ind else None,
        rs14_sparkline=rs14_spark,
        rs21_sparkline=rs21_spark,
        rs63_sparkline=rs63_spark,
        rank_rs_ratio_14=get_rank(sym.id, 'rs_ratio_14', target_date),
        rank_rs_ratio_21=get_rank(sym.id, 'rs_ratio_21', target_date),
        rank_rs_ratio_63=get_rank(sym.id, 'rs_ratio_63', target_date),
        chart_data=chart_data
    )

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
    symbols = db.query(Symbol).filter(Symbol.active == 1).all()
    sym_dict = {s.id: s for s in symbols}
    
    # Get Prices for target date
    prices = db.query(DailyPrice).filter(DailyPrice.date == target_date).all()
    price_dict = {p.symbol_id: p for p in prices}

    # Get Relative Ranks for target date (highly optimized single query!)
    ranks = db.query(
        RelativeRank.symbol_id, 
        RelativeRank.rs_ratio_14, 
        RelativeRank.rs_ratio_21, 
        RelativeRank.rs_ratio_63
    ).filter(
        RelativeRank.date == target_date
    ).all()
    rank_14_dict = {r.symbol_id: r.rs_ratio_14 for r in ranks if r.rs_ratio_14 is not None}
    rank_21_dict = {r.symbol_id: r.rs_ratio_21 for r in ranks if r.rs_ratio_21 is not None}
    rank_63_dict = {r.symbol_id: r.rs_ratio_63 for r in ranks if r.rs_ratio_63 is not None}

    for sym_id, s in sym_dict.items():
        if sym_id not in price_dict:
            continue
            
        dp = price_dict[sym_id]
        r14_rank = rank_14_dict.get(sym_id, 0.0) or 0.0
        r21_rank = rank_21_dict.get(sym_id, 0.0) or 0.0
        r63_rank = rank_63_dict.get(sym_id, 0.0) or 0.0
        
        if s.category == "市場":
            resp.indices.append(_build_panel_item(db, s, dp, r21_rank, r63_rank, target_date, r14_rank))
        elif s.category == "指標":
            if s.ticker == "SPY":
                resp.spy_feature = _build_etf_feature(db, s, dp, target_date)
            else:
                resp.leading.append(_build_leading_item(db, s, dp, target_date))
        elif s.category == "セクタ":
            resp.sectors.append(_build_panel_item(db, s, dp, r21_rank, r63_rank, target_date, r14_rank))
        elif s.category == "テーマ":
            item = _build_panel_item(db, s, dp, r21_rank, r63_rank, target_date, r14_rank)
            resp.themes_top.append(item)
            
    # Sort and slice
    resp.sectors.sort(key=lambda x: x.intensity_score, reverse=True)
    themes_sorted = sorted(resp.themes_top, key=lambda x: x.intensity_score, reverse=True)
    
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
            relative_strength_spy=i.relative_strength_spy if i else None,
            rs_ema_14=i.rs_ema_14 if i else None,
            rs_ema_21=i.rs_ema_21 if i else None,
            rs_ema_63=i.rs_ema_63 if i else None,
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
    mappings = db.query(ThemeConstituent).filter(ThemeConstituent.theme_id == symbol_id).all()
    constituent_symbols = [db.query(Symbol).filter(Symbol.id == m.symbol_id).first() for m in mappings]
    constituent_symbols = [s for s in constituent_symbols if s is not None]

    # Tag-based lookup for ETF themes
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
        c_rs_spark = _get_sparkline_data(db, c_id, str(c_dp.date), 21)

        # short history for RRG
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
            ds = str(h.date)
            i = c_ind_dict.get(ds)
            c_chart_data.append(schemas.ChartDataPoint(
                time=ds, open=h.open or 0.0, high=h.high or 0.0, low=h.low or 0.0, close=h.close,
                volume=int(round(h.volume)) if h.volume else 0, 
                relative_strength_spy=i.relative_strength_spy if i else None,
                rs_ema_14=i.rs_ema_14 if i else None,
                rs_ema_21=i.rs_ema_21 if i else None,
                rs_ema_63=i.rs_ema_63 if i else None,
                rs_ratio_14=i.rs_ratio_14 if i else None, rs_ratio_21=i.rs_ratio_21 if i else None,
                rs_ratio_63=i.rs_ratio_63 if i else None, rs_momentum_14=i.rs_momentum_14 if i else None,
                rs_momentum_21=i.rs_momentum_21 if i else None, rs_momentum_63=i.rs_momentum_63 if i else None,
            ))

        # Fetch ranks for constituent (highly optimized single query!)
        r_row = db.query(
            RelativeRank.rs_ratio_14, 
            RelativeRank.rs_ratio_21, 
            RelativeRank.rs_ratio_63, 
            RelativeRank.rs_momentum_21, 
            RelativeRank.rs_momentum_63
        ).filter(
            RelativeRank.symbol_id == c_id, 
            RelativeRank.date == c_dp.date
        ).first()
        
        rank_14 = (r_row.rs_ratio_14 or 0.0) if r_row else 0.0
        rank_21 = (r_row.rs_ratio_21 or 0.0) if r_row else 0.0
        rank_63 = (r_row.rs_ratio_63 or 0.0) if r_row else 0.0
        rank_mom21 = (r_row.rs_momentum_21 or 0.0) if r_row else 0.0
        rank_mom63 = (r_row.rs_momentum_63 or 0.0) if r_row else 0.0

        constituents.append(schemas.ThemeConstituentItem(
            id=c_id, ticker=c_sym.ticker, name=c_sym.name,
            close=c_dp.close,
            change_1d_pct=c_1d,
            change_1w_pct=c_1w,
            change_1m_pct=c_1m,
            rs_ratio_14=c_rs14,
            rs_ratio_21=c_rs21,
            rs_ratio_63=c_rs63,
            rs_momentum_21=c_rsmom21,
            rank_rs_ratio_14=rank_14,
            rank_rs_ratio_21=rank_21,
            rank_rs_ratio_63=rank_63,
            rank_rs_momentum_21=rank_mom21,
            rank_rs_momentum_63=rank_mom63,
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
        sma21_sma63_pct=sma21_sma63, rs_ratio_14=ind.rs_ratio_14 if ind else None,
        rs_ratio_21=ind.rs_ratio_21 if ind else None, rs_ratio_63=ind.rs_ratio_63 if ind else None,
        rs_momentum_14=ind.rs_momentum_14 if ind else None, rs_momentum_21=ind.rs_momentum_21 if ind else None,
        rs_momentum_63=ind.rs_momentum_63 if ind else None, rs_condition_14=ind.rs_condition_14 if ind else None,
        rs_condition_21=ind.rs_condition_21 if ind else None, rs_condition_63=ind.rs_condition_63 if ind else None,
        adr_pct_21=ind.adr_pct_21 if ind else None, dist_sma50_atr=ind.dist_sma50_atr if ind else None,
        rs14_sparkline=rs14_spark, rs21_sparkline=rs21_spark, rs63_sparkline=rs63_spark,
        rank_rs_ratio_14=theme_r.rs_ratio_14 if theme_r else None,
        rank_rs_ratio_21=theme_r.rs_ratio_21 if theme_r else None,
        rank_rs_ratio_63=theme_r.rs_ratio_63 if theme_r else None,
        rank_rs_momentum_14=theme_r.rs_momentum_14 if theme_r else None,
        rank_rs_momentum_21=theme_r.rs_momentum_21 if theme_r else None,
        rank_rs_momentum_63=theme_r.rs_momentum_63 if theme_r else None,
        rank_rs_condition_14=theme_r.rs_condition_14 if theme_r else None,
        rank_rs_condition_21=theme_r.rs_condition_21 if theme_r else None,
        rank_rs_condition_63=theme_r.rs_condition_63 if theme_r else None,
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
            RelativeRank.rs_ratio_14, 
            RelativeRank.rs_ratio_21, 
            RelativeRank.rs_ratio_63, 
            RelativeRank.rs_momentum_21, 
            RelativeRank.rs_momentum_63
        ).filter(
            RelativeRank.date == target_date, 
            RelativeRank.symbol_id.in_(child_ids)
        ).all()
        c_rank_14_dict = {r.symbol_id: r.rs_ratio_14 for r in c_ranks if r.rs_ratio_14 is not None}
        c_rank_21_dict = {r.symbol_id: r.rs_ratio_21 for r in c_ranks if r.rs_ratio_21 is not None}
        c_rank_63_dict = {r.symbol_id: r.rs_ratio_63 for r in c_ranks if r.rs_ratio_63 is not None}
        c_rank_mom_dict = {r.symbol_id: r.rs_momentum_21 for r in c_ranks if r.rs_momentum_21 is not None}
        c_rank_mom63_dict = {r.symbol_id: r.rs_momentum_63 for r in c_ranks if r.rs_momentum_63 is not None}

        c_symbols = db.query(Symbol).filter(Symbol.id.in_(child_ids)).all()
        for cs in c_symbols:
            if cs.id in c_price_dict:
                constituents.append(_build_panel_item(
                    db, cs, c_price_dict[cs.id], 
                    c_rank_21_dict.get(cs.id, 0.0), 
                    c_rank_63_dict.get(cs.id, 0.0), 
                    target_date,
                    rank_val_14=c_rank_14_dict.get(cs.id, 0.0),
                    rank_val_mom=c_rank_mom_dict.get(cs.id, 0.0),
                    rank_val_mom63=c_rank_mom63_dict.get(cs.id, 0.0)
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

@router.get("/screener/dashboard", response_model=schemas.ScreenerDashboardResponse)
def get_screener_dashboard(
    db: Session = Depends(get_api_db),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD")
):
    """Screener dashboard: builds each preset card from screener_presets.toml."""
    # Determine the date to use
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()
        
    if not latest_date_result:
        return schemas.ScreenerDashboardResponse(rise=[], fall=[])

    # Get previous date for RRG transition check
    previous_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date < latest_date_result).scalar()

    # Base query wrapper function
    def q_base():
        return db.query(Symbol.id, Symbol.ticker, Symbol.name, DailyPrice.open, DailyPrice.close, Indicator.change_1d_pct).join(
            Indicator, Symbol.id == Indicator.symbol_id
        ).join(
            DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
        ).filter(Symbol.active == True, Symbol.category.in_(["テーマ", "個別"]), Indicator.date == latest_date_result)

    def fetch_top_8(query):
        results = query.limit(8).all()
        items = []
        for r in results:
            chg = r.change_1d_pct if r.change_1d_pct is not None else 0.0
            items.append(schemas.ScreenerDashboardItem(id=r.id, ticker=r.ticker, name=r.name, change_pct=chg))
        return items

    def _build_preset_query(preset_def: dict):
        """Build a query from a single preset definition (TOML dict)."""
        q = q_base()

        # Apply special logic
        special = preset_def.get("special")
        if special:
            if special in ("rrg_leading_in", "rrg_lagging_in", "rrg_improving_in") and previous_date_result:
                intensity_threshold = preset_def.get("rrg_intensity_threshold", 0.0)
                intensity_sq = intensity_threshold * intensity_threshold
                
                IndPrev = aliased(Indicator)
                q = q.join(IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result))
                
                if special == "rrg_leading_in":
                    q = q.filter(
                        Indicator.rs_ratio_21 > 0, Indicator.rs_momentum_21 > 0,
                        Indicator.rs_momentum_21 > IndPrev.rs_momentum_21,
                        (Indicator.rs_ratio_21 * Indicator.rs_ratio_21 + Indicator.rs_momentum_21 * Indicator.rs_momentum_21) >= intensity_sq,
                        or_(
                            or_(IndPrev.rs_ratio_21 <= 0, IndPrev.rs_momentum_21 <= 0),
                            (IndPrev.rs_ratio_21 * IndPrev.rs_ratio_21 + IndPrev.rs_momentum_21 * IndPrev.rs_momentum_21) < intensity_sq
                        )
                    )
                elif special == "rrg_lagging_in":
                    q = q.filter(
                        Indicator.rs_ratio_21 < 0, Indicator.rs_momentum_21 < 0,
                        or_(IndPrev.rs_ratio_21 >= 0, IndPrev.rs_momentum_21 >= 0)
                    )
                elif special == "rrg_improving_in":
                    q = q.filter(
                        Indicator.rs_ratio_21 < 0, Indicator.rs_momentum_21 > 0,
                        Indicator.rs_momentum_21 > IndPrev.rs_momentum_21,
                        (Indicator.rs_ratio_21 * Indicator.rs_ratio_21 + Indicator.rs_momentum_21 * Indicator.rs_momentum_21) >= intensity_sq,
                        or_(
                            and_(IndPrev.rs_ratio_21 < 0, IndPrev.rs_momentum_21 <= 0),
                            and_(IndPrev.rs_ratio_21 < 0, (IndPrev.rs_ratio_21 * IndPrev.rs_ratio_21 + IndPrev.rs_momentum_21 * IndPrev.rs_momentum_21) < intensity_sq)
                        )
                    )
            elif special == "theme_rs21_gt_63":
                theme_momentum_subq = db.query(Indicator.symbol_id).filter(
                    Indicator.date == latest_date_result,
                    Indicator.rs_ratio_21 > Indicator.rs_ratio_63
                ).subquery()
                stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
                    ThemeConstituent.theme_id.in_(theme_momentum_subq)
                ).subquery()
                q = q.filter(
                    or_(
                        (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq)),
                        (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq))
                    )
                )
            elif special == "theme_rs_rank_21_gt_63":
                _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
                if _rk_date:
                    theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
                        RelativeRank.date == _rk_date,
                        RelativeRank.group_name == "テーマ",
                        RelativeRank.rs_ratio_21 > RelativeRank.rs_ratio_63
                    ).subquery()
                    stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
                        ThemeConstituent.theme_id.in_(theme_momentum_subq)
                    ).subquery()
                    q = q.filter(
                        or_(
                            (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq)),
                            (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq))
                        )
                    )

        # Apply expression-based filters (OR conditions etc.)
        expression = preset_def.get("expression")
        if expression:
            expr_filter = _parse_expression_to_filter(expression)
            if expr_filter is not None:
                q = q.filter(expr_filter)
            else:
                logger.warning(f"Preset '{preset_def.get('id')}': expression parse failed, skipping expression.")

        # Apply standard AND filters
        filters = preset_def.get("filters", {})
        for key, value in filters.items():
            q = _apply_filter(q, key, value, db, latest_date_result)

        # Default sort: by 1Day% (Prev Close base) descending
        q = q.order_by(desc(Indicator.change_1d_pct))
        return q

    # Load presets from TOML
    presets = _load_presets()
    
    rise_categories = []
    fall_categories = []

    for p in presets.get("rise", []):
        try:
            q = _build_preset_query(p)
            rise_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Check"), items=fetch_top_8(q)
            ))
        except Exception as e:
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")

    for p in presets.get("fall", []):
        try:
            q = _build_preset_query(p)
            fall_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Warning"), items=fetch_top_8(q)
            ))
        except Exception as e:
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")

    return schemas.ScreenerDashboardResponse(rise=rise_categories, fall=fall_categories)


@router.get("/screener", response_model=List[schemas.ScreenerResultItem])
def get_screener(
    request: Request,
    db: Session = Depends(get_api_db),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD"),
    rrg_leading_in: bool = Query(False),
    rrg_lagging_in: bool = Query(False),
    rrg_improving_in: bool = Query(False),
    rrg_intensity_threshold: float = Query(0.0),
    rs_rank_21_gt_63: bool = Query(False),
    theme_rs21_gt_63: bool = Query(False),
    theme_rs_rank_21_gt_63: bool = Query(False),
    rs_rank_14_gt_21: bool = Query(False),
    theme_rs14_gt_21: bool = Query(False),
    theme_rs_rank_14_gt_21: bool = Query(False),
    require_positive_eps: bool = Query(False),
    expression: Optional[str] = Query(None, description="Expression filter string"),
):
    """Dynamic screener: all min_*/max_*/exact filters via query params, routed through the generic engine."""
    # Determine the date to use for indicators
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()
        
    if not latest_date_result:
        return []

    # Get previous date for transition check
    previous_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date < latest_date_result).scalar()

    # Base query for active symbols
    query = db.query(Symbol, Indicator, DailyPrice).join(
        Indicator, Symbol.id == Indicator.symbol_id
    ).join(
        DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
    ).filter(Symbol.active == 1, Symbol.category.in_(["テーマ", "個別"]))

    # Filter by the determined date
    query = query.filter(Indicator.date == latest_date_result)

    # ---- Dynamic filters from query params ----
    # Collect all query params except reserved ones
    _RESERVED_PARAMS = {"target_date", "rrg_leading_in", "rrg_lagging_in", "rrg_improving_in",
                        "rrg_intensity_threshold",
                        "rs_rank_21_gt_63", "theme_rs21_gt_63", "theme_rs_rank_21_gt_63",
                        "rs_rank_14_gt_21", "theme_rs14_gt_21", "theme_rs_rank_14_gt_21",
                        "require_positive_eps", "preset", "expression"}
    for key, value in request.query_params.items():
        if key in _RESERVED_PARAMS:
            continue
        if not value:
            continue
        query = _apply_filter(query, key, value, db, latest_date_result)

    # ---- Expression filter ----
    if expression:
        expr_filter = _parse_expression_to_filter(expression)
        if expr_filter is not None:
            query = query.filter(expr_filter)

    # ---- Special boolean filters ----
    if rs_rank_21_gt_63:
        _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
        if _rk_date:
            rank_subq = db.query(RelativeRank.symbol_id).filter(
                RelativeRank.date == _rk_date,
                RelativeRank.rs_ratio_21 > RelativeRank.rs_ratio_63
            ).subquery()
            query = query.filter(Symbol.id.in_(rank_subq))

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

    if theme_rs21_gt_63:
        theme_momentum_subq = db.query(Indicator.symbol_id).filter(
            Indicator.date == latest_date_result,
            Indicator.rs_ratio_21 > Indicator.rs_ratio_63
        ).subquery()
        stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
            ThemeConstituent.theme_id.in_(theme_momentum_subq.select())
        ).subquery()
        query = query.filter(
            or_(
                (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq.select())),
                (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq.select()))
            )
        )

    if theme_rs_rank_21_gt_63:
        _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
        if _rk_date:
            theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
                RelativeRank.date == _rk_date,
                RelativeRank.group_name == "テーマ",
                RelativeRank.rs_ratio_21 > RelativeRank.rs_ratio_63
            ).subquery()
            stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
                ThemeConstituent.theme_id.in_(theme_momentum_subq.select())
            ).subquery()
            query = query.filter(
                or_(
                    (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq.select())),
                    (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq.select()))
                )
            )
    if rs_rank_14_gt_21:
        _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
        if _rk_date:
            rank_subq = db.query(RelativeRank.symbol_id).filter(
                RelativeRank.date == _rk_date,
                RelativeRank.rs_ratio_14 > RelativeRank.rs_ratio_21
            ).subquery()
            query = query.filter(Symbol.id.in_(rank_subq))

    if theme_rs14_gt_21:
        theme_momentum_subq = db.query(Indicator.symbol_id).filter(
            Indicator.date == latest_date_result,
            Indicator.rs_ratio_14 > Indicator.rs_ratio_21
        ).subquery()
        stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
            ThemeConstituent.theme_id.in_(theme_momentum_subq.select())
        ).subquery()
        query = query.filter(
            or_(
                (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq.select())),
                (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq.select()))
            )
        )

    if theme_rs_rank_14_gt_21:
        _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
        if _rk_date:
            theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
                RelativeRank.date == _rk_date,
                RelativeRank.group_name == "テーマ",
                RelativeRank.rs_ratio_14 > RelativeRank.rs_ratio_21
            ).subquery()
            stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
                ThemeConstituent.theme_id.in_(theme_momentum_subq.select())
            ).subquery()
            query = query.filter(
                or_(
                    (Symbol.category == "テーマ") & (Symbol.id.in_(theme_momentum_subq.select())),
                    (Symbol.category == "個別") & (Symbol.id.in_(stock_in_leading_themes_subq.select()))
                )
            )

    # RRG Transitions (Leading/Lagging/Improving In)
    if (rrg_leading_in or rrg_lagging_in or rrg_improving_in) and previous_date_result:
        IndPrev = aliased(Indicator)
        query = query.join(IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result))
        
        intensity_sq = rrg_intensity_threshold * rrg_intensity_threshold
        rrg_conds = []
        if rrg_leading_in:
            rrg_conds.append(
                and_(
                    Indicator.rs_ratio_21 > 0, Indicator.rs_momentum_21 > 0,
                    Indicator.rs_momentum_21 > IndPrev.rs_momentum_21,
                    (Indicator.rs_ratio_21 * Indicator.rs_ratio_21 + Indicator.rs_momentum_21 * Indicator.rs_momentum_21) >= intensity_sq,
                    or_(
                        or_(IndPrev.rs_ratio_21 <= 0, IndPrev.rs_momentum_21 <= 0),
                        (IndPrev.rs_ratio_21 * IndPrev.rs_ratio_21 + IndPrev.rs_momentum_21 * IndPrev.rs_momentum_21) < intensity_sq
                    )
                )
            )
        if rrg_lagging_in:
            rrg_conds.append(
                and_(
                    Indicator.rs_ratio_21 < 0, Indicator.rs_momentum_21 < 0,
                    or_(IndPrev.rs_ratio_21 >= 0, IndPrev.rs_momentum_21 >= 0)
                )
            )
        if rrg_improving_in:
            rrg_conds.append(
                and_(
                    Indicator.rs_ratio_21 < 0, Indicator.rs_momentum_21 > 0,
                    Indicator.rs_momentum_21 > IndPrev.rs_momentum_21,
                    (Indicator.rs_ratio_21 * Indicator.rs_ratio_21 + Indicator.rs_momentum_21 * Indicator.rs_momentum_21) >= intensity_sq,
                    or_(
                        and_(IndPrev.rs_ratio_21 < 0, IndPrev.rs_momentum_21 <= 0),
                        and_(IndPrev.rs_ratio_21 < 0, (IndPrev.rs_ratio_21 * IndPrev.rs_ratio_21 + IndPrev.rs_momentum_21 * IndPrev.rs_momentum_21) < intensity_sq)
                    )
                )
            )
            
        if rrg_conds:
            query = query.filter(or_(*rrg_conds))

    results = query.all()
    
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
    rank_map_21 = {r.symbol_id: r.rs_ratio_21 for r in ranks if r.rs_ratio_21 is not None}
    rank_map_63 = {r.symbol_id: r.rs_ratio_63 for r in ranks if r.rs_ratio_63 is not None}
    
    # Sparkline data: Need the past 21 days for these symbols
    start_date_sparkline = latest_date_result - timedelta(days=40)
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
        
        close_1w = hist_prices[-6] if len(hist_prices) >= 6 else hist_prices[0] if hist_prices else dp.close
        close_1m = hist_prices[-21] if len(hist_prices) >= 21 else hist_prices[0] if hist_prices else dp.close
        
        c_1d = ind.change_1d_pct if ind.change_1d_pct is not None else 0.0
        c_1w = ind.change_1w_pct if ind.change_1w_pct is not None else 0.0
        c_1m = ind.change_1m_pct if ind.change_1m_pct is not None else 0.0
        
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
            market_cap=dp.market_cap,
            up_down_vol_ratio_50=ind.up_down_vol_ratio_50,
            rs_blue_dot=ind.rs_blue_dot,
            rs_red_dot=ind.rs_red_dot,
            vcr=ind.vcr
        ))
        
    out.sort(key=lambda x: x.rs_ratio_21_rank, reverse=True)
    return out


# ============================================================
# Watchlist Endpoints
# ============================================================

from api.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    remove_bulk_from_watchlist,
    update_watchlist_entry_date,
    clear_removed,
    get_watchlist,
    get_watchlist_tickers,
)


@router.get("/watchlist", response_model=schemas.WatchlistResponse)
def api_get_watchlist(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_api_db),
    user_db: Session = Depends(get_api_user_db)
):
    """Get all watchlist items (active + removed) with computed metrics."""
    data = get_watchlist(db, user_db)
    
    # Trigger background earnings date update if any active symbols have expired dates
    active_tickers = [item["ticker"] for item in data.get("active", [])]
    if active_tickers:
        from datetime import date
        from pipeline.utils import get_expired_earnings_date_tickers, update_earnings_dates_sync
        
        expired_tickers = get_expired_earnings_date_tickers(db, active_tickers, date.today())
        if expired_tickers:
            background_tasks.add_task(update_earnings_dates_sync, db, expired_tickers, sleep_seconds=1.5)
            
    return schemas.WatchlistResponse(**data)


@router.post("/watchlist", response_model=Optional[schemas.WatchlistItem])
def api_add_to_watchlist(
    req: schemas.WatchlistAddRequest,
    db: Session = Depends(get_api_db),
    user_db: Session = Depends(get_api_user_db)
):
    """Add a symbol to watchlist with 1-hour reactivation rule."""
    wl = add_to_watchlist(db, user_db, ticker=req.ticker, entry_date=req.entry_date)
    if wl is None:
        raise HTTPException(status_code=404, detail="Symbol not found or no price data for the specified date")

    # Return minimal item (without full metrics, for speed)
    sym = db.query(Symbol).filter_by(id=wl.symbol_id).first()
    return schemas.WatchlistItem(
        id=wl.id,
        symbol_id=wl.symbol_id,
        ticker=sym.ticker if sym else "",
        name=sym.name if sym else "",
        entry_date=wl.entry_date.isoformat(),
        entry_price=wl.entry_price,
        latest_close=wl.entry_price,  # approximate on add
        latest_ema_21=0.0,
        gain_pct=0.0,
        max_gain_pct=0.0,
        min_gain_pct=0.0,
        latest_adr_pct=0.0,
        latest_dist_sma50_atr=0.0,
        rs_sparkline=[],
        status=wl.status,
        added_at=wl.added_at.isoformat() if wl.added_at else "",
        removed_at=wl.removed_at.isoformat() if wl.removed_at else None,
        removed_price=wl.removed_price,
    )


# IMPORTANT: Fixed-path routes MUST come before /{ticker} to avoid path collision
@router.delete("/watchlist/removed/clear")
def api_clear_removed(user_db: Session = Depends(get_api_user_db)):
    """Physically delete all 'removed' watchlist records."""
    count = clear_removed(user_db)
    return {"deleted": count}


@router.post("/watchlist/delete-bulk")
def api_remove_bulk(
    req: schemas.WatchlistBulkDeleteRequest,
    db: Session = Depends(get_api_db),
    user_db: Session = Depends(get_api_user_db)
):
    """Remove multiple symbols from watchlist (Logical delete ONLY)."""
    count = remove_bulk_from_watchlist(db, user_db, tickers=req.tickers)
    return {"count": count}


@router.get("/watchlist/tickers")
def api_get_watchlist_tickers(db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    """Get list of active watchlist tickers (lightweight, for star button state)."""
    tickers = get_watchlist_tickers(db, user_db)
    return {"tickers": tickers}


@router.delete("/watchlist/{ticker}")
def api_remove_from_watchlist(ticker: str, db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    """Remove a symbol from watchlist (physical delete within 1hr, otherwise logical)."""
    from db.models_user import Watchlist as WatchlistModel
    symbol_id_check = db.query(Symbol).filter_by(ticker=ticker).first()
    if not symbol_id_check:
        raise HTTPException(status_code=404, detail="Symbol not found")

    wl = remove_from_watchlist(db, user_db, ticker=ticker)
    if wl is None:
        # Physical deletion occurred (or not found in active)
        return {"status": "deleted"}
    return {"status": "removed", "removed_at": wl.removed_at.isoformat() if wl.removed_at else None}


@router.put("/watchlist/{ticker}")
def api_update_watchlist_entry(
    ticker: str,
    req: schemas.WatchlistUpdateRequest,
    db: Session = Depends(get_api_db),
    user_db: Session = Depends(get_api_user_db)
):
    """Update entry_date (and corresponding entry_price) for an active watchlist item."""
    wl = update_watchlist_entry_date(db, user_db, ticker=ticker, new_entry_date=req.entry_date)
    if wl is None:
        raise HTTPException(status_code=404, detail="Active watchlist item not found or no price data for specified date")
    return {
        "ticker": ticker,
        "entry_date": wl.entry_date.isoformat(),
        "entry_price": wl.entry_price,
    }
