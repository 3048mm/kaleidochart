from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, Column as SAColumn, select
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

# --- Boolean Filter Handlers for Screener Presets ---
def _apply_rrg_leading_in(q, preset_def, db, latest_date_result, previous_date_result):
    if not previous_date_result:
        return q
    intensity_threshold = preset_def.get("rrg_intensity_threshold", 0.0)
    if intensity_threshold == 0.0:
        intensity_threshold = preset_def.get("filters", {}).get("rrg_intensity_threshold", 0.0)
    intensity_sq = float(intensity_threshold) * float(intensity_threshold)
    
    IndPrev = aliased(Indicator)
    q = q.join(IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result))
    return q.filter(
        Indicator.rs_ratio_e21 > 0, Indicator.rs_momentum_e21 > 0,
        Indicator.rs_momentum_e21 > IndPrev.rs_momentum_e21,
        (Indicator.rs_ratio_e21 * Indicator.rs_ratio_e21 + Indicator.rs_momentum_e21 * Indicator.rs_momentum_e21) >= intensity_sq,
        or_(
            or_(IndPrev.rs_ratio_e21 <= 0, IndPrev.rs_momentum_e21 <= 0),
            (IndPrev.rs_ratio_e21 * IndPrev.rs_ratio_e21 + IndPrev.rs_momentum_e21 * IndPrev.rs_momentum_e21) < intensity_sq
        )
    )

def _apply_rrg_lagging_in(q, preset_def, db, latest_date_result, previous_date_result):
    if not previous_date_result:
        return q
    IndPrev = aliased(Indicator)
    q = q.join(IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result))
    return q.filter(
        Indicator.rs_ratio_e21 < 0, Indicator.rs_momentum_e21 < 0,
        or_(IndPrev.rs_ratio_e21 >= 0, IndPrev.rs_momentum_e21 >= 0)
    )

def _apply_rrg_improving_in(q, preset_def, db, latest_date_result, previous_date_result):
    if not previous_date_result:
        return q
    intensity_threshold = preset_def.get("rrg_intensity_threshold", 0.0)
    if intensity_threshold == 0.0:
        intensity_threshold = preset_def.get("filters", {}).get("rrg_intensity_threshold", 0.0)
    intensity_sq = float(intensity_threshold) * float(intensity_threshold)
    
    IndPrev = aliased(Indicator)
    q = q.join(IndPrev, (Symbol.id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result))
    return q.filter(
        Indicator.rs_ratio_e21 < 0, Indicator.rs_momentum_e21 > 0,
        Indicator.rs_momentum_e21 > IndPrev.rs_momentum_e21,
        (Indicator.rs_ratio_e21 * Indicator.rs_ratio_e21 + Indicator.rs_momentum_e21 * Indicator.rs_momentum_e21) >= intensity_sq,
        or_(
            and_(IndPrev.rs_ratio_e21 < 0, IndPrev.rs_momentum_e21 <= 0),
            and_(IndPrev.rs_ratio_e21 < 0, (IndPrev.rs_ratio_e21 * IndPrev.rs_ratio_e21 + IndPrev.rs_momentum_e21 * IndPrev.rs_momentum_e21) < intensity_sq)
        )
    )

def _apply_theme_rs_ratio_e21_gt_e63(q, preset_def, db, latest_date_result, previous_date_result):
    theme_momentum_subq = db.query(Indicator.symbol_id).filter(
        Indicator.date == latest_date_result,
        Indicator.rs_ratio_e21 > Indicator.rs_ratio_e63
    ).subquery()
    stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
        ThemeConstituent.theme_id.in_(select(theme_momentum_subq))
    ).subquery()
    return q.filter(
        or_(
            (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_momentum_subq))),
            (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_leading_themes_subq)))
        )
    )

def _apply_theme_rs_ratio_e14_gt_e21(q, preset_def, db, latest_date_result, previous_date_result):
    theme_momentum_subq = db.query(Indicator.symbol_id).filter(
        Indicator.date == latest_date_result,
        Indicator.rs_ratio_e14 > Indicator.rs_ratio_e21
    ).subquery()
    stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
        ThemeConstituent.theme_id.in_(select(theme_momentum_subq))
    ).subquery()
    return q.filter(
        or_(
            (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_momentum_subq))),
            (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_leading_themes_subq)))
        )
    )

def _apply_theme_rs_trend_rank_s14_gt_s21(q, preset_def, db, latest_date_result, previous_date_result):
    _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    if _rk_date:
        theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
            RelativeRank.date == _rk_date,
            RelativeRank.group_name == "テーマ",
            RelativeRank.rs_trend_rank_s14 > RelativeRank.rs_trend_rank_s21
        ).subquery()
        stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
            ThemeConstituent.theme_id.in_(select(theme_momentum_subq))
        ).subquery()
        return q.filter(
            or_(
                (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_momentum_subq))),
                (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_leading_themes_subq)))
            )
        )
    return q

def _apply_theme_rs_ratio_rank_e14_gt_e21(q, preset_def, db, latest_date_result, previous_date_result):
    _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    if _rk_date:
        theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
            RelativeRank.date == _rk_date,
            RelativeRank.group_name == "テーマ",
            RelativeRank.rs_ratio_rank_e14 > RelativeRank.rs_ratio_rank_e21
        ).subquery()
        stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
            ThemeConstituent.theme_id.in_(select(theme_momentum_subq))
        ).subquery()
        return q.filter(
            or_(
                (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_momentum_subq))),
                (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_leading_themes_subq)))
            )
        )
    return q

def _apply_theme_rs_ratio_rank_e21_gt_e63(q, preset_def, db, latest_date_result, previous_date_result):
    _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    if _rk_date:
        theme_momentum_subq = db.query(RelativeRank.symbol_id).filter(
            RelativeRank.date == _rk_date,
            RelativeRank.group_name == "テーマ",
            RelativeRank.rs_ratio_rank_e21 > RelativeRank.rs_ratio_rank_e63
        ).subquery()
        stock_in_leading_themes_subq = db.query(ThemeConstituent.symbol_id).filter(
            ThemeConstituent.theme_id.in_(select(theme_momentum_subq))
        ).subquery()
        return q.filter(
            or_(
                (Symbol.category == "テーマ") & (Symbol.id.in_(select(theme_momentum_subq))),
                (Symbol.category == "個別") & (Symbol.id.in_(select(stock_in_leading_themes_subq)))
            )
        )
    return q

