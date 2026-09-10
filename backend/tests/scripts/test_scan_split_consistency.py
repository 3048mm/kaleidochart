"""分割調整の自己矛盾スキャン（`scan_split_consistency.py`）のテスト。

## 何のための機能か

2026-09-04、価格履歴の充足で接合部に段差が出た5銘柄を「上流が正・手元が古い」と
判断して上流へ合わせたところ、**MNST は上流側が壊れており、正しかった手元データを
壊した**。既存の検査は3つとも素通りした:

- `resync_price_scale.py` の `verify_after()` は「上流と一致するか」しか見ない
  → **上流の誤りは原理的に検出不能**
- `scan_price_anomalies.py` は系列内の段差を見る
  → 全期間が一律に同じ倍率でズレていると**段差が出ない**
- `adjust_symbol_split.py` の接合部検算は申告 `--factor` との照合
  → 申告値自体は検証しない

MNST は yfinance が分割記録を**持っていた**（2026-08-11 ×2）のに調整済み系列へ
適用していなかった。つまり**同じ上流の中で metadata と時系列が矛盾していた**。
これは外部ソース無しで検出できる。

## この機能が絶対に守るべきこと

1. **自動修正しない。報告だけ。** 事故の原因は「自動的に上流へ合わせた」こと
2. **どちらが正しいかを決めない。** 両方の値を並べ、判断は人間に残す
3. **誤検出を出さない。** 警告が常態化すると読まれなくなる。特に配当銘柄の
   乖離は正常（`auto_adjust=True` で配当調整も入るため）
4. **判定不能を「正常」に丸めない。** 分割日にデータが無い等は素直に不明とする
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


def _closes(pairs):
    """(date, close) から上流の終値系列を作る。"""
    return pd.DataFrame(pairs, columns=["date", "close"])


class TestDetectUnappliedSplit:
    """検査A: 上流が記録している分割を、上流自身が適用できているか。"""

    def test_no_jump_means_the_split_was_applied(self):
        """遡及適用済みなら分割日に段差は出ない。"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 45.7), ("2026-08-11", 45.5)])
        got = detect_unapplied_split(px, "2026-08-11", 2.0)
        assert got["verdict"] == "applied"

    def test_jump_matching_the_split_ratio_means_it_was_not_applied(self):
        """段差が 1/factor なら、記録はあるのに適用されていない（MNST 型）。"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 90.0), ("2026-08-11", 45.0)])
        got = detect_unapplied_split(px, "2026-08-11", 2.0)
        assert got["verdict"] == "unapplied"
        assert got["jump"] == pytest.approx(0.5)

    def test_reverse_split_not_applied_is_detected(self):
        """併合（factor < 1）でも同じく検出する。1:10 併合なら段差は ×10。"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 0.5), ("2026-08-11", 5.0)])
        got = detect_unapplied_split(px, "2026-08-11", 0.1)
        assert got["verdict"] == "unapplied"

    def test_ambiguous_jump_is_not_reported_as_broken(self):
        """中途半端な段差は実際の値動きかもしれない。**誤検出しない。**"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 100.0), ("2026-08-11", 72.0)])
        got = detect_unapplied_split(px, "2026-08-11", 2.0)
        assert got["verdict"] == "unknown"

    def test_missing_data_at_the_split_date_is_unknown_not_an_error(self):
        """分割日が直近すぎてデータが無いことがある（APH 2026-09-03 の実例）。

        例外を投げるとスキャン全体が止まる。**判定不能として先へ進む。**
        """
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 45.0)])
        got = detect_unapplied_split(px, "2026-09-03", 2.0)
        assert got["verdict"] == "unknown"

    def test_zero_price_does_not_raise(self):
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 0.0), ("2026-08-11", 45.0)])
        got = detect_unapplied_split(px, "2026-08-11", 2.0)
        assert got["verdict"] == "unknown"

    @pytest.mark.parametrize("factor", [1.006, 1.01, 1.03, 1.05, 1.061])
    def test_tiny_stock_dividends_are_not_reported_as_unapplied(self, factor):
        """株式配当（factor ≈ 1.0x）は段差では判定できない。**誤検出しない。**

        2026-09-09 の全ユニバース実測で、検出18件のうち**16件がこれ**だった
        （`SCCO` 7件 / `TR` 2件 / `METC` 2件 / `SNFCA` 2件 / `HON` / `J` / `LEN`）。
        いずれも 1.006〜1.061 の株式配当で、分割ではない。

        原因は判定順序。`factor=1.01` なら「未適用」の期待値 `1/1.01=0.990` と
        「適用済み」の `1.0` が許容幅の中で**重なり**、先に評価する「未適用」が
        常に当たっていた。**2つの仮説が識別できない領域では判定を放棄する。**
        """
        from scripts.scan_split_consistency import detect_unapplied_split

        # 段差なし（＝適用済み）でも、段差が小さくても、未適用と言ってはいけない
        for after in (100.0, 99.0, 101.0):
            px = _closes([("2026-08-10", 100.0), ("2026-08-11", after)])
            got = detect_unapplied_split(px, "2026-08-11", factor)
            assert got["verdict"] != "unapplied", (
                f"factor={factor} after={after} を未適用と誤判定した")

    def test_a_real_split_is_still_detected_after_the_fix(self):
        """株式配当を除外する修正で、本物の分割まで見逃さないこと。"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 90.0), ("2026-08-11", 45.0)])
        assert detect_unapplied_split(px, "2026-08-11", 2.0)["verdict"] == "unapplied"

    @pytest.mark.parametrize("factor", [1.25, 1.5, 2.0, 3.0, 0.5, 0.1])
    def test_discriminable_factors_are_still_judged(self, factor):
        """識別可能な比では、段差なし＝適用済みと判定できること。"""
        from scripts.scan_split_consistency import detect_unapplied_split

        px = _closes([("2026-08-10", 100.0), ("2026-08-11", 100.0)])
        assert detect_unapplied_split(px, "2026-08-11", factor)["verdict"] == "applied"


