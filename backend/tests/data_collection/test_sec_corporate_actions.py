"""SEC 差分検知のテスト（data_collection/sec_corporate_actions.py）

## なぜこの仕組みが要るか

2026年6〜7月に**15件**（改称6・上場廃止9）のコーポレートアクションを2ヶ月見逃し、
「Yahoo 側のデータ不具合」と誤診して復旧に丸一日を費やした。
Yahoo は「データが少ない」としか言わないので、**改称と上場廃止を区別できない**。

CIK と classId は変わらない。マスタ3ファイルの差分を取れば
3リクエストで全銘柄をスクリーニングできる。

## テストは実測ケースで固定する

作り物ではなく、実際に起きた事象（`BK`→`BNY`、`BLD` の Form 15-12G 等）を使う。
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

import datetime as dt  # noqa: E402

from data_collection.sec_corporate_actions import (  # noqa: E402
    DEREGISTRATION_FORMS,
    DEREGISTRATION_MAX_AGE_DAYS,
    MIN_NEW_TICKER_ROWS,
    OLD_TICKER_STALE_DAYS,
    build_master_index,
    classify_candidate,
    detect_candidates,
    evaluate_rename_guard,
    evaluate_retire_guard,
    find_deregistration,
    is_still_filing,
    preferred_ticker,
    resolve_rename_outcome,
    sec_key,
)

TODAY = dt.date(2026, 8, 6)


def _sym(ticker, cik=None, class_id=None, active=1):
    return {"ticker": ticker, "cik": cik, "sec_class_id": class_id, "active": active}


# ---------------------------------------------------------------------------
# sec_key — 突合キーの選択
# ---------------------------------------------------------------------------
def test_class_id_wins_over_cik_for_etfs():
    """ETF は classId で追う。**CIK はトラスト単位で粗すぎる。**

    `RSHO` の CIK 1944285 は Tema ETF Trust の13ファンドを含むため、
    CIK で突合するとトラスト内の別ファンドと取り違える。
    """
    assert sec_key(cik=1944285, sec_class_id="C000123456") == ("class", "C000123456")


def test_cik_is_used_when_there_is_no_class_id():
    assert sec_key(cik=320193, sec_class_id=None) == ("cik", "320193")


def test_no_key_means_untracked():
    """仮想テーマ・指数は SEC に登録主体が無い。追跡対象外。"""
    assert sec_key(cik=None, sec_class_id=None) is None


# ---------------------------------------------------------------------------
# build_master_index / preferred_ticker
# ---------------------------------------------------------------------------
def test_one_cik_can_have_multiple_tickers():
    """**1つの CIK に複数ティッカーがぶら下がる。**

    実測: Bank of New York Mellon（CIK 1390777）は `BNY`（普通株）と
    `BNY-PK`（優先株）の2行を持つ。cik→ticker を1対1の辞書にすると
    後勝ちで `BNY-PK` になり、普通株の `BNY` を「消えた」と誤判定する。
    """
    idx = build_master_index({"BNY": 1390777, "BNY-PK": 1390777}, {})

    assert idx[("cik", "1390777")] == {"BNY", "BNY-PK"}


def test_preferred_ticker_picks_the_common_share():
    """優先株・ワラント（`-` 付き）ではなく普通株を採用する。"""
    assert preferred_ticker({"BNY-PK", "BNY"}) == "BNY"
    assert preferred_ticker({"ABC-WT", "ABC-PA"}) in {"ABC-PA", "ABC-WT"}


def test_master_index_includes_both_key_kinds():
    idx = build_master_index({"AAPL": 320193}, {"SPY": (884394, "S000001", "C000001")})

    assert idx[("cik", "320193")] == {"AAPL"}
    assert idx[("class", "C000001")] == {"SPY"}


# ---------------------------------------------------------------------------
# detect_candidates — マスタとの突合
# ---------------------------------------------------------------------------
def test_unchanged_symbol_is_ok():
    idx = build_master_index({"AAPL": 320193}, {})
    got = detect_candidates([_sym("AAPL", cik=320193)], idx)

    assert [c["status"] for c in got] == ["ok"]


def test_renamed_symbol_is_detected():
    """`BK` → `BNY` の実測ケース（Bank of New York Mellon, CIK 1390777）。

    universe.db は `BK` のまま。**同じ CIK が別ティッカーになっている**ので改称候補。
    """
    idx = build_master_index({"BNY": 1390777, "BNY-PK": 1390777}, {})
    got = detect_candidates([_sym("BK", cik=1390777)], idx)

    assert got[0]["status"] == "rename_candidate"
    assert got[0]["new_ticker"] == "BNY"


def test_symbol_missing_from_master_is_a_delist_candidate():
    """**キーがマスタから消えた＝登録抹消**。

    実測: `BLD` は `company_tickers.json` から削除済みだったが、
    `submissions` は `tickers=['BLD']` を返し続けていた。
    マスタは登録抹消で除去され、submissions には残る。
    """
    got = detect_candidates([_sym("BLD", cik=1633931)], build_master_index({}, {}))

    assert got[0]["status"] == "delist_candidate"


def test_symbol_without_key_is_untracked():
    """仮想テーマ・指数は候補にしない（毎週ノイズになる）。"""
    got = detect_candidates([_sym("_DRON_")], build_master_index({"AAPL": 320193}, {}))

    assert got[0]["status"] == "untracked"


def test_inactive_symbols_are_skipped():
    """退役済みは対象外。**再検知して毎週レポートに出し続けない。**"""
    got = detect_candidates([_sym("OLD", cik=1, active=0)], build_master_index({}, {}))

    assert got == []


def test_ticker_comparison_is_case_insensitive():
    idx = build_master_index({"AAPL": 320193}, {})
    got = detect_candidates([_sym("aapl", cik=320193)], idx)

    assert got[0]["status"] == "ok"


# ---------------------------------------------------------------------------
# find_deregistration — 廃止の確定
# ---------------------------------------------------------------------------
def _subs(name="X", tickers=("X",), forms=(), dates=()):
    return {
        "name": name,
        "tickers": list(tickers),
        "filings": {"recent": {"form": list(forms), "filingDate": list(dates)}},
    }


def test_form_15_12g_confirms_deregistration():
    """`CCRN`（Cross Country Healthcare / Aya による買収）の実測ケース。

    Form 25-NSE 2026-07-21 → Form 15-12G 2026-07-27。以後 10-Q は出ていない。
    """
    s = _subs(forms=["25-NSE", "15-12G"], dates=["2026-07-21", "2026-07-27"])

    assert find_deregistration(s, today=TODAY) == {"form": "15-12G", "date": "2026-07-27"}


def test_form_25_nse_confirms_delisting():
    s = _subs(forms=["25-NSE"], dates=["2026-06-02"])

    assert find_deregistration(s, today=TODAY)["form"] == "25-NSE"


def test_ordinary_filings_are_not_deregistration():
    s = _subs(forms=["10-K", "8-K", "4"], dates=["2026-02-01", "2026-03-01", "2026-04-01"])

    assert find_deregistration(s, today=TODAY) is None


def test_deregistration_form_set_covers_both_routes():
    """登録抹消（Form 15 系）と上場廃止（Form 25 系）の両方を見る。"""
    assert {"15-12B", "15-12G", "25-NSE"} <= DEREGISTRATION_FORMS


def test_stale_form_25_is_not_evidence():
    """`UUP` の実測ケース。**Form 25 は取引所の移管でも提出される。**

    Invesco DB US Dollar Index Bullish Fund は 2008-11-21 に Form 25 を出しているが、
    2026年も 10-Q / 10-K を出し続けている現役 ETF。
    古い提出をそのまま根拠にすると、生きている銘柄を退役させてしまう。
    """
    s = _subs(forms=["25", "10-Q"], dates=["2008-11-21", "2026-05-07"])

    assert find_deregistration(s, today=TODAY) is None


def test_periodic_report_after_deregistration_invalidates_it():
    """`AEP` の実測ケース。**NYSE → Nasdaq の上場移管**。

    2023-08-14 に 25-NSE を出したあとも 10-Q を出し続けている（直近 2026-07-30）。
    登録が本当に抹消されていれば定期報告は止まる。
    移管直後で日付が新しくても、後続の定期報告があれば廃止ではない。
    """
    s = _subs(forms=["25-NSE", "10-Q"], dates=["2026-07-01", "2026-07-30"])

    assert find_deregistration(s, today=TODAY) is None


def test_deregistration_age_boundary():
    old = TODAY - dt.timedelta(days=DEREGISTRATION_MAX_AGE_DAYS + 1)
    fresh = TODAY - dt.timedelta(days=DEREGISTRATION_MAX_AGE_DAYS)

    assert find_deregistration(_subs(forms=["15-12G"], dates=[str(old)]), today=TODAY) is None
    assert find_deregistration(_subs(forms=["15-12G"], dates=[str(fresh)]), today=TODAY)


# ---------------------------------------------------------------------------
# is_still_filing — マスタの取りこぼしと本物の廃止を分ける
# ---------------------------------------------------------------------------
def test_company_still_filing_periodic_reports_is_alive():
    """`AEP` `UUP` の実測ケース。

    `company_tickers.json` は 10,398件しかなく**全登録企業を網羅していない**。
    載っていない＝廃止、としてしまうと現役銘柄を毎週退役候補に挙げ続ける。
    """
    assert is_still_filing(_subs(forms=["10-Q"], dates=["2026-07-30"]), today=TODAY) is True


def test_company_that_stopped_filing_is_not_alive():
    assert is_still_filing(_subs(forms=["10-K"], dates=["2023-02-01"]), today=TODAY) is False


def test_no_filings_at_all_is_not_alive():
    assert is_still_filing(_subs(), today=TODAY) is False


# ---------------------------------------------------------------------------
# classify_candidate — submissions で確定させる
# ---------------------------------------------------------------------------
def test_delist_candidate_with_deregistration_becomes_retire():
    c = {"ticker": "CCRN", "status": "delist_candidate", "new_ticker": None}
    got = classify_candidate(c, _subs(tickers=["CCRN"], forms=["15-12G"],
                                      dates=["2026-07-27"]), today=TODAY)

    assert got["action"] == "retire"
    assert "15-12G" in got["evidence"]


def test_rename_candidate_confirmed_by_submissions():
    c = {"ticker": "SCVL", "status": "rename_candidate", "new_ticker": "SHOE"}
    got = classify_candidate(c, _subs(name="Shoe Station", tickers=["SHOE"]), today=TODAY)

    assert got["action"] == "rename"
    assert got["new_ticker"] == "SHOE"
    assert got["evidence_strength"] == "confirmed"


def test_deregistration_wins_over_rename():
    """**買収では改称と登録抹消が同時に立つ。** その証券は消えるので退役が正しい。

    改称として付け替えると、存在しないティッカーを追い続けることになる。
    """
    c = {"ticker": "BLD", "status": "rename_candidate", "new_ticker": "QXO"}
    got = classify_candidate(c, _subs(tickers=["QXO"], forms=["15-12G"],
                                      dates=["2026-07-13"]), today=TODAY)

    assert got["action"] == "retire"


def test_delist_candidate_still_filing_is_a_master_gap():
    """`AEP` `UUP` の実測ケース。**一括マスタの取りこぼしを廃止と呼ばない。**

    退役候補にも要判断にも入れない。毎週レポートに出続けるノイズになるため、
    情報として数えるだけにする。
    """
    c = {"ticker": "AEP", "status": "delist_candidate", "new_ticker": None}
    got = classify_candidate(c, _subs(tickers=["AEP"], forms=["10-Q"],
                                      dates=["2026-07-30"]), today=TODAY)

    assert got["action"] == "master_gap"


def test_no_evidence_becomes_unknown_not_a_guess():
    """根拠が無ければ**推測しない**。

    `RSHO` / `CORZZ` のように SEC 上は何も起きていない（Yahoo 側の問題）ケースがある。
    黙って退役・改称すると復旧が難しくなるので、人間の判断に回す。
    """
    c = {"ticker": "RSHO", "status": "delist_candidate", "new_ticker": None}
    got = classify_candidate(c, _subs(tickers=["RSHO"]), today=TODAY)

    assert got["action"] == "unknown"


def test_classify_without_submissions_is_unknown():
    """submissions が引けない（404 等）なら確定させない。"""
    c = {"ticker": "X", "status": "delist_candidate", "new_ticker": None}

    assert classify_candidate(c, None, today=TODAY)["action"] == "unknown"


def test_rename_is_kept_even_when_submissions_is_stale():
    """`GAMB` → `GRSD` の実測ケース。**submissions の `tickers` は遅れる。**

    社名は既に GRANDSTAND Ltd に変わり `company_tickers.json` も `GRSD` なのに、
    submissions は `tickers=['GAMB']` を返し続けていた。
    ここで `unknown` に落とすと、一括マスタで捕まえた改称を毎回取りこぼす。
    **弱い根拠として残し、ガード3条件に判断させる。**
    """
    c = {"ticker": "GAMB", "status": "rename_candidate", "new_ticker": "GRSD"}
    got = classify_candidate(c, _subs(name="GRANDSTAND Ltd", tickers=["GAMB"]), today=TODAY)

    assert got["action"] == "rename"
    assert got["new_ticker"] == "GRSD"
    assert got["evidence_strength"] == "master_only"


# ---------------------------------------------------------------------------
# resolve_rename_outcome — ガード結果の解釈
# ---------------------------------------------------------------------------
def test_guard_pass_means_apply():
    assert resolve_rename_outcome(True, new_ticker_rows=8404,
                                  old_last_date=None,
                                  new_last_date="2026-08-05") == "rename"


def test_both_securities_trading_is_coexistence_not_a_rename():
    """`VWDRY` / `VWSYF` の実測ケース。**1つの CIK に別々の証券がぶら下がる。**

    Vestas Wind Systems（CIK 1330306）の ADR と原株。どちらも取引されている。
    改称ではないので、要判断リストに毎週載せても人間は何もできない。
    情報として分離し、レポートのノイズを増やさない。
    """
    got = resolve_rename_outcome(False, new_ticker_rows=501,
                                 old_last_date="2026-08-05", new_last_date="2026-08-04")

    assert got == "coexisting"


def test_dead_old_ticker_with_empty_new_ticker_needs_a_human():
    """旧が死んで新にも履歴が無い＝付け替え先が無い。人間の判断が要る。"""
    got = resolve_rename_outcome(False, new_ticker_rows=0,
                                 old_last_date=None, new_last_date=None)

    assert got == "pending"


# ---------------------------------------------------------------------------
# evaluate_rename_guard — 自動適用の3条件
# ---------------------------------------------------------------------------
def test_guard_passes_when_all_three_conditions_hold():
    """`SCVL`→`SHOE` の実測ケース（新ティッカーに 8,404行・旧は応答なし）。"""
    ok, reasons = evaluate_rename_guard(key_matched=True, new_ticker_rows=8404,
                                        old_last_date=None, new_last_date="2026-08-05")

    assert ok is True
    assert reasons == []


def test_guard_passes_for_a_recent_rename_with_leftover_days():
    """`GAMB`→`GRSD` の実測ケース。**改称直後は旧ティッカーに数日分の残骸がある。**

    GAMB は 2026-07-29 まで取引され、GRSD は 08-05 まで動いている。
    「直近1ヶ月の行数」で見ると GAMB は6行あり、行数基準では
    「まだ生きている」と誤判定して改称を取りこぼした。**最終取引日の差で見る。**
    """
    ok, reasons = evaluate_rename_guard(key_matched=True, new_ticker_rows=502,
                                        old_last_date="2026-07-29",
                                        new_last_date="2026-08-05")

    assert ok is True, reasons


def test_guard_blocks_when_new_ticker_has_no_history():
    """**空振りのティッカーへ付け替えて履歴を失う事故**を防ぐ。

    改称を適用すると以後その銘柄は新ティッカーで取得される。
    新ティッカーにデータが無ければ、価格履歴が伸びなくなる。
    """
    ok, reasons = evaluate_rename_guard(key_matched=True, new_ticker_rows=0,
                                        old_last_date=None, new_last_date=None)

    assert ok is False
    assert any("履歴" in r for r in reasons)


def test_guard_blocks_when_old_ticker_is_still_trading():
    """`VWDRY` / `VWSYF` の実測ケース。両方が同じ日まで取引されている。

    SEC のマスタは優先株・ADR などを同じ CIK に載せるため、
    キー一致だけでは付け替えの根拠にならない。
    """
    ok, reasons = evaluate_rename_guard(key_matched=True, new_ticker_rows=501,
                                        old_last_date="2026-08-05",
                                        new_last_date="2026-08-04")

    assert ok is False
    assert any("旧" in r for r in reasons)


def test_guard_blocks_when_key_does_not_match():
    ok, reasons = evaluate_rename_guard(key_matched=False, new_ticker_rows=5000,
                                        old_last_date=None, new_last_date="2026-08-05")

    assert ok is False


def test_guard_reports_every_failed_condition():
    """1条件だけ報告して終わらせない（人間が全体像を掴めるように）。"""
    ok, reasons = evaluate_rename_guard(key_matched=False, new_ticker_rows=0,
                                        old_last_date="2026-08-05",
                                        new_last_date="2026-08-05")

    assert ok is False
    assert len(reasons) == 3


@pytest.mark.parametrize("rows,expected", [
    (MIN_NEW_TICKER_ROWS - 1, False),
    (MIN_NEW_TICKER_ROWS, True),
])
def test_new_ticker_history_threshold_boundary(rows, expected):
    ok, _ = evaluate_rename_guard(key_matched=True, new_ticker_rows=rows,
                                  old_last_date=None, new_last_date="2026-08-05")
    assert ok is expected


@pytest.mark.parametrize("lag,expected", [
    (OLD_TICKER_STALE_DAYS - 1, False),
    (OLD_TICKER_STALE_DAYS, True),
])
def test_old_ticker_staleness_boundary(lag, expected):
    """旧の最終日が新より何日古ければ「死んだ」とみなすか。"""
    new = dt.date(2026, 8, 5)
    ok, _ = evaluate_rename_guard(key_matched=True, new_ticker_rows=1000,
                                  old_last_date=str(new - dt.timedelta(days=lag)),
                                  new_last_date=str(new))
    assert ok is expected


def test_staleness_is_measured_against_the_new_ticker_not_today():
    """**「今日から何日前か」で測らない。**

    連休・祝日・取得タイミングでぶれる。同じ市場の2銘柄を比べればその影響が消える。
    両方が古い（＝単に取得が遅れているだけ）ときに改称扱いしないことを固定する。
    """
    ok, _ = evaluate_rename_guard(key_matched=True, new_ticker_rows=1000,
                                  old_last_date="2026-06-01", new_last_date="2026-06-01")

    assert ok is False


# ---------------------------------------------------------------------------
# evaluate_retire_guard — 退役の自動適用
# ---------------------------------------------------------------------------
def test_ordinary_symbol_is_retired_automatically():
    """退役は根拠が硬く可逆（active=1 に戻せば価格履歴ごと復元できる）ので自動でよい。"""
    ok, reasons = evaluate_retire_guard(theme_child_count=0)

    assert ok is True and reasons == []


def test_theme_parent_is_not_retired_automatically():
    """**テーマの親を落とすと構成銘柄の紐付けが宙に浮きテーマの合成値が壊れる。**

    影響が本人に閉じないので人間の判断に回す
    （`retire_stale_symbols.py` の保護と揃えている）。
    """
    ok, reasons = evaluate_retire_guard(theme_child_count=12)

    assert ok is False
    assert "12" in reasons[0]
