"""捏造行の削除のテスト（scripts/delete_symbol_rows.py）。

## なぜ必要か

`data_collection/fetcher.py` の `fetch_daily_data` は取得直後に無条件で
`df.ffill()` する（upstream-data-diagnosis SKILL 限界I）。上流が欠損を返すと
**前日値がそのまま「取引があったこと」として保存される**。

2026-08-25 に `AVB` で実際に発火していた:

```
2026-08-14  O=184.55 H=185.62 L=182.74 C=184.06  vol=2,484,400   ← 実データ
2026-08-17  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-18  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-19  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-20  O=184.06 H=184.06 L=184.06 C=184.06  vol=0           ← 捏造
2026-08-21  O=65.90  H=65.90  L=65.90  C=65.90   vol=0           ← 捏造（別スケール）
```

**OHLC が全部同値かつ出来高0** が指紋。実在しない値動きなので、
バックテストが「4日間まったく動かなかった」ものとして扱ってしまう。
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

from scripts.delete_symbol_rows import (  # noqa: E402
    drop_rows,
    find_fabricated_rows,
)


def _avb_frame():
    """`AVB` の実データを模したフレーム。巻き込んではいけない別銘柄も入れる。"""
    rows = [
        ("2026-08-13", 180.27, 186.00, 180.27, 183.91, 1745300.0),
        ("2026-08-14", 184.55, 185.62, 182.74, 184.06, 2484400.0),
        ("2026-08-17", 184.06, 184.06, 184.06, 184.06, 0.0),   # 捏造
        ("2026-08-18", 184.06, 184.06, 184.06, 184.06, 0.0),   # 捏造
        ("2026-08-19", 184.06, 184.06, 184.06, 184.06, 0.0),   # 捏造
        ("2026-08-20", 184.06, 184.06, 184.06, 184.06, 0.0),   # 捏造
        ("2026-08-21", 65.900467, 65.900467, 65.900467, 65.900467, 0.0),   # 捏造
        ("2026-08-24", 67.36, 68.62, 67.36, 68.14, 6201087.0),
    ]
    out = [{"symbol_id": 606, "date": d, "open": o, "high": h, "low": lo,
            "close": c, "volume": v} for d, o, h, lo, c, v in rows]
    for d, o, h, lo, c, v in rows:
        out.append({"symbol_id": 99, "date": d, "open": 10.0, "high": 11.0,
                    "low": 9.0, "close": 10.5, "volume": 1000.0})
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# find_fabricated_rows
# ---------------------------------------------------------------------------
def test_finds_all_ffill_fabricated_rows():
    """OHLC 全同値かつ出来高0 の行だけを挙げる。"""
    out = find_fabricated_rows(_avb_frame(), 606)
    assert list(out["date"]) == ["2026-08-17", "2026-08-18", "2026-08-19",
                                 "2026-08-20", "2026-08-21"]


def test_real_rows_are_not_flagged():
    """出来高のある行は、値幅が狭くても捏造ではない。"""
    out = find_fabricated_rows(_avb_frame(), 606)
    assert "2026-08-14" not in set(out["date"])
    assert "2026-08-24" not in set(out["date"])


def test_zero_volume_with_a_real_range_is_not_flagged():
    """出来高0でも日中値があれば ffill ではない（板の薄い日など）。

    指紋は **OHLC 全同値 かつ 出来高0** の両方。片方だけで消してはいけない。
    """
    px = _avb_frame()
    px.loc[(px.symbol_id == 606) & (px.date == "2026-08-18"), "high"] = 185.0
    out = find_fabricated_rows(px, 606)
    assert "2026-08-18" not in set(out["date"])


def test_flat_row_with_volume_is_not_flagged():
    px = _avb_frame()
    px.loc[(px.symbol_id == 606) & (px.date == "2026-08-19"), "volume"] = 500.0
    out = find_fabricated_rows(px, 606)
    assert "2026-08-19" not in set(out["date"])


def test_other_symbols_are_never_inspected():
    out = find_fabricated_rows(_avb_frame(), 606)
    assert set(out["symbol_id"]) == {606}


def test_date_range_narrows_the_search():
    out = find_fabricated_rows(_avb_frame(), 606,
                               date_from="2026-08-18", date_to="2026-08-20")
    assert list(out["date"]) == ["2026-08-18", "2026-08-19", "2026-08-20"]


# ---------------------------------------------------------------------------
# drop_rows
# ---------------------------------------------------------------------------
def test_drops_only_the_given_dates():
    px = _avb_frame()
    out = drop_rows(px, 606, ["2026-08-17", "2026-08-18"])
    left = out[out.symbol_id == 606]["date"].tolist()
    assert "2026-08-17" not in left and "2026-08-18" not in left
    assert "2026-08-19" in left, "指定していない捏造行まで消している"


def test_other_symbols_keep_the_same_dates():
    px = _avb_frame()
    out = drop_rows(px, 606, ["2026-08-17", "2026-08-18"])
    pd.testing.assert_frame_equal(
        px[px.symbol_id == 99].reset_index(drop=True),
        out[out.symbol_id == 99].reset_index(drop=True),
    )


def test_dropping_nothing_is_a_noop():
    px = _avb_frame()
    pd.testing.assert_frame_equal(px, drop_rows(px, 606, []))


def test_row_count_drops_by_exactly_the_number_given():
    px = _avb_frame()
    out = drop_rows(px, 606, ["2026-08-17", "2026-08-18", "2026-08-19"])
    assert len(out) == len(px) - 3
