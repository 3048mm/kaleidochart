"""Structure Pivot (LL-HL) — 押し目構造とブレイクアウト水準の検出。

TradingView 公開スクリプト `Structure Pivot (LL-HL / HH-LH)`（scriptAccess: open_no_auth）
のロング側を移植したもの。チャート描画専用で、T3 インジケータには載せていない
（採否の実測根拠は `doc/in_progress/structure_pivot_chart_plan.md` §1）。

## 検出するもの

1. `ta.pivotlow(low, L, L)` 相当のスイング安値を検出する
2. 直前のピボット安値より今回が **高ければ**（Higher Low）構造成立
3. **LL と HL の「間」の最高値**をピボット＝ブレイクアウト・トリガーとする
4. 当日安値が HL を割ったら構造は消える

## 移植上の要点

- **確定遅延**: `ta.pivotlow(low, L, L)` は左右 L 本を見る中心窓なので、
  ある足がピボットだと分かるのは **L 本先**。本実装は確定バー以降にしか
  構造を返さない（`confirmed_index = hl_index + length`）。
  Pine はチャート上で過去バーの位置に描くため「その時点で分かっていた」ように
  見えるが、実際には分かっていない。ここを崩すと先読みバイアスになる。

- **単調性による1パス化**: ピボット成立条件は L について単調である
  （±L の最安値なら ±j (j<L) の最安値でもある）。したがって
  「その足が成立する最大の L」＝**ピボット強度**を1回計算すれば、
  任意の L はしきい値比較で導出できる。TV 版が謳う
  「Min〜Max を並列に9本走らせる」は、強度計算1パス＋長さ別の状態機械に畳める。

- **Priority Mode は Tightest 固定**: ピボット価格が最小＝最も近い抵抗を選ぶ。
  同値なら短い L を優先する（TV 版の `find_winner` が配列順で先勝ちするのと同じ）。
"""
from dataclasses import dataclass
from typing import List

import numpy as np

def _min_bars_required(min_len: int) -> int:
    """構造成立に最低限必要なバー数。

    LL の確定に左右 min_len 本、HL の確定にさらに min_len 本が要るため
    理論上の下限は 2*min_len + 3。恣意的な固定値を置かず探索範囲から導く。
    """
    return 2 * min_len + 3

#: 長さ帯の既定値。改良版 Advanced Structure Pivot の既定に合わせて 2-5。
#: チャート表示とスクリーナーで同じ水準を見るため、両者でこの値を共有する
#: （doc/in_progress/structure_pivot_screener_plan.md §2.2）。
DEFAULT_MIN_LEN = 2
DEFAULT_MAX_LEN = 5


@dataclass(frozen=True)
class Structure:
    """確定した LL-HL 構造ひとつ分。インデックスは入力配列の位置。"""

    length: int
    """検出に使ったピボット長 L。"""

    ll_index: int
    ll_price: float
    """起点となる安値（Lower Low）。"""

    hl_index: int
    hl_price: float
    """切り上げた安値（Higher Low）。**そのまま損切り候補になる。**"""

    pivot_index: int
    pivot_price: float
    """LL と HL の間の最高値＝ブレイクアウト・トリガー。"""

    confirmed_index: int
    """構造が確定したバー（= hl_index + length）。これ以前は知り得ない。"""

    end_index: int
    """構造が生きていた最後のバー（含む）。描画はここで打ち切る。"""

    invalidated: bool
    """HL 割れで終わったか。最終バーまで生存していれば False。"""

    is_current: bool
    """最終バー時点で生きているか。"""

    broken_at_confirmation: bool
    """確定した時点で既に終値がピボットを超えていたか。

    確定遅延の実害。本番 Parquet 5年の実測では全体の 27〜28% がこれに該当する
    （長さ帯によらずほぼ一定）。「構造は見えたがブレイクは終わっていた」ケース。
    """


