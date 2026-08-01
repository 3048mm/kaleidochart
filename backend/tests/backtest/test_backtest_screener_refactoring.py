import pytest
import pandas as pd
import numpy as np
from backend.backtest.backtest_screener import apply_filters_to_df

def test_apply_filters_new_naming_rs_ratio_rank():
    # Test 'is_rs_ratio_rank_e21_gt_e63'
    # Setup mock DataFrames
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'is_rs_ratio_rank_e21_gt_e63': True
    }

    df_ind = pd.DataFrame(columns=['date', 'symbol_id'])
    df_ranks = pd.DataFrame([
        # date, symbol_id, indicator_name, percent_rank
        ['2026-06-26', 1, 'rs_ratio_rank_e21', 0.8],
        ['2026-06-26', 1, 'rs_ratio_rank_e63', 0.5],
        ['2026-06-26', 2, 'rs_ratio_rank_e21', 0.4],
        ['2026-06-26', 2, 'rs_ratio_rank_e63', 0.6],
    ], columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])

    df_symbols = pd.DataFrame()
    df_theme_constituents = pd.DataFrame()

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    # Currently in backtest_screener.py:
    # needs_rs21 checks for strategy.get('rs_rank_21_gt_63'), NOT 'is_rs_ratio_rank_e21_gt_e63'.
    # If the code is not updated, it won't merge rs21_rank or apply the filter.
    # Therefore, this test will FAIL under current implementation.
    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_new_naming_dist_ema21():
    # Test 'min_dist_ema21_pct'
    # In apply_filters_to_df, 'dist_21ema_pct' is derived from 'ema_21' and 'close'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, close, ema_21
        [1, 'STK1', 'Stock 1', '個別', 1, 105.0, 100.0], # dist = 5.0% (Pass: >= 2.0%)
        [2, 'STK2', 'Stock 2', '個別', 1, 101.0, 100.0], # dist = 1.0% (Fail: < 2.0%)
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active', 'close', 'ema_21'])

    strategy = {
        'name': 'test_strat',
        'min_dist_ema21_pct': 2.0
    }

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_new_naming_close_gt_ema63():
    # Test 'is_close_gt_ema63' or 'close_gt_ema63'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, close, ema_63
        [1, 'STK1', 'Stock 1', '個別', 1, 110.0, 100.0], # Pass: 110 > 100
        [2, 'STK2', 'Stock 2', '個別', 1, 95.0, 100.0],  # Fail: 95 < 100
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active', 'close', 'ema_63'])

    strategy = {
        'name': 'test_strat',
        'is_close_gt_ema63': True
    }

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_theme_rs_ratio_e14_gt_e21():
    # Test 'is_theme_rs_ratio_e14_gt_e21'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
        [10, 'THEME_A', 'Theme A', 'テーマ', 1],
        [11, 'THEME_B', 'Theme B', 'テーマ', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'is_theme_rs_ratio_e14_gt_e21': True
    }

    # Setup indicators: rs_ratio_e14 and rs_ratio_e21 for the themes
    df_ind = pd.DataFrame([
        # date, symbol_id, rs_ratio_e14, rs_ratio_e21
        ['2026-06-26', 10, 1.2, 0.8], # THEME_A: 1.2 > 0.8 (Pass)
        ['2026-06-26', 11, 0.5, 0.9], # THEME_B: 0.5 < 0.9 (Fail)
    ], columns=['date', 'symbol_id', 'rs_ratio_e14', 'rs_ratio_e21'])

    df_ranks = pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])
    
    df_symbols = pd.DataFrame([
        [1, 'STK1', '個別'],
        [2, 'STK2', '個別'],
        [10, 'THEME_A', 'テーマ'],
        [11, 'THEME_B', 'テーマ'],
    ], columns=['id', 'ticker', 'category'])

    df_theme_constituents = pd.DataFrame([
        # theme_id, symbol_id
        [10, 1], # STK1 belongs to THEME_A
        [11, 2], # STK2 belongs to THEME_B
    ], columns=['theme_id', 'symbol_id'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    # Filtered should keep THEME_A (category 'テーマ') and STK1 (its constituent)
    # Filtered usually contains only the subset of '個別' (and active 'テーマ' if we want to run theme simulation, but here category is '個別' for targets)
    # The actual implementation of apply_filters_to_df filters both, but let's check STK1 (id=1) is in and STK2 (id=2) is out.
    active_stocks = filtered[filtered['category'] == '個別']
    assert len(active_stocks) == 1
    assert active_stocks.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_min_theme_rs_ratio_rank_e14():
    # Test 'min_theme_rs_ratio_rank_e14'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
        [10, 'THEME_A', 'Theme A', 'テーマ', 1],
        [11, 'THEME_B', 'Theme B', 'テーマ', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'min_theme_rs_ratio_rank_e14': 0.7
    }

    df_ind = pd.DataFrame(columns=['date', 'symbol_id'])

    # Setup ranks for themes: THEME_A has 0.8 (>=0.7), THEME_B has 0.5 (<0.7)
    df_ranks = pd.DataFrame([
        # date, symbol_id, indicator_name, percent_rank
        ['2026-06-26', 10, 'rs_ratio_rank_e14', 0.8],
        ['2026-06-26', 11, 'rs_ratio_rank_e14', 0.5],
    ], columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])
    
    df_symbols = pd.DataFrame([
        [1, 'STK1', '個別'],
        [2, 'STK2', '個別'],
        [10, 'THEME_A', 'テーマ'],
        [11, 'THEME_B', 'テーマ'],
    ], columns=['id', 'ticker', 'category'])

    df_theme_constituents = pd.DataFrame([
        # theme_id, symbol_id
        [10, 1], # STK1 belongs to THEME_A
        [11, 2], # STK2 belongs to THEME_B
    ], columns=['theme_id', 'symbol_id'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    active_stocks = filtered[filtered['category'] == '個別']
    assert len(active_stocks) == 1
    assert active_stocks.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_min_theme_rs_trend_s21():
    # Test 'min_theme_rs_trend_s21'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, rs_trend_s21
        [1, 'STK1', 'Stock 1', '個別', 1, 0.0],
        [2, 'STK2', 'Stock 2', '個別', 1, 0.0],
        [10, 'THEME_A', 'Theme A', 'テーマ', 1, 1.5], # THEME_A: 1.5 >= 1.2 (Pass)
        [11, 'THEME_B', 'Theme B', 'テーマ', 1, 0.8], # THEME_B: 0.8 < 1.2 (Fail)
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active', 'rs_trend_s21'])

    strategy = {
        'name': 'test_strat',
        'min_theme_rs_trend_s21': 1.2
    }

    # Setup indicators: rs_trend_s21 for themes
    df_ind = pd.DataFrame([
        # date, symbol_id, rs_trend_s21
        ['2026-06-26', 10, 1.5], # THEME_A: 1.5 >= 1.2 (Pass)
        ['2026-06-26', 11, 0.8], # THEME_B: 0.8 < 1.2 (Fail)
    ], columns=['date', 'symbol_id', 'rs_trend_s21'])

    df_ranks = pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])
    
    df_symbols = pd.DataFrame([
        [1, 'STK1', '個別'],
        [2, 'STK2', '個別'],
        [10, 'THEME_A', 'テーマ'],
        [11, 'THEME_B', 'テーマ'],
    ], columns=['id', 'ticker', 'category'])

    df_theme_constituents = pd.DataFrame([
        # theme_id, symbol_id
        [10, 1], # STK1 belongs to THEME_A
        [11, 2], # STK2 belongs to THEME_B
    ], columns=['theme_id', 'symbol_id'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    active_stocks = filtered[filtered['category'] == '個別']
    assert len(active_stocks) == 1
    assert active_stocks.iloc[0]['ticker'] == 'STK1'

def test_apply_filters_is_rs_blue_dot():
    # 2026-07-22 発見: is_rs_blue_dot は alias_map に 'rs_blue_dot': 'is_rs_blue_dot' が
    # 無いため、汎用 is_ プレフィックス除去ロジックが存在しない列名 'rs_blue_dot' を探しに行き
    # サイレントに素通し（全銘柄通過）していた（G3_bluedot_leader で実害確認済み）。
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, is_rs_blue_dot
        [1, 'STK1', 'Stock 1', '個別', 1, 1],  # Pass: blue dot 点灯
        [2, 'STK2', 'Stock 2', '個別', 1, 0],  # Fail: blue dot 不点灯
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active', 'is_rs_blue_dot'])

    strategy = {
        'name': 'test_strat',
        'is_rs_blue_dot': True
    }

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'


def test_apply_filters_is_theme_rs_trend_rank_s14_gt_s21():
    # Test 'is_theme_rs_trend_rank_s14_gt_s21'
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
        [10, 'THEME_A', 'Theme A', 'テーマ', 1],
        [11, 'THEME_B', 'Theme B', 'テーマ', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'is_theme_rs_trend_rank_s14_gt_s21': True
    }

    df_ind = pd.DataFrame(columns=['date', 'symbol_id'])

    # Setup ranks for themes: THEME_A (s14: 0.8 > s21: 0.5 - Pass), THEME_B (s14: 0.4 < s21: 0.6 - Fail)
    df_ranks = pd.DataFrame([
        # date, symbol_id, indicator_name, percent_rank
        ['2026-06-26', 10, 'rs_trend_rank_s14', 0.8],
        ['2026-06-26', 10, 'rs_trend_rank_s21', 0.5],
        ['2026-06-26', 11, 'rs_trend_rank_s14', 0.4],
        ['2026-06-26', 11, 'rs_trend_rank_s21', 0.6],
    ], columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])
    
    df_symbols = pd.DataFrame([
        [1, 'STK1', '個別'],
        [2, 'STK2', '個別'],
        [10, 'THEME_A', 'テーマ'],
        [11, 'THEME_B', 'テーマ'],
    ], columns=['id', 'ticker', 'category'])

    df_theme_constituents = pd.DataFrame([
        # theme_id, symbol_id
        [10, 1], # STK1 belongs to THEME_A
        [11, 2], # STK2 belongs to THEME_B
    ], columns=['theme_id', 'symbol_id'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    active_stocks = filtered[filtered['category'] == '個別']
    assert len(active_stocks) == 1
    assert active_stocks.iloc[0]['ticker'] == 'STK1'



def test_apply_filters_excludes_theme_rows_from_final_output():
    """テーマ（仮想指数含む）は売買対象外。他の条件を満たしても最終出力には含めない。

    2026-07-29 発見: category='テーマ' 行がリーディングテーマ判定用の中間データ
    としてだけでなく、そのまま買いシグナルとして最終出力に混入していた
    （個別銘柄シナリオテストで実在しない仮想指数ティッカーが売買される実害あり）。
    """
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, change_1d_pct
        [1, 'STK1', 'Stock 1', '個別', 1, 10.0],
        [10, '_THEME_A_', 'Theme A', 'テーマ', 1, 10.0],  # 通常フィルタは満たすが除外対象
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active', 'change_1d_pct'])

    strategy = {
        'name': 'test_strat',
        'min_change_1d_pct': 5.0,
    }

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy
    )

    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'
    assert not (filtered['category'] == 'テーマ').any()


def test_apply_filters_theme_leadership_filter_still_works_after_theme_exclusion():
    """テーマ自体は最終出力から除外されても、リーディングテーマ判定
    （min_theme_rs_ratio_rank_e14 等）による構成銘柄の絞り込みは従来通り機能すること。
    """
    merged = pd.DataFrame([
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
        [10, 'THEME_A', 'Theme A', 'テーマ', 1],
        [11, 'THEME_B', 'Theme B', 'テーマ', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'min_theme_rs_ratio_rank_e14': 0.7
    }

    df_ind = pd.DataFrame(columns=['date', 'symbol_id'])
    df_ranks = pd.DataFrame([
        ['2026-06-26', 10, 'rs_ratio_rank_e14', 0.8],
        ['2026-06-26', 11, 'rs_ratio_rank_e14', 0.5],
    ], columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])

    df_symbols = pd.DataFrame([
        [1, 'STK1', '個別'],
        [2, 'STK2', '個別'],
        [10, 'THEME_A', 'テーマ'],
        [11, 'THEME_B', 'テーマ'],
    ], columns=['id', 'ticker', 'category'])

    df_theme_constituents = pd.DataFrame([
        [10, 1],
        [11, 2],
    ], columns=['theme_id', 'symbol_id'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy
    )

    # リーディングテーマ(THEME_A)の構成銘柄STK1のみが残り、テーマ自体は出力に含まれない
    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'
