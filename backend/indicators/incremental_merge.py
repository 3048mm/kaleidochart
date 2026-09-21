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
"""
import pandas as pd


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
