"""Watchlist API router (audit D-1 で routers.py から分割)."""
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
