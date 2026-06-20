"""
test_etf_single_backtest.py — TDD Tests for ETF Single Backtest Engine

Tests the core logic of etf_single_runner.py and etf_single_reporter.py
using synthetic price data (no DB/Parquet dependency).
"""
import pytest
import datetime
from typing import Dict, List, Any


# ============================================================
# Test Group 1: VxvVixRegimeEngine — Regime Detection
# ============================================================

class TestVxvVixRegimeEngine:
    """Tests for the VXV/VIX EMA regime classification engine."""

    def _make_engine(self):
        from backend.backtest.etf_single_runner import VxvVixRegimeEngine
        return VxvVixRegimeEngine()

    def test_warmup_defaults_to_bull(self):
        """During the first 20 days (warm-up), regime should default to BULL."""
        engine = self._make_engine()
        for i in range(20):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            regime = engine.update(25.0, 20.0, day)  # ratio = 1.25
        assert regime == "BULL"
        assert engine.current_target_pct == 1.0

    def test_bull_regime_ema5_gt_ema21(self):
        """After warm-up, if EMA5 > EMA21 and EMA5 in [1.00, 1.20], regime is BULL."""
        engine = self._make_engine()
        # Feed 40 days of stable ratio = 1.12 — ensures EMA5 and EMA21 converge above crossover
        for i in range(40):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            engine.update(22.4, 20.0, day)  # ratio = 1.12
        assert engine.current_regime == "BULL"
        assert engine.current_target_pct == 1.0

    def test_bear_regime_ema5_lt_ema21(self):
        """When EMA5 drops below EMA21, regime transitions to BEAR."""
        engine = self._make_engine()
        # 25 days of ratio = 1.15 (establish BULL baseline)
        for i in range(25):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            engine.update(23.0, 20.0, day)  # ratio = 1.15
        # Then drop ratio sharply to 1.02 for 10 days (EMA5 drops below EMA21)
        for i in range(10):
            day = datetime.date(2020, 1, 26) + datetime.timedelta(days=i)
            engine.update(20.4, 20.0, day)  # ratio = 1.02
        assert engine.current_regime == "BEAR"
        assert engine.current_target_pct == 0.0

    def test_bottom_regime_ema5_below_1(self):
        """When EMA5 < 1.00, regime is BOTTOM (deep bear panic)."""
        engine = self._make_engine()
        # 25 days normal
        for i in range(25):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            engine.update(22.0, 20.0, day)  # ratio = 1.10
        # Then extreme panic: ratio < 1.0 for many days
        for i in range(30):
            day = datetime.date(2020, 1, 26) + datetime.timedelta(days=i)
            engine.update(18.0, 20.0, day)  # ratio = 0.90
        assert engine.current_regime == "BOTTOM"
        assert engine.current_target_pct == 0.25

    def test_overheat_regime_ema5_above_120(self):
        """When EMA5 > 1.20, regime is OVERHEAT."""
        engine = self._make_engine()
        # Feed ratio consistently above 1.25 to push EMA5 > 1.20
        for i in range(40):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            engine.update(25.0, 19.0, day)  # ratio ≈ 1.316
        assert engine.current_regime == "OVERHEAT"
        assert engine.current_target_pct == 0.50

    def test_regime_history_tracking(self):
        """Regime history should record one entry per day."""
        engine = self._make_engine()
        for i in range(5):
            day = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            engine.update(22.0, 20.0, day)
        assert len(engine.regime_history) == 5
        assert engine.regime_history[0]["date"] == datetime.date(2020, 1, 1)

    def test_missing_vix_data_uses_fallback(self):
        """When VIX or VXV data is None, should use fallback ratio 1.10."""
        engine = self._make_engine()
        day = datetime.date(2020, 1, 1)
        regime = engine.update(None, None, day)
        assert regime == "BULL"  # warm-up default
        assert engine.history[-1] == 1.10  # fallback ratio


# ============================================================
# Test Group 2: VXV Strategy Simulation
# ============================================================

