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
_backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    db_session.add(Indicator(symbol_id=101, date=base_date, sma_50=100.0, rs_value=1.2)) # T3 of theme
    db_session.add(RelativeRank(symbol_id=101, date=base_date, group_name="テーマ", rs_value_rank=0.85)) # T4 of theme
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


def test_synthetic_ohlcv_integrity(db_session):
    """
    仮想テーマの始値・高値・安値・出来高（21日平均売買代金ベースのsurge）が
    期待通りに合成されるか検証する。

    dollar_volume_ma21 は min_periods=21 で計算されるため、window(21日)が
    埋まるまでは NaN となり、直後の np.where(...).fillna(1.0) により
    surge は中立値 1.0 に丸められる（このマスキング処理自体は本テストの対象外・
    変更対象外）。そのためウォームアップ期間中（21日未満）の volume は
    1000000.0 (=1.0 * 1000000.0) ぴったりに固定され、window がちょうど
    21日分埋まった日（Day 21）以降で初めて実際の21日平均売買代金に基づく
    surge が反映される。
    """
    # フィクスチャの5日分(2026-05-01〜05-05, i=0..4)に続けて、AAPL(1)・MSFT(2)
    # のみに同じ生成式で16日分(i=5..20)を追加し、合計21日分(window=21がちょうど
    # 埋まる)のデータにする。GOOG(3)・_PHNC_(102, theme_id=102)は本テストの
    # アサーション対象外のため触らない。
    base_date = date(2026, 5, 1)
    for i in range(5, 21):
        d = base_date + timedelta(days=i)
        db_session.add(DailyPrice(symbol_id=1, date=d, open=100.0 * (1.01**i), high=101.0 * (1.01**i), low=99.0 * (1.01**i), close=100.0 * (1.01**i), volume=1000))
        db_session.add(DailyPrice(symbol_id=2, date=d, open=200.0 * (1.02**i), high=202.0 * (1.02**i), low=198.0 * (1.02**i), close=200.0 * (1.02**i), volume=1500))
    db_session.commit()

    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name

    try:
        # 計算実行
        build_all_virtual_indexes_prices(
            db=db_session,
            virtual_items=virtual_items,
            symbol_id_map=symbol_id_map,
            hash_file_path=hash_file_path
        )

        prices = db_session.query(DailyPrice).filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()

        # 構成銘柄(AAPL/MSFT)の21日分の終値・出来高を、プロダクションコードとは
        # 独立に pandas で素朴に再現し、期待値を計算する（手計算による桁間違いを避ける）。
        dates = [base_date + timedelta(days=i) for i in range(21)]
        aapl_close = pd.Series([100.0 * (1.01**i) for i in range(21)], index=dates)
        aapl_volume = pd.Series([1000.0] * 21, index=dates)
        msft_close = pd.Series([200.0 * (1.02**i) for i in range(21)], index=dates)
        msft_volume = pd.Series([1500.0] * 21, index=dates)

        aapl_dollar_volume = aapl_close * aapl_volume
        msft_dollar_volume = msft_close * msft_volume

        aapl_ma21 = aapl_dollar_volume.rolling(window=21, min_periods=21).mean()
        msft_ma21 = msft_dollar_volume.rolling(window=21, min_periods=21).mean()

        aapl_surge = (aapl_dollar_volume / aapl_ma21).fillna(1.0)
        msft_surge = (msft_dollar_volume / msft_ma21).fillna(1.0)

        avg_surge = (aapl_surge + msft_surge) / 2.0
        expected_volume = avg_surge * 1000000.0

        # 合成テーマの価格系列は close_prev が存在しない Day1 を除いた
        # Day2〜Day21 の20日分になる
        assert len(prices) == 20

        for idx, p in enumerate(prices):
            src_date = dates[idx + 1]
            expected_vol = expected_volume.loc[src_date]
            assert abs(p.volume - expected_vol) < 10.0, f"date={p.date}"

        # ウォームアップ期間(Day2〜Day20, window未満)は中立値1.0でマスクされ、
        # volume は常に 1000000.0 ぴったりになることを明示的に検証する
        for p in prices[:-1]:
            assert abs(p.volume - 1000000.0) < 1e-6, f"warmup date={p.date} volume={p.volume}"

        # window がちょうど21日分埋まった最終日(Day21)は、実際の21日平均売買代金に
        # 基づく surge が反映され、中立値(1000000.0)から明確に乖離する
        assert abs(prices[-1].volume - 1000000.0) > 10.0

        # 始値、高値、安値の論理的整合性の検証
        for p in prices:
            assert p.high >= p.open
            assert p.high >= p.close
            assert p.low <= p.open
            assert p.low <= p.close
            assert p.volume > 0  # 出来高が0より大きいこと

    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)



