"""
backtest_screener.py — Signal Scanner for Backtest Engine

Evaluates screener filter conditions against preloaded pandas DataFrames
and returns signal records for each trading day.
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


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
) -> List[SignalRecord]:
    """
    Scan for signals on a specific date using the given strategy parameters.

    Args:
        target_date: The date to evaluate.
        df_ind: Full indicators DataFrame (preloaded).
        df_price: Full daily prices DataFrame (preloaded).
        df_ranks: Full relative ranks DataFrame (preloaded).
        df_symbols: Full symbols DataFrame (preloaded).
        df_theme_constituents: Full theme constituents DataFrame (preloaded).
        strategy: Strategy parameter dict from TOML config.
        prev_date: Previous trading date (for RRG transition check).

    Returns:
        List of SignalRecord for stocks matching the strategy on target_date.
    """
    # Filter indicator data for target date
    ind_day = df_ind[df_ind['date'] == target_date].copy()
    if ind_day.empty:
        return []

    # Filter price data for target date
    price_day = df_price[df_price['date'] == target_date].copy()
    if price_day.empty:
        return []

    # Merge indicator + price + symbol info
    merged = ind_day.merge(
        price_day[['symbol_id', 'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap']],
        on=['symbol_id', 'date'],
        how='inner'
    )
    merged = merged.merge(
        df_symbols[['id', 'ticker', 'name', 'category', 'active']],
        left_on='symbol_id', right_on='id', how='inner'
    )

    # Base filter: active symbols, category in (テーマ, 個別)
    merged = merged[(merged['active'] == 1) & (merged['category'].isin(['テーマ', '個別']))].copy()
    if merged.empty:
        return []

    # Reset index after initial filtering to avoid alignment issues
    merged = merged.reset_index(drop=True)

    # Calculate 1D gain % from open
    merged['gain_1d_pct'] = np.where(
        merged['open'] > 0,
        (merged['close'] - merged['open']) / merged['open'] * 100,
        0.0
    )

    # Calculate EMA21 distance %
    merged['dist_21ema_pct'] = np.where(
        merged['ema_21'] > 0,
        (merged['close'] - merged['ema_21']) / merged['ema_21'] * 100,
        0.0
    )

    # --- RS Rank merge (do all merges first, then filter) ---
    sort_col = strategy.get('sort_column', 'rs21_rank')
    needs_rs21 = 'min_rs_ratio_21_rank' in strategy or strategy.get('rs_rank_21_gt_63') or sort_col in ('rs21_rank', 'rs_ratio_21_rank')
    needs_rs63 = strategy.get('rs_rank_21_gt_63') or sort_col in ('rs63_rank', 'rs_ratio_63_rank')

    if needs_rs21 or needs_rs63:
        past_ranks = df_ranks[df_ranks['date'] <= target_date]
        if not past_ranks.empty:
            rank_date = past_ranks['date'].max()
            ranks_day = df_ranks[df_ranks['date'] == rank_date]

            if needs_rs21:
                r21 = ranks_day[ranks_day['indicator_name'] == 'rs_ratio_21'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs21_rank'}
                )
                merged = merged.merge(r21, on='symbol_id', how='left').reset_index(drop=True)

            if needs_rs63:
                r63 = ranks_day[ranks_day['indicator_name'] == 'rs_ratio_63'][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': 'rs63_rank'}
                )
                merged = merged.merge(r63, on='symbol_id', how='left').reset_index(drop=True)
        else:
            if 'min_rs_ratio_21_rank' in strategy or strategy.get('rs_rank_21_gt_63'):
                return []

    # --- RRG merges ---
    if strategy.get('rrg_leading_in') and prev_date is not None:
        ind_prev = df_ind[df_ind['date'] == prev_date][['symbol_id', 'rs_ratio_21', 'rs_momentum_21']].rename(
            columns={'rs_ratio_21': 'prev_rs_ratio_21', 'rs_momentum_21': 'prev_rs_momentum_21'}
        )
        merged = merged.merge(ind_prev, on='symbol_id', how='left').reset_index(drop=True)

    if (strategy.get('rrg_lagging_in') or strategy.get('rrg_improving_in')) and prev_date is not None:
        if 'prev_rs_ratio_21' not in merged.columns:
            ind_prev = df_ind[df_ind['date'] == prev_date][['symbol_id', 'rs_ratio_21', 'rs_momentum_21']].rename(
                columns={'rs_ratio_21': 'prev_rs_ratio_21', 'rs_momentum_21': 'prev_rs_momentum_21'}
            )
            merged = merged.merge(ind_prev, on='symbol_id', how='left').reset_index(drop=True)

    # --- Derived columns for filtering ---
    if 'gain_1d_pct' not in merged.columns and 'close' in merged.columns and 'open' in merged.columns:
        merged['gain_1d_pct'] = (merged['close'] - merged['open']) / merged['open'] * 100.0
    
    if 'dist_21ema_pct' not in merged.columns and 'close' in merged.columns and 'ema_21' in merged.columns:
        merged['dist_21ema_pct'] = (merged['close'] - merged['ema_21']) / merged['ema_21'] * 100.0

    # --- Now build the mask after all merges are done ---
    mask = pd.Series(True, index=merged.index)

    # Alias map for handling legacy TOML names vs actual DataFrame column names
    alias_map = {
        '1d_gain_pct': 'gain_1d_pct',
        'rs_ratio_21_rank': 'rs21_rank',
        'rs_ratio_63_rank': 'rs63_rank',
        'trend_template_ok': 'trend_template_ok'
    }

    # 1. Generic Dynamic Filters
    for key, value in strategy.items():
        if key in ('name', 'description', 'max_hits_per_day', 'sort_column', 'sort_ascending'):
            continue
            
        col = key
        op = None

        if key.startswith('min_'):
            col = key[4:]
            op = '>='
        elif key.startswith('max_'):
            col = key[4:]
            op = '<='
        elif isinstance(value, bool) or key.startswith('bool_') or key.startswith('is_') or key.startswith('has_'):
            op = '=='
            if key.startswith('bool_'): col = key[5:]
            elif key.startswith('is_'): col = key[3:]
            elif key.startswith('has_'): col = key[4:]

        if op:
            col = alias_map.get(col, col)
            
            # Special exemption for market_cap logic on themes
            if col == 'market_cap' and 'market_cap' in merged.columns and op == '>=':
                mask &= (merged['market_cap'] >= value) | (merged['category'] == 'テーマ')
            elif col in merged.columns:
                if op == '>=':   mask &= merged[col] >= value
                elif op == '<=': mask &= merged[col] <= value
                elif op == '==': mask &= merged[col] == value

    # 2. Explicit / Complex Filters
    if strategy.get('close_gt_sma50') and 'close' in merged.columns and 'sma_50' in merged.columns:
        mask &= merged['close'] > merged['sma_50']

    if strategy.get('rs_rank_21_gt_63') and 'rs21_rank' in merged.columns and 'rs63_rank' in merged.columns:
        mask &= merged['rs21_rank'] > merged['rs63_rank']

    if strategy.get('theme_rs21_gt_63'):
        theme_ind = df_ind[(df_ind['date'] == target_date)].merge(
            df_symbols[df_symbols['category'] == 'テーマ'][['id']],
            left_on='symbol_id', right_on='id', how='inner'
        )
        leading_themes = theme_ind[theme_ind['rs_ratio_21'] > theme_ind['rs_ratio_63']]['symbol_id'].values

        stocks_in_themes = df_theme_constituents[
            df_theme_constituents['theme_id'].isin(leading_themes)
        ]['symbol_id'].values

        theme_mask = (
            ((merged['category'] == 'テーマ') & (merged['symbol_id'].isin(leading_themes))) |
            ((merged['category'] == '個別') & (merged['symbol_id'].isin(stocks_in_themes)))
        )
        mask &= theme_mask

    # RRG Transition Logic
    if strategy.get('rrg_leading_in') and 'prev_rs_ratio_21' in merged.columns:
        mask &= (
            (merged['rs_ratio_21'] > 0) & (merged['rs_momentum_21'] > 0) &
            ((merged['prev_rs_ratio_21'] <= 0) | (merged['prev_rs_momentum_21'] <= 0))
        )

    if strategy.get('rrg_lagging_in') and 'prev_rs_ratio_21' in merged.columns:
        mask &= (
            (merged['rs_ratio_21'] < 0) & (merged['rs_momentum_21'] < 0) &
            ((merged['prev_rs_ratio_21'] >= 0) | (merged['prev_rs_momentum_21'] >= 0))
        )

    if strategy.get('rrg_improving_in') and 'prev_rs_ratio_21' in merged.columns:
        mask &= (
            (merged['rs_ratio_21'] < 0) & (merged['rs_momentum_21'] > 0) &
            (merged['prev_rs_ratio_21'] < 0) & (merged['prev_rs_momentum_21'] <= 0)
        )

    # Apply final mask
    filtered = merged[mask].copy()

    # --- Top-N Extraction Filter ---
    max_hits = strategy.get('max_hits_per_day')
    if max_hits and max_hits > 0 and len(filtered) > max_hits:
        sort_col = strategy.get('sort_column', 'rs21_rank')
        sort_col = alias_map.get(sort_col, sort_col)
        sort_asc = strategy.get('sort_ascending', False)
        
        if sort_col in filtered.columns:
            filtered = filtered.sort_values(by=sort_col, ascending=sort_asc).head(max_hits)
        else:
            # Fallback to random or index if sort column doesn't exist
            filtered = filtered.head(max_hits)

    # Build signal records
    signals = []
    for _, row in filtered.iterrows():
        signals.append(SignalRecord(
            symbol_id=int(row['symbol_id']),
            ticker=row['ticker'],
            date=target_date,
            entry_price=float(row['close']),
        ))

    return signals
