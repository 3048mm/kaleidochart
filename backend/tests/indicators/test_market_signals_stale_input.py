"""MTS 入力（^VIX / ^VIX3M）の供給停止検知に関するテスト。

背景（2026-07-30 発見）:
Yahoo が ^VIX3M を 2026-07-20〜07-29 の 8営業日 null で返し、market_signals の
`ffill()` が 07-17 の値（20.54）を持ち越して vxv_vix_ratio を計算し続けていた。
警告も無く、`market_trend_score` は正常値に見えていた。

【重要】ffill は残す。VIX3M は3ヶ月物で動きが緩やかなため、短い空白なら
直前値の持ち越しは良い近似である。実測でも空白直前 20.54 / 空白明け 20.51 と
ほぼ動いておらず、ffill の MTS 誤差は平均 0.055pt だった（VIX 単独の代替式に
切り替えると平均 4.055pt ずれ、73倍悪化する）。したがってここで検証するのは
「値を変えないこと」と「気づけること」の two 点。
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from indicators.market_signals import (
    STALE_INPUT_WARN_DAYS,
    find_stale_input_gaps,
    report_stale_input_gaps,
)

DATES = [date(2026, 7, 13) + timedelta(days=i) for i in range(12)]


def _frame(values):
    return pd.DataFrame({"date": DATES[:len(values)], "vxv_close": values})


class TestFindStaleInputGaps:
    def test_no_gap(self):
        assert find_stale_input_gaps(_frame([1.0] * 5), "vxv_close") == []

    def test_single_gap_in_the_middle(self):
        df = _frame([1.0, 1.0, None, None, None, 2.0])
        gaps = find_stale_input_gaps(df, "vxv_close")
        assert len(gaps) == 1
        start, end, n = gaps[0]
        assert (start, end, n) == (DATES[2], DATES[4], 3)

    def test_gap_extending_to_the_end(self):
        """空白が続いたまま最新日を迎えるケース（今回の実障害の形）。"""
        df = _frame([1.0, 1.0, None, None, None])
        gaps = find_stale_input_gaps(df, "vxv_close")
        assert gaps == [(DATES[2], DATES[4], 3)]

    def test_multiple_gaps(self):
        df = _frame([1.0, None, 2.0, None, None, 3.0])
        gaps = find_stale_input_gaps(df, "vxv_close")
        assert [n for _s, _e, n in gaps] == [1, 2]

    def test_all_missing(self):
        df = _frame([None, None, None])
        assert find_stale_input_gaps(df, "vxv_close") == [(DATES[0], DATES[2], 3)]

    def test_missing_column_is_tolerated(self):
        assert find_stale_input_gaps(pd.DataFrame({"date": DATES[:3]}), "nope") == []


class TestReportStaleInputGaps:
    def test_warns_when_gap_reaches_threshold(self, caplog):
        n = STALE_INPUT_WARN_DAYS
        df = _frame([1.0] + [None] * n)
        with caplog.at_level("INFO"):
            gaps = report_stale_input_gaps(df, "vxv_close", "^VIX3M")
        assert gaps and gaps[0][2] == n
        assert any(r.levelname == "WARNING" for r in caplog.records)
        assert "^VIX3M" in caplog.text

    def test_short_gap_is_info_not_warning(self, caplog):
        df = _frame([1.0, None, None, 2.0])
        with caplog.at_level("INFO"):
            report_stale_input_gaps(df, "vxv_close", "^VIX3M")
        assert not any(r.levelname == "WARNING" for r in caplog.records)
        assert "^VIX3M" in caplog.text

    def test_does_not_modify_the_frame(self):
        """検知は副作用を持たない（ffill は呼び出し側の責務）。"""
        df = _frame([1.0, None, None, 2.0])
        before = df["vxv_close"].isna().tolist()
        report_stale_input_gaps(df, "vxv_close", "^VIX3M")
        assert df["vxv_close"].isna().tolist() == before


class TestFfillBehaviourIsPreserved:
    """回帰: 検知を足しても vxv_vix_ratio の計算結果が変わらないこと。"""

    def test_ratio_still_uses_carried_forward_value(self):
        from indicators.market_signals import calculate_market_signals

        n = 6
        spy = pd.DataFrame({
            "date": DATES[:n],
            "close": [500.0] * n, "high": [501.0] * n, "low": [499.0] * n,
            "volume": [1_000_000] * n,
        })
        vix = pd.DataFrame({"date": DATES[:n], "close": [20.0] * n})
        # VIX3M は最初の2日だけ実データ、以降は欠損
        vxv = pd.DataFrame({"date": DATES[:2], "close": [22.0, 24.0]})

        out = calculate_market_signals(spy, vix, vxv, None)
        ratios = out["vxv_vix_ratio"].tolist()
        # 3日目以降は 24.0 / 20.0 = 1.2 が持ち越されること
        assert ratios[1] == pytest.approx(1.2)
        assert all(r == pytest.approx(1.2) for r in ratios[2:]), (
            "ffill の挙動が変わっている。短期空白では持ち越しの方が精度が高いため維持すべき"
        )
