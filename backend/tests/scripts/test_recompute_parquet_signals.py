"""Parquet の T5（market_signals）全期間再計算（`recompute_parquet_signals.py`）のテスト。

I/O（Parquet 読み書き・pointer 更新）を含まない、計算部分のみを対象にする:

  - `build_market_signals_frame`: SPY/VIX/VXV/breadth の DataFrame から
    Parquet の `market_signals` スキーマに合わせた出力を組み立てる
  - `compare_by_period`: 現行世代との期間別・列別の差分集計（dry-run 表示用）

背景: `doc/in_progress/t5_parquet_rebuild_plan.md` §3.2
"""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from indicators.market_signals import compute_breadth_momentum
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
        assert out["spy_above_sma200"].dtype == np.int64
        assert out["distribution_days"].dtype == np.int64
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
