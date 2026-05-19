# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import json
import pytest
from datetime import date, timedelta
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend directory is in sys.path and backend/tests is removed to avoid 'indicators' import collision
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path = [p for p in sys.path if not p.endswith('backend\\tests') and not p.endswith('backend/tests')]
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
else:
    sys.path.remove(_backend_dir)
    sys.path.insert(0, _backend_dir)

from db.models import Base, Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
# We import orchestrator's functions
# build_all_virtual_indexes_prices should fail to import or fail when run in Red Phase
from pipeline.orchestrator import (
    build_virtual_index_prices, 
    build_all_virtual_indexes_prices
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # 1. Setup mock symbols
    # Stocks
    stock1 = Symbol(id=1, ticker="AAPL", exchange="NASDAQ", category="個別", theme_type="stock", active=1)
    stock2 = Symbol(id=2, ticker="MSFT", exchange="NASDAQ", category="個別", theme_type="stock", active=1)
    stock3 = Symbol(id=3, ticker="GOOG", exchange="NASDAQ", category="個別", theme_type="stock", active=1)
    
    # Virtual Themes (tickers surrounded by underscores)
    v_theme1 = Symbol(id=101, ticker="_TECH_", exchange="VIRTUAL", category="テーマ", theme_type="virtual", active=1)
    v_theme2 = Symbol(id=102, ticker="_PHNC_", exchange="VIRTUAL", category="テーマ", theme_type="virtual", active=1)
    
    session.add_all([stock1, stock2, stock3, v_theme1, v_theme2])
    session.commit()
    
    # 2. Setup mock Theme Constituents
    # _TECH_: AAPL (1), MSFT (2)
    session.add(ThemeConstituent(theme_id=101, symbol_id=1, weight=0.5))
    session.add(ThemeConstituent(theme_id=101, symbol_id=2, weight=0.5))
    
    # _PHNC_: MSFT (2), GOOG (3)
    session.add(ThemeConstituent(theme_id=102, symbol_id=2, weight=0.5))
    session.add(ThemeConstituent(theme_id=102, symbol_id=3, weight=0.5))
    session.commit()
    
    # 3. Setup Daily Prices for stocks
    base_date = date(2026, 5, 1)
    # 5 days of data
    for i in range(5):
        d = base_date + timedelta(days=i)
        # AAPL (1): up 1% each day
        session.add(DailyPrice(symbol_id=1, date=d, open=100.0 * (1.01**i), high=101.0 * (1.01**i), low=99.0 * (1.01**i), close=100.0 * (1.01**i), volume=1000))
        # MSFT (2): up 2% each day
        session.add(DailyPrice(symbol_id=2, date=d, open=200.0 * (1.02**i), high=202.0 * (1.02**i), low=198.0 * (1.02**i), close=200.0 * (1.02**i), volume=1500))
        # GOOG (3): down 0.5% each day
        session.add(DailyPrice(symbol_id=3, date=d, open=150.0 * (0.995**i), high=151.0 * (0.995**i), low=149.0 * (0.995**i), close=150.0 * (0.995**i), volume=800))
    session.commit()
    
    return session


def test_import_and_existence():
    """TDD Red Phase validation: Check that the speedup functions exist."""
    assert build_virtual_index_prices is not None, "Original build function must exist"
    assert build_all_virtual_indexes_prices is not None, "Speedup function build_all_virtual_indexes_prices must be implemented"


def test_bulk_preloading_integrity(db_session):
    """
    テスト1 (一括ロードの整合性):
    新ロジックの結果が、従来の直列合成と小数点以下第6位まで完全に一致することを確認。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [
        {"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"},
        {"ticker": "_PHNC_", "exchange": "VIRTUAL", "theme_type": "virtual"}
    ]
    symbol_id_map = {
        ("_TECH_", "VIRTUAL"): 101,
        ("_PHNC_", "VIRTUAL"): 102
    }
    
    # 1. Run traditional serial calculation
    traditional_df1 = build_virtual_index_prices(db_session, 101)
    traditional_df2 = build_virtual_index_prices(db_session, 102)
    
    # 2. Run new bulk preloaded speedup calculation
    # We pass temporary json path for hash checks
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
    
    try:
        # The new bulk speedup calculation runs for all virtual indexes at once
        build_all_virtual_indexes_prices(
            db=db_session, 
            virtual_items=virtual_items, 
            symbol_id_map=symbol_id_map, 
            hash_file_path=hash_file_path
        )
        
        # 3. Read newly inserted daily prices from DB and verify matching
        new_prices1 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()
        new_prices2 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 102).order_by(DailyPrice.date).all()
        
        # Verify count
        assert len(new_prices1) == len(traditional_df1)
        assert len(new_prices2) == len(traditional_df2)
        
        # Verify close prices matching up to 6 decimals
        for i, row in traditional_df1.iterrows():
            assert abs(new_prices1[i].close - row['close']) < 1e-6
            
        for i, row in traditional_df2.iterrows():
            assert abs(new_prices2[i].close - row['close']) < 1e-6
            
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_regression_with_existing_db_data(db_session):
    """
    テスト1.1 (修正前既存DBデータとの期待値検証):
    リファクタ前に実際にDBに書き込まれている（従来のロジックで書き込まれた）値と、
    リファクタ後の新ロジックによる再合成・書き込み結果が完全に一致することを確認する。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [
        {"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"},
        {"ticker": "_PHNC_", "exchange": "VIRTUAL", "theme_type": "virtual"}
    ]
    symbol_id_map = {
        ("_TECH_", "VIRTUAL"): 101,
        ("_PHNC_", "VIRTUAL"): 102
    }
    
    # 1. 従来のロジックで算出し、実際にDBに一度書き込む（これが「修正前のDBの値」にあたる）
    traditional_df1 = build_virtual_index_prices(db_session, 101)
    traditional_df2 = build_virtual_index_prices(db_session, 102)
    
    db_session.bulk_save_objects([DailyPrice(symbol_id=101, date=row['date'], open=row['open'], high=row['high'], low=row['low'], close=row['close'], volume=0) for _, row in traditional_df1.iterrows()])
    db_session.bulk_save_objects([DailyPrice(symbol_id=102, date=row['date'], open=row['open'], high=row['high'], low=row['low'], close=row['close'], volume=0) for _, row in traditional_df2.iterrows()])
    db_session.commit()
    
    # 2. 修正前のDBから期待値を一時退避する
    expected_prices1 = [{"date": p.date, "close": p.close} for p in db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()]
    expected_prices2 = [{"date": p.date, "close": p.close} for p in db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 102).order_by(DailyPrice.date).all()]
    
    # 3. DBをクリアする（新ロジックによる再合成の準備）
    db_session.query(DailyPrice).filter(DailyPrice.symbol_id.in_([101, 102])).delete()
    db_session.commit()
    
    # 4. 新ロジックを実行
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        build_all_virtual_indexes_prices(
            db=db_session, 
            virtual_items=virtual_items, 
            symbol_id_map=symbol_id_map, 
            hash_file_path=hash_file_path
        )
        
        # 5. 退避した期待値と新ロジックで書き込まれた実DB値を完全一致アサーション
        new_prices1 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()
        new_prices2 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 102).order_by(DailyPrice.date).all()
        
        assert len(new_prices1) == len(expected_prices1)
        assert len(new_prices2) == len(expected_prices2)
        
        for i in range(len(expected_prices1)):
            assert new_prices1[i].date == expected_prices1[i]["date"]
            assert abs(new_prices1[i].close - expected_prices1[i]["close"]) < 1e-6
            
        for i in range(len(expected_prices2)):
            assert new_prices2[i].date == expected_prices2[i]["date"]
            assert abs(new_prices2[i].close - expected_prices2[i]["close"]) < 1e-6
            
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_full_rebuild_mode(db_session):
    """
    テスト2 (フルリビルドモード):
    過去データが存在しない場合、基準値 1000.0 から全期間が正しく合成・保存されること。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        build_all_virtual_indexes_prices(
            db=db_session, 
            virtual_items=virtual_items, 
            symbol_id_map=symbol_id_map, 
            hash_file_path=hash_file_path
        )
        
        prices = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()
        assert len(prices) == 4 # 5 days of base prices means 4 return intervals
        
        # Verify starting price calculation
        # The first synthetic close is 1000 * (1 + avg_ret)
        # AAPL close returns: day1->day2 is +1%, MSFT day1->day2 is +2%. Average = +1.5%
        # So synthetic close for day 2 (the first return date) must be 1000.0 * 1.015 = 1015.0
        assert abs(prices[0].close - 1015.0000) < 1e-4
        
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_incremental_mode(db_session):
    """
    テスト3 (差分更新モード):
    すでに価格データが存在し、構成銘柄に1日分の新規価格が追加された場合、
    差分更新（インクリメンタルモード）が走り、差分日程のみ追加合成され、
    過去の価格データは一切破壊されないこと。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        # 1. 最初の計算 (全期間分) -> ハッシュが記録される
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # 退避して検証用にする
        initial_prices = {p.date: p.close for p in db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).all()}
        
        # 2. AAPL と MSFT に 1日分 (6日目) のデータを投入する
        new_date = date(2026, 5, 6)
        # AAPL (1): close is 100.0 * (1.01**4) * 1.01 (1% up)
        aapl_last_close = db_session.query(DailyPrice.close).filter(DailyPrice.symbol_id == 1, DailyPrice.date == date(2026, 5, 5)).scalar()
        msft_last_close = db_session.query(DailyPrice.close).filter(DailyPrice.symbol_id == 2, DailyPrice.date == date(2026, 5, 5)).scalar()
        
        db_session.add(DailyPrice(symbol_id=1, date=new_date, open=aapl_last_close*1.01, high=aapl_last_close*1.01, low=aapl_last_close*1.01, close=aapl_last_close*1.01, volume=1000))
        db_session.add(DailyPrice(symbol_id=2, date=new_date, open=msft_last_close*1.02, high=msft_last_close*1.02, low=msft_last_close*1.02, close=msft_last_close*1.02, volume=1500))
        db_session.commit()
        
        # 3. 2回目の計算 (差分更新モードがトリガーされるべき)
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # 4. 検証
        updated_prices = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()
        assert len(updated_prices) == 5 # 5 intervals now
        
        # 過去のデータが破壊されていないことを保証
        for d, expected_close in initial_prices.items():
            db_p = next(p for p in updated_prices if p.date == d)
            assert abs(db_p.close - expected_close) < 1e-6
            
        # 新しい日程の価格が正しく計算されているか
        # 最終日の終値に (1.01 + 1.02)/2 = 1.015 倍したものであるはず
        last_synth_close_5 = initial_prices[date(2026, 5, 5)]
        expected_new_close = last_synth_close_5 * 1.015
        new_synth = next(p for p in updated_prices if p.date == new_date)
        assert abs(new_synth.close - expected_new_close) < 1e-4
        
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_hash_mismatch_triggers_full_rebuild(db_session):
    """
    テスト4 (構成銘柄変更の検知と自動リビルド):
    構成銘柄が変更されハッシュが不一致になった場合、自動的にフルリビルドが走り、
    過去全期間が新しい構成銘柄ベースで再合成されること。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        # 1. 最初の計算 (構成: AAPL + MSFT)
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # AAPL+MSFT構成での最終日 (5/5) の合成終値を記録
        old_close = db_session.query(DailyPrice.close).filter(DailyPrice.symbol_id == 101, DailyPrice.date == date(2026, 5, 5)).scalar()
        
        # 2. 構成を変更する (_TECH_ の構成を MSFT + GOOG に変更)
        db_session.query(ThemeConstituent).filter(ThemeConstituent.theme_id == 101).delete()
        db_session.add(ThemeConstituent(theme_id=101, symbol_id=2, weight=0.5)) # MSFT
        db_session.add(ThemeConstituent(theme_id=101, symbol_id=3, weight=0.5)) # GOOG
        db_session.commit()
        
        # 3. 2回目の計算 (ハッシュ不一致が検知され、フル再合成が走るべき)
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # 4. 検証
        new_close = db_session.query(DailyPrice.close).filter(DailyPrice.symbol_id == 101, DailyPrice.date == date(2026, 5, 5)).scalar()
        
        # 構成銘柄が変わったため、過去全期間の累積値が変わり、終値が不一致になるはず
        assert old_close != new_close, "Synthetic close must change since constituents were modified"
        
        # 新しいハッシュ値がJSONに記録されているか確認
        with open(hash_file_path, 'r', encoding='utf-8') as f:
            saved_hashes = json.load(f)
        assert "101" in saved_hashes
        
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_cascading_updates_on_hash_mismatch(db_session):
    """
    テスト4.1 (連鎖フル再計算の検知と自動実行):
    ハッシュ変更によるフルリビルド発生時、該当仮想テーマの indicators (T3) が全クリアされ、
    さらに同一実行ターン内で「テーマ」カテゴリ全体の relative_ranks (T4) が全クリアされ、
    T5 (MarketSignal) は影響を受けないこと。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}
    
    # ダミーの指標データ (T3) と 相対ランク (T4)、市場概況 (T5) をあらかじめ入れておく
    base_date = date(2026, 5, 5)
    db_session.add(Indicator(symbol_id=101, date=base_date, sma_50=100.0, relative_strength_spy=1.2)) # T3 of theme
    db_session.add(RelativeRank(symbol_id=101, date=base_date, group_name="テーマ", indicator_name="relative_strength_spy", percent_rank=0.85)) # T4 of theme
    # T5
    # T5 has no symbol_id, it is a single market score record per date
    from db.models import MarketSignal
    db_session.add(MarketSignal(date=base_date, spy_above_sma200=1, market_trend_score=3.0))
    db_session.commit()
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        # 1. 最初の計算 (全期間分) -> ハッシュが記録される
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # 2. 構成変更してハッシュ不一致を起こす
        db_session.query(ThemeConstituent).filter(ThemeConstituent.theme_id == 101).delete()
        db_session.add(ThemeConstituent(theme_id=101, symbol_id=2, weight=1.0)) # MSFTのみに
        db_session.commit()
        
        # 3. 計算実行
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        # 4. アサーション
        # 仮想テーマの Indicators (T3) が全削除されていること
        t3_count = db_session.query(Indicator).filter(Indicator.symbol_id == 101).count()
        assert t3_count == 0, "T3 Indicators of modified virtual theme should be purged"
        
        # 「テーマ」カテゴリの T4 レコードが全削除されていること
        t4_count = db_session.query(RelativeRank).filter(RelativeRank.group_name == "テーマ").count()
        assert t4_count == 0, "T4 RelativeRanks of group 'テーマ' should be cascadingly cleared"
        
        # T5 レコードは削除されず残っていること
        t5_count = db_session.query(MarketSignal).count()
        assert t5_count == 1, "T5 MarketSignals must remain untouched by virtual theme rebuild"
        
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)


def test_incremental_idempotency(db_session):
    """
    テスト5 (べき等性の検証):
    同じ差分更新を2回連続で実行しても、重複データが作られず、データが正常に維持されること。
    """
    if build_all_virtual_indexes_prices is None:
        pytest.fail("build_all_virtual_indexes_prices is not implemented (Red Phase)")
        
    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}
    
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name
        
    try:
        # 1. 1回目の実行 (フル再合成)
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        count1 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).count()
        prices1 = {p.date: p.close for p in db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).all()}
        
        # 2. 2回目の実行 (差分更新 / ハッシュ一致のため実際には追加合成なし、あるいは上書き)
        build_all_virtual_indexes_prices(db=db_session, virtual_items=virtual_items, symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)
        
        count2 = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).count()
        prices2 = {p.date: p.close for p in db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).all()}
        
        # 件数が重複していないこと
        assert count1 == count2
        
        # 価格が一切変わっていないこと
        for d, close_val in prices1.items():
            assert abs(prices2[d] - close_val) < 1e-6
            
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)
