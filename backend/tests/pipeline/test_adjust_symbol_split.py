"""分割・併合の価格補正のテスト（scripts/adjust_symbol_split.py）

## なぜ補正が必要になったか

2026-08-06 の調査では「未調整の分割は `SOXS` 1件のみ、しかもレバレッジ category で
母集団外」だったため、**補正は実装しない**と判断していた
（`doc/completed/split_anomaly_noise_reduction_plan.md` §8）。

2026-08-25 に前提が変わった。`BYND` が 1:30 併合し、上流(Yahoo)の系列が
**歯抜けにしか調整されていない**（全期間1,838行中7営業日だけ ×30 済み）ため
取り直しでは直らず、さらに所属する仮想テーマ3本まで汚染した。

    2026-08-12   close 0.4141   ← 併合前スケール
    2026-08-13   close 12.4650  ← 併合後スケール（段差 ×30.10）

## 二重に効かせない設計

補正は**冪等ではない**。2回かければ ×900 になる。接合部の検算を必須にして、
比率が `factor` に一致しない状態では**書き込ませない**ことで事故を防ぐ。

## SQLite も直さないと翌日戻る

`truncate_symbol_history` と同じ罠。`daily_prices` は Parquet と SQLite の
マージ（`drop_duplicates(keep='last')`・SQL 側優先）で伝播するため、
SQLite に併合前スケールの行が残っていると翌日のデイリーで Parquet に戻る。
"""

import os
import sqlite3
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

from scripts.adjust_symbol_split import (  # noqa: E402
    MERGED_TABLES,
    SeamCheckError,
    backadjust_prices,
    interpolate_market_cap,
    replace_sqlite_rows,
    resolve_db_path,
    scale_sqlite_history,
    verify_seam,
)

SEAM = "2026-08-13"
FACTOR = 30.0


def _bynd_frame():
    """`BYND` を模した最小の価格フレーム。08-13 に ×30 の段差がある。

    巻き込んではいけない別銘柄(id=99)も同じ日付で入れておく。
    """
    rows = []
    pre = [("2026-08-10", 0.5200, 36247500.0, 2.679780e08),
           ("2026-08-11", 0.4180, 143012600.0, 2.156123e08),
           ("2026-08-12", 0.4141, 108607358.0, 2.136006e08)]
    post = [("2026-08-13", 12.4650, 2574106.0, 6.429684e09),   # mcap だけ跳ねている
            ("2026-08-14", 13.4700, 3149974.0, 2.316027e08),
            ("2026-08-17", 11.6300, 2864437.0, 1.999658e08)]
    for d, c, v, mc in pre + post:
        rows.append({"symbol_id": 680, "date": d, "open": c, "high": c * 1.02,
                     "low": c * 0.98, "close": c, "volume": v, "market_cap": mc})
    for d, _, _, _ in pre + post:
        rows.append({"symbol_id": 99, "date": d, "open": 10.0, "high": 10.0,
                     "low": 10.0, "close": 10.0, "volume": 1000.0,
                     "market_cap": 1.0e09})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# verify_seam — 二重適用と比率間違いに対する唯一の歯止め
# ---------------------------------------------------------------------------
def test_seam_ratio_matches_the_declared_factor():
    px = _bynd_frame()
    info = verify_seam(px, 680, SEAM, FACTOR)
    assert info["ratio"] == pytest.approx(12.4650 / 0.4141, rel=1e-6)
    assert info["last_pre_date"] == "2026-08-12"
    assert info["first_post_date"] == SEAM


def test_seam_check_rejects_a_wrong_factor():
    """比率が違うのに書き込ませない。**推定値を本番に入れないための線**。"""
    px = _bynd_frame()
    with pytest.raises(SeamCheckError):
        verify_seam(px, 680, SEAM, factor=2.0)


def test_seam_check_rejects_a_second_application():
    """一度補正した系列にもう一度かけようとしたら止まる（冪等ではないため）。

    補正後は段差が消えて比率が 1.0 付近になるので、factor=30 とは一致しない。
    """
    px = backadjust_prices(_bynd_frame(), 680, SEAM, FACTOR)
    with pytest.raises(SeamCheckError):
        verify_seam(px, 680, SEAM, FACTOR)


