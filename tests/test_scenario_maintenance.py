"""
Tests for scenario test maintenance improvements.

Covers:
- Progress tracking (start/running/completed lifecycle)
- Max Drawdown calculation (with date and tickers)
- Profit Factor calculation
- Equity curve recording
"""
import pytest
import pandas as pd
import numpy as np
import datetime
import os
import json
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============================================================
# Progress Tracking Tests
# ============================================================
class TestProgressTracking:
    """Tests for update_scenario_progress and get_scenario_progress."""

    @pytest.fixture(autouse=True)
    def setup_tmp_dir(self, tmp_path):
        """Create a temporary directory for progress files."""
        self.output_dir = str(tmp_path / "scenario_output")
        os.makedirs(self.output_dir, exist_ok=True)

    def test_initial_progress_is_not_started(self):
        """get_scenario_progress returns 'not_started' when no file exists."""
        from backend.backtest.scenario_runner import get_scenario_progress
        result = get_scenario_progress(output_dir=self.output_dir)
        assert result["status"] == "not_started"

    def test_progress_writes_running_status(self):
        """update_scenario_progress writes a file with 'running' status."""
        from backend.backtest.scenario_runner import update_scenario_progress, get_scenario_progress
        import time

        update_scenario_progress(
            current_idx=10, total_days=100, start_time=time.time() - 5.0,
            active_pos_count=3, total_trades=7,
            current_date_str="2024-02-15", output_dir=self.output_dir,
        )

        result = get_scenario_progress(output_dir=self.output_dir)
        assert result["status"] == "running"
        assert result["progress_pct"] == 10.0
        assert result["processed_days"] == 10
        assert result["total_days"] == 100
        assert result["active_positions"] == 3
        assert result["total_trades"] == 7
        assert result["current_date"] == "2024-02-15"
        assert result["eta_seconds"] > 0

    def test_progress_writes_completed_status(self):
        """When current_idx == total_days, status should be 'completed'."""
        from backend.backtest.scenario_runner import update_scenario_progress, get_scenario_progress
        import time

        update_scenario_progress(
            current_idx=100, total_days=100, start_time=time.time() - 30.0,
            active_pos_count=5, total_trades=42,
            current_date_str="2024-12-31", output_dir=self.output_dir,
        )

        result = get_scenario_progress(output_dir=self.output_dir)
        assert result["status"] == "completed"
        assert result["progress_pct"] == 100.0
        assert result["eta_seconds"] == 0.0

    def test_progress_file_is_in_output_dir(self):
        """Progress file should be created inside the specified output_dir."""
        from backend.backtest.scenario_runner import update_scenario_progress, PROGRESS_FILENAME
        import time

        update_scenario_progress(
            current_idx=1, total_days=10, start_time=time.time(),
            active_pos_count=0, total_trades=0,
            current_date_str="2024-01-01", output_dir=self.output_dir,
        )

        expected_path = os.path.join(self.output_dir, PROGRESS_FILENAME)
        assert os.path.exists(expected_path)

    def test_progress_lifecycle(self):
        """Test the full lifecycle: not_started -> running -> completed."""
        from backend.backtest.scenario_runner import update_scenario_progress, get_scenario_progress
        import time

        # Phase 1: Not started
        assert get_scenario_progress(output_dir=self.output_dir)["status"] == "not_started"

        # Phase 2: Running
        start = time.time()
        update_scenario_progress(
            current_idx=0, total_days=50, start_time=start,
            active_pos_count=0, total_trades=0,
            current_date_str="2024-01-01", output_dir=self.output_dir,
        )
        assert get_scenario_progress(output_dir=self.output_dir)["status"] == "running"

        # Phase 3: Mid-run
        update_scenario_progress(
            current_idx=25, total_days=50, start_time=start,
            active_pos_count=6, total_trades=15,
            current_date_str="2024-06-15", output_dir=self.output_dir,
        )
        result = get_scenario_progress(output_dir=self.output_dir)
        assert result["status"] == "running"
        assert result["progress_pct"] == 50.0

        # Phase 4: Completed
        update_scenario_progress(
            current_idx=50, total_days=50, start_time=start,
            active_pos_count=3, total_trades=40,
            current_date_str="2024-12-31", output_dir=self.output_dir,
        )
        assert get_scenario_progress(output_dir=self.output_dir)["status"] == "completed"