def _apply_rs_trend_s21_lt_s63(q, preset_def, db, latest_date_result, previous_date_result):
    return q.filter(Indicator.rs_trend_s21 < Indicator.rs_trend_s63)
    
def _apply_rs_ratio_rank_e21_gt_e63(q, preset_def, db, latest_date_result, previous_date_result):
    _rk_date = db.query(func.max(RelativeRank.date)).filter(RelativeRank.date <= latest_date_result).scalar()
    if _rk_date:
        rank_subq = db.query(RelativeRank.symbol_id).filter(
            RelativeRank.date == _rk_date,
            RelativeRank.group_name == "個別",
            RelativeRank.rs_ratio_rank_e21 > RelativeRank.rs_ratio_rank_e63
        ).subquery()
        return q.filter(Symbol.id.in_(select(rank_subq)))
    return q

def _apply_rs_macd_hist_rising_21(q, preset_def, db, latest_date_result, previous_date_result):
    if previous_date_result:
        from sqlalchemy.orm import aliased
        IndPrev = aliased(Indicator)
        return q.join(
            IndPrev,
            (Indicator.symbol_id == IndPrev.symbol_id) & (IndPrev.date == previous_date_result)
        ).filter(
            Indicator.rs_macd_hist_21 > 0.0,
            Indicator.rs_macd_hist_21 > IndPrev.rs_macd_hist_21
        )
    return q.filter(Indicator.rs_macd_hist_21 > 0.0)

BOOLEAN_FILTER_HANDLERS = {
    "rrg_leading_in": _apply_rrg_leading_in,
    "rrg_lagging_in": _apply_rrg_lagging_in,
    "rrg_improving_in": _apply_rrg_improving_in,
    "is_theme_rs_ratio_e14_gt_e21": _apply_theme_rs_ratio_e14_gt_e21,
    "is_theme_rs_ratio_e21_gt_e63": _apply_theme_rs_ratio_e21_gt_e63,
    "is_theme_rs_ratio_rank_e14_gt_e21": _apply_theme_rs_ratio_rank_e14_gt_e21,
    "is_theme_rs_ratio_rank_e21_gt_e63": _apply_theme_rs_ratio_rank_e21_gt_e63,
    "is_rs_trend_s21_lt_s63": _apply_rs_trend_s21_lt_s63,
    "is_rs_ratio_rank_e21_gt_e63": _apply_rs_ratio_rank_e21_gt_e63,
    "is_theme_rs_trend_rank_s14_gt_s21": _apply_theme_rs_trend_rank_s14_gt_s21,
    "is_rs_macd_hist_rising_21": _apply_rs_macd_hist_rising_21,
}

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


@router.get("/symbols", response_model=List[schemas.SymbolResponse])
def get_symbols(db: Session = Depends(get_api_db)):
    """
    T1: Get all registered active symbols
    """
    symbols = db.query(Symbol).filter(Symbol.active == 1).order_by(Symbol.category, Symbol.ticker).all()
    
    # 仮想シンボルを追加
    virtual_vxv_vix = Symbol(
        id=99999,
        ticker="^VXV_VIX",
        name="VXV/VIX Ratio",
        category="市場指標",
        active=1,
        tags=""
    )
    virtual_mkt_trend = Symbol(
        id=99998,
        ticker="^MKT_TREND",
        name="Market Trend Score",
        category="市場指標",
        active=1,
        tags=""
    )
    return [virtual_vxv_vix, virtual_mkt_trend] + symbols

