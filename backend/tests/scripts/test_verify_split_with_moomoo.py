"""moomoo API による分割記録の独立照合（`verify_split_with_moomoo.py`）のテスト。

## 背景

`scan_split_consistency.py`（第1・2段階）は yfinance 自身の自己矛盾は検出できるが、
**上流(yfinance)が分割記録そのものを持たない銘柄は原理的に検出できない**
（`SOXS`/`UAVS`等）。本モジュールは moomoo API を独立ソースとして、
yfinance側の記録（`split_records.json`）と突き合わせる（第3段階）。

## この機能が絶対に守るべきこと

1. **自動修正しない。報告だけ。**（`scan_split_consistency.py`と同じ方針）
2. **メタデータ同士の突き合わせに留める。** 価格の実際のジャンプとの比較
   （ノイズを含む）はしない（`doc/in_progress/moomoo_split_verification_plan.md` §2.2）
3. **「一致」「不一致」「moomoo側のみ」「yfinance側のみ」「両ソースとも記録なし」を
   取り違えない。** 特に最後の2つを混同すると「照合したつもり」になる
"""
import os
import sys

import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# --- 比の変換仕様 -------------------------------------------------------------
# 実測（moomoo-api SKILL 参照）: moomoo `split_ratio` == 1.0 / yfinance `factor`
# 独立の実測は AAPL（4:1 forward split）/ STKH（1:3 reverse split）の2件。

class TestExpectedRatioFromYfinanceFactor:
    def test_aapl_forward_split_4to1(self):
        """AAPL 2020-08-31 4:1 forward split: yfinance factor=4.0 → moomoo split_ratio=0.25。"""
        from scripts.verify_split_with_moomoo import expected_ratio_from_yfinance_factor

        assert expected_ratio_from_yfinance_factor(4.0) == pytest.approx(0.25)

    def test_stkh_reverse_split_1to3(self):
        """STKH 2026-07-27 1:3 reverse split: yfinance factor=1/3 → moomoo split_ratio=3.0。"""
        from scripts.verify_split_with_moomoo import expected_ratio_from_yfinance_factor

        assert expected_ratio_from_yfinance_factor(1 / 3) == pytest.approx(3.0)


class TestIsDiscriminableRatio:
    """株式配当（factor≈1.0）は段差では識別できない（既存の MIN_DISCRIMINABLE_GAP と同じ考え方）。"""

    def test_real_split_ratio_is_discriminable(self):
        from scripts.verify_split_with_moomoo import is_discriminable_ratio

        assert is_discriminable_ratio(0.25) is True   # AAPL 4:1
        assert is_discriminable_ratio(3.0) is True     # STKH 1:3

    def test_stock_dividend_like_ratio_is_not_discriminable(self):
        from scripts.verify_split_with_moomoo import is_discriminable_ratio

        assert is_discriminable_ratio(1.01) is False
        assert is_discriminable_ratio(0.99) is False


# --- 突き合わせ判定ロジック ----------------------------------------------------

