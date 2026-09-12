"""Parquet の T5（market_signals）全期間再計算（`recompute_parquet_signals.py`）のテスト。

I/O を含まない計算部分（`build_market_signals_frame` / `compare_by_period`）に加え、
`run()` のパス解決・書き込みガードのテストを含む（§3.6・5-6b）。

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.2・§3.6
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import paths
from indicators.market_signals import compute_breadth_momentum
from scripts import recompute_parquet_signals as rps
from scripts.recompute_parquet_signals import (
    OUTPUT_COLUMNS,
    build_market_signals_frame,
    compare_by_period,
)


def _spy_df(n=5, start="2020-01-01"):
    dates = pd.date_range(start, periods=n)
    return pd.DataFrame({
        "date": dates,
        "close": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "volume": [1_000_000 + i * 1000 for i in range(n)],
    })


def _raw_metrics_input(dates):
    # 2銘柄。銘柄1は常に sma_50 を上回り、銘柄2は常に下回る。
    n = len(dates)
    return pd.DataFrame({
        "symbol_id": [1] * n + [2] * n,
        "date": list(dates) * 2,
        "close": [20.0] * n + [5.0] * n,
        "sma_50": [10.0] * n + [6.0] * n,
    })


class TestBuildMarketSignalsFrame:
    def test_output_has_expected_columns_and_dtypes(self):
        spy_df = _spy_df()
        metrics_df = compute_breadth_momentum(_raw_metrics_input(spy_df["date"]))
        created_at = datetime(2026, 9, 11, 12, 0, 0, 123456)

        out = build_market_signals_frame(spy_df, None, None, metrics_df, created_at)

        assert list(out.columns) == OUTPUT_COLUMNS
        assert out["id"].tolist() == list(range(1, len(spy_df) + 1))
        assert out["date"].tolist() == spy_df["date"].dt.strftime("%Y-%m-%d").tolist()
        assert (out["created_at"] == "2026-09-11 12:00:00.123456").all()
        # spy_above_sma200 / distribution_days は SPY の遡りが足りない先頭区間で
        # None になりうる列（indicators/market_signals.py 参照）。int64 は NaN を
        # 表現できないため、spy_sma200_rising と同じ float64（NaN 許容）にする
        # （t5_parquet_rebuild_plan.md §4-4・5-9）。
        assert out["spy_above_sma200"].dtype == np.float64
        assert out["distribution_days"].dtype == np.float64
        assert out["is_distribution_day"].dtype == np.int64
        assert out["follow_through_day"].dtype == np.int64
        assert out["market_trend_score"].dtype == np.float64
        assert out["vxv_vix_ratio"].dtype == np.float64
        assert out["breadth_sma50"].dtype == np.float64

    def test_breadth_sma50_is_merged_from_metrics_by_date(self):
        spy_df = _spy_df()
        metrics_df = compute_breadth_momentum(_raw_metrics_input(spy_df["date"]))

        out = build_market_signals_frame(spy_df, None, None, metrics_df, datetime.utcnow())

        expected = metrics_df.sort_values("date")["breadth_sma50"].tolist()
        assert out["breadth_sma50"].tolist() == pytest.approx(expected)

    def test_breadth_sma50_is_nan_for_dates_missing_from_metrics(self):
        spy_df = _spy_df(n=5)
        # metrics は先頭3日分しか無い（残り2日は個別銘柄データが無いケース相当）
        metrics_df = compute_breadth_momentum(_raw_metrics_input(spy_df["date"][:3]))

        out = build_market_signals_frame(spy_df, None, None, metrics_df, datetime.utcnow())

        assert out["breadth_sma50"].iloc[:3].notna().all()
        assert out["breadth_sma50"].iloc[3:].isna().all()

    def test_empty_metrics_df_gives_all_nan_breadth(self):
        spy_df = _spy_df()
        metrics_df = pd.DataFrame(columns=["date", "breadth_sma50", "momentum_ratio"])

        out = build_market_signals_frame(spy_df, None, None, metrics_df, datetime.utcnow())

        assert out["breadth_sma50"].isna().all()

    def test_insufficient_lookback_produces_nan_without_raising(self):
        """SPY の本数が sma_200 の窓（200本）に満たない場合、例外を出さず
        spy_above_sma200 / distribution_days が NaN になること
        （t5_parquet_rebuild_plan.md §4-4・5-9）。"""
        spy_df = _spy_df(n=10)
        metrics_df = pd.DataFrame(columns=["date", "breadth_sma50", "momentum_ratio"])

        out = build_market_signals_frame(spy_df, None, None, metrics_df, datetime.utcnow())

        assert out["spy_above_sma200"].isna().all()
        assert out["distribution_days"].isna().all()

    def test_empty_spy_df_returns_empty_frame_with_expected_columns(self):
        out = build_market_signals_frame(
            pd.DataFrame(), None, None,
            pd.DataFrame(columns=["date", "breadth_sma50", "momentum_ratio"]),
            datetime.utcnow(),
        )
        assert list(out.columns) == OUTPUT_COLUMNS
        assert out.empty


class TestCompareByPeriod:
    def _frame(self, dates, **overrides):
        n = len(dates)
        base = {
            "date": dates,
            "spy_above_sma200": [1] * n,
            "spy_sma200_rising": [1.0] * n,
            "distribution_days": [0] * n,
            "is_distribution_day": [0] * n,
            "follow_through_day": [0] * n,
            "market_phase": ["BULL"] * n,
            "market_trend_score": [70.0] * n,
            "vxv_vix_ratio": [1.05] * n,
            "breadth_sma50": [0.55] * n,
        }
        base.update(overrides)
        return pd.DataFrame(base)

    def test_identical_frames_have_zero_mismatches(self):
        dates = pd.date_range("2019-01-01", periods=3).strftime("%Y-%m-%d").tolist()
        old = self._frame(dates)
        new = self._frame(dates)

        diff = compare_by_period(old, new)

        p2 = diff["P2 2018-04-01〜2024-09-02"]
        assert p2["days"] == 3
        for col, stats in p2["columns"].items():
            assert stats["mismatch_days"] == 0

    def test_detects_numeric_mismatch_above_tolerance(self):
        dates = pd.date_range("2019-01-01", periods=3).strftime("%Y-%m-%d").tolist()
        old = self._frame(dates, market_trend_score=[70.0, 70.0, 70.0])
        new = self._frame(dates, market_trend_score=[70.0, 85.5, 70.0])

        diff = compare_by_period(old, new)

        p2 = diff["P2 2018-04-01〜2024-09-02"]["columns"]["market_trend_score"]
        assert p2["mismatch_days"] == 1
        assert p2["max_abs_diff"] == pytest.approx(15.5)

    def test_tiny_float_noise_within_tolerance_is_not_a_mismatch(self):
        dates = pd.date_range("2019-01-01", periods=1).strftime("%Y-%m-%d").tolist()
        old = self._frame(dates, breadth_sma50=[0.55])
        new = self._frame(dates, breadth_sma50=[0.55 + 1e-9])

        diff = compare_by_period(old, new)
        assert diff["P2 2018-04-01〜2024-09-02"]["columns"]["breadth_sma50"]["mismatch_days"] == 0

    def test_detects_market_phase_string_mismatch(self):
        dates = pd.date_range("2019-01-01", periods=2).strftime("%Y-%m-%d").tolist()
        old = self._frame(dates, market_phase=["BULL", "BULL"])
        new = self._frame(dates, market_phase=["BULL", "CORRECTION"])

        diff = compare_by_period(old, new)
        stats = diff["P2 2018-04-01〜2024-09-02"]["columns"]["market_phase"]
        assert stats["mismatch_days"] == 1
        assert stats["max_abs_diff"] is None

    def test_int_vs_float_dtype_does_not_cause_false_mismatch(self):
        """整数列で dtype が世代間で int/float に揺れても、値が同じなら不一致にしない。"""
        dates = pd.date_range("2019-01-01", periods=2).strftime("%Y-%m-%d").tolist()
        old = self._frame(dates)
        old["distribution_days"] = old["distribution_days"].astype(object)
        new = self._frame(dates)
        new["distribution_days"] = new["distribution_days"].astype(np.float64)

        diff = compare_by_period(old, new)
        assert diff["P2 2018-04-01〜2024-09-02"]["columns"]["distribution_days"]["mismatch_days"] == 0

    def test_splits_by_all_four_periods(self):
        dates = ["2015-01-05", "2020-01-05", "2024-12-05", "2026-01-05"]
        old = self._frame(dates)
        new = self._frame(dates)

        diff = compare_by_period(old, new)

        assert diff["P1 2010-04-01〜2018-03-31"]["days"] == 1
        assert diff["P2 2018-04-01〜2024-09-02"]["days"] == 1
        assert diff["P3 2024-09-03〜2025-06-30"]["days"] == 1
        assert diff["P4 2025-07-01〜"]["days"] == 1


# --- run() のパス解決・書き込みガード（§3.6・5-6b） --------------------------

_STOCKTOOL_ENV_VARS = (
    "STOCKTOOL_DATA_ROOT",
    "STOCKTOOL_PROD_DATA_ROOT",
    "STOCKTOOL_ENV",
    "STOCKTOOL_ALLOW_DB_CREATE",
    "STOCKTOOL_DB_PATH",
    "STOCKTOOL_USER_DB_PATH",
    "STOCKTOOL_UNIVERSE_DB_PATH",
)


def _make_worktree_repo(tmp_path, config_local_toml: str | None = None):
    """ワークツリーを模したディレクトリを作る（`.git` がファイル）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: D:/dummy/.git/worktrees/x\n", encoding="utf-8")
    if config_local_toml is not None:
        (repo / "config.local.toml").write_text(config_local_toml, encoding="utf-8")
    return repo


