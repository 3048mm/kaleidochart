"""incremental_merge.py のテスト。

計画書: doc/in_progress/t3_incremental_plan.md 5-4b・5-15b（code-review指摘4）

`prev_self_seed` は増分モードで RECURSIVE 型列の「前日シード」を df の供給済み
履歴（最終行の1つ前の行）から取り出す。取り出した値が NaN の場合は None を返し、
呼び出し元（`if prev_x is not None:`）が state=None と同じ全期間計算へ
フォールバックできる契約になっている（docstring）。5-15b 以前はこの契約が
守られておらず、NaN がそのまま返っていた（`NaN is not None` が True になるため
呼び出し元の None 判定をすり抜ける）。
"""
import numpy as np
import pandas as pd

from indicators.incremental_merge import finalize_incremental_column, prev_self_seed


class TestPrevSelfSeed:
    """`prev_self_seed` の契約（NaN なら None を返す）の固定。"""

    def test_通常値ならその値を返す(self):
        df = pd.DataFrame({'ema_21': [1.0, 2.0, 3.0]})
        assert prev_self_seed(df, 'ema_21', incremental=True) == 2.0

    def test_incrementalがFalseならNoneを返す(self):
        df = pd.DataFrame({'ema_21': [1.0, 2.0, 3.0]})
        assert prev_self_seed(df, 'ema_21', incremental=False) is None

    def test_列が無ければNoneを返す(self):
        df = pd.DataFrame({'close': [1.0, 2.0, 3.0]})
        assert prev_self_seed(df, 'ema_21', incremental=True) is None

    def test_行数が2未満ならNoneを返す(self):
        df = pd.DataFrame({'ema_21': [1.0]})
        assert prev_self_seed(df, 'ema_21', incremental=True) is None

    def test_取り出した値がNaNならNoneを返す(self):
        """5-15b（code-review指摘4）: 修正前はNaNがそのまま返っていた。"""
        df = pd.DataFrame({'ema_21': [1.0, np.nan, 3.0]})
        assert prev_self_seed(df, 'ema_21', incremental=True) is None

    def test_取り出した値がNoneならNoneを返す(self):
        df = pd.DataFrame({'ema_21': [1.0, None, 3.0]})
        assert prev_self_seed(df, 'ema_21', incremental=True) is None


class TestFinalizeIncrementalColumn:
    """`finalize_incremental_column` の基本挙動の固定（既存の回帰確認）。"""

    def test_全期間計算モードではcomputedをそのまま返す(self):
        df = pd.DataFrame({'ema_21': [1.0, 2.0, 3.0]})
        computed = pd.Series([10.0, 20.0, 30.0])
        result = finalize_incremental_column(df, 'ema_21', computed, incremental=False)
        assert result.tolist() == [10.0, 20.0, 30.0]

    def test_増分モードでは最終行だけcomputedを採用する(self):
        df = pd.DataFrame({'ema_21': [1.0, 2.0, np.nan]})
        computed = pd.Series([np.nan, np.nan, 99.0])
        result = finalize_incremental_column(df, 'ema_21', computed, incremental=True)
        assert result.tolist()[:2] == [1.0, 2.0]
        assert result.iloc[-1] == 99.0

    def test_増分モードで列が無ければcomputedをそのまま使う(self):
        df = pd.DataFrame({'close': [1.0, 2.0, 3.0]})
        computed = pd.Series([10.0, 20.0, 30.0])
        result = finalize_incremental_column(df, 'ema_21', computed, incremental=True)
        assert result.iloc[-1] == 30.0