# ============================================================
# Max Drawdown Tests
# ============================================================
class TestMaxDrawdown:
    """Tests for ScenarioReporter._calculate_max_drawdown."""

    def test_no_drawdown_monotonically_rising(self):
        """No drawdown when equity only goes up."""
        from backend.backtest.scenario_reporter import ScenarioReporter

        equity_curve = [
            {"date": datetime.date(2024, 1, i+1), "total_equity": 100000 + i * 1000,
             "held_tickers": "AAPL,MSFT", "cash": 50000, "invested": 50000 + i * 1000, "positions": 2}
            for i in range(10)
        ]

        result = ScenarioReporter._calculate_max_drawdown(equity_curve)
        assert result["pct"] == 0.0
        assert result["amount"] == 0.0

    def test_drawdown_with_recovery(self):
        """Drawdown detected in a V-shaped equity curve."""
        from backend.backtest.scenario_reporter import ScenarioReporter

        equity_curve = [
            {"date": datetime.date(2024, 1, 1), "total_equity": 100000, "held_tickers": "AAPL", "cash": 50000, "invested": 50000, "positions": 1},
            {"date": datetime.date(2024, 1, 2), "total_equity": 105000, "held_tickers": "AAPL,MSFT", "cash": 30000, "invested": 75000, "positions": 2},
            # Peak at 105000
            {"date": datetime.date(2024, 1, 3), "total_equity": 95000, "held_tickers": "AAPL,GOOG", "cash": 40000, "invested": 55000, "positions": 2},
            {"date": datetime.date(2024, 1, 4), "total_equity": 90000, "held_tickers": "GOOG,TSLA", "cash": 35000, "invested": 55000, "positions": 2},
            # Trough at 90000 -> DD = (105000-90000)/105000 = 14.29%
            {"date": datetime.date(2024, 1, 5), "total_equity": 110000, "held_tickers": "GOOG,TSLA,NVDA", "cash": 20000, "invested": 90000, "positions": 3},
        ]

        result = ScenarioReporter._calculate_max_drawdown(equity_curve)
        assert abs(result["pct"] - 14.29) < 0.01
        assert abs(result["amount"] - 15000) < 0.01
        assert result["peak_date"] == "2024-01-02"
        assert result["trough_date"] == "2024-01-04"
        assert result["trough_tickers"] == ["GOOG", "TSLA"]

    def test_drawdown_at_end(self):
        """Drawdown persists without recovery at end of curve."""
        from backend.backtest.scenario_reporter import ScenarioReporter

        equity_curve = [
            {"date": datetime.date(2024, 1, 1), "total_equity": 100000, "held_tickers": "", "cash": 100000, "invested": 0, "positions": 0},
            {"date": datetime.date(2024, 1, 2), "total_equity": 98000, "held_tickers": "AAPL", "cash": 50000, "invested": 48000, "positions": 1},
            {"date": datetime.date(2024, 1, 3), "total_equity": 95000, "held_tickers": "AAPL,BAD", "cash": 30000, "invested": 65000, "positions": 2},
        ]

        result = ScenarioReporter._calculate_max_drawdown(equity_curve)
        assert result["pct"] == 5.0
        assert result["trough_date"] == "2024-01-03"
        assert "AAPL" in result["trough_tickers"]
        assert "BAD" in result["trough_tickers"]

    def test_empty_equity_curve(self):
        """Empty or too-short equity curve returns zeros."""
        from backend.backtest.scenario_reporter import ScenarioReporter

        result = ScenarioReporter._calculate_max_drawdown([])
        assert result["pct"] == 0.0
        assert result["trough_tickers"] == []

        result2 = ScenarioReporter._calculate_max_drawdown(None)
        assert result2["pct"] == 0.0


