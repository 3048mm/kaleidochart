import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from backend.backtest.scenario_runner import run_scenario_test, inject_liquidity_floor


# =============================================================
# 2026-07-26: 全戦略共通の流動性ハード制約(min_avg_dollar_volume_21)が
# 個別銘柄シナリオテストには注入されていなかった問題への対応
# =============================================================

def test_inject_liquidity_floor_adds_to_strategies_without_it():
    strategies = {
        'Rise - Check - foo': {'min_change_1d_pct': 5.0},
        'Rise - Check - bar': {'min_change_1d_pct': 3.0},
    }
    result = inject_liquidity_floor(strategies, 2e6)
    assert result['Rise - Check - foo']['min_avg_dollar_volume_21'] == 2e6
    assert result['Rise - Check - bar']['min_avg_dollar_volume_21'] == 2e6


def test_inject_liquidity_floor_respects_explicit_override():
    # 戦略側で明示指定済みなら上書きしない（optimization_runner.py と同じ扱い）
    strategies = {'Rise - Check - foo': {'min_avg_dollar_volume_21': 5e6}}
    result = inject_liquidity_floor(strategies, 2e6)
    assert result['Rise - Check - foo']['min_avg_dollar_volume_21'] == 5e6


def test_inject_liquidity_floor_noop_when_value_is_none():
    strategies = {'Rise - Check - foo': {'min_change_1d_pct': 5.0}}
    result = inject_liquidity_floor(strategies, None)
    assert 'min_avg_dollar_volume_21' not in result['Rise - Check - foo']

@pytest.fixture
def mock_scenario_data():
    dates = pd.date_range('2024-01-01', periods=10, freq='B')
    
    # Mock prices for SPY, VIX, and a stock AAPL
    prices = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1]*10 + [2]*10 + [3]*10,
        'open': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)],
        'high': [101.0 + i for i in range(10)] + [16.0]*10 + [152.0 + i*5 for i in range(10)],
        'low': [99.0 + i for i in range(10)] + [14.0]*10 + [148.0 + i*5 for i in range(10)],
        'close': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)],
        'volume': [1000000]*30,
        'market_cap': [1000000000]*30,
        'sma_20': [90.0]*10 + [15.0]*10 + [140.0]*10,
        'change_1d_pct': [0.01]*10 + [0.0]*10 + [0.05]*10,
        'ftd_signal': [False]*30,
        'dd_signal': [False]*30
    })
    
    symbols = pd.DataFrame({
        'id': [1, 2, 3],
        'ticker': ['SPY', '^VIX', 'AAPL'],
        'category': ['ETF', 'ETF', '個別'],
        'name': ['SPDR S&P 500', 'VIX', 'Apple'],
        'active': [1, 1, 1]
    })
    
    indicators = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1]*10 + [2]*10 + [3]*10,
        'rs_ratio_e21': [105.0]*30,
        'rs_momentum_e21': [100.0]*30,
        'ema_21': [95.0]*30,
        'sma_50': [90.0]*30,
        'atr_14': [2.0]*30,
        'sma50_atr_mult': [5.0]*30,
        # data/screener_presets.toml の active_rise_ids ('rrg_improving_in' / 'check_1d_gain'、
        # いずれも group='Check') が要求する列。レジストリ導出（screener_registry 経由）に
        # なったことで、この最小フィクスチャにも本番相当の列が無いと
        # UnknownFilterKeyError で落ちるようになった（本番は T3 が一括で埋めるため未発生）。
        'vol_surge_21': [2.0]*30,
        'adr_pct_21': [5.0]*30,
        'vol_surge_rel_spy_21': [1.5]*30,
    })
    
    return prices, symbols, indicators

@patch('backend.backtest.scenario_runner.SessionLocal')
@patch('backend.backtest.scenario_runner.preload_data')
def test_scenario_runner_integration(mock_preload, mock_session, mock_scenario_data, tmp_path):
    prices, symbols, indicators = mock_scenario_data
    mock_preload.return_value = (
        symbols,
        prices,
        indicators,
        pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        pd.DataFrame(columns=['theme_id', 'symbol_id']),
        prices['date'].unique().tolist()
    )
    
    mock_db_instance = MagicMock()
    mock_session.return_value = mock_db_instance
    
    output_dir = tmp_path / "scenario_output"
    output_dir.mkdir()
    
    # Run the integration wrapper
    result = run_scenario_test(
        start_date="2024-01-01",
        end_date="2024-01-12",
        initial_capital=100000.0,
        max_positions=4,
        output_dir=str(output_dir)
    )
    
    assert result is not None
    assert 'summary' in result
    assert 'trade_history' in result
    
    # Check that CSV was created
    assert (output_dir / "scenario_trade_logs.csv").exists()
    
    # With a highly bullish setup and only 1 stock, it might trigger a buy
    # The integration should run without errors, verifying the component wiring.
    assert result['summary']['initial_capital'] == 100000.0