class TestIsWithinOurData:
    """保有期間の外にある分割は、こちらが手を出せないので報告しない。

    2026-09-10 の全期間スキャンで `CHT`（2008-10-15 ×1.21）と
    `FWONA`（2016-04-18 ×1.424）が検出されたが、**手元の保有期間は
    2017-01-03 以降**で、どちらも範囲外だった。上流の古い履歴が壊れている
    のは事実だが、こちらのデータには一切影響しない。

    報告し続けると対応不能な警告が常態化し、**レポートが読まれなくなる**。
    """

    def test_split_inside_our_range_is_reported(self):
        from scripts.scan_split_consistency import is_within_our_data

        assert is_within_our_data("2026-08-24", "2017-01-03", "2026-09-03") is True

    def test_split_before_our_first_row_is_skipped(self):
        from scripts.scan_split_consistency import is_within_our_data

        assert is_within_our_data("2008-10-15", "2017-01-03", "2026-09-03") is False

    def test_split_after_our_last_row_is_skipped(self):
        from scripts.scan_split_consistency import is_within_our_data

        assert is_within_our_data("2026-12-01", "2017-01-03", "2026-09-03") is False

    def test_boundaries_are_inclusive(self):
        """初日・最終日の分割は保有期間の中。境界で落とさない。"""
        from scripts.scan_split_consistency import is_within_our_data

        assert is_within_our_data("2017-01-03", "2017-01-03", "2026-09-03") is True
        assert is_within_our_data("2026-09-03", "2017-01-03", "2026-09-03") is True

    def test_missing_range_does_not_skip(self):
        """保有期間が分からないなら**スキップしない**（見落としより誤検出を選ぶ）。"""
        from scripts.scan_split_consistency import is_within_our_data

        assert is_within_our_data("2026-08-24", None, None) is True


