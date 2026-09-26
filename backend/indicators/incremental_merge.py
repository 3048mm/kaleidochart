"""incremental_merge.py — T3 増分計算で RECURSIVE 型列に共通して使う、
「供給済み履歴を保持し、最終行だけ新規計算する」という単一のマージパターンを
提供する共通ユーティリティ。

計画書: doc/in_progress/t3_incremental_plan.md §3.1〜§3.2（5-4b で追加）

## 5-4/5-5 で見つかった問題（背景）

5-4/5-5 の実装は、増分呼び出しの入力を「生価格 K+1 本」のみとし、
RECURSIVE 型列（`rs_value_eN` 等）をコンテキスト行1本のスカラー state から
窓全体（K 本）を再帰的に歩き直す設計だった。この設計では、`rs_ratio_eN`
（WINDOW 型。`rs_value_eN` の直近N本のZ-score）が増分ウィンドウ内でしか
rolling 窓を作れず、窓が N 本育つまでの区間（最大 N-1 行）が実際より
小さい窓で計算された不正確な値になる。この不正確な値が `roc`（14日ROC）
経由で `rs_roc_ema_N`（RECURSIVE）の再帰入力に混入すると、α が小さい列
（N=200 で約0.01）では窓（K=302 程度）を伸ばしても数千行分の「汚染」が
解消しきれなかった（詳細: `t3_incremental_plan.md` §7 の5-4/5-5節）。

## 5-4b の設計

増分呼び出しの入力を「生価格 K+1 本」から「生価格 K+1 本 ＋ 保存済み T3
中間列 K 本」に拡張する。呼び出し元は、直近 K+1 行の DataFrame を渡す。
生の価格列（open/high/low/close/volume）は全行に値があるが、T3 の
計算列（`indicators` テーブルの列）は **行 0..K-1 に保存済みの値が入っており、
最終行（K）だけが NaN**（＝これから計算する日）という契約。行0..K-1は
「実値」とは限らず、先頭側に NULL 区間が残っていてよい —
ただし呼び出し側（`_calculate_t3_worker`）は「単調（値→NULLの戻りが無い）・
最終行（K-1）に値がある」ことだけを検査し、それに加えて「NULL区間の長さ
（`first_valid_pos`）が銘柄の真のウォームアップ本数（`warmup_bars`）を超えて
いないか」を上限チェックとして観測する（t3_fallback_lookback_window計画
5-7a）。ただし5-7d（G3 2周目 R8/R9・案X）で、この上限を超えるケースを
欠陥として弾くのをやめ WARNING のみに変更したため、**呼び出し側が保証する
のは単調性と最終行に値があることだけ**であり、上限（`first_valid_pos <=
warmup_bars`）を超える「欠陥的な NULL 区間」は検出できても弾かれない
（欠陥であっても増分計算はそのまま実行される）。したがって以下のマージ・
rolling の正しさは、NULL区間が実際に真のウォームアップと一致している
ことに依存しており、`_calculate_t3_worker` はそれを検査で保証しているの
ではなく、単調性チェックをすり抜けた「単調だが遅すぎる」ケースが理論上
残りうることを前提に読むこと（5-7e・R10）。

この契約のもとでは:

- **RECURSIVE 型列**（`incremental_state_registry.ColumnKind.RECURSIVE`。
  `ema_*` / `rs_value_eN` / `rs_roc_ema_N` / `td9` / `atr_14` /
  `rs_macd_signal_21` / `rs_blue_dot_age` / `rs_red_dot_age`）:
  供給された行 K-1（＝最終行の1つ前。既に実値が入っている）をシードにし、
  最終行 K だけを1歩計算する。計算自体（`_ema_kernel` 等）は
  `state=None` のときと**同じ関数**を使う。「どこからシードするか」
  （0行目 or K-1行目）と「どこまで計算するか」（全行 or 最終行のみ）が
  変わるだけで、式そのものは変えない（単一実装）。
  この関数が担うのは、計算結果（`computed`。実際は最終行以外NaNになる）を
  供給済みの履歴（`df[col]` の行0..K-1）とマージし、「行0..K-1は供給値、
  行Kだけ新規計算値」という最終列を組み立てること。

- **WINDOW 型列**（`rs_ratio_eN` / `rs_momentum_eN` / `sma_*` 等）:
  この関数は使わない。**通常どおり**（`state=None` と同じ式で。
  `min_periods=window` の rolling）計算すれば正しくなる。理由: WINDOW 型列の
  入力（RECURSIVE 型列 or 生価格）は、RECURSIVE 型列側で本関数によるマージが
  先に適用済みのため、行0..K-1が供給済みの値・行Kが新規計算値になっている。
  行0..K-1の先頭にNULL区間があっても、それが真のウォームアップと一致して
  いる限り、rolling（`min_periods=window`）は全履歴で1回計算した場合と
  同じ NaN/実値を返す（窓の先頭からの本数が window未満ならNaN、以降は
  通常どおり計算されるだけで、供給列がどこから始まっているかには依存
  しない）。ただし呼び出し側は単調性と上限（`first_valid_pos <=
  warmup_bars`。超過は5-7dでWARNINGのみ）だけを検査しており、上限内に
  収まる欠陥的なNULL区間（真のウォームアップと一致していないのに
  `first_valid_pos <= warmup_bars`であるようなケース）は検出できない
  （上記・5-7e R10）。

## 5-4d で見つかった問題（供給列の dtype とゼロ除算）

`calculate_indicators` の戻り値は末尾（`calculate.py` の `df.replace({np.nan: None})`）で
NaN を None に変換している。これは pandas の仕様上、**履歴のどこかに1つでも NaN があれば
列全体が object dtype になる**（実測: AAPL/XLB とも67列中58列が object）。増分呼び出しの
入力契約（5-4b）は、この戻り値をそのまま次の呼び出しの「供給済み履歴」として使うため、
**object dtype がそのまま増分計算の入力に混入する**。

object dtype のまま `np.where` の分岐（例: `volatility.py` の `sma50_atr_mult`）に渡すと、
ガードで弾かれるはずの0除算が **Python のスカラー演算として実行され `ZeroDivisionError`**
になる（`np.where` は全分岐を評価するため。numpy 配列同士の演算なら0除算は inf/nan に
なるだけで例外にならない）。さらに `atr_14`（Wilder 再帰平滑化）は先頭 `window-1`（=13）本が
**リテラルな 0.0**（`_atr_wilder_kernel` の `np.zeros` 初期化）になる。SQLite のホットキャッシュは
504本しか保持しないため、増分呼び出しが「利用可能な行すべて」を供給する限り、
その窓は必ずその銘柄の最古の行（＝この 0.0 が並ぶ区間）を含む。これが重なって
`ZeroDivisionError: float division by zero` が実データのほぼ全銘柄で発生していた。

`normalize_supplied_dtypes` は、増分呼び出しの入口で供給された T3 列（数値列）を
float64 に強制変換し、この問題を解消する。

## 5-15d で見つかった問題（`prev_self_seed` が None を返したときの安全性）

`prev_self_seed` が None を返すのは「非増分モード（`incremental=False`）」と
「増分モードだが前日シードが取得できない（列が無い／行数不足／NaN）」の2通りだが、
呼び出し側の個別カーネル（`calculate_ema_tv`・`_atr_wilder_kernel`・`_td9_kernel`・
`_rs_dot_age_kernel`）はこの区別をせず、増分モードでも `prev_x is None` のまま
「**df 全体（＝増分呼び出しでは K+1 本の窓）**」から SMA 等で再シードしてしまう
契約になっていた。これは §1.2/§1.4 の「遡り不足の計算」を増分呼び出し1回のなかで
再現する——NULL にはならず、K+1 本（例: 401本）の窓の中で再シードした
「それらしいが実質的に誤った値」（例: `ema_200`）が `finalize_incremental_column`
によって最終行にそのまま永続化されてしまう。

現状はこの状況自体が `_calculate_t3_worker`（`pipeline/phases/t3_indicators.py`）の
判定（供給履歴の最終行＝前日行がNaNの列は増分呼び出し自体を行わず
`state=None` にフォールバックし、先頭側のNULL区間には単調性を要求する。
`first_valid_pos <= warmup_bars` の上限は 5-7d 以降 WARNING のみで要求しない。
t3_fallback_lookback_window計画 5-3・5-7d）によって未然に防がれているため、
実害は出ていない。しかし `doc/issue_list.md` に起票済みのとおり、この増分設計をT5へ
流用する予定があり、将来の呼び出し元がこのガードを持つとは限らない。
「呼び出し側のガード頼み」ではなく**契約自体を安全側に閉じる**ため、
`compute_recursive_series` を追加した。

### `compute_recursive_series` の設計

RECURSIVE型列の共通パターン（シード取得 → 計算 → マージ）を1箇所に集約し、
「増分モードでシード取得に失敗したら、計算自体を行わずNaNにする」という
ガードを常に適用する:

- 非増分モード（`incremental=False`）: `prev_self_seed` は常に None を返す契約
  なので、`compute_fn(None)` がそのまま呼ばれ、`state=None` の挙動は一切変わらない
  （5-4 の絶対条件を維持）。
- 増分モードでシード取得成功: `compute_fn(prev_seed)` が実際のシード値で呼ばれ、
  最終行だけの1歩計算になる（5-4bの設計どおり、挙動は変わらない）。
- **増分モードでシード取得失敗**: `compute_fn` を一切呼ばず、NaN を最終計算結果と
  みなす。`finalize_incremental_column` によって最終行だけがNaNになり
  （供給済みの過去行はそのまま維持される）、「それらしいが誤った値」より
  安全な「NULL」を書く。
"""
import numpy as np
import pandas as pd

