"""incremental_state_registry.py のテスト。

計画書: doc/in_progress/t3_incremental_plan.md §3.3, 5-3, 5-3b（再設計）

固定する点:
1. T3（`Indicator` モデル）の全列（id/symbol_id/date を除く）がレジストリに登録済み
   （逆にレジストリにあって Indicator に無い列も検出する）
2. TWO_SIDED（(c) 両側参照）に分類された列が存在しない（5-2 の実測結果の固定）
3. WINDOW 型の lookback 最大値が 252 以下（SQLite の504行に収まることの担保。5-3b）。
   ただし zone_break系4列（`zb_ssl`/`zb_bsl`/`is_zone_break_bull`/`is_zone_break_weak`）は
   5-4c で原理的に非有界と判明したため例外とし、ZONE_BREAK_LOOKBACK(400) に固定されている
   ことのみ担保する（5-4e）
4. RECURSIVE 型は prev_self=True（5-3b）
5. inputs の列名がすべて有効（生の価格列 or Indicator 実列。5-3b）
6. `max_lookback() + 1` が `HOT_WINDOW_BARS`（SQLiteの保持行数）を超えない
   （＝マージンがゼロ以下にならない。5-4e）
"""
import pytest

from db.models import Indicator
from indicators.incremental_state_registry import (
    HOT_WINDOW_BARS,
    RAW_PRICE_COLUMNS,
    ZONE_BREAK_LOOKBACK,
    ColumnKind,
    ColumnSpec,
    INDICATOR_COLUMN_REGISTRY,
    columns_with_undeterminable_warmup,
    columns_with_warmup_threshold,
    is_structurally_null_column,
    max_lookback,
    recursive_column_names,
    supplied_column_names,
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
    # ため、意図的に ZONE_BREAK_LOOKBACK(400。5-4e。旧版はHOT_WINDOW_BARS=504) まで
    # 引き上げた特例。他の WINDOW 型列とは別枠で扱う
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

    def test_zone_break系4列はZONE_BREAK_LOOKBACKに固定されている(self):
        """5-4c/5-4e: zone_break系（zb_ssl/zb_bsl/is_zone_break_bull/is_zone_break_weak）は
        必要履歴が原理的に非有界（300銘柄実測で最大2,400本）と判明したため、
        「有界だから252以下」ではなく近似解として固定値を割り当てている（厳密解ではない）。

        5-4c 版は暫定的に HOT_WINDOW_BARS(504) をそのまま使っていたが、これは
        SQLite の保持行数そのものであり `max_lookback()+1` が保持行数を1行超え
        マージンがゼロだった。5-4e でユーザー判断により ZONE_BREAK_LOOKBACK(400) に
        見直した（400と600〜2400の間で精度が変わらないと実測で確認済みのため、
        400を採用してもホットウィンドウ504との精度差は無い。詳細は
        incremental_state_registry.py の ZONE_BREAK_LOOKBACK docstring）。
        詳細: doc/backend_specification.md、doc/in_progress/t3_incremental_plan.md §8。
        """
        for name in sorted(self._HOT_WINDOW_EXEMPT_COLUMNS):
            spec = INDICATOR_COLUMN_REGISTRY[name]
            assert spec.kind is ColumnKind.WINDOW, f'{name}: WINDOW 型である想定です'
            assert spec.lookback == ZONE_BREAK_LOOKBACK, (
                f'{name}: lookback が ZONE_BREAK_LOOKBACK({ZONE_BREAK_LOOKBACK}) と一致しません: {spec.lookback}'
            )

    def test_max_lookback_plus_oneがHOT_WINDOW_BARSに対してマージンを持つ(self):
        """5-4e: `max_lookback() + 1` が SQLite の保持行数(HOT_WINDOW_BARS)を超えないこと。

        5-4c 版は zb_* の lookback を HOT_WINDOW_BARS(504) そのものに設定していたため、
        `max_lookback()+1`(505) が保持行数(504)を1行超えマージンがゼロだった
        （730暦日に含まれる営業日数は祝日配置で年により500〜505程度に揺れるため、
        マージンゼロは危険）。5-4e で見直し後はマージンが確保されているはずで、
        将来どれかの列の lookback を引き上げてこの不変条件が壊れたら、この
        テストが検出する。
        """
        assert max_lookback() + 1 <= HOT_WINDOW_BARS, (
            f'max_lookback()+1={max_lookback() + 1} が HOT_WINDOW_BARS({HOT_WINDOW_BARS}) を'
            '超えています。SQLite の保持行数に対するマージンがゼロ以下になっています。'
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
        # zone_break系4列は5-4cで原理的に非有界と判明したため近似値に変更。
        # 5-4e でマージン確保のため ZONE_BREAK_LOOKBACK(400) に見直し。
        ('zb_ssl', ColumnKind.WINDOW, ZONE_BREAK_LOOKBACK),
        ('zb_bsl', ColumnKind.WINDOW, ZONE_BREAK_LOOKBACK),
        ('is_zone_break_bull', ColumnKind.WINDOW, ZONE_BREAK_LOOKBACK),
        ('is_zone_break_weak', ColumnKind.WINDOW, ZONE_BREAK_LOOKBACK),
        ('dist_52w_high_pct', ColumnKind.WINDOW, 252),
        ('is_trend_template', ColumnKind.WINDOW, 252),
    ])
    def test_代表列の分類とlookback(self, name, expected_kind, expected_lookback):
        spec = INDICATOR_COLUMN_REGISTRY[name]
        assert spec.kind is expected_kind
        assert spec.lookback == expected_lookback


