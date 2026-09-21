"""incremental_state_registry.py のテスト。

計画書: doc/in_progress/t3_incremental_plan.md §3.3, 5-3

固定する2点:
1. T3（`Indicator` モデル）の全列（id/symbol_id/date を除く）がレジストリに登録済み
   （逆にレジストリにあって Indicator に無い列も検出する）
2. TWO_SIDED（(c) 両側参照）に分類された列が存在しない（5-2 の実測結果の固定）
"""
import pytest

from db.models import Indicator
from indicators.incremental_state_registry import (
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

    def test_recursive型は状態列を持つ(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.kind is ColumnKind.RECURSIVE:
                assert spec.state_columns, f'{name}: RECURSIVE 型なのに state_columns が空です'
                assert spec.lookback is None, f'{name}: RECURSIVE 型なのに lookback が設定されています'

    def test_window型は状態列を持たない(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.kind is ColumnKind.WINDOW:
                assert spec.state_columns == (), f'{name}: WINDOW 型なのに state_columns が設定されています'

    def test_recursive型の状態列名はindicator列として実在する(self):
        model_columns = _indicator_model_columns()
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.kind is ColumnKind.RECURSIVE:
                for state_col in spec.state_columns:
                    assert state_col in model_columns, (
                        f'{name}: state_columns の "{state_col}" が Indicator の実列にありません'
                    )

    def test_lookbackが設定されている場合は非負整数(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.lookback is not None:
                assert isinstance(spec.lookback, int) and spec.lookback >= 0, (
                    f'{name}: lookback は0以上の整数である必要があります（実際: {spec.lookback!r}）'
                )

    def test_不正な組み合わせはpost_initで例外になる(self):
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.RECURSIVE, state_columns=())
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.RECURSIVE, state_columns=('dummy',), lookback=5)
        with pytest.raises(ValueError):
            ColumnSpec(name='dummy', kind=ColumnKind.WINDOW, state_columns=('dummy',))


class TestKnownLookbackValues:
    """本番実測で判明している4値がレジストリに正しく反映されていること（回帰固定）。"""

    @pytest.mark.parametrize('name,expected', [
        ('rs_trend_s200', 99),
        ('rs_ratio_e200', 298),
        ('rs_momentum_e200', 610),
    ])
    def test_window型の実測値(self, name, expected):
        spec = INDICATOR_COLUMN_REGISTRY[name]
        assert spec.kind is ColumnKind.WINDOW
        assert spec.lookback == expected

    def test_rs_roc_ema_200はrecursive型で実測値がnoteに記載されている(self):
        spec = INDICATOR_COLUMN_REGISTRY['rs_roc_ema_200']
        assert spec.kind is ColumnKind.RECURSIVE
        assert '511' in spec.note
