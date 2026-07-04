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


# Dependencies moved to api/deps.py (re-exported here for backward compatibility)
from api.deps import get_api_db, get_api_user_db



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


# --- Backward-compatibility re-exports (moved to dedicated routers, audit D-1) ---
from api.chart_router import get_chart_data  # noqa: E402  (tests import this from api.routers)
