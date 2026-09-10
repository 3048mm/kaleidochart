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


# ---------------------------------------------------------------------------
# 分割記録の照合（2026-09-10 追加）
#
# 分類器は**分割メタデータを一切見ていなかった**ため、比の大きさと同日件数だけで
# 判定していた。2026-09-10 に補正した本物の破損2件は、どちらもレポートに
# 入っていながら捨てられていた（`doc/issue_list.md` P1）:
#
#     IESC  2026-08-24  ratio=0.4731  same_day_count=4  → market_wide
#     WLFC  2026-07-20  ratio=0.3294  same_day_count=1  → real_move
#
# ここでは「つなぎ」を固定する。分類そのものは
# `backend/tests/indicators/test_price_anomaly.py` が受け持つ。
# ---------------------------------------------------------------------------
IESC_ROW = pd.DataFrame([
    {"ticker": "IESC", "date": "2026-08-24", "prev_close": 71.5,
     "close": 33.83, "ratio": 0.473140, "adv21": 1.2e7,
     "dv_ratio": 1.0, "dv_vs_adv": 1.0, "same_day_count": 4},
    {"ticker": "GOOD", "date": "2020-03-09", "prev_close": 100.0,
     "close": 55.0, "ratio": 0.55, "adv21": 5.0e7,
     "dv_ratio": 2.5, "dv_vs_adv": 2.5, "same_day_count": 1},
])
SPLITS = {"IESC": [("2026-08-24", 2.0)]}


def test_split_records_rescue_a_row_from_market_wide():
    """**本件の回帰テスト。** 同日4件でも分割記録があれば要対応に上がる。"""
    out = classify(IESC_ROW.copy(), SPLITS)
    row = out[out["ticker"] == "IESC"].iloc[0]
    assert row["classification"] == "split_suspect"
    assert row["split_date"] == "2026-08-24"
    assert row["split_factor"] == 2.0
    assert row["split_days_off"] == 0


def test_without_split_records_classification_is_unchanged():
    """後方互換 — 記録を渡さなければ従来どおり `market_wide`。"""
    out = classify(IESC_ROW.copy())
    assert out[out["ticker"] == "IESC"].iloc[0]["classification"] == "market_wide"
    assert out[out["ticker"] == "IESC"].iloc[0]["split_date"] == ""


def test_unrelated_row_is_untouched_by_split_records():
    """記録に無い銘柄は影響を受けない。"""
    out = classify(IESC_ROW.copy(), SPLITS)
    good = out[out["ticker"] == "GOOD"].iloc[0]
    assert good["classification"] == "real_move"
    assert good["split_date"] == ""


def test_liquidity_flag_is_reported_but_does_not_change_class():
    """低流動でも分割記録と一致すれば要対応（計画書 §4-3）。

    フラグはレポートの**読む順**を決めるためだけに使う。
    """
    df = IESC_ROW.copy()
    df.loc[df["ticker"] == "IESC", ["prev_close", "close", "adv21"]] = [
        1.0, 0.47, 100.0]
    out = classify(df, SPLITS)
    row = out[out["ticker"] == "IESC"].iloc[0]
    assert row["below_liquidity_floor"]
    assert row["classification"] == "split_suspect"


def test_nearby_but_mismatched_split_is_kept_for_reporting():
    """`STKH` 型 — 比が合わず要対応にはしないが、**近傍にあることは残す**。"""
    df = pd.DataFrame([
        {"ticker": "STKH", "date": "2026-07-28", "prev_close": 3.0,
         "close": 8.0, "ratio": 2.6667, "adv21": 1.0e7,
         "dv_ratio": 1.0, "dv_vs_adv": 1.0, "same_day_count": 1},
    ])
    out = classify(df, {"STKH": [("2026-07-27", 1 / 3)]})
    row = out.iloc[0]
    assert row["classification"] != "split_suspect", "比が合わないのに要対応にしている"
    assert row["nearby_split"] == "2026-07-27 x0.333333"


# ---------------------------------------------------------------------------
# report / _print_rows — **出力まで通すテスト**
#
# 分類までしかテストしていなかったため、レポート出力に実害のあるバグを通した
# （2026-09-11 のレビューで発覚）。`split_days_off` は未一致行があると float64 に
# なり、`f"{off:+d}"` が `ValueError: Unknown format code 'd'` で落ちる。
# **CSV とスナップショットの書き出し前に死ぬのでスキャン結果が丸ごと失われる。**
# しかも落ちる条件は「ずれが0日でない一致行がある」= 本機能が狙った WLFC そのもの。
# ---------------------------------------------------------------------------
MIXED = pd.DataFrame([
    # 一致する行（ずれ1日）。これが float 化の引き金を引く
    {"ticker": "WLFC", "date": "2026-07-20", "prev_close": 180.0,
     "close": 59.3, "ratio": 0.3294, "adv21": 3.0e7,
     "dv_ratio": 3.5, "dv_vs_adv": 3.5, "same_day_count": 1},
    # 一致しない行。`split_days_off` に NaN が入り、列全体が float64 になる
    {"ticker": "GOOD", "date": "2020-03-09", "prev_close": 100.0,
     "close": 55.0, "ratio": 0.55, "adv21": 5.0e7,
     "dv_ratio": 2.5, "dv_vs_adv": 2.5, "same_day_count": 1},
])
MIXED_SPLITS = {"WLFC": [("2026-07-21", 3.0)]}


