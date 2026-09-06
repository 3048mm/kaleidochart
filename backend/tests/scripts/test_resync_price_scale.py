"""上流への価格スケール再同期（`resync_price_scale.py`）のテスト。

## 何のための機能か

分割日に上流（yfinance）は**全履歴を再調整する**が、こちらのマージは
`drop_duplicates(keep='last')` で**行を書き換えない**ため、既存行が古いスケールの
まま取り残される。2026-09-04 の価格履歴充足で 5銘柄（MNST / APH / RUSHA /
VISN / IEP）にこれが見つかった。

`adjust_symbol_split.py` は「上流が調整して**くれない**」逆のケース用で、係数を
人間が申告する。取り違えると壊れた値を取り込むので、両者は別スクリプトにしてある。

## この機能が絶対に守るべきこと

1. **上流と既に一致している行は触らない**。全期間を一律に掛けると、分割後に
   取得された正しい行まで壊す（MNST / RUSHA は途中に正常行が混ざっており、
   単一 factor では表現できない）
2. **上流に無い日付の行を消したり歪めたりしない**。上流の欠測はこちらの行の
   誤りを意味しない
3. **`market_cap` を触らない**。分割で株数と株価が逆方向に動くので時価総額は不変
4. **補正後に上流と一致しない行が残っていたら書かずに落ちる**。上流側が壊れて
   いる銘柄に当てると壊れた値を取り込むため、事後条件が唯一の歯止めになる
"""
import os
import sys

import pandas as pd
import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _ours(rows):
    """(date, close) から手元の価格を作る。"""
    return pd.DataFrame(rows, columns=["date", "close"])


def _up(rows):
    """(date, up_close) から上流の価格を作る。"""
    return pd.DataFrame(rows, columns=["date", "up_close"])


def _px(rows):
    """Parquet の prices 相当（market_cap まで含む）。"""
    return pd.DataFrame(rows, columns=["symbol_id", "date", "open", "high", "low",
                                       "close", "volume", "market_cap"])


class TestMeasureFactors:
    def test_computes_upstream_over_ours_for_mismatched_rows(self):
        """係数は 上流/手元。2倍にズレていれば ×0.5 が返る。"""
        from scripts.resync_price_scale import measure_factors

        got = measure_factors(_ours([("2020-01-02", 20.0)]),
                              _up([("2020-01-02", 10.0)]), tolerance=0.02)
        assert got.loc[0, "factor"] == pytest.approx(0.5)
        assert bool(got.loc[0, "corrected"])

    def test_rows_already_matching_upstream_are_left_alone(self):
        """一致している行の係数は 1.0（＝補正しない）。

        MNST / RUSHA は途中に正常行が混ざる。全期間へ一律に掛けると
        **分割後に取得された正しい行を壊す**。
        """
        from scripts.resync_price_scale import measure_factors

        got = measure_factors(_ours([("2020-01-02", 10.0)]),
                              _up([("2020-01-02", 10.0)]), tolerance=0.02)
        assert got.loc[0, "factor"] == 1.0
        assert not bool(got.loc[0, "corrected"])

    def test_difference_within_tolerance_is_not_corrected(self):
        """許容範囲内の差は当日の値動き・端数として補正しない。"""
        from scripts.resync_price_scale import measure_factors

        got = measure_factors(_ours([("2020-01-02", 10.0)]),
                              _up([("2020-01-02", 10.1)]), tolerance=0.02)
        assert got.loc[0, "factor"] == 1.0

    def test_dates_missing_upstream_are_left_alone(self):
        """上流に無い日付は係数 1.0。上流の欠測で手元の行を歪めない。"""
        from scripts.resync_price_scale import measure_factors

        got = measure_factors(_ours([("2020-01-02", 20.0), ("2020-01-03", 20.0)]),
                              _up([("2020-01-02", 10.0)]), tolerance=0.02)
        row = got[got["date"] == "2020-01-03"].iloc[0]
        assert row["factor"] == 1.0
        assert not bool(row["matched"])

    def test_mixed_scale_history_gets_per_row_factors(self):
        """古い行だけズレ、新しい行は一致、という実際の形を再現する。"""
        from scripts.resync_price_scale import measure_factors

        got = measure_factors(
            _ours([("2018-01-02", 20.0), ("2018-01-03", 30.0), ("2026-01-02", 5.0)]),
            _up([("2018-01-02", 10.0), ("2018-01-03", 15.0), ("2026-01-02", 5.0)]),
            tolerance=0.02)
        assert list(got["factor"].round(4)) == [0.5, 0.5, 1.0]


