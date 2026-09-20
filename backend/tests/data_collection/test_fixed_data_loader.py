"""fixed_data_loader.py の単体テスト（TDD red フェーズ）。

`data/fixed_data/<ticker>.csv`（Investing.comからの手動エクスポート、
UTF-8 BOM付き、日本語ヘッダー、日付降順）を読み、`fetch_daily_data` と
同じ列構成（date/open/high/low/close/volume）の DataFrame に変換する
`load_fixed_data()` の契約を検証する。

本物のファイル（`data/fixed_data/S5FI.csv` 等）には依存せず、
`tmp_path` に同形式のCSVを作って検証する（`fixed_data_dir` 引数で
差し替え可能にしてあるのはこのテスト容易性のため）。
"""

from datetime import date

import pandas as pd

from data_collection.fixed_data_loader import load_fixed_data


def _write_fixed_csv(dir_path, ticker, rows):
    """本物のInvesting.comエクスポート形式（BOM付きUTF-8、日付降順）を模したCSVを書く。

    rows: [(日付, 終値, 始値, 高値, 安値, 出来高, 変化率%), ...]（新しい日付が先頭）
    """
    path = dir_path / f"{ticker}.csv"
    lines = ['"日付","終値","始値","高値","安値","出来高","変化率 %"']
    for d, close, o, h, l, vol, chg in rows:
        lines.append(f'"{d}","{close}","{o}","{h}","{l}","{vol}","{chg}"')
    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8-sig")
    return path


# 新しい日付が先頭（実物CSVと同じ降順）。出来高は基本空欄、1行だけ実値を混ぜる。
_SAMPLE_ROWS = [
    ("2026-09-17", "30.81", "32.80", "32.80", "29.62", "", "0.00%"),
    ("2026-09-16", "30.81", "33.99", "35.18", "29.42", "", "-10.93%"),
    ("2026-09-15", "34.59", "33.59", "35.18", "33.39", "12345", "-8.42%"),
    ("2026-01-02", "78.18", "78.18", "78.18", "78.18", "", "20.18%"),
]


def test_load_fixed_data_filters_by_date_range_and_returns_expected_columns(tmp_path):
    """範囲内の行だけが、昇順・期待した列構成で返る。"""
    _write_fixed_csv(tmp_path, "S5FI", _SAMPLE_ROWS)

    df = load_fixed_data("S5FI", "2026-09-15", "2026-09-17", fixed_data_dir=str(tmp_path))

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert list(df["date"]) == [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    assert df["close"].tolist() == [34.59, 30.81, 30.81]
    assert df["open"].tolist() == [33.59, 33.99, 32.80]
    assert pd.api.types.is_float_dtype(df["open"])
    assert pd.api.types.is_float_dtype(df["high"])
    assert pd.api.types.is_float_dtype(df["low"])
    assert pd.api.types.is_float_dtype(df["close"])


def test_load_fixed_data_returns_empty_when_file_missing(tmp_path):
    """`<fixed_data_dir>/<ticker>.csv` が存在しなければ空DataFrame。"""
    df = load_fixed_data("NOPE", "2020-01-01", fixed_data_dir=str(tmp_path))

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_load_fixed_data_returns_empty_when_start_date_after_last_date(tmp_path):
    """start_dateがファイル最終日より後なら空DataFrame。"""
    _write_fixed_csv(tmp_path, "S5FI", _SAMPLE_ROWS)

    df = load_fixed_data("S5FI", "2026-09-18", fixed_data_dir=str(tmp_path))

    assert df.empty


def test_load_fixed_data_end_date_defaults_to_last_date_in_file(tmp_path):
    """end_date省略時はファイル最終日まで含む。"""
    _write_fixed_csv(tmp_path, "S5FI", _SAMPLE_ROWS)

    df = load_fixed_data("S5FI", "2026-09-15", fixed_data_dir=str(tmp_path))

    assert len(df) == 3
    assert df["date"].max() == date(2026, 9, 17)


def test_load_fixed_data_fills_missing_volume_with_zero(tmp_path):
    """出来高が空欄の行は0で補完され、実値がある行はそのまま反映される。dtypeはint64。"""
    _write_fixed_csv(tmp_path, "S5FI", _SAMPLE_ROWS)

    df = load_fixed_data("S5FI", "2026-01-02", "2026-09-17", fixed_data_dir=str(tmp_path))

    vol_by_date = dict(zip(df["date"], df["volume"]))
    assert vol_by_date[date(2026, 9, 17)] == 0
    assert vol_by_date[date(2026, 9, 16)] == 0
    assert vol_by_date[date(2026, 9, 15)] == 12345
    assert vol_by_date[date(2026, 1, 2)] == 0
    assert pd.api.types.is_integer_dtype(df["volume"])


def test_load_fixed_data_reads_bom_prefixed_csv_without_corrupting_header(tmp_path):
    """BOM付きUTF-8を`utf-8-sig`以外（例: 素の`utf-8`）で読むと、先頭列名が
    `\\ufeff日付` のように壊れて列マッピングに失敗し、結果的に空扱いになる
    回帰を防ぐ。テスト用CSV自体が確実にBOM付きであることも確認する。
    """
    path = _write_fixed_csv(tmp_path, "S5FI", _SAMPLE_ROWS)
    raw = path.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "テスト用CSV自体がBOM付きになっていない"

    df = load_fixed_data("S5FI", "2009-01-01", "2026-12-31", fixed_data_dir=str(tmp_path))

    assert not df.empty
    assert len(df) == len(_SAMPLE_ROWS)
