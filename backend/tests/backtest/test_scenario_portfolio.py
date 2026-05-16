import pytest
import pandas as pd
from backend.backtest.scenario_portfolio import ScenarioPortfolio, PortfolioConfig
from backend.backtest.scenario_market_score import MarketPhase

def test_portfolio_initialization():
    config = PortfolioConfig(initial_capital=100000.0, max_positions=8)
    portfolio = ScenarioPortfolio(config)
    
    assert portfolio.capital == 100000.0
    assert portfolio.config.max_positions == 8
    assert len(portfolio.active_positions) == 0

def test_portfolio_cash_ratio_by_market_phase():
    config = PortfolioConfig(initial_capital=100000.0, max_positions=8)
    portfolio = ScenarioPortfolio(config)
    
    # In BULL phase, cash ratio should be 0.0 (fully invested)
    assert portfolio._get_target_cash_ratio(MarketPhase.BULL) == 0.0
    
    # In NEUTRAL phase, default 0.3 (30% cash)
    assert portfolio._get_target_cash_ratio(MarketPhase.NEUTRAL) == 0.3
    
    # In BEAR phase, default 0.6 (60% cash)
    assert portfolio._get_target_cash_ratio(MarketPhase.BEAR) == 0.6

def test_portfolio_calculate_position_size():
    config = PortfolioConfig(initial_capital=100000.0, max_positions=8, risk_per_trade_pct=0.01, stop_loss_pct=-0.08)
    portfolio = ScenarioPortfolio(config)
    
    # Risk per trade is 1% of 100k = $1000
    # Stop loss is 8%, so size needed to lose $1000 at 8% drop is 1000 / 0.08 = 12500
    # 12500 is exactly 100000 / 8, matching max_positions division.
    
    # Price is $100. Target allocation is $12500 -> 125 shares
    shares, amount = portfolio._calculate_position_size(100.0)
    
    assert shares == 125
    assert amount == 12500.0
    
    # If price is $300 -> 12500 / 300 = 41.66 -> 41 shares
    shares, amount = portfolio._calculate_position_size(300.0)
    assert shares == 41
    assert amount == 41 * 300.0

def test_portfolio_buy_signal_processing():
    config = PortfolioConfig(initial_capital=100000.0, max_positions=8, risk_per_trade_pct=0.01, stop_loss_pct=-0.08)
    portfolio = ScenarioPortfolio(config)
    
    # Assume 12500 allocation per position
    target_date = pd.Timestamp('2024-01-01')
    candidate = {
        'symbol_id': 1,
        'ticker': 'AAPL',
        'close': 100.0,
        'score': 3,
        'rs21_rank': 90.0
    }
    
    # In BULL phase, we should buy
    bought = portfolio.process_buy_candidate(candidate, target_date, MarketPhase.BULL)
    
    assert bought is True
    assert len(portfolio.active_positions) == 1
    assert portfolio.capital == 100000.0 - 12500.0
    assert portfolio.active_positions[0]['shares'] == 125

def test_portfolio_rejects_buy_if_cash_target_exceeded():
    # High risk allowed so position size is dictated entirely by max_positions
    config = PortfolioConfig(initial_capital=100000.0, max_positions=4, risk_per_trade_pct=0.1) # 25k per position
    portfolio = ScenarioPortfolio(config)
    
    # Set to NEUTRAL phase (target 30% cash -> max 70k invested)
    # Buy 2 positions (50k invested, 50k cash left)
    portfolio.process_buy_candidate({'symbol_id': 1, 'ticker': 'A', 'close': 100.0}, pd.Timestamp('2024-01-01'), MarketPhase.NEUTRAL)
    portfolio.process_buy_candidate({'symbol_id': 2, 'ticker': 'B', 'close': 100.0}, pd.Timestamp('2024-01-01'), MarketPhase.NEUTRAL)
    
    assert len(portfolio.active_positions) == 2
    
    # Try to buy 3rd position. This would make invested 75k > 70k max allowed. Should be rejected.
    bought = portfolio.process_buy_candidate({'symbol_id': 3, 'ticker': 'C', 'close': 100.0}, pd.Timestamp('2024-01-01'), MarketPhase.NEUTRAL)
    
    assert bought is False
    assert len(portfolio.active_positions) == 2

def test_portfolio_apply_exits():
    config = PortfolioConfig(initial_capital=100000.0, max_positions=8)
    portfolio = ScenarioPortfolio(config)
    
    # Manually add a position
    portfolio.active_positions = [{
        'symbol_id': 1,
        'ticker': 'AAPL',
        'entry_date': pd.Timestamp('2024-01-01'),
        'entry_price': 100.0,
        'shares': 100,
        'amount': 10000.0
    }]
    portfolio.capital -= 10000.0 # 90000 left
    
    # Exit triggered
    exits = [{
        'symbol_id': 1,
        'exit_date': pd.Timestamp('2024-01-03'),
        'exit_price': 110.0, # 10% gain -> 11000 return
        'reason': 'profit_target',
        'pnl_pct': 0.10
    }]
    
    portfolio.apply_exits(exits)
    
    assert len(portfolio.active_positions) == 0
    assert portfolio.capital == 90000.0 + 11000.0 # 101000
    assert len(portfolio.trade_history) == 1
    assert portfolio.trade_history[0]['pnl_amount'] == 1000.0
