"""
Watchlist service layer: core business logic for watchlist operations.
Handles add/remove/update/clear with 3-day reactivation and same-day cancel rules.
"""
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from db.models import Symbol, DailyPrice, Indicator, RelativeRank
from db.models_user import Watchlist


def _get_close_price(db: Session, symbol_id: int, target_date: date) -> float | None:
    """Get closing price for a symbol on a specific date."""
    dp = db.query(DailyPrice).filter_by(
        symbol_id=symbol_id, date=target_date
    ).first()
    return dp.close if dp else None


def _get_latest_price_row(db: Session, symbol_id: int) -> DailyPrice | None:
    """Get the most recent DailyPrice row for a symbol."""
    return db.query(DailyPrice).filter_by(
        symbol_id=symbol_id
    ).order_by(DailyPrice.date.desc()).first()


def _resolve_symbol(db: Session, ticker: str) -> Symbol | None:
    """Resolve ticker to Symbol object."""
    return db.query(Symbol).filter_by(ticker=ticker).first()


def heal_watchlist_ids(db: Session, user_db: Session) -> None:
    """
    Heal symbol_ids in watchlist table by matching ticker and exchange with stocktool.db symbols.
    """
    items = user_db.query(Watchlist).all()
    if not items:
        return

    # Fetch all active symbols to optimize
    symbols = db.query(Symbol).all()
    sym_map = {}
    for s in symbols:
        sym_map[(s.ticker, s.exchange)] = s
        if s.ticker not in sym_map:
            sym_map[s.ticker] = s

    modified = False
    for wl in items:
        current_sym = None
        if wl.symbol_id is not None:
            current_sym = db.query(Symbol).filter_by(id=wl.symbol_id).first()
        
        is_valid = (
            current_sym is not None 
            and current_sym.ticker == wl.ticker 
            and current_sym.exchange == wl.exchange
        )
        
        if not is_valid:
            target_sym = sym_map.get((wl.ticker, wl.exchange))
            if not target_sym:
                target_sym = sym_map.get(wl.ticker)
                
            if target_sym:
                wl.symbol_id = target_sym.id
                modified = True
            else:
                if wl.symbol_id is not None:
                    wl.symbol_id = None
                    modified = True

    if modified:
        user_db.commit()


def add_to_watchlist(db: Session, user_db: Session, ticker: str, entry_date: date) -> Watchlist | None:
    """
    Add a symbol to watchlist with 3-day reactivation logic.
    """
    sym = _resolve_symbol(db, ticker)
    if sym is None:
        return None

    # Find by ticker/exchange to ensure we get it even if ID changed
    existing = user_db.query(Watchlist).filter_by(ticker=sym.ticker, exchange=sym.exchange).first()

    if existing:
        # Sync symbol_id just in case it was off
        if existing.symbol_id != sym.id:
            existing.symbol_id = sym.id
            user_db.commit()

        if existing.status == "active":
            return existing  # Already active, no-op

        # status == 'removed': check 1-hour rule
        if existing.removed_at and (datetime.utcnow() - existing.removed_at).total_seconds() < 3600:
            # Reactivate with original entry_date/price (added_at preserved)
            existing.status = "active"
            existing.removed_at = None
            existing.removed_price = None
            user_db.commit()
            return existing
        else:
            # Overwrite with new entry (added_at preserved)
            entry_price = _get_close_price(db, sym.id, entry_date)
            if entry_price is None:
                return None
            existing.entry_date = entry_date
            existing.entry_price = entry_price
            existing.status = "active"
            existing.removed_at = None
            existing.removed_price = None
            user_db.commit()
            return existing
    else:
        # New record
        entry_price = _get_close_price(db, sym.id, entry_date)
        if entry_price is None:
            return None
        wl = Watchlist(
            symbol_id=sym.id,
            ticker=sym.ticker,
            exchange=sym.exchange,
            entry_date=entry_date,
            entry_price=entry_price,
            status="active",
            added_at=datetime.utcnow(),
        )
        user_db.add(wl)
        user_db.commit()
        user_db.refresh(wl)
        return wl


def remove_from_watchlist(db: Session, user_db: Session, ticker: str) -> Watchlist | None:
    """
    Remove a symbol from watchlist.
    """
    sym = _resolve_symbol(db, ticker)
    if sym is None:
        wl = user_db.query(Watchlist).filter_by(ticker=ticker, status="active").first()
    else:
        wl = user_db.query(Watchlist).filter_by(ticker=sym.ticker, exchange=sym.exchange, status="active").first()

    if wl is None:
        return None

    # Within 1 hour of registration: physically delete (accidental add)
    if (datetime.utcnow() - wl.added_at).total_seconds() < 3600:
        user_db.delete(wl)
        user_db.commit()
        return None  # Signals physical deletion

    # Logical delete
    latest = _get_latest_price_row(db, wl.symbol_id) if wl.symbol_id else None
    wl.status = "removed"
    wl.removed_at = datetime.utcnow()
    wl.removed_price = latest.close if latest else wl.entry_price
    user_db.commit()
    return wl


def update_watchlist_entry_date(db: Session, user_db: Session, ticker: str, new_entry_date: date) -> Watchlist | None:
    """Update entry_date and entry_price for an active watchlist item."""
    sym = _resolve_symbol(db, ticker)
    if sym is None:
        wl = user_db.query(Watchlist).filter_by(ticker=ticker, status="active").first()
    else:
        wl = user_db.query(Watchlist).filter_by(ticker=sym.ticker, exchange=sym.exchange, status="active").first()

    if wl is None:
        return None

    # If symbol_id is resolved now, sync it
    if sym and wl.symbol_id != sym.id:
        wl.symbol_id = sym.id

    if wl.symbol_id is None:
        return None

    new_price = _get_close_price(db, wl.symbol_id, new_entry_date)
    if new_price is None:
        return None

    wl.entry_date = new_entry_date
    wl.entry_price = new_price
    user_db.commit()
    return wl


