"""Universe Manager – SQLAlchemy models (universe.db)

独立した DB (universe.db) で銘柄マスタ・テーマ構成・ティッカー履歴を管理する。
既存の stocktool.db (symbols テーブル) には一切影響しない。
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, SmallInteger,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base

BaseUniverse = declarative_base()


class SymbolMaster(BaseUniverse):
    """銘柄マスタ – 全カテゴリ共通"""

    __tablename__ = "symbols_master"

    id         = Column(Integer, primary_key=True)
    ticker     = Column(String, nullable=False, index=True)
    exchange   = Column(String)
    name       = Column(String)
    category   = Column(String, nullable=False)    # 市場 / 指標 / セクタ / テーマ / 個別 / レバレッジ
    industry   = Column(String)                    # 業界（旧 asset_class）
    theme_type = Column(String)                    # etf / virtual / sector / NULL
    sector_etf = Column(String)                    # 親セクタETF（旧 tags）
    active     = Column(SmallInteger, default=1)
    source     = Column(String, default="manual")  # manual / spreadsheet / finviz
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_symbols_master_ticker_exchange"),
        Index("ix_symbols_master_active_category", "active", "category"),
    )


class ThemeMember(BaseUniverse):
    """テーマ ↔ 構成銘柄の N:M 関係"""

    __tablename__ = "theme_members"

    id             = Column(Integer, primary_key=True)
    theme_ticker   = Column(String, nullable=False, index=True)
    member_ticker  = Column(String, nullable=False, index=True)
    weight         = Column(Float, default=1.0)
    source         = Column(String, default="manual")  # manual / spreadsheet / finviz

    __table_args__ = (
        UniqueConstraint("theme_ticker", "member_ticker", name="uq_theme_members_pair"),
    )


class TickerHistory(BaseUniverse):
    """ティッカー変更履歴 (リネーム / 上場廃止等)"""

    __tablename__ = "ticker_history"

    id             = Column(Integer, primary_key=True)
    current_ticker = Column(String, nullable=False)
    old_ticker     = Column(String, nullable=False)
    old_exchange   = Column(String)
    changed_at     = Column(String, nullable=False)   # ISO date string
    reason         = Column(String)

    __table_args__ = (
        UniqueConstraint("old_ticker", "old_exchange", name="uq_ticker_history_old"),
    )