def pivot_strength_low(low: np.ndarray) -> np.ndarray:
    """各足について `ta.pivotlow(low, L, L)` が成立する **最大の L** を返す。

    `left_run[i]`  = 直前に `low[j] <= low[i]` となる j までの距離 - 1
    `right_run[i]` = 直後に `low[j] <= low[i]` となる j までの距離 - 1
    強度 = min(left_run, right_run)

    単調スタックで O(n)。同値は「自分より低くない」とみなすため、
    横ばい（全て同値）の系列では強度が 0 になり、ピボットは成立しない。
    """
    low = np.asarray(low, dtype=np.float64)
    n = low.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.int32)

    left = np.empty(n, dtype=np.int64)
    right = np.empty(n, dtype=np.int64)

    stack: List[int] = []
    for i in range(n):
        while stack and low[stack[-1]] > low[i]:
            stack.pop()
        left[i] = i - (stack[-1] if stack else -1) - 1
        stack.append(i)

    stack.clear()
    for i in range(n - 1, -1, -1):
        while stack and low[stack[-1]] > low[i]:
            stack.pop()
        right[i] = (stack[-1] if stack else n) - i - 1
        stack.append(i)

    return np.minimum(left, right).astype(np.int32)


def _scan_for_length(high: np.ndarray, low: np.ndarray, strength: np.ndarray, length: int):
    """長さ `length` の状態機械。Pine の `PivotState.update()` ＋ HL 割れ無効化に対応。

    Returns
    -------
    setup     : bool[n]  その足の時点で構造が生きているか
    hl_idx    : int[n]   現在の HL のインデックス（生きていないときは -1）
    ll_idx    : int[n]   現在の LL のインデックス
    pv_idx    : int[n]   ピボット足のインデックス
    pv_val    : float[n] ピボット価格（生きていないときは nan）
    """
    n = low.shape[0]
    setup = np.zeros(n, dtype=bool)
    hl_idx = np.full(n, -1, dtype=np.int64)
    ll_idx = np.full(n, -1, dtype=np.int64)
    pv_idx = np.full(n, -1, dtype=np.int64)
    pv_val = np.full(n, np.nan, dtype=np.float64)

    curr_price = np.nan
    curr_idx = -1
    prev_idx = -1
    is_setup = False
    break_val = np.nan
    break_idx = -1

    for t in range(n):
        # ピボットは length 本遅れて確定する。t 番目のバーで確定するのは t-length のピボット
        i = t - length
        if i >= 0 and strength[i] >= length:
            price = low[i]
            higher_low = curr_idx >= 0 and price > curr_price
            prev_idx = curr_idx
            curr_price = price
            curr_idx = i
            break_val = np.nan
            break_idx = -1
            if higher_low and prev_idx >= 0:
                start, end = prev_idx + 1, curr_idx  # 両端は含まない
                if end > start:
                    offset = int(np.argmax(high[start:end]))
                    break_idx = start + offset
                    break_val = float(high[break_idx])
            is_setup = higher_low and break_idx >= 0

        # 無効化: HL 割れ（Pine と同じく当日安値で判定）
        if is_setup and low[t] < curr_price:
            is_setup = False

        setup[t] = is_setup
        if is_setup:
            hl_idx[t] = curr_idx
            ll_idx[t] = prev_idx
            pv_idx[t] = break_idx
            pv_val[t] = break_val

    return setup, hl_idx, ll_idx, pv_idx, pv_val


