"""WAL チェックポイントのテスト（pipeline/orchestrator.safe_wal_checkpoint）

## なぜ壊れていたか

`stocktool.db` が 6.3GB・`-wal` が 3.6GB まで肥大し、パイプラインの各フェーズ終了時に
必ずこう出ていた。

    [WARNING] WAL checkpoint (TRUNCATE) skipped/failed (likely active read locks):
              (sqlite3.OperationalError) database table is locked

当初は「他の読み取り接続に阻まれている」と診断されていたが、**それは主因ではなかった**。

`write_engine` は `begin` イベントで **`BEGIN IMMEDIATE`** を張る。
`db.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))` はセッション経由なので
**暗黙のトランザクションに入り**、SQLite はトランザクション内のチェックポイントを拒否する。
つまり**他に誰も居なくても必ず失敗する**。実測（2026-08-07）:

    A. 現行実装（write セッションで PRAGMA）  → 失敗 database table is locked
    B. 生コネクションで PRAGMA                → 成功 (0, 0, 0) / WAL 11.6MB → 0

**このフェーズのチェックポイントは一度も成功していなかった。**

## 読み取りに阻まれた場合（別問題・実測で確認）

読み取りトランザクションが開いていると、**どのモードでも1フレームも回収できない**。

    PASSIVE   busy=0 log=506  checkpointed=0    ← 成功に見えるが回収ゼロ
    FULL      busy=1 log=1012 checkpointed=0
    TRUNCATE  busy=1 log=1518 checkpointed=0

`PASSIVE` に変えても解決しないどころか、**busy=0 を返すので問題が見えなくなる**。
したがって TRUNCATE のまま、**結果を数値で報告する**方針を採る。
"""

import os
import sqlite3
import sys

import pytest
from sqlalchemy import text

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline.orchestrator import (  # noqa: E402
    describe_checkpoint_result,
    safe_wal_checkpoint,
)


class _Log:
    """logger の代わり。出た文言を検証できるようにする。"""

    def __init__(self):
        self.info, self.warning, self.error = [], [], []

    def __getattr__(self, name):
        raise AttributeError(name)


class Recorder:
    def __init__(self):
        self.messages = {"info": [], "warning": [], "error": []}

    def info(self, m):
        self.messages["info"].append(str(m))

    def warning(self, m):
        self.messages["warning"].append(str(m))

    def error(self, m):
        self.messages["error"].append(str(m))

    def all(self):
        return " / ".join(sum(self.messages.values(), []))


@pytest.fixture
def db_path(tmp_path):
    """本番と同じエンジン設定（WAL + BEGIN IMMEDIATE）の一時 DB。"""
    import db.database as dbm

    path = str(tmp_path / "wal_test.db")
    dbm.init_db(path)
    yield path
    if dbm.engine:
        dbm.engine.dispose()
    if dbm.write_engine:
        dbm.write_engine.dispose()
    dbm.engine = dbm.write_engine = None
    dbm.SessionLocal = dbm.SessionLocalWrite = None


def _wal_bytes(path):
    p = path + "-wal"
    return os.path.getsize(p) if os.path.exists(p) else 0


def _fill_wal(path, rows=400):
    import db.database as dbm

    with dbm.get_write_db() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS blob_t (id INTEGER PRIMARY KEY, v BLOB)"))
        for _ in range(rows):
            db.execute(text("INSERT INTO blob_t (v) VALUES (:v)"), {"v": b"x" * 8000})
        db.commit()
    assert _wal_bytes(path) > 0, "テストの前提が崩れている（WAL が育っていない）"


def test_checkpoint_actually_truncates_the_wal(db_path):
    """**これが本丸。** 呼んだら WAL が実際に回収されること。

    現行実装は例外を握り潰して警告を出すだけだったため、
    「呼んでいるのに一度も効いていない」ことに誰も気づけなかった。
    """
    import db.database as dbm

    _fill_wal(db_path)
    before = _wal_bytes(db_path)

    with dbm.get_write_db() as db:
        safe_wal_checkpoint(db, Recorder())

    assert _wal_bytes(db_path) == 0, f"WAL が回収されていない（{before} → {_wal_bytes(db_path)} bytes）"


def test_reports_success_with_numbers(db_path):
    """成功したことがログで分かること（黙って成功しない）。"""
    import db.database as dbm

    _fill_wal(db_path)
    rec = Recorder()
    with dbm.get_write_db() as db:
        result = safe_wal_checkpoint(db, rec)

    assert result is not None and result[0] == 0, f"busy が立っている: {result}"
    assert rec.messages["warning"] == [], f"成功なのに警告が出ている: {rec.messages['warning']}"