@patch('backend.backtest.scenario_runner.SessionLocal')
@patch('backend.backtest.scenario_runner.preload_data')
def test_scenario_runner_excludes_illiquid_symbols(mock_preload, mock_session, tmp_path):
    """全戦略共通の流動性ハード制約(min_avg_dollar_volume_21, backtest_config.tomlの[general]値)が
    個別銘柄シナリオテストでも効くこと。AAPL(高流動性)とMICRO(低流動性)を同一条件で
    用意し、MICROだけがシグナルから除外されることを確認する。
    """
    # preload_data の実際の返り値は date 型（Timestamp ではない）。scenario_runner.py の
    # 日次ループは price_by_date/ind_by_date のキー引きに datetime.date を使うため、
    # ここで型を揃えないと price_day が常に None になり全シグナルが握りつぶされる。
    dates = [d.date() for d in pd.date_range('2024-01-01', periods=10, freq='B')]

    # symbol 4 = MICRO: AAPLと同じ価格・指標パターンだが avg_dollar_volume_21 が閾値未満
    prices = pd.DataFrame({
        'date': dates * 4,
        'symbol_id': [1]*10 + [2]*10 + [3]*10 + [4]*10,
        'open': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)] + [150.0 + i*5 for i in range(10)],
        'high': [101.0 + i for i in range(10)] + [16.0]*10 + [152.0 + i*5 for i in range(10)] + [152.0 + i*5 for i in range(10)],
        'low': [99.0 + i for i in range(10)] + [14.0]*10 + [148.0 + i*5 for i in range(10)] + [148.0 + i*5 for i in range(10)],
        'close': [100.0 + i for i in range(10)] + [15.0]*10 + [150.0 + i*5 for i in range(10)] + [150.0 + i*5 for i in range(10)],
        'volume': [1000000]*40,
        'market_cap': [1000000000]*40,
        'sma_20': [90.0]*10 + [15.0]*10 + [140.0]*10 + [140.0]*10,
        'change_1d_pct': [0.01]*10 + [0.0]*10 + [0.05]*10 + [0.05]*10,
        'ftd_signal': [False]*40,
        'dd_signal': [False]*40,
    })

    symbols = pd.DataFrame({
        'id': [1, 2, 3, 4],
        'ticker': ['SPY', '^VIX', 'AAPL', 'MICRO'],
        'category': ['ETF', 'ETF', '個別', '個別'],
        'name': ['SPDR S&P 500', 'VIX', 'Apple', 'Micro Cap'],
        'active': [1, 1, 1, 1]
    })

    indicators = pd.DataFrame({
        'date': dates * 4,
        'symbol_id': [1]*10 + [2]*10 + [3]*10 + [4]*10,
        'rs_ratio_e21': [105.0]*40,
        'rs_momentum_e21': [100.0]*40,
        'ema_21': [95.0]*40,
        'sma_50': [90.0]*40,
        'atr_14': [2.0]*40,
        'sma50_atr_mult': [5.0]*40,
        # avg_dollar_volume_21 は実プロダクションでは indicators 側の列
        # （T3 パイプライン、backend/indicators/volume_and_trends.py）。
        # AAPL(symbol_id=3): 閾値$2Mを大きく上回る。MICRO(symbol_id=4): 大きく下回る。
        'avg_dollar_volume_21': [1e8]*10 + [1e8]*10 + [1e8]*10 + [5e5]*10,
    })

    mock_preload.return_value = (
        symbols,
        prices,
        indicators,
        pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        pd.DataFrame(columns=['theme_id', 'symbol_id']),
        dates
    )

    mock_db_instance = MagicMock()
    mock_session.return_value = mock_db_instance

    # AAPL・MICRO のどちらも通過する緩い戦略(市場規模フィルタのみ)を用意
    config_path = tmp_path / "screener_presets_liquidity_test.toml"
    config_path.write_text(
        'active_rise_ids = []\n'
        'active_fall_ids = []\n\n'
        '[[rise]]\n'
        'id = "liquidity_test"\n'
        'name = "liquidity_test"\n'
        'group = "Check"\n\n'
        '[rise.filters]\n'
        'min_market_cap = 1.0\n',
        encoding='utf-8'
    )

    output_dir = tmp_path / "scenario_output"
    output_dir.mkdir()

    result = run_scenario_test(
        start_date="2024-01-01",
        end_date="2024-01-12",
        initial_capital=100000.0,
        max_positions=4,
        min_score=1,
        output_dir=str(output_dir),
        config_path=str(config_path),
        regime_model="full_position",
    )

    # 10日間の期間では保有継続中で決済に至らないため、trade_history ではなく
    # equity_curve の held_tickers（保有中を含む）で買われたかどうかを確認する
    held_tickers = set()
    for row in result['equity_curve']:
        held_tickers.update(t for t in row.get('held_tickers', '').split(',') if t)
    assert 'AAPL' in held_tickers
    assert 'MICRO' not in held_tickers
