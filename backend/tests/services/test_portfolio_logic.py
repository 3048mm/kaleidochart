"""
TDD tests for portfolio core logic: position sizing, stop-loss, PnL, alerts.
Tests the pure calculation functions in portfolio_logic.py.
Phase 1 - Steps 1-1 through 1-5.
"""
import pytest
from datetime import date

from api.portfolio_logic import (
    calc_max_investment,
    calc_stop_loss_price,
    calc_pnl,
    check_alert_status,
)


# ============================================================
# 1-1 & 1-2: Position Sizing
# ============================================================
class TestCalcMaxInvestment:
    """Test position sizing calculations."""

    # --- 1-1: Fixed percentage method ---
    def test_fixed_pct_basic(self):
        """Total ¥10M, risk 1%, stop 8% → risk_amount=¥100,000, max_investment=¥1,250,000."""
        result = calc_max_investment(
            total_capital=10_000_000,
            risk_pct=1.0,
            stop_loss_pct=8.0,
            stop_loss_method="fixed_pct",
        )
        assert result["risk_amount"] == 100_000
        assert result["max_investment"] == 1_250_000
        assert result["stop_loss_distance_pct"] == 8.0

    def test_fixed_pct_different_params(self):
        """Total ¥5M, risk 0.5%, stop 5% → risk_amount=¥25,000, max_investment=¥500,000."""
        result = calc_max_investment(
            total_capital=5_000_000,
            risk_pct=0.5,
            stop_loss_pct=5.0,
            stop_loss_method="fixed_pct",
        )
        assert result["risk_amount"] == 25_000
        assert result["max_investment"] == 500_000
        assert result["stop_loss_distance_pct"] == 5.0

    def test_fixed_pct_no_max_shares(self):
        """Fixed % method should not return max_shares (price-independent)."""
        result = calc_max_investment(
            total_capital=10_000_000,
            risk_pct=1.0,
            stop_loss_pct=8.0,
            stop_loss_method="fixed_pct",
        )
        assert "max_shares" not in result or result["max_shares"] is None

    # --- 1-2: ATR-based method ---
    def test_atr_method_basic(self):
        """Total ¥10M, risk 1%, ATR=$5, multiplier=2.0, price=$100 → max_shares=100, max_investment=$10,000."""
        result = calc_max_investment(
            total_capital=10_000_000,
            risk_pct=1.0,
            stop_loss_method="atr_multiple",
            atr=5.0,
            atr_multiplier=2.0,
            current_price=100.0,
        )
        risk_amount = 10_000_000 * 0.01  # ¥100,000
        stop_distance = 5.0 * 2.0        # $10 per share
        expected_shares = int(risk_amount / stop_distance)  # 10,000
        expected_investment = expected_shares * 100.0

        assert result["risk_amount"] == risk_amount
        assert result["max_shares"] == expected_shares
        assert result["max_investment"] == expected_investment

    def test_atr_method_volatile_stock(self):
        """Volatile stock (ATR=$8) gets smaller position than calm stock (ATR=$2)."""
        calm = calc_max_investment(
            total_capital=10_000_000, risk_pct=1.0,
            stop_loss_method="atr_multiple", atr=2.0, atr_multiplier=2.0,
            current_price=100.0,
        )
        volatile = calc_max_investment(
            total_capital=10_000_000, risk_pct=1.0,
            stop_loss_method="atr_multiple", atr=8.0, atr_multiplier=2.0,
            current_price=100.0,
        )
        assert calm["max_investment"] > volatile["max_investment"]
        assert calm["max_shares"] > volatile["max_shares"]

    def test_atr_method_stop_loss_distance_pct(self):
        """ATR=$5, multiplier=2.0, price=$100 → stop_loss_distance_pct=10.0."""
        result = calc_max_investment(
            total_capital=10_000_000, risk_pct=1.0,
            stop_loss_method="atr_multiple", atr=5.0, atr_multiplier=2.0,
            current_price=100.0,
        )
        expected_pct = (5.0 * 2.0) / 100.0 * 100  # 10.0%
        assert abs(result["stop_loss_distance_pct"] - expected_pct) < 0.01

    # --- Edge cases ---
    def test_invalid_method_raises_error(self):
        """Unknown stop_loss_method should raise ValueError."""
        with pytest.raises(ValueError):
            calc_max_investment(
                total_capital=10_000_000, risk_pct=1.0,
                stop_loss_method="unknown",
            )

    def test_atr_missing_params_raises_error(self):
        """ATR method without atr or current_price should raise ValueError."""
        with pytest.raises(ValueError):
            calc_max_investment(
                total_capital=10_000_000, risk_pct=1.0,
                stop_loss_method="atr_multiple",
                # atr and current_price missing
            )


