"""分割記録ストアのテスト（data_collection/split_records.py）

## 背景

上流が記録している株式分割は、**手元のどこにも保存されていなかった**
（DB にも Parquet にも splits 列は無い。`actions=True` のヒットは
`scan_split_consistency.py` の1箇所だけ）。取得したそばから捨てていたため、
`classify_price_jump()` は「その日に分割があったか」を知らないまま段差を
`market_wide` / `real_move` に落としていた（`doc/issue_list.md` P1）。

このモジュールはその記録を2つのレポート（`scan_price_anomalies.py` /
`weekly_maintenance.py`）で共有するための最小限の永続化層。

## 設計上の要点をテストで固定する

- **無いこと自体は異常ではない**（`data/` は git 管理外で、新規チェックアウトには
  存在しない）→ `None` を返す
- **壊れていることは異常**（黙って `None` に丸めない）→ 例外を投げる。
  `get_latest_master_files()` が読み込み失敗を握り潰して全期間履歴を捨てた前例が
  あるため、ここでは fail-loud にする
- **BOM を付けない**（BOM 付き JSON は `json.load()` が
  `Unexpected UTF-8 BOM` で落ちる。CLAUDE.md「Coding conventions」）
"""

import json
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

from data_collection.split_records import (  # noqa: E402
    DEFAULT_FILENAME,
    load_split_records,
    resolve_default_path,
    save_split_records,
    split_map,
)


SAMPLE = {
    "IESC": [("2026-08-24", 2.0)],
    "AVB": [("2026-08-17", 2.793)],
    "SCCO": [("2026-05-01", 1.01), ("2025-05-02", 1.006)],
}


# ---------------------------------------------------------------------------
# 保存と読み出し
# ---------------------------------------------------------------------------
def test_roundtrip_keeps_dates_and_factors(tmp_path):
    """保存した分割記録が、日付と比率をそのまま復元できる。"""
    path = str(tmp_path / DEFAULT_FILENAME)
    save_split_records(path, SAMPLE, start="2024-09-11", years=2,
                       tickers_fetched=2956, failed=["ZZZZ"])

    got = load_split_records(path)
    assert got is not None
    assert got["start"] == "2024-09-11"
    assert got["years"] == 2
    assert got["tickers_fetched"] == 2956
    assert got["failed"] == ["ZZZZ"]
    assert got["splits"]["IESC"] == [["2026-08-24", 2.0]]
    assert got["splits"]["AVB"] == [["2026-08-17", 2.793]]
    assert len(got["splits"]["SCCO"]) == 2


def test_save_records_generated_at(tmp_path):
    """いつ取った記録かが分かる（古い記録を新鮮なものと誤読しないため）。"""
    path = str(tmp_path / DEFAULT_FILENAME)
    save_split_records(path, SAMPLE, start="2024-09-11", years=2,
                       tickers_fetched=1, failed=[])
    got = load_split_records(path)
    assert got["generated_at"]                      # 空でない
    assert got["generated_at"][:2] == "20"          # ISO 形式の年


def test_save_writes_utf8_without_bom_and_lf(tmp_path):
    """BOM 付きだと `json.load()` が Unexpected UTF-8 BOM で落ちる。"""
    path = str(tmp_path / DEFAULT_FILENAME)
    save_split_records(path, SAMPLE, start="2024-09-11", years=2,
                       tickers_fetched=1, failed=[])

    raw = open(path, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM が付いている"
    assert b"\r\n" not in raw, "CRLF が混入している"
    json.loads(raw.decode("utf-8"))                 # 素直に読めること


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    """**一時ファイル経由で差し替える。**

    直接上書きすると書き込み中断で切り詰まった JSON が残る。読み出しは fail-loud
    なので、そうなると価格アノマリー・週次メンテの両方が手動削除まで止まる。
    """
    path = str(tmp_path / DEFAULT_FILENAME)
    save_split_records(path, SAMPLE, start="2024-09-11", years=2,
                       tickers_fetched=1, failed=[])
    save_split_records(path, {"NEW": [("2026-01-01", 2.0)]}, start="2025-01-01",
                       years=1, tickers_fetched=1, failed=[])

    assert not os.path.exists(path + ".tmp"), "一時ファイルが残っている"
    got = load_split_records(path)
    assert set(got["splits"]) == {"NEW"}, "差し替えになっていない"


def test_save_creates_parent_directory(tmp_path):
    """出力先ディレクトリが無くても保存できる。"""
    path = str(tmp_path / "nested" / "dir" / DEFAULT_FILENAME)
    save_split_records(path, SAMPLE, start="2024-09-11", years=2,
                       tickers_fetched=1, failed=[])
    assert os.path.exists(path)


# ---------------------------------------------------------------------------
# 無い場合と壊れている場合 — **扱いを変える**
# ---------------------------------------------------------------------------
def test_missing_file_returns_none(tmp_path):
    """無いこと自体は異常ではない（data/ は git 管理外）。"""
    assert load_split_records(str(tmp_path / "does_not_exist.json")) is None


def test_malformed_file_raises(tmp_path):
    """**壊れているのは異常。黙って None に丸めない。**

    `get_latest_master_files()` が読み込み失敗を握り潰して Parquet の全期間履歴を
    捨てた前例（`doc/issue_list.md` 2026-09-09 解決）と同じ型を作らない。
    """
    path = tmp_path / DEFAULT_FILENAME
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_split_records(str(path))


def test_json_without_splits_key_raises(tmp_path):
    """形が違うものを「記録ゼロ」と読み替えない。"""
    path = tmp_path / DEFAULT_FILENAME
    path.write_text('{"generated_at": "2026-09-10"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_split_records(str(path))


# ---------------------------------------------------------------------------
# 既定パスの解決
# ---------------------------------------------------------------------------
def test_resolve_default_path_sits_next_to_other_reports():
    """他の監査レポートと同じ `maintenance_reports/` に置く。"""
    got = resolve_default_path(os.path.join("d:", "x", "data", "stocktool.db"))
    assert got.endswith(os.path.join("maintenance_reports", DEFAULT_FILENAME))
    assert os.path.join("data", "maintenance_reports") in got


# ---------------------------------------------------------------------------
# 照合用の取り出し
# ---------------------------------------------------------------------------
def test_split_map_returns_ticker_to_pairs():
    """分類器へ渡す形（ticker → [(date, factor)]）に直す。"""
    records = {"splits": {"IESC": [["2026-08-24", 2.0]]}}
    m = split_map(records)
    assert m["IESC"] == [("2026-08-24", 2.0)]


def test_split_map_of_none_is_empty():
    """記録が無ければ空。**呼び出し側に None 分岐を強いない。**"""
    assert split_map(None) == {}