class TestCompareScale:
    """検査B: 手元と上流のスケールが食い違っていないか。"""

    def test_identical_series_reports_no_divergence(self):
        from scripts.scan_split_consistency import compare_scale

        d = ["2026-08-0%d" % i for i in range(1, 6)]
        mine = _closes(list(zip(d, [10.0] * 5)))
        up = _closes(list(zip(d, [10.0] * 5)))
        got = compare_scale(mine, up, has_dividend=False)
        assert got["verdict"] == "ok"
        assert got["ratio"] == pytest.approx(1.0)

    def test_ratio_equal_to_a_split_factor_without_dividend_is_a_strong_warning(self):
        """無配当なのに比が分割比と一致 → 分割の取りこぼしが濃厚（MNST 型）。"""
        from scripts.scan_split_consistency import compare_scale

        d = ["2026-08-0%d" % i for i in range(1, 6)]
        mine = _closes(list(zip(d, [10.0] * 5)))
        up = _closes(list(zip(d, [20.0] * 5)))
        got = compare_scale(mine, up, has_dividend=False)
        assert got["verdict"] == "split_suspect"
        assert got["ratio"] == pytest.approx(0.5)

    def test_small_divergence_with_dividends_is_not_warned(self):
        """配当銘柄の緩やかな乖離は配当調整で説明できる。**警告しない。**

        IEP は比 1.07 で、分配金から算出した係数と整合していた（2026-09-04）。
        ここで警告を出すと配当銘柄が軒並み鳴り、レポートが読まれなくなる。
        """
        from scripts.scan_split_consistency import compare_scale

        d = ["2026-08-0%d" % i for i in range(1, 6)]
        mine = _closes(list(zip(d, [10.7] * 5)))
        up = _closes(list(zip(d, [10.0] * 5)))
        got = compare_scale(mine, up, has_dividend=True)
        assert got["verdict"] == "ok"

    def test_large_divergence_with_dividends_is_still_reported(self):
        """配当があっても比が分割比ちょうどなら見逃さない（APH 型）。"""
        from scripts.scan_split_consistency import compare_scale

        d = ["2026-08-0%d" % i for i in range(1, 6)]
        mine = _closes(list(zip(d, [20.0] * 5)))
        up = _closes(list(zip(d, [10.0] * 5)))
        got = compare_scale(mine, up, has_dividend=True)
        assert got["verdict"] == "split_suspect"

    def test_large_non_split_divergence_is_divergent_not_ok(self):
        """分割比ではないが乖離が大きい場合（VISN 型・比 1.75）。

        「分割ではない」を理由に `ok` へ丸めてはいけない。VISN は実際に
        手元が古く、修正が必要だった。**分割比に一致しないことは、
        正常であることを意味しない。**
        """
        from scripts.scan_split_consistency import compare_scale

        d = ["2026-08-0%d" % i for i in range(1, 6)]
        mine = _closes(list(zip(d, [17.54] * 5)))
        up = _closes(list(zip(d, [10.0] * 5)))
        got = compare_scale(mine, up, has_dividend=True)
        assert got["verdict"] == "divergent"

    def test_no_overlap_is_unknown(self):
        """重なる日付が無ければ判定できない。0 や 1.0 に丸めない。"""
        from scripts.scan_split_consistency import compare_scale

        mine = _closes([("2026-08-01", 10.0)])
        up = _closes([("2026-07-01", 10.0)])
        got = compare_scale(mine, up, has_dividend=False)
        assert got["verdict"] == "unknown"


class TestIsSplitLikeRatio:
    """比が「よくある分割比」かどうかの判定。検査Bの中核。"""

    @pytest.mark.parametrize("ratio", [0.5, 2.0, 1.5, 0.6667, 3.0, 0.3333, 0.25, 4.0])
    def test_common_split_ratios_are_recognized(self, ratio):
        from scripts.scan_split_consistency import is_split_like_ratio

        assert is_split_like_ratio(ratio) is True

    @pytest.mark.parametrize("ratio", [1.0, 1.07, 0.93, 1.12, 0.88])
    def test_dividend_sized_divergences_are_not_split_like(self, ratio):
        """配当調整ぶんの乖離を分割と誤認しない。"""
        from scripts.scan_split_consistency import is_split_like_ratio

        assert is_split_like_ratio(ratio) is False


