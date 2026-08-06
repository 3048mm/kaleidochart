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
