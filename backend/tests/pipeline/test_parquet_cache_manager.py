# parquet_cache_manager のバルクインサートに関する回帰テスト
#
# 背景（2026-07-09 deploy_after_merge 初回実行で実発生）:
# bulk_insert_df_to_sqlite が PRAGMA journal_mode = MEMORY を実行していたが、
# WAL からの journal_mode 変更は「他の接続が1つでも開いていると」database is locked
# で即失敗する。restore 処理は同一プロセス内で SQLAlchemy セッションと raw 接続の
# 複数接続を持つため、本番/ワークスペースを問わず失敗し得る。
# 対策: バルクインサート中も WAL を維持し、journal_mode を変更しない
# （.claude/skills/sqlite-wal-handling/SKILL.md の規約）。
import logging
import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from pipeline.parquet_cache_manager import bulk_insert_df_to_sqlite

logger = logging.getLogger(__name__)


def _prepare_wal_db(tmp_path):
    """WAL モードの SQLite と symbols 互換の最小テーブルを用意する"""
    db_file = str(tmp_path / "bulk_test.db")
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text(
            "CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT, exchange TEXT)"
        ))
    return db_file, engine


def _sample_df():
    return pd.DataFrame({
        "id": [1, 2],
        "ticker": ["AAA", "BBB"],
        "exchange": ["NYSE", "NASDAQ"],
    })


def test_bulk_insert_succeeds_while_another_connection_is_open(tmp_path):
    """他の接続が開いたままでもバルクインサートが成功する（journal_mode 変更禁止の担保）。
    WAL からの journal_mode 変更は他接続が存在するだけで database is locked になるため、
    restore の実行環境（同一プロセス内の複数接続）では必ず他接続が存在する前提で書く"""
    db_file, engine = _prepare_wal_db(tmp_path)
    other = sqlite3.connect(db_file)
    other.execute("SELECT 1").fetchone()  # WAL の共有状態を掴んだ接続を保持
    try:
        bulk_insert_df_to_sqlite(engine, _sample_df(), "symbols", logger)
        count = other.execute("SELECT count(*) FROM symbols").fetchone()[0]
        assert count == 2
    finally:
        other.close()
        engine.dispose()


def test_bulk_insert_keeps_wal_mode(tmp_path):
    """バルクインサート後も DB が WAL モードのままである"""
    db_file, engine = _prepare_wal_db(tmp_path)
    try:
        bulk_insert_df_to_sqlite(engine, _sample_df(), "symbols", logger)
    finally:
        engine.dispose()
    conn = sqlite3.connect(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_bulk_insert_empty_df_is_noop(tmp_path):
    """空 DataFrame は何もしない"""
    db_file, engine = _prepare_wal_db(tmp_path)
    try:
        bulk_insert_df_to_sqlite(engine, pd.DataFrame(), "symbols", logger)
        conn = sqlite3.connect(db_file)
        assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
        conn.close()
    finally:
        engine.dispose()


def test_clean_old_parquet_versions_grace_period(tmp_path):
    """15分の猶予期間（grace period）が適用され、最近更新されたバージョンは削除されないこと、
    および十分に古いバージョンは削除されることをテストする。"""
    import json
    import os
    import time
    from pipeline.parquet_cache_manager import clean_old_parquet_versions

    parquet_dir = tmp_path / "parquet_master"
    os.makedirs(parquet_dir, exist_ok=True)

    # 1. 3つのバージョン用 JSON ポインタと関連ダミーファイルを作成する
    # v1 (非常に古い: 30分前)
    v1_json = parquet_dir / "data_version_20260717_060000.json"
    v1_file = parquet_dir / "v1_prices.parquet"
    v1_file.write_text("dummy")
    with open(v1_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v1_file)}, f)
    # mtime を30分前（1800秒前）に設定
    t_v1 = time.time() - 1800
    os.utime(v1_json, (t_v1, t_v1))
    os.utime(v1_file, (t_v1, t_v1))

    # v2 (猶予期間内: 5分前)
    v2_json = parquet_dir / "data_version_20260717_062500.json"
    v2_file = parquet_dir / "v2_prices.parquet"
    v2_file.write_text("dummy")
    with open(v2_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v2_file)}, f)
    # mtime を5分前（300秒前）に設定
    t_v2 = time.time() - 300
    os.utime(v2_json, (t_v2, t_v2))
    os.utime(v2_file, (t_v2, t_v2))

    # v3 (最新: 今作成)
    v3_json = parquet_dir / "data_version_20260717_063000.json"
    v3_file = parquet_dir / "v3_prices.parquet"
    v3_file.write_text("dummy")
    with open(v3_json, 'w', encoding='utf-8') as f:
        json.dump({"prices": str(v3_file)}, f)
    # mtime は現在時刻

    # 2. クリーンアップを実行する（keep_count=2 で実行するが、v2は猶予期間内のためスキップされるはず）
    clean_old_parquet_versions(str(parquet_dir), logger, keep_count=2)

    # 3. アサーション
    # v1（非常に古い）は削除されているはず
    assert not os.path.exists(v1_json)
    assert not os.path.exists(v1_file)

    # v2（猶予期間内）は keep_count=2 枠の「外側」（pointers_to_delete に入る）だが、
    # 5分前作成で猶予期間（15分）内なので、削除されずに残っているはず！
    assert os.path.exists(v2_json)
    assert os.path.exists(v2_file)

    # v3（最新）は keep_count=2 内なので当然残る
    assert os.path.exists(v3_json)
    assert os.path.exists(v3_file)



