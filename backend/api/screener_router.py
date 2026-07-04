"""Screener API router.

Screener engine (preset loading, dynamic filters, boolean special filters,
expression parser) and the /screener* endpoints, extracted from routers.py
(audit D-1). The special-filter logic is being unified with
indicators/screener_filters.py (audit D-2).
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, Column as SAColumn, select
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, re, logging, tomli
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent, Earning
from api import schemas
from api.deps import get_api_db
from api.screener_cross_section import build_cross_section, evaluate_special_filters
from indicators.screener_filters import SPECIAL_FILTER_KEYS

logger = logging.getLogger(__name__)
router = APIRouter()

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
    "change_oc_pct":  lambda: (DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100,
    "dist_ema21_pct": lambda: (DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100,
    "dist_sma50_pct": lambda: (DailyPrice.close - Indicator.sma_50) / Indicator.sma_50 * 100,
}

# --- Rank Column Aliases for mapping old naming rules to new RelativeRank columns ---
_RANK_COLUMN_ALIASES = {
    "rs_ratio_14": "rs_ratio_rank_e14",
    "rs_ratio_21": "rs_ratio_rank_e21",
    "rs_ratio_63": "rs_ratio_rank_e63",
    "rs_momentum_14": "rs_momentum_rank_e14",
    "rs_momentum_21": "rs_momentum_rank_e21",
    "rs_momentum_63": "rs_momentum_rank_e63",
    "rs_trend_14": "rs_trend_rank_s14",
    "rs_trend_21": "rs_trend_rank_s21",
    "rs_trend_63": "rs_trend_rank_s63",
    "rs_roc_ema_14": "rs_roc_ema_rank_e14",
    "rs_roc_ema_21": "rs_roc_ema_rank_e21",
    "rs_roc_ema_63": "rs_roc_ema_rank_e63",
    "rs_value": "rs_value_rank",
}

# --- Column category mapping for frontend ---
_COLUMN_CATEGORIES = {
    "Price & Trend": ["sma_5", "sma_21", "sma_50", "sma_63", "sma_150", "sma_200",
                      "ema_5", "ema_21", "ema_50", "ema_63", "ema_150", "ema_200",
                      "is_trend_template", "change_1d_pct", "change_1w_pct", "change_1m_pct",
                      "change_oc_pct", "dist_ema21_pct", "dist_sma50_pct",
                      "dist_63d_high_pct", "dist_52w_high_pct"],
    "Volume & Volatility": ["atr_14", "atr_pct_14", "adr_pct_21", "sma50_atr_mult",
                            "td9", "vol_surge_21", "vol_surge_rel_spy_21", "up_down_vol_ratio_50", "vcr", "vol_accum_days_5"],
    "Momentum & RS": ["rs_value",
                      "rs_trend_s14", "rs_trend_s21", "rs_trend_s63",
                      "rs_value_e14", "rs_value_e21", "rs_value_e63",
                      "rs_momentum_e14", "rs_momentum_e21", "rs_momentum_e63",
                      "rs_ratio_e14", "rs_ratio_e21", "rs_ratio_e63",
                      "is_rs_blue_dot", "is_rs_red_dot"],
    "Fundamentals": ["market_cap"],
}
_COL_TO_CATEGORY = {}
for _cat, _cols in _COLUMN_CATEGORIES.items():
    for _c in _cols:
        _COL_TO_CATEGORY[_c] = _cat

_COLUMN_LABELS = {
    # Price & Trend
    "sma_5": "5 SMA",
    "sma_21": "21 SMA",
    "sma_50": "50 SMA",
    "sma_63": "63 SMA",
    "sma_150": "150 SMA",
    "sma_200": "200 SMA",
    "ema_5": "5 EMA",
    "ema_21": "21 EMA",
    "ema_50": "50 EMA",
    "ema_63": "63 EMA",
    "ema_150": "150 EMA",
    "ema_200": "200 EMA",
    "is_trend_template": "Trend Template",
    "change_1d_pct": "1D Change %",
    "change_1w_pct": "1W Change %",
    "change_1m_pct": "1M Change %",
    "change_oc_pct": "Open-to-Close %",
    "dist_ema21_pct": "Dist 21EMA %",
    "dist_sma50_pct": "Dist 50SMA %",
    "dist_63d_high_pct": "Dist 63D High %",
    "dist_52w_high_pct": "Dist 52W High %",
    # Volume & Volatility
    "atr_14": "14 ATR",
    "atr_pct_14": "14 ATR %",
    "adr_pct_21": "21 ADR %",
    "sma50_atr_mult": "50/ATR",
    "td9": "TD9",
    "vol_surge_21": "21 Vol Surge",
    "vol_surge_rel_spy_21": "Vol Surge vs SPY",
    "up_down_vol_ratio_50": "U/D Vol Ratio",
    "vcr": "VCR",
    "vol_accum_days_5": "Accum Days (5D)",
    # Momentum & RS
    "rs_value": "RS Value",
    "rs_trend_s14": "RS Trend 14",
    "rs_trend_s21": "RS Trend 21",
    "rs_trend_s63": "RS Trend 63",
    "rs_value_e14": "RS Value e14",
    "rs_value_e21": "RS Value e21",
    "rs_value_e63": "RS Value e63",
    "rs_momentum_e14": "RS Momentum e14",
    "rs_momentum_e21": "RS Momentum e21",
    "rs_momentum_e63": "RS Momentum e63",
    "rs_ratio_e14": "RS Ratio e14",
    "rs_ratio_e21": "RS Ratio e21",
    "rs_ratio_e63": "RS Ratio e63",
    "is_rs_blue_dot": "RS Blue Dot",
    "is_rs_red_dot": "RS Red Dot",
    # Fundamentals
    "market_cap": "Market Cap",
    # Ranks (Relative Rank indicator columns labels)
    "rs_value_rank": "RS Value Rank",
    "rs_ratio_rank_e14": "RSR14% Rank",
    "rs_ratio_rank_e21": "RSR21% Rank",
    "rs_ratio_rank_e63": "RSR63% Rank",
    "rs_momentum_rank_e14": "RSM14% Rank",
    "rs_momentum_rank_e21": "RSM21% Rank",
    "rs_momentum_rank_e63": "RSM63% Rank",
    "rs_trend_rank_s14": "RS Trend Rank 14",
    "rs_trend_rank_s21": "RS Trend Rank 21",
    "rs_trend_rank_s63": "RS Trend Rank 63",
    "rs_roc_ema_rank_e14": "RS ROC Rank 14",
    "rs_roc_ema_rank_e21": "RS ROC Rank 21",
    "rs_roc_ema_rank_e63": "RS ROC Rank 63",
}

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
        # Theme numerical filters (Supports both RelativeRank and Indicator columns)
        # New pattern: min_theme_rs_ratio_rank_e21 (RelativeRank)
        # New pattern: min_theme_rs_trend_s21 (Indicator)
        theme_rank_match = re.match(r'^(min|max)_theme_(.+)$', key)
        if theme_rank_match:
            direction, col_name = theme_rank_match.group(1), theme_rank_match.group(2)
            
            is_theme_filter = False
            is_rank = False
            col_attr = getattr(RelativeRank, col_name, None)
            
            if col_attr is not None:
                is_theme_filter = True
                is_rank = True
            else:
                if col_name.endswith('_rank'):
                    old_indicator = col_name[:-5]
                    mapped_col = _RANK_COLUMN_ALIASES.get(old_indicator)
                    if mapped_col:
                        col_attr = getattr(RelativeRank, mapped_col, None)
                        if col_attr is not None:
                            is_theme_filter = True
                            is_rank = True
                else:
                    # Check if it's a standard Indicator column (e.g. rs_trend_s21)
                    col_attr = _resolve_column(col_name)
                    if col_attr is not None:
                        is_theme_filter = True
                        is_rank = False
                        
            if is_theme_filter:
                if is_rank:
                    _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date).scalar()
                    if not _rk_date:
                        return query
                    
                    theme_q = db.query(RelativeRank.symbol_id).filter(
                        RelativeRank.date == _rk_date,
                        RelativeRank.group_name == "テーマ"
                    )
                    if direction == 'min':
                        theme_q = theme_q.filter(col_attr >= float(value))
                    else:
                        theme_q = theme_q.filter(col_attr <= float(value))
                    theme_subq = theme_q.subquery()
                else:
                    # Indicator base theme filter
                    theme_q = db.query(Indicator.symbol_id).filter(
                        Indicator.date == latest_date
                    )
                    if direction == 'min':
                        theme_q = theme_q.filter(col_attr >= float(value))
                    else:
                        theme_q = theme_q.filter(col_attr <= float(value))
                    theme_subq = theme_q.subquery()
                
                # Find stocks belonging to those themes
                stock_in_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
                    ThemeConstituent.theme_id.in_(select(theme_subq))
                ).subquery()
                
                # Filter original query
                query = query.filter(
                    or_(
                        (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_subq))),
                        (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_themes_subq)))
                    )
                )
                return query

        # Individual RelativeRank filters (Support new & old naming rules)
        # New pattern: min_rs_ratio_rank_e21
        # Old pattern: min_rs_ratio_21_rank
        rank_match = re.match(r'^(min|max)_(.+)$', key)
        if rank_match:
            direction, col_name = rank_match.group(1), rank_match.group(2)
            is_rank_filter = False
            col_attr = getattr(RelativeRank, col_name, None)
            if col_attr is not None:
                is_rank_filter = True
            else:
                if col_name.endswith('_rank'):
                    old_indicator = col_name[:-5]
                    mapped_col = _RANK_COLUMN_ALIASES.get(old_indicator)
                    if mapped_col:
                        col_attr = getattr(RelativeRank, mapped_col, None)
                        if col_attr is not None:
                            is_rank_filter = True

            if is_rank_filter:
                _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date).scalar()
                if not _rk_date:
                    return query
                rank_subq = db.query(
                    RelativeRank.symbol_id,
                    col_attr.label('rank_val')
                ).filter(
                    RelativeRank.date == _rk_date
                ).subquery(name=f"rank_{col_name}_{direction}")
                query = query.join(rank_subq, Symbol.id == rank_subq.c.symbol_id, isouter=False)
                if direction == 'min':
                    query = query.filter(rank_subq.c.rank_val >= float(value))
                else:
                    query = query.filter(rank_subq.c.rank_val <= float(value))
                return query

        # Close-above filters: is_close_gt_ema63=true → DailyPrice.close > Indicator.ema_63
        if key.startswith('is_close_gt_') or key.startswith('close_gt_'):
            ind_name = key[12:] if key.startswith('is_close_gt_') else key[9:]
            # Normalize shorthand names (e.g. ema63 → ema_63, sma50 → sma_50)
            for num in ('200', '150', '63', '50', '21', '5'):
                if ind_name.endswith(num) and not ind_name.endswith('_' + num):
                    ind_name = ind_name.replace(num, '_' + num)
                    break
            ind_col = _resolve_column(ind_name)
            if ind_col is not None:
                query = query.filter(DailyPrice.close > ind_col)
            return query

        # Standard min/max filters
        match = re.match(r'^(min|max)_(.+)$', key)
        if match:
            direction, col_name = match.group(1), match.group(2)
            col = _resolve_column(col_name)
            if col is None:
                return query
            if direction == 'min':
                if col_name == 'market_cap':
                    # S-1: テーマ（market_cap なし）は免除する（バックテストと同一の意味論）
                    query = query.filter(or_(col >= float(value), Symbol.category == 'テーマ'))
                else:
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

# --- Special boolean filters ---
# 実体は indicators/screener_filters.py の純関数群に一本化（audit D-2）。
# API からは api/screener_cross_section.py 経由で評価する。

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
            # (value: 数値または true/false リテラル。true=1.0, false=0.0 として扱う)
            m = re.match(r'^(\w+)\s*(>=|<=|!=|==|>|<)\s*(-?[\d.]+|true|false)$', part, re.IGNORECASE)
            if m:
                col_name, op, val_str = m.group(1), m.group(2), m.group(3)
                col = _resolve_column(col_name)
                if col is None:
                    logger.warning(f"Expression: unknown column '{col_name}'")
                    return None
                if val_str.lower() == 'true':
                    val = 1.0
                elif val_str.lower() == 'false':
                    val = 0.0
                else:
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
        label = _COLUMN_LABELS.get(col_name, col_name.replace("_", " ").title())
        step = 1.0 if col_type == "int" else 0.1
        columns.append(schemas.ScreenerColumnMeta(name=col_name, label=label, category=cat, type=col_type, step=step))

    # Virtual columns
    virtual = []
    for vc_name in _VIRTUAL_COLUMNS.keys():
        cat = _COL_TO_CATEGORY.get(vc_name, "Price & Trend")
        label = _COLUMN_LABELS.get(vc_name, vc_name.replace("_", " ").title())
        virtual.append(schemas.ScreenerColumnMeta(name=vc_name, label=label, category=cat, type="float", step=0.1))

    # T4 rank indicator names (relative_ranks テーブル of ランクカラム名)
    rank_names = [
        'rs_value_rank',
        'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63',
        'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63',
        'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63',
        'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63',
    ]

    return schemas.ScreenerMetaResponse(
        columns=columns, 
        rank_indicators=rank_names, 
        virtual_columns=virtual,
        labels=_COLUMN_LABELS
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

    # Shared cross-section for special boolean filters (built once per request,
    # evaluated with the same pure functions as the backtest engine)
    merged_cs, df_tc_cs = build_cross_section(db, latest_date_result, previous_date_result)

    # Base query wrapper function
    def q_base():
        return db.query(Symbol.id, Symbol.ticker, Symbol.name, DailyPrice.open, DailyPrice.close, Indicator.change_1d_pct).join(
            Indicator, Symbol.id == Indicator.symbol_id
        ).join(
            DailyPrice, (Symbol.id == DailyPrice.symbol_id) & (Indicator.date == DailyPrice.date)
        ).filter(Symbol.active == True, Symbol.category.in_(["テーマ", "個別"]), Indicator.date == latest_date_result)

    def fetch_top_8(query, passing_ids, is_rise=True):
        if passing_ids is None:
            results = query.limit(8).all()
        else:
            results = [r for r in query.all() if r.id in passing_ids][:8]
        if not results:
            return []
            
        # Get all mapped themes for these 8 stocks with RelativeRank on target date
        stock_ids = [r.id for r in results]
        
        # We query the mapping and theme rank in one query
        theme_ranks = db.query(
            ThemeConstituent.symbol_id.label("stock_id"),
            Symbol.ticker.label("theme_ticker"),
            Symbol.name.label("theme_name"),
            RelativeRank.rs_ratio_rank_e21.label("rs_ratio_21")
        ).join(
            Symbol, ThemeConstituent.theme_id == Symbol.id
        ).join(
            RelativeRank, (Symbol.id == RelativeRank.symbol_id) & (RelativeRank.date == latest_date_result)
        ).filter(
            ThemeConstituent.symbol_id.in_(stock_ids)
        ).all()
        
        # Group theme ranks by stock_id
        from collections import defaultdict
        stock_themes = defaultdict(list)
        for tr in theme_ranks:
            stock_themes[tr.stock_id].append({
                "ticker": tr.theme_ticker,
                "name": tr.theme_name.split("::")[1] if "::" in tr.theme_name else tr.theme_name,
                "rs_ratio": tr.rs_ratio_21 if tr.rs_ratio_21 is not None else 0.0
            })
            
        items = []
        for r in results:
            chg = r.change_1d_pct if r.change_1d_pct is not None else 0.0
            
            # Find the strongest or weakest theme
            themes_for_stock = stock_themes.get(r.id, [])
            best_weakest_theme = None
            if themes_for_stock:
                if is_rise:
                    # Rise: pick the one with max RSRatio
                    best_weakest_theme = max(themes_for_stock, key=lambda x: x["rs_ratio"])
                else:
                    # Fall: pick the one with min RSRatio
                    best_weakest_theme = min(themes_for_stock, key=lambda x: x["rs_ratio"])
            
            theme_ticker = best_weakest_theme["ticker"] if best_weakest_theme else None
            theme_name = best_weakest_theme["name"] if best_weakest_theme else None
            theme_rs = best_weakest_theme["rs_ratio"] if best_weakest_theme else None
            
            items.append(schemas.ScreenerDashboardItem(
                id=r.id, 
                ticker=r.ticker, 
                name=r.name, 
                change_pct=chg,
                theme_ticker=theme_ticker,
                theme_name=theme_name,
                theme_rs_ratio=theme_rs
            ))
        return items

    def _build_preset_query(preset_def: dict):
        """Build a query from a single preset definition (TOML dict)."""
        q = q_base()

        # Apply expression-based filters (OR conditions etc.)
        expression = preset_def.get("expression")
        if expression:
            expr_filter = _parse_expression_to_filter(expression)
            if expr_filter is not None:
                q = q.filter(expr_filter)
            else:
                logger.warning(f"Preset '{preset_def.get('id')}': expression parse failed, skipping expression.")

        # Apply standard AND and boolean filters
        filters = preset_def.get("filters", {})
        special_flags = {}
        for key, value in filters.items():
            if key in SPECIAL_FILTER_KEYS:
                if value is True:
                    special_flags[key] = True
            elif key == "_use_hysteresis":
                continue
            elif key == "rrg_intensity_threshold":
                continue
            else:
                # Check if filter key is known
                is_known = False
                if key == "expression":
                    is_known = True
                elif re.match(r'^(min|max)_theme_(.+)$', key):
                    direction, col_name = re.match(r'^(min|max)_theme_(.+)$', key).groups()
                    if hasattr(RelativeRank, col_name):
                        is_known = True
                    elif col_name.endswith('_rank'):
                        old_indicator = col_name[:-5]
                        if old_indicator in _RANK_COLUMN_ALIASES:
                            is_known = True
                    elif _resolve_column(col_name) is not None:
                        is_known = True
                elif re.match(r'^(min|max)_(.+)$', key):
                    direction, col_name = re.match(r'^(min|max)_(.+)$', key).groups()
                    if hasattr(RelativeRank, col_name):
                        is_known = True
                    elif col_name.endswith('_rank'):
                        old_indicator = col_name[:-5]
                        if old_indicator in _RANK_COLUMN_ALIASES:
                            is_known = True
                    elif _resolve_column(col_name) is not None:
                        is_known = True
                elif key.startswith('close_gt_') or key.startswith('is_close_gt_'):
                    ind_name = key[9:] if key.startswith('close_gt_') else key[12:]
                    if ind_name in ('sma5', 'sma21', 'sma50', 'sma63', 'sma150', 'sma200', 'ema5', 'ema21', 'ema50', 'ema63', 'ema150', 'ema200'):
                        for num in ('200', '150', '63', '50', '21', '5'):
                            if ind_name.endswith(num) and not ind_name.endswith('_' + num):
                                ind_name = ind_name.replace(num, '_' + num)
                                break
                    if _resolve_column(ind_name) is not None:
                        is_known = True
                elif _resolve_column(key) is not None:
                    is_known = True
                
                if not is_known:
                    logger.warning(f"Screener Preset '{preset_def.get('id')}': Unknown filter key '{key}' ignored.")
                
                q = _apply_filter(q, key, value, db, latest_date_result)

        # Special boolean filters: evaluated on the shared cross-section
        passing_ids = None
        if special_flags:
            thr = preset_def.get("rrg_intensity_threshold", 0.0)
            if thr == 0.0:
                thr = preset_def.get("filters", {}).get("rrg_intensity_threshold", 0.0)
            passing_ids = evaluate_special_filters(merged_cs, df_tc_cs, special_flags, float(thr))

        # Default sort: by 1Day% (Prev Close base) descending
        q = q.order_by(desc(Indicator.change_1d_pct))
        return q, passing_ids

    # Load presets from TOML
    presets = _load_presets()
    
    rise_categories = []
    fall_categories = []

    for p in presets.get("rise", []):
        try:
            q, passing_ids = _build_preset_query(p)
            rise_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Check"), items=fetch_top_8(q, passing_ids, is_rise=True)
            ))
        except Exception as e:
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")

    for p in presets.get("fall", []):
        try:
            q, passing_ids = _build_preset_query(p)
            fall_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Warning"), items=fetch_top_8(q, passing_ids, is_rise=False)
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

    # ---- Special boolean filters (shared cross-section, same code path as backtest) ----
    _special_flags = {}
    if rs_rank_21_gt_63: _special_flags["is_rs_ratio_rank_e21_gt_e63"] = True
    if theme_rs21_gt_63: _special_flags["is_theme_rs_ratio_e21_gt_e63"] = True
    if theme_rs_rank_21_gt_63: _special_flags["is_theme_rs_ratio_rank_e21_gt_e63"] = True
    if rs_rank_14_gt_21: _special_flags["is_rs_ratio_rank_e14_gt_e21"] = True
    if theme_rs14_gt_21: _special_flags["is_theme_rs_ratio_e14_gt_e21"] = True
    if theme_rs_rank_14_gt_21: _special_flags["is_theme_rs_ratio_rank_e14_gt_e21"] = True
    if rrg_leading_in: _special_flags["rrg_leading_in"] = True
    if rrg_lagging_in: _special_flags["rrg_lagging_in"] = True
    if rrg_improving_in: _special_flags["rrg_improving_in"] = True

    _passing_ids = None
    if _special_flags:
        _merged_cs, _df_tc_cs = build_cross_section(db, latest_date_result, previous_date_result)
        _passing_ids = evaluate_special_filters(_merged_cs, _df_tc_cs, _special_flags, rrg_intensity_threshold)

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

    # Apply special boolean filters as a post-filter (shared logic result)
    if _passing_ids is not None:
        results = [r for r in results if r.Symbol.id in _passing_ids]

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
    rank_map_21 = {r.symbol_id: r.rs_ratio_rank_e21 for r in ranks if r.rs_ratio_rank_e21 is not None}
    rank_map_63 = {r.symbol_id: r.rs_ratio_rank_e63 for r in ranks if r.rs_ratio_rank_e63 is not None}
    
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
            rs_ratio_rank_e21=rank_map_21.get(sym.id, 0.0),
            rs_ratio_rank_e63=rank_map_63.get(sym.id, 0.0),
            rs_ratio_21=ind.rs_ratio_e21,
            rs_ratio_63=ind.rs_ratio_e63,
            rs_ratio_e21=ind.rs_ratio_e21,
            rs_ratio_e63=ind.rs_ratio_e63,
            rs_momentum_21=ind.rs_momentum_e21,
            rs_momentum_e21=ind.rs_momentum_e21,
            sparkline=sparkline_data,
            vol_surge_21=ind.vol_surge_21,
            adr_pct_21=ind.adr_pct_21,
            sma50_atr_mult=ind.sma50_atr_mult,
            dist_sma50_atr=ind.sma50_atr_mult,
            is_trend_template=ind.is_trend_template,
            trend_template_ok=ind.is_trend_template,
            market_cap=dp.market_cap,
            up_down_vol_ratio_50=ind.up_down_vol_ratio_50,
            is_rs_blue_dot=ind.is_rs_blue_dot,
            rs_blue_dot=ind.is_rs_blue_dot,
            is_rs_red_dot=ind.is_rs_red_dot,
            rs_red_dot=ind.is_rs_red_dot,
            vcr=ind.vcr,
            vol_accum_days_5=ind.vol_accum_days_5
        ))
        
    out.sort(key=lambda x: x.rs_ratio_21_rank, reverse=True)
    return out