def find_structures(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
) -> List[Structure]:
    """長さ帯を走らせ、各バーで Tightest な構造を勝者として選び、履歴を返す。

    返るのは「一度でも勝者になった構造」だけ。長さ違いでほぼ同じ構造が
    重複して返るのを防ぐ（TV 版が勝者だけを描くのと同じ）。
    各構造の描画区間は `confirmed_index` 〜 `end_index`。

    Parameters
    ----------
    high, low, close : 同じ長さの1次元配列。時系列昇順であること
    min_len, max_len : ピボット長の探索範囲（既定 2〜10）

    Returns
    -------
    `confirmed_index` の昇順に並んだ `Structure` のリスト。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    n = low.shape[0]
    if max_len < min_len or min_len < 1 or n < _min_bars_required(min_len):
        return []
    if high.shape[0] != n or close.shape[0] != n:
        raise ValueError('high / low / close の長さが一致していません')

    strength = pivot_strength_low(low)
    scans = {
        length: _scan_for_length(high, low, strength, length)
        for length in range(min_len, max_len + 1)
    }

    # --- 各バーの勝者（Tightest = ピボット価格が最小。同値なら短い L）---
    winners = {}       # (length, hl_index) -> 勝者だった最初のバー
    for t in range(n):
        best_len = -1
        best_pivot = np.inf
        best_hl = -1
        for length, (setup, hl_idx, _ll, _pv, pv_val) in scans.items():
            if setup[t] and pv_val[t] < best_pivot:
                best_pivot = pv_val[t]
                best_len = length
                best_hl = int(hl_idx[t])
        if best_len > 0:
            winners.setdefault((best_len, best_hl), t)

    # --- 勝者ごとに、その構造が実際に生きていた区間を求める ---
    structures: List[Structure] = []
    for (length, hl_index) in winners:
        setup, hl_idx, ll_idx, pv_idx, pv_val = scans[length]
        alive = np.flatnonzero(setup & (hl_idx == hl_index))
        if alive.size == 0:
            continue
        start, end = int(alive[0]), int(alive[-1])

        confirmed_index = hl_index + length
        invalidated = end + 1 < n and not setup[end + 1]
        structures.append(Structure(
            length=length,
            ll_index=int(ll_idx[start]),
            ll_price=float(low[ll_idx[start]]),
            hl_index=hl_index,
            hl_price=float(low[hl_index]),
            pivot_index=int(pv_idx[start]),
            pivot_price=float(pv_val[start]),
            confirmed_index=confirmed_index,
            end_index=end,
            invalidated=invalidated,
            is_current=end == n - 1,
            broken_at_confirmation=bool(close[confirmed_index] >= pv_val[start])
            if confirmed_index < n else False,
        ))

    structures.sort(key=lambda s: (s.confirmed_index, s.length))
    return structures


def structure_pivot_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
) -> tuple:
    """各バーの `(sp_pivot, sp_hl)` を返す。T3 インジケータ用。

    描画用の `find_structures()` が「構造のリスト」を返すのに対し、こちらは
    **1バー1値の配列**を返す。両者は同じ `_scan_for_length()` を使うので
    検出ロジックは1つのまま（勝者選択も Tightest で同一）。

    構造が生きていないバーは `NaN`。**確定前のバーも `NaN`** であり、
    ピボットが `L` 本先まで確定しない性質はここでも保たれる。

    Returns
    -------
    sp_pivot : ブレイクアウト水準（LL と HL の間の最高値）
    sp_hl    : HL の価格（そのまま損切り候補になる）
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    n = low.shape[0]
    sp_pivot = np.full(n, np.nan)
    sp_hl = np.full(n, np.nan)
    if n == 0:
        return sp_pivot, sp_hl
    if high.shape[0] != n or close.shape[0] != n:
        raise ValueError('high / low / close の長さが一致していません')
    if max_len < min_len or min_len < 1 or n < _min_bars_required(min_len):
        return sp_pivot, sp_hl

    strength = pivot_strength_low(low)
    for length in range(min_len, max_len + 1):
        setup, hl_idx, _ll_idx, _pv_idx, pv_val = _scan_for_length(high, low, strength, length)
        # Tightest: ピボット価格が小さい方を採る。未設定（NaN）なら無条件に採る
        better = setup & (np.isnan(sp_pivot) | (pv_val < sp_pivot))
        if not better.any():
            continue
        sp_pivot[better] = pv_val[better]
        sp_hl[better] = low[hl_idx[better]]

    return sp_pivot, sp_hl


def pivot_strength_high(high: np.ndarray) -> np.ndarray:
    """各足について `ta.pivothigh(high, L, L)` が成立する **最大の L** を返す。

    `pivot_strength_low` の鏡像。符号を反転した系列の安値側強度に等しいので、
    ロジックを二重に持たず委譲する（片方だけ直す事故を防ぐ）。
    """
    high = np.asarray(high, dtype=np.float64)
    return pivot_strength_low(-high)