from .incremental_state_registry import INDICATOR_COLUMN_REGISTRY


def normalize_supplied_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """増分呼び出しで供給されたT3列（`INDICATOR_COLUMN_REGISTRY` の全列）のdtypeを
    float64へ正規化する（5-4d）。

    対象列はレジストリから機械的に導出する（手書きリストにしない）。
    `is_zone_break_bull`/`is_zone_break_weak`（DB上はBoolean）も対象に含めて
    numeric化するが、これら2列は `calculate_indicators` 内で生価格のみから
    毎回無条件に上書きされる（`calc_volatility`等どの計算の入力にもならない）ため、
    floatへ丸めても計算結果に影響しない。

    `pd.to_numeric(..., errors='coerce')` を使うため、None/NaN はNaNへ、
    True/False は 1.0/0.0 へ揃う。df に該当列が無ければ何もしない
    （新規上場のフォールバック等、供給履歴が無いケースを壊さないため）。
    """
    for col in INDICATOR_COLUMN_REGISTRY:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def finalize_incremental_column(df: pd.DataFrame, col: str, computed: pd.Series, incremental: bool) -> pd.Series:
    """増分モードでは、最終行以外は df に供給済みの値をそのまま維持し、
    最終行だけ新規計算値(computed)を採用したSeriesを返す。

    全期間計算モード（incremental=False）では computed をそのまま返す
    （5-4 の絶対条件: state=None の挙動を一切変えない）。

    df にまだ該当列が無い場合（新規上場でフォールバックする場合など）は
    computed をそのまま使う。
    """
    if not incremental:
        return computed
    if col in df.columns:
        merged = df[col].copy()
    else:
        merged = computed.copy()
    merged.iloc[-1] = computed.iloc[-1]
    return merged


