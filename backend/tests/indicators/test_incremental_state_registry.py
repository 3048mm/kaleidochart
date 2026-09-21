"""incremental_state_registry.py のテスト。

計画書: doc/in_progress/t3_incremental_plan.md §3.3, 5-3, 5-3b（再設計）

固定する点:
1. T3（`Indicator` モデル）の全列（id/symbol_id/date を除く）がレジストリに登録済み
   （逆にレジストリにあって Indicator に無い列も検出する）
2. TWO_SIDED（(c) 両側参照）に分類された列が存在しない（5-2 の実測結果の固定）
3. WINDOW 型の lookback 最大値が 252 以下（SQLite の504行に収まることの担保。5-3b）。
   ただし zone_break系4列（`zb_ssl`/`zb_bsl`/`is_zone_break_bull`/`is_zone_break_weak`）は
   5-4c で原理的に非有界と判明したため例外とし、HOT_WINDOW_BARS(504) 以下であることのみ担保する
4. RECURSIVE 型は prev_self=True（5-3b）
5. inputs の列名がすべて有効（生の価格列 or Indicator 実列。5-3b）
"""
import pytest

from db.models import Indicator
from indicators.incremental_state_registry import (
    HOT_WINDOW_BARS,
    RAW_PRICE_COLUMNS,
    ColumnKind,
    ColumnSpec,
    INDICATOR_COLUMN_REGISTRY,
)

# id/symbol_id/date は主キー・外部キー・日付であり、増分計算の特性分類の対象外
_NON_INDICATOR_COLUMNS = {'id', 'symbol_id', 'date'}


def _indicator_model_columns() -> set:
    return {c.name for c in Indicator.__table__.columns if c.name not in _NON_INDICATOR_COLUMNS}


class TestRegistryCoversAllIndicatorColumns:
    """レジストリと Indicator モデルの列集合が完全一致すること。"""

    def test_indicator列が全てレジストリに登録済み(self):
        model_columns = _indicator_model_columns()
        registered = set(INDICATOR_COLUMN_REGISTRY.keys())
        missing = model_columns - registered
        assert not missing, (
            f'Indicator モデルにあるがレジストリ未登録の列: {sorted(missing)}\n'
            'incremental_state_registry.py の INDICATOR_COLUMN_REGISTRY に追加してください。'
        )

    def test_レジストリに存在しないindicator列が無いこと(self):
        model_columns = _indicator_model_columns()
        registered = set(INDICATOR_COLUMN_REGISTRY.keys())
        extra = registered - model_columns
        assert not extra, (
            f'レジストリにあるが Indicator モデルに無い列: {sorted(extra)}\n'
            '列名のタイポ、または列削除後にレジストリ側の掃除漏れの可能性があります。'
        )

    def test_列数が一致する(self):
        assert len(INDICATOR_COLUMN_REGISTRY) == len(_indicator_model_columns())


class TestNoTwoSidedColumns:
    """(c) 両側参照に分類された列が存在しないこと（5-2 の実測結果の固定）。

    将来 (c) が必要になったらこのテストを意図的に更新する運用
    （doc/in_progress/t3_incremental_plan.md §3.3）。
    """

    def test_two_sided列が存在しない(self):
        two_sided = [
            name for name, spec in INDICATOR_COLUMN_REGISTRY.items()
            if spec.kind is ColumnKind.TWO_SIDED
        ]
        assert two_sided == [], (
            f'TWO_SIDED に分類された列が見つかりました: {sorted(two_sided)}\n'
            '5-2 の実測結果（現時点で該当列なし）と矛盾します。意図した追加であれば、'
            'このテストと doc/backend_specification.md の特性タイプ表を更新してください。'
        )


class TestColumnSpecInvariants:
    """ColumnSpec 自体の型不変条件（実装ミスの検出）。"""

    def test_recursive型はprev_selfがTrueでinputsを持つ(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.kind is ColumnKind.RECURSIVE:
                assert spec.prev_self is True, f'{name}: RECURSIVE 型なのに prev_self が True ではありません'
                assert spec.inputs, f'{name}: RECURSIVE 型なのに inputs が空です（当日分の入力が必要）'

    def test_window型はprev_selfがFalse(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.kind is ColumnKind.WINDOW:
                assert spec.prev_self is False, f'{name}: WINDOW 型なのに prev_self が True です'

    def test_lookbackが設定されている場合は1以上の整数(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.lookback is not None:
                assert isinstance(spec.lookback, int) and spec.lookback >= 1, (
                    f'{name}: lookback は1以上の整数である必要があります（実際: {spec.lookback!r}）'
                )

    def test_不正な組み合わせはpost_initで例外になる(self):
        # RECURSIVE なのに prev_self=False
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.RECURSIVE, prev_self=False, inputs=('close',), lookback=1)
        # RECURSIVE なのに inputs が空
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.RECURSIVE, prev_self=True, inputs=())
        # WINDOW なのに prev_self=True
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.WINDOW, prev_self=True, inputs=('close',))
        # lookback が1未満
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.WINDOW, inputs=('close',), lookback=0)


