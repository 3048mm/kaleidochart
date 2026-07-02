"""
Portfolio service layer: core business logic for portfolio operations.
Handles CRUD, position management, sell/trim flow, history, and summary.
"""
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import desc

from db.models import Symbol, DailyPrice, Indicator, MarketSignal, FxRate
from db.models_user import Portfolio, PortfolioPosition, PositionHistory, TotalPortfolio, Transaction
from api.portfolio_logic import (
    calc_max_investment,
    calc_stop_loss_price,
    calc_pnl,
    check_alert_status,
    calculate_recommended_cash,
    determine_vxv_vix_ema_regime,
)



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


def heal_portfolio_ids(db: Session, user_db: Session) -> None:
    """
    Heal symbol_ids in portfolio_positions and position_history tables by matching
    ticker and exchange with stocktool.db symbols.
    """
    positions = user_db.query(PortfolioPosition).all()
    history = user_db.query(PositionHistory).all()
    
    if not positions and not history:
        return

    # Fetch all active symbols to optimize matching
    symbols = db.query(Symbol).all()
    sym_map = {}
    for s in symbols:
        sym_map[(s.ticker, s.exchange)] = s
        if s.ticker not in sym_map:
            sym_map[s.ticker] = s

    modified = False

    # 1. Heal portfolio_positions
    for pos in positions:
        current_sym = None
        if pos.symbol_id is not None:
            current_sym = db.query(Symbol).filter_by(id=pos.symbol_id).first()
        
        is_valid = (
            current_sym is not None 
            and current_sym.ticker == pos.ticker 
            and current_sym.exchange == pos.exchange
        )
        
        if not is_valid:
            target_sym = sym_map.get((pos.ticker, pos.exchange))
            if not target_sym:
                target_sym = sym_map.get(pos.ticker)
                
            if target_sym:
                pos.symbol_id = target_sym.id
                modified = True
            else:
                if pos.symbol_id is not None:
                    pos.symbol_id = None
                    modified = True

    # 2. Heal position_history
    for hist in history:
        current_sym = None
        if hist.symbol_id is not None:
            current_sym = db.query(Symbol).filter_by(id=hist.symbol_id).first()
            
        is_valid = (
            current_sym is not None 
            and current_sym.ticker == hist.ticker 
            and current_sym.exchange == hist.exchange
        )
        
        if not is_valid:
            target_sym = sym_map.get((hist.ticker, hist.exchange))
            if not target_sym:
                target_sym = sym_map.get(hist.ticker)
                
            if target_sym:
                hist.symbol_id = target_sym.id
                modified = True
            else:
                if hist.symbol_id is not None:
                    hist.symbol_id = None
                    modified = True

    if modified:
        user_db.commit()


# ============================================================
# 2-1: Portfolio CRUD
# ============================================================
def _get_or_create_default_total_portfolio(user_db: Session) -> TotalPortfolio:
    tp = user_db.query(TotalPortfolio).first()
    if not tp:
        tp = TotalPortfolio(name="Main Account", currency="USD")
        user_db.add(tp)
        user_db.commit()
        user_db.refresh(tp)
    return tp

def create_portfolio(
    user_db: Session, *, name: str, currency: str, total_capital: float,
    risk_pct: float, default_stop_loss_pct: float, stop_loss_method: str,
    max_positions: int, atr_multiplier: float = 2.0,
    profit_take_method: str = None, source: str = "manual",
) -> Portfolio:
    tp = _get_or_create_default_total_portfolio(user_db)
    pf = Portfolio(
        total_portfolio_id=tp.id,
        name=name, currency=currency, total_capital=total_capital,
        risk_pct=risk_pct, default_stop_loss_pct=default_stop_loss_pct,
        stop_loss_method=stop_loss_method, atr_multiplier=atr_multiplier,
        profit_take_method=profit_take_method, max_positions=max_positions,
        source=source, status="active",
    )
    user_db.add(pf)
    user_db.commit()
    user_db.refresh(pf)
    return pf

def get_portfolios(user_db: Session) -> list[Portfolio]:
    return user_db.query(Portfolio).filter_by(status="active").all()

def get_portfolio(user_db: Session, portfolio_id: int) -> Portfolio | None:
    return user_db.query(Portfolio).filter_by(id=portfolio_id).first()