# ---------------------------------------------------------------------------
# rotate_and_archive_to_parquet の「全期間同期テーブルは置換」の回帰テスト
#
# 背景（universe.db 移行 2026-07-28）:
# process_and_merge_table は concat + drop_duplicates の追記型マージのため、
# SQLite での「削除」が Parquet に伝播しない。symbols / theme_constituents は
# SQLite が完全集合を持つ全期間同期テーブル（architecture.md §11.1）なので、
# マージのままだと削除済みのテーマ構成が Parquet に残り、Parquet を直読みする
# バックテストが古い構成を見続ける。
# ---------------------------------------------------------------------------
import os

from db.models import Base
from pipeline.parquet_cache_manager import rotate_and_archive_to_parquet
from sqlalchemy.orm import sessionmaker


def _make_db_with_data(tmp_path, symbols, tc_rows):
    db_file = str(tmp_path / "stocktool_rotate_test.db")
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=engine)
    conn = sqlite3.connect(db_file)
    conn.executemany(
        "INSERT INTO symbols (id, ticker, exchange, category, active) VALUES (?,?,?,?,?)", symbols)
    conn.executemany(
        "INSERT INTO theme_constituents (id, theme_id, symbol_id, weight) VALUES (?,?,?,?)", tc_rows)
    conn.commit()
    conn.close()
    return db_file, engine


# 注: 以下のテストは fx / 全期間同期テーブルだけを検証する最小フィクスチャのため
#     価格・指標を持たない。空マスタガード（is_publishable_master）は本番経路の
#     保護が目的なので require_non_empty=False で明示的に外している。
def test_deletions_propagate_to_parquet_for_full_sync_tables(tmp_path):
    """SQLite で削除した theme_constituents / symbols が Parquet からも消えること。"""
    os.makedirs(tmp_path / "parquet_master", exist_ok=True)

    symbols = [(1, "SPY", "NYSEARCA", "市場", 1),
               (2, "COPX", "NYSEARCA", "テーマ", 1),
               (3, "FCX", "NYSE", "個別", 1),
               (4, "OLDCO", "NYSE", "個別", 1)]
    tc_rows = [(1, 2, 3, 0.5), (2, 2, 4, 0.5)]
    db_file, engine = _make_db_with_data(tmp_path, symbols, tc_rows)

    Session = sessionmaker(bind=engine)

    # 1回目: 4銘柄 / 2ペアを書き出す
    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import get_latest_master_files, get_pointer_file_path, get_parquet_master_dir
    pointer = get_pointer_file_path(get_parquet_master_dir(db_file))
    files = get_latest_master_files(pointer)
    assert len(pd.read_parquet(files["symbols"])) == 4
    assert len(pd.read_parquet(files["tc"])) == 2

    # SQLite 側から 1銘柄と 1ペアを削除
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM theme_constituents WHERE theme_id=2 AND symbol_id=4")
    conn.execute("DELETE FROM symbols WHERE ticker='OLDCO'")
    conn.commit()
    conn.close()

    # 2回目: 削除が Parquet に伝播していること
    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    files = get_latest_master_files(pointer)
    df_sym = pd.read_parquet(files["symbols"])
    df_tc = pd.read_parquet(files["tc"])
    assert len(df_sym) == 3, "削除した symbols が Parquet に残っている"
    assert "OLDCO" not in set(df_sym["ticker"])
    assert len(df_tc) == 1, "削除した theme_constituents が Parquet に残っている"
    assert set(zip(df_tc["theme_id"], df_tc["symbol_id"])) == {(2, 3)}
    engine.dispose()


def test_timeseries_tables_still_merge_history(tmp_path):
    """時系列テーブルは従来通りマージされ、ホット期間外の履歴が保持されること。"""
    os.makedirs(tmp_path / "parquet_master", exist_ok=True)
    symbols = [(1, "SPY", "NYSEARCA", "市場", 1)]
    db_file, engine = _make_db_with_data(tmp_path, symbols, [])
    Session = sessionmaker(bind=engine)

    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2020-01-02',100.0)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    # ホットキャッシュのパージを模して古い行を消す
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM daily_prices WHERE date='2020-01-02'")
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2026-01-05',500.0)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import get_latest_master_files, get_pointer_file_path, get_parquet_master_dir
    files = get_latest_master_files(get_pointer_file_path(get_parquet_master_dir(db_file)))
    dates = set(pd.read_parquet(files["prices"])["date"].astype(str))
    assert dates == {"2020-01-02", "2026-01-05"}, "パージされた履歴が Parquet から失われた"
    engine.dispose()


# ---------------------------------------------------------------------------
# fx_rates の Parquet 対象化に関する回帰テスト
#
# 背景（2026-08-01）:
# fx_rates は長らく SQLite のみで Parquet に含まれていなかった。そのため
# 2026-07-30 の完全再構築（空DBから作り直し）で為替履歴 7,711行(1996-2026) が
# 22行(直近30日) に消えた。architecture.md では market_signals も「SQLiteのみ」と
# 書かれていたが実装では Parquet 対象になっており、fx_rates だけが取り残されていた。
# ---------------------------------------------------------------------------
from db.models import FxRate
from datetime import date as _date


def _seed_fx(db_file, rows):
    conn = sqlite3.connect(db_file)
    conn.executemany(
        "INSERT INTO fx_rates (currency_pair, date, rate) VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()


def _seed_price(db_file, symbol_id=1, d="2026-01-05", close=100.0):
    """restore_sqlite_cache_from_parquet は daily_prices の最大日付から
    ホット期間の cutoff を決めるため、価格が1行も無いと算出に失敗する。"""
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO daily_prices (symbol_id, date, close) VALUES (?,?,?)",
        (symbol_id, d, close))
    conn.commit()
    conn.close()


