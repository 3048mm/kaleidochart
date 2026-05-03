"""
Portfolio service layer: core business logic for portfolio operations.
Handles CRUD, position management, sell/trim flow, history, and summary.
"""
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import desc

from db.models import (
    Symbol, DailyPrice, Indicator, RelativeRank,
    Portfolio, PortfolioPosition, PositionHistory,
)
from api.portfolio_logic import calc_max_investment, calc_stop_loss_price, calc_pnl, check_alert_status


# --- Helpers ---

def _resolve_symbol(db: Session, ticker: str) -> Symbol | None:
    return db.query(Symbol).filter_by(ticker=ticker).first()


def _get_close_price(db: Session, symbol_id: int, target_date: date) -> float | None:
    dp = db.query(DailyPrice).filter_by(symbol_id=symbol_id, date=target_date).first()
    return dp.close if dp else None


def _get_latest_price(db: Session, symbol_id: int) -> DailyPrice | None:
    return db.query(DailyPrice).filter_by(
        symbol_id=symbol_id
    ).order_by(desc(DailyPrice.date)).first()


# ============================================================
# 2-1: Portfolio CRUD
# ============================================================

def create_portfolio(
    db: Session, *, name: str, currency: str, total_capital: float,
    risk_pct: float, default_stop_loss_pct: float, stop_loss_method: str,
    max_positions: int, atr_multiplier: float = 2.0,
    profit_take_method: str = None, source: str = "manual",
) -> Portfolio:
    pf = Portfolio(
        name=name, currency=currency, total_capital=total_capital,
        risk_pct=risk_pct, default_stop_loss_pct=default_stop_loss_pct,
        stop_loss_method=stop_loss_method, atr_multiplier=atr_multiplier,
        profit_take_method=profit_take_method, max_positions=max_positions,
        source=source, status="active",
    )
    db.add(pf)
    db.commit()
    db.refresh(pf)
    return pf


def get_portfolios(db: Session) -> list[Portfolio]:
    return db.query(Portfolio).filter_by(status="active").all()


def get_portfolio(db: Session, portfolio_id: int) -> Portfolio | None:
    return db.query(Portfolio).filter_by(id=portfolio_id).first()


def update_portfolio(db: Session, portfolio_id: int, **kwargs) -> Portfolio | None:
    pf = db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None
    for key, value in kwargs.items():
        if hasattr(pf, key) and value is not None:
            setattr(pf, key, value)
    db.commit()
    db.refresh(pf)
    return pf


def archive_portfolio(db: Session, portfolio_id: int) -> Portfolio | None:
    pf = db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None
    pf.status = "archived"
    db.commit()
    db.refresh(pf)
    return pf


# ============================================================
# 2-2: Position Management
# ============================================================