def _pivot_state_track(strength: np.ndarray, length: int):
    """Pine の `PivotState.update()` 相当。各バー時点の (prev_idx, curr_idx) を返す。

    新しいピボットが確定したら `prev = curr; curr = 新` と押し出す。
    確定遅延を守るため、位置 `i` のピボットを見るのは `i + length` 本目から。
    高値側・安値側のどちらにも使う（渡す strength が違うだけ）。
    """
    n = strength.shape[0]
    prev_i = np.full(n, -1, dtype=np.int64)
    curr_i = np.full(n, -1, dtype=np.int64)
    p, c = -1, -1
    for t in range(n):
        i = t - length
        if i >= 0 and strength[i] >= length:
            p, c = c, i
        prev_i[t] = p
        curr_i[t] = c
    return prev_i, curr_i


def counter_trend_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
) -> np.ndarray:
    """各バーのカウンタートレンドライン値を返す。引かれないバーは `NaN`。

    作者の改良版が `rt_cnt_break`（Trend Line Break）として出しているシグナルの土台。
    **LL-HL 構造が生きていない期間**に、ショート側のピボット高値2点を結んだ線を引く。
    終値がこれを上抜けたら「次の上昇トレンドへの転換」とみなす。

    Pine 原文（`get_counter_coordinates(opp_arr, w.curr_idx, true)`）の移植:

    - **候補点はショート側の状態が持つ prev / curr の2点だけ**。長さ帯 2-5 なので
      全部で最大8点しかない。「窓の中の全ピボット」ではない
    - **アンカー1** = `idx <= limit_idx` の候補のうち**価格が最大**のもの
      （prev / curr の両方を見る）
    - **アンカー2** = `anchor1 < idx < limit_idx` の **curr のみ**が候補で、
      アンカー1 から見た傾き `m` が**最大**のもの
    - `limit_idx` = ロング側の状態が持つ現在のピボット安値（`w.curr_idx`）
    - 値 = ``y2 + m * (i - x2)``

    > [!IMPORTANT]
    > **傾きは最大化であって最小化ではない。** 原文は `find_highs` のとき
    > `if m > best_slope` で更新する。アンカー1 が最高値なので後続は下向きになり、
    > 最大化＝**最も浅い下向き**＝後続の高値を上から包む線になる。
    > 「最も急な下向き」と読み違えると別物の線になる（2026-08-29 に実際に間違えた）。
    > 傾きの符号は**判定しない**（原文にその条件は無い）。

    > [!NOTE]
    > 候補点が prev / curr の2点だけなので、値は**直近の数ピボット**だけで決まる。
    > 履歴を何本読ませても同じ値になり、T3（SQLite 約500本）と Parquet の
    > バックフィル（全期間）が一致する。回帰テストは
    > `test_counter_trend.py::TestHistoryLengthStability`。
    """
    return _counter_scan(high, low, close, min_len, max_len)[0]