class TestInputsAreValidColumnNames:
    """inputs に書かれた列名が、生価格列か Indicator 実列のどちらかであること（5-3b）。"""

    def test_全列のinputsが有効な列名である(self):
        valid = RAW_PRICE_COLUMNS | _indicator_model_columns()
        invalid = {
            (name, input_name)
            for name, spec in INDICATOR_COLUMN_REGISTRY.items()
            for input_name in spec.inputs
            if input_name not in valid
        }
        assert not invalid, (
            f'inputs が生価格列にも Indicator 実列にも無い組み合わせ: {sorted(invalid)}\n'
            'RAW_PRICE_COLUMNS への追加漏れ、または列名のタイポの可能性があります。'
        )


class TestLookbackBound:
    """WINDOW 型 lookback の上限（SQLite の504行に収まることの担保。5-3b/5-4c）。"""

    # 5-4c: zone_break系4列は原理的に非有界（トレンドレッグ長に上限なし）と実測で判明した
    # ため、意図的に HOT_WINDOW_BARS(504) まで引き上げた特例。他の WINDOW 型列とは別枠で扱う
    # （incremental_state_registry.py の _ENTRIES コメント参照）。
    _HOT_WINDOW_EXEMPT_COLUMNS = frozenset({
        'zb_ssl', 'zb_bsl', 'is_zone_break_bull', 'is_zone_break_weak',
    })

    def test_window型のlookback最大値が252以下(self):
        window_lookbacks = [
            spec.lookback for name, spec in INDICATOR_COLUMN_REGISTRY.items()
            if spec.kind is ColumnKind.WINDOW and spec.lookback is not None
            and name not in self._HOT_WINDOW_EXEMPT_COLUMNS
        ]
        assert window_lookbacks, 'WINDOW 型で lookback が確定している列が1つもありません'
        assert max(window_lookbacks) <= 252, (
            f'WINDOW 型の lookback 最大値が252を超えています: {max(window_lookbacks)}\n'
            'SQLite の保持期間（504行）を圧迫するため設計を見直してください。'
            '（zone_break系4列は既知の例外として _HOT_WINDOW_EXEMPT_COLUMNS で除外済み）'
        )

    def test_zone_break系4列はHOT_WINDOW_BARSに固定されている(self):
        """5-4c: zone_break系（zb_ssl/zb_bsl/is_zone_break_bull/is_zone_break_weak）は
        必要履歴が原理的に非有界（300銘柄実測で最大2,400本）と判明したため、
        「有界だから252以下」ではなく「ホットウィンドウ全体＝現行の日次計算と同等」という
        意味で HOT_WINDOW_BARS(504) を割り当てている（厳密解ではない）。
        詳細: doc/backend_specification.md、doc/in_progress/t3_incremental_plan.md §8。
        """
        for name in sorted(self._HOT_WINDOW_EXEMPT_COLUMNS):
            spec = INDICATOR_COLUMN_REGISTRY[name]
            assert spec.kind is ColumnKind.WINDOW, f'{name}: WINDOW 型である想定です'
            assert spec.lookback == HOT_WINDOW_BARS, (
                f'{name}: lookback が HOT_WINDOW_BARS({HOT_WINDOW_BARS}) と一致しません: {spec.lookback}'
            )

    def test_lookback未確定の列を一覧する(self):
        """可視化目的。lookback=None の列があっても失敗させない。"""
        unresolved = sorted(
            name for name, spec in INDICATOR_COLUMN_REGISTRY.items() if spec.lookback is None
        )
        print(f'lookback 未確定の列（{len(unresolved)}件）: {unresolved}')


class TestKnownLookbackValues:
    """5-3b の設計（計算式を読んで導出した値）の回帰固定。"""

    @pytest.mark.parametrize('name,expected_kind,expected_lookback', [
        ('ema_200', ColumnKind.RECURSIVE, 1),
        ('atr_14', ColumnKind.RECURSIVE, 2),
        ('td9', ColumnKind.RECURSIVE, 5),
        ('rs_value_e200', ColumnKind.RECURSIVE, 1),
        ('rs_roc_ema_200', ColumnKind.RECURSIVE, 15),
        ('rs_blue_dot_age', ColumnKind.RECURSIVE, 252),
        ('rs_red_dot_age', ColumnKind.RECURSIVE, 252),
        ('rs_trend_s200', ColumnKind.WINDOW, 200),
        ('rs_ratio_e200', ColumnKind.WINDOW, 200),
        ('rs_momentum_e200', ColumnKind.WINDOW, 200),
        ('sp_pivot', ColumnKind.WINDOW, 250),
        ('sp_hl', ColumnKind.WINDOW, 250),
        ('sp_counter', ColumnKind.WINDOW, 250),
        # zone_break系4列は5-4cで原理的に非有界と判明したためHOT_WINDOW_BARS(504)に変更
        ('zb_ssl', ColumnKind.WINDOW, HOT_WINDOW_BARS),
        ('zb_bsl', ColumnKind.WINDOW, HOT_WINDOW_BARS),
        ('is_zone_break_bull', ColumnKind.WINDOW, HOT_WINDOW_BARS),
        ('is_zone_break_weak', ColumnKind.WINDOW, HOT_WINDOW_BARS),
        ('dist_52w_high_pct', ColumnKind.WINDOW, 252),
        ('is_trend_template', ColumnKind.WINDOW, 252),
    ])
    def test_代表列の分類とlookback(self, name, expected_kind, expected_lookback):
        spec = INDICATOR_COLUMN_REGISTRY[name]
        assert spec.kind is expected_kind
        assert spec.lookback == expected_lookback