@router.get("/chart/{symbol_id}")
def get_chart_data(symbol_id: int, db: Session = Depends(get_api_db), full_range: bool = Query(False)):
    """T2+T3: Get combined daily prices and indicators for rendering charts (High speed)"""
    if symbol_id in (99998, 99999):
        import pandas as pd
        import numpy as np
        import json
        from fastapi import Response
        
        raw_points = []
        
        if full_range is True:
            # Try loading/calculating from Parquet
            from db.database import get_active_db_path
            from pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files
            db_path = get_active_db_path()
            if not db_path:
                try:
                    import tomllib
                    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.toml")
                    with open(config_path, "rb") as f:
                        config = tomllib.load(f)
                        db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
                except Exception:
                    db_path = "data/stocktool.db"
            
            parquet_dir = get_parquet_master_dir(db_path)
            pointer_file = get_pointer_file_path(parquet_dir)
            latest_files = get_latest_master_files(pointer_file)
            
            if latest_files:
                try:
                    df_sym = pd.read_parquet(latest_files['symbols'])
                    vix_id = int(df_sym[df_sym['ticker'] == '^VIX']['id'].iloc[0])
                    vxv_id = int(df_sym[df_sym['ticker'] == '^VIX3M']['id'].iloc[0])
                    spy_id = int(df_sym[df_sym['ticker'] == 'SPY']['id'].iloc[0])
                    
                    if symbol_id == 99999:
                        # VXV Ratio: compute from VXV and VIX closes
                        df_p_vix = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', vix_id)])
                        df_p_vxv = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', vxv_id)])
                        
                        df_p_vix = df_p_vix.sort_values("date").reset_index(drop=True)
                        df_p_vxv = df_p_vxv.sort_values("date").reset_index(drop=True)
                        
                        vix_ref = df_p_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
                        vxv_ref = df_p_vxv[['date', 'close']].rename(columns={'close': 'vxv_close'})
                        merged_vix = pd.merge(vxv_ref, vix_ref, on='date', how='inner')
                        merged_vix['val'] = merged_vix['vxv_close'] / merged_vix['vix_close']
                        merged_vix = merged_vix.sort_values("date").reset_index(drop=True)
                        
                        for _, row in merged_vix.iterrows():
                            val = row['val']
                            if val is not None and not pd.isna(val):
                                raw_points.append({
                                    "date": pd.to_datetime(row["date"]).date(),
                                    "close": float(val)
                                })
                    else:
                        # MTS: compute using MarketTrendScorer
                        from backend.backtest.scenario_market_score import MarketTrendScorer
                        df_p_subset = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', 'in', [spy_id, vix_id, vxv_id])])
                        scorer = MarketTrendScorer(df_p_subset, df_sym, daily_metrics={}, use_vxv_vix=True, scaling_ratio=None)
                        df_p_spy = df_p_subset[df_p_subset['symbol_id'] == spy_id].sort_values("date")
                        
                        for d in df_p_spy['date'].tolist():
                            try:
                                score, _ = scorer.evaluate_market_phase(d)
                            except Exception:
                                score = 70.0
                            raw_points.append({
                                "date": pd.to_datetime(d).date(),
                                "close": float(score)
                            })
                            
                    # SQLite から Parquet データの最新日より新しい差分データを取得してマージ
                    max_p_date = None
                    if raw_points:
                        max_p_date = max(pt['date'] for pt in raw_points)
 
                    if max_p_date:
                        signals_newer = db.query(MarketSignal).filter(
                            MarketSignal.date > max_p_date
                        ).order_by(MarketSignal.date.asc()).all()
 
                        for s in signals_newer:
                            if symbol_id == 99999:
                                val = float(s.vxv_vix_ratio) if s.vxv_vix_ratio is not None else None
                            else:
                                val = float(s.market_trend_score) if s.market_trend_score is not None else None
 
                            if val is not None:
                                raw_points.append({
                                    "date": s.date,
                                    "close": val
                                })
                except Exception as ex:
                    logger.error(f"Error dynamically computing virtual symbols: {ex}")
                    
        # Fallback to SQLite if full_range is False or Parquet loading failed
        if not raw_points:
            signals = db.query(MarketSignal).order_by(MarketSignal.date.asc()).all()
            for s in signals:
                if symbol_id == 99999:
                    val = float(s.vxv_vix_ratio) if s.vxv_vix_ratio is not None else None
                else:
                    val = float(s.market_trend_score) if s.market_trend_score is not None else None
                    
                if val is not None:
                    raw_points.append({
                        "date": s.date,
                        "close": val
                    })
                
        chart_data = []
        ticker = "^VXV_VIX" if symbol_id == 99999 else "^MKT_TREND"
        name = "VXV/VIX Ratio" if symbol_id == 99999 else "Market Trend Score"
        
        if raw_points:
            df = pd.DataFrame(raw_points)
            df = df.sort_values("date").reset_index(drop=True)
            
            # 動的インジケーター計算
            for p in [5, 21, 50, 63, 150, 200]:
                df[f"sma_{p}"] = df["close"].rolling(window=p, min_periods=1).mean()
                df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False, min_periods=1).mean()
                
            # ボリンジャーバンド
            std_21 = df["close"].rolling(window=21, min_periods=1).std()
            df["bb_upper"] = df["sma_21"] + 2 * std_21
            df["bb_lower"] = df["sma_21"] - 2 * std_21
            
            # NaN の処理
            df = df.replace({np.nan: None})
            
            for _, row in df.iterrows():
                d_str = row["date"].strftime('%Y-%m-%d')
                val = row["close"]
                point = {
                    "time": d_str,
                    "open": val,
                    "high": val,
                    "low": val,
                    "close": val,
                    "volume": 0,
                    "market_cap": None,
                    "sma_5": row["sma_5"],
                    "sma_21": row["sma_21"],
                    "sma_50": row["sma_50"],
                    "sma_63": row["sma_63"],
                    "sma_150": row["sma_150"],
                    "sma_200": row["sma_200"],
                    "ema_5": row["ema_5"],
                    "ema_21": row["ema_21"],
                    "ema_50": row["ema_50"],
                    "ema_63": row["ema_63"],
                    "ema_200": row["ema_200"],
                    "bb_upper": row["bb_upper"],
                    "bb_lower": row["bb_lower"],
                    "change_1d_pct": None,
                    "change_1w_pct": None,
                    "change_1m_pct": None,
                    "adr_pct_21": None,
                    "dist_sma50_atr": None,
                    "sma50_atr_mult": None,
                    "relative_strength_spy": None,
                    "rs_value": None
                }
                chart_data.append(point)
                
        # Custom encoder/cleaner to handle NaN/Inf
        def clean_data(obj):
            if isinstance(obj, float):
                if obj != obj or obj == float('inf') or obj == float('-inf'):
                    return None
            return obj
            
        cleaned_data = {
            "metadata": {"id": symbol_id, "ticker": ticker, "name": name, "category": "市場指標"},
            "themes": [],
            "data": [
                {k: clean_data(v) for k, v in point.items()}
                for point in chart_data
            ]
        }
        return Response(
            content=json.dumps(cleaned_data, allow_nan=False),
            media_type="application/json"
        )

    symbol = db.query(Symbol).filter(Symbol.id == symbol_id, Symbol.active == 1).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    if full_range is True:
        # Load data from Parquet Master cache
        import pandas as pd
        import numpy as np
        from db.database import get_active_db_path
        from pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files
        from fastapi.responses import JSONResponse

        db_path = get_active_db_path()
        if not db_path:
            try:
                import tomllib
                config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.toml")
                with open(config_path, "rb") as f:
                    config = tomllib.load(f)
                    db_path = config.get("system", {}).get("db_path", "data/stocktool.db")
            except Exception:
                db_path = "data/stocktool.db"
        
        parquet_dir = get_parquet_master_dir(db_path)
        pointer_file = get_pointer_file_path(parquet_dir)
        latest_files = get_latest_master_files(pointer_file)

        if latest_files:
            try:
                df_p = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', symbol_id)])
                if df_p.empty:
                    return JSONResponse(content={"data": [], "themes": []})

                df_p = df_p.sort_values("date").reset_index(drop=True)
                df_p['date_str'] = pd.to_datetime(df_p['date']).dt.strftime('%Y-%m-%d')

                # SQLite から Parquet データの最新日より新しい差分データを取得してマージ
                max_p_date = None
                if not df_p.empty:
                    max_p_date_raw = df_p['date'].max()
                    if isinstance(max_p_date_raw, str):
                        max_p_date = pd.to_datetime(max_p_date_raw).date()
                    elif hasattr(max_p_date_raw, 'date'):
                        max_p_date = max_p_date_raw.date()
                    else:
                        max_p_date = pd.to_datetime(max_p_date_raw).date()

                if max_p_date:
                    prices_newer = db.query(DailyPrice).filter(
                        DailyPrice.symbol_id == symbol_id,
                        DailyPrice.date > max_p_date
                    ).order_by(DailyPrice.date.asc()).all()

                    if prices_newer:
                        newer_rows = []
                        for p in prices_newer:
                            newer_rows.append({
                                'symbol_id': symbol_id,
                                'date': p.date,
                                'open': p.open,
                                'high': p.high,
                                'low': p.low,
                                'close': p.close,
                                'volume': p.volume,
                                'market_cap': p.market_cap,
                                'date_str': p.date.strftime('%Y-%m-%d')
                            })
                        df_newer = pd.DataFrame(newer_rows)
                        df_p = pd.concat([df_p, df_newer], ignore_index=True)
                        df_p = df_p.sort_values("date").reset_index(drop=True)

                if full_range is True:
                    # Calculate basic indicators dynamically to cover the full historical range
                    close = df_p['close']
                    high = df_p['high']
                    low = df_p['low']

                    for p_val in [5, 21, 50, 63, 150, 200]:
                        df_p[f"calc_sma_{p_val}"] = close.rolling(window=p_val, min_periods=1).mean()
                        df_p[f"calc_ema_{p_val}"] = close.ewm(span=p_val, adjust=False, min_periods=1).mean()

                    df_p["calc_change_1d_pct"] = close.pct_change(1) * 100.0
                    df_p["calc_change_1w_pct"] = close.pct_change(5) * 100.0
                    df_p["calc_change_1m_pct"] = close.pct_change(21) * 100.0

                    close_prev = close.shift(1)
                    tr = pd.concat([
                        high - low,
                        (high - close_prev).abs(),
                        (low - close_prev).abs()
                    ], axis=1).max(axis=1)
                    df_p["calc_atr_14"] = tr.rolling(window=14, min_periods=1).mean().ffill().fillna(1.0)
                    df_p["calc_atr_pct_14"] = np.where(close == 0, 0.0, (df_p["calc_atr_14"] / close) * 100.0)
                    df_p["calc_sma50_atr_mult"] = np.where(
                        df_p["calc_atr_pct_14"].isna() | (df_p["calc_atr_pct_14"] == 0) | df_p["calc_sma_50"].isna() | (df_p["calc_sma_50"] == 0),
                        None,
                        ((close / df_p["calc_sma_50"] * 100) - 100) / df_p["calc_atr_pct_14"]
                    )

                try:
                    df_i = pd.read_parquet(latest_files['indicators'], filters=[('symbol_id', '==', symbol_id)])
                    if not df_i.empty:
                        df_i['date_str'] = pd.to_datetime(df_i['date']).dt.strftime('%Y-%m-%d')
                        ind_cols = [c for c in df_i.columns if c not in ('id', 'symbol_id', 'date', 'date_str')]
                        df_i_indexed = df_i.set_index('date_str')
                        ind_map = df_i_indexed[ind_cols].to_dict(orient='index')
                    else:
                        ind_map = {}
                except Exception as e:
                    logger.error(f"Error reading parquet indicators: {e}")
                    ind_map = {}

                # SQLite から最新の Indicators を取得してマージ
                max_i_date = None
                if not df_i.empty:
                    max_i_date_raw = df_i['date'].max()
                    if isinstance(max_i_date_raw, str):
                        max_i_date = pd.to_datetime(max_i_date_raw).date()
                    elif hasattr(max_i_date_raw, 'date'):
                        max_i_date = max_i_date_raw.date()
                    else:
                        max_i_date = pd.to_datetime(max_i_date_raw).date()

                try:
                    if max_i_date:
                        indicators_newer = db.query(Indicator).filter(
                            Indicator.symbol_id == symbol_id,
                            Indicator.date > max_i_date
                        ).all()
                    else:
                        indicators_newer = db.query(Indicator).filter(
                            Indicator.symbol_id == symbol_id
                        ).all()
                    for ind in indicators_newer:
                        ds = ind.date.strftime('%Y-%m-%d')
                        ind_dict = {}
                        for col in ind.__table__.columns.keys():
                            if col not in ('id', 'symbol_id', 'date'):
                                val = getattr(ind, col)
                                if val is not None:
                                    ind_dict[col] = float(val) if isinstance(val, (int, float)) else val
                                else:
                                    ind_dict[col] = None
                        ind_map[ds] = ind_dict
                except Exception as e:
                    logger.error(f"Error merging newer SQLite indicators: {e}")

                try:
                    df_r = pd.read_parquet(latest_files['ranks'], filters=[('symbol_id', '==', symbol_id)])
                    if not df_r.empty:
                        df_r['date_str'] = pd.to_datetime(df_r['date']).dt.strftime('%Y-%m-%d')
                        rank_indicators = [
                            'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63',
                            'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63',
                            'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63',
                            'rs_value_rank'
                        ]
                        df_r['is_kobetsu'] = df_r['group_name'] == '個別'
                        df_r = df_r.sort_values('is_kobetsu', ascending=True)
                        
                        rank_map_nested = {}
                        for _, row in df_r.iterrows():
                            ds = row['date_str']
                            if ds not in rank_map_nested:
                                rank_map_nested[ds] = {}
                            for ind_name in rank_indicators:
                                val = row.get(ind_name)
                                if val is not None and not pd.isna(val):
                                    rank_map_nested[ds][ind_name] = float(val)
                    else:
                        rank_map_nested = {}
                except Exception as e:
                    logger.error(f"Error reading parquet ranks: {e}")
                    rank_map_nested = {}

                # SQLite から最新の Ranks を取得してマージ
                max_r_date = None
                if not df_r.empty:
                    max_r_date_raw = df_r['date'].max()
                    if isinstance(max_r_date_raw, str):
                        max_r_date = pd.to_datetime(max_r_date_raw).date()
                    elif hasattr(max_r_date_raw, 'date'):
                        max_r_date = max_r_date_raw.date()
                    else:
                        max_r_date = pd.to_datetime(max_r_date_raw).date()

                try:
                    if max_r_date:
                        ranks_newer = db.query(RelativeRank).filter(
                            RelativeRank.symbol_id == symbol_id,
                            RelativeRank.date > max_r_date
                        ).all()
                    else:
                        ranks_newer = db.query(RelativeRank).filter(
                            RelativeRank.symbol_id == symbol_id
                        ).all()
                    for r in ranks_newer:
                        ds = r.date.strftime('%Y-%m-%d')
                        if ds not in rank_map_nested:
                            rank_map_nested[ds] = {}
                        is_kobetsu = (r.group_name == '個別')
                        for ind_name in rank_indicators:
                            val = getattr(r, ind_name, None)
                            if val is not None:
                                if is_kobetsu or ind_name not in rank_map_nested[ds]:
                                    rank_map_nested[ds][ind_name] = float(val)
                except Exception as e:
                    logger.error(f"Error merging newer SQLite ranks: {e}")

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

                chart_data = []
                for _, p in df_p.iterrows():
                    d_str = p['date_str']
                    ind = ind_map.get(d_str, {})
                    
                    point = {
                        "time": d_str, 
                        "open": float(p['open']) if not pd.isna(p['open']) else None, 
                        "high": float(p['high']) if not pd.isna(p['high']) else None, 
                        "low": float(p['low']) if not pd.isna(p['low']) else None, 
                        "close": float(p['close']) if not pd.isna(p['close']) else None, 
                        "volume": float(p['volume']) if not pd.isna(p['volume']) else 0,
                        "market_cap": float(p['market_cap']) if not pd.isna(p['market_cap']) else None
                    }
                    
                    def val_or_none(k, calc_fallback_key=None, force_calc=False):
                        if force_calc and full_range is True and calc_fallback_key and calc_fallback_key in p:
                            fv = p[calc_fallback_key]
                            if fv is not None and not pd.isna(fv):
                                return float(fv)
                        v = ind.get(k) if ind else None
                        if v is not None and not pd.isna(v):
                            return float(v)
                        if full_range is True and calc_fallback_key and calc_fallback_key in p:
                            fv = p[calc_fallback_key]
                            if fv is not None and not pd.isna(fv):
                                return float(fv)
                        return None
                    
                    def bool_or_none(k):
                        v = ind.get(k) if ind else None
                        if v is None or pd.isna(v):
                            return None
                        return bool(v)

                    sma_21_val = val_or_none("sma_21", "calc_sma_21", force_calc=True)
                    atr_14_val = val_or_none("atr_14", "calc_atr_14", force_calc=True)
                    
                    point.update({
                        "sma_5": val_or_none("sma_5", "calc_sma_5", force_calc=True),
                        "sma_21": sma_21_val, 
                        "sma_50": val_or_none("sma_50", "calc_sma_50", force_calc=True), 
                        "sma_63": val_or_none("sma_63", "calc_sma_63", force_calc=True),
                        "sma_150": val_or_none("sma_150", "calc_sma_150", force_calc=True), 
                        "sma_200": val_or_none("sma_200", "calc_sma_200", force_calc=True),
                        "ema_5": val_or_none("ema_5", "calc_ema_5", force_calc=True), 
                        "ema_21": val_or_none("ema_21", "calc_ema_21", force_calc=True), 
                        "ema_50": val_or_none("ema_50", "calc_ema_50", force_calc=True),
                        "ema_63": val_or_none("ema_63", "calc_ema_63", force_calc=True), 
                        "ema_150": val_or_none("ema_150", "calc_ema_150", force_calc=True), 
                        "ema_200": val_or_none("ema_200", "calc_ema_200", force_calc=True),
                        "td9": val_or_none("td9"), 
                        "atr_14": atr_14_val, 
                        "atr_pct_14": val_or_none("atr_pct_14", "calc_atr_pct_14", force_calc=True),
                        "adr_pct_21": val_or_none("adr_pct_21"), 
                        "sma50_atr_mult": val_or_none("sma50_atr_mult", "calc_sma50_atr_mult", force_calc=True),
                        "dist_sma50_atr": val_or_none("sma50_atr_mult", "calc_sma50_atr_mult", force_calc=True), # Legacy compat
                        "relative_strength_spy": val_or_none("rs_value"), # Legacy compat
                        "rs_condition_14": val_or_none("rs_trend_s14"), # Legacy compat
                        "rs_condition_21": val_or_none("rs_trend_s21"), # Legacy compat
                        "rs_condition_63": val_or_none("rs_trend_s63"), # Legacy compat
                        "rs_ema_14": val_or_none("rs_value_e14"), # Legacy compat
                        "rs_ema_21": val_or_none("rs_value_e21"), # Legacy compat
                        "rs_ema_63": val_or_none("rs_value_e63"), # Legacy compat
                        "rs_momentum_14": val_or_none("rs_momentum_e14"), # Legacy compat
                        "rs_momentum_21": val_or_none("rs_momentum_e21"), # Legacy compat
                        "rs_momentum_63": val_or_none("rs_momentum_e63"), # Legacy compat
                        "rs_ratio_14": val_or_none("rs_ratio_e14"), # Legacy compat
                        "rs_ratio_21": val_or_none("rs_ratio_e21"), # Legacy compat
                        "rs_ratio_63": val_or_none("rs_ratio_e63"), # Legacy compat
                        "rel_vol_vs_spy_21": val_or_none("vol_surge_rel_spy_21"), # Legacy compat
                        "pct_from_63d_high": val_or_none("dist_63d_high_pct"), # Legacy compat
                        "pct_from_52w_high": val_or_none("dist_52w_high_pct"), # Legacy compat
                        "rs_blue_dot": val_or_none("is_rs_blue_dot"), # Legacy compat
                        "rs_red_dot": val_or_none("is_rs_red_dot"), # Legacy compat
                        "trend_template_ok": val_or_none("is_trend_template"), # Legacy compat
                        "change_1d_pct": val_or_none("change_1d_pct", "calc_change_1d_pct", force_calc=True),
                        "change_1w_pct": val_or_none("change_1w_pct", "calc_change_1w_pct", force_calc=True),
                        "change_1m_pct": val_or_none("change_1m_pct", "calc_change_1m_pct", force_calc=True),
                        "rs_value": val_or_none("rs_value"),
                        "rs_trend_s5": val_or_none("rs_trend_s5"),
                        "rs_trend_s14": val_or_none("rs_trend_s14"),
                        "rs_trend_s21": val_or_none("rs_trend_s21"),
                        "rs_trend_s63": val_or_none("rs_trend_s63"),
                        "rs_trend_s200": val_or_none("rs_trend_s200"),
                        "rs_macd_line_21": val_or_none("rs_macd_line_21"),
                        "rs_macd_signal_21": val_or_none("rs_macd_signal_21"),
                        "rs_macd_hist_21": val_or_none("rs_macd_hist_21"),
                        "rs_value_e5": val_or_none("rs_value_e5"),
                        "rs_value_e14": val_or_none("rs_value_e14"),
                        "rs_value_e21": val_or_none("rs_value_e21"),
                        "rs_value_e63": val_or_none("rs_value_e63"),
                        "rs_value_e200": val_or_none("rs_value_e200"),
                        "rs_momentum_e5": val_or_none("rs_momentum_e5"),
                        "rs_momentum_e14": val_or_none("rs_momentum_e14"),
                        "rs_momentum_e21": val_or_none("rs_momentum_e21"),
                        "rs_momentum_e63": val_or_none("rs_momentum_e63"),
                        "rs_momentum_e200": val_or_none("rs_momentum_e200"),
                        "rs_ratio_e5": val_or_none("rs_ratio_e5"),
                        "rs_ratio_e14": val_or_none("rs_ratio_e14"),
                        "rs_ratio_e21": val_or_none("rs_ratio_e21"),
                        "rs_ratio_e63": val_or_none("rs_ratio_e63"),
                        "rs_ratio_e200": val_or_none("rs_ratio_e200"),
                        "rs_roc_ema_5": val_or_none("rs_roc_ema_5"),
                        "rs_roc_ema_14": val_or_none("rs_roc_ema_14"),
                        "rs_roc_ema_21": val_or_none("rs_roc_ema_21"),
                        "rs_roc_ema_63": val_or_none("rs_roc_ema_63"),
                        "vol_surge_21": val_or_none("vol_surge_21"),
                        "vol_surge_rel_spy_21": val_or_none("vol_surge_rel_spy_21"),
                        "up_down_vol_ratio_50": val_or_none("up_down_vol_ratio_50"),
                        "dist_63d_high_pct": val_or_none("dist_63d_high_pct"),
                        "dist_52w_high_pct": val_or_none("dist_52w_high_pct"),
                        "is_rs_blue_dot": bool_or_none("is_rs_blue_dot"), 
                        "is_rs_red_dot": bool_or_none("is_rs_red_dot"),
                        "vcr": val_or_none("vcr"), 
                        "is_trend_template": bool_or_none("is_trend_template"),
                        "vol_accum_days_5": val_or_none("vol_accum_days_5"),
                        "bb_upper": (sma_21_val + 2 * atr_14_val) if sma_21_val and atr_14_val else None,
                        "bb_lower": (sma_21_val - 2 * atr_14_val) if sma_21_val and atr_14_val else None,
                    })

                    r_data = rank_map_nested.get(d_str, {})
                    for r_name in rank_indicators:
                        point[r_name] = r_data.get(r_name)
                    
                    # Include legacy rank keys for compatibility
                    point["rank_rs_ratio_14"] = r_data.get("rs_ratio_rank_e14")
                    point["rank_rs_ratio_21"] = r_data.get("rs_ratio_rank_e21")
                    point["rank_rs_ratio_63"] = r_data.get("rs_ratio_rank_e63")
                    point["rank_rs_momentum_14"] = r_data.get("rs_momentum_rank_e14")
                    point["rank_rs_momentum_21"] = r_data.get("rs_momentum_rank_e21")
                    point["rank_rs_momentum_63"] = r_data.get("rs_momentum_rank_e63")
                    point["rank_rs_condition_14"] = r_data.get("rs_trend_rank_s14")
                    point["rank_rs_condition_21"] = r_data.get("rs_trend_rank_s21")
                    point["rank_rs_condition_63"] = r_data.get("rs_trend_rank_s63")
                    
                    point["rs_ratio"] = r_data.get("rs_ratio_rank_e21")

                    chart_data.append(point)

                import json
                from fastapi import Response

                def clean_data(obj):
                    if isinstance(obj, float):
                        if obj != obj or obj == float('inf') or obj == float('-inf'):
                            return None
                    return obj

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

            except Exception as ex:
                logger.error(f"Error building chart from Parquet: {ex}")
                # Fallback to standard SQLite DB query
                pass

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
        'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63',
        'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63',
        'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63',
        'rs_value_rank'
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
                "adr_pct_21": ind.adr_pct_21, "sma50_atr_mult": ind.sma50_atr_mult,
                "dist_sma50_atr": ind.sma50_atr_mult, # Legacy compat
                "change_1d_pct": ind.change_1d_pct,
                "change_1w_pct": ind.change_1w_pct,
                "change_1m_pct": ind.change_1m_pct,
                "rs_value": ind.rs_value,
                "relative_strength_spy": ind.rs_value, # Legacy compat
                "rs_trend_s5": ind.rs_trend_s5,
                "rs_trend_s14": ind.rs_trend_s14,
                "rs_condition_14": ind.rs_trend_s14, # Legacy compat
                "rs_trend_s21": ind.rs_trend_s21,
                "rs_condition_21": ind.rs_trend_s21, # Legacy compat
                "rs_trend_s63": ind.rs_trend_s63,
                "rs_condition_63": ind.rs_trend_s63, # Legacy compat
                "rs_trend_s200": ind.rs_trend_s200,
                "rs_macd_line_21": ind.rs_macd_line_21,
                "rs_macd_signal_21": ind.rs_macd_signal_21,
                "rs_macd_hist_21": ind.rs_macd_hist_21,
                "rs_value_e5": ind.rs_value_e5,
                "rs_value_e14": ind.rs_value_e14,
                "rs_ema_14": ind.rs_value_e14, # Legacy compat
                "rs_value_e21": ind.rs_value_e21,
                "rs_ema_21": ind.rs_value_e21, # Legacy compat
                "rs_value_e63": ind.rs_value_e63,
                "rs_ema_63": ind.rs_value_e63, # Legacy compat
                "rs_value_e200": ind.rs_value_e200,
                "rs_momentum_e5": ind.rs_momentum_e5,
                "rs_momentum_e14": ind.rs_momentum_e14,
                "rs_momentum_14": ind.rs_momentum_e14, # Legacy compat
                "rs_momentum_e21": ind.rs_momentum_e21,
                "rs_momentum_21": ind.rs_momentum_e21, # Legacy compat
                "rs_momentum_e63": ind.rs_momentum_e63,
                "rs_momentum_63": ind.rs_momentum_e63, # Legacy compat
                "rs_momentum_e200": ind.rs_momentum_e200,
                "rs_ratio_e5": ind.rs_ratio_e5,
                "rs_ratio_e14": ind.rs_ratio_e14,
                "rs_ratio_14": ind.rs_ratio_e14, # Legacy compat
                "rs_ratio_e21": ind.rs_ratio_e21,
                "rs_ratio_21": ind.rs_ratio_e21, # Legacy compat
                "rs_ratio_e63": ind.rs_ratio_e63,
                "rs_ratio_e200": ind.rs_ratio_e200,
                "rs_ratio_63": ind.rs_ratio_e63, # Legacy compat
                "rs_roc_ema_14": ind.rs_roc_ema_14,
                "rs_roc_ema_21": ind.rs_roc_ema_21,
                "rs_roc_ema_63": ind.rs_roc_ema_63,
                "vol_surge_21": ind.vol_surge_21,
                "vol_surge_rel_spy_21": ind.vol_surge_rel_spy_21,
                "rel_vol_vs_spy_21": ind.vol_surge_rel_spy_21, # Legacy compat
                "up_down_vol_ratio_50": ind.up_down_vol_ratio_50,
                "dist_63d_high_pct": ind.dist_63d_high_pct,
                "pct_from_63d_high": ind.dist_63d_high_pct, # Legacy compat
                "dist_52w_high_pct": ind.dist_52w_high_pct,
                "pct_from_52w_high": ind.dist_52w_high_pct, # Legacy compat
                "is_rs_blue_dot": ind.is_rs_blue_dot, "is_rs_red_dot": ind.is_rs_red_dot,
                "rs_blue_dot": ind.is_rs_blue_dot, "rs_red_dot": ind.is_rs_red_dot, # Legacy compat
                "vcr": ind.vcr, "is_trend_template": ind.is_trend_template,
                "trend_template_ok": ind.is_trend_template, # Legacy compat
                "vol_accum_days_5": ind.vol_accum_days_5,
                "bb_upper": (ind.sma_21 + 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
                "bb_lower": (ind.sma_21 - 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
            })
            # Include all ranks for RRG minimaps
            r_data = rank_map_nested.get(d_str, {})
            for r_name in rank_indicators:
                point[r_name] = r_data.get(r_name)
            
            # Include legacy rank keys for compatibility
            point["rank_rs_ratio_14"] = r_data.get("rs_ratio_rank_e14")
            point["rank_rs_ratio_21"] = r_data.get("rs_ratio_rank_e21")
            point["rank_rs_ratio_63"] = r_data.get("rs_ratio_rank_e63")
            point["rank_rs_momentum_14"] = r_data.get("rs_momentum_rank_e14")
            point["rank_rs_momentum_21"] = r_data.get("rs_momentum_rank_e21")
            point["rank_rs_momentum_63"] = r_data.get("rs_momentum_rank_e63")
            point["rank_rs_condition_14"] = r_data.get("rs_trend_rank_s14")
            point["rank_rs_condition_21"] = r_data.get("rs_trend_rank_s21")
            point["rank_rs_condition_63"] = r_data.get("rs_trend_rank_s63")
            
            # Include legacy key for RsLineChart
            point["rs_ratio"] = r_data.get("rs_ratio_rank_e21")
        
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

