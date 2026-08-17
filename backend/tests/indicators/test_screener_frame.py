"""
test_screener_frame.py — Phase 3 ステップ 3a TDD: ScreenerFrame 契約の単体テスト

`screener_frame.py`（新規予定モジュール、backend/indicators/screener_frame.py）が
1営業日分のクロスセクション DataFrame の契約（同一性の列・数値列の dtype・ハード要求カラム）を
検査できることを検証する。仕様は doc/completed/screener_filter_unification_plan.md
§3.3.1 (a)（Phase 3 詳細設計）そのもの。

この時点では backend/indicators/screener_frame.py が未実装のため、このファイル全体が
import エラー（ModuleNotFoundError）で RED になることを意図している。
"""
import pandas as pd
import pytest

from indicators.screener_frame import (
    FrameContractError,
    IDENTITY_COLUMNS,
    PRICE_COLUMNS,
    assert_frame_contract,
)
from indicators.screener_registry import RequiredColumns


def _valid_frame(**overrides) -> pd.DataFrame:
    """契約を満たす最小のフレームを作る（同一性の列＋価格の列＋数値指標列1つ）。"""
    data = {
        'symbol_id': [1, 2],
        'ticker': ['AAA', 'BBB'],
        'name': ['Alpha', 'Beta'],
        'category': ['個別', '個別'],
        'active': [1, 1],
        'date': ['2026-08-11', '2026-08-11'],
        'open': [10.0, 20.0],
        'high': [11.0, 21.0],
        'low': [9.0, 19.0],
        'close': [10.5, 20.5],
        'volume': [1000, 2000],
        'market_cap': [1.0e9, 2.0e9],
        'vol_surge_21': [1.2, 1.5],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TestValidFrame:
    def test_valid_frame_does_not_raise(self):
        assert_frame_contract(_valid_frame())


class TestMissingIdentityColumns:
    def test_missing_identity_column_raises(self):
        df = _valid_frame().drop(columns=['category'])
        with pytest.raises(FrameContractError):
            assert_frame_contract(df)

    def test_missing_identity_column_message_lists_missing_names(self):
        df = _valid_frame().drop(columns=['category', 'active'])
        with pytest.raises(FrameContractError) as exc_info:
            assert_frame_contract(df)
        message = str(exc_info.value)
        assert 'category' in message
        assert 'active' in message


class TestObjectDtypeNumericColumns:
    """P1-2 の回帰テスト: 列が丸ごと NULL だと object dtype になり、
    特殊フィルタ内の `float > None` 比較で TypeError になる（計画書 §7 P1-2）。
    """

    def test_all_null_numeric_column_is_object_dtype_and_raises(self):
        df = _valid_frame(vol_surge_21=[None, None])
        assert df['vol_surge_21'].dtype == object
        with pytest.raises(FrameContractError):
            assert_frame_contract(df)

    def test_all_null_numeric_column_message_lists_column_name(self):
        df = _valid_frame(vol_surge_21=[None, None])
        with pytest.raises(FrameContractError) as exc_info:
            assert_frame_contract(df)
        assert 'vol_surge_21' in str(exc_info.value)

    def test_partially_null_numeric_column_infers_float64_and_does_not_raise(self):
        """一部だけ NaN の場合は pandas が float64 に推論するため契約違反にならない
        （本番の基準日で NULL が最大2件でも float64 になり正常動作していた実測に対応）。
        """
        df = _valid_frame(vol_surge_21=[1.2, None])
        assert df['vol_surge_21'].dtype != object
        assert_frame_contract(df)


class TestStringIdentityColumnsAreExcludedFromDtypeCheck:
    def test_object_dtype_ticker_name_category_do_not_raise(self):
        df = _valid_frame()
        assert df['ticker'].dtype == object
        assert df['name'].dtype == object
        assert df['category'].dtype == object
        assert_frame_contract(df)


class TestRequiredColumns:
    def test_missing_required_today_column_raises(self):
        df = _valid_frame()
        required = RequiredColumns(
            today=frozenset({'sma_50'}), prev=frozenset(), ranks=frozenset(),
        )
        with pytest.raises(FrameContractError) as exc_info:
            assert_frame_contract(df, required=required)
        assert 'sma_50' in str(exc_info.value)

    def test_required_today_column_present_does_not_raise(self):
        df = _valid_frame(sma_50=[10.0, 20.0])
        required = RequiredColumns(
            today=frozenset({'sma_50'}), prev=frozenset(), ranks=frozenset(),
        )
        assert_frame_contract(df, required=required)

    def test_required_rank_is_checked_by_frame_column_name(self):
        """rs_ratio_rank_e21（正準名）を要求したら rs21_rank（フレーム内名）の有無を見る。"""
        required = RequiredColumns(
            today=frozenset(), prev=frozenset(), ranks=frozenset({'rs_ratio_rank_e21'}),
        )
        df_missing = _valid_frame()
        with pytest.raises(FrameContractError) as exc_info:
            assert_frame_contract(df_missing, required=required)
        assert 'rs21_rank' in str(exc_info.value)

        df_present = _valid_frame(rs21_rank=[0.5, 0.8])
        assert_frame_contract(df_present, required=required)

    def test_required_prev_is_not_checked(self):
        """required.prev はここでは検査しない（apply_filters_to_df 側の
        deny-by-default 等の例外処理と二重に検査しないため。§3.3.1 (a)）。
        """
        df = _valid_frame()
        required = RequiredColumns(
            today=frozenset(), prev=frozenset({'rs_ratio_e21'}), ranks=frozenset(),
        )
        assert_frame_contract(df, required=required)


class TestFrameContractErrorIsValueError:
    def test_is_value_error_subclass(self):
        assert issubclass(FrameContractError, ValueError)


class TestWhereIdentifiesCaller:
    def test_where_is_included_in_message(self):
        df = _valid_frame().drop(columns=['category'])
        with pytest.raises(FrameContractError) as exc_info:
            assert_frame_contract(df, where='load_cross_section')
        assert 'load_cross_section' in str(exc_info.value)


class TestConstants:
    def test_identity_columns_content(self):
        assert IDENTITY_COLUMNS == frozenset({
            'symbol_id', 'ticker', 'name', 'category', 'active',
        })

    def test_price_columns_content(self):
        assert PRICE_COLUMNS == frozenset({
            'date', 'open', 'high', 'low', 'close', 'volume', 'market_cap',
        })
