import pandas as pd
import numpy as np

from .moving_averages import calc_moving_averages
from .volatility import calc_volatility
from .relative_strength import calc_relative_strength
from .volume_and_trends import calc_volume_and_trends
from .structure_pivot import counter_trend_series, structure_pivot_series
from .zone_break import zone_break_series
from .incremental_merge import normalize_supplied_dtypes

# Export calculate_market_signals so that update_pipeline can import it from here if needed
# or it can import it directly. We'll expose it here for backward compatibility.
from .market_signals import calculate_market_signals

def calculate_indicators(df_daily: pd.DataFrame, df_spy: pd.DataFrame = None, state: bool = None) -> pd.DataFrame:
    """
    日足データ(T2)を受け取り、テクニカル指標とSPYとの相対評価を算出して返す純粋な関数。

    単一実装・2つの入口にする（T3 増分化計画 5-4／5-4b で入力契約を改訂）。
    - state=None（既定）: 現行と完全に同一の挙動。全期間計算
      （`--rebuild-from T3` や検証オラクルが使う）。`df_daily` は生価格のみでよい。
    - state=True（増分計算。5-4b）: `df_daily` は直近 K+1 行を渡す契約に変わる。
      生の価格列（open/high/low/close/volume）は全行に値が要るが、**T3 の計算列
      （`indicators` テーブル相当の列）は行 0..K-1 に保存済みの実値が入っており、
      最終行（K）だけが NaN（＝これから計算する日）**という前提（K は
      `backend/indicators/incremental_state_registry.py` の `max_lookback()` 以上）。
      RECURSIVE 型列（`backend/indicators/incremental_state_registry.py` の
      `ColumnKind.RECURSIVE`）は、供給された行 K-1 の値をシードに最終行だけを
      1歩計算し、供給済み履歴とマージする（`incremental_merge.py`）。WINDOW 型列は
      通常どおり計算すれば正しくなる（マージ済みのRECURSIVE型列・生価格が入力のため）。
      5-4 版は `state` をスカラー辞書（前日値のみ）として受け取り、増分ウィンドウ
      全体を再帰的に歩き直す設計だったが、WINDOW 型列の rolling 窓が増分ウィンドウ内
      でしか育たず不正確になる問題があった（詳細: `incremental_merge.py` モジュール
      docstring）。5-4b はこれを「保存済み T3 列を df 自体に供給する」設計に改め、
      `state` は単なる増分モードのフラグに簡素化した。

    Args:
        df_daily: 対象銘柄の日足DataFrame。state=None なら (date, open, high, low,
            close, volume) のみでよい。state=True なら上記の増分契約に従う
            （T3 の全計算列を含む K+1 行）。
        df_spy:   SPYの日足DataFrame (date, close, volume) — RS系・Relative Volume計算に使用
        state:    増分計算のフラグ（truthy で増分モード）。None/False なら現行どおり全期間計算
    Returns:
        各種インジケーターカラムが追加されたDataFrame
    """
    df = df_daily.copy()
    df = df.sort_values('date').reset_index(drop=True)

    # 増分呼び出し（state truthy）では、供給されたT3列のdtypeを正規化する（5-4d）。
    # 前回の全期間計算の戻り値は末尾で NaN→None 変換を経ており、履歴のどこかに
    # 1つでもNaNがあれば列全体がobject dtypeになる（既存の挙動）。object dtypeのまま
    # np.where の分岐（volatility.py の sma50_atr_mult 等）に渡すと、ガードで弾かれる
    # はずの0除算がPythonのスカラー演算として実行され ZeroDivisionError になるため、
    # ここでfloat64へ強制変換して防ぐ。state=None（全期間計算）はdf_dailyに生価格のみ
    # しか無いため無害だが、挙動を一切変えないという絶対条件のため明示的にガードする。
    if state:
        df = normalize_supplied_dtypes(df)

    # Returns (Prev Close Base)
    df['change_1d_pct'] = df['close'].pct_change() * 100
    df['change_1w_pct'] = df['close'].pct_change(5) * 100
    df['change_1m_pct'] = df['close'].pct_change(20) * 100

    if df.empty:
        return df

    # 1. Moving Averages
    df = calc_moving_averages(df, state)

    # 2. Volatility (Requires MAs)
    df = calc_volatility(df, state)

    # 3. Relative Strength (Requires SPY and MAs for dots)
    df = calc_relative_strength(df, df_spy, state)

    # 4. Volume and Trends (Requires MAs and SPY)
    df = calc_volume_and_trends(df)

    # 5. Structure Pivot (LL-HL). 価格のみから決まるので SPY 非依存。
    #    確定遅延（ピボットは L 本先まで確定しない）は関数側で保たれている。
    sp_pivot, sp_hl = structure_pivot_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )
    df['sp_pivot'] = sp_pivot
    df['sp_hl'] = sp_hl
    #    カウンタートレンド線は構造が無い期間に引かれる（sp_pivot とは排他）
    df['sp_counter'] = counter_trend_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )

    # 6. Direction via Zone Break。価格のみから決まるので SPY 非依存。
    #    確定遅延は無い（当日の確定closeだけでブレイク判定が決まる。フラクタル自体の
    #    確定は1本遅れるが先読みではない。doc/completed/zone_break_plan.md §3.1 参照）
    is_bull, zb_ssl, zb_bsl, is_weak = zone_break_series(
        df['high'].to_numpy(dtype=float),
        df['low'].to_numpy(dtype=float),
        df['close'].to_numpy(dtype=float),
    )
    df['is_zone_break_bull'] = is_bull
    df['zb_ssl'] = zb_ssl
    df['zb_bsl'] = zb_bsl
    df['is_zone_break_weak'] = is_weak

    # Sanitize: replace np.nan with None for SQLAlchemy
    df = df.replace({np.nan: None})
    
    return df

def calculate_relative_ranks(df_all_indicators: pd.DataFrame, group_col: str, indicator_col: str) -> pd.DataFrame:
    """
    全銘柄の計算済みデータを受け取り、日付ごとのグループ内 Relative Rank (Percentile) を計算。
    対象: rs_ratio_21, rs_ratio_63 など T4 に保存する指標
    """
    if df_all_indicators.empty or indicator_col not in df_all_indicators.columns:
        return pd.DataFrame()
        
    df_result = df_all_indicators.copy()
    df_result['percent_rank'] = df_result.groupby(['date', group_col])[indicator_col].rank(pct=True, ascending=True)
    
    res = df_result[['symbol_id', 'date', group_col, indicator_col, 'percent_rank']].copy()
    res = res.rename(columns={group_col: 'group_name'})
    res['indicator_name'] = indicator_col
    res = res.drop(columns=[indicator_col])
    return res
