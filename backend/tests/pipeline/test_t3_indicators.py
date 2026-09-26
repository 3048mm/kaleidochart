"""T3 ワーカー（`_calculate_t3_worker`）の増分化テスト。

計画書: doc/in_progress/t3_incremental_plan.md 5-6（§3.2・§3.5）・5-6b

`_calculate_t3_worker` は「生価格 K+1 本 ＋ 保存済み T3 中間列 K 本」を
`calculate_indicators(df, spy_df, state=True)` に渡す増分経路と、
従来どおり全価格履歴を渡す全期間計算（フォールバック）経路の2つを持つ
（K = `max_lookback()`）。本ファイルはこの分岐そのものと、増分経路の
結果が全期間計算と一致することを固定する。

## 分岐条件（§3.5）

以下のいずれかに該当すると state=None（全期間計算）にフォールバックする:

1. 保存済み T3 行が存在しない（`t3_max` が None）
2. 保存済み行数が K（`max_lookback()`）に満たない
3. 新規に書く日付がちょうど1日ぶんでない（0日・2日以上）
4. RECURSIVE型列（前日値を継ぐ列）の供給履歴に NaN がある

該当しなければ増分経路（`state=True`）を使う。

## 戻り値契約（5-6b）

`_calculate_t3_worker` の戻り値は `(ticker, sid, records, fallback_reason)` の
4要素タプル。増分経路が使われた場合 `fallback_reason` は None、フォールバック
した場合は上記1〜4に対応する `FALLBACK_REASON_*` のいずれか（4の場合は
`"null_recursive_column:列名,列名"` の形式で NULL だった列名も付与される）。

## DB スキーマについて

`test_adjust_symbol_split.py` の慣例に倣い、SQLAlchemy を経由せず
raw sqlite3 で必要最小限のテーブルだけを作る。`indicators` テーブルの
列は `INDICATOR_COLUMN_REGISTRY`（本番の Indicator モデルと1:1対応済み。
`test_incremental_state_registry.py` で固定）から機械的に導出し、
手書きの列リストにしない。
"""
import logging
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
backend_dir = os.path.join(project_root, "backend")
for _p in (project_root, backend_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import indicators.calculate as calc_module  # noqa: E402
from indicators.calculate import calculate_indicators  # noqa: E402
from indicators.incremental_state_registry import (  # noqa: E402
    INDICATOR_COLUMN_REGISTRY, max_lookback,
)
from pipeline.phases.t3_indicators import (  # noqa: E402
    _calculate_t3_worker,
    _log_fallback_summary,
    FALLBACK_REASON_NO_SAVED_ROWS,
    FALLBACK_REASON_INSUFFICIENT_ROWS,
    FALLBACK_REASON_MULTI_DAY_GAP,
    FALLBACK_REASON_NULL_RECURSIVE_COLUMN,
    FALLBACK_REASON_WARMUP_UNDETERMINED,
)

SID_TARGET = 2
SID_SPY = 1
N_TOTAL = 1400
K = max_lookback()
MARGIN = 10

IND_COLS = sorted(INDICATOR_COLUMN_REGISTRY.keys())

# zone_break系4列は必要履歴が原理的に非有界（計画書§8）。ワーカーが供給する
# K本（=max_lookback()）ではこのテストの乱数シードで全履歴一致を保証できない
# （5-4c/5-4e で実測済みの既知の近似）ため、本テスト（配線の検証が目的）では
# 比較対象から除く。zone_break自体の精度は test_calculate_incremental_equivalence.py
# の TestZoneBreakHotWindowEquivalence が別途固定している。
_NON_STRICT_COLUMNS = frozenset({'zb_ssl', 'zb_bsl', 'is_zone_break_bull', 'is_zone_break_weak'})


def _make_series(n, seed, base, vol=1.2):
    """合成 OHLCV 系列を作る（test_calculate_incremental_equivalence.py と同趣旨）。"""
    rng = np.random.default_rng(seed)
    walk = np.cumsum(rng.normal(0.03, vol, n))
    close = np.abs(base + walk) + 10.0
    high = close + np.abs(rng.normal(1.0, 0.3, n))
    low = close - np.abs(rng.normal(1.0, 0.3, n))
    low = np.minimum(low, close - 0.01)
    openp = close + rng.normal(0, 0.2, n)
    volume = rng.integers(100_000, 900_000, n).astype(float)
    return pd.DataFrame({
        'open': openp, 'high': high, 'low': low, 'close': close, 'volume': volume,
    })


def _build_scenario():
    """SPY・対象銘柄の全履歴価格と、全期間計算（オラクル）した指標を作る。"""
    dates = pd.bdate_range('2019-01-02', periods=N_TOTAL).date
    px = _make_series(N_TOTAL, seed=1, base=100.0)
    df_full = pd.DataFrame({'date': dates}).join(px)

    spy_px = _make_series(N_TOTAL, seed=2, base=300.0, vol=2.0)
    spy_full = pd.DataFrame({'date': dates}).join(spy_px)

    full_res = calculate_indicators(df_full.copy(), spy_full.copy(), state=None)
    spy_price_only = spy_full[['date', 'close', 'volume']].reset_index(drop=True)
    return dates, df_full, spy_full, full_res, spy_price_only


def _build_short_scenario(n_short):
    """短い履歴（正当なウォームアップ中NULLを再現するための）シナリオ。

    5-15b（code-review指摘1）: `_build_scenario()`（N_TOTAL=1400）は履歴が
    十分に長く、増分ウィンドウ（K=`max_lookback()`本）内のどの位置も
    warmup_bars を大きく超えるため、「壊れているから NULL」の欠陥ケースしか
    作れない。本ヘルパーは `n_short` を K に近い値にすることで、増分ウィンドウが
    その銘柄の履歴の先頭付近を含むようにし、オラクル自身が正当に NULL を返す
    区間（＝ウォームアップ中）を再現する。
    """
    dates = pd.bdate_range('2019-01-02', periods=n_short).date
    px = _make_series(n_short, seed=3, base=150.0)
    df_full = pd.DataFrame({'date': dates}).join(px)

    spy_px = _make_series(n_short, seed=4, base=310.0, vol=2.0)
    spy_full = pd.DataFrame({'date': dates}).join(spy_px)

    full_res = calculate_indicators(df_full.copy(), spy_full.copy(), state=None)
    spy_price_only = spy_full[['date', 'close', 'volume']].reset_index(drop=True)
    return dates, df_full, spy_full, full_res, spy_price_only


def _build_spy_scenario():
    """SPY自身をワーカーへ渡すためのシナリオ（5-15b・code-review指摘2）。

    `_calculate_t3_worker` はticker=='SPY'のとき`spy_df_arg=None`で
    `calculate_indicators`を呼ぶ（自分自身に対する相対強度は定義されない）。
    そのためSPYのオラクルは、SPY自身の価格系列に対して df_spy=None で計算する。
    """
    dates = pd.bdate_range('2019-01-02', periods=N_TOTAL).date
    spy_px = _make_series(N_TOTAL, seed=2, base=300.0, vol=2.0)
    spy_full = pd.DataFrame({'date': dates}).join(spy_px)
    full_res_spy = calculate_indicators(spy_full.copy(), None, state=None)
    # df_spy=None のときrs_*系列はcalc_relative_strengthの早期returnで
    # 列自体が作られない（rs_blue_dot_age/rs_red_dot_ageを除く）。本番では
    # `row.get(col)`でNoneとして書き込まれる（sync_phase_t3_indicators）ため、
    # ここでも欠けている列をNaN列として補い、実際のDB格納を模す。
    for col in IND_COLS:
        if col not in full_res_spy.columns:
            full_res_spy[col] = np.nan
    return dates, spy_full, full_res_spy


def _to_sqlite_value(v):
    """numpy スカラー / bool / NaN を sqlite3 が扱える型に変換する。"""
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else float(v)
    return v


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / 't3_incremental_test.db')
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE symbols (id INTEGER PRIMARY KEY, ticker TEXT, exchange TEXT, category TEXT, active INTEGER)')
    con.execute('''CREATE TABLE daily_prices (
        id INTEGER PRIMARY KEY, symbol_id INTEGER, date TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL)''')
    ind_col_defs = ', '.join(f'{c} REAL' for c in IND_COLS)
    con.execute(f'CREATE TABLE indicators (id INTEGER PRIMARY KEY, symbol_id INTEGER, date TEXT, {ind_col_defs})')
    con.executemany('INSERT INTO symbols (id, ticker, exchange, category, active) VALUES (?, ?, ?, ?, 1)', [
        (SID_TARGET, 'TEST', 'NYSE', '個別'),
        (SID_SPY, 'SPY', 'NYSE', 'ETF'),
    ])
    con.commit()
    con.close()
    return path