def test_fx_rates_are_archived_to_parquet(tmp_path):
    """fx_rates が Parquet に書き出されること。"""
    os.makedirs(tmp_path / "parquet_master", exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    _seed_fx(db_file, [("USD/JPY", "2020-01-02", 108.5), ("USD/JPY", "2020-01-03", 108.7)])

    Session = sessionmaker(bind=engine)
    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import (
        get_latest_master_files, get_parquet_master_dir, get_pointer_file_path)
    files = get_latest_master_files(
        get_pointer_file_path(get_parquet_master_dir(db_file)))
    assert "fx" in files, "latest_master.json に fx キーが無い"
    df = pd.read_parquet(files["fx"])
    assert len(df) == 2
    assert set(df["date"].astype(str)) == {"2020-01-02", "2020-01-03"}
    engine.dispose()


def test_fx_rates_deletions_propagate(tmp_path):
    """SQLite で消した行が Parquet からも消えること（マージではなく置換）。

    ダミー値を実データに入れ替えるような修正が Parquet に伝播しないと、
    再構築のたびに古い値が復活する。
    """
    os.makedirs(tmp_path / "parquet_master", exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    _seed_fx(db_file, [("USD/JPY", "2020-01-02", 155.4), ("USD/JPY", "2020-01-03", 155.6)])
    Session = sessionmaker(bind=engine)

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    # ダミー値を消して実データに入れ替える
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM fx_rates")
    conn.execute("INSERT INTO fx_rates (currency_pair, date, rate) VALUES ('USD/JPY','2020-01-02',108.5)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import (
        get_latest_master_files, get_parquet_master_dir, get_pointer_file_path)
    files = get_latest_master_files(get_pointer_file_path(get_parquet_master_dir(db_file)))
    df = pd.read_parquet(files["fx"])
    assert len(df) == 1, "削除した fx_rates が Parquet に残っている"
    assert df["rate"].iloc[0] == pytest.approx(108.5)
    engine.dispose()


def test_restore_keeps_fx_when_pointer_has_no_fx_key(tmp_path):
    """'fx' キーの無い旧世代から復元しても fx_rates を消さないこと。

    無条件クリアにすると、Parquet 対象化より前の世代へロールバックした瞬間に
    為替履歴が全滅する。
    """
    import json
    from pipeline.parquet_cache_manager import (
        restore_sqlite_cache_from_parquet, get_parquet_master_dir, get_pointer_file_path)

    os.makedirs(tmp_path / "parquet_master", exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    _seed_fx(db_file, [("USD/JPY", "2020-01-02", 108.5)])
    _seed_price(db_file)
    Session = sessionmaker(bind=engine)

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    # ポインタから 'fx' を取り除いて旧世代を再現する
    pointer = get_pointer_file_path(get_parquet_master_dir(db_file))
    files = json.load(open(pointer, encoding="utf-8"))
    files.pop("fx", None)
    with open(pointer, "w", encoding="utf-8") as f:
        json.dump(files, f)

    db = Session()
    restore_sqlite_cache_from_parquet(db, db_file, logger)
    db.close()

    conn = sqlite3.connect(db_file)
    n = conn.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
    conn.close()
    assert n == 1, "旧世代からの復元で fx_rates が消えた"
    engine.dispose()


def test_restore_reloads_fx_from_parquet(tmp_path):
    """'fx' キーがあれば Parquet の内容で置き換えること。"""
    from pipeline.parquet_cache_manager import restore_sqlite_cache_from_parquet

    os.makedirs(tmp_path / "parquet_master", exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    _seed_fx(db_file, [("USD/JPY", "2020-01-02", 108.5), ("USD/JPY", "2020-01-03", 108.7)])
    _seed_price(db_file)
    Session = sessionmaker(bind=engine)

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    # SQLite 側を壊してから復元する
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM fx_rates")
    conn.commit()
    conn.close()

    db = Session()
    restore_sqlite_cache_from_parquet(db, db_file, logger)
    db.close()

    conn = sqlite3.connect(db_file)
    n = conn.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
    conn.close()
    assert n == 2, "Parquet から fx_rates が復元されていない"
    engine.dispose()


# ---------------------------------------------------------------------------
# 空マスタの書き込み拒否（2026-08-06 の事故を受けて追加）
# ---------------------------------------------------------------------------
class TestEmptyMasterGuard:
    """**価格ゼロの世代を Parquet マスタとして書いてはいけない。**

    2026-08-06 の全期間再構築で、Norton の TLS 傍受により `curl_cffi`（yfinance が
    cookie/crumb 取得に使う）が証明書検証に失敗し、SPY を含む全銘柄の取得が
    「possibly delisted; no price data found」で失敗した。

    パイプラインはそれを「データ無し」として **T2〜T5 を 0行のまま通過し、
    ほぼ空の Parquet 世代でポインタを上書きして "COMPLETED SUCCESSFULLY" を出した**。
    退避（`archive_parquet_master.py`）が無ければ 11GB・604万行の履歴が失われていた。

        Writing updated masters - Symbols: 3220, Prices: 0, Indicators: 0, Ranks: 0

    再構築時は旧世代が退避済みでマージ相手が居ないため、既存の
    「全期間同期テーブルが空なら旧世代を維持する」安全弁では防げない。
    """

    def test_empty_prices_is_rejected(self):
        from pipeline.parquet_cache_manager import is_publishable_master

        ok, reason = is_publishable_master(prices_rows=0, indicators_rows=0, symbols_rows=3220)

        assert ok is False
        assert "prices" in reason

    def test_normal_master_is_publishable(self):
        from pipeline.parquet_cache_manager import is_publishable_master

        ok, reason = is_publishable_master(
            prices_rows=6_041_821, indicators_rows=6_041_821, symbols_rows=3220)

        assert ok is True and reason == ""

    def test_prices_without_indicators_is_rejected(self):
        """T2 は通ったが T3 が落ちた世代も公開しない（バックテストが指標を失う）。"""
        from pipeline.parquet_cache_manager import is_publishable_master

        ok, reason = is_publishable_master(
            prices_rows=6_041_821, indicators_rows=0, symbols_rows=3220)

        assert ok is False
        assert "indicators" in reason

    def test_empty_symbols_is_rejected(self):
        from pipeline.parquet_cache_manager import is_publishable_master

        ok, _ = is_publishable_master(prices_rows=100, indicators_rows=100, symbols_rows=0)

        assert ok is False


# ---------------------------------------------------------------------------
# 全期間同期テーブルの縮小ガード（fx_rates 消失の3度目を防ぐ）
# ---------------------------------------------------------------------------
class TestFullSyncShrinkGuard:
    """**「空なら維持」では足りない。「激減したら維持」が要る。**

    `fx_rates` は SQLite を正として Parquet を**置換**するテーブル。
    ところが全期間再構築はサンドボックスの空 DB から始まるため、FX 同期は
    直近30日しか取得せず、その少数行が全履歴を置き換えてしまう。

        2026-07-30 の再構築  7,711行 → 22行
        2026-08-06 の再構築  7,717行 → 23行   ← 2026-08-01 の修正後も再発

    2026-08-01 に入れた安全弁は「空なら旧世代を維持」だったため、
    **23行は「空ではない」ので素通りした。**

    一方で置換をやめてマージにすると、ダミー値の削除が Parquet に伝播しない
    （それが置換にした理由）。**通常の削除は通し、再構築由来の激減だけ弾く。**
    """

    def _df(self, dates):
        import pandas as pd
        return pd.DataFrame({"currency_pair": ["USD/JPY"] * len(dates), "date": dates,
                             "rate": [150.0] * len(dates)})

    def test_rebuild_shrinkage_falls_back_to_union(self):
        """再構築で直近30日だけになったら旧世代と和集合にする。"""
        from pipeline.parquet_cache_manager import resolve_full_sync_table

        old = self._df([f"2020-01-{d:02d}" for d in range(1, 29)] + ["2026-08-04"])
        new = self._df(["2026-08-04", "2026-08-05"])

        df, action = resolve_full_sync_table(new, old, ["currency_pair", "date"])

        assert action == "union"
        assert len(df) == 30, "旧世代の履歴が失われている"
        assert "2026-08-05" in set(df["date"]), "新しい行が入っていない"

    def test_normal_deletion_still_propagates(self):
        """ダミー行の削除など通常の縮小は置換のまま通す（削除を伝播させる）。"""
        from pipeline.parquet_cache_manager import resolve_full_sync_table

        old = self._df([f"2026-07-{d:02d}" for d in range(1, 21)])
        new = self._df([f"2026-07-{d:02d}" for d in range(1, 19)])   # 2行削除

        df, action = resolve_full_sync_table(new, old, ["currency_pair", "date"])

        assert action == "replace"
        assert len(df) == 18

    def test_growth_is_replace(self):
        from pipeline.parquet_cache_manager import resolve_full_sync_table

        old = self._df(["2026-07-01"])
        new = self._df(["2026-07-01", "2026-07-02"])

        _df, action = resolve_full_sync_table(new, old, ["currency_pair", "date"])

        assert action == "replace"

    def test_empty_new_keeps_old(self):
        """既存の「空なら維持」も引き続き効くこと。"""
        import pandas as pd
        from pipeline.parquet_cache_manager import resolve_full_sync_table

        old = self._df(["2026-07-01", "2026-07-02"])

        df, action = resolve_full_sync_table(pd.DataFrame(columns=old.columns), old,
                                             ["currency_pair", "date"])

        assert action == "keep_old"
        assert len(df) == 2

    def test_no_old_generation_is_replace(self):
        """初回（旧世代なし）は素直に置換する。"""
        from pipeline.parquet_cache_manager import resolve_full_sync_table

        new = self._df(["2026-07-01"])

        _df, action = resolve_full_sync_table(new, None, ["currency_pair", "date"])

        assert action == "replace"


# ---------------------------------------------------------------------------
# merge_timeseries_table — 時系列テーブルのマージがコールド履歴を捨てないこと
#
# 背景（2026-09-01 に実発生・6年分の指標を喪失）:
# rotate 内の process_and_merge_table は、旧 Parquet とのマージに失敗すると
# `except` で握りつぶして「SQLite の内容だけ」を返していた。SQLite は
# ホットキャッシュ（730日）なので、**コールド側の全履歴がそこで消える**。
#
#   [WARNING] Failed to merge with old parquet cache for indicators:
#     Unable to allocate 1.77 GiB for an array with shape (39, 6082624)
#     and data type float64. Storing SQL only.
#
# 警告1行だけで、パイプラインは COMPLETED SUCCESSFULLY を出した。実際には
# indicators が 6,076,932行 → 1,594,632行 になり、2021〜2024前半の指標が
# 全滅してバックテストが動かなくなった。
#
# 同種の「テーブルが激減する」事故は全期間同期テーブル側では
# resolve_full_sync_table（2026-08-06 の fx_rates 7,717→23行）で既に塞がれて
# いたが、時系列マージ側には防護が無かった。
# ---------------------------------------------------------------------------
class TestMergeTimeseriesTable:
    @staticmethod
    def _old_parquet(tmp_path, rows):
        p = str(tmp_path / "old.parquet")
        pd.DataFrame(rows).to_parquet(p, index=False)
        return p

    def test_merges_new_rows_onto_old_history(self, tmp_path):
        from pipeline.parquet_cache_manager import merge_timeseries_table

        old = self._old_parquet(tmp_path, [
            {"symbol_id": 1, "date": "2020-01-01", "v": 1.0},
            {"symbol_id": 1, "date": "2020-01-02", "v": 2.0},
        ])
        sql = pd.DataFrame([{"symbol_id": 1, "date": "2020-01-03", "v": 3.0}])

        out = merge_timeseries_table("indicators", ["symbol_id", "date"], sql, old, logger)

        assert len(out) == 3, "旧履歴 + 新規行にならない"
        assert sorted(out["date"]) == ["2020-01-01", "2020-01-02", "2020-01-03"]

    def test_sql_row_wins_for_the_same_key(self, tmp_path):
        from pipeline.parquet_cache_manager import merge_timeseries_table

        old = self._old_parquet(tmp_path, [{"symbol_id": 1, "date": "2020-01-01", "v": 1.0}])
        sql = pd.DataFrame([{"symbol_id": 1, "date": "2020-01-01", "v": 99.0}])

        out = merge_timeseries_table("indicators", ["symbol_id", "date"], sql, old, logger)

        assert len(out) == 1
        assert out.iloc[0]["v"] == 99.0, "同一キーは SQLite 側が勝つべき"

    def test_raises_instead_of_silently_returning_sql_only(self, tmp_path, monkeypatch):
        """旧 Parquet の読み込みが失敗したら、**例外を上げて rotate を止める**。

        ここで SQLite の内容だけを返すと、ホットキャッシュ(730日)が
        全履歴マスタを置き換えてしまう。2026-09-01 の事故そのもの。
        """
        from pipeline import parquet_cache_manager as pcm

        old = self._old_parquet(tmp_path, [
            {"symbol_id": 1, "date": "2020-01-01", "v": 1.0},
            {"symbol_id": 1, "date": "2020-01-02", "v": 2.0},
        ])
        sql = pd.DataFrame([{"symbol_id": 1, "date": "2026-01-01", "v": 3.0}])

        def _boom(*a, **kw):
            raise MemoryError("Unable to allocate 1.77 GiB")

        monkeypatch.setattr(pcm.pd, "read_parquet", _boom)

        with pytest.raises(Exception) as exc:
            pcm.merge_timeseries_table("indicators", ["symbol_id", "date"], sql, old, logger)
        assert "indicators" in str(exc.value)

    def test_raises_when_merge_would_lose_old_rows(self, tmp_path, monkeypatch):
        """マージ結果が旧世代より減っていたら中断する。

        マージは和集合なので行が減ることは原理的に無い。減っていたら実装が
        壊れているので、マスタを上書きせずに止める。
        """
        from pipeline import parquet_cache_manager as pcm

        old = self._old_parquet(tmp_path, [
            {"symbol_id": 1, "date": f"2020-01-{d:02d}", "v": float(d)} for d in range(1, 11)
        ])
        sql = pd.DataFrame([{"symbol_id": 1, "date": "2026-01-01", "v": 99.0}])

        # 行を落とすマージを注入して防護柵が働くことを確かめる
        monkeypatch.setattr(pcm.pd, "concat", lambda *a, **kw: sql.copy())

        with pytest.raises(Exception) as exc:
            pcm.merge_timeseries_table("indicators", ["symbol_id", "date"], sql, old, logger)
        assert "行" in str(exc.value) or "row" in str(exc.value).lower()

    def test_no_old_parquet_returns_sql_as_is(self, tmp_path):
        """初回（旧世代が無い）は SQLite の内容がそのままマスタになる。"""
        from pipeline.parquet_cache_manager import merge_timeseries_table

        sql = pd.DataFrame([{"symbol_id": 1, "date": "2020-01-01", "v": 1.0}])
        out = merge_timeseries_table("indicators", ["symbol_id", "date"], sql, None, logger)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# ポインタ読み込み失敗の fail-loud 化に関する回帰テスト
#
# 背景（issue_list P1 / 2026-09-01 発見・2026-09-09 対応）:
# get_latest_master_files() は「ポインタが存在しない（＝初回。正常）」と
# 「存在するが読めない（BOM・JSON破損・権限）」を、どちらも警告なしの None で返していた。
# rotate_and_archive_to_parquet() はこの戻り値でマージ元の旧 Parquet を決めるため、
# None になると old_paths が空 → 旧世代を読まずに SQLite の内容だけで新世代を公開する。
# SQLite はホットキャッシュ（直近730日）しか持たないので、7年超の履歴を失った世代が
# 無警告で公開される。
#
# 実際に踏んだ例: latest_master.json を PowerShell で書き換えて BOM が付き、
# json.load() が `Unexpected UTF-8 BOM` を投げた（2026-08-30）。
#
#   ValueError: Unexpected UTF-8 BOM (decode using utf-8-sig)
#
# 同型の激減は 2026-07-30 の再構築でも起きている（fx_rates 7,711行 → 22行）。
# ---------------------------------------------------------------------------
import glob as _glob


BOM_BYTES = bytes([0xEF, 0xBB, 0xBF])


def _corrupt_pointer_with_bom(pointer_file):
    """PowerShell の Set-Content 相当。UTF-8 BOM を付けて json.load() を落とす。"""
    with open(pointer_file, "rb") as f:
        raw = f.read()
    with open(pointer_file, "wb") as f:
        f.write(BOM_BYTES + raw)


def _count_generations(parquet_dir):
    return len(_glob.glob(os.path.join(parquet_dir, "prices_*.parquet")))


def test_get_latest_master_files_returns_none_when_pointer_absent(tmp_path):
    """ポインタが存在しない（真の初回）は従来どおり None。例外にはしない。"""
    from pipeline.parquet_cache_manager import get_latest_master_files
    missing = str(tmp_path / "parquet_master" / "latest_master.json")
    assert get_latest_master_files(missing) is None
    assert get_latest_master_files(missing, strict=True) is None


def test_get_latest_master_files_logs_error_when_unreadable(tmp_path, caplog):
    """読めないポインタは、既定モードでも黙って None を返さずエラーログを出すこと。"""
    from pipeline.parquet_cache_manager import get_latest_master_files
    pointer = tmp_path / "latest_master.json"
    pointer.write_bytes(BOM_BYTES + b'{"prices": "x.parquet"}')

    with caplog.at_level(logging.ERROR):
        result = get_latest_master_files(str(pointer))

    assert result is None, "既定モードの戻り値は従来どおり None（呼び出し側 30箇所超の互換）"
    assert caplog.records, "読み込み失敗が無警告で握り潰されている"
    assert any("latest_master" in r.getMessage() or str(pointer) in r.getMessage()
               for r in caplog.records)


def test_get_latest_master_files_raises_when_unreadable_and_strict(tmp_path):
    """strict=True では「存在するが読めない」を例外にすること。"""
    from pipeline.parquet_cache_manager import (
        ParquetPointerUnreadableError, get_latest_master_files)
    pointer = tmp_path / "latest_master.json"
    pointer.write_bytes(BOM_BYTES + b'{"prices": "x.parquet"}')

    with pytest.raises(ParquetPointerUnreadableError):
        get_latest_master_files(str(pointer), strict=True)


def test_rotate_aborts_when_pointer_unreadable_instead_of_truncating(tmp_path):
    """【本命の再現】ポインタが壊れた状態でローテートしても履歴を切り詰めないこと。

    修正前はここで新世代が「直近ホット期間だけ」の内容で公開され、
    2020-01-02 の履歴が失われていた（警告も出ない）。
    """
    parquet_dir = tmp_path / "parquet_master"
    os.makedirs(parquet_dir, exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    Session = sessionmaker(bind=engine)

    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2020-01-02',100.0)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import (
        ParquetPointerUnreadableError, get_latest_master_files,
        get_parquet_master_dir, get_pointer_file_path)
    pointer = get_pointer_file_path(get_parquet_master_dir(db_file))
    before = get_latest_master_files(pointer)
    assert set(pd.read_parquet(before["prices"])["date"].astype(str)) == {"2020-01-02"}
    gen_before = _count_generations(str(parquet_dir))

    # ホットキャッシュのパージを模して古い行を消す（＝SQLite には履歴が無い状態）
    conn = sqlite3.connect(db_file)
    conn.execute("DELETE FROM daily_prices WHERE date='2020-01-02'")
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2026-01-05',500.0)")
    conn.commit()
    conn.close()

    # ポインタを壊す（PowerShell 由来の BOM 混入を再現）
    _corrupt_pointer_with_bom(pointer)

    db = Session()
    with pytest.raises(ParquetPointerUnreadableError):
        rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    assert _count_generations(str(parquet_dir)) == gen_before,         "マージ元が特定できないのに新世代が生成された（切り詰めが公開されうる）"
    engine.dispose()


def test_rotate_aborts_when_pointer_missing_but_parquet_files_exist(tmp_path):
    """ポインタが「消えた」ケースも止めること。

    「初回だから旧世代が無い」と「ポインタだけ消えた／壊れた」は、ポインタの
    有無だけでは区別できない。実ファイルの有無で照合する。
    """
    parquet_dir = tmp_path / "parquet_master"
    os.makedirs(parquet_dir, exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    Session = sessionmaker(bind=engine)

    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2020-01-02',100.0)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path
    pointer = get_pointer_file_path(get_parquet_master_dir(db_file))
    gen_before = _count_generations(str(parquet_dir))
    os.remove(pointer)

    db = Session()
    with pytest.raises(RuntimeError):
        rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    assert _count_generations(str(parquet_dir)) == gen_before,         "旧世代の実体が残っているのに『初回』として新世代を公開した"
    engine.dispose()


def test_rotate_succeeds_on_true_first_run(tmp_path):
    """真の初回（parquet_dir に実体が無い）は従来どおり成功すること（退行防止）。"""
    parquet_dir = tmp_path / "parquet_master"
    os.makedirs(parquet_dir, exist_ok=True)
    db_file, engine = _make_db_with_data(
        tmp_path, [(1, "SPY", "NYSEARCA", "市場", 1)], [])
    Session = sessionmaker(bind=engine)

    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO daily_prices (symbol_id, date, close) VALUES (1,'2026-01-05',500.0)")
    conn.commit()
    conn.close()

    db = Session()
    rotate_and_archive_to_parquet(db, db_file, logger, require_non_empty=False)
    db.close()

    from pipeline.parquet_cache_manager import (
        get_latest_master_files, get_parquet_master_dir, get_pointer_file_path)
    files = get_latest_master_files(get_pointer_file_path(get_parquet_master_dir(db_file)))
    assert files is not None
    assert len(pd.read_parquet(files["prices"])) == 1
    engine.dispose()


# ---------------------------------------------------------------------------
# find_latest_parquet_backup / get_backup_master_files
#
# 背景（backtest_stable_data_plan.md §3-A / 2026-09-25）:
# バックテスト系は daily update の影響を受けないよう、既定で
# 「検証済みの最新バックアップ」を読む。バックアップは data/_bk_* に
# 手作業で作られ、DB だけのもの（BACKUP_MANIFEST.json に parquet_generation
# が無い）と、Parquet を含むものが混在する。
#
# 🔴 バックアップ内の latest_master.json は本番 data/parquet_master/ を絶対
# パスで指しているため、絶対に使ってはいけない。manifest の parquet_generation
# （ファイル名のみ）をバックアップのフォルダ基準で解決する必要がある。
# ---------------------------------------------------------------------------
import json as _json


def _write_manifest(bk_dir, parquet_generation=None, extra=None):
    manifest = {"created_at": "2026-09-25T00:00:00", "databases": ["stocktool.db"]}
    if parquet_generation is not None:
        manifest["parquet_generation"] = parquet_generation
    if extra:
        manifest.update(extra)
    with open(os.path.join(bk_dir, "BACKUP_MANIFEST.json"), "w", encoding="utf-8") as f:
        _json.dump(manifest, f, ensure_ascii=False)


class TestFindLatestParquetBackup:
    def test_excludes_db_only_backups(self, tmp_path):
        """parquet_generation を持たない（DB だけの）バックアップは候補から除外する。"""
        from pipeline.parquet_cache_manager import find_latest_parquet_backup

        db_only = tmp_path / "_bk_20260910_083603"
        db_only.mkdir()
        _write_manifest(str(db_only))  # parquet_generation なし

        with_parquet = tmp_path / "_bk_20260909_233905"
        with_parquet.mkdir()
        _write_manifest(str(with_parquet), {"prices": "prices_20260909_233905.parquet"})

        result = find_latest_parquet_backup(str(tmp_path))
        assert result == str(with_parquet)

    def test_picks_latest_among_mixed_naming_formats(self, tmp_path):
        """_bk_YYYYMMDD と _bk_YYYYMMDD_HHMMSS が混在しても新しい順に選ぶ。"""
        from pipeline.parquet_cache_manager import find_latest_parquet_backup

        older = tmp_path / "_bk_20260830"
        older.mkdir()
        _write_manifest(str(older), {"prices": "prices_20260830.parquet"})

        newer = tmp_path / "_bk_20260910_083603"
        newer.mkdir()
        _write_manifest(str(newer), {"prices": "prices_20260910_083603.parquet"})

        result = find_latest_parquet_backup(str(tmp_path))
        assert result == str(newer)

    def test_returns_none_when_no_candidates(self, tmp_path):
        """Parquet を含むバックアップが1本も無ければ None。"""
        from pipeline.parquet_cache_manager import find_latest_parquet_backup

        db_only = tmp_path / "_bk_20260910_083603"
        db_only.mkdir()
        _write_manifest(str(db_only))

        assert find_latest_parquet_backup(str(tmp_path)) is None

    def test_returns_none_when_no_backup_dirs_at_all(self, tmp_path):
        from pipeline.parquet_cache_manager import find_latest_parquet_backup
        assert find_latest_parquet_backup(str(tmp_path)) is None

    def test_skips_corrupted_manifest_with_warning(self, tmp_path, caplog):
        """manifest が壊れている候補は警告を出してスキップし、他の正常な候補を選ぶ。"""
        from pipeline.parquet_cache_manager import find_latest_parquet_backup

        broken = tmp_path / "_bk_20260920_000000"
        broken.mkdir()
        with open(broken / "BACKUP_MANIFEST.json", "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")

        valid = tmp_path / "_bk_20260910_083603"
        valid.mkdir()
        _write_manifest(str(valid), {"prices": "prices_20260910_083603.parquet"})

        with caplog.at_level(logging.WARNING):
            result = find_latest_parquet_backup(str(tmp_path))

        assert result == str(valid)
        assert any("20260920_000000" in r.getMessage() for r in caplog.records)

    def test_ignores_dirs_without_manifest(self, tmp_path):
        """BACKUP_MANIFEST.json が無いディレクトリは候補にしない。"""
        from pipeline.parquet_cache_manager import find_latest_parquet_backup

        no_manifest = tmp_path / "_bk_20260925_999999"
        no_manifest.mkdir()

        valid = tmp_path / "_bk_20260910_083603"
        valid.mkdir()
        _write_manifest(str(valid), {"prices": "prices_20260910_083603.parquet"})

        result = find_latest_parquet_backup(str(tmp_path))
        assert result == str(valid)


class TestGetBackupMasterFiles:
    def _make_backup_with_files(self, tmp_path, generation=None):
        bk_dir = tmp_path / "_bk_20260925_135558"
        bk_dir.mkdir()
        generation = generation or {
            "symbols": "symbols_20260925_135558.parquet",
            "prices": "prices_20260925_135558.parquet",
            "indicators": "indicators_20260925_135558.parquet",
            "ranks": "ranks_20260925_135558.parquet",
            "tc": "theme_constituents_20260925_135558.parquet",
            "signals": "market_signals_20260925_135558.parquet",
            "fx": "fx_rates_20260925_135558.parquet",
        }
        for fname in generation.values():
            (bk_dir / fname).write_text("dummy")
        _write_manifest(str(bk_dir), generation)
        return str(bk_dir), generation

    def test_resolves_paths_relative_to_backup_dir(self, tmp_path):
        """manifest のファイル名を backup_dir 基準の絶対パスに解決する。"""
        from pipeline.parquet_cache_manager import get_backup_master_files

        bk_dir, generation = self._make_backup_with_files(tmp_path)
        result = get_backup_master_files(bk_dir)

        assert set(result.keys()) == set(generation.keys())
        for key, fname in generation.items():
            assert result[key] == os.path.join(bk_dir, fname)
            assert os.path.exists(result[key])

    def test_ignores_latest_master_json_pointing_at_production(self, tmp_path):
        """latest_master.json が本番の別パスを指していても、それは使わずに
        BACKUP_MANIFEST.json だけを根拠にバックアップ内のパスを解決する。"""
        from pipeline.parquet_cache_manager import get_backup_master_files

        bk_dir, generation = self._make_backup_with_files(tmp_path)
        # バックアップ内の latest_master.json（本番の絶対パスを指す罠）を再現
        parquet_dir = tmp_path / "parquet_master"
        with open(os.path.join(bk_dir, "latest_master.json"), "w", encoding="utf-8") as f:
            _json.dump({k: str(parquet_dir / v) for k, v in generation.items()}, f)

        result = get_backup_master_files(bk_dir)
        for key, fname in generation.items():
            assert result[key] == os.path.join(bk_dir, fname)
            assert str(parquet_dir) not in result[key]

    def test_raises_on_missing_manifest(self, tmp_path):
        bk_dir = tmp_path / "_bk_20260925_135558"
        bk_dir.mkdir()

        from pipeline.parquet_cache_manager import get_backup_master_files
        with pytest.raises(Exception):
            get_backup_master_files(str(bk_dir))

    def test_raises_on_missing_parquet_generation_key(self, tmp_path):
        bk_dir = tmp_path / "_bk_20260925_135558"
        bk_dir.mkdir()
        _write_manifest(str(bk_dir))  # parquet_generation なし

        from pipeline.parquet_cache_manager import get_backup_master_files
        with pytest.raises(Exception):
            get_backup_master_files(str(bk_dir))

    def test_raises_when_a_file_is_missing(self, tmp_path):
        """manifest に載っているのに実体が無いファイルがあれば例外にする（本番へ黙って戻らない）。"""
        from pipeline.parquet_cache_manager import get_backup_master_files

        bk_dir = tmp_path / "_bk_20260925_135558"
        bk_dir.mkdir()
        generation = {
            "symbols": "symbols_20260925_135558.parquet",
            "prices": "prices_20260925_135558.parquet",
        }
        (bk_dir / generation["symbols"]).write_text("dummy")
        # prices ファイルは作らない（欠損を再現）
        _write_manifest(str(bk_dir), generation)

        with pytest.raises(FileNotFoundError, match="prices"):
            get_backup_master_files(str(bk_dir))


# ---------------------------------------------------------------------------
# resolve_backtest_data_source (backtest_stable_data_plan.md §3-B)
# ---------------------------------------------------------------------------
class TestResolveBacktestDataSource:
    def _make_backup(self, prod_root, name="_bk_20260925_173413", generation=None):
        bk_dir = prod_root / name
        bk_dir.mkdir()
        generation = generation or {"prices": f"prices_{name[4:]}.parquet"}
        for fname in generation.values():
            (bk_dir / fname).write_text("dummy")
        _write_manifest(str(bk_dir), generation)
        return bk_dir

    def _make_production_pointer(self, prod_root, generation="20260920_000000"):
        parquet_dir = prod_root / "parquet_master"
        parquet_dir.mkdir(parents=True, exist_ok=True)
        files = {"prices": str(parquet_dir / f"prices_{generation}.parquet")}
        (parquet_dir / f"prices_{generation}.parquet").write_text("dummy")
        with open(parquet_dir / "latest_master.json", "w", encoding="utf-8") as f:
            _json.dump(files, f)
        return files

    def test_picks_latest_backup_by_default(self, tmp_path, monkeypatch):
        import paths
        from pipeline.parquet_cache_manager import resolve_backtest_data_source

        self._make_backup(tmp_path, "_bk_20260910_000000")
        newest = self._make_backup(tmp_path, "_bk_20260925_173413")
        monkeypatch.setattr(paths, "get_prod_data_root", lambda: str(tmp_path))

        master_files, meta = resolve_backtest_data_source("backup")

        assert master_files["prices"] == str(newest / "prices_20260925_173413.parquet")
        assert meta == {
            "data_source": "backup",
            "backup_name": "_bk_20260925_173413",
            "parquet_generation": "20260925_173413",
        }

    def test_falls_back_to_production_when_no_backup(self, tmp_path, monkeypatch, caplog):
        import paths
        from pipeline.parquet_cache_manager import resolve_backtest_data_source

        files = self._make_production_pointer(tmp_path)
        monkeypatch.setattr(paths, "get_prod_data_root", lambda: str(tmp_path))

        with caplog.at_level(logging.WARNING):
            master_files, meta = resolve_backtest_data_source("backup")

        assert master_files == files
        assert meta["data_source"] == "production"
        assert meta["backup_name"] is None
        assert any("フォールバック" in r.getMessage() for r in caplog.records)

    def test_named_backup_resolves_directly(self, tmp_path, monkeypatch):
        import paths
        from pipeline.parquet_cache_manager import resolve_backtest_data_source

        self._make_backup(tmp_path, "_bk_20260910_000000")
        self._make_backup(tmp_path, "_bk_20260925_173413")
        monkeypatch.setattr(paths, "get_prod_data_root", lambda: str(tmp_path))

        master_files, meta = resolve_backtest_data_source("_bk_20260910_000000")

        assert master_files["prices"] == str(tmp_path / "_bk_20260910_000000" / "prices_20260910_000000.parquet")
        assert meta["data_source"] == "_bk_20260910_000000"
        assert meta["backup_name"] == "_bk_20260910_000000"

    def test_named_backup_missing_raises(self, tmp_path, monkeypatch):
        import paths
        from pipeline.parquet_cache_manager import resolve_backtest_data_source

        monkeypatch.setattr(paths, "get_prod_data_root", lambda: str(tmp_path))

        with pytest.raises(FileNotFoundError):
            resolve_backtest_data_source("_bk_nonexistent")

    def test_production_reads_pointer_not_backup_latest_master_json(self, tmp_path, monkeypatch):
        """バックアップ内の latest_master.json（本番を指す罠）ではなく、
        本番の parquet_master/latest_master.json を読む。"""
        import paths
        from pipeline.parquet_cache_manager import resolve_backtest_data_source

        files = self._make_production_pointer(tmp_path, generation="20260920_000000")
        # バックアップ内に「別の」latest_master.json を置いても production 経路では無視される
        bk_dir = self._make_backup(tmp_path, "_bk_20260925_173413")
        with open(bk_dir / "latest_master.json", "w", encoding="utf-8") as f:
            _json.dump({"prices": "/tmp/should_not_be_used.parquet"}, f)
        monkeypatch.setattr(paths, "get_prod_data_root", lambda: str(tmp_path))

        master_files, meta = resolve_backtest_data_source("production")

        assert master_files == files
        assert meta["parquet_generation"] == "20260920_000000"