# --- Helper functions for dashboard & group pages ---

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
    symbols = db.query(Symbol).filter(Symbol.active == 1).all()
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

    for sym_id, s in sym_dict.items():
        if sym_id not in price_dict:
            continue
            
        dp = price_dict[sym_id]
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
            rank_val_trend_63=rt63_rank
        )
        
        if s.category == "市場":
            resp.indices.append(item)
        elif s.category == "指標":
            if s.ticker == "SPY":
                resp.spy_feature = _build_etf_feature(db, s, dp, target_date)
            else:
                resp.leading.append(_build_leading_item(db, s, dp, target_date))
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
            rs_ratio_14=i.rs_ratio_e14 if i else None,
            rs_ratio_e14=i.rs_ratio_e14 if i else None,
            rs_ratio_21=i.rs_ratio_e21 if i else None,
            rs_ratio_e21=i.rs_ratio_e21 if i else None,
            rs_ratio_63=i.rs_ratio_e63 if i else None,
            rs_ratio_e63=i.rs_ratio_e63 if i else None,
            rs_momentum_14=i.rs_momentum_e14 if i else None,
            rs_momentum_e14=i.rs_momentum_e14 if i else None,
            rs_momentum_21=i.rs_momentum_e21 if i else None,
            rs_momentum_e21=i.rs_momentum_e21 if i else None,
            rs_momentum_63=i.rs_momentum_e63 if i else None,
            rs_momentum_e63=i.rs_momentum_e63 if i else None,
            rs_condition_14=i.rs_trend_s14 if i else None,
            rs_trend_s14=i.rs_trend_s14 if i else None,
            rs_condition_21=i.rs_trend_s21 if i else None,
            rs_trend_s21=i.rs_trend_s21 if i else None,
            rs_condition_63=i.rs_trend_s63 if i else None,
            rs_trend_s63=i.rs_trend_s63 if i else None,
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
        c_rs14 = c_ind.rs_ratio_e14 if c_ind else None
        c_rs21 = c_ind.rs_ratio_e21 if c_ind else None
        c_rs63 = c_ind.rs_ratio_e63 if c_ind else None
        c_rsmom21 = c_ind.rs_momentum_e21 if c_ind else None
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
                relative_strength_spy=i.rs_value if i else None,
                rs_value=i.rs_value if i else None,
                rs_ema_14=i.rs_value_e14 if i else None,
                rs_value_e14=i.rs_value_e14 if i else None,
                rs_ema_21=i.rs_value_e21 if i else None,
                rs_value_e21=i.rs_value_e21 if i else None,
                rs_ema_63=i.rs_value_e63 if i else None,
                rs_value_e63=i.rs_value_e63 if i else None,
                rs_ratio_14=i.rs_ratio_e14 if i else None,
                rs_ratio_e14=i.rs_ratio_e14 if i else None,
                rs_ratio_21=i.rs_ratio_e21 if i else None,
                rs_ratio_e21=i.rs_ratio_e21 if i else None,
                rs_ratio_63=i.rs_ratio_e63 if i else None,
                rs_ratio_e63=i.rs_ratio_e63 if i else None,
                rs_momentum_14=i.rs_momentum_e14 if i else None,
                rs_momentum_e14=i.rs_momentum_e14 if i else None,
                rs_momentum_21=i.rs_momentum_e21 if i else None,
                rs_momentum_e21=i.rs_momentum_e21 if i else None,
                rs_momentum_63=i.rs_momentum_e63 if i else None,
                rs_momentum_e63=i.rs_momentum_e63 if i else None,
            ))

        # Fetch ranks for constituent (highly optimized single query!)
        r_row = db.query(
            RelativeRank.rs_ratio_rank_e14, 
            RelativeRank.rs_ratio_rank_e21, 
            RelativeRank.rs_ratio_rank_e63, 
            RelativeRank.rs_momentum_rank_e21, 
            RelativeRank.rs_momentum_rank_e63
        ).filter(
            RelativeRank.symbol_id == c_id, 
            RelativeRank.date == c_dp.date
        ).first()
        
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
        rs_ratio_14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rs_ratio_e14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs_momentum_14=ind.rs_momentum_e14 if ind else None,
        rs_momentum_21=ind.rs_momentum_e21 if ind else None,
        rs_momentum_63=ind.rs_momentum_e63 if ind else None,
        rs_momentum_e14=ind.rs_momentum_e14 if ind else None,
        rs_momentum_e21=ind.rs_momentum_e21 if ind else None,
        rs_momentum_e63=ind.rs_momentum_e63 if ind else None,
        rs_condition_14=ind.rs_trend_s14 if ind else None,
        rs_condition_21=ind.rs_trend_s21 if ind else None,
        rs_condition_63=ind.rs_trend_s63 if ind else None,
        rs_trend_s14=ind.rs_trend_s14 if ind else None,
        rs_trend_s21=ind.rs_trend_s21 if ind else None,
        rs_trend_s63=ind.rs_trend_s63 if ind else None,
        rs_value_e5=ind.rs_value_e5 if ind else None,
        rs_value_e14=ind.rs_value_e14 if ind else None,
        rs_value_e21=ind.rs_value_e21 if ind else None,
        rs_value_e63=ind.rs_value_e63 if ind else None,
        rs_ema_14=ind.rs_value_e14 if ind else None,
        rs_ema_21=ind.rs_value_e21 if ind else None,
        rs_ema_63=ind.rs_value_e63 if ind else None,
        adr_pct_21=ind.adr_pct_21 if ind else None,
        sma50_atr_mult=ind.sma50_atr_mult if ind else None,
        dist_sma50_atr=ind.sma50_atr_mult if ind else None,
        rs14_sparkline=rs14_spark, rs21_sparkline=rs21_spark, rs63_sparkline=rs63_spark,
        rank_rs_ratio_14=theme_r.rs_ratio_rank_e14 if theme_r else None,
        rank_rs_ratio_21=theme_r.rs_ratio_rank_e21 if theme_r else None,
        rank_rs_ratio_63=theme_r.rs_ratio_rank_e63 if theme_r else None,
        rs_ratio_rank_e14=theme_r.rs_ratio_rank_e14 if theme_r else None,
        rs_ratio_rank_e21=theme_r.rs_ratio_rank_e21 if theme_r else None,
        rs_ratio_rank_e63=theme_r.rs_ratio_rank_e63 if theme_r else None,
        rank_rs_momentum_14=theme_r.rs_momentum_rank_e14 if theme_r else None,
        rank_rs_momentum_21=theme_r.rs_momentum_rank_e21 if theme_r else None,
        rank_rs_momentum_63=theme_r.rs_momentum_rank_e63 if theme_r else None,
        rs_momentum_rank_e14=theme_r.rs_momentum_rank_e14 if theme_r else None,
        rs_momentum_rank_e21=theme_r.rs_momentum_rank_e21 if theme_r else None,
        rs_momentum_rank_e63=theme_r.rs_momentum_rank_e63 if theme_r else None,
        rank_rs_condition_14=theme_r.rs_trend_rank_s14 if theme_r else None,
        rank_rs_condition_21=theme_r.rs_trend_rank_s21 if theme_r else None,
        rank_rs_condition_63=theme_r.rs_trend_rank_s63 if theme_r else None,
        rs_trend_rank_s14=theme_r.rs_trend_rank_s14 if theme_r else None,
        rs_trend_rank_s21=theme_r.rs_trend_rank_s21 if theme_r else None,
        rs_trend_rank_s63=theme_r.rs_trend_rank_s63 if theme_r else None,
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

    def fetch_top_8(query, is_rise=True):
        results = query.limit(8).all()
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
        for key, value in filters.items():
            if key in BOOLEAN_FILTER_HANDLERS:
                if value is True:
                    q = BOOLEAN_FILTER_HANDLERS[key](q, preset_def, db, latest_date_result, previous_date_result)
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
                group=p.get("group", "Check"), items=fetch_top_8(q, is_rise=True)
            ))
        except Exception as e:
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")

    for p in presets.get("fall", []):
        try:
            q = _build_preset_query(p)
            fall_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Warning"), items=fetch_top_8(q, is_rise=False)
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
                RelativeRank.rs_ratio_rank_e21 > RelativeRank.rs_ratio_rank_e63
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
            Indicator.rs_ratio_e21 > Indicator.rs_ratio_e63
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
                RelativeRank.rs_ratio_rank_e21 > RelativeRank.rs_ratio_rank_e63
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
                RelativeRank.rs_ratio_rank_e14 > RelativeRank.rs_ratio_rank_e21
            ).subquery()
            query = query.filter(Symbol.id.in_(rank_subq))

    if theme_rs14_gt_21:
        theme_momentum_subq = db.query(Indicator.symbol_id).filter(
            Indicator.date == latest_date_result,
            Indicator.rs_ratio_e14 > Indicator.rs_ratio_e21
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
                RelativeRank.rs_ratio_rank_e14 > RelativeRank.rs_ratio_rank_e21
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
                    Indicator.rs_ratio_e21 > 0, Indicator.rs_momentum_e21 > 0,
                    Indicator.rs_momentum_e21 > IndPrev.rs_momentum_e21,
                    (Indicator.rs_ratio_e21 * Indicator.rs_ratio_e21 + Indicator.rs_momentum_e21 * Indicator.rs_momentum_e21) >= intensity_sq,
                    or_(
                        or_(IndPrev.rs_ratio_e21 <= 0, IndPrev.rs_momentum_e21 <= 0),
                        (IndPrev.rs_ratio_e21 * IndPrev.rs_ratio_e21 + IndPrev.rs_momentum_e21 * IndPrev.rs_momentum_e21) < intensity_sq
                    )
                )
            )
        if rrg_lagging_in:
            rrg_conds.append(
                and_(
                    Indicator.rs_ratio_e21 < 0, Indicator.rs_momentum_e21 < 0,
                    or_(IndPrev.rs_ratio_e21 >= 0, IndPrev.rs_momentum_e21 >= 0)
                )
            )
        if rrg_improving_in:
            rrg_conds.append(
                and_(
                    Indicator.rs_ratio_e21 < 0, Indicator.rs_momentum_e21 > 0,
                    Indicator.rs_momentum_e21 > IndPrev.rs_momentum_e21,
                    (Indicator.rs_ratio_e21 * Indicator.rs_ratio_e21 + Indicator.rs_momentum_e21 * Indicator.rs_momentum_e21) >= intensity_sq,
                    or_(
                        and_(IndPrev.rs_ratio_e21 < 0, IndPrev.rs_momentum_e21 <= 0),
                        and_(IndPrev.rs_ratio_e21 < 0, (IndPrev.rs_ratio_e21 * IndPrev.rs_ratio_e21 + IndPrev.rs_momentum_e21 * IndPrev.rs_momentum_e21) < intensity_sq)
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
        latest_sma50_atr_mult=0.0,
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