def _insert_prices(db_path, sid, df):
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            'INSERT INTO daily_prices (symbol_id, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)',
            [(sid, str(r.date), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
             for r in df.itertuples()],
        )
        con.commit()
    finally:
        con.close()


def _insert_indicators(db_path, sid, res, upto_idx, from_idx=0):
    """`res`（`calculate_indicators` の戻り値）の [from_idx, upto_idx] 行を
    `indicators` テーブルへ書き込む（保存済み T3 履歴を模す）。"""
    con = sqlite3.connect(db_path)
    try:
        cols = ['date'] + IND_COLS
        col_sql = ', '.join(['symbol_id'] + cols)
        placeholders = ','.join(['?'] * (len(cols) + 1))
        rows = []
        for _, row in res.iloc[from_idx:upto_idx + 1].iterrows():
            values = [sid, str(row['date'])] + [_to_sqlite_value(row[c]) for c in IND_COLS]
            rows.append(values)
        con.executemany(f'INSERT INTO indicators ({col_sql}) VALUES ({placeholders})', rows)
        con.commit()
    finally:
        con.close()


def _patch_calculate_indicators(monkeypatch):
    """`calculate_indicators` への呼び出しを記録するスパイに差し替える。

    `_calculate_t3_worker` は関数内で `from indicators.calculate import
    calculate_indicators` とローカルインポートしているため、モジュール属性を
    差し替えれば（呼び出しごとに再取得されるので）検知できる。
    """
    calls = []
    real = calc_module.calculate_indicators

    def _spy(df, df_spy=None, state=None):
        calls.append(state)
        return real(df, df_spy, state)

    monkeypatch.setattr(calc_module, 'calculate_indicators', _spy)
    return calls


