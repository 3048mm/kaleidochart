import os
import pytest
import tomli
from unittest.mock import MagicMock, patch
import pandas as pd

from backend.backtest.run_scenario_batch import (
    load_scenario_batch_jobs,
    get_best_params_from_db,
    generate_preset_toml
)


@pytest.fixture
def mock_jobs_toml(tmp_path):
    toml_content = """
[[job]]
name = "B4_test_opt"
strategy_code = "B4_rs_trend_with_theme"
source = "optuna"
study_name = "opt_strategy_B4_test_multi_period"

[[job]]
name = "B4_test_manual"
strategy_code = "B4_rs_trend_with_theme"
source = "manual"
base_study_name = "opt_strategy_B4_test_multi_period"
[job.override_params]
min_vol_surge_21 = 2.5
max_sma50_atr_mult = 3.0
"""
    file_path = tmp_path / "scenario_batch_jobs.toml"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(toml_content)
    return file_path


def test_load_scenario_batch_jobs(mock_jobs_toml):
    jobs = load_scenario_batch_jobs(str(mock_jobs_toml))
    assert len(jobs) == 2
    
    assert jobs[0]["name"] == "B4_test_opt"
    assert jobs[0]["strategy_code"] == "B4_rs_trend_with_theme"
    assert jobs[0]["source"] == "optuna"
    assert jobs[0]["study_name"] == "opt_strategy_B4_test_multi_period"
    
    assert jobs[1]["name"] == "B4_test_manual"
    assert jobs[1]["source"] == "manual"
    assert jobs[1]["override_params"]["min_vol_surge_21"] == 2.5


@patch("optuna.load_study")
def test_get_best_params_from_db(mock_load_study):
    # Setup mock study and best_trial
    mock_study = MagicMock()
    mock_study.best_trial.params = {"param1": 1.23, "param2": True}
    mock_load_study.return_value = mock_study
    
    params = get_best_params_from_db("dummy_db_path", "dummy_study")
    assert params == {"param1": 1.23, "param2": True}
    mock_load_study.assert_called_once_with(study_name="dummy_study", storage="sqlite:///dummy_db_path")


def test_generate_preset_toml(tmp_path):
    output_file = tmp_path / "preset_B4_test.toml"
    best_params = {
        "min_change_1d_pct": 7.0,
        "is_close_gt_ema63": True,
        "sort_column": "rs_ratio_rank_e21"
    }
    
    generate_preset_toml("B4_rs_trend_with_theme", best_params, str(output_file))
    
    assert output_file.exists()
    with open(output_file, "rb") as f:
        config = tomli.load(f)
        
    assert config["active_rise_ids"] == ["B4_rs_trend_with_theme_opt"]
    assert len(config["rise"]) == 1
    
    rise_entry = config["rise"][0]
    assert rise_entry["id"] == "B4_rs_trend_with_theme_opt"
    assert rise_entry["name"] == "B4_rs_trend_with_theme_opt"
    assert rise_entry["use_vxv_vix_hysteresis"] is True
    
    filters = rise_entry["filters"]
    assert filters["min_change_1d_pct"] == 7.0
    assert filters["is_close_gt_ema63"] is True
    assert filters["sort_column"] == "rs_ratio_rank_e21"