def _make_minimal_parquet_master(parquet_dir):
    """guard 到達までに読まれる最小限の Parquet 世代を作る。

    書き込みガード（`paths.ensure_writable`）は dry-run 早期リターンの直後・
    書き出しループの直前に置かれているため、`indicators`/`ranks`/`tc`/`fx` は
    ガードより後にしか読まれない（コピーのみ）。プレースホルダで足りる。
    """
    os.makedirs(parquet_dir, exist_ok=True)
    sym = pd.DataFrame({"id": [1], "ticker": ["SPY"], "category": ["指数"], "active": [1]})
    dates = pd.date_range("2020-01-01", periods=5)
    prices = pd.DataFrame({
        "symbol_id": [1] * 5,
        "date": dates.strftime("%Y-%m-%d"),
        "close": [100.0, 101.0, 102.0, 101.0, 103.0],
        "high": [101.0] * 5,
        "low": [99.0] * 5,
        "volume": [1_000_000] * 5,
    })
    signals = pd.DataFrame(columns=OUTPUT_COLUMNS)

    ts = "20200101_000000"
    files = {}
    for key, df in (("symbols", sym), ("prices", prices), ("signals", signals)):
        path = os.path.join(parquet_dir, f"{key}_{ts}.parquet")
        df.to_parquet(path, index=False)
        files[key] = path
    for key, base in (("indicators", "indicators"), ("ranks", "ranks"),
                       ("tc", "theme_constituents"), ("fx", "fx_rates")):
        path = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        with open(path, "wb") as f:
            f.write(b"placeholder")
        files[key] = path

    pointer = os.path.join(parquet_dir, "latest_master.json")
    with open(pointer, "w", encoding="utf-8") as f:
        json.dump(files, f)
    return files