# ============================================================
# Profit Factor Tests
# ============================================================
class TestProfitFactor:
    """Tests for profit factor calculation in ScenarioReporter."""

    def test_profit_factor_with_wins_and_losses(self):
        from backend.backtest.scenario_reporter import ScenarioReporter

        trades = [
            {"pnl_amount": 500, "pnl_pct": 0.05, "entry_date": "2024-01-01", "exit_date": "2024-01-15", "ticker": "AAPL"},
            {"pnl_amount": 300, "pnl_pct": 0.03, "entry_date": "2024-01-01", "exit_date": "2024-01-10", "ticker": "MSFT"},
            {"pnl_amount": -200, "pnl_pct": -0.02, "entry_date": "2024-01-01", "exit_date": "2024-01-05", "ticker": "BAD1"},
            {"pnl_amount": -100, "pnl_pct": -0.01, "entry_date": "2024-01-01", "exit_date": "2024-01-07", "ticker": "BAD2"},
        ]

        reporter = ScenarioReporter()
        summary = reporter.generate_summary(
            trade_history=trades, initial_capital=10000.0, final_capital=10500.0,
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 31),
            spy_prices=pd.DataFrame(), equity_curve=[], run_params=None,
        )

        # Profit Factor = 800 / 300 = 2.67
        assert summary["profit_factor"] == 2.67

    def test_profit_factor_all_wins(self):
        from backend.backtest.scenario_reporter import ScenarioReporter

        trades = [
            {"pnl_amount": 500, "pnl_pct": 0.05, "entry_date": "2024-01-01", "exit_date": "2024-01-15", "ticker": "A"},
        ]

        reporter = ScenarioReporter()
        summary = reporter.generate_summary(
            trade_history=trades, initial_capital=10000.0, final_capital=10500.0,
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 31),
            spy_prices=pd.DataFrame(), equity_curve=[], run_params=None,
        )

        # No losses -> profit_factor should be 0 (cannot divide by zero)
        assert summary["profit_factor"] == 0.0

    def test_profit_factor_no_trades(self):
        from backend.backtest.scenario_reporter import ScenarioReporter

        reporter = ScenarioReporter()
        summary = reporter.generate_summary(
            trade_history=[], initial_capital=10000.0, final_capital=10000.0,
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 31),
            spy_prices=pd.DataFrame(), equity_curve=[], run_params=None,
        )

        assert summary["profit_factor"] == 0.0


# ============================================================
# Equity Curve Recording Tests
# ============================================================
class TestEquityCurve:
    """Tests for ScenarioPortfolio.record_daily_equity."""

    def test_equity_records_correct_snapshot(self):
        from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig

        config = PortfolioConfig(initial_capital=100000)
        portfolio = ScenarioPortfolio(config)

        # Simulate: no positions on day 1
        portfolio.record_daily_equity(datetime.date(2024, 1, 1))
        assert len(portfolio.equity_curve) == 1
        snap = portfolio.equity_curve[0]
        assert snap["total_equity"] == 100000.0
        assert snap["positions"] == 0
        assert snap["held_tickers"] == ""

    def test_equity_with_active_positions_uses_close(self):
        from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig

        config = PortfolioConfig(initial_capital=100000)
        portfolio = ScenarioPortfolio(config)

        # Simulate a position
        portfolio.capital = 90000
        portfolio.active_positions = [
            {"symbol_id": 1, "ticker": "AAPL", "entry_price": 100, "shares": 100, "amount": 10000}
        ]

        # Mock price data: AAPL closes at $110
        price_day = pd.DataFrame({"symbol_id": [1], "close": [110.0]})
        price_by_date = {datetime.date(2024, 1, 2): price_day}

        portfolio.record_daily_equity(datetime.date(2024, 1, 2), price_by_date)
        snap = portfolio.equity_curve[-1]
        # total = 90000 (cash) + 100 * 110 (floating) = 101000
        assert snap["total_equity"] == 101000.0
        assert snap["held_tickers"] == "AAPL"

    def test_equity_fallback_to_entry_cost_when_no_price(self):
        from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig

        config = PortfolioConfig(initial_capital=100000)
        portfolio = ScenarioPortfolio(config)

        portfolio.capital = 90000
        portfolio.active_positions = [
            {"symbol_id": 1, "ticker": "AAPL", "entry_price": 100, "shares": 100, "amount": 10000}
        ]

        # No price data available -> fallback to entry cost
        portfolio.record_daily_equity(datetime.date(2024, 1, 2))
        snap = portfolio.equity_curve[-1]
        # total = 90000 + 10000 (entry cost) = 100000
        assert snap["total_equity"] == 100000.0


# ============================================================
# Run Parameters (S3) Tests
# ============================================================
class TestRunParams:
    """Tests for run parameter recording in summary."""

    def test_run_params_included_in_summary(self):
        from backend.backtest.scenario_reporter import ScenarioReporter

        reporter = ScenarioReporter()
        params = {"min_score": 2, "stop_loss_pct": -0.08}
        summary = reporter.generate_summary(
            trade_history=[], initial_capital=10000.0, final_capital=10000.0,
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 31),
            spy_prices=pd.DataFrame(), equity_curve=[], run_params=params,
        )

        assert "run_params" in summary
        assert summary["run_params"]["min_score"] == 2
        assert summary["run_params"]["stop_loss_pct"] == -0.08

    def test_run_params_omitted_when_none(self):
        from backend.backtest.scenario_reporter import ScenarioReporter

        reporter = ScenarioReporter()
        summary = reporter.generate_summary(
            trade_history=[], initial_capital=10000.0, final_capital=10000.0,
            start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 3, 31),
            spy_prices=pd.DataFrame(), equity_curve=[], run_params=None,
        )

        assert "run_params" not in summary
