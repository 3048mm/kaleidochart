import pytest
import pandas as pd
import numpy as np
from backend.backtest.backtest_screener import apply_filters_to_df
# apply_filters_to_df（backend.backtest.backtest_screener）は 'indicators.screener_registry'
# を bare import している（PYTHONPATH=backend 前提）ため、pytest.raises で型を一致させるには
# 同じ経路（bare）で import する必要がある（backend.indicators.screener_registry 経由だと
# 別モジュールインスタンスになり例外クラスが一致しない）。
from indicators.screener_registry import MissingFilterColumnError, UnknownFilterKeyError

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


# ============================================================
# レジストリ導出（doc/in_progress/screener_filter_unification_plan.md §3.1.4）のテスト
# ============================================================

def test_apply_filters_raises_missing_filter_column_error():
    """特殊フィルタが requires で宣言する当日カラムが merged に無ければ例外になること
    （サイレント素通しではなく MissingFilterColumnError で停止する。Phase 1 の核心）。
    """
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active（rs_macd_hist_21 列を欠く）
        [1, 'STK1', 'Stock 1', '個別', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'is_rs_macd_hist_rising_21': True,
    }

    with pytest.raises(MissingFilterColumnError) as exc_info:
        apply_filters_to_df(
            merged=merged,
            target_date='2026-06-26',
            df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
            df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
            df_symbols=pd.DataFrame(),
            df_theme_constituents=pd.DataFrame(),
            strategy=strategy,
        )
    assert 'rs_macd_hist_21' in str(exc_info.value)


def test_apply_filters_x_y_suffix_collision_raises_instead_of_silent_pass():
    """avg_dollar_volume_21 が indicators 側と prices 側の両方にあり、呼び出し元の merge で
    '_x'/'_y' サフィックスが付いた状態（2026-07-28 の実障害の再現）で
    min_avg_dollar_volume_21 を指定すると、サイレント素通し（全銘柄通過）ではなく
    例外になること。素の列名が merged に存在しないため、レジストリ解決自体が
    UnknownFilterKeyError になる（fail-loud という結果は MissingFilterColumnError と同じ）。
    """
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active, avg_dollar_volume_21_x/_y（素の列名が無い）
        [1, 'STK1', 'Stock 1', '個別', 1, 1_000_000.0, 2_000_000.0],
        [2, 'STK2', 'Stock 2', '個別', 1, 100.0, 200.0],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active',
                'avg_dollar_volume_21_x', 'avg_dollar_volume_21_y'])

    strategy = {
        'name': 'test_strat',
        'min_avg_dollar_volume_21': 500_000.0,
    }

    with pytest.raises(UnknownFilterKeyError):
        apply_filters_to_df(
            merged=merged,
            target_date='2026-06-26',
            df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
            df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
            df_symbols=pd.DataFrame(),
            df_theme_constituents=pd.DataFrame(),
            strategy=strategy,
        )


