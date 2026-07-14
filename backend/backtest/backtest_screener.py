"""
backtest_screener.py — Signal Scanner for Backtest Engine

Evaluates screener filter conditions against preloaded pandas DataFrames
and returns signal records for each trading day.
"""
import sys
import os
import pandas as pd
import numpy as np
import bisect
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from backend.backtest.strategy_normalizer import normalize_strategy_keys

try:
    from indicators.screener_filters import (
        filter_rs_rank_21_gt_63,
        filter_theme_rs21_gt_63,
        filter_theme_rs_rank_21_gt_63,
        filter_rs_rank_14_gt_21,
        filter_theme_rs14_gt_21,
        filter_theme_rs_rank_14_gt_21,
        filter_rrg_leading_in,
        filter_rrg_improving_in,
        filter_rrg_lagging_in,
        filter_rs_macd_hist_rising_21,
        filter_rs_trend_s21_lt_s63,
        filter_rs_trend_s14_lt_s21,
        filter_theme_rs_trend_rank_s14_gt_s21,
        filter_theme_rs_trend_rank_s21_gt_s63,
        filter_vcp_breakout,
    )
except ModuleNotFoundError:
    from backend.indicators.screener_filters import (
        filter_rs_rank_21_gt_63,
        filter_theme_rs21_gt_63,
        filter_theme_rs_rank_21_gt_63,
        filter_rs_rank_14_gt_21,
        filter_theme_rs14_gt_21,
        filter_theme_rs_rank_14_gt_21,
        filter_rrg_leading_in,
        filter_rrg_improving_in,
        filter_rrg_lagging_in,
        filter_rs_macd_hist_rising_21,
        filter_rs_trend_s21_lt_s63,
        filter_rs_trend_s14_lt_s21,
        filter_theme_rs_trend_rank_s14_gt_s21,
        filter_theme_rs_trend_rank_s21_gt_s63,
        filter_vcp_breakout,
    )


@dataclass
class SignalRecord:
    """Represents a single entry signal."""
    symbol_id: int
    ticker: str
    date: object  # datetime.date
    entry_price: float  # close price on signal day


def scan_signals_for_date(
    target_date,
    df_ind: pd.DataFrame,
    df_price: pd.DataFrame,
    df_ranks: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
    strategy: Dict[str, Any],
    prev_date=None,
    ind_day_cache: dict = None,
    price_day_cache: dict = None,
    ranks_day_cache: dict = None,
    ranks_dates_sorted: list = None,
) -> List[SignalRecord]:
    """
    Scan for signals on a specific date by merging data and applying filters.
    
    Args:
        ind_day_cache: Optional pre-grouped {date: DataFrame} for indicators. O(1) lookup.
        price_day_cache: Optional pre-grouped {date: DataFrame} for prices. O(1) lookup.
    """
    # 1. Prepare merged daily data (use cache if available)
    if ind_day_cache is not None:
        ind_day = ind_day_cache.get(target_date)
        if ind_day is None: return []
        ind_day = ind_day.copy()
    else:
        ind_day = df_ind[df_ind['date'] == target_date].copy()
        if ind_day.empty: return []
    
    if price_day_cache is not None:
        price_day = price_day_cache.get(target_date)
        if price_day is None: return []
        price_day = price_day.copy()
    else:
        price_day = df_price[df_price['date'] == target_date].copy()
        if price_day.empty: return []
    
    price_cols = ['symbol_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap']
    if 'avg_dollar_volume_21' in price_day.columns:
        price_cols.append('avg_dollar_volume_21')  # 流動性ハード制約（min_avg_dollar_volume_21）用
    merged = ind_day.merge(
        price_day[price_cols],
        on=['symbol_id', 'date'],
        how='inner'
    )
    merged = merged.merge(
        df_symbols[['id', 'ticker', 'name', 'category', 'active']],
        left_on='symbol_id', right_on='id', how='inner'
    )
    
    # 2. Apply filters
    filtered = apply_filters_to_df(
        merged=merged,
        target_date=target_date,
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_theme_constituents,
        strategy=strategy,
        prev_date=prev_date,
        ind_day_cache=ind_day_cache,
        ranks_day_cache=ranks_day_cache,
        ranks_dates_sorted=ranks_dates_sorted,
    )
    
    # 3. Convert to SignalRecords
    signals = []
    for _, row in filtered.iterrows():
        signals.append(SignalRecord(
            symbol_id=int(row['symbol_id']),
            ticker=row['ticker'],
            date=target_date,
            entry_price=float(row['close']),
        ))
    return signals