class TestWarmupBars:
    """warmup_bars（5-6c）の不変条件。

    「演算上必要な日数がある銘柄なら、その列は NULL にならない」という健全性チェック
    （`tools/db_health_check.py --check-warmup-nulls`）が使う属性の整合性を固定する。
    """

    # イベント駆動で閾値を置けない例外列（実測で最小9〜最大337本とばらついた）。
    _EVENT_DRIVEN_EXEMPT_COLUMNS = frozenset({'sp_pivot', 'sp_hl', 'sp_counter'})

    def test_例外列以外は全てwarmup_barsが確定している(self):
        unresolved = sorted(
            name for name, spec in INDICATOR_COLUMN_REGISTRY.items()
            if spec.warmup_bars is None and name not in self._EVENT_DRIVEN_EXEMPT_COLUMNS
        )
        assert unresolved == [], (
            f'warmup_bars が未確定の列があります: {unresolved}\n'
            '実測して値を設定するか、イベント駆動の例外として'
            '_EVENT_DRIVEN_EXEMPT_COLUMNS に追加してください。'
        )

    def test_例外列はwarmup_barsがNone(self):
        for name in sorted(self._EVENT_DRIVEN_EXEMPT_COLUMNS):
            assert INDICATOR_COLUMN_REGISTRY[name].warmup_bars is None, (
                f'{name}: イベント駆動の例外列のはずが warmup_bars が確定しています'
            )

    def test_warmup_barsは0以上の整数(self):
        for name, spec in INDICATOR_COLUMN_REGISTRY.items():
            if spec.warmup_bars is not None:
                assert isinstance(spec.warmup_bars, int) and spec.warmup_bars >= 0, (
                    f'{name}: warmup_bars は0以上の整数である必要があります（実際: {spec.warmup_bars!r}）'
                )

    def test_columns_with_warmup_thresholdは例外列を含まない(self):
        thresholds = columns_with_warmup_threshold()
        for name in self._EVENT_DRIVEN_EXEMPT_COLUMNS:
            assert name not in thresholds, (
                f'{name}: イベント駆動の例外列が columns_with_warmup_threshold() に含まれています'
            )

    def test_columns_with_warmup_thresholdは確定列を全て含む(self):
        thresholds = columns_with_warmup_threshold()
        expected = {
            name for name, spec in INDICATOR_COLUMN_REGISTRY.items() if spec.warmup_bars is not None
        }
        assert set(thresholds.keys()) == expected

    @pytest.mark.parametrize('name,expected_warmup', [
        ('sma_200', 0),
        ('ema_200', 199),
        ('rs_value_e200', 199),
        ('rs_ratio_e200', 298),
        ('rs_roc_ema_200', 511),
        ('rs_momentum_e200', 610),
        # 安全側（実測の最大値）を採用した2列。中央値は0だが最大が13だった。
        ('vol_surge_21', 13),
        ('vol_surge_rel_spy_21', 13),
    ])
    def test_代表列のwarmup_bars(self, name, expected_warmup):
        assert INDICATOR_COLUMN_REGISTRY[name].warmup_bars == expected_warmup