# ============================================================
# 1-3: Stop-Loss Price Calculation
# ============================================================
class TestCalcStopLossPrice:
    """Test stop-loss price calculations."""

    def test_fixed_pct_stop_loss(self):
        """Entry $100, stop 8% → stop price = $92."""
        result = calc_stop_loss_price(
            entry_price=100.0, stop_loss_pct=8.0, stop_loss_method="fixed_pct"
        )
        assert result == 92.0

    def test_fixed_pct_stop_loss_5pct(self):
        """Entry $200, stop 5% → stop price = $190."""
        result = calc_stop_loss_price(
            entry_price=200.0, stop_loss_pct=5.0, stop_loss_method="fixed_pct"
        )
        assert result == 190.0

    def test_atr_stop_loss(self):
        """Entry $100, ATR=$5, multiplier=2.0 → stop price = $90."""
        result = calc_stop_loss_price(
            entry_price=100.0, stop_loss_method="atr_multiple",
            atr=5.0, atr_multiplier=2.0,
        )
        assert result == 90.0

    def test_atr_stop_loss_high_volatility(self):
        """Entry $100, ATR=$8, multiplier=2.0 → stop price = $84."""
        result = calc_stop_loss_price(
            entry_price=100.0, stop_loss_method="atr_multiple",
            atr=8.0, atr_multiplier=2.0,
        )
        assert result == 84.0


# ============================================================
# 1-4: PnL Calculation
# ============================================================
class TestCalcPnl:
    """Test PnL calculations."""

    def test_profitable_trade(self):
        """Entry $100 → Exit $120, 50 shares → pnl_pct=20%, pnl_amount=$1000."""
        result = calc_pnl(
            entry_price=100.0, exit_price=120.0, shares=50,
            entry_date=date(2026, 1, 1), exit_date=date(2026, 1, 31),
        )
        assert result["pnl_pct"] == pytest.approx(20.0)
        assert result["pnl_amount"] == pytest.approx(1000.0)
        assert result["holding_days"] == 30

    def test_losing_trade(self):
        """Entry $100 → Exit $92, 100 shares → pnl_pct=-8%, pnl_amount=-$800."""
        result = calc_pnl(
            entry_price=100.0, exit_price=92.0, shares=100,
            entry_date=date(2026, 3, 1), exit_date=date(2026, 3, 10),
        )
        assert result["pnl_pct"] == pytest.approx(-8.0)
        assert result["pnl_amount"] == pytest.approx(-800.0)
        assert result["holding_days"] == 9

    def test_breakeven_trade(self):
        """Entry == Exit → pnl_pct=0, pnl_amount=0."""
        result = calc_pnl(
            entry_price=100.0, exit_price=100.0, shares=100,
            entry_date=date(2026, 5, 1), exit_date=date(2026, 5, 1),
        )
        assert result["pnl_pct"] == pytest.approx(0.0)
        assert result["pnl_amount"] == pytest.approx(0.0)
        assert result["holding_days"] == 0

    def test_partial_sell_pnl(self):
        """Trim: entry $100, exit $110, 33 shares → pnl_amount=$330."""
        result = calc_pnl(
            entry_price=100.0, exit_price=110.0, shares=33,
            entry_date=date(2026, 2, 1), exit_date=date(2026, 2, 15),
        )
        assert result["pnl_pct"] == pytest.approx(10.0)
        assert result["pnl_amount"] == pytest.approx(330.0)
        assert result["holding_days"] == 14


# ============================================================
# 1-5: Alert Status
# ============================================================
class TestCheckAlertStatus:
    """Test alert status checking logic."""

    def test_stop_loss_alert_triggered(self):
        """Price within 2% of stop loss → alert True."""
        result = check_alert_status(
            current_price=93.0,
            entry_price=100.0,
            stop_loss_price=92.0,
            alert_threshold_pct=2.0,
        )
        assert result["stop_loss_alert"] is True
        # Distance: (93 - 92) / 93 * 100 ≈ 1.075%
        assert result["distance_to_stop_pct"] < 2.0

    def test_stop_loss_alert_not_triggered(self):
        """Price far from stop loss → alert False."""
        result = check_alert_status(
            current_price=100.0,
            entry_price=100.0,
            stop_loss_price=92.0,
            alert_threshold_pct=2.0,
        )
        assert result["stop_loss_alert"] is False
        assert result["distance_to_stop_pct"] > 2.0

    def test_price_below_stop_loss(self):
        """Price already below stop → alert True, negative distance."""
        result = check_alert_status(
            current_price=90.0,
            entry_price=100.0,
            stop_loss_price=92.0,
            alert_threshold_pct=2.0,
        )
        assert result["stop_loss_alert"] is True
        assert result["distance_to_stop_pct"] < 0

    def test_take_profit_alert(self):
        """Price reaches take-profit level → take_profit_alert True."""
        result = check_alert_status(
            current_price=122.0,
            entry_price=100.0,
            stop_loss_price=92.0,
            take_profit_prices=[120.0],
        )
        assert result["take_profit_alert"] is True

    def test_take_profit_not_triggered(self):
        """Price below take-profit → take_profit_alert False."""
        result = check_alert_status(
            current_price=115.0,
            entry_price=100.0,
            stop_loss_price=92.0,
            take_profit_prices=[120.0],
        )
        assert result["take_profit_alert"] is False

    def test_no_take_profit_configured(self):
        """No take-profit prices → take_profit_alert False."""
        result = check_alert_status(
            current_price=150.0,
            entry_price=100.0,
            stop_loss_price=92.0,
        )
        assert result["take_profit_alert"] is False