class TestVxvStrategy:
    """Tests for the VXV/VIX EMA position-sizing strategy simulation."""

    def _make_prices(self, dates, close_val=100.0):
        """Helper to create price dict {date -> {close: X}}."""
        return {d: {"close": close_val} for d in dates}

    def _make_vix_vxv(self, dates, vix=20.0, vxv=22.0):
        """Helper to create VIX/VXV price dicts."""
        vix_prices = {d: {"close": vix} for d in dates}
        vxv_prices = {d: {"close": vxv} for d in dates}
        return vix_prices, vxv_prices

    def _trading_dates(self, n=100, start=datetime.date(2020, 1, 1)):
        """Generate n sequential dates (skip weekends)."""
        dates = []
        d = start
        while len(dates) < n:
            if d.weekday() < 5:  # Mon-Fri
                dates.append(d)
            d += datetime.timedelta(days=1)
        return dates

    def test_initial_buy_in_bull_regime(self):
        """In BULL regime, initial buy should invest 100% of capital."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(30)
        etf_prices = self._make_prices(dates, 50.0)
        vix_prices, vxv_prices = self._make_vix_vxv(dates)
        result = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        # Should buy shares on first day
        first_eq = result["equity_curve"][0]
        assert first_eq["vxv_shares"] > 0
        assert first_eq["vxv_equity"] <= 10000.0  # Can't exceed initial (remainder cash)

    def test_no_rebalance_when_regime_stable(self):
        """If regime stays BULL throughout, rebalance count should be 0."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(60)
        etf_prices = self._make_prices(dates, 100.0)
        # ratio=1.12 — comfortably in BULL zone after EMA warm-up
        vix_prices, vxv_prices = self._make_vix_vxv(dates, vix=20.0, vxv=22.4)
        result = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        assert result["rebalance_count"] == 0

    def test_regime_change_triggers_rebalance(self):
        """When regime changes from BULL to BEAR, shares should be sold."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(60)
        etf_prices = self._make_prices(dates, 100.0)
        # First 30 days: ratio=1.10 (BULL), then drop to ratio=1.02 (BEAR via EMA crossover)
        vix_prices = {}
        vxv_prices = {}
        for i, d in enumerate(dates):
            if i < 30:
                vix_prices[d] = {"close": 20.0}
                vxv_prices[d] = {"close": 22.0}  # ratio=1.10
            else:
                vix_prices[d] = {"close": 20.0}
                vxv_prices[d] = {"close": 20.4}  # ratio=1.02

        result = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        assert result["regime_changes"] >= 1
        assert result["rebalance_count"] >= 1

    def test_final_liquidation_with_tax(self):
        """On final day, holdings are liquidated and tax applied on gains."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(30)
        # Price starts at 100, stays at 100 — no gain, so no tax effect
        etf_prices = self._make_prices(dates, 100.0)
        vix_prices, vxv_prices = self._make_vix_vxv(dates)
        result_no_tax = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        result_tax = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.20)
        # No price change = no gain = no tax effect
        assert result_no_tax["final_capital"] == result_tax["final_capital"]

    def test_final_liquidation_tax_on_gains(self):
        """Tax should reduce final capital when ETF price has risen."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(60)
        # Gradual price rise from 100 to 150 — ensures shares are held during warm-up too
        etf_prices = {}
        for i, d in enumerate(dates):
            etf_prices[d] = {"close": 100.0 + (50.0 * i / (len(dates) - 1))}
        # ratio=1.12: BULL throughout
        vix_prices, vxv_prices = self._make_vix_vxv(dates, vix=20.0, vxv=22.4)

        result_no_tax = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        result_tax = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.20)
        # With tax, final capital should be less (price rose significantly)
        assert result_tax["final_capital"] < result_no_tax["final_capital"]

    def test_time_in_market_tracking(self):
        """Time in market should be tracked correctly."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(60)
        etf_prices = self._make_prices(dates, 100.0)
        # ratio=1.12: stable BULL after warm-up
        vix_prices, vxv_prices = self._make_vix_vxv(dates, vix=20.0, vxv=22.4)
        result = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        # In BULL with warm-up defaulting to BULL, should be in market 100%
        assert result["time_in_market_pct"] == 100.0

    def test_equity_curve_has_correct_length(self):
        """Equity curve should have one entry per trading day."""
        from backend.backtest.etf_single_runner import _simulate_vxv_strategy
        dates = self._trading_dates(40)
        etf_prices = self._make_prices(dates, 100.0)
        vix_prices, vxv_prices = self._make_vix_vxv(dates)
        result = _simulate_vxv_strategy(dates, etf_prices, vix_prices, vxv_prices, 10000.0, 0.0)
        assert len(result["equity_curve"]) == 40


# ============================================================
# Test Group 3: Buy & Hold
# ============================================================

