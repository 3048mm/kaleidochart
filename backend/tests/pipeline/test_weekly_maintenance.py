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

    def test_dry_run_success(self, temp_db):
        """Verify that --dry-run completes successfully without raising errors."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db],
            env=env,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Dry-run mode: skipping REINDEX and VACUUM" in result.stdout

    def test_fix_mode_success(self, temp_db):
        """Verify that --fix runs REINDEX and VACUUM on the database."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        result = subprocess.run(
            [PYTHON_EXEC, SCRIPT_PATH, "--fix", "--db-path", temp_db],
            env=env,
            capture_output=True,
            text=True
        )
        
        assert result.returncode == 0
        assert "Database integrity check: OK" in result.stdout
        assert "Rebuilding indexes (REINDEX)" in result.stdout
        assert "Reclaiming database pages (VACUUM)" in result.stdout

    def test_lock_concurrency_error(self, temp_db):
        """Verify that lock file blocks execution when actively held."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        
        import msvcrt
        
        # Acquire lock in this process
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        
        try:
            result = subprocess.run(
                [PYTHON_EXEC, SCRIPT_PATH, "--dry-run", "--db-path", temp_db],
                env=env,
                capture_output=True,
                text=True
            )
            
            assert result.returncode != 0
            assert "Another instance" in result.stdout or "Another instance" in result.stderr
        finally:
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
            os.close(lock_fd)
            if os.path.exists(LOCK_FILE):
                try:
                    os.remove(LOCK_FILE)
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