class TestMatchSplitEvents:
    """`moomoo_events: [(date, split_ratio)]` と `yfinance_events: [(date, factor)]` を
    日付窓＋比率許容幅で突き合わせる。"""

    def test_positive_control_aapl_all_five_splits_match(self):
        """陽性対照: AAPLの既知5分割が全て一致と判定されること。"""
        from scripts.verify_split_with_moomoo import match_split_events

        moomoo_events = [
            ("1987-06-16", 0.5), ("2000-06-21", 0.5), ("2005-02-28", 0.5),
            ("2014-06-09", 1 / 7), ("2020-08-31", 0.25),
        ]
        yfinance_events = [
            ("1987-06-16", 2.0), ("2000-06-21", 2.0), ("2005-02-28", 2.0),
            ("2014-06-09", 7.0), ("2020-08-31", 4.0),
        ]
        rows = match_split_events(moomoo_events, yfinance_events)
        assert len(rows) == 5
        assert all(r["verdict"] == "match" for r in rows)

    def test_stkh_metadata_match_despite_known_price_level_discrepancy(self):
        """`STKH`: 両ソースの記録（メタデータ）は一致する素直なケース。

        価格レベルで11%乖離していた事実（手動調査で確定済み）は、このスクリプトの
        検証範囲外（§2.2「価格レベルの突き合わせは標準機能にしない」）であり、
        メタデータの一致判定には影響しない。
        """
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([("2026-07-27", 3.0)], [("2026-07-27", 1 / 3)])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "match"

    def test_soxs_moomoo_only_is_the_core_target_case(self):
        """`SOXS`: moomooにのみ記録がある（yfinanceは無記録）＝第3段階の主目的。"""
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([("2026-03-05", 20.0)], [])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "moomoo_only"
        assert rows[0]["moomoo_ratio"] == 20.0
        assert rows[0]["yfinance_date"] is None

    def test_yfinance_only_when_moomoo_has_no_matching_event(self):
        """yfinance側にのみ記録がある場合（逆方向の非対称）も報告する。"""
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([], [("2020-08-31", 4.0)])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "yfinance_only"

    def test_negative_control_no_events_produces_no_rows(self):
        """陰性対照: 分割の無い銘柄では突き合わせ結果が0件（誤って何かを検出しない）。"""
        from scripts.verify_split_with_moomoo import match_split_events

        assert match_split_events([], []) == []

    def test_ratio_mismatch_when_dates_align_but_ratios_disagree(self):
        """日付は近いが比率が食い違う場合は `ratio_mismatch`（要人間確認）として報告する。"""
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([("2026-07-27", 3.0)], [("2026-07-28", 2.0)])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "ratio_mismatch"

    def test_date_window_default_matches_price_anomaly_precedent(self):
        """日付窓は `price_anomaly.SPLIT_DATE_WINDOW_DAYS`（実測: WLFCの1日ズレ前例）を流用する。

        窓の外（11日ズレ）なら対応づけず `moomoo_only` + `yfinance_only` の
        2件に分かれることを確認する。
        """
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([("2026-07-27", 3.0)], [("2026-08-07", 1 / 3)])
        verdicts = sorted(r["verdict"] for r in rows)
        assert verdicts == ["moomoo_only", "yfinance_only"]

    def test_date_window_one_day_off_still_matches_wlfc_precedent(self):
        """`WLFC`の実測1日ズレ（moomoo記録 vs 上流記録）を再現し、一致すること。"""
        from scripts.verify_split_with_moomoo import match_split_events

        rows = match_split_events([("2026-07-20", 3.0)], [("2026-07-21", 1 / 3)])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "match"


# --- yfinance側の株式配当ノイズ除去 ---------------------------------------------

class TestFilterDiscriminableYfinanceEvents:
    def test_removes_stock_dividend_like_factors(self):
        from scripts.verify_split_with_moomoo import filter_discriminable_yfinance_events

        events = [("2026-01-01", 4.0), ("2026-02-01", 0.99)]
        out = filter_discriminable_yfinance_events(events)
        assert out == [("2026-01-01", 4.0)]

    def test_keeps_all_when_none_are_ambiguous(self):
        from scripts.verify_split_with_moomoo import filter_discriminable_yfinance_events

        events = [("2026-01-01", 4.0), ("2026-02-01", 1 / 3)]
        assert filter_discriminable_yfinance_events(events) == events


# --- moomooの生データからのイベント抽出 -----------------------------------------

class TestExtractMoomooSplitEvents:
    def test_extracts_only_rows_with_non_null_split_ratio(self):
        """`get_rehab()` の生データは配当行(split_ratio=NaN)を含む。分割行だけ抽出する。"""
        import pandas as pd

        from scripts.verify_split_with_moomoo import extract_moomoo_split_events

        df = pd.DataFrame({
            "ex_div_date": ["2020-08-31", "2021-02-12", "2026-08-10"],
            "split_ratio": [0.25, None, None],
        })
        out = extract_moomoo_split_events(df)
        assert out == [("2020-08-31", 0.25)]

    def test_empty_dataframe_returns_empty_list(self):
        import pandas as pd

        from scripts.verify_split_with_moomoo import extract_moomoo_split_events

        df = pd.DataFrame({"ex_div_date": [], "split_ratio": []})
        assert extract_moomoo_split_events(df) == []


# --- 銘柄単位のオーケストレーション ---------------------------------------------

