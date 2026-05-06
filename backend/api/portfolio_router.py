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
    TransactionRequest,
)
from api import portfolio_service
from db.database import get_db
from db.database_user import get_user_db

def get_api_db():
    with get_db() as db:
        yield db

def get_api_user_db():
    with get_user_db() as user_db:
        yield user_db

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# ============================================================
# Total Portfolio Endpoints
# ============================================================
@router.get("/total/summary")
def get_total_summary(db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    return portfolio_service.get_total_portfolio_summary(db, user_db)

@router.get("/total/transactions")
def get_transactions(user_db: Session = Depends(get_api_user_db)):
    return portfolio_service.get_transactions(user_db)

@router.get("/total/fx-rate")
def get_fx_rate(date: dt_date, db: Session = Depends(get_api_db)):
    rate = portfolio_service.get_historical_fx_rate(db, date)
    return {"exchange_rate": rate}

@router.post("/total/transactions")
def add_transaction(req: TransactionRequest, user_db: Session = Depends(get_api_user_db)):
    t = portfolio_service.execute_transaction(
        user_db=user_db,
        transaction_type=req.transaction_type,
        amount=req.amount,
        local_amount=req.local_amount,
        exchange_rate=req.exchange_rate,
        local_currency=req.local_currency,
        date=req.date,
        portfolio_id=req.portfolio_id,
        memo=req.memo
    )
    return {
        "id": t.id,
        "transaction_type": t.transaction_type,
        "amount": t.amount,
        "date": t.date.isoformat(),
        "memo": t.memo,
        "portfolio_id": t.portfolio_id,
    }

# Portfolio CRUD
@router.get("")
def list_portfolios(user_db: Session = Depends(get_api_user_db)):
    portfolios = portfolio_service.get_portfolios(user_db)
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
def create_portfolio(req: PortfolioCreateRequest, user_db: Session = Depends(get_api_user_db)):
    pf = portfolio_service.create_portfolio(
        user_db, name=req.name, currency=req.currency,
        total_capital=req.total_capital, risk_pct=req.risk_pct,
        default_stop_loss_pct=req.default_stop_loss_pct,
        stop_loss_method=req.stop_loss_method,
        atr_multiplier=req.atr_multiplier,
        profit_take_method=req.profit_take_method,
        max_positions=req.max_positions,
    )
    return {"id": pf.id, "name": pf.name, "status": pf.status}

@router.get("/ticker-price")
def get_ticker_price(ticker: str, date: dt_date, db: Session = Depends(get_api_db)):
    from db.models import Symbol, DailyPrice
    sym = db.query(Symbol).filter_by(ticker=ticker).first()
    if not sym:
        raise HTTPException(status_code=404, detail="Symbol not found")
    
    dp = db.query(DailyPrice).filter_by(symbol_id=sym.id, date=date).first()
    if dp:
        return {"price": dp.close}
    
    # fallback to latest available price before the date
    from sqlalchemy import desc
    dp = db.query(DailyPrice).filter(
        DailyPrice.symbol_id == sym.id,
        DailyPrice.date <= date
    ).order_by(desc(DailyPrice.date)).first()
    if dp:
        return {"price": dp.close}
    raise HTTPException(status_code=404, detail="Price data not available")

@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: int, user_db: Session = Depends(get_api_user_db)):
    pf = portfolio_service.get_portfolio(user_db, portfolio_id)
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
                     user_db: Session = Depends(get_api_user_db)):
    pf = portfolio_service.update_portfolio(
        user_db, portfolio_id,
        **req.model_dump(exclude_none=True),
    )
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"id": pf.id, "name": pf.name, "status": "updated"}

@router.delete("/{portfolio_id}")
def archive_portfolio(portfolio_id: int, user_db: Session = Depends(get_api_user_db)):
    pf = portfolio_service.archive_portfolio(user_db, portfolio_id)
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"id": pf.id, "status": "archived"}



@router.get("/{portfolio_id}/positions")
def list_positions(portfolio_id: int, db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    return portfolio_service.get_positions_with_metrics(db, user_db, portfolio_id)

@router.post("/{portfolio_id}/positions", status_code=201)
def add_position(portfolio_id: int, req: PositionAddRequest,
                 db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    pos = portfolio_service.add_position(
        db, user_db, portfolio_id=portfolio_id, ticker=req.ticker,
        entry_date=req.entry_date, shares=req.shares,
        memo=req.memo, stop_loss_pct=req.stop_loss_pct,
        custom_take_profit_pct=req.custom_take_profit_pct,
        entry_price=req.entry_price,
    )
    if pos is None:
        raise HTTPException(status_code=400, detail="Failed to add position (invalid ticker or no price data)")
    return {"id": pos.id, "ticker": req.ticker, "shares": pos.shares}

from api.schemas import PositionUpdateRequest
@router.put("/{portfolio_id}/positions/{position_id}")
def edit_position(portfolio_id: int, position_id: int, req: PositionUpdateRequest, user_db: Session = Depends(get_api_user_db)):
    pos = portfolio_service.edit_position(
        user_db, position_id=position_id,
        entry_date=req.entry_date, entry_price=req.entry_price, shares=req.shares
    )
    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"id": pos.id, "status": "updated"}

@router.delete("/{portfolio_id}/positions/{position_id}")
def delete_position(portfolio_id: int, position_id: int, user_db: Session = Depends(get_api_user_db)):
    success = portfolio_service.delete_position(user_db, position_id)
    if not success:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "deleted"}

@router.post("/{portfolio_id}/positions/{position_id}/sell")
def sell_position(portfolio_id: int, position_id: int,
                  req: PositionSellRequest, user_db: Session = Depends(get_api_user_db)):
    hist = portfolio_service.sell_position(
        user_db, position_id=position_id,
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

# History & Summary
@router.get("/{portfolio_id}/history")
def get_history(portfolio_id: int, db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    return portfolio_service.get_history(db, user_db, portfolio_id)

from api.schemas import PositionHistoryUpdateRequest
@router.put("/{portfolio_id}/history/{history_id}")
def edit_history(portfolio_id: int, history_id: int, req: PositionHistoryUpdateRequest, user_db: Session = Depends(get_api_user_db)):
    hist = portfolio_service.edit_history(
        user_db, history_id=history_id,
        entry_date=req.entry_date, entry_price=req.entry_price,
        exit_date=req.exit_date, exit_price=req.exit_price,
        exit_shares=req.exit_shares, memo=req.memo
    )
    if hist is None:
        raise HTTPException(status_code=404, detail="History record not found")
    return {"id": hist.id, "status": "updated"}

@router.get("/{portfolio_id}/summary")
def get_summary(portfolio_id: int, db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    summary = portfolio_service.get_portfolio_summary(db, user_db, portfolio_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return summary

@router.get("/{portfolio_id}/analytics")
def get_analytics(portfolio_id: int, db: Session = Depends(get_api_db), user_db: Session = Depends(get_api_user_db)):
    data = portfolio_service.get_analytics(db, user_db, portfolio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return data