def test_does_not_raise_when_a_reader_holds_a_snapshot(db_path):
    """読み取りに阻まれてもパイプラインを落とさない（安全側の挙動は維持）。"""
    import db.database as dbm

    _fill_wal(db_path)
    reader = sqlite3.connect(db_path, timeout=1)
    reader.execute("PRAGMA busy_timeout=1000")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM blob_t").fetchone()
    try:
        rec = Recorder()
        with dbm.get_write_db() as db:
            safe_wal_checkpoint(db, rec)          # 例外を出さないこと
        assert rec.messages["warning"], "阻まれたのに警告が出ていない"
    finally:
        reader.close()


def test_blocked_warning_names_the_pending_frames(db_path):
    """**警告に未回収フレーム数を出す。**

    「失敗した」だけでは、たまたま1回阻まれたのか WAL が育ち続けているのか区別できない。
    肥大化の検知はこの数値に依存する。
    """
    import db.database as dbm

    _fill_wal(db_path)
    reader = sqlite3.connect(db_path, timeout=1)
    reader.execute("PRAGMA busy_timeout=1000")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM blob_t").fetchone()
    try:
        rec = Recorder()
        with dbm.get_write_db() as db:
            safe_wal_checkpoint(db, rec)
        assert any(char.isdigit() for char in rec.all()), f"数値が出ていない: {rec.all()}"
    finally:
        reader.close()


def test_repeated_calls_are_harmless(db_path):
    """連続で呼んでも壊れない（各フェーズ終了ごとに呼ばれるため）。"""
    import db.database as dbm

    _fill_wal(db_path)
    with dbm.get_write_db() as db:
        safe_wal_checkpoint(db, Recorder())
        safe_wal_checkpoint(db, Recorder())
        r = safe_wal_checkpoint(db, Recorder())

    assert r == (0, 0, 0)


# ---------------------------------------------------------------------------
# describe_checkpoint_result — 結果の解釈（純関数）
# ---------------------------------------------------------------------------
def test_describe_full_recovery():
    """WAL が空（回収済み）は成功。"""
    ok, msg = describe_checkpoint_result((0, 0, 0))

    assert ok is True
    assert "WAL" in msg


def test_describe_blocked_reports_unrecovered_frames():
    """`log > checkpointed` の差が「回収できなかった量」。"""
    ok, msg = describe_checkpoint_result((1, 1518, 0))

    assert ok is False
    assert "1518" in msg


def test_describe_passive_style_zero_recovery_is_not_success():
    """**busy=0 でも回収ゼロなら成功扱いしない。**

    PASSIVE は阻まれても busy=0 を返す。数値を見ないと問題を見逃す。
    """
    ok, _msg = describe_checkpoint_result((0, 506, 0))

    assert ok is False


def test_describe_handles_none():
    """PRAGMA が行を返さない環境でも落ちない。"""
    ok, msg = describe_checkpoint_result(None)

    assert ok is False and msg


# ---------------------------------------------------------------------------
# 週次メンテでの WAL 監視（再発の検知）
# ---------------------------------------------------------------------------
class TestWalSizeMonitoring:
    """**チェックポイントが再び壊れても気づけるようにする。**

    2026-07-28 に `-wal` 3.6GB が見つかるまで、パイプラインは毎フェーズ
    警告を出し続けていたのに誰も気づかなかった。ログの WARNING は流れて消える。
    週次レポートに**サイズを数値で残す**ことで、増え続けていることが分かるようにする。
    """

    def test_small_wal_is_normal(self):
        from scripts.weekly_maintenance import classify_wal_size

        level, msg = classify_wal_size(wal_bytes=2 * 1024**2, db_bytes=1700 * 1024**2)

        assert level == "ok"
        assert "MB" in msg

    def test_large_wal_is_flagged(self):
        """実測の異常値（-wal 3.6GB）を検知できること。"""
        from scripts.weekly_maintenance import classify_wal_size

        level, msg = classify_wal_size(wal_bytes=3600 * 1024**2, db_bytes=6300 * 1024**2)

        assert level == "warn"
        assert "チェックポイント" in msg

    def test_threshold_boundary(self):
        from scripts.weekly_maintenance import WAL_WARN_BYTES, classify_wal_size

        assert classify_wal_size(WAL_WARN_BYTES, 10**10)[0] == "ok"
        assert classify_wal_size(WAL_WARN_BYTES + 1, 10**10)[0] == "warn"

    def test_absent_wal_file_is_normal(self):
        """WAL が存在しない（＝完全に回収済み）は正常。"""
        from scripts.weekly_maintenance import classify_wal_size

        assert classify_wal_size(0, 1700 * 1024**2)[0] == "ok"


def test_describe_handles_sqlite_minus_one():
    """**SQLite はチェックポイントを開始できないと `-1` を返す。**

    実測（2026-08-07 の日次パイプライン）:

        busy=1 log=-1 checkpointed=-1

    これを引き算すると「未回収 0 フレーム」になり、
    **回収できたかのように読めてしまう**。開始できなかったことを明示する。
    """
    ok, msg = describe_checkpoint_result((1, -1, -1))

    assert ok is False
    assert "0 フレーム" not in msg, f"誤解を招く表現: {msg}"
    assert "開始" in msg


