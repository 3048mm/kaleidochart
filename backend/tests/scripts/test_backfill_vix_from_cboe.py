"""VIX 系指数の CBOE 充足（`backfill_vix_from_cboe.py`）のテスト。

## 何のための機能か

Yahoo は VIX の**期間構造**（`^VIX3M` / `^VIX9D` / `^VIX6M`）を落とすことがある。
`^VIX` 本体は無傷なので全体の異常としては現れない。`^VIX3M` は MTS の入力なので、
欠けると直前値の持ち越しになり、2026-07-20〜08-04 の 12営業日が信頼できない
状態になっていた（パイプライン自身が警告を出していた）。

上流から取り直しても直らない — 決定論テスト5通りで全て 0/14 だった。
CBOE は VIX の算出元なので一次情報に近く、重複4,118行の相対差は p50=2.06e-08。

## この機能が絶対に守るべきこと

1. **既存行を書き換えない**。`^VIX3M` は手元155日 / 上流136日で**こちらの方が
   完全**。取り直すと欠損が 14日→34日 に悪化する。だから追記専用にしてある
2. **重複期間で一致しなければ書かない**。別の指数を混ぜたら価格系列が壊れる
3. **営業日カレンダーに無い日は足さない**。CBOE は独自の営業日を持つため、
   そのまま入れると他銘柄に無い日付の行ができて T4 の母集団がズレる
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


def _series(dates, closes):
    return pd.DataFrame({"date": list(dates), "close": list(closes)})


def _many(n, close, start="2020-01-01"):
    """検証の下限行数を満たすだけの系列を作る。

    日付は**一意**でなければならない。重複すると merge が直積に膨らみ、
    ズレた行の数え上げが狂う。
    """
    dates = pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d").tolist()
    return dates, [close] * n


class TestVerifyOverlap:
    def test_passes_when_series_agree(self):
        from scripts.backfill_vix_from_cboe import verify_overlap

        d, c = _many(300, 20.0)
        got = verify_overlap(_series(d, c), _series(d, c), "^VIX3M")
        assert got["off"] == 0

    def test_raises_when_series_are_different(self):
        """別の指数を掴んだ場合に書かせない。**唯一の歯止め。**"""
        from scripts.backfill_vix_from_cboe import CboeBackfillError, verify_overlap

        d, c = _many(300, 20.0)
        _, other = _many(300, 35.0)
        with pytest.raises(CboeBackfillError, match="別系列"):
            verify_overlap(_series(d, c), _series(d, other), "^VIX3M")

    def test_tolerates_a_few_mismatched_rows(self):
        """実データには丸め由来の少数のズレがある（4,118行中4行）。"""
        from scripts.backfill_vix_from_cboe import verify_overlap

        d, c = _many(300, 20.0)
        theirs = list(c)
        theirs[0] = 25.0          # 1行だけ大きくズレる
        got = verify_overlap(_series(d, c), _series(d, theirs), "^VIX3M")
        assert got["off"] == 1

    def test_raises_when_overlap_is_too_small_to_judge(self):
        from scripts.backfill_vix_from_cboe import (MIN_OVERLAP_ROWS,
                                                    CboeBackfillError, verify_overlap)

        d, c = _many(MIN_OVERLAP_ROWS - 1, 20.0)
        with pytest.raises(CboeBackfillError, match="重複が"):
            verify_overlap(_series(d, c), _series(d, c), "^VIX3M")


class TestSelectFillDates:
    def test_picks_only_dates_we_are_missing(self):
        from scripts.backfill_vix_from_cboe import select_fill_dates

        mine = _series(["2026-07-17", "2026-08-05"], [19.0, 19.0])
        cboe = _series(["2026-07-17", "2026-07-20", "2026-08-05"], [19.0, 20.4, 19.0])
        cal = {"2026-07-17", "2026-07-20", "2026-08-05"}

        assert select_fill_dates(mine, cboe, cal, "2010-04-01") == ["2026-07-20"]

    def test_skips_dates_absent_from_the_trading_calendar(self):
        """CBOE 独自の営業日を持ち込むと T4 の母集団がズレる。"""
        from scripts.backfill_vix_from_cboe import select_fill_dates

        mine = _series(["2026-07-17"], [19.0])
        cboe = _series(["2026-07-17", "2026-07-18"], [19.0, 20.0])
        cal = {"2026-07-17"}          # 07-18 は他銘柄に無い

        assert select_fill_dates(mine, cboe, cal, "2010-04-01") == []

    def test_skips_dates_before_the_requested_start(self):
        """CBOE は 2009年から配信しているが、こちらの起点より前は足さない。"""
        from scripts.backfill_vix_from_cboe import select_fill_dates

        mine = _series(["2010-04-01"], [19.0])
        cboe = _series(["2009-09-18", "2010-04-01"], [25.0, 19.0])
        cal = {"2009-09-18", "2010-04-01"}

        assert select_fill_dates(mine, cboe, cal, "2010-04-01") == []

    def test_returns_dates_sorted(self):
        from scripts.backfill_vix_from_cboe import select_fill_dates

        mine = _series(["2026-07-17"], [19.0])
        cboe = _series(["2026-08-04", "2026-07-20", "2026-07-21"], [19.0, 20.0, 20.0])
        cal = {"2026-07-20", "2026-07-21", "2026-08-04"}

        assert select_fill_dates(mine, cboe, cal, "2010-04-01") == [
            "2026-07-20", "2026-07-21", "2026-08-04"]