def update_portfolio(user_db: Session, portfolio_id: int, **kwargs) -> Portfolio | None:
    pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None
    for key, value in kwargs.items():
        if hasattr(pf, key) and value is not None:
            setattr(pf, key, value)
    user_db.commit()
    user_db.refresh(pf)
    return pf

def archive_portfolio(user_db: Session, portfolio_id: int) -> Portfolio | None:
    pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None
    pf.status = "archived"
    user_db.commit()
    user_db.refresh(pf)
    return pf


# ============================================================
# 2-2: Position Management
# ============================================================
def add_position(
    db: Session, user_db: Session, *, portfolio_id: int, ticker: str,
    entry_date: date, shares: float, memo: str = None,
    stop_loss_pct: float = None, custom_take_profit_pct: float = None,
    entry_price: float = None,
) -> PortfolioPosition | None:
    sym = _resolve_symbol(db, ticker)
    if sym is None:
        return None

    if entry_price is None:
        entry_price = _get_close_price(db, sym.id, entry_date)
    if entry_price is None:
        return None

    pos = PortfolioPosition(
        portfolio_id=portfolio_id, symbol_id=sym.id,
        ticker=sym.ticker, exchange=sym.exchange,
        entry_date=entry_date, entry_price=entry_price,
        shares=shares, original_shares=shares,
        stop_loss_pct=stop_loss_pct,
        custom_take_profit_pct=custom_take_profit_pct,
        status="open", memo=memo,
    )
    user_db.add(pos)
    user_db.commit()
    user_db.refresh(pos)
    return pos

def edit_position(
    user_db: Session, position_id: int,
    entry_date: date = None, entry_price: float = None, shares: float = None
) -> PortfolioPosition | None:
    pos = user_db.query(PortfolioPosition).filter_by(id=position_id).first()
    if not pos:
        return None
    
    if entry_date is not None:
        pos.entry_date = entry_date
    if entry_price is not None:
        pos.entry_price = entry_price
    if shares is not None:
        # Also adjust original_shares relatively if we changed shares
        # Assuming we just update both if it's a correction of the initial amount
        if pos.shares == pos.original_shares:
            pos.original_shares = shares
        pos.shares = shares
        
    user_db.commit()
    user_db.refresh(pos)
    return pos