def test_apply_filters_merges_only_required_rank_columns():
    """必要なランクだけが merge されること（14/21/63 の6列を無条件に引かない）。

    旧実装は needs_rs14/21/63 のいずれかが真になると、ratio と trend の2列を必ず
    セットで引いていた。min_rs_ratio_rank_e21 だけを指定した場合、rs21_rank 以外の
    ランク列（rs14_rank / rs63_rank / rs_condition_*_rank）は merged に現れないこと。
    """
    merged = pd.DataFrame([
        [1, 'STK1', 'Stock 1', '個別', 1],
        [2, 'STK2', 'Stock 2', '個別', 1],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active'])

    strategy = {
        'name': 'test_strat',
        'min_rs_ratio_rank_e21': 0.5,
    }

    # 6ランク全種類ぶんのデータを用意しておく（旧実装ならこれを全部引いてしまう）
    df_ranks = pd.DataFrame([
        ['2026-06-26', 1, 'rs_ratio_rank_e14', 0.9],
        ['2026-06-26', 1, 'rs_ratio_rank_e21', 0.8],
        ['2026-06-26', 1, 'rs_ratio_rank_e63', 0.7],
        ['2026-06-26', 1, 'rs_trend_rank_s14', 0.6],
        ['2026-06-26', 1, 'rs_trend_rank_s21', 0.5],
        ['2026-06-26', 1, 'rs_trend_rank_s63', 0.4],
        ['2026-06-26', 2, 'rs_ratio_rank_e14', 0.1],
        ['2026-06-26', 2, 'rs_ratio_rank_e21', 0.1],
        ['2026-06-26', 2, 'rs_ratio_rank_e63', 0.1],
        ['2026-06-26', 2, 'rs_trend_rank_s14', 0.1],
        ['2026-06-26', 2, 'rs_trend_rank_s21', 0.1],
        ['2026-06-26', 2, 'rs_trend_rank_s63', 0.1],
    ], columns=['date', 'symbol_id', 'indicator_name', 'percent_rank'])

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=df_ranks,
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy,
    )

    assert 'rs21_rank' in filtered.columns
    for unused_col in ('rs14_rank', 'rs63_rank', 'rs_condition_14_rank',
                       'rs_condition_21_rank', 'rs_condition_63_rank'):
        assert unused_col not in filtered.columns, (
            f"必要でないランク列 {unused_col} が merge されている（過剰マージ）"
        )
    assert len(filtered) == 1
    assert filtered.iloc[0]['ticker'] == 'STK1'


def test_apply_filters_vcp_breakout_deny_by_default_without_prev_date():
    """is_vcp_breakout は前日列が無くても例外にならず、deny-by-default（全 False）のままであること。

    filter_vcp_breakout は前日カラムが無い場合に全 False を返す設計
    （indicators/screener_filters.py の docstring 参照）。prev_date=None
    （バックテスト初日等）でも MissingFilterColumnError にはならず、単に0件になる。
    """
    merged = pd.DataFrame([
        # symbol_id, ticker, name, category, active,
        # dist_63d_high_pct, dist_52w_high_pct, change_1d_pct, vol_surge_21, is_trend_template, vcr
        [1, 'STK1', 'Stock 1', '個別', 1, -1.0, -1.0, 5.0, 2.0, 1, 0.5],
    ], columns=['symbol_id', 'ticker', 'name', 'category', 'active',
                'dist_63d_high_pct', 'dist_52w_high_pct', 'change_1d_pct',
                'vol_surge_21', 'is_trend_template', 'vcr'])

    strategy = {
        'name': 'test_strat',
        'is_vcp_breakout': True,
    }

    filtered = apply_filters_to_df(
        merged=merged,
        target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(),
        df_theme_constituents=pd.DataFrame(),
        strategy=strategy,
        prev_date=None,
    )

    assert len(filtered) == 0


# =============================================================
# Phase 3c で API 側の _apply_filter / _parse_expression_to_filter を削除したため、
# それらを直接叩いていた test_screener_special_filter_behavior.py の2テストが消えた。
# **エンジンが一本化された後はパリティテストでは検出できない**（両経路が同じ関数を
# 呼ぶので自明に一致してしまう）ため、唯一のエンジンに対する挙動テストとしてここに移す。
# =============================================================

def _minimal_frame(rows, columns):
    return pd.DataFrame(rows, columns=columns)


def test_apply_filters_expression_accepts_lowercase_boolean_literals():
    """expression 内の小文字 true/false が pandas.query 用に正規化されること。

    🔴 回帰テスト（計画書 §7 P3-1）:
    D-2（2026-07-04, commit 487810992）で追加された true/false 正規化は、
    正規表現の単語境界 `\b` が **リテラルのバックスペース文字 (0x08)** として
    ファイルに書き込まれており（CLAUDE.md が警告する文字化け事故）、
    `re.sub` が何にもマッチせず **6週間ずっと no-op** だった。
    その結果 `merged.query()` が小文字の `false` を受け取って NameError になり、
    except に握り潰されて **expression フィルタ全体が黙ってスキップ**されていた
    （= trend_breakdown プリセットが無フィルタで表示される）。

    バックテスト側の戦略で expression を使うものが無かったため気づかれず、
    Phase 3c でエンジンを一本化した瞬間に露見した。
    """
    merged = _minimal_frame([
        [1, 'A', 'A', '個別', 1, 0, -2.0],   # is_trend_template == false → 通過
        [2, 'B', 'B', '個別', 1, 1, -2.0],   # true なので落ちる
    ], ['symbol_id', 'ticker', 'name', 'category', 'active',
        'is_trend_template', 'rs_ratio_e63'])

    filtered = apply_filters_to_df(
        merged=merged, target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(), df_theme_constituents=pd.DataFrame(),
        strategy={'name': 't', 'expression': 'is_trend_template == false and rs_ratio_e63 < -1.0'},
    )

    assert set(filtered['ticker']) == {'A'}, (
        '小文字の false が正規化されず expression が黙ってスキップされている'
        '（0x08 混入の再発を疑うこと）'
    )


def test_apply_filters_min_market_cap_exempts_theme_rows():
    """min_market_cap はテーマ行を免除すること（意味論 S-1）。

    テーマ（実在ETF・仮想指数）は market_cap を持たないため、
    時価総額フィルタで落とすとリーディングテーマ判定が壊れる。
    D-2（2026-07-04）で API 側の SQL も pandas 側に合わせて統一した挙動。
    Phase 3c で SQL 側が消えたので、唯一のエンジンに対する挙動として固定する。
    """
    merged = _minimal_frame([
        [1, 'BIG', 'Big', '個別', 1, 5e8],
        [2, 'SMALL', 'Small', '個別', 1, 1e7],     # 閾値未満 → 落ちる
        [100, 'THEME', 'Theme', 'テーマ', 1, None],  # market_cap 無し → 免除される
    ], ['symbol_id', 'ticker', 'name', 'category', 'active', 'market_cap'])

    filtered = apply_filters_to_df(
        merged=merged, target_date='2026-06-26',
        df_ind=pd.DataFrame(columns=['date', 'symbol_id']),
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(), df_theme_constituents=pd.DataFrame(),
        strategy={'name': 't', 'min_market_cap': 1e8},
    )

    # テーマ行は最終出力からは除外されるので、ここで見えるのは個別のみ。
    # 「テーマがフィルタ段階で落ちていない」ことは、除外前の中間結果ではなく
    # 個別銘柄の通過状況で担保する（BIG が残り SMALL が落ちる）。
    assert set(filtered['ticker']) == {'BIG'}


def test_apply_filters_does_not_remerge_prev_when_already_present():
    """呼び出し側が prev_ 列を持たせている場合、前日を再マージしないこと。

    🔴 回帰テスト（計画書 §7 P3-2）: ガードが無いと二重マージで pandas が
    `_x`/`_y` サフィックスを付け、素の `prev_` 列名が消えて RRG / RS-MACD / VCP
    フィルタが丸ごと壊れる（2026-07-28 に流動性床を無効化した衝突と同型）。
    API の load_cross_section は prev_ 列を含むフレームを渡すため、この経路を踏む。
    """
    # A: 前日 Improving（ratio<0）→ 当日 Leading（ratio>0, mom>0）へ転換し、mom も加速 → 通過
    # B: 当日も Lagging のまま → 落ちる
    merged = _minimal_frame([
        [1, 'A', 'A', '個別', 1, 0.5, 0.6, -0.4, 0.5],
        [2, 'B', 'B', '個別', 1, -0.5, -0.6, -0.4, -0.5],
    ], ['symbol_id', 'ticker', 'name', 'category', 'active',
        'rs_ratio_e21', 'rs_momentum_e21', 'prev_rs_ratio_e21', 'prev_rs_momentum_e21'])

    # 前日データを別途渡す（ガードが無ければ再マージされてしまう状況）。
    # 値は意図的に「転換条件を満たさない」ものにしてあるので、再マージされれば A も落ちる。
    df_ind = pd.DataFrame([
        ['2026-06-25', 1, 9.9, 9.9],
        ['2026-06-25', 2, 9.9, 9.9],
    ], columns=['date', 'symbol_id', 'rs_ratio_e21', 'rs_momentum_e21'])

    filtered = apply_filters_to_df(
        merged=merged, target_date='2026-06-26',
        df_ind=df_ind,
        df_ranks=pd.DataFrame(columns=['date', 'symbol_id', 'indicator_name', 'percent_rank']),
        df_symbols=pd.DataFrame(), df_theme_constituents=pd.DataFrame(),
        strategy={'name': 't', 'rrg_leading_in': True},
        prev_date='2026-06-25',
    )

    # _x/_y 衝突が起きていれば MissingFilterColumnError になるか結果が変わる。
    # ガードが効いていればフレーム上の prev_ 列がそのまま使われ、A のみ通過する。
    assert set(filtered['ticker']) == {'A'}