def test_print_rows_survives_float_days_off(capsys):
    """**回帰テスト。** 未一致行が混ざっても一致行を出力できる。"""
    from scripts.scan_price_anomalies import _print_rows

    out = classify(MIXED.copy(), MIXED_SPLITS)
    _print_rows(out[out["classification"] == "split_suspect"], limit=5)
    printed = capsys.readouterr().out
    assert "WLFC" in printed
    assert "+1日" in printed, f"ずれの表示が壊れている: {printed}"


def test_report_writes_csv_even_with_matched_rows(tmp_path, capsys):
    """**レポート全体が最後まで通ること。** CSV とスナップショットが残る。

    ここが落ちると、検出結果そのものが失われる（例外はレポート本文の途中で出るため、
    後段の `to_csv` / スナップショット保存に到達しない）。
    """
    from scripts.scan_price_anomalies import report

    out = classify(MIXED.copy(), MIXED_SPLITS)
    out["category"] = "個別"
    report(out, str(tmp_path), records=None, records_path="X.json")

    csv_path = tmp_path / "price_anomalies.csv"
    assert csv_path.exists(), "CSV が書かれていない"
    written = pd.read_csv(csv_path)
    assert set(written["ticker"]) == {"WLFC", "GOOD"}
    assert (tmp_path / "price_anomalies_snapshot.json").exists()


def test_low_liquidity_split_is_listed_in_its_own_section(capsys):
    """低流動の要対応は後段にまとめて出す（隠さない・§4-3）。"""
    from scripts.scan_price_anomalies import report

    df = MIXED.copy()
    df.loc[df["ticker"] == "WLFC", ["prev_close", "close", "adv21"]] = [
        1.0, 0.33, 100.0]
    out = classify(df, MIXED_SPLITS)
    out["category"] = "個別"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        report(out, d, records=None, records_path="X.json")
    printed = capsys.readouterr().out
    assert "うち低流動 1 件" in printed, printed


# ---------------------------------------------------------------------------
# report_split_coverage — **沈黙しない**ための段
# ---------------------------------------------------------------------------
def test_missing_records_are_reported_loudly(capsys):
    """記録が無いことを黙って通さない。"""
    from scripts.scan_price_anomalies import report_split_coverage

    report_split_coverage(IESC_ROW, None, "X/split_records.json")
    out = capsys.readouterr().out
    assert "無し" in out
    assert "scan_split_consistency" in out, "生成方法が案内されていない"


def test_coverage_gap_is_counted(capsys):
    """カバー期間より前のアノマリー件数を出す（照合していない範囲）。"""
    from scripts.scan_price_anomalies import report_split_coverage

    records = {"generated_at": "2026-09-10T20:00:00", "start": "2024-09-11",
               "years": 2, "tickers_fetched": 2984, "failed": [],
               "splits": {"IESC": [["2026-08-24", 2.0]]}}
    report_split_coverage(IESC_ROW, records, "X/split_records.json")
    # 2020-03-09 の1件がカバー期間外
    assert "カバー期間外のアノマリー: 1 件" in capsys.readouterr().out


def test_failed_tickers_are_reported(capsys):
    """取得できなかった銘柄も黙って落とさない。"""
    from scripts.scan_price_anomalies import report_split_coverage

    records = {"generated_at": "2026-09-10T20:00:00", "start": "2024-09-11",
               "years": 2, "tickers_fetched": 10, "failed": ["AAA", "BBB"],
               "splits": {}}
    report_split_coverage(IESC_ROW, records, "X/split_records.json")
    assert "取得できなかった銘柄: 2 件" in capsys.readouterr().out


def test_stale_records_are_warned_loudly(capsys):
    """**古い分割記録を黙って新鮮なものとして使わない**（`doc/issue_list.md` P2）。

    `split_records.json` は手で回したときだけ更新される。放置すると
    「照合したつもり」になるので、閾値を超えたら警告する。
    """
    from datetime import datetime
    from scripts.scan_price_anomalies import report_split_coverage

    records = {"generated_at": "2026-01-01T00:00:00", "start": "2024-01-01",
               "years": 2, "tickers_fetched": 10, "failed": [], "splits": {}}
    report_split_coverage(IESC_ROW, records, "X/split_records.json",
                          now=datetime(2026, 9, 11))
    printed = capsys.readouterr().out
    assert "古い" in printed or "STALE" in printed.upper()
    assert "scan_split_consistency" in printed, "更新方法が案内されていない"


def test_fresh_records_are_not_warned(capsys):
    """新鮮な記録では警告を出さない（誤検出しない）。"""
    from datetime import datetime
    from scripts.scan_price_anomalies import report_split_coverage

    records = {"generated_at": "2026-09-10T20:00:00", "start": "2024-09-11",
               "years": 2, "tickers_fetched": 10, "failed": [], "splits": {}}
    report_split_coverage(IESC_ROW, records, "X/split_records.json",
                          now=datetime(2026, 9, 11))
    printed = capsys.readouterr().out
    assert "古い" not in printed