def prev_self_seed(df: pd.DataFrame, col: str, incremental: bool):
    """増分モードで、RECURSIVE型列の「前日シード」を df の供給済み履歴
    （最終行の1つ前の行＝行K-1）から直接取り出す。

    外部から別途スカラー辞書（旧 state dict）を受け取る必要をなくすための
    導出関数（5-4b）。df に該当列が無い、行数が足りない、または取り出した値が
    NaN の場合は None を返し、呼び出し元は state=None と同じ全期間計算に
    フォールバックする（§3.5 のフォールバック方針と整合）。

    NaN チェックを本関数自体で行う理由（5-15b・code-review指摘4）: 修正前は
    `df[col].iloc[-2]` が NaN のままそのまま返っていた。`NaN is not None` が
    True になるため、呼び出し元の `if prev_x is not None:` によるフォールバック
    判定をすり抜け、NaN シードで `_ema_kernel` 等を1歩進めてしまう
    （結果は NaN のまま連鎖するだけで実害は小さいが、docstring が謳う
    「None を返してフォールバックする」契約とは食い違う）。現状は
    `_calculate_t3_worker` の `notna()` ゲートが呼び出し前に防いでいるが、
    このガードを T5 に流用する予定（`doc/issue_list.md`）のため、
    契約自体を本関数側で閉じる。
    """
    if not incremental or col not in df.columns or len(df) < 2:
        return None
    seed = df[col].iloc[-2]
    if pd.isna(seed):
        return None
    return seed