def remove_bulk_from_watchlist(db: Session, user_db: Session, tickers: list[str]) -> int:
    """
    Perform logical removal for multiple tickers at once.
    """
    if not tickers:
        return 0

    active_items = user_db.query(Watchlist).filter(
        Watchlist.ticker.in_(tickers),
        Watchlist.status == "active"
    ).all()

    count = 0
    now = datetime.utcnow()
    for wl in active_items:
        latest = _get_latest_price_row(db, wl.symbol_id) if wl.symbol_id else None
        
        wl.status = "removed"
        wl.removed_at = now
        wl.removed_price = latest.close if latest else wl.entry_price
        count += 1

    user_db.commit()
    return count


def clear_removed(user_db: Session) -> int:
    """Physically delete all 'removed' watchlist records."""
    count = user_db.query(Watchlist).filter_by(status="removed").delete()
    user_db.commit()
    return count


def get_watchlist(db: Session, user_db: Session) -> dict:
    """
    Get all watchlist items with computed metrics.
    """
    heal_watchlist_ids(db, user_db)

    items = user_db.query(Watchlist).all()
    active_list = []
    removed_list = []

    s_ids = [wl.symbol_id for wl in items if wl.symbol_id is not None]
    if not items:
        return {"active": [], "removed": []}
        
    sym_map = {}
    if s_ids:
        symbols = db.query(Symbol).filter(Symbol.id.in_(s_ids)).all()
        sym_map = {s.id: s for s in symbols}

    for wl in items:
        ticker = wl.ticker
        name = wl.ticker
        
        sym = sym_map.get(wl.symbol_id) if wl.symbol_id else None
        if sym:
            name = sym.name

        latest_close = 0.0
        latest_ind = None
        max_gain_pct = 0.0
        min_gain_pct = 0.0
        rs_sparkline = []

        if wl.symbol_id:
            latest_dp = _get_latest_price_row(db, wl.symbol_id)
            latest_close = latest_dp.close if latest_dp else 0.0

            latest_ind = db.query(Indicator).filter_by(
                symbol_id=wl.symbol_id
            ).order_by(Indicator.date.desc()).first()

            prices_in_range = db.query(DailyPrice).filter(
                DailyPrice.symbol_id == wl.symbol_id,
                DailyPrice.date >= wl.entry_date,
            ).all()

            if prices_in_range and wl.entry_price:
                max_close = max(p.close for p in prices_in_range)
                min_close = min(p.close for p in prices_in_range)
                max_gain_pct = (max_close - wl.entry_price) / wl.entry_price * 100
                min_gain_pct = (min_close - wl.entry_price) / wl.entry_price * 100

            rs_ranks = db.query(RelativeRank.rs_ratio_rank_e21).filter(
                RelativeRank.symbol_id == wl.symbol_id,
                RelativeRank.rs_ratio_rank_e21.isnot(None),
            ).order_by(RelativeRank.date.desc()).limit(30).all()
            rs_sparkline = [r[0] for r in reversed(rs_ranks)]
        else:
            latest_close = wl.removed_price if wl.status == "removed" and wl.removed_price else wl.entry_price

        gain_pct = ((latest_close - wl.entry_price) / wl.entry_price * 100
                    if wl.entry_price else 0.0)

        item_dict = {
            "id": wl.id,
            "symbol_id": wl.symbol_id,
            "ticker": ticker,
            "name": name,
            "entry_date": wl.entry_date.isoformat(),
            "entry_price": wl.entry_price,
            "latest_close": latest_close,
            "latest_ema_21": latest_ind.ema_21 if latest_ind and latest_ind.ema_21 else 0.0,
            "gain_pct": round(gain_pct, 2),
            "max_gain_pct": round(max_gain_pct, 2),
            "min_gain_pct": round(min_gain_pct, 2),
            "latest_adr_pct": latest_ind.adr_pct_21 if latest_ind and latest_ind.adr_pct_21 else 0.0,
            "latest_dist_sma50_atr": latest_ind.sma50_atr_mult if latest_ind and latest_ind.sma50_atr_mult else 0.0,
            "latest_sma50_atr_mult": latest_ind.sma50_atr_mult if latest_ind and latest_ind.sma50_atr_mult else 0.0,
            "rs_sparkline": rs_sparkline,
            "next_earnings_date": sym.next_earnings_date if sym else None,
            "status": wl.status,
            "added_at": wl.added_at.isoformat() if wl.added_at else None,
            "removed_at": wl.removed_at.isoformat() if wl.removed_at else None,
            "removed_price": wl.removed_price,
        }

        if wl.status == "active":
            active_list.append(item_dict)
        else:
            removed_list.append(item_dict)

    active_list.sort(key=lambda x: x["entry_date"], reverse=True)

    return {"active": active_list, "removed": removed_list}


def get_watchlist_tickers(db: Session, user_db: Session) -> list[str]:
    """Get list of active watchlist tickers."""
    heal_watchlist_ids(db, user_db)
    
    active_items = user_db.query(Watchlist).filter_by(status="active").all()
    return [wl.ticker for wl in active_items]

