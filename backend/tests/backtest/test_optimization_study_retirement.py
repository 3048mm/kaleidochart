"""optimization_runner の study 退避（backtest_stable_data_plan.md §4-5）に関するテスト。

参照データの世代が既存 study と変わっていたら、古い study を別名に退避してから
正式名（戦略名）で新規作成する。分岐は4つ:
  1. 既存 study が無い -> 新規作成し user_attrs を記録
  2. 世代が一致 -> そのまま再開
  3. 世代が不一致 -> ファイルバックアップ → 退避 → 正式名で新規作成
  4. user_attrs に記録が無い既存 study -> 退避せず、今回の参照先を記録して引き継ぐ
"""
import os
import sqlite3

import optuna
import pytest
from optuna.storages import RDBStorage

import optimization_runner as opt_runner


def _make_storage(tmp_path, name="opt.db"):
    db_path = os.path.join(str(tmp_path), name)
    storage = RDBStorage(url=f"sqlite:///{db_path}")
    return storage, db_path


def test_creates_new_study_with_user_attrs_when_absent(tmp_path):
    storage, db_path = _make_storage(tmp_path)
    meta = {"data_source": "backup", "backup_name": "_bk_a", "parquet_generation": "gen1"}

    study = opt_runner.retire_study_if_data_source_changed(storage, "strat_X", meta, db_path)

    assert study.study_name == "strat_X"
    assert study.user_attrs["data_source"] == "backup"
    assert study.user_attrs["backup_name"] == "_bk_a"
    assert study.user_attrs["parquet_generation"] == "gen1"
    # trial 0 件の新規 study のはず
    assert len(study.trials) == 0


def test_resumes_when_generation_matches(tmp_path):
    storage, db_path = _make_storage(tmp_path)
    meta = {"data_source": "backup", "backup_name": "_bk_a", "parquet_generation": "gen1"}

    study1 = optuna.create_study(study_name="strat_X", storage=storage, direction="maximize")
    study1.set_user_attr("data_source", "backup")
    study1.set_user_attr("backup_name", "_bk_a")
    study1.set_user_attr("parquet_generation", "gen1")
    study1.add_trial(optuna.trial.create_trial(
        params={"p": 1}, distributions={"p": optuna.distributions.IntDistribution(1, 10)},
        value=1.0,
    ))

    study = opt_runner.retire_study_if_data_source_changed(storage, "strat_X", meta, db_path)

    assert study.study_name == "strat_X"
    assert len(study.trials) == 1, "世代一致なのに既存 trial が失われた"
    # 退避先やバックアップファイルが作られていないこと
    assert "strat_X__gen1" not in optuna.study.get_all_study_names(storage)
    assert not any(f.startswith(os.path.basename(db_path) + ".bak_") for f in os.listdir(tmp_path))


def test_retires_old_study_and_backs_up_file_when_generation_differs(tmp_path):
    storage, db_path = _make_storage(tmp_path)

    study1 = optuna.create_study(study_name="strat_X", storage=storage, direction="maximize")
    study1.set_user_attr("data_source", "backup")
    study1.set_user_attr("backup_name", "_bk_old")
    study1.set_user_attr("parquet_generation", "gen_old")
    study1.add_trial(optuna.trial.create_trial(
        params={"p": 1}, distributions={"p": optuna.distributions.IntDistribution(1, 10)},
        value=1.0,
    ))

    new_meta = {"data_source": "backup", "backup_name": "_bk_new", "parquet_generation": "gen_new"}
    study = opt_runner.retire_study_if_data_source_changed(storage, "strat_X", new_meta, db_path)

    # 正式名は新規（trial 0件）
    assert study.study_name == "strat_X"
    assert len(study.trials) == 0
    assert study.user_attrs["parquet_generation"] == "gen_new"

    # 退避先に旧 study がコピーされている
    retired = optuna.load_study(study_name="strat_X__gen_old", storage=storage)
    assert len(retired.trials) == 1
    assert retired.user_attrs["parquet_generation"] == "gen_old"

    # 退避前にファイルバックアップが作られている
    backups = [f for f in os.listdir(tmp_path) if f.startswith("opt.db.bak_")]
    assert len(backups) == 1


def test_retirement_name_collision_gets_numeric_suffix(tmp_path):
    storage, db_path = _make_storage(tmp_path)

    study1 = optuna.create_study(study_name="strat_X", storage=storage, direction="maximize")
    study1.set_user_attr("parquet_generation", "gen_old")

    # 退避先の名前が既に存在する状況を作る
    optuna.create_study(study_name="strat_X__gen_old", storage=storage, direction="maximize")

    new_meta = {"data_source": "latest", "backup_name": None, "parquet_generation": "gen_new"}
    opt_runner.retire_study_if_data_source_changed(storage, "strat_X", new_meta, db_path)

    names = set(optuna.study.get_all_study_names(storage))
    assert "strat_X__gen_old_2" in names


def test_no_retirement_when_existing_study_has_no_generation_record(tmp_path, capsys):
    storage, db_path = _make_storage(tmp_path)

    study1 = optuna.create_study(study_name="strat_X", storage=storage, direction="maximize")
    study1.add_trial(optuna.trial.create_trial(
        params={"p": 1}, distributions={"p": optuna.distributions.IntDistribution(1, 10)},
        value=1.0,
    ))

    meta = {"data_source": "backup", "backup_name": "_bk_a", "parquet_generation": "gen1"}
    study = opt_runner.retire_study_if_data_source_changed(storage, "strat_X", meta, db_path)

    assert study.study_name == "strat_X"
    assert len(study.trials) == 1, "記録の無い既存 study の trial を失った"
    assert study.user_attrs["parquet_generation"] == "gen1"
    assert "strat_X__" not in "".join(optuna.study.get_all_study_names(storage))

    out = capsys.readouterr().out
    assert "WARNING" in out