def test_incremental_surge_uses_full_history_for_ma21(db_session):
    """
    増分更新で追加・再計算された仮想指数バーの volume（= surge * 1_000_000）が、
    構成銘柄の全履歴で計算した21日平均売買代金ベースの surge と一致すること（5-20 / R1）。

    回帰: 増分ブランチが `>= seed_date` で数行に絞ってから rolling(21, min_periods=21) を
    計算していたため、常に NaN → surge=1.0（volume=1,000,000 固定）になっていた。
    """
    base_date = date(2026, 5, 1)
    n_hist = 25  # 21本を超える履歴（i=0..24）
    new_idx = n_hist  # 増分で追加する日（i=25）

    def _close(base, growth, i):
        return base * (growth ** i)

    def _vol(base, i):
        # 日ごとに変化する出来高（surge が1.0にならないようにする）。最終日はスパイク
        return base + 37.0 * (i % 7) + (5000.0 if i == new_idx else 0.0)

    # フィクスチャの5日分(i=0..4)に続けて i=5..24 を追加（volume は日ごとに変化させる）
    for i in range(n_hist):
        d = base_date + timedelta(days=i)
        if i < 5:
            # フィクスチャ既存行の volume を上書きして変化を持たせる
            db_session.query(DailyPrice).filter(
                DailyPrice.symbol_id == 1, DailyPrice.date == d).update({"volume": _vol(1000.0, i)})
            db_session.query(DailyPrice).filter(
                DailyPrice.symbol_id == 2, DailyPrice.date == d).update({"volume": _vol(1500.0, i)})
        else:
            c1 = _close(100.0, 1.01, i)
            c2 = _close(200.0, 1.02, i)
            db_session.add(DailyPrice(symbol_id=1, date=d, open=c1, high=c1, low=c1, close=c1, volume=_vol(1000.0, i)))
            db_session.add(DailyPrice(symbol_id=2, date=d, open=c2, high=c2, low=c2, close=c2, volume=_vol(1500.0, i)))
    db_session.commit()

    virtual_items = [{"ticker": "_TECH_", "exchange": "VIRTUAL", "theme_type": "virtual"}]
    symbol_id_map = {("_TECH_", "VIRTUAL"): 101}

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_file:
        hash_file_path = tmp_file.name

    try:
        # 1. 全期間再合成（ハッシュ記録）
        build_all_virtual_indexes_prices(
            db=db_session, virtual_items=virtual_items,
            symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)

        # 2. 構成銘柄に1日分（i=25）を追加 → 次回は増分ブランチに入る
        d_new = base_date + timedelta(days=new_idx)
        c1 = _close(100.0, 1.01, new_idx)
        c2 = _close(200.0, 1.02, new_idx)
        db_session.add(DailyPrice(symbol_id=1, date=d_new, open=c1, high=c1, low=c1, close=c1, volume=_vol(1000.0, new_idx)))
        db_session.add(DailyPrice(symbol_id=2, date=d_new, open=c2, high=c2, low=c2, close=c2, volume=_vol(1500.0, new_idx)))
        db_session.commit()

        build_all_virtual_indexes_prices(
            db=db_session, virtual_items=virtual_items,
            symbol_id_map=symbol_id_map, hash_file_path=hash_file_path)

        prices = {p.date: p for p in db_session.query(DailyPrice)
                  .filter(DailyPrice.symbol_id == 101).order_by(DailyPrice.date).all()}

        # 3. pandas で独立に期待値（全履歴での21日平均売買代金ベースの surge 平均）を計算
        dates = [base_date + timedelta(days=i) for i in range(new_idx + 1)]

        def _surge(base_close, growth, base_vol):
            close = pd.Series([_close(base_close, growth, i) for i in range(new_idx + 1)], index=dates)
            vol = pd.Series([_vol(base_vol, i) for i in range(new_idx + 1)], index=dates)
            dv = close * vol
            ma21 = dv.rolling(window=21, min_periods=21).mean()
            return (dv / ma21).fillna(1.0)

        expected_volume = ((_surge(100.0, 1.01, 1000.0) + _surge(200.0, 1.02, 1500.0)) / 2.0) * 1_000_000.0

        # 増分で再計算・追加される日（last_date=i=24 と 新規 i=25）
        for i in (new_idx - 1, new_idx):
            d = dates[i]
            assert d in prices, f"date={d} の仮想指数バーがありません"
            exp = expected_volume.loc[d]
            assert abs(prices[d].volume - exp) < 1.0, f"date={d} volume={prices[d].volume} expected={exp}"
            # 縮退値（1.0固定）ではないこと
            assert abs(prices[d].volume - 1_000_000.0) > 1.0, f"date={d} が surge=1.0 固定になっています"
    finally:
        if os.path.exists(hash_file_path):
            os.remove(hash_file_path)
