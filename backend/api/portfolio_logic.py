"""
Portfolio management core logic.
Pure functions for position sizing, stop-loss/take-profit calculation,
PnL computation, and alert status checking.
Separated from the API layer for testability.
"""
from datetime import date


def calc_max_investment(
    total_capital: float,
    risk_pct: float,
    stop_loss_pct: float = None,
    stop_loss_method: str = "fixed_pct",
    atr: float = None,
    atr_multiplier: float = 2.0,
    current_price: float = None,
) -> dict:
    """
    Calculate the maximum investment amount per position based on risk parameters.

    Args:
        total_capital: Total portfolio capital (in portfolio currency)
        risk_pct: Risk tolerance as percentage of total capital (e.g., 1.0 = 1%)
        stop_loss_pct: Fixed stop loss percentage (e.g., 8.0 = 8%). Used when method='fixed_pct'.
        stop_loss_method: 'fixed_pct' or 'atr_multiple'
        atr: Average True Range value (absolute). Required when method='atr_multiple'.
        atr_multiplier: ATR multiplier (default 2.0). Used when method='atr_multiple'.
        current_price: Current stock price. Required when method='atr_multiple'.

    Returns:
        dict with keys:
            - risk_amount: Amount at risk per trade (portfolio currency)
            - max_investment: Maximum investment amount per position
            - max_shares: Maximum number of shares (only for atr_multiple)
            - stop_loss_distance_pct: Effective stop loss distance as percentage
    """
    risk_amount = total_capital * (risk_pct / 100.0)

    if stop_loss_method == "fixed_pct":
        if stop_loss_pct is None or stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct must be > 0 for fixed_pct method")
        max_investment = risk_amount / (stop_loss_pct / 100.0)
        return {
            "risk_amount": risk_amount,
            "max_investment": max_investment,
            "max_shares": None,
            "stop_loss_distance_pct": stop_loss_pct,
        }

    elif stop_loss_method == "atr_multiple":
        if atr is None or current_price is None:
            raise ValueError("atr and current_price are required for atr_multiple method")
        if atr <= 0 or current_price <= 0:
            raise ValueError("atr and current_price must be > 0")
        stop_distance = atr * atr_multiplier
        max_shares = int(risk_amount / stop_distance)
        max_investment = max_shares * current_price
        stop_loss_distance_pct = (stop_distance / current_price) * 100.0
        return {
            "risk_amount": risk_amount,
            "max_investment": max_investment,
            "max_shares": max_shares,
            "stop_loss_distance_pct": stop_loss_distance_pct,
        }

    else:
        raise ValueError(f"Unknown stop_loss_method: {stop_loss_method}")


def calc_stop_loss_price(
    entry_price: float,
    stop_loss_pct: float = None,
    stop_loss_method: str = "fixed_pct",
    atr: float = None,
    atr_multiplier: float = 2.0,
) -> float:
    """
    Calculate the stop-loss price for a position.

    Args:
        entry_price: Purchase price per share
        stop_loss_pct: Fixed stop loss percentage (e.g., 8.0)
        stop_loss_method: 'fixed_pct' or 'atr_multiple'
        atr: Average True Range value (absolute)
        atr_multiplier: ATR multiplier (default 2.0)

    Returns:
        Stop-loss price (float)
    """
    if stop_loss_method == "fixed_pct":
        return entry_price * (1.0 - stop_loss_pct / 100.0)
    elif stop_loss_method == "atr_multiple":
        return entry_price - (atr * atr_multiplier)
    else:
        raise ValueError(f"Unknown stop_loss_method: {stop_loss_method}")


def calc_pnl(
    entry_price: float,
    exit_price: float,
    shares: int,
    entry_date: date,
    exit_date: date,
) -> dict:
    """
    Calculate profit and loss for a trade.

    Args:
        entry_price: Purchase price per share
        exit_price: Sale price per share
        shares: Number of shares sold
        entry_date: Date of purchase
        exit_date: Date of sale

    Returns:
        dict with keys:
            - pnl_pct: Percentage gain/loss
            - pnl_amount: Absolute gain/loss (currency)
            - holding_days: Number of calendar days held
    """
    pnl_pct = (exit_price - entry_price) / entry_price * 100.0
    pnl_amount = (exit_price - entry_price) * shares
    holding_days = (exit_date - entry_date).days
    return {
        "pnl_pct": pnl_pct,
        "pnl_amount": pnl_amount,
        "holding_days": holding_days,
    }


def check_alert_status(
    current_price: float,
    entry_price: float,
    stop_loss_price: float,
    alert_threshold_pct: float = 2.0,
    take_profit_prices: list[float] = None,
) -> dict:
    """
    Check if a position is near stop-loss or take-profit levels.

    Args:
        current_price: Current market price
        entry_price: Purchase price per share
        stop_loss_price: Calculated stop-loss price
        alert_threshold_pct: Distance threshold for alerts (default 2%)
        take_profit_prices: List of take-profit price levels (optional)

    Returns:
        dict with keys:
            - stop_loss_alert: True if within threshold of stop loss
            - take_profit_alert: True if any take-profit level is reached
            - distance_to_stop_pct: Current distance to stop loss as percentage
    """
    # Distance from current price to stop loss, as % of current price
    distance_to_stop_pct = (current_price - stop_loss_price) / current_price * 100.0

    stop_loss_alert = distance_to_stop_pct <= alert_threshold_pct

    # Take-profit check
    take_profit_alert = False
    if take_profit_prices:
        take_profit_alert = any(current_price >= tp for tp in take_profit_prices)

    return {
        "stop_loss_alert": stop_loss_alert,
        "take_profit_alert": take_profit_alert,
        "distance_to_stop_pct": distance_to_stop_pct,
    }