def apply_filters_to_df(
    merged: pd.DataFrame,
    target_date,
    df_ind: pd.DataFrame,
    df_ranks: pd.DataFrame,
    df_symbols: pd.DataFrame,
    df_theme_constituents: pd.DataFrame,
    strategy: Dict[str, Any],
    prev_date=None,
    ind_day_cache: dict = None,
    ranks_day_cache: dict = None,
    ranks_dates_sorted: list = None,
) -> pd.DataFrame:
    """
    Applies strategy filters to a pre-merged daily DataFrame.
    """
    strategy = normalize_strategy_keys(strategy)
    if merged.empty:
        return merged

    # Base filter: active symbols, category in (テーマ, 個別)
    merged = merged[(merged['active'] == 1) & (merged['category'].isin(['テーマ', '個別']))].copy()
    if merged.empty:
        return merged

    merged = merged.reset_index(drop=True)

    # Derived columns
    if 'open' in merged.columns and 'close' in merged.columns:
        merged['change_intraday_pct'] = np.where(
            merged['open'] > 0,
            (merged['close'] - merged['open']) / merged['open'] * 100,
            0.0
        )
    if 'ema_21' in merged.columns and 'close' in merged.columns:
        merged['dist_21ema_pct'] = np.where(
            merged['ema_21'] > 0,
            (merged['close'] - merged['ema_21']) / merged['ema_21'] * 100,
            0.0
        )

    # --- RS Rank merge ---
    sort_col = strategy.get('sort_column', 'rs_ratio_rank_e21')
    needs_rs14 = (
        strategy.get('is_rs_ratio_rank_e14_gt_e21')
        or strategy.get('is_theme_rs_ratio_rank_e14_gt_e21')
        or 'min_rs_ratio_rank_e14' in strategy
        or 'min_theme_rs_ratio_rank_e14' in strategy
        or sort_col in ('rs14_rank', 'rs_ratio_rank_e14')
        or 'min_rs_trend_rank_s14' in strategy
        or 'min_theme_rs_trend_rank_s14' in strategy
        or strategy.get('is_rs_trend_rank_s14_gt_s21')
        or strategy.get('is_theme_rs_trend_rank_s14_gt_s21')
        or strategy.get('is_rs_trend_s14_lt_s21')
    )
    needs_rs21 = (
        'min_rs_ratio_rank_e21' in strategy
        or 'min_theme_rs_ratio_rank_e21' in strategy
        or 'min_rs_macd_hist_rank_21' in strategy
        or strategy.get('is_rs_macd_hist_rising_21')
        or strategy.get('is_rs_ratio_rank_e21_gt_e63')
        or strategy.get('is_theme_rs_ratio_rank_e21_gt_e63')
        or strategy.get('is_rs_ratio_rank_e14_gt_e21')
        or strategy.get('is_theme_rs_ratio_rank_e14_gt_e21')
        or sort_col in ('rs21_rank', 'rs_ratio_rank_e21')
        or 'min_rs_trend_rank_s21' in strategy
        or 'min_theme_rs_trend_rank_s21' in strategy
        or strategy.get('is_rs_trend_rank_s14_gt_s21')
        or strategy.get('is_theme_rs_trend_rank_s14_gt_s21')
        or strategy.get('is_rs_trend_rank_s21_gt_s63')
        or strategy.get('is_theme_rs_trend_rank_s21_gt_s63')
        or strategy.get('is_rs_trend_s21_lt_s63')
        or strategy.get('is_rs_trend_s14_lt_s21')
    )
    needs_rs63 = (
        strategy.get('is_rs_ratio_rank_e21_gt_e63')
        or strategy.get('is_theme_rs_ratio_rank_e21_gt_e63')
        or 'min_rs_ratio_rank_e63' in strategy
        or 'min_theme_rs_ratio_rank_e63' in strategy
        or sort_col in ('rs63_rank', 'rs_ratio_rank_e63')
        or 'min_rs_trend_rank_s63' in strategy
        or 'min_theme_rs_trend_rank_s63' in strategy
        or strategy.get('is_rs_trend_rank_s21_gt_s63')
        or strategy.get('is_theme_rs_trend_rank_s21_gt_s63')
        or strategy.get('is_rs_trend_s21_lt_s63')
    )

    if needs_rs14 or needs_rs21 or needs_rs63:
        # Use bisect to find the nearest previous date (ranks may not exist for every trading day)
        if ranks_dates_sorted is not None:
            cache_dates = ranks_dates_sorted
        elif ranks_day_cache is not None:
            cache_dates = sorted(ranks_day_cache.keys())
        else:
            cache_dates = sorted(df_ranks['date'].unique())

        idx = bisect.bisect_right(cache_dates, target_date)
        if idx > 0:
            rank_date = cache_dates[idx - 1]
            if ranks_day_cache is not None:
                ranks_day = ranks_day_cache.get(rank_date)
            else:
                ranks_day = df_ranks[df_ranks['date'] == rank_date]
        else:
            ranks_day = None

        if ranks_day is not None:
            if needs_rs14:
                r14 = ranks_day[ranks_day['indicator_name'] == 'rs_ratio_rank_e14'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs14_rank'}
                )
                merged = merged.merge(r14, on='symbol_id', how='left').reset_index(drop=True)
                
                c14 = ranks_day[ranks_day['indicator_name'] == 'rs_trend_rank_s14'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs_condition_14_rank'}
                )
                merged = merged.merge(c14, on='symbol_id', how='left').reset_index(drop=True)

            if needs_rs21:
                r21 = ranks_day[ranks_day['indicator_name'] == 'rs_ratio_rank_e21'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs21_rank'}
                )
                merged = merged.merge(r21, on='symbol_id', how='left').reset_index(drop=True)
                
                c21 = ranks_day[ranks_day['indicator_name'] == 'rs_trend_rank_s21'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs_condition_21_rank'}
                )
                merged = merged.merge(c21, on='symbol_id', how='left').reset_index(drop=True)

            if needs_rs63:
                r63 = ranks_day[ranks_day['indicator_name'] == 'rs_ratio_rank_e63'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs63_rank'}
                )
                merged = merged.merge(r63, on='symbol_id', how='left').reset_index(drop=True)
                
                c63 = ranks_day[ranks_day['indicator_name'] == 'rs_trend_rank_s63'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs_condition_63_rank'}
                )
                merged = merged.merge(c63, on='symbol_id', how='left').reset_index(drop=True)
        else:
            if (
                'min_rs_ratio_rank_e21' in strategy
                or 'min_rs_ratio_rank_e14' in strategy
                or 'min_rs_ratio_rank_e63' in strategy
                or 'min_theme_rs_ratio_rank_e14' in strategy
                or 'min_theme_rs_ratio_rank_e21' in strategy
                or 'min_theme_rs_ratio_rank_e63' in strategy
                or strategy.get('is_rs_ratio_rank_e21_gt_e63')
                or strategy.get('is_rs_ratio_rank_e14_gt_e21')
                or 'min_rs_trend_rank_s14' in strategy
                or 'min_rs_trend_rank_s21' in strategy
                or 'min_rs_trend_rank_s63' in strategy
                or 'min_theme_rs_trend_rank_s14' in strategy
                or 'min_theme_rs_trend_rank_s21' in strategy
                or 'min_theme_rs_trend_rank_s63' in strategy
                or strategy.get('is_rs_trend_rank_s14_gt_s21')
                or strategy.get('is_rs_trend_rank_s21_gt_s63')
                or strategy.get('is_rs_trend_s21_lt_s63')
                or strategy.get('is_rs_trend_s14_lt_s21')
                or strategy.get('is_theme_rs_trend_rank_s14_gt_s21')
                or strategy.get('is_theme_rs_trend_rank_s21_gt_s63')
                or 'min_rs_macd_hist_rank_21' in strategy
            ):
                return pd.DataFrame(columns=merged.columns)

    # --- RRG / RS-MACD / VCP breakout の前日マージ ---
    if (
        strategy.get('rrg_leading_in')
        or strategy.get('rrg_lagging_in')
        or strategy.get('rrg_improving_in')
        or strategy.get('is_rs_macd_hist_rising_21')
        or strategy.get('is_vcp_breakout')
    ) and prev_date is not None:
        if ind_day_cache is not None:
            ind_prev_all = ind_day_cache.get(prev_date)
        else:
            ind_prev_all = df_ind[df_ind['date'] == prev_date]

        if ind_prev_all is not None:
            cols_to_use = ['symbol_id']
            rename_dict = {}
            # VCP ブレイクアウト用に vcr / N日高値距離 / 出来高倍率も前日から取り込む
            prev_merge_cols = ['rs_ratio_e21', 'rs_momentum_e21', 'rs_macd_hist_21',
                               'vcr', 'dist_63d_high_pct', 'dist_52w_high_pct',
                               'vol_surge_21']
            for c in prev_merge_cols:
                if c in ind_prev_all.columns:
                    cols_to_use.append(c)
                    rename_dict[c] = f'prev_{c}'
            ind_prev = ind_prev_all[cols_to_use].rename(columns=rename_dict)
            merged = merged.merge(ind_prev, on='symbol_id', how='left').reset_index(drop=True)

    # --- Build the mask ---
    mask = pd.Series(True, index=merged.index)

    alias_map = {
        'rs_ratio_rank_e14': 'rs14_rank',
        'rs_ratio_rank_e21': 'rs21_rank',
        'rs_ratio_rank_e63': 'rs63_rank',
        'rs_trend_rank_s14': 'rs_condition_14_rank',
        'rs_trend_rank_s21': 'rs_condition_21_rank',
        'rs_trend_rank_s63': 'rs_condition_63_rank',
        'change_intraday_pct': 'change_intraday_pct',
        'dist_21ema_pct': 'dist_21ema_pct',
    }

    for key, value in strategy.items():
        if key in ('name', 'description', 'max_hits_per_day', 'sort_column', 'sort_ascending', 'expression', '_use_hysteresis'):
            continue
            
        col = key
        op = None

        if key.startswith('min_'):
            col = key[4:]; op = '>='
        elif key.startswith('max_'):
            col = key[4:]; op = '<='
        elif isinstance(value, bool) or key.startswith('bool_') or key.startswith('is_') or key.startswith('has_'):
            op = '=='
            if key.startswith('bool_'): col = key[5:]
            elif key.startswith('is_'): col = key[3:]
            elif key.startswith('has_'): col = key[4:]

        if op:
            col = alias_map.get(col, col)
            if col == 'market_cap' and 'market_cap' in merged.columns and op == '>=':
                mask &= (merged['market_cap'] >= value) | (merged['category'] == 'テーマ')
            elif col in merged.columns:
                if op == '>=':   mask &= merged[col] >= value
                elif op == '<=': mask &= merged[col] <= value
                elif op == '==': mask &= merged[col] == value

    # 2. Dynamic Close-Above Filters (e.g. close_gt_sma50, close_gt_ema21, close_gt_ema50, or is_close_gt_*)
    for key, value in strategy.items():
        if (key.startswith('close_gt_') or key.startswith('is_close_gt_')) and value is True:
            ind_name = key[9:] if key.startswith('close_gt_') else key[12:]
            
            # Normalize column names if they lack underscores (e.g., ema21 -> ema_21, sma50 -> sma_50)
            if ind_name in ('sma5', 'sma21', 'sma50', 'sma63', 'sma150', 'sma200', 'ema5', 'ema21', 'ema50', 'ema63', 'ema150', 'ema200'):
                for num in ('200', '150', '63', '50', '21', '5'):
                    if ind_name.endswith(num) and not ind_name.endswith('_' + num):
                        ind_name = ind_name.replace(num, '_' + num)
                        break
            
            if 'close' in merged.columns and ind_name in merged.columns:
                mask &= merged['close'] > merged[ind_name]

    if strategy.get('is_rs_ratio_rank_e21_gt_e63'):
        mask &= filter_rs_rank_21_gt_63(merged)

    if strategy.get('is_theme_rs_ratio_e21_gt_e63'):
        if ind_day_cache is not None:
            ind_day_for_theme = ind_day_cache.get(target_date)
        else:
            ind_day_for_theme = df_ind[df_ind['date'] == target_date]
            
        if ind_day_for_theme is not None:
            mask &= filter_theme_rs21_gt_63(merged, ind_day_for_theme, df_symbols, df_theme_constituents)

    if strategy.get('is_theme_rs_ratio_rank_e21_gt_e63'):
        mask &= filter_theme_rs_rank_21_gt_63(merged, df_symbols, df_theme_constituents)

    if strategy.get('is_rs_ratio_rank_e14_gt_e21'):
        mask &= filter_rs_rank_14_gt_21(merged)

    if strategy.get('is_theme_rs_ratio_e14_gt_e21'):
        if ind_day_cache is not None:
            ind_day_for_theme = ind_day_cache.get(target_date)
        else:
            ind_day_for_theme = df_ind[df_ind['date'] == target_date]
            
        if ind_day_for_theme is not None:
            mask &= filter_theme_rs14_gt_21(merged, ind_day_for_theme, df_symbols, df_theme_constituents)

    if strategy.get('is_theme_rs_ratio_rank_e14_gt_e21'):
        mask &= filter_theme_rs_rank_14_gt_21(merged, df_symbols, df_theme_constituents)

    # Dynamic Theme RS Rank Filters (min_theme_rs_ratio_rank_e14/e21/e63)
    for num in ('14', '21', '63'):
        new_param_key = f'min_theme_rs_ratio_rank_e{num}'
        if new_param_key in strategy:
            threshold = float(strategy.get(new_param_key))
            rank_col = f'rs{num}_rank'
            if rank_col in merged.columns:
                # 1. テーマの中で rank_col >= threshold のものを抽出
                theme_rows = merged[merged['category'] == 'テーマ']
                leading_themes = theme_rows[theme_rows[rank_col] >= threshold]['symbol_id'].values
                
                # 2. それらのテーマに属する個別銘柄を抽出
                stocks_in_themes = df_theme_constituents[
                    df_theme_constituents['theme_id'].isin(leading_themes)
                ]['symbol_id'].values
                
                # 3. マスクを更新
                mask &= (
                    ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
                    ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
                )

    # Dynamic Theme RS Condition Rank Filters (min_theme_rs_trend_rank_s14/s21/s63)
    for num in ('14', '21', '63'):
        new_param_key = f'min_theme_rs_trend_rank_s{num}'
        if new_param_key in strategy:
            threshold = float(strategy.get(new_param_key))
            rank_col = f'rs_condition_{num}_rank'
            if rank_col in merged.columns:
                # 1. テーマの中で rank_col >= threshold のものを抽出
                theme_rows = merged[merged['category'] == 'テーマ']
                leading_themes = theme_rows[theme_rows[rank_col] >= threshold]['symbol_id'].values
                
                # 2. それらのテーマに属する個別銘柄を抽出
                stocks_in_themes = df_theme_constituents[
                    df_theme_constituents['theme_id'].isin(leading_themes)
                ]['symbol_id'].values
                
                # 3. マスクを更新
                mask &= (
                    ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
                    ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
                )

    # Individual RS Condition Rank comparisons
    if strategy.get('is_rs_trend_rank_s14_gt_s21'):
        if 'rs_condition_14_rank' in merged.columns and 'rs_condition_21_rank' in merged.columns:
            mask &= merged['rs_condition_14_rank'] > merged['rs_condition_21_rank']

    if strategy.get('is_rs_trend_rank_s21_gt_s63'):
        if 'rs_condition_21_rank' in merged.columns and 'rs_condition_63_rank' in merged.columns:
            mask &= merged['rs_condition_21_rank'] > merged['rs_condition_63_rank']

    # Individual RS Trend raw value comparisons
    if strategy.get('is_rs_trend_s21_lt_s63'):
        mask &= filter_rs_trend_s21_lt_s63(merged)

    if strategy.get('is_rs_trend_s14_lt_s21'):
        mask &= filter_rs_trend_s14_lt_s21(merged)

    # RS-MACD acceleration filter
    if strategy.get('is_rs_macd_hist_rising_21'):
        mask &= filter_rs_macd_hist_rising_21(merged)

    # Theme RS Condition Rank comparisons
    if strategy.get('is_theme_rs_trend_rank_s14_gt_s21'):
        mask &= filter_theme_rs_trend_rank_s14_gt_s21(merged, df_theme_constituents)

    if strategy.get('is_theme_rs_trend_rank_s21_gt_s63'):
        mask &= filter_theme_rs_trend_rank_s21_gt_s63(merged, df_theme_constituents)

    # Dynamic Theme Indicator numerical filters (e.g. min_theme_rs_trend_s21)
    for key, value in strategy.items():
        if (key.startswith('min_theme_') or key.startswith('max_theme_')) and not key.endswith('_rank'):
            op = '>=' if key.startswith('min_') else '<='
            indicator_col = key[10:]
            if indicator_col in merged.columns:
                theme_rows = merged[merged['category'] == 'テーマ']
                if op == '>=':
                    leading_themes = theme_rows[theme_rows[indicator_col] >= float(value)]['symbol_id'].values
                else:
                    leading_themes = theme_rows[theme_rows[indicator_col] <= float(value)]['symbol_id'].values
                stocks_in_themes = df_theme_constituents[
                    df_theme_constituents['theme_id'].isin(leading_themes)
                ]['symbol_id'].values
                mask &= (
                    ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
                    ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
                )

    # RRG
    intensity_threshold = strategy.get('rrg_intensity_threshold', 0.0)
    if strategy.get('rrg_leading_in') and 'prev_rs_ratio_e21' in merged.columns:
        mask &= filter_rrg_leading_in(merged, intensity_threshold)
    if strategy.get('rrg_lagging_in') and 'prev_rs_ratio_e21' in merged.columns:
        mask &= filter_rrg_lagging_in(merged)
    if strategy.get('rrg_improving_in') and 'prev_rs_ratio_e21' in merged.columns:
        mask &= filter_rrg_improving_in(merged, intensity_threshold)

    # VCP ブレイクアウト（収縮からのピボット上抜けイベント）
    if strategy.get('is_vcp_breakout'):
        mask &= filter_vcp_breakout(
            merged,
            high_window=int(strategy.get('breakout_high_window', 63)),
            vcr_contraction_max=float(strategy.get('vcr_contraction_max', 0.8)),
            base_high_tol=float(strategy.get('base_high_tol', 15.0)),
            near_high_tol=float(strategy.get('near_high_tol', 4.0)),
            breakout_change=float(strategy.get('breakout_change', 4.0)),
            breakout_vol_mult=float(strategy.get('breakout_vol_mult', 1.5)),
            pivot_tol=(float(strategy['pivot_tol'])
                       if strategy.get('pivot_tol') is not None else None),
            base_vol_dry_max=(float(strategy['base_vol_dry_max'])
                              if strategy.get('base_vol_dry_max') is not None else None),
        )

    # 3. Expression Filter
    expression = strategy.get('expression')
    if expression and isinstance(expression, str) and expression.strip():
        try:
            # 小文字の true/false リテラルを pandas.query が解釈できる形へ正規化
            # (API 側 _parse_expression_to_filter と同一の式を受け付けるため)
            import re as _re
            expr_normalized = _re.sub(r'true', 'True', expression)
            expr_normalized = _re.sub(r'false', 'False', expr_normalized)
            # We use query() which is generally safe for simple filters
            # Ensure we only keep rows where expression is true
            merged = merged[mask].copy()
            # reset index to make query simpler
            merged = merged.query(expr_normalized)
            mask = pd.Series(True, index=merged.index) # reset mask as we already filtered
        except Exception as e:
            print(f"Warning: Failed to evaluate expression '{expression}': {e}")

    filtered = merged[mask].copy() if not expression else merged

    # Top-N
    max_hits = strategy.get('max_hits_per_day')
    if max_hits and max_hits > 0 and len(filtered) > max_hits:
        sort_col = alias_map.get(sort_col, sort_col)
        sort_asc = strategy.get('sort_ascending', False)
        if sort_col in filtered.columns:
            filtered = filtered.sort_values(by=sort_col, ascending=sort_asc).head(max_hits)
        else:
            filtered = filtered.head(max_hits)

    return filtered