class TestRegressionOf20260904:
    """2026-09-04 の5銘柄を再現する。**これが本丸。**

    当時の上流の状態は再現できないので、記録した実測値を固定値で埋め込む。
    「検出できた」だけでなく **IEP を誤検出しないこと**が同じくらい重要。
    """

    # 比が分割比に一致するものは split_suspect、しないが乖離が大きいものは
    # divergent。**IEP だけが ok**（比 1.07 は分配金で説明できる）。
    CASES = [
        # (ticker, 手元, 上流, 配当の有無, 期待, 当時の実際)
        ("MNST", 13.90, 27.81, False, "split_suspect",
         "上流が2026-08-11の×2分割を未適用。手元が正しかった"),
        ("APH", 19.45, 9.70, True, "split_suspect",
         "手元が2024-06-12の×2分割を取りこぼし。上流が正しかった"),
        ("RUSHA", 16.27, 10.85, True, "split_suspect",
         "比 1.5 = 分割比。手元が古かった"),
        ("VISN", 19.26, 10.98, True, "divergent",
         "比 1.75 は分割比ではないが、2026年の特別分配で手元が古かった"),
        ("IEP", 14.88, 13.88, True, "ok",
         "比 1.07。分配金から算出した係数と整合。**誤検出してはいけない**"),
    ]

    @pytest.mark.parametrize("ticker,mine_v,up_v,has_div,expected,note", CASES)
    def test_reproduces_the_20260904_verdicts(self, ticker, mine_v, up_v,
                                              has_div, expected, note):
        from scripts.scan_split_consistency import compare_scale

        d = ["2018-04-0%d" % i for i in range(2, 7)]
        mine = _closes(list(zip(d, [mine_v] * 5)))
        up = _closes(list(zip(d, [up_v] * 5)))
        got = compare_scale(mine, up, has_dividend=has_div)
        assert got["verdict"] == expected, (
            f"{ticker}: 比 {mine_v / up_v:.4f} を {got['verdict']} と判定した"
            f"（期待 {expected}）／ 当時の実際: {note}")

    def test_every_problem_symbol_is_flagged_and_the_healthy_one_is_not(self):
        """4件を拾い、1件を拾わないこと。**誤検出率が本丸。**"""
        from scripts.scan_split_consistency import compare_scale

        d = ["2018-04-0%d" % i for i in range(2, 7)]
        flagged = []
        for ticker, mine_v, up_v, has_div, _, _ in self.CASES:
            mine = _closes(list(zip(d, [mine_v] * 5)))
            up = _closes(list(zip(d, [up_v] * 5)))
            if compare_scale(mine, up, has_dividend=has_div)["verdict"] != "ok":
                flagged.append(ticker)
        assert flagged == ["MNST", "APH", "RUSHA", "VISN"], (
            f"検出された銘柄が想定と違う: {flagged}")


class TestShouldSaveRecords:
    """`--tickers` で絞った実行が全ユニバースの記録を潰さないこと。

    docstring も SKILL も `--tickers MNST,APH` をスポット確認の書式として
    案内している。それで既定パスへ書くと**全ユニバースの記録が数銘柄ぶんに
    置き換わり**、以後の価格アノマリー分類が分割メタデータ無しに逆戻りする
    （2026-09-11 のレビューで発覚）。
    """

    def test_full_universe_run_saves(self):
        from scripts.scan_split_consistency import should_save_records
        assert should_save_records(None, None) is True

    def test_ticker_filtered_run_does_not_overwrite_default(self):
        from scripts.scan_split_consistency import should_save_records
        assert should_save_records(["MNST", "APH"], None) is False

    def test_explicit_output_is_honoured_even_when_filtered(self):
        """書き先を明示しているなら、分かって指定しているので保存する。"""
        from scripts.scan_split_consistency import should_save_records
        assert should_save_records(["MNST"], "tmp/spot.json") is True
