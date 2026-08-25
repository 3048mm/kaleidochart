"""全期間の段差スキャンのテスト（scripts/scan_price_anomalies.py）。

## なぜ後から書いたか

このスクリプトには**テストが無かった**。そのため 2026-08-25 に
`same_day_count` の算出を `count_real_symbols_per_day()` へ寄せた際、
**`ticker` 列がまだ merge されていない位置で呼んでいた**のに気付けず、
実行時に落ちるようになっていた。

```
KeyError: 'ticker'
```

`weekly_maintenance.py` 側は同じ変更でもテストがあったので通った。
**同じ関数を2箇所で使うなら、両方にテストを置く。**
"""

import os
import sys

import pandas as pd

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.scan_price_anomalies import classify, find_anomalies  # noqa: E402


def _symbols():
    return pd.DataFrame([
        {"id": 1, "ticker": "SPLITME", "category": "個別", "active": 1},
        {"id": 2, "ticker": "CALM", "category": "個別", "active": 1},
        {"id": 3, "ticker": "_THEME_", "category": "テーマ", "active": 1},
        {"id": 9, "ticker": "RETIRED", "category": "個別", "active": 0},
    ])


SPLIT_DATE = "2026-08-13"


def _prices():
    """1銘柄が併合（×30）し、同じ日に所属テーマも跳ねる状況を作る。

    実データの `BYND`（2026-08-13 に 1:30 併合、テーマ3本を巻き込み）を模す。

    > [!IMPORTANT]
    > **助走期間を十分に取ること。** `adv21` は `shift(1).rolling(21, min_periods=5)`
    > なので、ジャンプ日までに5営業日以上の履歴が無いと NaN になり、
    > 分類が `low_liquidity` に落ちて検証したい経路を通らない。
    """
    days = list(pd.bdate_range("2026-07-01", SPLIT_DATE).strftime("%Y-%m-%d"))
    days.append("2026-08-14")
    n = len(days)
    split_i = days.index(SPLIT_DATE)

    rows = []
    for i, d in enumerate(days):
        # SPLITME: 併合日に ×30。売買代金は連続する（分割の指紋）
        if i < split_i:
            rows.append({"symbol_id": 1, "date": d, "close": 0.41, "volume": 3.0e7})
        else:
            rows.append({"symbol_id": 1, "date": d, "close": 12.30, "volume": 1.0e6})
        # CALM: ずっと穏やか
        rows.append({"symbol_id": 2, "date": d, "close": 100.0, "volume": 1.0e6})
        # _THEME_: SPLITME を巻き込んで同日に跳ねる
        rows.append({"symbol_id": 3, "date": d,
                     "close": 1000.0 if i < split_i else 3400.0, "volume": 1.0e6})
        # RETIRED: 退役済み。母集団に入れてはいけない
        rows.append({"symbol_id": 9, "date": d,
                     "close": 50.0 if i < split_i else 5.0, "volume": 1.0e6})
    assert n > 25, "助走期間が足りない"
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# find_anomalies — ここが KeyError で落ちていた
# ---------------------------------------------------------------------------
def test_find_anomalies_runs_end_to_end():
    """**回帰テスト。** `ticker` が無い位置で分類の特徴量を作らないこと。"""
    a = find_anomalies(_prices(), _symbols())
    assert not a.empty
    for col in ("ticker", "same_day_count", "ratio", "dv_ratio", "adv21"):
        assert col in a.columns, f"{col} が欠けている"


def test_inactive_symbols_are_excluded():
    a = find_anomalies(_prices(), _symbols())
    assert "RETIRED" not in set(a["ticker"])


def test_same_day_count_ignores_virtual_themes():
    """仮想テーマを数えると**犯人が自分の作った波及に隠れる**。

    ここでは 08-13 に SPLITME と _THEME_ が同時に跳ねるが、
    実在銘柄は SPLITME の1件だけ。
    """
    a = find_anomalies(_prices(), _symbols())
    row = a[(a["ticker"] == "SPLITME") & (a["date"] == SPLIT_DATE)]
    assert len(row) == 1
    assert int(row.iloc[0]["same_day_count"]) == 1, \
        "仮想テーマまで数えている（market_wide に誤分類される）"


def test_the_split_is_classified_as_split_suspect():
    """同日にテーマが巻き込まれていても、犯人は split_suspect に出ること。"""
    out = classify(find_anomalies(_prices(), _symbols()))
    row = out[(out["ticker"] == "SPLITME") & (out["date"] == SPLIT_DATE)]
    assert row.iloc[0]["classification"] == "split_suspect"


def test_the_theme_is_classified_as_virtual():
    out = classify(find_anomalies(_prices(), _symbols()))
    row = out[(out["ticker"] == "_THEME_") & (out["date"] == SPLIT_DATE)]
    assert row.iloc[0]["classification"] == "virtual"


def test_calm_symbol_produces_no_anomaly():
    a = find_anomalies(_prices(), _symbols())
    assert "CALM" not in set(a["ticker"])