def add_position(
    db: Session, *, portfolio_id: int, ticker: str,
    entry_date: date, shares: int, memo: str = None,
    stop_loss_pct: float = None, custom_take_profit_pct: float = None,
) -> PortfolioPosition | None:
    sym = _resolve_symbol(db, ticker)
    if sym is None:
        return None

    entry_price = _get_close_price(db, sym.id, entry_date)
    if entry_price is None:
        return None

    pos = PortfolioPosition(
        portfolio_id=portfolio_id, symbol_id=sym.id,
        entry_date=entry_date, entry_price=entry_price,
        shares=shares, original_shares=shares,
        stop_loss_pct=stop_loss_pct,
        custom_take_profit_pct=custom_take_profit_pct,
        status="open", memo=memo,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


def get_positions_with_metrics(db: Session, portfolio_id: int) -> list[dict]:
    pf = db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return []

    positions = db.query(PortfolioPosition).filter_by(
        portfolio_id=portfolio_id
    ).all()

    result = []
    for pos in positions:
        sym = db.query(Symbol).filter_by(id=pos.symbol_id).first()
        latest_dp = _get_latest_price(db, pos.symbol_id)
        current_price = latest_dp.close if latest_dp else pos.entry_price

        # Effective stop loss
        eff_stop_pct = pos.stop_loss_pct if pos.stop_loss_pct is not None else pf.default_stop_loss_pct

        # Get latest indicator for ATR-based stop loss
        latest_ind = db.query(Indicator).filter_by(
            symbol_id=pos.symbol_id
        ).order_by(desc(Indicator.date)).first()

        if pf.stop_loss_method == "atr_multiple" and latest_ind and latest_ind.atr_14:
            stop_price = calc_stop_loss_price(
                entry_price=pos.entry_price,
                stop_loss_method="atr_multiple",
                atr=latest_ind.atr_14,
                atr_multiplier=pf.atr_multiplier or 2.0,
            )
        else:
            stop_price = calc_stop_loss_price(
                entry_price=pos.entry_price,
                stop_loss_pct=eff_stop_pct,
                stop_loss_method="fixed_pct",
            )

        total_gain_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        total_gain_amount = (current_price - pos.entry_price) * pos.shares

        # Alert check
        alert = check_alert_status(
            current_price=current_price,
            entry_price=pos.entry_price,
            stop_loss_price=stop_price,
        )

        result.append({
            "id": pos.id,
            "symbol_id": pos.symbol_id,
            "ticker": sym.ticker if sym else "???",
            "name": sym.name if sym else "",
            "entry_date": pos.entry_date.isoformat(),
            "entry_price": pos.entry_price,
            "shares": pos.shares,
            "original_shares": pos.original_shares,
            "invested": pos.entry_price * pos.shares,
            "current_price": current_price,
            "total_gain_pct": round(total_gain_pct, 2),
            "total_gain_amount": round(total_gain_amount, 2),
            "stop_loss_price": round(stop_price, 2),
            "stop_loss_alert": alert["stop_loss_alert"],
            "distance_to_stop_pct": round(alert["distance_to_stop_pct"], 2),
            "atr_pct": latest_ind.atr_pct_14 if latest_ind and latest_ind.atr_pct_14 else None,
            "status": pos.status,
            "memo": pos.memo,
        })

    return result


# ============================================================
# 2-3: Sell / Trim Flow
# ============================================================

def sell_position(
    db: Session, *, position_id: int,
    exit_date: date, exit_price: float, exit_shares: int,
    exit_reason: str, memo: str = None,
) -> PositionHistory | None:
    pos = db.query(PortfolioPosition).filter_by(id=position_id).first()
    if pos is None:
        return None

    if exit_shares > pos.shares:
        return None  # Cannot sell more than held

    # Compute PnL
    pnl = calc_pnl(
        entry_price=pos.entry_price,
        exit_price=exit_price,
        shares=exit_shares,
        entry_date=pos.entry_date,
        exit_date=exit_date,
    )

    hist = PositionHistory(
        portfolio_id=pos.portfolio_id, symbol_id=pos.symbol_id,
        entry_date=pos.entry_date, entry_price=pos.entry_price,
        entry_shares=exit_shares,
        exit_date=exit_date, exit_price=exit_price,
        exit_shares=exit_shares, exit_reason=exit_reason,
        pnl_pct=pnl["pnl_pct"], pnl_amount=pnl["pnl_amount"],
        holding_days=pnl["holding_days"], memo=memo,
    )
    db.add(hist)

    # Update or delete position
    pos.shares -= exit_shares
    if pos.shares <= 0:
        db.delete(pos)
    else:
        pos.status = "partially_closed"

    db.commit()
    db.refresh(hist)
    return hist


# ============================================================
# 2-4: History
# ============================================================

def get_history(db: Session, portfolio_id: int) -> list[dict]:
    records = db.query(PositionHistory).filter_by(
        portfolio_id=portfolio_id
    ).order_by(PositionHistory.exit_date.asc(), PositionHistory.id.asc()).all()

    result = []
    cumulative_pnl = 0.0

    for h in records:
        sym = db.query(Symbol).filter_by(id=h.symbol_id).first()
        cumulative_pnl += (h.pnl_amount or 0.0)
        result.append({
            "id": h.id,
            "ticker": sym.ticker if sym else "???",
            "name": sym.name if sym else "",
            "entry_date": h.entry_date.isoformat(),
            "entry_price": h.entry_price,
            "exit_date": h.exit_date.isoformat(),
            "exit_price": h.exit_price,
            "exit_shares": h.exit_shares,
            "exit_reason": h.exit_reason,
            "pnl_pct": round(h.pnl_pct, 2) if h.pnl_pct else 0.0,
            "pnl_amount": round(h.pnl_amount, 2) if h.pnl_amount else 0.0,
            "holding_days": h.holding_days or 0,
            "cumulative_pnl": round(cumulative_pnl, 2),
            "memo": h.memo,
        })

    return result


# ============================================================
# 2-5: Portfolio Summary (Risk Dashboard Data)
# ============================================================

def get_portfolio_summary(db: Session, portfolio_id: int) -> dict | None:
    pf = db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None

    positions = db.query(PortfolioPosition).filter_by(
        portfolio_id=portfolio_id
    ).all()

    sizing = calc_max_investment(
        total_capital=pf.total_capital,
        risk_pct=pf.risk_pct,
        stop_loss_pct=pf.default_stop_loss_pct,
        stop_loss_method="fixed_pct",  # Summary uses fixed_pct for display
    )

    invested_total = 0.0
    market_value_total = 0.0

    for pos in positions:
        invested_total += pos.entry_price * pos.shares
        latest_dp = _get_latest_price(db, pos.symbol_id)
        current_price = latest_dp.close if latest_dp else pos.entry_price
        market_value_total += current_price * pos.shares

    unrealized_pnl = market_value_total - invested_total

    return {
        "portfolio_id": pf.id,
        "name": pf.name,
        "currency": pf.currency,
        "total_capital": pf.total_capital,
        "risk_pct": pf.risk_pct,
        "risk_amount": sizing["risk_amount"],
        "max_investment": sizing["max_investment"],
        "stop_loss_method": pf.stop_loss_method,
        "default_stop_loss_pct": pf.default_stop_loss_pct,
        "max_positions": pf.max_positions,
        "open_positions": len(positions),
        "invested_total": round(invested_total, 2),
        "market_value_total": round(market_value_total, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
    }


# ============================================================
# 2-6: Analytics (Sector distribution, performance stats)
# ============================================================

def get_analytics(db: Session, portfolio_id: int) -> dict | None:
    """Return analytics data: sector breakdown, win rate, monthly returns, equity curve."""
    pf = db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None

    # --- Sector/category distribution (from open positions) ---
    positions = db.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).all()
    sector_map: dict[str, float] = {}
    for pos in positions:
        sym = db.query(Symbol).filter_by(id=pos.symbol_id).first()
        cat = sym.category if sym else "Unknown"
        sector_map[cat] = sector_map.get(cat, 0) + (pos.entry_price * pos.shares)

    sector_breakdown = [
        {"category": k, "invested": round(v, 2)}
        for k, v in sorted(sector_map.items(), key=lambda x: -x[1])
    ]

    # --- Trade stats from history ---
    history = db.query(PositionHistory).filter_by(portfolio_id=portfolio_id)\
        .order_by(PositionHistory.exit_date.asc(), PositionHistory.id.asc()).all()

    total_trades = len(history)
    wins = [h for h in history if (h.pnl_pct or 0) > 0]
    losses = [h for h in history if (h.pnl_pct or 0) <= 0]
    win_count = len(wins)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = sum(h.pnl_pct or 0 for h in wins) / win_count if win_count else 0.0
    avg_loss = sum(h.pnl_pct or 0 for h in losses) / len(losses) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss) if total_trades > 0 else 0.0

    # --- Equity curve (cumulative PnL over time) ---
    equity_curve = []
    cumulative = 0.0
    for h in history:
        cumulative += (h.pnl_amount or 0)
        equity_curve.append({
            "date": h.exit_date.isoformat(),
            "cumulative_pnl": round(cumulative, 2),
            "ticker": db.query(Symbol).filter_by(id=h.symbol_id).first().ticker if db.query(Symbol).filter_by(id=h.symbol_id).first() else "???",
        })

    # --- Monthly returns ---
    monthly_map: dict[str, float] = {}
    for h in history:
        key = h.exit_date.strftime("%Y-%m")
        monthly_map[key] = monthly_map.get(key, 0) + (h.pnl_amount or 0)

    monthly_returns = [
        {"month": k, "pnl": round(v, 2)}
        for k, v in sorted(monthly_map.items())
    ]

    return {
        "sector_breakdown": sector_breakdown,
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 2),
        "equity_curve": equity_curve,
        "monthly_returns": monthly_returns,
    }

