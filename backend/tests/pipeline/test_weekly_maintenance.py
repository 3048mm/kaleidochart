"""
Tests for weekly_maintenance.py script operations.
"""
import os
import sys
import subprocess
import pytest
import sqlite3
from datetime import date

from db.models import Base, Symbol, DailyPrice, Indicator, ThemeConstituent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Paths
PYTHON_EXEC = sys.executable
SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "weekly_maintenance.py"
)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOCK_FILE = os.path.join(PROJECT_ROOT, "update_pipeline.lock")


@pytest.fixture
def temp_lock(tmp_path):
    """テスト専用のロックファイル。

    本番の update_pipeline.lock を使うと、日次パイプラインの実行中に
    テストが必ず失敗する（2026-07-31 に発生。パイプラインが5時間超保持し、
    サブプロセステスト5件が軒並み落ちた）。テストは本番の実行状況から独立させる。
    """
    return str(tmp_path / "test_pipeline.lock")


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary real sqlite file for testing VACUUM and REINDEX."""
    db_file = tmp_path / "test_maintenance.db"
    
    # Initialize basic schemas
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()
    
    yield str(db_file)
    
    if db_file.exists():
        try:
            os.remove(db_file)
        except:
            pass


class TestWeeklyMaintenancePhysical:
    """Tests the CLI parameters and physical maintenance steps."""

    def test_dry_run_success(self, temp_db, temp_lock):
        """Verify that --dry-run completes successfully without raising errors."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Dry-run mode: skipping REINDEX and VACUUM" in result.stdout

    def test_fix_mode_success(self, temp_db, temp_lock):
        """Verify that --fix runs REINDEX and VACUUM on the database."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--fix", "--db-path", temp_db,
             "--lock-file", temp_lock],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Rebuilding indexes (REINDEX)" in result.stdout
        assert "Reclaiming database pages (VACUUM)" in result.stdout

    def test_lock_concurrency_error(self, temp_db, temp_lock):
        """Verify that lock file blocks execution when actively held."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        import msvcrt
        
        # Acquire lock in this process
        lock_fd = os.open(temp_lock, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        
        try:
            result = subprocess.run(
                [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            
            assert result.returncode != 0
            assert "Another instance" in result.stdout or "Another instance" in result.stderr
        finally:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
            if os.path.exists(temp_lock):
                try:
                    os.remove(temp_lock)
                except:
                    pass


# Import audit function from scripts to verify granular logic
try:
    from scripts.weekly_maintenance import audit_and_fix_weekly
except ImportError:
    audit_and_fix_weekly = None


class TestWeeklyMaintenanceAudits:
    """TDD tests for the audit and self-healing logic in weekly_maintenance.py."""

    @pytest.fixture
    def setup_audit_db(self):
        """Prepare clean isolated memory DB for auditing tests."""
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = TestingSessionLocal()
        
        # Seed basic metadata
        spy = Symbol(id=1, ticker="SPY", exchange="US", category="市場", active=1)
        aapl = Symbol(id=2, ticker="AAPL", exchange="US", category="個別", active=1)
        stale = Symbol(id=3, ticker="MSFT", exchange="US", category="個別", active=1)
        empty_theme = Symbol(id=4, ticker="EMPTY_T", exchange="US", category="テーマ", theme_type="theme", active=1)
        
        db.add_all([spy, aapl, stale, empty_theme])
        db.commit()
        
        yield db
        
        db.close()
        Base.metadata.drop_all(engine)

    def test_audit_delisted_symbols(self, setup_audit_db):
        """Verify that symbols staler than 5 trading days relative to SPY are detected."""
        db = setup_audit_db
        d_fresh = date(2026, 7, 15)
        d_stale = date(2026, 7, 1)
        
        db.add_all([
            DailyPrice(symbol_id=1, date=d_fresh, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d_fresh, open=200, high=200, low=200, close=200, volume=200),
            DailyPrice(symbol_id=3, date=d_stale, open=300, high=300, low=300, close=300, volume=300),
        ])
        db.commit()
        
        # Run audit
        report = audit_and_fix_weekly(db, dry_run=True)
        
        assert "MSFT" in report["stale_symbols"]
        assert "EMPTY_T" in report["empty_themes"]

    def test_audit_invalid_constituents(self, setup_audit_db):
        """Verify that constituents pointing to inactive symbols are flagged."""
        db = setup_audit_db
        # Create an inactive symbol
        inactive = Symbol(id=5, ticker="OLD", exchange="US", category="個別", active=0)
        db.add(inactive)
        db.commit()
        
        # Add constituents pointing to active themes but targeting inactive symbols
        db.add_all([
            ThemeConstituent(theme_id=4, symbol_id=5, weight=1.0)
        ])
        db.commit()
        
        report = audit_and_fix_weekly(db, dry_run=True)
        assert len(report["invalid_constituents"]) > 0
        assert report["invalid_constituents"][0]["ticker"] == "OLD"

    def test_scan_and_fix_mismatched_indicators(self, setup_audit_db):
        """Verify that missing indicators on days with prices are backfilled when dry_run=False."""
        db = setup_audit_db
        d = date(2026, 7, 15)
        
        # AAPL has price but no indicator
        db.add_all([
            DailyPrice(symbol_id=1, date=d, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d, open=200, high=200, low=200, close=200, volume=200),
            Indicator(symbol_id=1, date=d, ema_21=100), # SPY has indicator
        ])
        db.commit()
        
        # Run dry run first
        report_dry = audit_and_fix_weekly(db, dry_run=True)
        assert report_dry["mismatched_indicators_count"] == 1
        
        # Indicator still missing in DB
        ind_count = db.query(Indicator).filter(Indicator.symbol_id == 2, Indicator.date == d).count()
        assert ind_count == 0
        
        # Run in fix mode
        report_fix = audit_and_fix_weekly(db, dry_run=False)
        assert report_fix["mismatched_indicators_count"] == 1
        assert report_fix["fixed_indicators_count"] == 1
        
        # Verify indicator exists now
        ind = db.query(Indicator).filter(Indicator.symbol_id == 2, Indicator.date == d).first()
        assert ind is not None

    def test_detect_stock_splits(self, setup_audit_db):
        """Verify that extreme price changes representing suspected splits are flagged."""
        db = setup_audit_db
        d_old = date(2026, 7, 14)
        d_new = date(2026, 7, 15)
        
        # AAPL prices drop by 50% (suspected split)
        db.add_all([
            DailyPrice(symbol_id=1, date=d_old, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=1, date=d_new, open=100, high=100, low=100, close=100, volume=100),
            DailyPrice(symbol_id=2, date=d_old, open=200, high=200, low=200, close=200, volume=100),
            DailyPrice(symbol_id=2, date=d_new, open=100, high=100, low=100, close=100, volume=100), # 200 -> 100
        ])
        db.commit()
        
        report = audit_and_fix_weekly(db, dry_run=True)
        assert len(report["split_anomalies"]) == 1
        assert report["split_anomalies"][0]["ticker"] == "AAPL"
        assert report["split_anomalies"][0]["ratio"] == 0.5

    def test_detect_fx_weekend_rows(self, setup_audit_db):
        """為替に存在しないはずの土日行を検出し、fix モードで除去する（第3層）。

        書き込み側の営業日フィルタと読み取り側の土日無視で実害は防いでいるが、
        それは「混入しても壊れない」だけ。新たな流入経路が生まれたときに
        気付けることを保証する。
        """
        from db.models import FxRate

        db = setup_audit_db
        db.add_all([
            FxRate(currency_pair="USD/JPY", date=date(2026, 7, 31), rate=160.18),  # 金
            FxRate(currency_pair="USD/JPY", date=date(2026, 8, 1), rate=157.40),   # 土
            FxRate(currency_pair="USD/JPY", date=date(2026, 8, 2), rate=157.50),   # 日
        ])
        db.commit()

        # dry-run: 報告するが消さない
        report = audit_and_fix_weekly(db, dry_run=True)
        assert [r[1] for r in report["fx_weekend_rows"]] == ["2026-08-01", "2026-08-02"]
        assert db.query(FxRate).count() == 3

        # fix: 土日行だけ消え、営業日の行は残る
        report_fix = audit_and_fix_weekly(db, dry_run=False)
        assert len(report_fix["fx_weekend_rows"]) == 2
        remaining = db.query(FxRate).all()
        assert [r.date for r in remaining] == [date(2026, 7, 31)]

    def test_no_fx_weekend_rows_when_clean(self, setup_audit_db):
        """平常時（営業日のみ）は何も報告しない＝ノイズにならない"""
        from db.models import FxRate

        db = setup_audit_db
        db.add(FxRate(currency_pair="USD/JPY", date=date(2026, 7, 31), rate=160.18))
        db.commit()

        report = audit_and_fix_weekly(db, dry_run=True)
        assert report["fx_weekend_rows"] == []


# ---------------------------------------------------------------------------
# 銘柄の鮮度分類（classify_symbol_freshness）の単体テスト
#
# 背景（2026-07-29 発見）:
# 旧実装は「SPY 最新日より5営業日以上古い」だけで上場廃止候補としていたため、
# 履歴が数行しか無い銘柄（供給側にデータが無い＝改称・廃止）と、完全な履歴があって
# 直近1日だけ取りこぼした銘柄（次回実行で自己回復）を区別できず、両者を同列に扱っていた。
# さらに inner join だったため「1行も無い」最も重症な銘柄を取りこぼしていた。
# ---------------------------------------------------------------------------
from datetime import date as _date

# `sys.path.insert(0, backend/scripts)` は使わないこと。pytest は収集時に全テストモジュールを
# import するため、scripts/ が sys.path の先頭に入って以降のモジュール解決を汚染し、
# 無関係なテスト（backtest 側）が順序依存で落ちる。PYTHONPATH=backend 前提の
# `scripts.` プレフィックス形式で読む（このファイル既存の import と同じ作法）。
from scripts.weekly_maintenance import classify_symbol_freshness, resolve_report_dir  # noqa: E402

SPY_LATEST = _date(2026, 7, 27)


class TestClassifySymbolFreshness:
    def test_up_to_date_is_ok(self):
        assert classify_symbol_freshness(500, _date(2026, 7, 27), SPY_LATEST) == "ok"

    def test_ahead_of_spy_is_ok(self):
        """為替など SPY より進んだ日付を持つケースでも ok 扱い"""
        assert classify_symbol_freshness(500, _date(2026, 7, 28), SPY_LATEST) == "ok"

    def test_one_day_behind_with_full_history_is_lagging(self):
        """回帰: WM / JBHT / MTD 等が 2,090 行を持ちながら1日遅れただけで
        上場廃止候補に混ざっていた。自己回復するので放置が正しい。"""
        assert classify_symbol_freshness(2090, _date(2026, 7, 24), SPY_LATEST) == "lagging"

    def test_long_stale_with_full_history_is_delisted(self):
        """CNCR: 1,804 行あって最終日が1年以上前 = 本当の上場廃止"""
        assert classify_symbol_freshness(1804, _date(2025, 6, 3), SPY_LATEST) == "delisted"

    def test_boundary_of_stale_window(self):
        # 7日ちょうどは境界の内側（まだ delisted ではない）
        assert classify_symbol_freshness(500, _date(2026, 7, 20), SPY_LATEST) == "lagging"
        assert classify_symbol_freshness(500, _date(2026, 7, 19), SPY_LATEST) == "delisted"

    @pytest.mark.parametrize("rows,last", [
        (0, None),                      # BK / ASGN: Yahoo に存在せず1行も無い
        (1, _date(2026, 7, 17)),        # IAC / VSCO: 直近だが1行だけ
        (3, _date(2026, 6, 12)),        # MASI: 3行だけ
        (7, _date(2026, 7, 27)),        # LC: 最新日に追いついていても行数が足りない
    ])
    def test_almost_no_history_is_no_history(self, rows, last):
        """回帰: 供給側にデータが無い銘柄。取り直しても取得できないため
        バックフィルではなく退役が正しい対応。"""
        assert classify_symbol_freshness(rows, last, SPY_LATEST) == "no_history"

    def test_row_count_threshold_boundary(self):
        assert classify_symbol_freshness(20, _date(2026, 7, 27), SPY_LATEST) == "no_history"
        assert classify_symbol_freshness(21, _date(2026, 7, 27), SPY_LATEST) == "ok"

    def test_none_last_date_is_no_history_regardless_of_count(self):
        assert classify_symbol_freshness(9999, None, SPY_LATEST) == "no_history"

    def test_thresholds_are_configurable(self):
        assert classify_symbol_freshness(
            30, _date(2026, 7, 27), SPY_LATEST, low_history_rows=50) == "no_history"
        assert classify_symbol_freshness(
            500, _date(2026, 7, 24), SPY_LATEST, stale_days=1) == "delisted"


class TestReportDirIsolation:
    """回帰: レポート出力先が監査対象 DB に紐付くこと。

    出力先が `data/maintenance_reports/` 固定だったため、テストが `--db-path <一時DB>` で
    起動したサブプロセスの結果（空 DB なので全項目ゼロ）が本番のレポートを上書きし、
    さらに退役候補 CSV を削除していた（2026-07-29 実際に発生）。
    """

    def test_report_dir_follows_db_path(self, tmp_path):
        target = str(tmp_path / "sub" / "stocktool_test.db")
        assert resolve_report_dir(target) == os.path.join(
            os.path.dirname(os.path.abspath(target)), "maintenance_reports")

    def test_report_dir_falls_back_to_project_data(self):
        assert resolve_report_dir(None) == os.path.join(
            PROJECT_ROOT, "data", "maintenance_reports")

    def test_temp_db_run_does_not_touch_production_reports(self, temp_db, temp_lock, tmp_path):
        """一時 DB を監査しても本番の maintenance_reports に触れないこと。"""
        prod_dir = os.path.join(PROJECT_ROOT, "data", "maintenance_reports")
        before = set(os.listdir(prod_dir)) if os.path.isdir(prod_dir) else set()

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db,
             "--lock-file", temp_lock],
            env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0

        after = set(os.listdir(prod_dir)) if os.path.isdir(prod_dir) else set()
        assert after == before, "本番の maintenance_reports が変化した"

        # 一時 DB 側に出力されていること
        own_dir = os.path.join(os.path.dirname(temp_db), "maintenance_reports")
        assert os.path.isfile(os.path.join(own_dir, "weekly_maintenance_report.txt"))
