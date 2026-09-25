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
    resolve_job_strategy_specs,
    resolve_tax_rate,
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

    generate_preset_toml([("B4_rs_trend_with_theme", best_params)], str(output_file))

    assert output_file.exists()
    with open(output_file, "rb") as f:
        config = tomli.load(f)

    assert config["active_rise_ids"] == ["B4_rs_trend_with_theme_opt"]
    assert len(config["rise"]) == 1

    rise_entry = config["rise"][0]
    assert rise_entry["id"] == "B4_rs_trend_with_theme_opt"
    assert rise_entry["name"] == "B4_rs_trend_with_theme_opt"
    assert rise_entry["group"] == "Pickup"
    assert rise_entry["use_vxv_vix_hysteresis"] is True

    filters = rise_entry["filters"]
    assert filters["min_change_1d_pct"] == 7.0
    assert filters["is_close_gt_ema63"] is True
    assert filters["sort_column"] == "rs_ratio_rank_e21"


def test_generate_preset_toml_single_strategy_byte_identical_to_legacy(tmp_path):
    """単一戦略の出力が、複数戦略対応前の実装とバイト単位で一致すること（後方互換の要）。

    generate_preset_toml() が [(name, params), ...] のリストを受ける形に変わったが、
    要素数1で呼び出した場合の出力は変更前の実装が生成していた TOML と完全に一致しなければ
    ならない（既存13ジョブの結果が変わってはいけないため）。
    """
    strategy_name = "B4_rs_trend_with_theme"
    best_params = {
        "min_change_1d_pct": 7.0,
        "is_close_gt_ema63": True,
        "sort_column": "rs_ratio_rank_e21",
    }

    # 変更前の実装をそのまま再現した期待値（generate_preset_toml の旧ロジック）
    expected = f'active_rise_ids = ["{strategy_name}_opt"]\nactive_fall_ids = []\n\n'
    expected += '[[rise]]\n'
    expected += f'id = "{strategy_name}_opt"\n'
    expected += f'name = "{strategy_name}_opt"\n'
    expected += f'subname = "Optuna Best for {strategy_name}"\n'
    expected += 'group = "Pickup"\n'
    expected += 'use_vxv_vix_hysteresis = true\n'
    expected += 'vxv_vix_hysteresis_type = "vxv_vix_ema"\n'
    expected += '\n[rise.filters]\n'
    for k, v in best_params.items():
        if isinstance(v, bool):
            toml_val = "true" if v else "false"
        elif isinstance(v, str):
            toml_val = f'"{v}"'
        else:
            toml_val = v
        expected += f"{k} = {toml_val}\n"

    output_file = tmp_path / "preset_legacy_compare.toml"
    generate_preset_toml([(strategy_name, best_params)], str(output_file))

    actual = output_file.read_text(encoding="utf-8")
    assert actual == expected


def test_generate_preset_toml_multi_strategy(tmp_path):
    """複数戦略を渡すと、その数だけ [[rise]] ブロックが生成され、全て group = "Pickup" であること。"""
    output_file = tmp_path / "preset_multi.toml"
    strategies = [
        ("B2_theme_rsrank_momentum", {"min_change_1d_pct": 5.0}),
        ("B5_rs_trend_with_theme", {"min_change_1d_pct": 6.0}),
        ("B6_rs_macd_and_theme", {"min_change_1d_pct": 7.0}),
    ]

    generate_preset_toml(strategies, str(output_file))

    with open(output_file, "rb") as f:
        config = tomli.load(f)

    assert config["active_rise_ids"] == [
        "B2_theme_rsrank_momentum_opt",
        "B5_rs_trend_with_theme_opt",
        "B6_rs_macd_and_theme_opt",
    ]
    assert len(config["rise"]) == 3
    for entry in config["rise"]:
        assert entry["group"] == "Pickup"

    ids = [entry["id"] for entry in config["rise"]]
    assert ids == [
        "B2_theme_rsrank_momentum_opt",
        "B5_rs_trend_with_theme_opt",
        "B6_rs_macd_and_theme_opt",
    ]


# ---------------------------------------------------------------------------
# resolve_job_strategy_specs — ジョブ定義の複数戦略対応（2026-08-21 追加）
# ---------------------------------------------------------------------------
def test_resolve_job_strategy_specs_single():
    job = {"name": "B2", "strategy_code": "B2_theme_rsrank_momentum", "study_name": "B2_theme_rsrank_momentum"}
    codes, studies, is_multi = resolve_job_strategy_specs(job)
    assert codes == ["B2_theme_rsrank_momentum"]
    assert studies == ["B2_theme_rsrank_momentum"]
    assert is_multi is False


def test_resolve_job_strategy_specs_multi():
    job = {
        "name": "B256_union",
        "strategy_codes": ["B2_x", "B5_x", "B6_x"],
        "study_names": ["B2_x", "B5_x", "B6_x"],
    }
    codes, studies, is_multi = resolve_job_strategy_specs(job)
    assert codes == ["B2_x", "B5_x", "B6_x"]
    assert studies == ["B2_x", "B5_x", "B6_x"]
    assert is_multi is True


