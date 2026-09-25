"""参照データの記録（backtest_stable_data_plan.md §3-D）に関するテスト。

- preload_data() が価格データの最終日を1行ログに出すこと。
- save_results_json() が data_source_meta を結果 JSON に記録すること。
- scenario_runner.run_scenario_test() が data_source_meta を summary JSON に記録すること。
"""
import json
import os

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

import backtest.backtest_runner as br
from backtest.backtest_report import save_results_json
from backend.backtest.scenario_runner import run_scenario_test


def test_preload_data_logs_price_last_date(monkeypatch, capsys):
    """master_files 指定時、読み込んだ df_prices の最終日をログに1行出す。"""
    import backend.pipeline.parquet_cache_manager as pcm

    def _spy(*args, **kwargs):
        raise AssertionError("master_files 指定時に resolve_backtest_data_source が呼ばれた")

    monkeypatch.setattr(pcm, "resolve_backtest_data_source", _spy)

    prices_df = pd.DataFrame({
        "symbol_id": [1, 1],
        "date": ["2020-01-02", "2020-01-03"],
        "close": [10.0, 11.0],
    })
    empty_df = pd.DataFrame({"date": []})
    symbols_df = pd.DataFrame({"symbol_id": [1], "ticker": ["SPY"]})
    tc_df = pd.DataFrame()

    def fake_read_parquet(path, filters=None):
        name = os.path.basename(str(path))
        if "prices" in name:
            return prices_df.copy()
        if "indicators" in name:
            return pd.DataFrame({"date": ["2020-01-02", "2020-01-03"], "symbol_id": [1, 1]})
        if "ranks" in name:
            return pd.DataFrame({"date": ["2020-01-02", "2020-01-03"], "symbol_id": [1, 1]})
        if "symbols" in name:
            return symbols_df.copy()
        if "tc" in name:
            return tc_df.copy()
        return empty_df.copy()

    monkeypatch.setattr(br.pd, "read_parquet", fake_read_parquet)

    bogus = {
        "symbols": "x_symbols.parquet",
        "prices": "x_prices.parquet",
        "indicators": "x_indicators.parquet",
        "ranks": "x_ranks.parquet",
        "tc": "x_tc.parquet",
    }

    br.preload_data(None, "2020-01-01", "2020-01-31", master_files=bogus)

    out = capsys.readouterr().out
    assert "Price data through: 2020-01-03" in out


def test_save_results_json_records_data_source_meta(tmp_path):
    """save_results_json は data_source_meta を summary JSON に記録する。"""
    meta = {"data_source": "backup", "backup_name": "_bk_20260925_173413", "parquet_generation": "20260925_135558"}

    save_results_json({}, {}, str(tmp_path), "2020-01-01", "2020-12-31", data_source_meta=meta)

    with open(tmp_path / "backtest_summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)

    assert summary["data_source"] == meta


def test_save_results_json_data_source_meta_defaults_to_none(tmp_path):
    """data_source_meta を渡さない既存呼び出しでも壊れず、None が記録される。"""
    save_results_json({}, {}, str(tmp_path), "2020-01-01", "2020-12-31")

    with open(tmp_path / "backtest_summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)

    assert summary["data_source"] is None


@pytest.fixture
def _mock_scenario_data():
    """test_scenario_runner.py の mock_scenario_data フィクスチャと同内容（screener_registry が
    要求する列を含む最小フィクスチャ）。"""
    dates = pd.date_range('2024-01-01', periods=10, freq='B')
    prices = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1] * 10 + [2] * 10 + [3] * 10,
        'open': [100.0 + i for i in range(10)] + [15.0] * 10 + [150.0 + i * 5 for i in range(10)],
        'high': [101.0 + i for i in range(10)] + [16.0] * 10 + [152.0 + i * 5 for i in range(10)],
        'low': [99.0 + i for i in range(10)] + [14.0] * 10 + [148.0 + i * 5 for i in range(10)],
        'close': [100.0 + i for i in range(10)] + [15.0] * 10 + [150.0 + i * 5 for i in range(10)],
        'volume': [1000000] * 30,
        'market_cap': [1000000000] * 30,
        'sma_20': [90.0] * 10 + [15.0] * 10 + [140.0] * 10,
        'change_1d_pct': [0.01] * 10 + [0.0] * 10 + [0.05] * 10,
        'ftd_signal': [False] * 30,
        'dd_signal': [False] * 30,
    })
    symbols = pd.DataFrame({
        'id': [1, 2, 3],
        'ticker': ['SPY', '^VIX', 'AAPL'],
        'category': ['ETF', 'ETF', '個別'],
        'name': ['SPDR S&P 500', 'VIX', 'Apple'],
        'active': [1, 1, 1],
    })
    indicators = pd.DataFrame({
        'date': dates.tolist() * 3,
        'symbol_id': [1] * 10 + [2] * 10 + [3] * 10,
        'rs_ratio_e21': [105.0] * 30,
        'rs_momentum_e21': [100.0] * 30,
        'ema_21': [95.0] * 30,
        'sma_50': [90.0] * 30,
        'atr_14': [2.0] * 30,
        'sma50_atr_mult': [5.0] * 30,
        'vol_surge_21': [2.0] * 30,
        'adr_pct_21': [5.0] * 30,
        'vol_surge_rel_spy_21': [1.5] * 30,
    })
    return prices, symbols, indicators


@patch('backend.backtest.scenario_runner.SessionLocal')
@patch('backend.backtest.scenario_runner.preload_data')
def test_run_scenario_test_records_data_source_meta(mock_preload, mock_session, _mock_scenario_data, tmp_path):
    """run_scenario_test は data_source_meta が渡されたら scenario_summary.json に記録する。"""
    prices, symbols, indicators = _mock_scenario_data
    mock_preload.return_value = (
        symbols, prices, indicators,
        pd.DataFrame(columns=['date', 'symbol_id']),
        pd.DataFrame(columns=['theme_id', 'symbol_id']),
        prices['date'].unique().tolist(),
    )
    mock_session.return_value = MagicMock()

    output_dir = tmp_path / "scenario_output"
    output_dir.mkdir()
    meta = {"data_source": "backup", "backup_name": "_bk_x", "parquet_generation": "g1"}

    run_scenario_test(
        start_date="2024-01-01",
        end_date="2024-01-12",
        initial_capital=100000.0,
        max_positions=4,
        output_dir=str(output_dir),
        data_source_meta=meta,
    )

    with open(output_dir / "scenario_summary.json", "r", encoding="utf-8") as f:
        summary = json.load(f)

    assert summary["data_source"] == meta