class TestVerifyTicker:
    def test_reports_no_records_either_source(self):
        """両ソースとも記録が無い＝『一致』でも『不一致』でもない第3の状態。取り違えないこと。"""
        from scripts.verify_split_with_moomoo import verify_ticker

        result = verify_ticker("QQQ", moomoo_events=[], yfinance_events=[])
        assert result["no_records_either_source"] is True
        assert result["events"] == []

    def test_reports_events_when_present(self):
        from scripts.verify_split_with_moomoo import verify_ticker

        result = verify_ticker(
            "SOXS", moomoo_events=[("2026-03-05", 20.0)], yfinance_events=[])
        assert result["no_records_either_source"] is False
        assert len(result["events"]) == 1
        assert result["events"][0]["verdict"] == "moomoo_only"

    def test_filters_stock_dividend_noise_from_yfinance_side_before_matching(self):
        """yfinance側の株式配当ノイズ(factor≈1.0)が混じっていても無視して判定する。"""
        from scripts.verify_split_with_moomoo import verify_ticker

        result = verify_ticker(
            "AAPL",
            moomoo_events=[("2020-08-31", 0.25)],
            yfinance_events=[("2020-08-31", 4.0), ("2020-09-01", 0.995)])
        # 0.995 はノイズとして除去され、4.0 とだけ対応づいて match になる
        assert len(result["events"]) == 1
        assert result["events"][0]["verdict"] == "match"


# --- run() のクライアント後始末（2026-09-11実測: close忘れでプロセスがハングした） ------

class _FakeClient:
    """`MoomooClient` の代わりに `run()` へ注入する疑似クライアント。"""

    def __init__(self, rehab_by_ticker=None):
        self.rehab_by_ticker = rehab_by_ticker or {}
        self.closed = False

    def get_rehab(self, ticker):
        import pandas as pd

        return self.rehab_by_ticker.get(
            ticker, pd.DataFrame({"ex_div_date": [], "split_ratio": []}))

    def close(self):
        self.closed = True


class TestRunClosesClient:
    def _patch_io(self, monkeypatch, tmp_path):
        import scripts.verify_split_with_moomoo as mod

        monkeypatch.setattr(mod, "resolve_yfinance_records_path", lambda: "unused")
        monkeypatch.setattr(mod, "load_split_records", lambda path: None)
        monkeypatch.setattr(mod, "split_map", lambda records: {})
        return mod, str(tmp_path)

    def test_closes_a_self_created_client(self, monkeypatch, tmp_path):
        """`client` を渡さなかった場合、`run()` 内で生成したクライアントを必ず閉じる。"""
        import scripts.verify_split_with_moomoo as mod

        mod, report_dir = self._patch_io(monkeypatch, tmp_path)
        created = []

        def fake_ctor():
            c = _FakeClient()
            created.append(c)
            return c

        monkeypatch.setattr(mod, "MoomooClient", fake_ctor)
        mod.run(["AAPL"], report_dir=report_dir)
        assert len(created) == 1
        assert created[0].closed is True

    def test_does_not_close_a_caller_supplied_client(self, monkeypatch, tmp_path):
        """呼び出し側が渡した `client` は `run()` の中で閉じない（使い回す前提のため）。"""
        mod, report_dir = self._patch_io(monkeypatch, tmp_path)
        fake = _FakeClient()
        mod.run(["AAPL"], report_dir=report_dir, client=fake)
        assert fake.closed is False

    def test_closes_self_created_client_even_when_ticker_lookup_fails(self, monkeypatch, tmp_path):
        """途中で例外が起きても close() 漏れが起きない（try/finally で保証する）。"""
        import scripts.verify_split_with_moomoo as mod

        mod, report_dir = self._patch_io(monkeypatch, tmp_path)
        created = []

        def fake_ctor():
            c = _FakeClient()
            created.append(c)

            def boom(_ticker):
                raise RuntimeError("unexpected")
            c.get_rehab = boom
            return c

        monkeypatch.setattr(mod, "MoomooClient", fake_ctor)
        with pytest.raises(RuntimeError):
            mod.run(["AAPL"], report_dir=report_dir)
        assert created[0].closed is True
