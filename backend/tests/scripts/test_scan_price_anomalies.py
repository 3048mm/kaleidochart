"""価格アノマリー・スキャンのテスト（scripts/scan_price_anomalies.py）

## なぜ足したか

このスクリプトには**テストが1件も無かった**。分類器（`indicators/price_anomaly.py`）
だけがテストされており、「分割記録を読んで各行に照合し、レポートに出す」という
つなぎの部分は無防備だった。実際、2026-09-10 に補正した破損2件は
**分類器の手前でメタデータが渡っていなかった**ことが原因（`doc/issue_list.md` P1）。

## ここで固定する振る舞い

- 分割記録を渡せば `market_wide` を上書きして `split_suspect` になる
- **渡さなければ従来どおり**（後方互換）
- 記録が無い / カバー期間外は**その事実が出力に出る**（沈黙しない）
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

from scripts.scan_price_anomalies import (  # noqa: E402
    classify,
    report_split_coverage,
)


def _anomalies() -> pd.DataFrame:
    """実データ由来の2行。

    `IESC` は 2026-08-24 に同日4件のうちの1つで、分割記録が無ければ
    `market_wide` に落ちる（本件そのもの）。`GOOD` は素の急落。
    """
    return pd.DataFrame([
        {"ticker": "IESC", "date": "2026-08-24", "prev_close": 71.5,
         "close": 33.83, "ratio": 0.473140, "adv21": 1.2e7,
         "dv_ratio": 1.0, "dv_vs_adv": 1.0, "same_day_count": 4},
        {"ticker": "GOOD", "date": "2020-03-09", "prev_close": 100.0,
         "close": 55.0, "ratio": 0.55, "adv21": 5.0e7,
         "dv_ratio": 2.5, "dv_vs_adv": 2.5, "same_day_count": 1},
    ])


SPLITS = {"IESC": [("2026-08-24", 2.0)]}


# ---------------------------------------------------------------------------
# classify — 分割記録の照合
# ---------------------------------------------------------------------------
def test_split_records_rescue_a_row_from_market_wide():
    """**本件の回帰テスト。** 分割記録があれば要対応に上がる。"""
    out = classify(_anomalies(), SPLITS)
    row = out[out["ticker"] == "IESC"].iloc[0]
    assert row["classification"] == "split_suspect"
    assert row["split_date"] == "2026-08-24"
    assert row["split_factor"] == 2.0
    assert row["split_days_off"] == 0


def test_without_split_records_classification_is_unchanged():
    """後方互換 — 記録を渡さなければ従来どおり `market_wide`。"""
    out = classify(_anomalies())
    assert out[out["ticker"] == "IESC"].iloc[0]["classification"] == "market_wide"
    assert out[out["ticker"] == "IESC"].iloc[0]["split_date"] == ""


def test_unrelated_row_is_untouched_by_split_records():
    """記録に無い銘柄は影響を受けない。"""
    out = classify(_anomalies(), SPLITS)
    good = out[out["ticker"] == "GOOD"].iloc[0]
    assert good["classification"] == "real_move"
    assert good["split_date"] == ""


def test_liquidity_flag_is_reported_but_does_not_change_class():
    """低流動でも分割記録と一致すれば要対応（§4-3）。フラグは読む順の材料。"""
    df = _anomalies()
    df.loc[df["ticker"] == "IESC", ["prev_close", "close", "adv21"]] = [
        1.0, 0.47, 100.0]
    out = classify(df, SPLITS)
    row = out[out["ticker"] == "IESC"].iloc[0]
    assert row["below_liquidity_floor"]
    assert row["classification"] == "split_suspect"


# ---------------------------------------------------------------------------
# report_split_coverage — **沈黙しない**ための段
# ---------------------------------------------------------------------------
def test_missing_records_are_reported_loudly(capsys):
    """記録が無いことを黙って通さない。"""
    report_split_coverage(_anomalies(), None, "X/split_records.json")
    out = capsys.readouterr().out
    assert "無し" in out
    assert "scan_split_consistency" in out, "生成方法が案内されていない"


def test_coverage_gap_is_counted(capsys):
    """カバー期間より前のアノマリー件数を出す（照合していない範囲）。"""
    records = {"generated_at": "2026-09-10T20:00:00", "start": "2024-09-11",
               "years": 2, "tickers_fetched": 2956, "failed": [],
               "splits": {"IESC": [["2026-08-24", 2.0]]}}
    report_split_coverage(_anomalies(), records, "X/split_records.json")
    out = capsys.readouterr().out
    # 2020-03-09 の1件がカバー期間外
    assert "カバー期間外のアノマリー: 1 件" in out


def test_failed_tickers_are_reported(capsys):
    """取得できなかった銘柄も黙って落とさない。"""
    records = {"generated_at": "2026-09-10T20:00:00", "start": "2024-09-11",
               "years": 2, "tickers_fetched": 10, "failed": ["AAA", "BBB"],
               "splits": {}}
    report_split_coverage(_anomalies(), records, "X/split_records.json")
    assert "取得できなかった銘柄: 2 件" in capsys.readouterr().out