def _find_mismatches(row_a, row_b, columns):
    mismatches = []
    for col in columns:
        a, b = row_a[col], row_b[col]
        a_nan = a is None or (isinstance(a, float) and np.isnan(a))
        b_nan = b is None or (isinstance(b, float) and np.isnan(b))
        if a_nan and b_nan:
            continue
        if a_nan != b_nan:
            mismatches.append((col, a, b, 'NaN不一致'))
            continue
        try:
            af, bf = float(a), float(b)
        except (TypeError, ValueError):
            if a != b:
                mismatches.append((col, a, b, '非数値の不一致'))
            continue
        if not np.isclose(af, bf, rtol=1e-9, atol=1e-9):
            mismatches.append((col, af, bf, 'rtol/atol=1e-9で不一致'))
    return mismatches


class TestCalculateT3WorkerIncrementalPath:
    """通常の日次（1日ぶん）で増分経路が使われることの固定。"""

    def test_normal_daily_uses_incremental_path(self, db_path, monkeypatch):
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        # indicators はちょうど昨日（N_TOTAL-2）まで保存済み、daily_prices は
        # 今日（N_TOTAL-1）まで入っている＝新規1日ぶんの通常の日次。
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 2)

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [True], f'増分経路（state=True）が1回だけ呼ばれるはずが: {calls}'
        assert len(records) == 1
        assert records[0]['date'] == dates[-1]
        assert fallback_reason is None, f'増分経路が使われた場合 fallback_reason は None のはずが: {fallback_reason}'

    def test_incremental_result_matches_full_recompute(self, db_path, monkeypatch):
        """増分経路で書かれた行が、全期間計算の同じ日付の行と一致すること（本項目の要）。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 2)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert fallback_reason is None
        assert len(records) == 1
        got_row = records[0]
        ref_row = full_res.iloc[-1]

        compare_cols = [c for c in IND_COLS if c not in _NON_STRICT_COLUMNS]
        mismatches = _find_mismatches(ref_row, got_row, compare_cols)
        assert not mismatches, f'増分経路の結果が全期間計算と不一致: {mismatches}'


class TestCalculateT3WorkerFallback:
    """§3.5 のフォールバック条件の固定。"""

    def test_no_saved_t3_rows_falls_back(self, db_path, monkeypatch):
        """保存済み T3 行が存在しない（新規上場・オンボード直後）場合は全期間計算。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        _insert_prices(db_path, SID_TARGET, df_full)
        # indicators は一切書き込まない -> t3_max は None

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', None, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert len(records) > 0
        assert fallback_reason == FALLBACK_REASON_NO_SAVED_ROWS

    def test_insufficient_saved_rows_falls_back(self, db_path, monkeypatch):
        """保存済み行数が K（max_lookback()）に満たない場合は全期間計算。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        # 直近 K-50 行だけを保存済みとする（K本に満たない）。
        _insert_indicators(
            db_path, SID_TARGET, full_res,
            upto_idx=N_TOTAL - 2, from_idx=N_TOTAL - 2 - (K - 50) + 1,
        )

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert len(records) == 1
        assert records[0]['date'] == dates[-1]
        assert fallback_reason == FALLBACK_REASON_INSUFFICIENT_ROWS

    def test_multi_day_gap_falls_back(self, db_path, monkeypatch):
        """新規に書く日付が2日ぶん以上ある場合は全期間計算にフォールバックする。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        # indicators は N_TOTAL-3 まで、daily_prices は N_TOTAL-1 まで
        # -> 新規2日ぶん（連休明け・障害復旧後などを想定）。
        t3_max = dates[N_TOTAL - 3]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 3)

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert len(records) == 2
        got_dates = sorted(r['date'] for r in records)
        assert got_dates == [dates[N_TOTAL - 2], dates[N_TOTAL - 1]]
        assert fallback_reason == FALLBACK_REASON_MULTI_DAY_GAP

    def test_null_state_column_falls_back(self, db_path, monkeypatch):
        """RECURSIVE型列（前日値を継ぐ列）の供給履歴に NaN があれば全期間計算。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 2)

        # 直近の保存済み行（境界に近い1行）の ema_200 を意図的に NULL にする
        # （実運用では起こらないはずだが、破損データに対する安全装置を検証する）。
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                'UPDATE indicators SET ema_200 = NULL WHERE symbol_id = ? AND date = ?',
                (SID_TARGET, str(dates[N_TOTAL - 2])),
            )
            con.commit()
        finally:
            con.close()

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert len(records) == 1
        assert fallback_reason is not None
        assert fallback_reason.startswith(FALLBACK_REASON_NULL_RECURSIVE_COLUMN + ':')
        assert 'ema_200' in fallback_reason, f'NULLにした列名(ema_200)が理由に含まれるはずが: {fallback_reason}'


class TestCalculateT3WorkerWarmupClassification:
    """5-15b→5-15c（2回目のcode-review指摘1）: 「壊れているから NULL」
    「まだ出ないから NULL（正当）」「判別できないから NULL（判別不能）」の3区分。

    5-15b の初版は `daily_prices` 内での絶対位置と `warmup_bars` を比較する
    方式だったが、SQLiteの保持行数（実測504）が `rs_roc_ema_200` の
    `warmup_bars`（511）より小さいため判定が原理的に到達不能で、本番の実際の
    欠陥が「正当」に誤分類され続けていた（5-15b の回帰）。5-15c で
    増分ウィンドウ（K本）内だけで観測できる性質（単調性・ウィンドウ長との
    大小関係）に基づく方式に改めた。本クラスはこの3区分それぞれを固定する。
    """

    def test_ウィンドウ全体がnullかつwarmup_barsがk未満なら欠陥になる(self, db_path, monkeypatch):
        """§3.5 ケース2: ウィンドウ全体がNULLで `warmup_bars < K` なら、
        ウィンドウがどこから始まっていても最終行の絶対位置は必ず warmup_bars を
        超えるため、欠陥と断定できる（正当なウォームアップ中ではあり得ない）。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 2)

        # ema_200 の warmup_bars(199) は K(400) 未満。オラクルは十分な履歴
        # （N_TOTAL=1400）を持つため本来は全て非NULLのはずだが、意図的に
        # 保存済み値を全て壊す（実運用では起こらない破損シナリオ）。
        con = sqlite3.connect(db_path)
        try:
            con.execute('UPDATE indicators SET ema_200 = NULL WHERE symbol_id = ?', (SID_TARGET,))
            con.commit()
        finally:
            con.close()

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert fallback_reason is not None
        assert fallback_reason.startswith(FALLBACK_REASON_NULL_RECURSIVE_COLUMN + ':'), (
            f'ウィンドウ全体NULL・warmup_bars<Kは欠陥のはずが: {fallback_reason}'
        )
        assert 'ema_200' in fallback_reason

    def test_ウィンドウ内で一度非nullになった値がより新しい行でnullに戻る場合は欠陥になる(self, db_path, monkeypatch):
        """§3.5 ケース1（単調性の破れ）: 本番の実際の不具合
        （2026-09-14以前は値があり、09-15以降がNULL）そのものを再現する。
        ウィンドウ内の一部が非NULLで、より新しい複数行がNULLに戻っている場合、
        正常系では起こり得ないため欠陥と判定する。"""
        dates, df_full, spy_full, full_res, spy_price_only = _build_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=N_TOTAL - 2)

        # ウィンドウ末尾（最新側）の直近5営業日ぶんだけを意図的にNULLにする
        # （本番の実際の不具合を模した、複数行にまたがる「回帰」パターン）。
        con = sqlite3.connect(db_path)
        try:
            recent_dates = [str(d) for d in dates[N_TOTAL - 7:N_TOTAL - 1]]
            con.executemany(
                'UPDATE indicators SET ema_200 = NULL WHERE symbol_id = ? AND date = ?',
                [(SID_TARGET, d) for d in recent_dates],
            )
            con.commit()
        finally:
            con.close()

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'全期間計算（state=None）にフォールバックするはずが: {calls}'
        assert fallback_reason is not None
        assert fallback_reason.startswith(FALLBACK_REASON_NULL_RECURSIVE_COLUMN + ':'), (
            f'単調性の破れ（値→NULLへの回帰）は欠陥のはずが: {fallback_reason}'
        )
        assert 'ema_200' in fallback_reason

    def test_ウィンドウ内で単調にnullから非nullへ遷移し前日行に値があれば増分を継続する(self, db_path, monkeypatch):
        """t3_fallback_lookback_window計画 §3.1（5-3で改訂）: ウィンドウが列の
        真のウォームアップ完了点をまたいでいて（NULL→非NULLへの単調な遷移のみ・
        全NULLではない）、かつ最終供給行（前日）に値がある場合は、
        フォールバックせず増分計算を継続する（旧仕様は`warmup_in_progress`として
        全期間計算へフォールバックしていたが、その全期間計算はSQLiteの保持本数
        （実測504本）だけで行われるため611本必要なrs_roc_ema_200にNULLを
        書いてしまっていた＝本計画が解消する不具合そのもの）。

        RECURSIVE型列は前日値（K-1行目）をシードに最終行だけを1歩計算する
        契約（`incremental_merge.prev_self_seed`）のため、シードより前の
        行にNULLがあっても増分計算の正しさに影響しない。

        5-9c（A-full。RS系のmin_periodsをmax(1,n//2)からnへ統一）により
        rs_roc_ema_200 の実効ウォームアップが大幅に後ろへ伸びた（真の
        warmup_bars の確定値は5-11bで全列再実測する予定。本テストは
        このファイル固有の合成データ（`_build_short_scenario` のシード）
        での実測遷移点=絶対位置611を使う）。n_short=900・K=400 では窓が
        絶対位置[499,898]をカバーし、この遷移点をまたぐため、この列自身が
        「正当なウォームアップ中」を再現する（他のRECURSIVE型列はwarmup_barsが
        全て499未満のため、この窓では既にウォームアップ済みで非NULL）。"""
        n_short = 900
        dates, df_full, spy_full, full_res, spy_price_only = _build_short_scenario(n_short)
        t3_max = dates[n_short - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=n_short - 2)

        # 前提確認: ウィンドウ内（絶対位置499〜898）で rs_roc_ema_200 が
        # NULL→非NULLへ単調に遷移していること（実測遷移点=611）。
        # かつ最終供給行（前日=n_short-2）には値があること。
        assert pd.isna(full_res['rs_roc_ema_200'].iloc[550])
        assert not pd.isna(full_res['rs_roc_ema_200'].iloc[700])
        assert not pd.isna(full_res['rs_roc_ema_200'].iloc[n_short - 2])

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [True], (
            f'単調にNULL→非NULLへ遷移し前日行に値がある場合は増分経路が使われるはずが: {calls}'
        )
        assert fallback_reason is None, (
            f'増分継続すべきでフォールバック理由は無いはずが: {fallback_reason}'
        )
        assert len(records) == 1
        got_row = records[0]
        ref_row = full_res.iloc[-1]

        compare_cols = [c for c in IND_COLS if c not in _NON_STRICT_COLUMNS]
        mismatches = _find_mismatches(ref_row, got_row, compare_cols)
        assert not mismatches, (
            f'増分経路の結果が全期間計算（全履歴を1回で計算した値）と不一致: {mismatches}'
        )
        assert not pd.isna(got_row['rs_roc_ema_200']), 'rs_roc_ema_200 にNULLが書かれてはいけない'

    def test_ウィンドウ全体がnullかつwarmup_barsがk以上なら判別不能になる(self, db_path, monkeypatch):
        """§3.5 ケース3: `rs_roc_ema_200`（warmup_bars=511）は増分ウィンドウ長
        K（=max_lookback()=400）より大きいため、ウィンドウ全体がNULLでも
        「正当なウォームアップ中」か「欠陥」かをこの検査だけでは判別できない。
        これは5-15bの回帰そのものが起きていたシナリオ（本番の実際の不具合と
        同じ列）であり、誤って「正当」と分類してはならない。"""
        n_short = K + 2
        dates, df_full, spy_full, full_res, spy_price_only = _build_short_scenario(n_short)
        t3_max = dates[n_short - 2]
        _insert_prices(db_path, SID_TARGET, df_full)
        _insert_indicators(db_path, SID_TARGET, full_res, upto_idx=n_short - 2)

        # 前提確認: この短い履歴では、オラクル自身がまだ ema_200/rs_roc_ema_200 の
        # ウォームアップを終えていない。
        assert pd.isna(full_res['ema_200'].iloc[50])
        assert pd.isna(full_res['rs_roc_ema_200'].iloc[-1])

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_TARGET, 'TEST', t3_max, db_path, spy_price_only,
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [None], f'NaNを含む供給履歴のため全期間計算にフォールバックするはずが: {calls}'
        assert fallback_reason is not None
        assert fallback_reason.startswith(FALLBACK_REASON_WARMUP_UNDETERMINED + ':'), (
            f'ウィンドウ全体NULL・warmup_bars>=Kは判別不能のはずが: {fallback_reason}'
        )
        assert 'rs_roc_ema_200' in fallback_reason
        assert FALLBACK_REASON_NULL_RECURSIVE_COLUMN not in fallback_reason


