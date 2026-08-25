"""補正台帳の読み込み・検証のテスト（pipeline/price_corrections.py）。

## なぜ台帳が要るか

上流（Yahoo）が調整しなかった分割や、`ffill` が捏造した行の補正は
**人が判断して適用する**。ところが全期間再構築（`run/tool/refresh_All.bat`）は
Yahoo から取り直すので、**適用した補正はすべて巻き戻る**
（`parquet-data-quality` SKILL §9 の項目4「`truncate_symbol_history.py` を再適用」と同じ性質）。

2026-08-25 時点で、この知識は git のコミットログにしか無かった。
再構築後に「BYND は 2026-08-13 より前を ×30」と知っている人がいなくなる。

> [!IMPORTANT]
> **これは 2026-08-06 に「不要」と判断した補正テーブルとは別物。**
> あちらは「上流の分割記録から**自動で補正値を推定して当てる**仕組み」で、
> 推定値を本番に書き込むリスクから見送った
> （`doc/completed/split_anomaly_noise_reduction_plan.md` §8）。
> こちらは**人が適用済みの補正を記録して再生するだけ**で、新しい値を推定しない。
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

from pipeline.price_corrections import (  # noqa: E402
    KINDS,
    CorrectionError,
    load_corrections,
    parse_corrections,
)

SPLIT = {
    "ticker": "BYND", "kind": "split", "before": "2026-08-13", "factor": 30.0,
    "reason": "1:30 併合 (2026-08-14 ET)", "applied": "2026-08-25",
}
FABRICATED = {
    "ticker": "AVB", "kind": "fabricated_rows",
    "from": "2026-08-15", "to": "2026-08-23",
    "reason": "上流欠損を ffill が埋めた行", "applied": "2026-08-25",
}


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------
def test_parses_a_split_entry():
    (c,) = parse_corrections([SPLIT])
    assert c.ticker == "BYND"
    assert c.kind == "split"
    assert c.before == "2026-08-13"
    assert c.factor == 30.0
    assert c.applied == "2026-08-25"


def test_parses_a_fabricated_rows_entry():
    (c,) = parse_corrections([FABRICATED])
    assert c.kind == "fabricated_rows"
    assert c.date_from == "2026-08-15"
    assert c.date_to == "2026-08-23"


def test_file_order_is_preserved():
    """適用順は台帳の記載順。**並べ替えない**（依存関係を人が決められるように）。"""
    out = parse_corrections([FABRICATED, SPLIT])
    assert [c.ticker for c in out] == ["AVB", "BYND"]


def test_reverse_split_and_forward_split_are_both_allowed():
    fwd = dict(SPLIT, ticker="MNST", factor=0.5, before="2026-08-10")
    (c,) = parse_corrections([fwd])
    assert c.factor == 0.5


def test_known_kinds_are_fixed():
    """種別を増やすときは再適用スクリプト側の分岐も足すこと。"""
    assert KINDS == ("split", "fabricated_rows")


# ---------------------------------------------------------------------------
# 検証 — **理由の記録を必須にする**
# ---------------------------------------------------------------------------
def test_reason_is_required():
    """台帳の存在意義は「なぜ直したか」を残すこと。理由なしは受け付けない。"""
    bad = {k: v for k, v in SPLIT.items() if k != "reason"}
    with pytest.raises(CorrectionError, match="reason"):
        parse_corrections([bad])


def test_empty_reason_is_rejected():
    with pytest.raises(CorrectionError, match="reason"):
        parse_corrections([dict(SPLIT, reason="   ")])


def test_unknown_kind_is_rejected():
    with pytest.raises(CorrectionError, match="kind"):
        parse_corrections([dict(SPLIT, kind="magic")])


def test_ticker_is_required():
    with pytest.raises(CorrectionError, match="ticker"):
        parse_corrections([dict(SPLIT, ticker="")])


# ---------------------------------------------------------------------------
# 検証 — 二重適用・取り違えを台帳の段階で止める
# ---------------------------------------------------------------------------
def test_factor_of_one_is_rejected():
    """`factor = 1` は何もしないので、書いた人の意図が別にあるはず。"""
    with pytest.raises(CorrectionError, match="factor"):
        parse_corrections([dict(SPLIT, factor=1.0)])


def test_non_positive_factor_is_rejected():
    for f in (0.0, -30.0):
        with pytest.raises(CorrectionError, match="factor"):
            parse_corrections([dict(SPLIT, factor=f)])


def test_split_requires_before():
    bad = {k: v for k, v in SPLIT.items() if k != "before"}
    with pytest.raises(CorrectionError, match="before"):
        parse_corrections([bad])


def test_malformed_date_is_rejected():
    with pytest.raises(CorrectionError, match="日付"):
        parse_corrections([dict(SPLIT, before="2026/08/13")])


def test_fabricated_rows_requires_a_range():
    bad = {k: v for k, v in FABRICATED.items() if k != "to"}
    with pytest.raises(CorrectionError, match="to"):
        parse_corrections([bad])


def test_reversed_range_is_rejected():
    with pytest.raises(CorrectionError, match="範囲"):
        parse_corrections([dict(FABRICATED, **{"from": "2026-08-23", "to": "2026-08-15"})])


def test_duplicate_entries_are_rejected():
    """同じ銘柄・同じ境界を2回書くと、再適用で二重にかかる危険がある。"""
    with pytest.raises(CorrectionError, match="重複"):
        parse_corrections([SPLIT, dict(SPLIT)])


def test_same_ticker_with_different_boundaries_is_allowed():
    """同じ銘柄が複数回分割することはある（`STKH` は2年で4回）。"""
    out = parse_corrections([SPLIT, dict(SPLIT, before="2025-04-28", factor=5.0)])
    assert len(out) == 2


# ---------------------------------------------------------------------------
# ファイル読み込み
# ---------------------------------------------------------------------------
def test_missing_file_returns_empty(tmp_path):
    """台帳がまだ無い環境でも再適用スクリプトが落ちないこと。"""
    assert load_corrections(str(tmp_path / "nope.toml")) == []


def test_loads_from_toml(tmp_path):
    p = tmp_path / "corrections.toml"
    p.write_text(
        '[[correction]]\n'
        'ticker = "BYND"\n'
        'kind = "split"\n'
        'before = "2026-08-13"\n'
        'factor = 30.0\n'
        'reason = "1:30 併合"\n',
        encoding="utf-8")
    (c,) = load_corrections(str(p))
    assert c.ticker == "BYND" and c.factor == 30.0


def test_empty_toml_returns_empty(tmp_path):
    p = tmp_path / "empty.toml"
    p.write_text("", encoding="utf-8")
    assert load_corrections(str(p)) == []


def test_error_message_names_the_entry(tmp_path):
    """どの行が悪いのか分からないと直せない。"""
    p = tmp_path / "bad.toml"
    p.write_text(
        '[[correction]]\nticker = "ZZZ"\nkind = "split"\nbefore = "2026-01-01"\n'
        'factor = 2.0\nreason = "ok"\n\n'
        '[[correction]]\nticker = "WWW"\nkind = "split"\nfactor = 2.0\nreason = "ng"\n',
        encoding="utf-8")
    with pytest.raises(CorrectionError, match="WWW"):
        load_corrections(str(p))
