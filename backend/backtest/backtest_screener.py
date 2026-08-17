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
    from indicators import screener_registry
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
    from backend.indicators import screener_registry


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

    # Derived columns（必要カラム解決より前に生成すること。既知カラム判定に使われるため）
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

    # --- レジストリで必要カラムを解決する（doc/in_progress/screener_filter_unification_plan.md §3.1.4） ---
    from backend.db.models import RelativeRank
    known_columns = set(merged.columns) | set(screener_registry.VIRTUAL_COLUMNS)
    # RelativeRank の実カラム。df_ranks は long 形式でランク名が列に出ないため使えない。
    rank_columns = {
        c for c in RelativeRank.__table__.columns.keys()
        if c not in ('id', 'symbol_id', 'date', 'group_name')
    }
    # 並べ替えキー（Top-N 抽出用）。sort_column は METADATA_KEYS なのでフィルタキーとしては
    # 解決されないが、**列としては供給されていないと Top-N の結果が変わる**ため
    # extra_columns で明示的に要求する（計画書 §7 P1-6）。
    sort_col = strategy.get('sort_column', 'rs_ratio_rank_e21')
    required = screener_registry.resolve_required_columns(
        strategy, known_columns, rank_columns, extra_columns=(sort_col,)
    )

    # --- RS Rank merge（必要なランクだけを引く） ---
    # 引く対象 = ハード要求 + ソフト要求（並べ替えキー）。
    # deny-by-default と欠落検査には **ハード要求のみ** を使う（§7 P1-6）。
    ranks_to_merge = required.ranks | required.optional
    if ranks_to_merge:
        # 呼び出し側（scenario_runner 等）が既に事前マージ済みなら再取得しない
        has_all_premerged = all(
            screener_registry.to_frame_column(c) in merged.columns for c in ranks_to_merge
        )
        if has_all_premerged:
            ranks_day = None
        else:
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

            if ranks_day is None:
                # ランクデータが本当に手に入らなかった場合のみ deny-by-default とする
                # （has_all_premerged=True のときの ranks_day=None は「取得済みなのでスキップ」
                # という別の意味なので、ここには来ない）
                #
                # ただし **ハード要求が無い**（＝欲しかったのは並べ替えキーだけ）場合は
                # deny してはいけない。ソート列が無ければ既存実装どおり head() に
                # フォールバックするだけで、フィルタの意味は変わらないため。
                # ここを区別しないと、ランクを使わない戦略（RRG 系等）が
                # ランクデータの無い環境で常に0件になる（§7 P1-6）。
                if required.ranks:
                    return pd.DataFrame(columns=merged.columns)
                ranks_day = None

            for canonical in (ranks_to_merge if ranks_day is not None else ()):
                frame_col = screener_registry.to_frame_column(canonical)
                r_df = ranks_day[ranks_day['indicator_name'] == canonical][['symbol_id', 'percent_rank']].rename(
                    columns={'percent_rank': frame_col}
                )
                merged = merged.merge(r_df, on='symbol_id', how='left').reset_index(drop=True)

    # --- RRG / RS-MACD / VCP breakout の前日マージ ---
    # 呼び出し側（API の load_cross_section 等）が既に prev_ 列を持たせている場合は再マージしない。
    # ランク側の has_all_premerged と対称。ガードが無いと、既に prev_ 列があるフレームに
    # 対して二重マージが走り pandas が _x/_y サフィックスを付けるため、素の prev_ 列名が
    # 消えて **RRG / RS-MACD / VCP フィルタが丸ごと壊れる**
    # （2026-07-28 に流動性床を無効化した _x/_y 衝突と同型。計画書 §1.2 F1）。
    _prev_premerged = bool(required.prev) and all(
        f'prev_{c}' in merged.columns for c in required.prev
    )
    if required.prev and prev_date is not None and not _prev_premerged:
        if ind_day_cache is not None:
            ind_prev_all = ind_day_cache.get(prev_date)
        else:
            ind_prev_all = df_ind[df_ind['date'] == prev_date]

        if ind_prev_all is not None:
            cols_to_use = ['symbol_id']
            rename_dict = {}
            for c in required.prev:
                if c in ind_prev_all.columns:
                    cols_to_use.append(c)
                    rename_dict[c] = f'prev_{c}'
            ind_prev = ind_prev_all[cols_to_use].rename(columns=rename_dict)
            merged = merged.merge(ind_prev, on='symbol_id', how='left').reset_index(drop=True)

    # --- fail-loud: 必要カラムが実際に揃っているかを検査する（Phase 1 の核心） ---
    # is_theme_rs_ratio_e21_gt_e63 / is_theme_rs_ratio_e14_gt_e21（生値のテーマ比較）は、
    # merged ではなく当日の df_ind 全体スライス（下記ディスパッチの ind_day_for_theme）を
    # 直接参照する設計（dispatch 構造は Phase 1 では変更しない）。そのため merged 上の
    # 存在チェックからは除外する（これらの列は merged ではなく ind_day_for_theme 側で
    # 検証されるべきものであり、この検査で落とすのは誤検知になる）。
    _theme_raw_value_exempt = set()
    for _theme_key in ('is_theme_rs_ratio_e21_gt_e63', 'is_theme_rs_ratio_e14_gt_e21'):
        if _theme_key in strategy:
            _theme_raw_value_exempt |= set(screener_registry.EXPLICIT_SPECS[_theme_key].requires)

    missing_columns = []
    for canonical in (required.today | required.ranks) - _theme_raw_value_exempt:
        frame_col = screener_registry.to_frame_column(canonical)
        if frame_col not in merged.columns:
            missing_columns.append(frame_col)

    # 前日列は prev_date が実際に与えられているときのみ検査する。prev_date=None
    # （バックテスト初日等、前日データが原理的に存在しない）は RRG/RS-MACD 系フィルタが
    # 前日比較なしで no-op にフォールバックする既存の意図的な設計であり、fail-loud の対象外。
    # is_vcp_breakout は前日列が無い場合に「全 False」を返す deny-by-default 設計
    # （screener_filters.filter_vcp_breakout の docstring 参照）なので、prev_date の有無に
    # 関わらずその prev_requires は常に検査対象から除外する（検査で落とすと意図が壊れる）。
    if prev_date is not None:
        _vcp_prev_exempt = set(screener_registry.EXPLICIT_SPECS['is_vcp_breakout'].prev_requires)
        for canonical in required.prev:
            if canonical in _vcp_prev_exempt:
                continue
            frame_col = f'prev_{screener_registry.to_frame_column(canonical)}'
            if frame_col not in merged.columns:
                missing_columns.append(frame_col)

    if missing_columns:
        raise screener_registry.MissingFilterColumnError(
            f"apply_filters_to_df: 必要なカラムが供給されていません: {sorted(set(missing_columns))}"
        )

    # --- Build the mask ---
    mask = pd.Series(True, index=merged.index)

    # 数値 / ランク / bool_column フィルタの適用（レジストリ駆動。kind ごとに専用ブロックへ委譲する
    # ものは kind で判別してここでは skip する: close_gt / theme_numeric / theme_rank / special）
    for key, value in strategy.items():
        if screener_registry.is_non_filter_key(key):
            continue
        spec = screener_registry.resolve_filter_spec(key, known_columns, rank_columns)
        if spec.kind not in ('numeric', 'rank', 'bool_column'):
            continue
        col = screener_registry.to_frame_column(spec.column)
        if col == 'market_cap' and 'market_cap' in merged.columns and spec.op == '>=':
            mask &= (merged['market_cap'] >= value) | (merged['category'] == 'テーマ')
        elif col in merged.columns:
            if spec.op == '>=':   mask &= merged[col] >= value
            elif spec.op == '<=': mask &= merged[col] <= value
            elif spec.op == '==': mask &= merged[col] == value

    # 2. Dynamic Close-Above Filters (e.g. close_gt_sma50, close_gt_ema21, close_gt_ema50, or is_close_gt_*)
    for key, value in strategy.items():
        if (key.startswith('close_gt_') or key.startswith('is_close_gt_')) and value is True:
            ind_name = key[9:] if key.startswith('close_gt_') else key[12:]
            
            # 短縮名（ema21 -> ema_21）の正規化はレジストリに一本化した（§7 P2-1）
            ind_name = screener_registry.normalize_close_gt_target(ind_name)

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
            expr_normalized = _re.sub(r'\btrue\b', 'True', expression)
            expr_normalized = _re.sub(r'\bfalse\b', 'False', expr_normalized)
            # We use query() which is generally safe for simple filters
            # Ensure we only keep rows where expression is true
            merged = merged[mask].copy()
            # reset index to make query simpler
            merged = merged.query(expr_normalized)
            mask = pd.Series(True, index=merged.index) # reset mask as we already filtered
        except Exception as e:
            print(f"Warning: Failed to evaluate expression '{expression}': {e}")

    filtered = merged[mask].copy() if not expression else merged

    # 最終出力から常に除外するカテゴリの定義は screener_registry.OUTPUT_EXCLUDED_CATEGORIES
    # に一本化している（screener_router.py と共通。§5 Phase 1）。適用位置（フィルタ後・
    # 出力直前）は変えないこと（前に出すとリーディングテーマ判定が壊れる）。
    if 'category' in filtered.columns:
        filtered = filtered[~filtered['category'].isin(screener_registry.OUTPUT_EXCLUDED_CATEGORIES)]

    # Top-N
    max_hits = strategy.get('max_hits_per_day')
    if max_hits and max_hits > 0 and len(filtered) > max_hits:
        sort_col = screener_registry.to_frame_column(sort_col)
        sort_asc = strategy.get('sort_ascending', False)
        if sort_col in filtered.columns:
            filtered = filtered.sort_values(by=sort_col, ascending=sort_asc).head(max_hits)
        else:
            filtered = filtered.head(max_hits)

    return filtered
