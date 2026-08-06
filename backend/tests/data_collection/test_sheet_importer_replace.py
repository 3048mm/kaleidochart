"""スプレッドシート import の replace モードが手動データを消さないことの検証。

## 背景

旧実装は replace モードで `symbols_master` / `theme_members` を**無条件に全 DELETE** していた。
そのため import のたびに以下が消えていた:

- パイプラインが登録した銘柄（`source='pipeline'`。実例: `GBTC`）
- 手動追加・手動改称の結果

痕跡として `ticker_history` に `_DRONE_`（旧 `ARKX`）が残っているのに
`symbols_master` に実体が無い、という状態が確認されている。

T1 のソースがスプレッドシートから universe.db へ移った今、universe.db は
**ユーザー資産**（`agent_execution_rules.md` §10.1）であり、消えた手動編集は再生成できない。
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_collection.sheet_importer import (  # noqa: E402
    SHEET_SOURCE,
    _is_sheet_owned,
    execute_import_diff,
    preview_import_diff,
)
from db.models_universe import BaseUniverse, SymbolMaster, ThemeMember  # noqa: E402


@pytest.fixture
def udb(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'universe_test.db'}")
    BaseUniverse.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _sym(ticker, source=SHEET_SOURCE, category="個別"):
    return SymbolMaster(ticker=ticker, exchange="NASDAQ", name=f"{ticker} Inc.",
                        category=category, industry="", theme_type=None,
                        sector_etf=None, active=1, source=source)


def _parsed(ticker, category="個別"):
    return {"ticker": ticker, "exchange": "NASDAQ", "name": f"{ticker} Inc.",
            "category": category, "industry": "", "theme_type": None, "sector_etf": None}


def _seed(db, n_sheet=12):
    """シート由来 n 件 + 保護対象3件（pipeline / 手動 / source NULL）"""
    db.add_all([_sym(f"SH{i:02d}") for i in range(n_sheet)])
    db.add(_sym("GBTC", source="pipeline", category="指標"))
    db.add(_sym("MANUAL1", source="manual"))
    db.add(_sym("UNKNOWN1", source=None))
    db.add_all([
        ThemeMember(theme_ticker="TH1", member_ticker="SH00", weight=1.0, source=SHEET_SOURCE),
        ThemeMember(theme_ticker="TH1", member_ticker="MANUAL1", weight=1.0, source="manual"),
    ])
    db.commit()


# ---------------------------------------------------------------------------
# _is_sheet_owned
# ---------------------------------------------------------------------------
def test_only_sheet_source_is_owned():
    assert _is_sheet_owned(_sym("A", source=SHEET_SOURCE)) is True
    assert _is_sheet_owned(_sym("B", source="pipeline")) is False
    assert _is_sheet_owned(_sym("C", source="manual")) is False


def test_null_source_is_protected():
    """出所不明の行は保護側に倒す。消えた手動編集は再生成できないため。"""
    assert _is_sheet_owned(_sym("D", source=None)) is False


# ---------------------------------------------------------------------------
# execute_import_diff (replace)
# ---------------------------------------------------------------------------
def test_replace_keeps_non_sheet_symbols(udb):
    """replace 後も pipeline / 手動 / NULL 由来の銘柄が残ること"""
    _seed(udb)
    parsed = [_parsed(f"SH{i:02d}") for i in range(12)]

    result = execute_import_diff(udb, parsed, [], mode="replace")
    udb.commit()

    remaining = {s.ticker for s in udb.query(SymbolMaster).all()}
    assert {"GBTC", "MANUAL1", "UNKNOWN1"} <= remaining, "保護対象が消えている"
    assert result["protected"] == ["GBTC", "MANUAL1", "UNKNOWN1"]


def test_replace_still_removes_stale_sheet_rows(udb):
    """保護を入れても、シートから消えた銘柄はちゃんと削除されること（保護しすぎない）"""
    _seed(udb)
    # SH00 だけシートから消えた想定
    parsed = [_parsed(f"SH{i:02d}") for i in range(1, 12)]

    execute_import_diff(udb, parsed, [], mode="replace")
    udb.commit()

    remaining = {s.ticker for s in udb.query(SymbolMaster).all()}
    assert "SH00" not in remaining, "シートから消えた銘柄が残っている"
    assert "SH11" in remaining


def test_replace_keeps_non_sheet_theme_members(udb):
    """theme_members も同じ保護が効くこと"""
    _seed(udb)
    parsed = [_parsed(f"SH{i:02d}") for i in range(12)]

    execute_import_diff(udb, parsed, [], mode="replace")
    udb.commit()

    members = {(m.theme_ticker, m.member_ticker) for m in udb.query(ThemeMember).all()}
    assert ("TH1", "MANUAL1") in members, "手動のテーマ構成が消えている"
    assert ("TH1", "SH00") not in members, "シート由来の構成が残っている"


def test_replace_still_guards_against_tiny_input(udb):
    """既存の誤消去ガード（10件未満で中止）が壊れていないこと"""
    _seed(udb)
    with pytest.raises(ValueError, match="極めて少ない"):
        execute_import_diff(udb, [_parsed("SH00")], [], mode="replace")


# ---------------------------------------------------------------------------
# preview_import_diff
# ---------------------------------------------------------------------------
def test_preview_does_not_list_protected_symbols_as_deleted(udb):
    """プレビューと実行で対象がズレないこと。

    ズレていると「消えないはずの行が消えた」ことに人間が気づけない。
    """
    _seed(udb)
    parsed = [_parsed(f"SH{i:02d}") for i in range(12)]

    preview = preview_import_diff(udb, parsed, [], mode="replace")
    deleted = {d["ticker"] for d in preview["deleted"]}

    assert not ({"GBTC", "MANUAL1", "UNKNOWN1"} & deleted), "保護対象が削除予定に出ている"


# ---------------------------------------------------------------------------
# replace が既存行を破壊しないこと（DELETE+INSERT → upsert への変更の回帰）
# ---------------------------------------------------------------------------
def test_replace_preserves_id_and_sec_keys(udb):
    """**replace はシート由来の行も更新にとどめ、id と SEC キーを保つこと。**

    旧実装は「source=spreadsheet_url を全 DELETE → 全 INSERT」だったため、
    importer が知らない列（`cik` / `sec_class_id` / `sec_checked_at`）が
    import のたびに NULL に戻り、`symbols_master.id` も振り直されていた。

    SEC キーが消えると、その銘柄は**改称・上場廃止の追跡対象外**に静かに落ちる。
    2026年6〜7月に15件のコーポレートアクションを2ヶ月見逃したのと同じ状態に戻る。
    """
    _seed(udb)
    target = udb.query(SymbolMaster).filter(SymbolMaster.ticker == "SH03").first()
    target.cik = 320193
    target.sec_class_id = "C000000001"
    udb.commit()
    before_id = target.id

    execute_import_diff(udb, [_parsed(f"SH{i:02d}") for i in range(12)], [], mode="replace")
    udb.commit()

    after = udb.query(SymbolMaster).filter(SymbolMaster.ticker == "SH03").first()
    assert after is not None, "シート掲載中の銘柄が消えた"
    assert after.id == before_id, "symbols_master.id が振り直された"
    assert after.cik == 320193, "cik が破壊された"
    assert after.sec_class_id == "C000000001", "sec_class_id が破壊された"


def test_replace_still_updates_editable_fields(udb):
    """id を保つために更新まで止めてはいけない（シートの編集が反映されること）。"""
    _seed(udb)
    parsed = [_parsed(f"SH{i:02d}") for i in range(12)]
    parsed[3]["name"] = "Renamed Corp"
    parsed[3]["category"] = "テーマ"

    execute_import_diff(udb, parsed, [], mode="replace")
    udb.commit()

    after = udb.query(SymbolMaster).filter(SymbolMaster.ticker == "SH03").first()
    assert after.name == "Renamed Corp"
    assert after.category == "テーマ"


def test_replace_reactivates_a_symbol_that_came_back_to_the_sheet(udb):
    """一度シートから外して戻した銘柄が active=1 に復帰すること。"""
    _seed(udb)
    s = udb.query(SymbolMaster).filter(SymbolMaster.ticker == "SH05").first()
    s.active = 0
    udb.commit()

    execute_import_diff(udb, [_parsed(f"SH{i:02d}") for i in range(12)], [], mode="replace")
    udb.commit()

    assert udb.query(SymbolMaster).filter(SymbolMaster.ticker == "SH05").first().active == 1
