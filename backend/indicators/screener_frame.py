"""
screener_frame.py — ScreenerFrame 契約（1営業日分のクロスセクション DataFrame が満たすべき形）。

`doc/completed/screener_filter_unification_plan.md` §3.3.1 (a)（Phase 3 ステップ 3a）が仕様。
どちらのローダ（SQLite 経由 / Parquet 経由）で構築したフレームでも、この契約を満たしていなければ
`apply_filters_to_df` に安全に渡せない。

ステップ 3a はこの契約定義（＝検査関数）の追加のみを行う。呼び出し側（ローダの実装・API の切替）は
3b 以降で行う。
"""
from typing import Optional

import pandas as pd
from pandas.api.types import is_object_dtype

try:
    from indicators.screener_registry import to_frame_column
except ModuleNotFoundError:
    from backend.indicators.screener_registry import to_frame_column


# ============================================================
# IDENTITY_COLUMNS / PRICE_COLUMNS — ScreenerFrame が区分として持つべき列
# ============================================================
# どちらのローダで構築しても必ず存在しなければならない列（§3.3.1 (a) の表「同一性」「価格」）。
IDENTITY_COLUMNS: frozenset = frozenset({
    'symbol_id', 'ticker', 'name', 'category', 'active',
})

PRICE_COLUMNS: frozenset = frozenset({
    'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap',
})


class FrameContractError(ValueError):
    """DataFrame が ScreenerFrame 契約を満たさないことを表す例外。

    既存の fail-loud 実装（UnknownFilterKeyError / MissingFilterColumnError 等）と
    契約を揃えるため ValueError のサブクラスにする。
    """


def assert_frame_contract(df: pd.DataFrame, *, required=None, where: str = '') -> None:
    """DataFrame が ScreenerFrame 契約を満たすか検査する。満たさなければ FrameContractError。

    検査項目（§3.3.1 (a)）:
      1. 同一性の列（IDENTITY_COLUMNS）が全て存在する。欠けていれば列名を列挙して例外にする。
      2. **数値列が object dtype になっていない**（同一性の列と 'date' を除く全列が対象。
         ticker/name/category は同一性の列側に入っているため対象外）。
         列が丸ごと NULL だと pandas がその列を object dtype に推論し、特殊フィルタ内の
         `float > None` 比較で TypeError になる（計画書 §7 P1-2。T3 に新しい指標カラムを
         追加したがパイプライン未実行、といった状態で本番でも起こり得る）。
      3. `required`（`screener_registry.RequiredColumns`）を渡した場合、
         `required.today` と `required.ranks`（`to_frame_column()` でフレーム内名に変換して）
         が存在する。`required.prev` はここでは検査しない — `apply_filters_to_df` 側が
         VCP の deny-by-default 等、前日データ欠落時の例外処理を既に持っているため、
         ここで二重に検査しない（§3.3.1 (a)）。

    Args:
        df: 検査対象の1営業日分クロスセクション DataFrame。
        required: 追加で存在を要求するハード要求カラム集合（`RequiredColumns`）。省略可。
        where: 例外メッセージに含める呼び出し元の識別子（例: 'load_cross_section'）。
            どちらのローダで壊れたかが分かるようにするための識別用。
    """
    prefix = f'[{where}] ' if where else ''

    # 1. 同一性の列が全て存在する
    missing_identity = sorted(IDENTITY_COLUMNS - set(df.columns))
    if missing_identity:
        raise FrameContractError(
            f'{prefix}ScreenerFrame 契約違反: 同一性の列が欠けている: {missing_identity}'
        )

    # 2. 数値列が object dtype になっていない（同一性の列と date は文字列/対象外なので除く）
    excluded = IDENTITY_COLUMNS | {'date'}
    object_dtype_columns = sorted(
        col for col in df.columns
        if col not in excluded and is_object_dtype(df[col])
    )
    if object_dtype_columns:
        raise FrameContractError(
            f'{prefix}ScreenerFrame 契約違反: 数値列が object dtype になっている: '
            f'{object_dtype_columns}。列が丸ごと NULL になっていないか'
            '（パイプライン未実行の指標カラム等）を確認すること。'
        )

    # 3. required のハード要求（today / ranks）
    if required is not None:
        missing_today = sorted(c for c in required.today if c not in df.columns)
        missing_ranks = sorted(
            to_frame_column(c) for c in required.ranks
            if to_frame_column(c) not in df.columns
        )
        missing = missing_today + missing_ranks
        if missing:
            raise FrameContractError(
                f'{prefix}ScreenerFrame 契約違反: 必要カラムが欠けている: {missing}'
            )