class TestSuppliedColumnNames:
    """`supplied_column_names()`（5-6d・読み出しコスト削減。5-15cでRECURSIVE型のみに変更）の不変条件。

    T3 ワーカーが `indicators` テーブルから読むべき列を絞り込むための関数。
    5-6d で67列→33列（RECURSIVE21列＋WINDOW型で参照される12列）に絞ったが、
    2回目の `/code-review` 指摘3（5-15c）で、WINDOW型の12列は
    `calculate_indicators` 内で生価格から無条件に上書きされ読まれる前に
    捨てられることが判明したため、RECURSIVE型21列のみに絞った。
    件数（21）を固定し、将来 lookback/inputs の変更で増減したら気づけるように
    する（doc/in_progress/t3_incremental_plan.md §2.3・5-6d・5-15c）。
    """

    def test_列数が21である(self):
        assert len(supplied_column_names()) == 21, (
            f'supplied_column_names() の件数が変わりました: {len(supplied_column_names())}\n'
            'レジストリの kind（RECURSIVE/WINDOW）を変更した場合の意図した増減であれば、'
            'この期待値を更新してください（読み出しコストの見積もりにも影響します）。'
        )

    def test_recursive型と完全一致する(self):
        """5-15c: WINDOW型の12列は読まれる前に必ず上書きされるため、供給対象から外した。
        supplied_column_names() は recursive_column_names() と完全一致するはず。"""
        assert supplied_column_names() == recursive_column_names()

    def test_recursive型は全て含まれる(self):
        supplied = set(supplied_column_names())
        for name in recursive_column_names():
            assert name in supplied, f'{name}: RECURSIVE型なのにsupplied_column_namesに含まれていません'

    def test_戻り値はソート済みタプルで重複が無い(self):
        names = supplied_column_names()
        assert names == tuple(sorted(names))
        assert len(names) == len(set(names))

    def test_戻り値は全てレジストリに登録済みの列である(self):
        registered = set(INDICATOR_COLUMN_REGISTRY.keys())
        for name in supplied_column_names():
            assert name in registered, f'{name}: INDICATOR_COLUMN_REGISTRY に無い列名です'


class TestColumnsWithUndeterminableWarmup:
    """`columns_with_undeterminable_warmup()`（5-15c・2回目のcode-review指摘1）の可視化テスト。

    `warmup_bars >= max_lookback()` の列は、`_calculate_t3_worker` の増分
    ウィンドウ（K本）だけでは「欠陥」か「正当なウォームアップ中」かを
    判別できない（判別不能。`FALLBACK_REASON_WARMUP_UNDETERMINED`）。
    この事実自体（現状は rs_roc_ema_200 の1列）をテストで固定し、
    将来この集合が増えたら気づけるようにする。
    """

    def test_現状はrs_roc_ema_200の1列のみ(self):
        assert columns_with_undeterminable_warmup() == ('rs_roc_ema_200',), (
            f'判別不能になりうる列の集合が変わりました: {columns_with_undeterminable_warmup()}\n'
            'レジストリの warmup_bars/lookback を変更した場合の意図した増減であれば、'
            'この期待値を更新してください（_calculate_t3_worker の分類挙動にも影響します）。'
        )

    def test_該当列はwarmup_barsがmax_lookback以上(self):
        k = max_lookback()
        for name in columns_with_undeterminable_warmup():
            spec = INDICATOR_COLUMN_REGISTRY[name]
            assert spec.warmup_bars is not None and spec.warmup_bars >= k, (
                f'{name}: warmup_bars({spec.warmup_bars}) が max_lookback()({k}) 未満です'
            )


class TestIsStructurallyNullColumn:
    """`is_structurally_null_column`（5-15b・code-review指摘2）の不変条件。

    SPY自身の `rs_*` 列（相対強度）は `calc_relative_strength` が
    `df_spy is None` の早期returnで計算をスキップするため常にNULLになる
    （増分計算の状態が壊れているわけではない）。この判定を
    `tools/db_health_check.py` と `pipeline/phases/t3_indicators.py`
    （`_calculate_t3_worker`）の両方で共有するための関数。
    """

    def test_SPYのrs始まりの列はTrue(self):
        assert is_structurally_null_column('SPY', 'rs_value') is True
        assert is_structurally_null_column('SPY', 'rs_roc_ema_200') is True
        assert is_structurally_null_column('SPY', 'rs_blue_dot_age') is True

    def test_SPYでもrs始まりでない列はFalse(self):
        assert is_structurally_null_column('SPY', 'ema_200') is False
        assert is_structurally_null_column('SPY', 'atr_14') is False
        assert is_structurally_null_column('SPY', 'td9') is False

    def test_SPY以外の銘柄はrs始まりでもFalse(self):
        assert is_structurally_null_column('AAPL', 'rs_value') is False
        assert is_structurally_null_column('AAPL', 'rs_roc_ema_200') is False