def test_resolve_job_strategy_specs_length_mismatch_raises():
    job = {
        "name": "B256_union",
        "strategy_codes": ["B2_x", "B5_x", "B6_x"],
        "study_names": ["B2_x", "B5_x"],
    }
    with pytest.raises(ValueError, match="長さが一致しません"):
        resolve_job_strategy_specs(job)


def test_resolve_job_strategy_specs_both_singular_and_plural_raises():
    job = {
        "name": "bad",
        "strategy_code": "B2_x",
        "strategy_codes": ["B2_x", "B5_x"],
    }
    with pytest.raises(ValueError, match="同時に指定できません"):
        resolve_job_strategy_specs(job)


def test_resolve_job_strategy_specs_neither_raises():
    job = {"name": "bad"}
    with pytest.raises(ValueError):
        resolve_job_strategy_specs(job)


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
    job_names, list_only, tax, data_source = parse_args([])
    assert job_names is None
    assert list_only is False
    assert tax is None, "--tax 未指定は None（設定ファイルの値を使う合図）"
    assert data_source == "backup", "--data-source 未指定の既定は 'backup'"


def test_parse_args_single_job():
    job_names, _, _, _ = parse_args(["--jobs", "B1"])
    assert job_names == ["B1"]


def test_parse_args_multiple_jobs_comma_separated():
    job_names, _, _, _ = parse_args(["--jobs", "B1,B2, E1"])
    assert job_names == ["B1", "B2", "E1"]


def test_parse_args_list_jobs_flag():
    _, list_only, _, _ = parse_args(["--list-jobs"])
    assert list_only is True


def test_parse_args_data_source_is_parsed():
    _, _, _, data_source = parse_args(["--data-source", "production"])
    assert data_source == "production"


# ---------------------------------------------------------------------------
# --tax（2026-09-07 追加）
#
# 型3の並列 MC で税が効くことを実測するために足したオプション。
# 本番の backtest_config.toml を書き換えずに 0.2 と 0.0 を比較できるようにする。
#
# **元のバグは「実行できたが効いていなかった」**（consider_tax=20.0 を指定しても
# 結果が1円も変わらなかった）。単位の取り違えを二度と通さないよう、検証は
# common_constraints.load_tax_rate() に集約して CLI 値にも同じ検査をかける。
# ---------------------------------------------------------------------------
def test_parse_args_tax_is_none_by_default():
    _, _, tax, _ = parse_args([])
    assert tax is None


def test_parse_args_tax_is_parsed_as_float():
    _, _, tax, _ = parse_args(["--tax", "0.2"])
    assert tax == pytest.approx(0.2)


def test_resolve_tax_rate_uses_override_when_given():
    assert resolve_tax_rate(0.2) == pytest.approx(0.2)


def test_resolve_tax_rate_treats_zero_as_tax_free_not_as_unset():
    """`--tax 0.0` は「税なし」。設定ファイルへフォールバックしてはいけない。

    0.2 と 0.0 の比較実測がこの挙動に依存している。`if tax_override:` のような
    真偽判定にすると 0.0 が「未指定」に化けて、**税ありの設定値が使われ**、
    比較が成立しないまま「変わらなかった」という誤った結論になる。
    """
    assert resolve_tax_rate(0.0) == 0.0


def test_resolve_tax_rate_rejects_percent_notation():
    """20.0（＝2000%）は単位の取り違え。元のバグと同じ形なので必ず落とす。"""
    from backend.backtest.common_constraints import InvalidTaxRateError

    with pytest.raises(InvalidTaxRateError):
        resolve_tax_rate(20.0)


def test_resolve_tax_rate_rejects_negative():
    from backend.backtest.common_constraints import InvalidTaxRateError

    with pytest.raises(InvalidTaxRateError):
        resolve_tax_rate(-0.1)


def test_resolve_tax_rate_falls_back_to_config_when_none():
    """未指定なら backtest_config.toml の [general] consider_tax を読む。"""
    with patch("backend.backtest.run_scenario_batch.load_tax_rate",
               return_value=0.2) as m:
        assert resolve_tax_rate(None) == pytest.approx(0.2)
        m.assert_called_once_with()


# ---------------------------------------------------------------------------
# validate_jobs_studies (事前一括検証)
# ---------------------------------------------------------------------------
from backend.backtest.run_scenario_batch import validate_jobs_studies


def test_validate_jobs_studies_empty_jobs():
    """空ジョブリストなら何もしない。"""
    validate_jobs_studies([], "dummy.db")


def test_validate_jobs_studies_db_not_found(tmp_path):
    """Optuna DB が存在しない場合、即座にエラー。"""
    non_existent = str(tmp_path / "no_such.db")
    jobs = [{"name": "j1", "strategy_code": "A", "source": "optuna", "study_name": "s1"}]
    with pytest.raises(ValueError, match="Optuna DB ファイルが存在しません"):
        validate_jobs_studies(jobs, non_existent)