def test_seam_check_aborts_when_the_boundary_has_no_data():
    px = _bynd_frame()
    with pytest.raises(SeamCheckError):
        verify_seam(px, 680, "2019-01-01", FACTOR)


def test_seam_tolerance_is_configurable():
    """実データの比率はぴったり factor にならない（当日の値動きが乗る）。"""
    px = _bynd_frame()
    assert verify_seam(px, 680, SEAM, FACTOR, tolerance=0.05)["ok"]
    with pytest.raises(SeamCheckError):
        verify_seam(px, 680, SEAM, FACTOR, tolerance=0.001)


# ---------------------------------------------------------------------------
# backadjust_prices
# ---------------------------------------------------------------------------
def test_pre_split_ohlc_are_multiplied_and_volume_divided():
    out = backadjust_prices(_bynd_frame(), 680, SEAM, FACTOR)
    r = out[(out.symbol_id == 680) & (out.date == "2026-08-12")].iloc[0]

    assert r["close"] == pytest.approx(0.4141 * 30)
    assert r["open"] == pytest.approx(0.4141 * 30)
    assert r["high"] == pytest.approx(0.4141 * 1.02 * 30)
    assert r["low"] == pytest.approx(0.4141 * 0.98 * 30)
    assert r["volume"] == pytest.approx(108607358.0 / 30)


def test_post_split_rows_are_untouched():
    px = _bynd_frame()
    out = backadjust_prices(px, 680, SEAM, FACTOR)
    for d in ("2026-08-13", "2026-08-14", "2026-08-17"):
        before = px[(px.symbol_id == 680) & (px.date == d)].iloc[0]
        after = out[(out.symbol_id == 680) & (out.date == d)].iloc[0]
        for col in ("open", "high", "low", "close", "volume"):
            assert after[col] == before[col], f"{d} の {col} が変わっている"


def test_other_symbols_are_untouched():
    px = _bynd_frame()
    out = backadjust_prices(px, 680, SEAM, FACTOR)
    pd.testing.assert_frame_equal(
        px[px.symbol_id == 99].reset_index(drop=True),
        out[out.symbol_id == 99].reset_index(drop=True),
    )


def test_market_cap_is_never_scaled():
    """併合は株数÷30・株価×30 なので**時価総額は不変**。

    実データでも併合前の market_cap は正しい値（2.1e8 前後）で入っている。
    ここをスケールすると 6.4e9 の誤った値を全期間にばら撒くことになる。
    """
    px = _bynd_frame()
    out = backadjust_prices(px, 680, SEAM, FACTOR)
    pd.testing.assert_series_equal(
        out[out.symbol_id == 680]["market_cap"].reset_index(drop=True),
        px[px.symbol_id == 680]["market_cap"].reset_index(drop=True),
    )


def test_row_count_is_preserved():
    px = _bynd_frame()
    out = backadjust_prices(px, 680, SEAM, FACTOR)
    assert len(out) == len(px), "補正で行が増減している"


def test_dollar_volume_is_preserved_across_the_adjustment():
    """close×volume は不変（×30 と ÷30 で打ち消す）。

    売買代金は段差判定にも流動性フィルタにも使われるので、
    ここが動くと補正が別の歪みを生む。
    """
    px = _bynd_frame()
    out = backadjust_prices(px, 680, SEAM, FACTOR)
    b = px[(px.symbol_id == 680) & (px.date < SEAM)]
    a = out[(out.symbol_id == 680) & (out.date < SEAM)]
    assert list((a["close"] * a["volume"]).round(6)) == \
        list((b["close"] * b["volume"]).round(6))


# ---------------------------------------------------------------------------
# interpolate_market_cap — 接合日の1行だけのスパイク
# ---------------------------------------------------------------------------
def test_market_cap_spike_is_interpolated_from_neighbours():
    """08-13 だけ株数が未更新のまま価格×30 が入り 6.43e9 に跳ねている。

    前後（2.136e8 / 2.316e8）から補間して埋める。mcap フィルタを使う
    スクリーナー・バックテストが接合日だけ別の母集団にならないようにするため。
    """
    px = interpolate_market_cap(_bynd_frame(), 680, SEAM)
    v = px[(px.symbol_id == 680) & (px.date == SEAM)].iloc[0]["market_cap"]
    assert v == pytest.approx((2.136006e08 + 2.316027e08) / 2, rel=1e-6)