class TestApplyFactors:
    def test_scales_ohlc_up_and_volume_down(self):
        from scripts.resync_price_scale import apply_factors

        px = _px([(1, "2020-01-02", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8)])
        factors = pd.DataFrame({"date": ["2020-01-02"], "factor": [0.5]})

        out = apply_factors(px, 1, factors)
        r = out.iloc[0]
        assert (r["open"], r["high"], r["low"], r["close"]) == (1.0, 2.0, 0.5, 1.5)
        assert r["volume"] == 2000.0

    def test_market_cap_is_never_touched(self):
        """分割は株数と株価を逆方向に動かすので時価総額は不変。"""
        from scripts.resync_price_scale import apply_factors

        px = _px([(1, "2020-01-02", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8)])
        factors = pd.DataFrame({"date": ["2020-01-02"], "factor": [0.5]})

        out = apply_factors(px, 1, factors)
        assert out.iloc[0]["market_cap"] == 5e8

    def test_other_symbols_are_not_affected(self):
        """スコープを間違えて別銘柄を壊さないこと。"""
        from scripts.resync_price_scale import apply_factors

        px = _px([(1, "2020-01-02", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8),
                  (2, "2020-01-02", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8)])
        factors = pd.DataFrame({"date": ["2020-01-02"], "factor": [0.5]})

        out = apply_factors(px, 1, factors)
        other = out[out["symbol_id"] == 2].iloc[0]
        assert other["close"] == 3.0 and other["volume"] == 1000.0

    def test_dates_absent_from_factors_keep_their_values(self):
        """係数表に無い日付は係数 1.0 として扱う（NaN で潰さない）。"""
        from scripts.resync_price_scale import apply_factors

        px = _px([(1, "2020-01-02", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8),
                  (1, "2020-01-03", 2.0, 4.0, 1.0, 3.0, 1000.0, 5e8)])
        factors = pd.DataFrame({"date": ["2020-01-02"], "factor": [0.5]})

        out = apply_factors(px, 1, factors)
        kept = out[out["date"] == "2020-01-03"].iloc[0]
        assert kept["close"] == 3.0 and kept["volume"] == 1000.0


class TestVerifyAfter:
    def _many(self, n, ours_close, up_close, start=0):
        dates = [f"2020-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}" for i in
                 range(start, start + n)]
        px = _px([(1, d, 1.0, 1.0, 1.0, ours_close, 1.0, 1.0) for d in dates])
        up = _up([(d, up_close) for d in dates])
        return px, up

    def test_passes_when_everything_matches(self):
        from scripts.resync_price_scale import verify_after

        px, up = self._many(300, 10.0, 10.0)
        got = verify_after(px, 1, up, "T", tolerance=0.02)
        assert got["off"] == 0 and got["match_ratio"] == 1.0

    def test_raises_when_rows_still_disagree_with_upstream(self):
        """上流側が壊れている銘柄に当てた場合を検出する。**唯一の歯止め。**"""
        from scripts.resync_price_scale import ResyncError, verify_after

        px, up = self._many(300, 10.0, 20.0)
        with pytest.raises(ResyncError, match="上流と一致しない"):
            verify_after(px, 1, up, "T", tolerance=0.02)

    def test_raises_when_overlap_is_too_small_to_judge(self):
        """照合材料が足りないまま本番へ書かせない。"""
        from scripts.resync_price_scale import (MIN_OVERLAP_ROWS, ResyncError,
                                                verify_after)

        px, up = self._many(MIN_OVERLAP_ROWS - 1, 10.0, 10.0)
        with pytest.raises(ResyncError, match="重なる行"):
            verify_after(px, 1, up, "T", tolerance=0.02)