def test_validate_jobs_studies_all_valid(tmp_path):
    """全ジョブの study が存在する場合、正常終了。"""
    db_file = tmp_path / "opt.db"
    db_file.touch()

    jobs = [
        {"name": "j1", "strategy_code": "A", "source": "optuna", "study_name": "study_a"},
        {"name": "j2", "strategy_codes": ["B1", "B2"], "source": "optuna", "study_names": ["study_b1", "study_b2"]},
        {"name": "j3", "strategy_code": "C", "source": "manual", "base_study_name": "study_c"},
        {"name": "j4", "strategy_code": "D", "source": "manual"},  # base_study なし
    ]

    def mock_load_study(study_name, storage):
        mock_st = MagicMock()
        mock_st.best_trial.params = {"p": 1}
        return mock_st

    with patch("optuna.load_study", side_effect=mock_load_study):
        validate_jobs_studies(jobs, str(db_file))


def test_validate_jobs_studies_missing_study_single(tmp_path):
    """単一戦略ジョブの study が存在しない場合、集約エラーで報告。"""
    db_file = tmp_path / "opt.db"
    db_file.touch()

    jobs = [{"name": "job_a", "strategy_code": "A", "source": "optuna", "study_name": "missing_study"}]

    def mock_load_study(study_name, storage):
        raise KeyError(f"No study with name '{study_name}' exists.")

    with patch("optuna.load_study", side_effect=mock_load_study):
        with pytest.raises(ValueError) as exc_info:
            validate_jobs_studies(jobs, str(db_file))
        err = str(exc_info.value)
        assert "job_a" in err
        assert "missing_study" in err
        assert "DB に存在しません" in err


def test_validate_jobs_studies_aggregates_multiple_errors(tmp_path):
    """複数のジョブでエラーがある場合、すべてのエラーを1回でまとめて報告する。"""
    db_file = tmp_path / "opt.db"
    db_file.touch()

    jobs = [
        {"name": "job_1", "strategy_code": "A", "source": "optuna", "study_name": "missing_1"},
        {"name": "job_2", "strategy_code": "B", "source": "optuna"},  # study_name 欠落
        {"name": "job_3", "strategy_code": "C", "source": "unknown_src"},  # 不正な source
        {"name": "job_4", "strategy_code": "D", "source": "manual", "base_study_name": "missing_base"},
    ]

    def mock_load_study(study_name, storage):
        raise KeyError(f"No study with name '{study_name}' exists.")

    with patch("optuna.load_study", side_effect=mock_load_study):
        with pytest.raises(ValueError) as exc_info:
            validate_jobs_studies(jobs, str(db_file))
        err = str(exc_info.value)
        assert "4 件の問題が見つかりました" in err
        assert "job_1" in err and "missing_1" in err
        assert "job_2" in err and "study_name" in err
        assert "job_3" in err and "未知の source" in err
        assert "job_4" in err and "missing_base" in err


def test_validate_jobs_studies_no_completed_trials(tmp_path):
    """study はあるが完了した trial がない場合のエラー報告。"""
    db_file = tmp_path / "opt.db"
    db_file.touch()

    jobs = [{"name": "job_empty", "strategy_code": "A", "source": "optuna", "study_name": "empty_study"}]

    def mock_load_study(study_name, storage):
        mock_st = MagicMock()
        type(mock_st).best_trial = property(fget=MagicMock(side_effect=ValueError("Record does not exist.")))
        return mock_st

    with patch("optuna.load_study", side_effect=mock_load_study):
        with pytest.raises(ValueError) as exc_info:
            validate_jobs_studies(jobs, str(db_file))
        err = str(exc_info.value)
        assert "job_empty" in err
        assert "empty_study" in err
        assert "完了したトライアルがありません" in err


def test_main_aborts_immediately_on_invalid_studies(capsys, monkeypatch, tmp_path):
    """main() 実行時、ジョブに無効な study があれば MC 実行に入らず即座に sys.exit(1) すること。"""
    from backend.backtest.run_scenario_batch import main

    jobs_toml = tmp_path / "jobs.toml"
    jobs_toml.write_text("""
[[job]]
name = "bad_job"
strategy_code = "A"
source = "optuna"
study_name = "non_existent_study"
""", encoding="utf-8")

    db_file = tmp_path / "opt.db"
    db_file.touch()

    monkeypatch.setattr(
        "backend.backtest.run_scenario_batch.load_scenario_batch_jobs",
        lambda p: [{"name": "bad_job", "strategy_code": "A", "source": "optuna", "study_name": "non_existent_study"}],
    )
    monkeypatch.setattr(
        "backend.backtest.run_scenario_batch.parse_args",
        lambda argv=None: (None, False, None, "backup"),
    )

    def mock_load_study(study_name, storage):
        raise KeyError("not found")

    with patch("optuna.load_study", side_effect=mock_load_study):
        with pytest.raises(SystemExit) as exc_info:
            main(db_path_override=str(db_file))
        assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "[ERROR] シナリオバッチ開始前検証エラー" in captured.out
    assert "bad_job" in captured.out
    assert "non_existent_study" in captured.out