def get_positions_with_metrics(db: Session, user_db: Session, portfolio_id: int) -> list[dict]:
    heal_portfolio_ids(db, user_db)
    pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return []

    positions = user_db.query(PortfolioPosition).filter_by(
        portfolio_id=portfolio_id
    ).all()

    result = []
    for pos in positions:
        sym = db.query(Symbol).filter_by(id=pos.symbol_id).first() if pos.symbol_id else None
        latest_dp = _get_latest_price(db, pos.symbol_id) if pos.symbol_id else None
        current_price = latest_dp.close if latest_dp else pos.entry_price

        eff_stop_pct = pos.stop_loss_pct if pos.stop_loss_pct is not None else pf.default_stop_loss_pct

        latest_ind = db.query(Indicator).filter_by(
            symbol_id=pos.symbol_id
        ).order_by(desc(Indicator.date)).first() if pos.symbol_id else None

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

        total_gain_pct = (current_price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0.0
        total_gain_amount = (current_price - pos.entry_price) * pos.shares

        alert = check_alert_status(
            current_price=current_price,
            entry_price=pos.entry_price,
            stop_loss_price=stop_price,
        )

        result.append({
            "id": pos.id,
            "symbol_id": pos.symbol_id,
            "ticker": pos.ticker,
            "name": sym.name if sym else pos.ticker,
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
def delete_position(user_db: Session, position_id: int) -> bool:
    pos = user_db.query(PortfolioPosition).filter(PortfolioPosition.id == position_id).first()
    if not pos:
        return False
    user_db.delete(pos)
    user_db.commit()
    return True

def sell_position(
    user_db: Session, *, position_id: int,
    exit_date: date, exit_price: float, exit_shares: float,
    exit_reason: str, memo: str = None,
) -> PositionHistory | None:
    pos = user_db.query(PortfolioPosition).filter_by(id=position_id).first()
    if pos is None:
        return None

    if exit_shares > pos.shares:
        return None

    pnl = calc_pnl(
        entry_price=pos.entry_price,
        exit_price=exit_price,
        shares=exit_shares,
        entry_date=pos.entry_date,
        exit_date=exit_date,
    )

    hist = PositionHistory(
        portfolio_id=pos.portfolio_id, symbol_id=pos.symbol_id,
        ticker=pos.ticker, exchange=pos.exchange,
        entry_date=pos.entry_date, entry_price=pos.entry_price,
        entry_shares=exit_shares,
        exit_date=exit_date, exit_price=exit_price,
        exit_shares=exit_shares, exit_reason=exit_reason,
        pnl_pct=pnl["pnl_pct"], pnl_amount=pnl["pnl_amount"],
        holding_days=pnl["holding_days"], memo=memo,
    )
    user_db.add(hist)

    pos.shares -= exit_shares
    if pos.shares <= 0:
        user_db.delete(pos)
    else:
        pos.status = "partially_closed"

    user_db.commit()
    user_db.refresh(hist)
    return hist

def edit_history(
    user_db: Session, history_id: int,
    entry_date: date = None, entry_price: float = None,
    exit_date: date = None, exit_price: float = None, exit_shares: float = None,
    memo: str = None
) -> PositionHistory | None:
    hist = user_db.query(PositionHistory).filter_by(id=history_id).first()
    if not hist:
        return None
    
    if entry_date is not None: hist.entry_date = entry_date
    if entry_price is not None: hist.entry_price = entry_price
    if exit_date is not None: hist.exit_date = exit_date
    if exit_price is not None: hist.exit_price = exit_price
    if exit_shares is not None:
        hist.entry_shares = exit_shares
        hist.exit_shares = exit_shares
    if memo is not None: hist.memo = memo
    
    # Recalculate P&L
    pnl = calc_pnl(
        entry_price=hist.entry_price,
        exit_price=hist.exit_price,
        shares=hist.exit_shares,
        entry_date=hist.entry_date,
        exit_date=hist.exit_date,
    )
    hist.pnl_pct = pnl["pnl_pct"]
    hist.pnl_amount = pnl["pnl_amount"]
    hist.holding_days = pnl["holding_days"]
    
    user_db.commit()
    user_db.refresh(hist)
    return hist

# ============================================================
# 2-4: History
# ============================================================
def get_history(db: Session, user_db: Session, portfolio_id: int) -> list[dict]:
    heal_portfolio_ids(db, user_db)
    records = user_db.query(PositionHistory).filter_by(
        portfolio_id=portfolio_id
    ).order_by(PositionHistory.exit_date.asc(), PositionHistory.id.asc()).all()

    result = []
    cumulative_pnl = 0.0

    for h in records:
        sym = db.query(Symbol).filter_by(id=h.symbol_id).first() if h.symbol_id else None
        cumulative_pnl += (h.pnl_amount or 0.0)
        result.append({
            "id": h.id,
            "ticker": h.ticker,
            "name": sym.name if sym else h.ticker,
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

def get_vxv_vix_regime_info(db: Session, max_positions_limit: int = 8) -> dict:
    """Helper to query VXV/VIX ratios from database and run regime calculation."""
    records = db.query(MarketSignal).order_by(desc(MarketSignal.date)).limit(50).all()
    ratios = [r.vxv_vix_ratio for r in records if r.vxv_vix_ratio is not None]
    ratios.reverse()
    return determine_vxv_vix_ema_regime(ratios, max_positions_limit)


# ============================================================
# 2-5: Portfolio Summary (Risk Dashboard Data)
# ============================================================
def get_portfolio_summary(db: Session, user_db: Session, portfolio_id: int) -> dict | None:
    heal_portfolio_ids(db, user_db)
    pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None

    positions = user_db.query(PortfolioPosition).filter_by(
        portfolio_id=portfolio_id
    ).all()

    # Determine dynamic max positions and target cash ratio based on VXV/VIX EMA
    regime_info = get_vxv_vix_regime_info(db, pf.max_positions)
    dynamic_max_positions = regime_info["dynamic_max_positions"]
    target_cash_ratio = regime_info["target_cash_ratio"]

    # Calculate standard sizing from risk parameters
    sizing = calc_max_investment(
        total_capital=pf.total_capital,
        risk_pct=pf.risk_pct,
        stop_loss_pct=pf.default_stop_loss_pct,
        stop_loss_method="fixed_pct",
    )
    
    risk_max_investment = sizing["max_investment"]
    
    # Calculate weight-based limit under the current regime
    if dynamic_max_positions > 0:
        weight_max_investment = (pf.total_capital * (1.0 - target_cash_ratio)) / dynamic_max_positions
        recommended_max_investment = min(risk_max_investment, weight_max_investment)
    else:
        # BEAR phase (dynamic_max_positions == 0), no new buying recommended
        recommended_max_investment = 0.0

    invested_total = 0.0
    market_value_total = 0.0

    for pos in positions:
        invested_total += pos.entry_price * pos.shares
        latest_dp = _get_latest_price(db, pos.symbol_id) if pos.symbol_id else None
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
        "max_investment": round(recommended_max_investment, 2),  # Reflects the dynamic logic
        "risk_max_investment": round(risk_max_investment, 2),   # Keep risk-only sizing as reference
        "stop_loss_method": pf.stop_loss_method,
        "default_stop_loss_pct": pf.default_stop_loss_pct,
        "max_positions": pf.max_positions,
        "dynamic_max_positions": dynamic_max_positions,
        "open_positions": len(positions),
        "invested_total": round(invested_total, 2),
        "market_value_total": round(market_value_total, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "market_regime": regime_info["regime"],
        "vxv_vix_ema5": regime_info["ema5"],
        "vxv_vix_ema21": regime_info["ema21"],
        "target_cash_ratio": target_cash_ratio,
        "tighten_stop_loss": regime_info["tighten_stop_loss"],
    }


# ============================================================
# 2-6: Analytics (Sector distribution, performance stats)
# ============================================================
def get_analytics(db: Session, user_db: Session, portfolio_id: int) -> dict | None:
    heal_portfolio_ids(db, user_db)
    pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
    if pf is None:
        return None

    positions = user_db.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).all()
    sector_map: dict[str, float] = {}
    for pos in positions:
        sym = db.query(Symbol).filter_by(id=pos.symbol_id).first() if pos.symbol_id else None
        cat = sym.category if sym else "Unknown"
        sector_map[cat] = sector_map.get(cat, 0) + (pos.entry_price * pos.shares)

    sector_breakdown = [
        {"category": k, "invested": round(v, 2)}
        for k, v in sorted(sector_map.items(), key=lambda x: -x[1])
    ]

    history = user_db.query(PositionHistory).filter_by(portfolio_id=portfolio_id)\
        .order_by(PositionHistory.exit_date.asc(), PositionHistory.id.asc()).all()

    total_trades = len(history)
    wins = [h for h in history if (h.pnl_pct or 0) > 0]
    losses = [h for h in history if (h.pnl_pct or 0) <= 0]
    win_count = len(wins)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = sum(h.pnl_pct or 0 for h in wins) / win_count if win_count else 0.0
    avg_loss = sum(h.pnl_pct or 0 for h in losses) / len(losses) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss) if total_trades > 0 else 0.0

    equity_curve = []
    cumulative = 0.0
    for h in history:
        cumulative += (h.pnl_amount or 0)
        sym = db.query(Symbol).filter_by(id=h.symbol_id).first() if h.symbol_id else None
        equity_curve.append({
            "date": h.exit_date.isoformat(),
            "cumulative_pnl": round(cumulative, 2),
            "ticker": h.ticker,
        })

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

# ============================================================
# 2-7: Total Portfolio (Master Account)
# ============================================================
def get_total_portfolio_summary(db: Session, user_db: Session) -> dict:
    heal_portfolio_ids(db, user_db)
    tp = _get_or_create_default_total_portfolio(user_db)

    transactions = user_db.query(Transaction).filter_by(total_portfolio_id=tp.id).all()
    total_deposits_usd = sum(t.amount for t in transactions if t.transaction_type == 'DEPOSIT')
    total_withdrawals_usd = sum(t.amount for t in transactions if t.transaction_type == 'WITHDRAWAL')
    net_injected_capital = total_deposits_usd - total_withdrawals_usd

    total_deposits_jpy = sum(t.local_amount or 0.0 for t in transactions if t.transaction_type == 'DEPOSIT')
    total_withdrawals_jpy = sum(t.local_amount or 0.0 for t in transactions if t.transaction_type == 'WITHDRAWAL')
    net_injected_jpy = total_deposits_jpy - total_withdrawals_jpy

    portfolios = user_db.query(Portfolio).filter_by(total_portfolio_id=tp.id, status="active").all()
    total_allocated_capital = sum(pf.total_capital for pf in portfolios)
    unallocated_cash = net_injected_capital - total_allocated_capital

    total_equities_value = 0.0
    allocated_cash = 0.0

    theme_map = {}
    ticker_map = {}
    
    sub_portfolios_info = []

    for pf in portfolios:
        pf_invested = 0.0
        pf_market_value = 0.0
        
        positions = user_db.query(PortfolioPosition).filter_by(portfolio_id=pf.id, status="open").all()
        for pos in positions:
            sym = db.query(Symbol).filter_by(id=pos.symbol_id).first() if pos.symbol_id else None
            latest_dp = _get_latest_price(db, pos.symbol_id) if pos.symbol_id else None
            current_price = latest_dp.close if latest_dp else pos.entry_price
            
            value = current_price * pos.shares
            pf_invested += pos.entry_price * pos.shares
            pf_market_value += value
            total_equities_value += value
            
            if sym and sym.category in ("テーマ", "セクタ"):
                theme_list = [sym.ticker]
            else:
                tags_str = sym.tags if sym and sym.tags else "Uncategorized"
                theme_list = [t.strip() for t in tags_str.split(",") if t.strip()]
                if not theme_list:
                    theme_list = ["Uncategorized"]
            
            val_per_theme = value / len(theme_list)
            for t in theme_list:
                theme_map[t] = theme_map.get(t, 0.0) + val_per_theme
            
            ticker = pos.ticker
            ticker_map[ticker] = ticker_map.get(ticker, 0.0) + value
            
        pf_cash = pf.total_capital - pf_invested
        allocated_cash += pf_cash
        
        sub_portfolios_info.append({
            "id": pf.id,
            "name": pf.name,
            "total_capital": pf.total_capital,
            "invested": pf_invested,
            "cash": pf_cash,
            "market_value": pf_market_value,
            "unrealized_pnl": pf_market_value - pf_invested,
        })

    total_system_cash = unallocated_cash + allocated_cash
    total_equity_value = total_system_cash + total_equities_value
    total_unrealized_pnl = total_equity_value - net_injected_capital
    total_unrealized_pnl_pct = (total_unrealized_pnl / net_injected_capital * 100) if net_injected_capital > 0 else 0.0

    theme_breakdown = [{"name": k, "value": round(v, 2)} for k, v in sorted(theme_map.items(), key=lambda x: -x[1])]
    ticker_breakdown = [{"name": k, "value": round(v, 2)} for k, v in sorted(ticker_map.items(), key=lambda x: -x[1])]
    asset_allocation = [
        {"name": "Cash", "value": round(total_system_cash, 2)},
        {"name": "Equities", "value": round(total_equities_value, 2)}
    ]

    latest_signal = db.query(MarketSignal).order_by(desc(MarketSignal.date)).first()
    if latest_signal:
        phase = latest_signal.market_phase
        score = latest_signal.market_trend_score
    else:
        phase = "UNKNOWN"
        score = 50.0

    # Dynamic cash recommendation based on VXV/VIX EMA regime (fallback to original if UNKNOWN)
    regime_info = get_vxv_vix_regime_info(db, 8)
    if regime_info["regime"] != "UNKNOWN":
        regime = regime_info["regime"]
        if regime == "BULL":
            min_pct = 0
            max_pct = 20
        elif regime == "BOTTOM":
            min_pct = 50
            max_pct = 70
        elif regime == "OVERHEAT":
            min_pct = 30
            max_pct = 50
        elif regime == "BEAR":
            min_pct = 70
            max_pct = 100
        else:
            min_pct = 50
            max_pct = 100
        recommended_cash = {
            "phase": f"VIX EMA: {regime}",
            "trend_score": score,
            "recommended_min_pct": min_pct,
            "recommended_max_pct": max_pct,
            "message": f"Based on VXV/VIX EMA {regime} phase, recommended cash is {min_pct}%-{max_pct}%."
        }
    else:
        recommended_cash = calculate_recommended_cash(phase, score)


    # Fetch latest USD/JPY from FxRate
    latest_fx = db.query(FxRate).filter_by(currency_pair="USD/JPY").order_by(desc(FxRate.date)).first()
    current_exchange_rate = latest_fx.rate if latest_fx else 150.0

    total_equity_jpy = total_equity_value * current_exchange_rate
    total_unrealized_pnl_jpy = total_equity_jpy - net_injected_jpy
    total_unrealized_pnl_pct_jpy = (total_unrealized_pnl_jpy / net_injected_jpy * 100) if net_injected_jpy > 0 else 0.0

    return {
        "total_portfolio_id": tp.id,
        "name": tp.name,
        "currency": tp.currency,
        "net_injected_capital": round(net_injected_capital, 2),
        "net_injected_jpy": round(net_injected_jpy, 0),
        "total_allocated_capital": round(total_allocated_capital, 2),
        "unallocated_cash": round(unallocated_cash, 2),
        "allocated_cash": round(allocated_cash, 2),
        "total_system_cash": round(total_system_cash, 2),
        "total_equities_value": round(total_equities_value, 2),
        "total_equity_value": round(total_equity_value, 2),
        "total_equity_jpy": round(total_equity_jpy, 0),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "total_unrealized_pnl_pct": round(total_unrealized_pnl_pct, 2),
        "total_unrealized_pnl_jpy": round(total_unrealized_pnl_jpy, 0),
        "total_unrealized_pnl_pct_jpy": round(total_unrealized_pnl_pct_jpy, 2),
        "current_exchange_rate": round(current_exchange_rate, 2),
        "asset_allocation": asset_allocation,
        "theme_breakdown": theme_breakdown,
        "ticker_breakdown": ticker_breakdown,
        "recommended_cash": recommended_cash,
        "sub_portfolios": sub_portfolios_info,
    }
    
def get_historical_fx_rate(db: Session, target_date: date) -> float:
    fx = db.query(FxRate).filter(
        FxRate.currency_pair == "USD/JPY",
        FxRate.date <= target_date
    ).order_by(desc(FxRate.date)).first()
    
    if fx:
        return fx.rate
    
    # Fallback to earliest available
    fx_fallback = db.query(FxRate).filter(
        FxRate.currency_pair == "USD/JPY"
    ).order_by(FxRate.date).first()
    return fx_fallback.rate if fx_fallback else 150.0


def execute_transaction(
    user_db: Session, 
    transaction_type: str, 
    amount: float, 
    date: date, 
    portfolio_id: int = None, 
    memo: str = None,
    local_amount: float = None,
    exchange_rate: float = None,
    local_currency: str = None
) -> Transaction:
    tp = _get_or_create_default_total_portfolio(user_db)
    
    t = Transaction(
        total_portfolio_id=tp.id,
        portfolio_id=portfolio_id,
        transaction_type=transaction_type,
        amount=amount,
        local_amount=local_amount,
        exchange_rate=exchange_rate,
        local_currency=local_currency,
        date=date,
        memo=memo
    )
    user_db.add(t)
    
    if portfolio_id:
        pf = user_db.query(Portfolio).filter_by(id=portfolio_id).first()
        if pf:
            if transaction_type == "FUNDING":
                pf.total_capital += amount
            elif transaction_type == "REFUND":
                pf.total_capital -= amount
                
    user_db.commit()
    user_db.refresh(t)
    return t

def get_transactions(user_db: Session) -> list[dict]:
    tp = _get_or_create_default_total_portfolio(user_db)
    transactions = user_db.query(Transaction).filter_by(total_portfolio_id=tp.id).order_by(desc(Transaction.date), desc(Transaction.id)).all()
    
    result = []
    for t in transactions:
        pf_name = None
        if t.portfolio_id:
            pf = user_db.query(Portfolio).filter_by(id=t.portfolio_id).first()
            if pf:
                pf_name = pf.name
        
        result.append({
            "id": t.id,
            "transaction_type": t.transaction_type,
            "amount": t.amount,
            "date": t.date.isoformat(),
            "memo": t.memo,
            "portfolio_id": t.portfolio_id,
            "portfolio_name": pf_name,
        })
    return result
