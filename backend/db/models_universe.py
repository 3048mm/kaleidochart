"""Universe Manager – SQLAlchemy models (universe.db)

独立した DB (universe.db) で銘柄マスタ・テーマ構成・ティッカー履歴を管理する。
既存の stocktool.db (symbols テーブル) には一切影響しない。
"""

from datetime import datetime
from sqlalchemy import (
    BigInteger, Column, DateTime, Float, Index, Integer, SmallInteger,
    String, Text, UniqueConstraint,
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

    # --- SEC EDGAR の安定キー（コーポレートアクション追随用） -----------------
    # ティッカーは変わるが、CIK / classId は変わらない。これを軸に
    # 改称・上場廃止を週次で検知する（`data_collection/sec_client.py`）。
    #   個別銘柄        … cik
    #   ETF・ファンド   … sec_class_id（CIK はトラスト単位で粗すぎる。
    #                     RSHO の CIK は Tema ETF Trust の13ファンドを含む）
    #   指数・仮想テーマ … SEC に実体が無いので両方 NULL（追跡対象外）
    cik            = Column(Integer, index=True)
    sec_class_id   = Column(String, index=True)
    sec_checked_at = Column(DateTime)              # 最後に SEC と突合した日時

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


class IpoCandidate(BaseUniverse):
    """IPO 銘柄の追加候補（レビュー待ちの銘柄プール）。

    SEC マスタと `symbols_master` の差分から週次で検知した新規上場銘柄を、
    人間がレビューして `symbols_master` へ採用するまでの待機場所。

    **`symbols_master.active` の第3の値にはしない。** `active` は実質 bool で、
    T1 同期・`detect_candidates()`・退役スクリプトが全て真偽値として読んでいる。
    第3の値を足すと全経路の洗い直しになる（計画書 §2.2）。

    **却下しても行は消さない。** `status='rejected'` で残しておけば
    画面のトグル1つで復活でき、追加コストがゼロで済む。

    検知ロジック: `data_collection/ipo_discovery.py`
    """

    __tablename__ = "ipo_candidates"

    id       = Column(Integer, primary_key=True)
    ticker   = Column(String, nullable=False, index=True)
    exchange = Column(String)                    # Yahoo の fullExchangeName
    name     = Column(String)
    cik      = Column(Integer, index=True)

    # Yahoo `firstTradeDate`。**上場日の正**（SEC は上場日を持たない）
    first_trade_date = Column(String)            # ISO date 文字列

    # 検知時点のスナップショット。レビュー時の判断材料であり、
    # 最新値を追い続ける必要はない（追うなら採用して T2 に載せる）
    market_cap  = Column(BigInteger)
    avg_volume  = Column(BigInteger)
    last_price  = Column(Float)

    # 企業概要。テーマのタグ付け判断に使う。`.info` が不安定なので NULL 可
    sector   = Column(String)
    industry = Column(String)
    summary  = Column(Text)
    website  = Column(String)

    # 'spac' / 'fund' のカンマ区切り。**除外ではなく分類**。
    # SPAC は合併後に実業会社へ変わるため、行を残して拾い直せるようにする
    flags = Column(String)

    status      = Column(String, nullable=False, default="pending")
    status_note = Column(String)
    reviewed_at = Column(DateTime)
    detected_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_ipo_candidates_ticker_exchange"),
        Index("ix_ipo_candidates_status", "status"),
    )
