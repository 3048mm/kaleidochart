import os
import sys
from datetime import datetime, date
import pytest
import msvcrt
import builtins
from unittest.mock import patch, mock_open
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend directory to sys.path
test_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.dirname(test_dir)
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, backend_dir)
sys.path.insert(0, project_root)

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, PipelineMeta
from api.routers import router
from api.deps import get_api_db

_engine = create_engine(
    "sqlite:///file:memdb_health?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

@pytest.fixture
def db_session():
    """Sets up an in-memory SQLite DB for testing."""
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()

# Mock open to avoid relying on actual screener_presets.toml in general tests
@pytest.fixture(autouse=True)
def mock_presets_toml():
    real_open = builtins.open
    real_exists = os.path.exists
    
    def my_open(file, *args, **kwargs):
        if "screener_presets.toml" in str(file):
            return mock_open(read_data=b"[[rise]]\nname='dummy_preset'\nmin_change_1d_pct=5.0\n")()
        return real_open(file, *args, **kwargs)
        
    def my_exists(path):
        if "screener_presets.toml" in str(path):
            return True
        return real_exists(path)

    with patch("builtins.open", new=my_open), \
         patch("os.path.exists", new=my_exists):
        yield

def test_get_system_health_tracer_bullet(db_session):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = lambda: db_session
    client = TestClient(app)
    
    response = client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "healthy"

def test_get_system_health_freshness(db_session):
    # Seed symbols
    spy = Symbol(id=1, ticker="SPY", exchange="US", name="SPY", category="市場", active=1)
    aapl = Symbol(id=2, ticker="AAPL", exchange="US", name="AAPL", category="個別", active=1)
    db_session.add_all([spy, aapl])
    db_session.commit()

    # Seed DailyPrice (T2)
    db_session.add_all([
        DailyPrice(symbol_id=1, date=date(2026, 7, 10), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000), # SPY
        DailyPrice(symbol_id=2, date=date(2026, 7, 9), open=200.0, high=201.0, low=199.0, close=200.0, volume=2000),  # AAPL
    ])
    
    # Seed Indicator (T3)
    db_session.add_all([
        Indicator(symbol_id=2, date=date(2026, 7, 8), sma_5=200.0)
    ])
    
    # Seed RelativeRank (T4)
    db_session.add_all([
        RelativeRank(symbol_id=2, date=date(2026, 7, 7), group_name="個別", rs_ratio_rank_e21=0.5)
    ])
    
    # Seed MarketSignal (T5)
    db_session.add_all([
        MarketSignal(date=date(2026, 7, 6), market_phase="BULL", distribution_days=0, market_trend_score=80.0, vxv_vix_ratio=1.1, is_distribution_day=0, follow_through_day=0)
    ])
    db_session.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = lambda: db_session
    client = TestClient(app)

    response = client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    
    freshness = data["data_freshness"]
    assert freshness["daily_prices"] == "2026-07-10"
    assert freshness["indicators"] == "2026-07-08"
    assert freshness["relative_ranks"] == "2026-07-07"
    assert freshness["market_signals"] == "2026-07-06"
    assert freshness["spy_latest"] == "2026-07-10"

def test_get_system_health_integrity_failure(db_session):
    # Seed symbols
    spy = Symbol(id=1, ticker="SPY", exchange="US", name="SPY", category="市場", active=1)
    aapl = Symbol(id=2, ticker="AAPL", exchange="US", name="AAPL", category="個別", active=1)
    db_session.add_all([spy, aapl])
    db_session.commit()

    # Seed DailyPrice (T2) on latest date 2026-07-10
    db_session.add_all([
        DailyPrice(symbol_id=1, date=date(2026, 7, 10), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000), # SPY
        DailyPrice(symbol_id=2, date=date(2026, 7, 10), open=200.0, high=201.0, low=199.0, close=200.0, volume=2000), # AAPL
    ])
    # Seed Indicator (T3) on latest date 2026-07-10 (only 1 indicator, so count mismatch: 2 vs 1)
    db_session.add(Indicator(symbol_id=1, date=date(2026, 7, 10), sma_5=100.0))
    db_session.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = lambda: db_session
    client = TestClient(app)

    response = client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    
    assert data["overall_status"] == "error"
    integrity = data["data_integrity"]
    assert integrity["latest_date"] == "2026-07-10"
    assert integrity["daily_prices_count"] == 2
    assert integrity["indicators_count"] == 1
    assert integrity["is_consistent"] is False

def test_get_system_health_pipeline_status(db_session, tmp_path, monkeypatch):
    """回帰: 本番の update_pipeline.lock を参照・削除しないこと。

    旧実装はロックパスがエンドポイント内に直書きで、テストが本番ロックを
    os.remove しようとしていた。日次パイプラインの実行中はロックが消せず
    `is_running` が True のままになり、テストが必ず落ちた（2026-07-31 発生）。
    さらに削除できてしまった場合は、実行中のパイプラインの排他が壊れる。
    """
    # Seed PipelineMeta
    meta = PipelineMeta(id=1, last_completed_at=datetime(2026, 7, 12, 12, 30, 0), last_spy_date=date(2026, 7, 10))
    db_session.add(meta)
    db_session.commit()

    # エンドポイントが見るロックをテスト専用のものへ差し替える
    lock_path = str(tmp_path / "test_pipeline.lock")
    import api.routers as routers_module
    monkeypatch.setattr(routers_module, "get_pipeline_lock_path", lambda: lock_path)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_api_db] = lambda: db_session
    client = TestClient(app)

    # 1. Test when pipeline is NOT running (lock file not locked)
    response = client.get("/api/system/health")
    assert response.status_code == 200
    data = response.json()
    status = data["pipeline_status"]
    assert status["is_running"] is False
    assert "2026-07-12" in status["last_completed_at"]
    assert status["last_spy_date"] == "2026-07-10"

    # 2. Test when pipeline IS running (lock file locked)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        response = client.get("/api/system/health")
        assert response.status_code == 200
        data = response.json()
        status = data["pipeline_status"]
        assert status["is_running"] is True
    finally:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)

def test_get_system_health_preset_validation(db_session):
    # Test invalid preset configuration
    invalid_toml = b"""
    [[rise]]
    name = "invalid_preset"
    min_invalid_column = 10
    """
    
    real_open = builtins.open
    def my_open_invalid(file, *args, **kwargs):
        if "screener_presets.toml" in str(file):
            return mock_open(read_data=invalid_toml)()
        return real_open(file, *args, **kwargs)
        
    # We patch open inside the test specifically to load the invalid TOML
    with patch("builtins.open", new=my_open_invalid):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_api_db] = lambda: db_session
        client = TestClient(app)

        response = client.get("/api/system/health")
        assert response.status_code == 200
        data = response.json()
        
        validation = data["validation"]
        assert validation["is_valid"] is False
        assert len(validation["warnings"]) > 0
        assert any("invalid_preset" in w for w in validation["warnings"])
