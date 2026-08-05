import os
import pytest
import tomli
from unittest.mock import MagicMock, patch
import pandas as pd

from backend.backtest.run_scenario_batch import (
    load_scenario_batch_jobs,
    get_best_params_from_db,
    generate_preset_toml,
    filter_jobs_by_names,
    format_elapsed,
    parse_args,
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


# ---------------------------------------------------------------------------
# filter_jobs_by_names — `--jobs` によるジョブ絞り込み（2026-08-06 追加）
# ---------------------------------------------------------------------------
_JOBS = [
    {"name": "A", "strategy_code": "A_momentum_breakout"},
    {"name": "B1", "strategy_code": "B1_theme_leader"},
    {"name": "B2", "strategy_code": "B2_theme_rsrank_momentum"},
]


def test_filter_jobs_by_names_none_returns_all():
    assert filter_jobs_by_names(_JOBS, None) == _JOBS


def test_filter_jobs_by_names_empty_returns_all():
    assert filter_jobs_by_names(_JOBS, []) == _JOBS


def test_filter_jobs_by_names_single():
    result = filter_jobs_by_names(_JOBS, ["B1"])
    assert [j["name"] for j in result] == ["B1"]


def test_filter_jobs_by_names_multiple_preserves_original_order():
    result = filter_jobs_by_names(_JOBS, ["B2", "A"])
    # jobs 側の元の順序（A, B1, B2）を維持する。--jobs の指定順ではない。
    assert [j["name"] for j in result] == ["A", "B2"]


def test_filter_jobs_by_names_unknown_name_raises():
    """タイポで「絞り込んだつもりが全件スキップ」になる事故を防ぐ（D-2/I-6 と同型）。"""
    with pytest.raises(ValueError, match="B9"):
        filter_jobs_by_names(_JOBS, ["B9"])


# ---------------------------------------------------------------------------
# format_elapsed / parse_args（2026-08-06 追加: 進捗ログ表示の改善）
# ---------------------------------------------------------------------------
def test_format_elapsed_under_a_minute():
    assert format_elapsed(5) == "0:05"


def test_format_elapsed_minutes():
    assert format_elapsed(125) == "2:05"


def test_format_elapsed_hours():
    assert format_elapsed(3725) == "1:02:05"


def test_parse_args_no_jobs_flag_means_all():
    job_names, list_only = parse_args([])
    assert job_names is None
    assert list_only is False


def test_parse_args_single_job():
    job_names, _ = parse_args(["--jobs", "B1"])
    assert job_names == ["B1"]


def test_parse_args_multiple_jobs_comma_separated():
    job_names, _ = parse_args(["--jobs", "B1,B2, E1"])
    assert job_names == ["B1", "B2", "E1"]


def test_parse_args_list_jobs_flag():
    _, list_only = parse_args(["--list-jobs"])
    assert list_only is True
