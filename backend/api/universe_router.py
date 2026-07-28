"""Universe Manager – CRUD API Router

銘柄マスタ (symbols_master) およびテーマ構成銘柄 (theme_members) の
CRUD エンドポイントを提供する。
"""

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from data_collection.symbol_classify import derive_theme_type
from db.database_universe import get_universe_db, get_universe_write_db
from db.models_universe import SymbolMaster, ThemeMember

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/universe", tags=["universe"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SymbolMasterOut(BaseModel):
    id: int
    ticker: str
    exchange: Optional[str] = None
    name: Optional[str] = None
    category: str
    industry: Optional[str] = None
    theme_type: Optional[str] = None
    sector_etf: Optional[str] = None
    active: int = 1
    source: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    member_count: Optional[int] = 0

    class Config:
        from_attributes = True


class SymbolMasterCreate(BaseModel):
    ticker: str
    exchange: Optional[str] = None
    name: Optional[str] = None
    category: str = "個別"
    industry: Optional[str] = None
    theme_type: Optional[str] = None
    sector_etf: Optional[str] = None
    active: int = 1


class SymbolMasterUpdate(BaseModel):
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    industry: Optional[str] = None
    theme_type: Optional[str] = None
    sector_etf: Optional[str] = None
    active: Optional[int] = None


class ThemeMemberOut(BaseModel):
    id: int
    theme_ticker: str
    member_ticker: str
    weight: float = 1.0
    source: Optional[str] = None

    class Config:
        from_attributes = True


class ThemeMemberCreate(BaseModel):
    member_ticker: str
    weight: float = 1.0


