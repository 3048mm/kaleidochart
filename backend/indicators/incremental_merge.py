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
計算列（`indicators` テーブルの列）は **行 0..K-1 に保存済みの実値が
入っており、最終行（K）だけが NaN**（＝これから計算する日）という契約。

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
  この関数は使わない。**通常どおり**（`state=None` と同じ式で）計算すれば
  正しくなる。理由: WINDOW 型列の入力（RECURSIVE 型列 or 生価格）は、
  RECURSIVE 型列側で本関数によるマージが先に適用済みのため、
  行0..K-1が供給済みの実値・行Kが新規計算値で全行が「実値」になっている。
  rolling 計算はこの実値のみからなる列に対して素直に適用すれば、
  窓が不足することなく正しい値が出る（旧設計の「窓が育つまで不正確」問題は
  RECURSIVE側の再構築をやめたことで消える）。

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
"""
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
    導出関数（5-4b）。df に該当列が無い、または行数が足りない場合は
    None を返し、呼び出し元は state=None と同じ全期間計算にフォールバック
    する（§3.5 のフォールバック方針と整合）。
    """
    if not incremental or col not in df.columns or len(df) < 2:
        return None
    return df[col].iloc[-2]