def test_market_cap_interpolation_touches_only_that_date():
    px = _bynd_frame()
    out = interpolate_market_cap(px, 680, SEAM)
    others = out[(out.symbol_id == 680) & (out.date != SEAM)]
    orig = px[(px.symbol_id == 680) & (px.date != SEAM)]
    pd.testing.assert_series_equal(
        others["market_cap"].reset_index(drop=True),
        orig["market_cap"].reset_index(drop=True),
    )


def test_market_cap_interpolation_is_a_noop_without_neighbours():
    """端で前後が揃わないときは触らない（推定値を作らない）。"""
    px = _bynd_frame()
    out = interpolate_market_cap(px, 680, "2026-08-10")
    assert out[(out.symbol_id == 680) & (out.date == "2026-08-10")].iloc[0][
        "market_cap"] == pytest.approx(2.679780e08)


# ---------------------------------------------------------------------------
# scale_sqlite_history — これを省くと翌日のデイリーで補正が戻る
# ---------------------------------------------------------------------------
@pytest.fixture
def hot_db(tmp_path):
    path = str(tmp_path / "stocktool_test.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT)")
    con.execute("""CREATE TABLE daily_prices (
        id INTEGER PRIMARY KEY, symbol_id INTEGER, date TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL, market_cap REAL)""")
    con.executemany("INSERT INTO symbols (id, ticker) VALUES (?, ?)",
                    [(680, "BYND"), (99, "OTHER")])
    rows = []
    for sid in (680, 99):
        for d, c, v in [("2026-08-11", 0.4180, 143012600.0),
                        ("2026-08-12", 0.4141, 108607358.0),
                        ("2026-08-13", 12.4650, 2574106.0)]:
            rows.append((sid, d, c, c, c, c, v, 2.1e08))
    con.executemany(
        "INSERT INTO daily_prices (symbol_id, date, open, high, low, close, volume, market_cap)"
        " VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


def test_sqlite_pre_split_rows_are_scaled(hot_db):
    scale_sqlite_history(hot_db, 680, SEAM, FACTOR, dry_run=False, expect_ticker="BYND")
    con = sqlite3.connect(hot_db)
    got = dict(con.execute(
        "SELECT date, close FROM daily_prices WHERE symbol_id = 680").fetchall())
    con.close()
    assert got["2026-08-12"] == pytest.approx(0.4141 * 30)
    assert got["2026-08-13"] == pytest.approx(12.4650), "接合日を二重に補正している"


def test_sqlite_other_symbols_are_untouched(hot_db):
    scale_sqlite_history(hot_db, 680, SEAM, FACTOR, dry_run=False, expect_ticker="BYND")
    con = sqlite3.connect(hot_db)
    got = dict(con.execute(
        "SELECT date, close FROM daily_prices WHERE symbol_id = 99").fetchall())
    con.close()
    assert got["2026-08-12"] == pytest.approx(0.4141)


def test_sqlite_dry_run_reports_without_writing(hot_db):
    n = scale_sqlite_history(hot_db, 680, SEAM, FACTOR, dry_run=True, expect_ticker="BYND")
    con = sqlite3.connect(hot_db)
    close = con.execute(
        "SELECT close FROM daily_prices WHERE symbol_id = 680 AND date = '2026-08-12'"
    ).fetchone()[0]
    con.close()
    assert n["daily_prices"] == 2
    assert close == pytest.approx(0.4141), "dry-run で書き込んでいる"


def test_sqlite_refuses_when_the_ticker_does_not_match(hot_db):
    """**全期間再構築は `symbols.id` を再採番する。** Parquet で解決した id を
    照合せずに SQLite へ流すと別銘柄を削る（2026-08-06 に 2,378件が変化した実績）。
    """
    with pytest.raises(ValueError):
        scale_sqlite_history(hot_db, 680, SEAM, FACTOR, dry_run=False,
                             expect_ticker="AAPL")
    con = sqlite3.connect(hot_db)
    close = con.execute(
        "SELECT close FROM daily_prices WHERE symbol_id = 680 AND date = '2026-08-12'"
    ).fetchone()[0]
    con.close()
    assert close == pytest.approx(0.4141), "検証に失敗したのに書き込んでいる"


def test_sqlite_missing_table_does_not_abort(tmp_path):
    """Sandbox 等で daily_prices が無くても止まらない。"""
    path = str(tmp_path / "bare.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT)")
    con.execute("INSERT INTO symbols (id, ticker) VALUES (680, 'BYND')")
    con.commit()
    con.close()
    out = scale_sqlite_history(path, 680, SEAM, FACTOR, dry_run=True,
                               expect_ticker="BYND")
    assert out["daily_prices"] is None


def test_merged_tables_covers_the_merge_path():
    """マージで SQLite → Parquet に戻る階層を漏らしていないこと。

    価格だけ直しても indicators / relative_ranks に古い値が残っていると
    翌日のマージで Parquet 側に戻る。
    """
    assert MERGED_TABLES == ("daily_prices", "indicators", "relative_ranks")


# ---------------------------------------------------------------------------
# replace_sqlite_rows — indicators / relative_ranks は「スケール」ではなく「差し替え」
#
# 指標は比率・偏差・フラグが混在しており、×30 して意味が通るのは価格系の一部だけ。
# 価格を補正したら **再計算した値で丸ごと置き換える**のが唯一正しい。
# ---------------------------------------------------------------------------
def _ind_db(tmp_path):
    path = str(tmp_path / "ind.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT)")
    con.execute("""CREATE TABLE indicators (
        id INTEGER PRIMARY KEY, symbol_id INTEGER, date TEXT, ema_21 REAL)""")
    con.execute("INSERT INTO symbols (id, ticker) VALUES (680, 'BYND')")
    con.executemany(
        "INSERT INTO indicators (symbol_id, date, ema_21) VALUES (?,?,?)",
        [(680, "2026-08-12", 0.41), (680, "2026-08-13", 12.4),
         (99, "2026-08-12", 5.0)])
    con.commit()
    con.close()
    return path


def test_replace_swaps_only_the_target_symbol_rows(tmp_path):
    path = _ind_db(tmp_path)
    new = pd.DataFrame([{"symbol_id": 680, "date": "2026-08-12", "ema_21": 12.3},
                        {"symbol_id": 680, "date": "2026-08-13", "ema_21": 12.4}])
    replace_sqlite_rows(path, "indicators", [680], new, dry_run=False)

    con = sqlite3.connect(path)
    got = dict(con.execute(
        "SELECT date, ema_21 FROM indicators WHERE symbol_id = 680").fetchall())
    other = con.execute(
        "SELECT ema_21 FROM indicators WHERE symbol_id = 99").fetchone()[0]
    con.close()
    assert got["2026-08-12"] == pytest.approx(12.3)
    assert other == pytest.approx(5.0), "対象外の銘柄まで消している"


def test_replace_is_a_noop_on_dry_run(tmp_path):
    path = _ind_db(tmp_path)
    new = pd.DataFrame([{"symbol_id": 680, "date": "2026-08-12", "ema_21": 99.9}])
    replace_sqlite_rows(path, "indicators", [680], new, dry_run=True)

    con = sqlite3.connect(path)
    v = con.execute(
        "SELECT ema_21 FROM indicators WHERE symbol_id = 680 AND date = '2026-08-12'"
    ).fetchone()[0]
    con.close()
    assert v == pytest.approx(0.41), "dry-run で書き込んでいる"


def test_replace_ignores_columns_absent_from_the_table(tmp_path):
    """Parquet 側の列が SQLite に無くても落ちない（列は増え続けている）。"""
    path = _ind_db(tmp_path)
    new = pd.DataFrame([{"symbol_id": 680, "date": "2026-08-12",
                         "ema_21": 12.3, "brand_new_col": 1.0}])
    replace_sqlite_rows(path, "indicators", [680], new, dry_run=False)

    con = sqlite3.connect(path)
    v = con.execute(
        "SELECT ema_21 FROM indicators WHERE symbol_id = 680 AND date = '2026-08-12'"
    ).fetchone()[0]
    con.close()
    assert v == pytest.approx(12.3)


# ---------------------------------------------------------------------------
# resolve_db_path — sandbox 隔離の穴を塞ぐ
#
# `truncate_symbol_history.py` は `config["system"]["db_path"]` を直読みしており、
# **`STOCKTOOL_DB_PATH` を設定しても本番 DB を掴む**。環境変数の解釈は
# `db.database.init_db()` の中にしか無いため、それを import しないスクリプトは
# 全部この穴を持つ。本スクリプトでは明示的に解決する。
# ---------------------------------------------------------------------------
def _cfg():
    return {"system": {"db_path": "data/stocktool.db"}}


def test_db_path_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("STOCKTOOL_ENV", raising=False)
    monkeypatch.delenv("STOCKTOOL_DB_PATH", raising=False)
    assert resolve_db_path(_cfg()) == "data/stocktool.db"


def test_stocktool_db_path_overrides_config(monkeypatch):
    monkeypatch.delenv("STOCKTOOL_ENV", raising=False)
    monkeypatch.setenv("STOCKTOOL_DB_PATH", "data/sandbox/stocktool_sandbox.db")
    assert resolve_db_path(_cfg()) == "data/sandbox/stocktool_sandbox.db"


def test_sandbox_env_wins_over_db_path(monkeypatch):
    """`init_db()` と同じ優先順位（STOCKTOOL_ENV が先）にする。

    ここが `init_db` とズレると、API が見ている DB とスクリプトが書く DB が
    食い違う。
    """
    monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
    monkeypatch.setenv("STOCKTOOL_DB_PATH", "data/other.db")
    assert resolve_db_path(_cfg()) == "data/sandbox/stocktool.db"


def test_explicit_argument_wins_over_everything(monkeypatch):
    monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
    monkeypatch.setenv("STOCKTOOL_DB_PATH", "data/other.db")
    assert resolve_db_path(_cfg(), explicit="data/x.db") == "data/x.db"


def test_replace_stores_symbol_id_as_integer_not_blob(tmp_path):
    """**Int64（pandas nullable）の symbol_id を BLOB で書かないこと。**

    Parquet の ID 列はリポジトリの規約で `Int64` に統一されている
    （parquet-data-quality SKILL §3）。この列を `itertuples()` で取り出すと
    `numpy.int64` が出てくるが、**numpy スカラはバッファプロトコルを持つため
    sqlite3 が BLOB として束縛する**。

    2026-08-25 に Sandbox で実際に起きた。挿入は成功し件数も合っているのに、
    `WHERE symbol_id = 317` が 1 件も返らなくなる:

        (b'=\x01\x00\x00\x00\x00\x00\x00', 2110, '2018-04-03', '2026-08-24')

    仮想テーマ3本の価格が丸ごと参照不能になり、UI からもバックテストからも
    消えた状態になっていた。**例外は出ないので気付けない。**

    既存の `restore_sqlite_cache_from_parquet` が使う
    `[tuple(x) for x in df.to_numpy()]` は Python int に落ちるので安全。
    """
    path = str(tmp_path / "int64.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT)")
    con.execute("""CREATE TABLE daily_prices (
        id INTEGER PRIMARY KEY, symbol_id INTEGER, date TEXT,
        close REAL, market_cap REAL)""")
    con.execute("INSERT INTO symbols (id, ticker) VALUES (317, '_CNSM0A_')")
    con.commit()
    con.close()

    rows = pd.DataFrame({
        "symbol_id": pd.array([317, 317], dtype="Int64"),   # Parquet と同じ型
        "date": ["2026-08-12", "2026-08-13"],
        "close": [972.23, 985.10],
        "market_cap": [float("nan"), float("nan")],         # テーマは常に NULL
    })
    n = replace_sqlite_rows(path, "daily_prices", [317], rows, dry_run=False)

    con = sqlite3.connect(path)
    types = con.execute("SELECT typeof(symbol_id) FROM daily_prices").fetchall()
    reachable = con.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE symbol_id = 317").fetchone()[0]
    mc = con.execute(
        "SELECT typeof(market_cap) FROM daily_prices LIMIT 1").fetchone()[0]
    con.close()

    assert n == 2
    assert {t[0] for t in types} == {"integer"}, f"symbol_id が integer でない: {types}"
    assert reachable == 2, "symbol_id で引けない（BLOB として入っている）"
    assert mc == "null", "NaN が NULL になっていない"