class TestWeeklyMaintenanceFinalCheckpoint:
    """**物理メンテの後に監査が書き込むので、最後にもう一度回収する必要がある。**

    2026-08-07 の週次メンテ実測:

        08:18:57  VACUUM 完了  DB 6,086MB → 1,575MB（74.1% 回収）
        08:19:11  監査・SEC 同期の書き込み後 …  -wal 1,584 MB が残存

    物理メンテ（checkpoint → REINDEX → VACUUM）は**監査より前**に走るため、
    その後の書き込みが WAL に積まれたまま終わる。レポートにも
    「チェックポイントが効いていません」と出てしまい、実態と食い違う。
    """

    def test_ratio_is_omitted_for_tiny_databases(self):
        """小さい DB で「DB の 153%」のような無意味な比率を出さない。

        `user_data.db` は 0.09MB なので、WAL 0.1MB でも比率が 153% になる。
        """
        from scripts.weekly_maintenance import classify_wal_size

        _level, msg = classify_wal_size(wal_bytes=100 * 1024, db_bytes=90 * 1024)

        assert "%" not in msg, f"極小 DB で比率を出している: {msg}"

    def test_ratio_is_shown_for_real_databases(self):
        from scripts.weekly_maintenance import classify_wal_size

        _level, msg = classify_wal_size(wal_bytes=50 * 1024**2, db_bytes=1500 * 1024**2)

        assert "%" in msg

    def test_tiny_database_with_tiny_wal_is_ok(self):
        from scripts.weekly_maintenance import classify_wal_size

        assert classify_wal_size(100 * 1024, 90 * 1024)[0] == "ok"


# ---------------------------------------------------------------------------
# SPY 事前チェックが失敗したときに黙って通過しない
# ---------------------------------------------------------------------------
class TestAbortWhenSpyDateIsUnknown:
    """**「今日の相場日が分からない」まま走ると、取得ゼロでも成功と報告する。**

    2026-08-07 の日次更新で実際に起きた。`.bat` のコメント（日本語）で cmd の
    パース位置がずれ `CURL_CA_BUNDLE` が設定されず、Norton の TLS 傍受により
    yfinance が全滅した。それでもパイプラインはこう報告した:

        09:51:31 [ERROR] $SPY: possibly delisted; no price data found (period=1d)
        09:51:34 [ERROR] $SPY: possibly delisted...            ← 1行も取れず
        09:53:27 [INFO]  --- Step 3 Pipeline COMPLETED SUCCESSFULLY ---

    `spy_latest_date` が None だと判定ブロックごとスキップされ、そのまま T2〜T5 を
    通過してしまう。**上流の日付が分からない = 新しいデータがあるか判断できない**
    のだから、成功と報告してはいけない。

    既存のガードでは捕まらない:
      - `is_publishable_master`  … 既存 Parquet とマージされるので prices は空にならない
      - T2 の SPY ガード          … SPY には過去の行があるので `spy_latest_date` は真
    """

    def test_aborts_when_precheck_could_not_determine_the_date(self):
        from pipeline.orchestrator import should_abort_without_spy_date

        abort, reason = should_abort_without_spy_date(
            spy_latest_date=None, has_explicit_rebuild=False, skip_fetch=False)

        assert abort is True
        assert "SPY" in reason

    def test_proceeds_when_the_date_is_known(self):
        import datetime as dt

        from pipeline.orchestrator import should_abort_without_spy_date

        abort, _ = should_abort_without_spy_date(
            spy_latest_date=dt.date(2026, 8, 6), has_explicit_rebuild=False, skip_fetch=False)

        assert abort is False

    def test_explicit_rebuild_does_not_need_the_precheck(self):
        """`--rebuild-from` / `--re-calculate` は事前チェックを行わない経路。"""
        from pipeline.orchestrator import should_abort_without_spy_date

        abort, _ = should_abort_without_spy_date(
            spy_latest_date=None, has_explicit_rebuild=True, skip_fetch=False)

        assert abort is False

    def test_skip_fetch_does_not_need_the_precheck(self):
        """`--skip-fetch` は上流を見ない前提の実行。"""
        from pipeline.orchestrator import should_abort_without_spy_date

        abort, _ = should_abort_without_spy_date(
            spy_latest_date=None, has_explicit_rebuild=False, skip_fetch=True)

        assert abort is False

    def test_reason_points_at_the_likely_cause(self):
        """原因の当たりを付けられる文言にする（同じ事故で毎回ログを追わないため）。"""
        from pipeline.orchestrator import should_abort_without_spy_date

        _abort, reason = should_abort_without_spy_date(False, False, False)

        assert "証明書" in reason or "CURL_CA_BUNDLE" in reason