def compute_recursive_series(df: pd.DataFrame, col: str, incremental: bool, compute_fn) -> pd.Series:
    """RECURSIVE型列の共通ガード付き計算パターン（シード取得 → 計算 → マージ）を
    1箇所に閉じる（5-15d・2回目のcode-review指摘3）。

    `prev_self_seed(df, col, incremental)` で前日シードを取得し、
    `compute_fn(prev_seed)` に渡す。**増分モードでシードが取得できない場合は
    `compute_fn` を一切呼ばず、NaN で埋めた Series を計算結果として扱う**——
    そうしないと、呼び出し元のカーネル（`calculate_ema_tv` 等）が
    「df 全体（＝増分呼び出しでは K+1 本の窓）」から SMA 等で再シードしてしまい、
    §1.2/§1.4 の「遡り不足の計算」を増分呼び出し1回のなかで再現する
    （NULLにはならず「それらしいが実質的に誤った値」が最終行に永続化される）。
    詳細な経緯はモジュール docstring 参照。

    - **非増分モード**（`incremental=False`）: `prev_self_seed` は常に None を
      返す契約なので `compute_fn(None)` がそのまま呼ばれる（`state=None` の
      挙動は一切変えない。5-4の絶対条件）。
    - **増分モードでシード取得成功**: `compute_fn(prev_seed)` が実際のシード値で
      呼ばれる（5-4bの挙動と同じ）。
    - **増分モードでシード取得失敗**（列が無い／行数不足／NaN）:
      `compute_fn` を呼ばず、NaN の Series を使う。
      `finalize_incremental_column` により最終行だけがNaNになり、供給済みの
      過去行はそのまま維持される。

    現状は `_calculate_t3_worker` の `notna()` ゲートがこの状況の発生自体を
    未然に防いでいるため、既存の日次パイプラインの出力（`state=None` の挙動・
    シード取得成功時の増分1歩の値）は一切変わらない。将来 T5 等でこのガードを
    経由しない呼び出しが増えたときの安全網として機能する。

    Args:
        df: 供給済み履歴を含む DataFrame（`prev_self_seed`/`finalize_incremental_column`
            と同じもの）。
        col: 対象列名。
        incremental: 増分モードかどうか。
        compute_fn: `prev_seed`（float または None）を受け取り、`df` と同じ
            index を持つ計算結果 Series を返す callable。
    """
    prev_seed = prev_self_seed(df, col, incremental)
    if incremental and prev_seed is None:
        computed = pd.Series(np.nan, index=df.index)
    else:
        computed = compute_fn(prev_seed)
    return finalize_incremental_column(df, col, computed, incremental)
