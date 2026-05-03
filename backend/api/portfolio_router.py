"""
Portfolio API router: FastAPI endpoints for portfolio management.
Delegates business logic to portfolio_service.py.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date as dt_date

from api.schemas import (
    PortfolioCreateRequest,
    PortfolioUpdateRequest,
    PositionAddRequest,
    PositionSellRequest,
)
from api import portfolio_service
from db.database import get_db


def get_api_db():
    """Dependency for DB session. Matches the pattern in routers.py."""
    with get_db() as db:
        yield db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# ============================================================
# Portfolio CRUD
# ============================================================

@router.get("")
def list_portfolios(db: Session = Depends(get_api_db)):
    """Get all active portfolios."""
    portfolios = portfolio_service.get_portfolios(db)
    return [
        {
            "id": p.id, "name": p.name, "currency": p.currency,
            "total_capital": p.total_capital, "risk_pct": p.risk_pct,
            "status": p.status, "max_positions": p.max_positions,
            "stop_loss_method": p.stop_loss_method,
            "default_stop_loss_pct": p.default_stop_loss_pct,
        }
        for p in portfolios
    ]


@router.post("", status_code=201)
def create_portfolio(req: PortfolioCreateRequest, db: Session = Depends(get_api_db)):
    """Create a new portfolio."""
    pf = portfolio_service.create_portfolio(
        db, name=req.name, currency=req.currency,
        total_capital=req.total_capital, risk_pct=req.risk_pct,
        default_stop_loss_pct=req.default_stop_loss_pct,
        stop_loss_method=req.stop_loss_method,
        atr_multiplier=req.atr_multiplier,
        profit_take_method=req.profit_take_method,
        max_positions=req.max_positions,
    )
    return {"id": pf.id, "name": pf.name, "status": pf.status}


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Get a single portfolio by ID."""
    pf = portfolio_service.get_portfolio(db, portfolio_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {
        "id": pf.id, "name": pf.name, "currency": pf.currency,
        "total_capital": pf.total_capital, "risk_pct": pf.risk_pct,
        "default_stop_loss_pct": pf.default_stop_loss_pct,
        "stop_loss_method": pf.stop_loss_method,
        "atr_multiplier": pf.atr_multiplier,
        "profit_take_method": pf.profit_take_method,
        "max_positions": pf.max_positions,
        "source": pf.source, "status": pf.status,
    }


@router.put("/{portfolio_id}")
def update_portfolio(portfolio_id: int, req: PortfolioUpdateRequest,
                     db: Session = Depends(get_api_db)):
    """Update portfolio settings."""
    pf = portfolio_service.update_portfolio(
        db, portfolio_id,
        **req.model_dump(exclude_none=True),
    )
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"id": pf.id, "name": pf.name, "status": "updated"}


@router.delete("/{portfolio_id}")
def archive_portfolio(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Archive a portfolio (soft delete)."""
    pf = portfolio_service.archive_portfolio(db, portfolio_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"id": pf.id, "status": "archived"}


# ============================================================
# Position Management
# ============================================================

@router.get("/{portfolio_id}/positions")
def list_positions(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Get all positions with enriched metrics."""
    return portfolio_service.get_positions_with_metrics(db, portfolio_id)


@router.post("/{portfolio_id}/positions", status_code=201)
def add_position(portfolio_id: int, req: PositionAddRequest,
                 db: Session = Depends(get_api_db)):
    """Add a new position to the portfolio."""
    pos = portfolio_service.add_position(
        db, portfolio_id=portfolio_id, ticker=req.ticker,
        entry_date=req.entry_date, shares=req.shares,
        memo=req.memo, stop_loss_pct=req.stop_loss_pct,
        custom_take_profit_pct=req.custom_take_profit_pct,
    )
    if pos is None:
        raise HTTPException(status_code=400, detail="Failed to add position (invalid ticker or no price data)")
    return {"id": pos.id, "ticker": req.ticker, "shares": pos.shares}


@router.post("/{portfolio_id}/positions/{position_id}/sell")
def sell_position(portfolio_id: int, position_id: int,
                  req: PositionSellRequest, db: Session = Depends(get_api_db)):
    """Sell (full or trim) a position."""
    hist = portfolio_service.sell_position(
        db, position_id=position_id,
        exit_date=req.exit_date, exit_price=req.exit_price,
        exit_shares=req.exit_shares, exit_reason=req.exit_reason,
        memo=req.memo,
    )
    if hist is None:
        raise HTTPException(status_code=400, detail="Sell failed (invalid position or shares exceed held)")
    return {
        "id": hist.id, "exit_shares": hist.exit_shares,
        "pnl_pct": round(hist.pnl_pct, 2) if hist.pnl_pct else 0,
        "pnl_amount": round(hist.pnl_amount, 2) if hist.pnl_amount else 0,
    }


# ============================================================
# History & Summary
# ============================================================

@router.get("/{portfolio_id}/history")
def get_history(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Get position history (sold/trimmed trades)."""
    return portfolio_service.get_history(db, portfolio_id)


@router.get("/{portfolio_id}/summary")
def get_summary(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Get portfolio summary (risk dashboard data)."""
    summary = portfolio_service.get_portfolio_summary(db, portfolio_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return summary


@router.get("/{portfolio_id}/analytics")
def get_analytics(portfolio_id: int, db: Session = Depends(get_api_db)):
    """Get portfolio analytics (sector breakdown, performance stats, equity curve)."""
    data = portfolio_service.get_analytics(db, portfolio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return data