def _counter_scan(high, low, close, min_len: int, max_len: int):
    """カウンタートレンドラインの走査（唯一の実装）。

    Returns:
        ``(line, a1_idx, a2_idx)``。いずれも長さ n の配列で、ラインが引かれない
        バーは ``line`` が NaN、``a1_idx`` / ``a2_idx`` が -1。

    `counter_trend_series`（バーごとのスカラー）と `find_counter_trends`
    （アンカー付きの線分列）は**どちらもこの関数の結果から導く**。
    `_scan_for_length` が `structure_pivot_series` と `find_structures` の
    単一の走査であるのと同じ関係で、片方だけ直す事故を防ぐ。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)

    n = high.shape[0]
    line = np.full(n, np.nan)
    a1_idx = np.full(n, -1, dtype=np.int64)
    a2_idx = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return line, a1_idx, a2_idx
    if low.shape[0] != n or close.shape[0] != n:
        raise ValueError('high / low / close の長さが一致していません')
    if max_len < min_len or min_len < 1 or n < _min_bars_required(min_len):
        return line, a1_idx, a2_idx

    sp_pivot, _sp_hl = structure_pivot_series(high, low, close, min_len, max_len)
    active = ~np.isnan(sp_pivot)

    strength_high = pivot_strength_high(high)
    strength_low = pivot_strength_low(low)
    lengths = range(min_len, max_len + 1)
    short_states = {L: _pivot_state_track(strength_high, L) for L in lengths}
    long_states = {L: _pivot_state_track(strength_low, L) for L in lengths}

    for t in range(n):
        if active[t]:
            continue

        # limit_idx = ロング側の現在のピボット安値。帯の中で最も新しいものを採る
        limit_idx = -1
        for L in lengths:
            c = long_states[L][1][t]
            if c > limit_idx:
                limit_idx = c
        if limit_idx < 0:
            continue

        # --- アンカー1: limit_idx までで価格が最大の候補（prev / curr 両方） ---
        a1 = -1
        for L in lengths:
            prev_i, curr_i = short_states[L]
            for idx in (prev_i[t], curr_i[t]):
                if 0 <= idx <= limit_idx and (a1 < 0 or high[idx] > high[a1]):
                    a1 = idx
        if a1 < 0:
            continue

        # --- アンカー2: a1 < idx < limit_idx の curr のみ。傾きが最大のもの ---
        a2 = -1
        best_slope = 0.0
        for L in lengths:
            idx = short_states[L][1][t]
            if idx <= a1 or idx >= limit_idx:
                continue
            m = (high[idx] - high[a1]) / (idx - a1)
            if a2 < 0 or m > best_slope:
                a2 = idx
                best_slope = m
        if a2 < 0:
            continue

        line[t] = high[a2] + best_slope * (t - a2)
        a1_idx[t] = a1
        a2_idx[t] = a2

    return line, a1_idx, a2_idx


@dataclass(frozen=True)
class CounterTrend:
    """カウンタートレンドライン1本分。インデックスは入力配列の位置。

    「1本」の単位は**アンカー組 (a1, a2) が同じ連続区間**。アンカーが入れ替われば
    別の線として切る。区間内では値が完全な直線なので、線分として描いてよい。
    """

    a1_index: int
    a1_price: float
    """アンカー1（`limit_idx` までで価格が最大の高値ピボット）。"""

    a2_index: int
    a2_price: float
    """アンカー2（`a1 < idx < limit_idx` で傾きが最大の高値ピボット）。"""

    slope: float
    """1バーあたりの値動き。アンカー1 が最高値なので構造上つねに 0 以下。"""

    start_index: int
    """このアンカー組で線が引かれ始めたバー。**必ず `a2_index` より後**
    （確定遅延。`counter_trend_series` の NOTE 参照）。"""

    end_index: int
    """同じアンカー組が続いた最後のバー。"""

    is_current: bool
    """最終バーまで生きているか。"""

    def value_at(self, index: int) -> float:
        """バー `index` でのライン値。区間外でも直線を延長して返す。"""
        return self.a2_price + self.slope * (index - self.a2_index)


def find_counter_trends(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
) -> List['CounterTrend']:
    """カウンタートレンドラインを**線分の列**として返す（チャート描画用）。

    `counter_trend_series` と同じ `_counter_scan` を使うので、
    各線分の区間内の値は series と厳密に一致する（`test_counter_trend.py`
    の `TestFindCounterTrendsEquivalence` が縛っている）。

    引かれるバーが無ければ空リストを返す（例外にしない）。チャートの一部なので、
    線が出ないことで画面全体を落とさない。
    """
    line, a1_idx, a2_idx = _counter_scan(high, low, close, min_len, max_len)
    n = line.shape[0]
    if n == 0:
        return []

    high = np.asarray(high, dtype=np.float64)
    segments: List[CounterTrend] = []
    start = -1
    for t in range(n + 1):
        drawn = t < n and a1_idx[t] >= 0
        same = (drawn and start >= 0
                and a1_idx[t] == a1_idx[start] and a2_idx[t] == a2_idx[start])
        if same:
            continue
        if start >= 0:
            a1, a2 = int(a1_idx[start]), int(a2_idx[start])
            segments.append(CounterTrend(
                a1_index=a1, a1_price=float(high[a1]),
                a2_index=a2, a2_price=float(high[a2]),
                slope=(float(high[a2]) - float(high[a1])) / (a2 - a1),
                start_index=start, end_index=t - 1,
                is_current=(t == n),
            ))
        start = t if drawn else -1

    return segments
