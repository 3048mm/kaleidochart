"""価格履歴の充足（`backfill_price_history.py`）のテスト。

## 何のための機能か

個別銘柄の価格は `default_start_date`（2018-04-01）からしか取得していない。
そのため `optimization_validation` の `stress_bear` 窓（2018-10-01〜12-31）は
**遡りが約125営業日しかなく**、`sma_200` / `dist_52w_high_pct` /
`is_trend_template` が成立しない。`ema_200` は初期値の残存重みが 28.7% ある。

2017年まで遡れば約440営業日になり、ローリング窓は充足、EMA の残存も約1.25% になる。

## この機能が絶対に守るべきこと

1. **既存行を書き換えない**。追記だけを行う。全期間再構築（yfinance 取り直し）は
   上流が系列を切り落とした銘柄の履歴を失う（2026-08-02 に 17銘柄・8年分を喪失）。
   この機能はその轍を踏まないために「追記専用」で設計されている
2. **取得できなかった範囲を検出して報告する**。yfinance はレート制限(429)を
   `possibly delisted; no price data found` として返すため、**部分的にしか
   取れなくても成功に見える**（`backfill_symbol_history.py` の docstring が
   警告している既知の罠）
3. 既に十分な履歴がある銘柄は対象にしない（冪等）
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


def _px(rows):
    return pd.DataFrame(rows, columns=["symbol_id", "date", "open", "high", "low",
                                       "close", "volume"])


class TestSelectTargets:
    def test_picks_symbols_that_start_after_the_requested_date(self):
        from scripts.backfill_price_history import select_targets

        prices = _px([
            (1, "2018-04-02", 1, 1, 1, 1, 100),   # 2018 開始 → 対象
            (1, "2018-04-03", 1, 1, 1, 1, 100),
            (2, "2015-01-05", 1, 1, 1, 1, 100),   # 既に 2017 より前 → 対象外
            (3, "2024-06-03", 1, 1, 1, 1, 100),   # 2024 上場 → 対象外(後述)
        ])
        symbols = pd.DataFrame({"id": [1, 2, 3], "ticker": ["A", "SPY", "NEW"],
                                "category": ["個別", "市場", "個別"],
                                "active": [1, 1, 1]})

        got = select_targets(prices, symbols, start_date="2017-01-01",
                             categories=["個別"])
        ids = {t["symbol_id"] for t in got}
        assert 1 in ids, "2018 開始の個別銘柄が対象に入っていない"
        assert 2 not in ids, "カテゴリ外(市場)が混ざっている"

    def test_symbol_listed_after_start_date_is_not_a_target(self):
        """上場日が要求開始日より後の銘柄は、遡っても取るものが無い。

        2024年上場の銘柄に 2017年を要求しても意味がなく、
        毎回「取得できなかった」と報告され続けてしまう。
        """
        from scripts.backfill_price_history import select_targets

        prices = _px([(3, "2024-06-03", 1, 1, 1, 1, 100)])
        symbols = pd.DataFrame({"id": [3], "ticker": ["NEW"], "category": ["個別"],
                                "active": [1]})
        got = select_targets(prices, symbols, start_date="2017-01-01",
                             categories=["個別"], listed_grace_days=90)
        assert got == [], "上場日が後の銘柄を対象にしてはいけない"

    def test_is_idempotent(self):
        """既に十分な履歴がある銘柄は2回目以降の対象にならない。"""
        from scripts.backfill_price_history import select_targets

        prices = _px([(1, "2016-12-01", 1, 1, 1, 1, 100)])
        symbols = pd.DataFrame({"id": [1], "ticker": ["A"], "category": ["個別"],
                                "active": [1]})
        assert select_targets(prices, symbols, "2017-01-01", ["個別"]) == []


class TestMergeIsAppendOnly:
    def test_existing_rows_are_never_modified(self):
        """既存行は1行も変わってはいけない（追記専用）。"""
        from scripts.backfill_price_history import merge_append_only

        existing = _px([
            (1, "2018-04-02", 10.0, 11.0, 9.0, 10.5, 1000),
            (1, "2018-04-03", 10.5, 11.5, 9.5, 11.0, 1100),
        ])
        fetched = _px([
            (1, "2017-01-03", 5.0, 5.5, 4.5, 5.2, 500),
            # 既存と同じ日付を含めても既存が勝つ
            (1, "2018-04-02", 99.0, 99.0, 99.0, 99.0, 9999),
        ])

        out = merge_append_only(existing, fetched)

        assert len(out) == 3, "追記後の行数が合わない"
        kept = out[out["date"] == "2018-04-02"].iloc[0]
        assert kept["close"] == 10.5, "既存行が上書きされている"
        added = out[out["date"] == "2017-01-03"].iloc[0]
        assert added["close"] == 5.2

    def test_result_is_sorted_by_symbol_and_date(self):
        from scripts.backfill_price_history import merge_append_only

        existing = _px([(1, "2018-04-02", 1, 1, 1, 1, 1)])
        fetched = _px([(1, "2017-01-03", 1, 1, 1, 1, 1)])
        out = merge_append_only(existing, fetched)
        assert list(out["date"]) == ["2017-01-03", "2018-04-02"]


class TestVerifyCoverage:
    def test_reports_symbols_that_did_not_reach_the_requested_start(self):
        """429 の握り潰しを検出する。取得できなかった銘柄を列挙すること。"""
        from scripts.backfill_price_history import verify_coverage

        merged = _px([
            (1, "2017-01-03", 1, 1, 1, 1, 1),   # 要求どおり
            (2, "2017-11-01", 1, 1, 1, 1, 1),   # 途中までしか取れていない
        ])
        targets = [{"symbol_id": 1, "ticker": "A", "current_first": "2018-04-02"},
                   {"symbol_id": 2, "ticker": "B", "current_first": "2018-04-02"}]

        short = verify_coverage(merged, targets, start_date="2017-01-01",
                                tolerance_days=30)

        tickers = {s["ticker"] for s in short}
        assert "B" in tickers, "取得不足の銘柄を検出できていない"
        assert "A" not in tickers, "要求を満たした銘柄が誤検出されている"

    def test_symbol_with_no_new_rows_is_reported(self):
        """1行も取れなかった銘柄も報告対象。"""
        from scripts.backfill_price_history import verify_coverage

        merged = _px([(1, "2018-04-02", 1, 1, 1, 1, 1)])
        targets = [{"symbol_id": 1, "ticker": "A", "current_first": "2018-04-02"}]
        short = verify_coverage(merged, targets, "2017-01-01", tolerance_days=30)
        assert len(short) == 1 and short[0]["ticker"] == "A"