class TestCalculateT3WorkerSpyExclusion:
    """5-15b（code-review指摘2）: SPYのrs_*列は構造的にNULLが正常なため、
    フォールバック判定から除外され、他のRECURSIVE型列（ema_*/td9/atr_14）が
    揃っていれば増分経路を使えること。"""

    def test_spyはrs列のnullを無視して増分経路を使う(self, db_path, monkeypatch):
        dates, spy_full, full_res_spy = _build_spy_scenario()
        t3_max = dates[N_TOTAL - 2]
        _insert_prices(db_path, SID_SPY, spy_full)
        _insert_indicators(db_path, SID_SPY, full_res_spy, upto_idx=N_TOTAL - 2)

        calls = _patch_calculate_indicators(monkeypatch)

        ticker, sid, records, fallback_reason = _calculate_t3_worker(
            SID_SPY, 'SPY', t3_max, db_path, spy_full[['date', 'close', 'volume']],
            skip_fetch=False, is_virtual=False, spy_latest_date=dates[-1],
        )

        assert not isinstance(records, Exception), f'ワーカーが例外を返した: {records}'
        assert calls == [True], (
            f'SPYはrs_*列のNULLを除外して増分経路を使うはずが、全期間計算に'
            f'フォールバックした: calls={calls}, fallback_reason={fallback_reason}'
        )
        assert fallback_reason is None