class PaginatedSymbols(BaseModel):
    items: List[SymbolMasterOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class StatsOut(BaseModel):
    total_symbols: int
    by_category: dict
    total_themes: int
    total_theme_members: int
    last_updated: Optional[datetime] = None


class ImportRequest(BaseModel):
    spreadsheet_url: str
    mode: str = "upsert"  # "upsert" or "replace"


class ExportRequest(BaseModel):
    spreadsheet_url: str


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _get_read_db():
    with get_universe_db() as db:
        yield db


def _get_write_db():
    with get_universe_write_db() as db:
        yield db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_theme_type(exchange: str | None, category: str, ticker: str | None = None) -> str | None:
    """theme_type の判定は data_collection.symbol_classify に一元化している。

    後方互換のため本名の薄いラッパーとして残す。
    """
    return derive_theme_type(exchange, category, ticker)


# ---------------------------------------------------------------------------
# Symbols CRUD
# ---------------------------------------------------------------------------

@router.get("/symbols", response_model=PaginatedSymbols)
def list_symbols(
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search ticker / name / industry"),
    include_inactive: bool = Query(False, description="Include inactive symbols"),
    sort_by: str = Query("ticker", description="Sort column"),
    sort_dir: str = Query("asc", description="asc or desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(_get_read_db),
):
    # Sanitize Query defaults if function is called directly
    if not isinstance(category, str):
        category = None
    if not isinstance(search, str):
        search = None
    if not isinstance(include_inactive, bool):
        include_inactive = False
    if not isinstance(sort_by, str):
        sort_by = "ticker"
    if not isinstance(sort_dir, str):
        sort_dir = "asc"
    if not isinstance(page, int):
        page = 1
    if not isinstance(page_size, int):
        page_size = 50

    query = db.query(SymbolMaster)

    # Filter: active
    if not include_inactive:
        query = query.filter(SymbolMaster.active == 1)

    # Filter: category
    #   「テーマ」タブのみ、テーマ本体に加えて theme_members の親になっている銘柄と
    #   セクタETF の BLOK をマルチヒット表示する。
    #   かつて IBIT / CPER をティッカー直指定で『指標』タブにも出していたが、
    #   GBTC / CPER を正式に category='指標' へ移したため撤廃した（W1 / W2b）。
    if category:
        if category == "テーマ":
            theme_parent_subq = db.query(ThemeMember.theme_ticker).distinct()
            query = query.filter(
                SymbolMaster.category != "個別",
                or_(
                    SymbolMaster.category == "テーマ",
                    SymbolMaster.ticker == "BLOK",
                    SymbolMaster.ticker.in_(theme_parent_subq),
                )
            )
        else:
            query = query.filter(SymbolMaster.category == category)

    # Filter: search
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                SymbolMaster.ticker.ilike(pattern),
                SymbolMaster.name.ilike(pattern),
                SymbolMaster.industry.ilike(pattern),
            )
        )

    # Count
    total = query.count()

    # Sort
    if sort_by == "member_count":
        theme_subq = (
            db.query(ThemeMember.theme_ticker, func.count(ThemeMember.id).label("tm_cnt"))
            .group_by(ThemeMember.theme_ticker)
            .subquery()
        )
        sector_subq = (
            db.query(SymbolMaster.sector_etf, func.count(SymbolMaster.id).label("sec_cnt"))
            .filter(SymbolMaster.active == 1)
            .group_by(SymbolMaster.sector_etf)
            .subquery()
        )
        cnt_expr = func.coalesce(theme_subq.c.tm_cnt, sector_subq.c.sec_cnt, 0)
        query = query.outerjoin(theme_subq, SymbolMaster.ticker == theme_subq.c.theme_ticker)
        query = query.outerjoin(sector_subq, SymbolMaster.ticker == sector_subq.c.sector_etf)

        if sort_dir.lower() == "desc":
            query = query.order_by(cnt_expr.desc(), SymbolMaster.ticker.asc())
        else:
            query = query.order_by(cnt_expr.asc(), SymbolMaster.ticker.asc())
    else:
        sort_column = getattr(SymbolMaster, sort_by, SymbolMaster.ticker)
        if sort_dir.lower() == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

    # Paginate
    total_pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    # Collect member counts for items in current page
    tickers = [item.ticker for item in items]
    theme_counts = {}
    sector_counts = {}
    if tickers:
        theme_counts = dict(
            db.query(ThemeMember.theme_ticker, func.count(ThemeMember.id))
            .filter(ThemeMember.theme_ticker.in_(tickers))
            .group_by(ThemeMember.theme_ticker)
            .all()
        )
        sector_counts = dict(
            db.query(SymbolMaster.sector_etf, func.count(SymbolMaster.id))
            .filter(SymbolMaster.sector_etf.in_(tickers), SymbolMaster.active == 1)
            .group_by(SymbolMaster.sector_etf)
            .all()
        )

    out_items = []
    for item in items:
        cnt = theme_counts.get(item.ticker, 0) or sector_counts.get(item.ticker, 0)
        out_item = SymbolMasterOut.model_validate(item)
        out_item.member_count = cnt
        out_items.append(out_item)

    return PaginatedSymbols(
        items=out_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/symbols", response_model=SymbolMasterOut, status_code=201)
def create_symbol(
    body: SymbolMasterCreate,
    db: Session = Depends(_get_write_db),
):
    """銘柄追加"""
    existing = (
        db.query(SymbolMaster)
        .filter(SymbolMaster.ticker == body.ticker, SymbolMaster.exchange == body.exchange)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Symbol {body.ticker} ({body.exchange}) already exists")

    sym = SymbolMaster(
        ticker=body.ticker,
        exchange=body.exchange,
        name=body.name,
        category=body.category,
        industry=body.industry,
        theme_type=_derive_theme_type(body.exchange, body.category, body.ticker),
        sector_etf=body.sector_etf,
        active=body.active,
        source="manual",
    )
    db.add(sym)
    db.flush()
    db.refresh(sym)
    return sym


@router.put("/symbols/{symbol_id}", response_model=SymbolMasterOut)
def update_symbol(
    symbol_id: int,
    body: SymbolMasterUpdate,
    db: Session = Depends(_get_write_db),
):
    """銘柄編集 (インライン編集)

    ticker を変更した場合:
      - theme_members の theme_ticker / member_ticker を連動更新
      - ticker_history に旧ティッカーを記録
    """
    from db.models_universe import TickerHistory
    from datetime import date as dt_date

    sym = db.query(SymbolMaster).filter(SymbolMaster.id == symbol_id).first()
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol id={symbol_id} not found")

    update_data = body.model_dump(exclude_unset=True)
    old_ticker = sym.ticker

    # Apply updates
    for key, value in update_data.items():
        setattr(sym, key, value)

    # Cascade: ticker rename → theme_members + ticker_history
    new_ticker = update_data.get("ticker")
    if new_ticker and new_ticker != old_ticker:
        # Update theme_members where this was a theme
        db.query(ThemeMember).filter(
            ThemeMember.theme_ticker == old_ticker
        ).update({ThemeMember.theme_ticker: new_ticker}, synchronize_session="fetch")

        # Update theme_members where this was a member
        db.query(ThemeMember).filter(
            ThemeMember.member_ticker == old_ticker
        ).update({ThemeMember.member_ticker: new_ticker}, synchronize_session="fetch")

        # Record in ticker_history
        history = TickerHistory(
            current_ticker=new_ticker,
            old_ticker=old_ticker,
            old_exchange=sym.exchange,
            changed_at=str(dt_date.today()),
            reason="renamed via Universe Manager",
        )
        db.add(history)

    # Auto-derive theme_type when exchange / category / ticker changes
    if "exchange" in update_data or "category" in update_data or "ticker" in update_data:
        sym.theme_type = _derive_theme_type(sym.exchange, sym.category, sym.ticker)

    db.flush()
    db.refresh(sym)
    return sym


@router.delete("/symbols/{symbol_id}")
def delete_symbol(
    symbol_id: int,
    db: Session = Depends(_get_write_db),
):
    """銘柄削除 (soft delete: active=0)"""
    sym = db.query(SymbolMaster).filter(SymbolMaster.id == symbol_id).first()
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol id={symbol_id} not found")

    sym.active = 0
    db.flush()
    return {"detail": f"Symbol {sym.ticker} deactivated (soft delete)"}


# ---------------------------------------------------------------------------
# Theme Members CRUD
# ---------------------------------------------------------------------------

@router.get("/themes/{ticker}/members", response_model=List[ThemeMemberOut])
def get_theme_members(
    ticker: str,
    db: Session = Depends(_get_read_db),
):
    """テーマの構成銘柄一覧"""
    members = (
        db.query(ThemeMember)
        .filter(ThemeMember.theme_ticker == ticker)
        .order_by(ThemeMember.member_ticker)
        .all()
    )
    return members


@router.post("/themes/{ticker}/members", response_model=ThemeMemberOut, status_code=201)
def add_theme_member(
    ticker: str,
    body: ThemeMemberCreate,
    db: Session = Depends(_get_write_db),
):
    """テーマに構成銘柄を追加"""
    existing = (
        db.query(ThemeMember)
        .filter(
            ThemeMember.theme_ticker == ticker,
            ThemeMember.member_ticker == body.member_ticker,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{body.member_ticker} is already a member of {ticker}",
        )

    tm = ThemeMember(
        theme_ticker=ticker,
        member_ticker=body.member_ticker,
        weight=body.weight,
        source="manual",
    )
    db.add(tm)
    db.flush()
    db.refresh(tm)
    return tm


@router.get("/symbols/{ticker}/themes", response_model=List[ThemeMemberOut])
def get_symbol_themes(
    ticker: str,
    db: Session = Depends(_get_read_db),
):
    """個別銘柄が所属するテーマ一覧（逆引き）"""
    memberships = (
        db.query(ThemeMember)
        .filter(ThemeMember.member_ticker == ticker)
        .order_by(ThemeMember.theme_ticker)
        .all()
    )
    return memberships


@router.get("/themes-batch")
def get_themes_batch(
    tickers: str = Query(..., description="Comma-separated member tickers"),
    db: Session = Depends(_get_read_db),
):
    """複数銘柄のテーマ所属を一括取得。{ ticker: [theme_ticker, ...] } を返す。"""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {}

    members = (
        db.query(ThemeMember)
        .filter(ThemeMember.member_ticker.in_(ticker_list))
        .all()
    )

    result: dict[str, list[str]] = {}
    for m in members:
        result.setdefault(m.member_ticker, []).append(m.theme_ticker)

    # Sort each list for consistency
    for k in result:
        result[k].sort()

    return result


@router.delete("/themes/{ticker}/members/{member_ticker}")
def remove_theme_member(
    ticker: str,
    member_ticker: str,
    db: Session = Depends(_get_write_db),
):
    """テーマから構成銘柄を削除"""
    tm = (
        db.query(ThemeMember)
        .filter(
            ThemeMember.theme_ticker == ticker,
            ThemeMember.member_ticker == member_ticker,
        )
        .first()
    )
    if not tm:
        raise HTTPException(
            status_code=404,
            detail=f"{member_ticker} is not a member of {ticker}",
        )

    db.delete(tm)
    db.flush()
    return {"detail": f"Removed {member_ticker} from {ticker}"}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(_get_read_db)):
    """統計情報"""
    total = db.query(func.count(SymbolMaster.id)).scalar() or 0

    # Category breakdown
    cat_rows = (
        db.query(SymbolMaster.category, func.count(SymbolMaster.id))
        .group_by(SymbolMaster.category)
        .all()
    )
    by_category = {cat: cnt for cat, cnt in cat_rows}

    total_themes = by_category.get("テーマ", 0)
    total_members = db.query(func.count(ThemeMember.id)).scalar() or 0
    last_updated = db.query(func.max(SymbolMaster.updated_at)).scalar()

    return StatsOut(
        total_symbols=total,
        by_category=by_category,
        total_themes=total_themes,
        total_theme_members=total_members,
        last_updated=last_updated,
    )


# ---------------------------------------------------------------------------
# Spreadsheet Import Endpoints
# ---------------------------------------------------------------------------

@router.post("/import/preview")
def import_preview(
    body: ImportRequest,
    db: Session = Depends(_get_read_db),
):
    """Google スプレッドシート URL からデータをパースし、差分プレビューを返却する"""
    from data_collection.sheet_importer import parse_symbols_from_url, preview_import_diff
    try:
        symbols, members = parse_symbols_from_url(body.spreadsheet_url)
        diff = preview_import_diff(db, symbols, members, mode=body.mode)
        return diff
    except (ValueError, PermissionError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Import preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析エラー: {str(e)}")


@router.post("/import/execute")
def import_execute(
    body: ImportRequest,
    db: Session = Depends(_get_write_db),
):
    """Google スプレッドシート URL からデータを取得し、universe.db に本反映する"""
    from data_collection.sheet_importer import parse_symbols_from_url, execute_import_diff
    try:
        symbols, members = parse_symbols_from_url(body.spreadsheet_url)
        result = execute_import_diff(db, symbols, members, mode=body.mode)
        return result
    except (ValueError, PermissionError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Import execute error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"インポート実行エラー: {str(e)}")


@router.post("/export/spreadsheet")
def export_spreadsheet(
    body: ExportRequest,
    db: Session = Depends(_get_read_db),
):
    """universe.db の最新データを Google スプレッドシートへ書き出す"""
    from data_collection.sheet_exporter import export_universe_to_spreadsheet
    try:
        result = export_universe_to_spreadsheet(db, body.spreadsheet_url)
        return result
    except (ValueError, FileNotFoundError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Export spreadsheet error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"エクスポートエラー: {str(e)}")