class TestRunPathIsolation:
    """`run()` のパス解決が worktree / 環境変数を尊重すること（§3.6・5-6b）。

    実行環境の実際の worktree/本体判定に依存しないよう、`paths.get_repo_root`
    を偽の worktree に差し替えて検証する（main へ merge 後の実行でも決定的）。
    """

    @pytest.fixture(autouse=True)
    def _clean_stocktool_env(self, monkeypatch):
        for name in _STOCKTOOL_ENV_VARS:
            monkeypatch.delenv(name, raising=False)

    def test_apply_from_worktree_pointing_at_prod_raises_production_write_error(
            self, tmp_path, monkeypatch):
        """本番データを指すワークツリーから書き込み経路に入ると拒否される。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=(
                f'[data]\nroot = "{prod.as_posix()}"\nprod_root = "{prod.as_posix()}"\n'
            ),
        )
        parquet_dir = prod / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        with pytest.raises(paths.ProductionWriteError):
            rps.run(dry_run=False)

        # 拒否された時点で新世代は書かれていないこと
        assert len(list(parquet_dir.glob("market_signals_*.parquet"))) == 0

    def test_apply_with_stocktool_db_path_env_uses_work_dir_not_prod(
            self, tmp_path, monkeypatch):
        """`STOCKTOOL_DB_PATH` を設定すると、本番ではなく作業領域が使われる。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        repo = _make_worktree_repo(tmp_path)
        parquet_dir = work / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))
        monkeypatch.setenv("STOCKTOOL_DB_PATH", str(work / "stocktool.db"))
        monkeypatch.setenv("STOCKTOOL_PROD_DATA_ROOT", str(prod))

        rps.run(dry_run=False)  # 例外を投げずに完走する

        assert len(list(parquet_dir.glob("market_signals_*.parquet"))) == 1
        assert list(prod.iterdir()) == []

    def test_dry_run_does_not_write_even_when_pointed_at_prod(
            self, tmp_path, monkeypatch):
        """`--dry-run` は書き込みガードの有無に関係なく書き込まない。"""
        prod = tmp_path / "prod"
        prod.mkdir()
        repo = _make_worktree_repo(
            tmp_path,
            config_local_toml=(
                f'[data]\nroot = "{prod.as_posix()}"\nprod_root = "{prod.as_posix()}"\n'
            ),
        )
        parquet_dir = prod / "parquet_master"
        _make_minimal_parquet_master(str(parquet_dir))
        before = set(os.listdir(parquet_dir))
        monkeypatch.setattr(paths, "get_repo_root", lambda: str(repo))

        rps.run(dry_run=True)  # 例外にならない（読み取りのみ）

        after = set(os.listdir(parquet_dir))
        assert after == before