class TestBuyAndHold:
    """Tests for Buy & Hold strategy."""

    def _trading_dates(self, n=30, start=datetime.date(2020, 1, 1)):
        dates = []
        d = start
        while len(dates) < n:
            if d.weekday() < 5:
                dates.append(d)
            d += datetime.timedelta(days=1)
        return dates

    def test_buys_on_first_day(self):
        """Should buy maximum shares on the first trading day."""
        from backend.backtest.etf_single_runner import _simulate_buy_and_hold
        dates = self._trading_dates(10)
        prices = {d: {"close": 50.0} for d in dates}
        result = _simulate_buy_and_hold(dates, prices, 10000.0, 0.0)
        # 10000 / 50 = 200 shares, equity = 200 * 50 = 10000
        assert result["equity_curve"][0]["buyhold_equity"] == 10000.0

    def test_equity_tracks_price(self):
        """Equity should track ETF price proportionally."""
        from backend.backtest.etf_single_runner import _simulate_buy_and_hold
        dates = self._trading_dates(5)
        prices = {}
        close_vals = [100.0, 110.0, 90.0, 105.0, 120.0]
        for d, c in zip(dates, close_vals):
            prices[d] = {"close": c}
        result = _simulate_buy_and_hold(dates, prices, 10000.0, 0.0)
        # Bought at 100: 100 shares, remainder=0
        # Day 2: 100*110=11000
        assert result["equity_curve"][1]["buyhold_equity"] == 11000.0
        # Day 3: 100*90=9000
        assert result["equity_curve"][2]["buyhold_equity"] == 9000.0

    def test_tax_on_final_gain(self):
        """Tax should be applied on gain at final liquidation."""
        from backend.backtest.etf_single_runner import _simulate_buy_and_hold
        dates = self._trading_dates(5)
        prices = {}
        for i, d in enumerate(dates):
            prices[d] = {"close": 100.0 + i * 10}  # 100, 110, 120, 130, 140
        result_no_tax = _simulate_buy_and_hold(dates, prices, 10000.0, 0.0)
        result_tax = _simulate_buy_and_hold(dates, prices, 10000.0, 0.20)
        # Gain = (140 - 100) * 100 shares = 4000
        # Tax = 4000 * 0.20 = 800
        assert result_tax["final_capital"] == result_no_tax["final_capital"] - 800.0

    def test_no_tax_on_loss(self):
        """No tax should be applied when ETF price drops."""
        from backend.backtest.etf_single_runner import _simulate_buy_and_hold
        dates = self._trading_dates(5)
        prices = {}
        for i, d in enumerate(dates):
            prices[d] = {"close": 100.0 - i * 5}  # 100, 95, 90, 85, 80
        result_no_tax = _simulate_buy_and_hold(dates, prices, 10000.0, 0.0)
        result_tax = _simulate_buy_and_hold(dates, prices, 10000.0, 0.20)
        assert result_tax["final_capital"] == result_no_tax["final_capital"]


# ============================================================
# Test Group 4: DCA (Dollar-Cost Averaging)
# ============================================================

class TestDCA:
    """Tests for Dollar-Cost Averaging strategy."""

    def _trading_dates(self, months=6, start=datetime.date(2020, 1, 2)):
        """Generate trading dates spanning multiple months."""
        dates = []
        d = start
        end = datetime.date(2020, 1 + months, 1) if months < 12 else datetime.date(2021, 1, 1)
        while d < end:
            if d.weekday() < 5:
                dates.append(d)
            d += datetime.timedelta(days=1)
        return dates

    def test_invests_monthly(self):
        """Should invest on first trading day of each month."""
        from backend.backtest.etf_single_runner import _simulate_dca
        dates = self._trading_dates(months=6)
        prices = {d: {"close": 100.0} for d in dates}
        result = _simulate_dca(dates, prices, 60000.0, 0.0, "2020-01-02", "2020-07-01")
        # 7 months (Jan-Jul), monthly = 60000/7 ≈ 8571.4
        # Each month buys int(8571.4 / 100) = 85 shares
        # Final equity should show accumulated shares
        assert result["final_capital"] > 0
        last_eq = result["equity_curve"][-1]
        assert last_eq["dca_equity"] > 0

    def test_dca_final_tax(self):
        """Tax on DCA should be based on average cost basis."""
        from backend.backtest.etf_single_runner import _simulate_dca
        dates = self._trading_dates(months=3)
        prices = {d: {"close": 100.0} for d in dates}
        # Price constant at 100 = no gain = no tax
        result_no_tax = _simulate_dca(dates, prices, 30000.0, 0.0, "2020-01-02", "2020-04-01")
        result_tax = _simulate_dca(dates, prices, 30000.0, 0.20, "2020-01-02", "2020-04-01")
        assert result_no_tax["final_capital"] == result_tax["final_capital"]

    def test_dca_accumulates_shares_over_months(self):
        """Shares should increase month over month."""
        from backend.backtest.etf_single_runner import _simulate_dca
        dates = self._trading_dates(months=4)
        prices = {d: {"close": 50.0} for d in dates}
        result = _simulate_dca(dates, prices, 20000.0, 0.0, "2020-01-02", "2020-05-01")
        # 5 months, 20000/5 = 4000/month, 4000/50 = 80 shares/month
        # After all months: should have ~400 shares total equity ~20000
        assert result["final_capital"] == pytest.approx(20000.0, abs=50.0)


