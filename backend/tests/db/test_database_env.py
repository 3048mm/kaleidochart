"""
Tests for database initialization path overrides based on STOCKTOOL_ENV.

Verifies that when STOCKTOOL_ENV is set to 'sandbox' or 'test', the target database paths
for both system and user databases are overridden to safer, isolated locations automatically.
"""
import os
import pytest
from backend.db.database import init_db, get_active_db_path
from backend.db.database_user import init_user_db, get_active_user_db_path


class TestDatabaseEnvOverrides:
    """TDD Phase 1: Environment-based database path configuration overrides."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        """Ensure no pre-existing environment overrides pollute tests."""
        monkeypatch.delenv("STOCKTOOL_ENV", raising=False)
        monkeypatch.delenv("STOCKTOOL_DB_PATH", raising=False)
        monkeypatch.delenv("STOCKTOOL_USER_DB_PATH", raising=False)

    def test_no_env_uses_passed_path(self):
        """When no env vars are defined, it should use the path passed directly."""
        target_db = "data/dummy_stocktool.db"
        target_user = "data/dummy_user_data.db"
        
        init_db(target_db)
        init_user_db(target_user)
        
        assert get_active_db_path().endswith("dummy_stocktool.db")
        assert get_active_user_db_path().endswith("dummy_user_data.db")

    def test_stocktool_env_sandbox_override(self, monkeypatch):
        """STOCKTOOL_ENV=sandbox should override paths to sandbox folder."""
        monkeypatch.setenv("STOCKTOOL_ENV", "sandbox")
        
        init_db("data/stocktool.db")
        init_user_db("data/user_data.db")
        
        active_db = get_active_db_path().replace("\\", "/")
        active_user = get_active_user_db_path().replace("\\", "/")
        
        assert active_db.endswith("data/sandbox/stocktool.db")
        assert active_user.endswith("data/sandbox/user_data.db")

    def test_stocktool_env_test_override(self, monkeypatch):
        """STOCKTOOL_ENV=test should override paths to test folder."""
        monkeypatch.setenv("STOCKTOOL_ENV", "test")
        
        init_db("data/stocktool.db")
        init_user_db("data/user_data.db")
        
        active_db = get_active_db_path().replace("\\", "/")
        active_user = get_active_user_db_path().replace("\\", "/")
        
        assert active_db.endswith("data/test/stocktool.db")
        assert active_user.endswith("data/test/user_data.db")

    def test_legacy_path_overrides_still_win_if_env_absent(self, monkeypatch):
        """Legacy direct overrides still work if STOCKTOOL_ENV is not set."""
        monkeypatch.setenv("STOCKTOOL_DB_PATH", "data/custom_path.db")
        monkeypatch.setenv("STOCKTOOL_USER_DB_PATH", "data/custom_user.db")
        
        init_db("data/stocktool.db")
        init_user_db("data/user_data.db")
        
        active_db = get_active_db_path().replace("\\", "/")
        active_user = get_active_user_db_path().replace("\\", "/")
        
        assert active_db.endswith("data/custom_path.db")
        assert active_user.endswith("data/custom_user.db")
