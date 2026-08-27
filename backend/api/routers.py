from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, Column as SAColumn, select
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, re, logging, tomli
from db.database import get_db, engine
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning, PipelineMeta
from api import schemas

logger = logging.getLogger(__name__)
router = APIRouter()


def get_pipeline_lock_path() -> str:
    """パイプラインの排他ロックのパスを返す。

    `/system/health` はこのファイルのロック状態で「パイプライン実行中か」を判定する。
    関数に切り出しているのはテストから差し替えられるようにするため。
    直書きだと**テストが本番のロックファイルを参照・削除してしまい**、
    日次パイプラインの実行中はテストが必ず落ちる（2026-07-31 に実際に発生）。
    """
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(backend_dir)
    return os.path.join(project_root, "update_pipeline.lock")


@router.get("/ping")
def ping():
    return {"ping": "pong"}

@router.get("/system/info", response_model=schemas.SystemInfoResponse)
def get_system_info():
    """Return current system configuration details, primarily for DB verification."""
    from db import database
    # Access database.engine instead of a stale import
    full_path = str(database.engine.url.database) if database.engine else "unknown"
    db_name = os.path.basename(full_path)
    is_production = db_name == "stocktool.db"
    
    return schemas.SystemInfoResponse(
        db_path=full_path,
        db_name=db_name,
        is_production=is_production
    )

# Dependencies moved to api/deps.py (re-exported here for backward compatibility)
from api.deps import get_api_db, get_api_user_db