# ============================================================
# Test Group 5: Reporter — Metrics Calculation
# ============================================================

class TestEtfSingleReporter:
    """Tests for the ETF Single Reporter's metric calculations."""

    def _make_equity_curve(self, values, start=datetime.date(2020, 1, 1)):
        """Create a minimal equity curve from a list of equity values."""
        curve = []
        for i, val in enumerate(values):
            d = start + datetime.timedelta(days=i)
            curve.append({"date": d, "equity": val})
        return curve

    def test_cagr_calculation(self):
        """CAGR should be correctly calculated."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        # $100,000 -> $200,000 in 5 years = CAGR ≈ 14.87%
        cagr = reporter.calculate_cagr(100000.0, 200000.0, 5.0)
        assert cagr == pytest.approx(14.87, abs=0.1)

    def test_cagr_zero_years(self):
        """CAGR with 0 years should return 0."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        assert reporter.calculate_cagr(100000.0, 200000.0, 0.0) == 0.0

    def test_max_drawdown_calculation(self):
        """Max drawdown should identify the largest peak-to-trough decline."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        # Peak at 10000, trough at 7000 → -30%
        values = [10000, 10500, 10000, 8000, 7000, 8500, 9000, 11000]
        curve = self._make_equity_curve(values)
        dd = reporter.calculate_max_drawdown(curve, "equity")
        assert dd["pct"] == pytest.approx(-33.33, abs=0.1)  # 7000/10500 - 1 = -33.33%

    def test_max_drawdown_no_drawdown(self):
        """If equity only goes up, drawdown should be 0."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        values = [10000, 10500, 11000, 11500]
        curve = self._make_equity_curve(values)
        dd = reporter.calculate_max_drawdown(curve, "equity")
        assert dd["pct"] == 0.0

    def test_sharpe_ratio_calculation(self):
        """Sharpe ratio should be calculated from daily returns."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        # Steady 0.1% daily return for 252 days: annualized Sharpe should be high
        values = [10000 * (1.001 ** i) for i in range(253)]
        curve = self._make_equity_curve(values)
        sharpe = reporter.calculate_sharpe_ratio(curve, "equity")
        assert sharpe > 3.0  # Very consistent returns → high Sharpe

    def test_sharpe_ratio_flat(self):
        """Sharpe ratio with zero volatility should return 0."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        values = [10000] * 50
        curve = self._make_equity_curve(values)
        sharpe = reporter.calculate_sharpe_ratio(curve, "equity")
        assert sharpe == 0.0

    def test_yearly_returns_calculation(self):
        """Should calculate per-year return percentages."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()
        # Craft equity curve spanning 2 years
        curve = []
        # Year 1: 10000 -> 12000 (+20%)
        for i in range(252):
            d = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            val = 10000 + (2000 / 252) * i
            curve.append({"date": d, "equity": round(val, 2)})
        # Year 2: 12000 -> 15000 (+25%)
        for i in range(252):
            d = datetime.date(2021, 1, 1) + datetime.timedelta(days=i)
            val = 12000 + (3000 / 252) * i
            curve.append({"date": d, "equity": round(val, 2)})

        yearly = reporter.calculate_yearly_returns(curve, "equity")
        assert 2020 in yearly
        assert 2021 in yearly
        assert yearly[2020] == pytest.approx(20.0, abs=1.0)
        assert yearly[2021] == pytest.approx(25.0, abs=1.0)

    def test_generate_report_structure(self):
        """Report should contain all required fields for all strategies."""
        from backend.backtest.etf_single_reporter import EtfSingleReporter
        reporter = EtfSingleReporter()

        dates = [datetime.date(2020, 1, 1) + datetime.timedelta(days=i) for i in range(30)]

        vxv_eq = [{"date": d, "vxv_equity": 10000.0 + i * 10, "regime": "BULL",
                    "vxv_position_pct": 100.0, "vxv_cash": 0.0, "vxv_invested": 10000.0 + i * 10,
                    "vxv_shares": 100} for i, d in enumerate(dates)]
        mts_eq = [{"date": d, "mts_v2_equity": 10000.0 + i * 10, "mts_v2_position_pct": 100.0, "mts_score": 50, "mts_ema5": 50} for i, d in enumerate(dates)]
        opta_eq = [{"date": d, "option_a_equity": 10000.0 + i * 10, "option_a_position_pct": 100.0} for i, d in enumerate(dates)]
        optc_eq = [{"date": d, "option_c_equity": 10000.0 + i * 10, "option_c_position_pct": 100.0} for i, d in enumerate(dates)]
        optd_eq = [{"date": d, "option_d_equity": 10000.0 + i * 10, "option_d_position_pct": 100.0} for i, d in enumerate(dates)]
        based_eq = [{"date": d, "based_sma200_equity": 10000.0 + i * 10, "based_sma200_position_pct": 100.0} for i, d in enumerate(dates)]

        bh_eq = [{"date": d, "buyhold_equity": 10000.0 + i * 10} for i, d in enumerate(dates)]
        dca_eq = [{"date": d, "dca_equity": 10000.0 + i * 5} for i, d in enumerate(dates)]
        based63_eq = [{"date": d, "based_sma63_equity": 10000.0 + i * 10, "based_sma63_position_pct": 100.0} for i, d in enumerate(dates)]

        vxv_result = {
            "final_capital": 10290.0, "equity_curve": vxv_eq,
            "regime_changes": 2, "rebalance_count": 2,
            "time_in_market_pct": 95.0, "trade_log": [], "regime_history": [],
        }
        mts_v2_result = {"final_capital": 10290.0, "equity_curve": mts_eq, "rebalance_count": 2}
        option_a_result = {"final_capital": 10290.0, "equity_curve": opta_eq, "rebalance_count": 2}
        option_c_result = {"final_capital": 10290.0, "equity_curve": optc_eq, "rebalance_count": 2}
        option_d_result = {"final_capital": 10290.0, "equity_curve": optd_eq, "rebalance_count": 2}
        based_sma200_result = {"final_capital": 10290.0, "equity_curve": based_eq, "rebalance_count": 2}
        based_sma63_result = {"final_capital": 10290.0, "equity_curve": based63_eq, "rebalance_count": 2}

        bh_result = {"final_capital": 10290.0, "equity_curve": bh_eq}
        dca_result = {"final_capital": 10145.0, "equity_curve": dca_eq}

        report = reporter.generate_report(
            ticker="SPY", start_date="2020-01-01", end_date="2020-01-30",
            initial_capital=10000.0, consider_tax=0.0,
            trading_dates=dates,
            vxv_result=vxv_result,
            mts_v2_result=mts_v2_result,
            option_a_result=option_a_result,
            option_c_result=option_c_result,
            option_d_result=option_d_result,
            based_sma200_result=based_sma200_result,
            based_sma63_result=based_sma63_result,
            bh_result=bh_result,
            dca_result=dca_result,
            benchmark_data={},
        )

        assert "summary" in report
        assert "equity_curve" in report
        summary = report["summary"]
        assert "strategies" in summary
        assert "vxv_vix_ema" in summary["strategies"]
        assert "mts_v2" in summary["strategies"]
        assert "option_a" in summary["strategies"]
        assert "option_c_strict" in summary["strategies"]
        assert "option_d" in summary["strategies"]
        assert "based_sma200" in summary["strategies"]
        assert "based_sma63" in summary["strategies"]
        assert "buy_and_hold" in summary["strategies"]
        assert "dca" in summary["strategies"]

        vxv_strat = summary["strategies"]["vxv_vix_ema"]
        assert "final_capital" in vxv_strat
        assert "cagr" in vxv_strat
        assert "max_drawdown_pct" in vxv_strat
        assert "sharpe_ratio" in vxv_strat
        assert "yearly_returns" in vxv_strat
        assert "regime_changes" in vxv_strat
        assert "time_in_market_pct" in vxv_strat