class TestLogFallbackSummary:
    """フォールバック理由の集計ログ（5-6b）の固定。"""

    def test_no_fallbacks_logs_info_not_warning(self, caplog):
        """0件のときは INFO に留め、WARNING は出さない（ログを汚さない）。"""
        logger = logging.getLogger('test_t3_fallback_summary_empty')
        with caplog.at_level(logging.INFO, logger=logger.name):
            _log_fallback_summary(logger, [])

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f'0件のときにWARNINGが出た: {[r.message for r in warnings]}'
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any('フォールバックなし' in r.message for r in infos)

    def test_fallbacks_logged_as_warning_with_breakdown(self, caplog):
        """1件以上のときは WARNING で件数と理由内訳を出す。"""
        logger = logging.getLogger('test_t3_fallback_summary_nonempty')
        reasons = [
            FALLBACK_REASON_NO_SAVED_ROWS,
            FALLBACK_REASON_INSUFFICIENT_ROWS,
            FALLBACK_REASON_INSUFFICIENT_ROWS,
            f'{FALLBACK_REASON_NULL_RECURSIVE_COLUMN}:ema_200,rs_value_e5',
        ]
        with caplog.at_level(logging.WARNING, logger=logger.name):
            _log_fallback_summary(logger, reasons)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].message
        assert '4 件' in msg
        assert f'{FALLBACK_REASON_NO_SAVED_ROWS}=1' in msg
        assert f'{FALLBACK_REASON_INSUFFICIENT_ROWS}=2' in msg
        # detail（列名）は集計時に落として、カテゴリだけで数える
        assert f'{FALLBACK_REASON_NULL_RECURSIVE_COLUMN}=1' in msg
        assert '--rebuild-from T3' in msg

    def test_欠陥理由が無ければWARNINGではなくINFOになる(self, caplog):
        """5-15b（code-review指摘2・軽微2件目）: 新規上場・履歴不足・連休明け・
        判別不能など想定内の理由だけなら、WARNINGではなくINFOに留める
        （一律WARNINGだとノイズになりWARNINGが読まれなくなる）。

        5-15d（2回目のcode-review指摘2）: `warmup_undetermined`等（no_saved_rowsを
        除く）は「SQLiteの保持本数だけを使った全期間計算」であり遡り不足の可能性が
        あるため、5-15c時点まで残っていた「不正確という文言を禁止する」という
        アサーションを反転した（5-15bの誤りの再発そのものだった。§7-6参照）。

        t3_fallback_lookback_window計画（5-3）で `warmup_in_progress` は
        フォールバック理由から廃止された（単調にNULL→非NULLへ移行し前日行に
        値がある列は増分継続の対象になったため）。本テストは残る想定内理由
        （insufficient_saved_rows/multi_day_gap）で同じ分類を固定する。"""
        logger = logging.getLogger('test_t3_fallback_summary_benign_only')
        reasons = [
            FALLBACK_REASON_NO_SAVED_ROWS,
            FALLBACK_REASON_INSUFFICIENT_ROWS,
            FALLBACK_REASON_INSUFFICIENT_ROWS,
            FALLBACK_REASON_MULTI_DAY_GAP,
        ]
        with caplog.at_level(logging.INFO, logger=logger.name):
            _log_fallback_summary(logger, reasons)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f'想定内の理由のみなのにWARNINGが出た: {[r.message for r in warnings]}'
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        msg = infos[0].message
        assert '4 件' in msg
        assert f'{FALLBACK_REASON_INSUFFICIENT_ROWS}=2' in msg
        # insufficient_saved_rows/multi_day_gap はSQLiteの保持本数
        # だけを使った全期間計算であり、正確とは言い切れない（5-15d）。
        assert '不正確' in msg, (
            'no_saved_rows以外を含むフォールバックで「不正確」の留保が無い'
            f'（5-15bの誤りの再発の可能性）: {msg}'
        )
        # no_saved_rows（新規上場。1件）については正確と明示する区別は維持する。
        assert 'no_saved_rows' in msg

    def test_全件がno_saved_rowsなら正確と言い切ってよい(self, caplog):
        """5-15d: `no_saved_rows`（新規上場。t3_maxが無く daily_prices の全行が
        そのまま銘柄の全履歴）だけで構成されるフォールバックは、全期間計算が
        そのまま全履歴計算になるため「正確です」と言い切ってよい唯一のケース。"""
        logger = logging.getLogger('test_t3_fallback_summary_no_saved_rows_only')
        reasons = [FALLBACK_REASON_NO_SAVED_ROWS, FALLBACK_REASON_NO_SAVED_ROWS]

        with caplog.at_level(logging.INFO, logger=logger.name):
            _log_fallback_summary(logger, reasons)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        msg = infos[0].message
        assert '2 件' in msg
        assert '不正確' not in msg, f'no_saved_rowsのみなのに不正確の留保が出た: {msg}'
        assert '正確' in msg

    def test_判別不能理由があればWARNINGにはならないが正確性を主張しない(self, caplog):
        """5-15c（2回目のcode-review指摘1）: `warmup_undetermined` は欠陥と確定した
        わけではないため WARNING には昇格させない（`warmup_in_progress` と同じ
        重大度）が、「全期間計算そのものの結果は正確です」と一括で言い切っては
        いけない（5-15bの回帰そのもの: 誤って「正確」と報告し続けたことが問題
        だった）。判別不能ぶんを名指しし、正確性の主張から除外する。"""
        logger = logging.getLogger('test_t3_fallback_summary_undetermined')
        reasons = [
            FALLBACK_REASON_NO_SAVED_ROWS,
            f'{FALLBACK_REASON_WARMUP_UNDETERMINED}:rs_roc_ema_200',
            f'{FALLBACK_REASON_WARMUP_UNDETERMINED}:rs_roc_ema_200',
        ]
        with caplog.at_level(logging.INFO, logger=logger.name):
            _log_fallback_summary(logger, reasons)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f'判別不能はWARNINGに昇格しないはずが: {[r.message for r in warnings]}'
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        msg = infos[0].message
        assert '3 件' in msg
        assert f'{FALLBACK_REASON_WARMUP_UNDETERMINED}=2' in msg
        assert '判別できません' in msg or '判別不能' in msg, (
            f'判別不能ぶんの不確かさが明示されていない: {msg}'
        )
        # 「いずれも...全期間計算そのものの結果は正確です」という一括の断定は禁止
        # （判別不能な列については正確性を保証できないため）。
        assert 'いずれも' not in msg, f'判別不能を含むのに一括で正確と主張している: {msg}'
