import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

import sys
import os

# プロジェクトのルートとbackendディレクトリをパスに追加 (tests -> pipeline -> backend -> project_root と4段階上に遡る)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
backend_dir = os.path.join(project_root, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from db.models import Base, Symbol, DailyPrice, FxRate
from db.database import init_db, SessionLocal

# テスト用のデータベース（Sandbox）のパス
SANDBOX_DB_PATH = os.path.join(project_root, "data", "stocktool_sandbox.db")
engine = create_engine(f"sqlite:///{SANDBOX_DB_PATH}")
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    """Setup and teardown a clean database session for each test."""
    # sandbox.db のテーブルをリセット・作成
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================================
# TEST-A: fx_rates テーブルの生成と制約の検証
# ============================================================
def test_fx_rates_schema_and_constraints(db_session):
    """TEST-A: FxRate テーブルが正しく生成され、一意制約が効くことの検証"""
    # 1. 正常なレコードのインサート
    rate1 = FxRate(currency_pair="USD/JPY", date=date(2026, 5, 26), rate=155.5)
    db_session.add(rate1)
    db_session.commit()
    
    # 2. 保存されたレコードの確認
    saved = db_session.query(FxRate).filter_by(currency_pair="USD/JPY", date=date(2026, 5, 26)).first()
    assert saved is not None
    assert saved.rate == 155.5
    
    # 3. 同一の通貨ペア・日付での重複インサート（一意制約エラーの検証）
    rate_dup = FxRate(currency_pair="USD/JPY", date=date(2026, 5, 26), rate=156.0)
    db_session.add(rate_dup)
    
    with pytest.raises(IntegrityError):
        db_session.commit()
    
    db_session.rollback()

# ============================================================
# TEST-B: 既存 JPY=X データの移行ロジックの検証
# ============================================================
def test_fx_rates_data_migration(db_session):
    """TEST-B: 既存の daily_prices から JPY=X データを fx_rates へ移行できることの検証"""
    # 1. ダミーの為替シンボルと価格を daily_prices に追加
    jpy_symbol = Symbol(ticker="JPY=X", name="USD/JPY Forex", category="市場", active=1)
    db_session.add(jpy_symbol)
    db_session.commit()
    
    price1 = DailyPrice(symbol_id=jpy_symbol.id, date=date(2026, 5, 25), close=154.2, open=154.0, high=154.5, low=153.8, volume=0)
    price2 = DailyPrice(symbol_id=jpy_symbol.id, date=date(2026, 5, 26), close=155.5, open=155.0, high=155.8, low=154.9, volume=0)
    db_session.add_all([price1, price2])
    db_session.commit()
    
    # 2. 移行処理を実行 (未実装のため、後で実装する移行関数を呼び出す)
    from scripts.migrate_fx_rates import migrate_fx_rates
    success = migrate_fx_rates(db_session)
    assert success is True
    
    # 3. fx_rates テーブルにデータが正しくコピーされたことを検証
    migrated_prices = db_session.query(FxRate).order_by(FxRate.date).all()
    assert len(migrated_prices) == 2
    assert migrated_prices[0].currency_pair == "USD/JPY"
    assert migrated_prices[0].date == date(2026, 5, 25)
    assert migrated_prices[0].rate == 154.2
    assert migrated_prices[1].rate == 155.5
    
    # 4. 旧 daily_prices から JPY=X レコードが削除されていることを検証
    old_prices = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == jpy_symbol.id).all()
    assert len(old_prices) == 0

# ============================================================
# TEST-C: ポートフォリオ機能の新為替テーブル参照の検証
# ============================================================
def test_portfolio_fx_rate_lookup(db_session):
    """TEST-C: portfolio_service 内の新為替レート取得ロジックが機能することの検証"""
    # 1. fx_rates テーブルに為替レートを投入
    rate_record = FxRate(currency_pair="USD/JPY", date=date(2026, 5, 26), rate=155.5)
    db_session.add(rate_record)
    db_session.commit()
    
    # 2. portfolio_service 内の新為替レート取得関数を呼び出して検証
    # (後で修正する portfolio_service.py の get_historical_fx_rate を呼び出す)
    from api.portfolio_service import get_historical_fx_rate
    
    rate = get_historical_fx_rate(db_session, date(2026, 5, 26))
    assert rate == 155.5
    
    # 存在しない日付の場合は、直近の過去日付のレートが返るか、またはデフォルト値が返ることを検証
    rate_future = get_historical_fx_rate(db_session, date(2026, 5, 27))
    assert rate_future == 155.5  # 直近過去日の 155.5 が返るべき (Fallback)

# ============================================================
# TEST-D: 為替の独立フェッチ機能 (T2拡張) の検証
# ============================================================
def test_fx_rate_independent_fetching(db_session):
    """TEST-D: 日次バッチとは独立した為替レートフェッチ・更新ロジックの検証"""
    # 1. 為替の独立フェッチ同期関数を実行
    from pipeline.phases.t2_prices import sync_fx_rates
    
    # yfinance へのアクセスをテストするため、モックを使わずにフェッチ（あるいはskip_fetch=Trueでモックテスト）
    # ここでは、skip_fetch=True の時にダミーデータが保存されるかテスト
    success = sync_fx_rates(db_session, skip_fetch=True)
    assert success is True
    
    # 2. レコードが正しく FxRate に入っていることを検証
    saved = db_session.query(FxRate).filter_by(currency_pair="USD/JPY").first()
    assert saved is not None
    assert saved.rate > 0.0