@router.get("/system/health", response_model=schemas.SystemHealthResponse)
def get_system_health(db: Session = Depends(get_api_db)):
    """Return system health details (freshness, integrity, pipeline status, preset validation)."""
    from datetime import datetime, timedelta
    import pytz
    
    # 1. Data Freshness
    t2_max = db.query(func.max(DailyPrice.date)).scalar()
    t3_max = db.query(func.max(Indicator.date)).scalar()
    t4_max = db.query(func.max(RelativeRank.date)).scalar()
    t5_max = db.query(func.max(MarketSignal.date)).scalar()
    
    spy_max = db.query(func.max(DailyPrice.date)).join(Symbol).filter(Symbol.ticker == "SPY").scalar()
    
    # Calculate delay days (business days)
    delay_days = None
    if spy_max:
        est = pytz.timezone("America/New_York")
        now_est = datetime.now(pytz.utc).astimezone(est).date()
        delay_days = 0
        current = spy_max + timedelta(days=1)
        while current <= now_est:
            if current.weekday() < 5:
                delay_days += 1
            current += timedelta(days=1)
            
    freshness = {
        "daily_prices": str(t2_max) if t2_max else None,
        "indicators": str(t3_max) if t3_max else None,
        "relative_ranks": str(t4_max) if t4_max else None,
        "market_signals": str(t5_max) if t5_max else None,
        "spy_latest": str(spy_max) if spy_max else None,
        "delay_days": delay_days
    }
    
    # 2. Data Integrity (T2 Count vs T3 Count on latest T2 date)
    t2_count = 0
    t3_count = 0
    is_consistent = True
    if t2_max:
        t2_count = db.query(func.count(DailyPrice.id)).filter(DailyPrice.date == t2_max).scalar() or 0
        t3_count = db.query(func.count(Indicator.id)).filter(Indicator.date == t2_max).scalar() or 0
        is_consistent = (t2_count == t3_count)
        
    overall_status = "healthy"
    if not is_consistent:
        overall_status = "error"
        
    integrity = {
        "latest_date": str(t2_max) if t2_max else None,
        "daily_prices_count": t2_count,
        "indicators_count": t3_count,
        "is_consistent": is_consistent
    }
    
    # 3. Pipeline Status (File Lock & Metadata)
    import msvcrt
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(backend_dir)   # 後段の TOML プリセット検証でも使う
    lock_file = get_pipeline_lock_path()

    is_running = False
    if os.path.exists(lock_file):
        try:
            fd = os.open(lock_file, os.O_RDWR)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except (IOError, OSError):
                is_running = True
            finally:
                os.close(fd)
        except (IOError, OSError):
            is_running = True
            
    meta = db.query(PipelineMeta).filter(PipelineMeta.id == 1).first()
    last_completed = meta.last_completed_at.isoformat() if meta and meta.last_completed_at else None
    last_spy = str(meta.last_spy_date) if meta and meta.last_spy_date else None
    
    pipeline_status = {
        "is_running": is_running,
        "last_completed_at": last_completed,
        "last_spy_date": last_spy
    }
    
    # 4. TOML Preset Validation (Using dummy DataFrames for speed)
    import tomli
    presets_file = os.path.join(project_root, "data", "screener_presets.toml")
    warnings = []
    is_valid = True
    if os.path.exists(presets_file):
        try:
            with open(presets_file, "rb") as f:
                presets = tomli.load(f)
            strategies = presets.get("rise", []) + presets.get("fall", [])
            
            import pandas as pd
            dp_cols = [c.key for c in DailyPrice.__table__.columns]
            ind_cols = [c.key for c in Indicator.__table__.columns]
            rank_cols = [c.key for c in RelativeRank.__table__.columns]
            
            dummy_dp = pd.DataFrame(columns=dp_cols)
            dummy_ind = pd.DataFrame(columns=ind_cols)
            dummy_rank = pd.DataFrame(columns=rank_cols)
            
            from backtest.backtest_runner import validate_strategies_config
            warnings = validate_strategies_config(
                strategies, 
                df_ind=dummy_ind, 
                df_prices=dummy_dp, 
                df_ranks=dummy_rank
            )
            is_valid = (len(warnings) == 0)
        except Exception as ve:
            warnings.append(f"Failed to read or validate presets TOML: {ve}")
            is_valid = False
    else:
        warnings.append("Screener presets TOML file not found.")
        is_valid = False
        
    if not is_valid:
        overall_status = "error"
        
    validation = {
        "presets_path": presets_file,
        "is_valid": is_valid,
        "warnings": warnings
    }
    
    # 5. Universe（IPO 追加候補の未レビュー件数）
    #
    # universe.db は別系統の DB なので、`get_api_db` のセッションでは引けない。
    # 小さなテーブルへの COUNT 1本なのでヘッダのポーリングに載せても軽い。
    # **ここで失敗しても health 全体は落とさない** — 候補件数が出ないことより、
    # 鮮度・整合性・パイプライン状態が見えなくなる方が困る。
    ipo_pending = 0
    try:
        from db.database_universe import get_universe_db
        from db.models_universe import IpoCandidate

        with get_universe_db() as udb:
            ipo_pending = (udb.query(func.count(IpoCandidate.id))
                              .filter(IpoCandidate.status == "pending").scalar()) or 0
    except Exception as e:  # noqa: BLE001 — universe.db 未初期化・テーブル未作成を許容
        logger.debug(f"IPO 候補件数の取得に失敗しました（health は継続）: {e}")

    return {
        "overall_status": overall_status,
        "data_freshness": freshness,
        "data_integrity": integrity,
        "pipeline_status": pipeline_status,
        "validation": validation,
        "universe": {"ipo_candidates_pending": ipo_pending},
    }



@router.get("/symbols", response_model=List[schemas.SymbolResponse])
def get_symbols(db: Session = Depends(get_api_db)):
    """
    T1: Get all registered active symbols
    """
    symbols = db.query(Symbol).filter(Symbol.active == 1).order_by(Symbol.category, Symbol.ticker).all()
    
    # 仮想シンボルを追加
    virtual_vxv_vix = Symbol(
        id=99999,
        ticker="^VXV_VIX",
        name="VXV/VIX Ratio",
        category="市場指標",
        active=1,
        tags=""
    )
    virtual_mkt_trend = Symbol(
        id=99998,
        ticker="^MKT_TREND",
        name="Market Trend Score",
        category="市場指標",
        active=1,
        tags=""
    )
    return [virtual_vxv_vix, virtual_mkt_trend] + symbols


# --- Backward-compatibility re-exports (moved to dedicated routers, audit D-1) ---
from api.chart_router import get_chart_data  # noqa: E402  (tests import this from api.routers)
