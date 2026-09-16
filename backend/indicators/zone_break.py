"""Direction via Zone Break [by rukich] — 3本足フラクタルによる SSL/BSL 検出と状態機械。

TradingView 公開スクリプト `Direction via Zone Break [by rukich]`（Pine v6、MPL 2.0、
scriptAccess: open_no_auth）を移植したもの。Pine 原文全文は `tmp/direction_via_zone_break.txt`
（gitignore 対象・ワークツリーに実在）。状態遷移の解析結果は
`doc/completed/zone_break_plan.md` §3.1.1 を参照。

## 検出するもの

- 3本足フラクタル（`low[i-1] < low[i-2] and low[i-1] < low[i]` / 高値側は対称）で
  SSL（直近安値）・BSL（直近高値）を確定・追従更新する
- 確定 close が SSL/BSL を超えたら、次のフラクタル確定で「反転（フリップ）」または
  「継続ブレイク（ゾーン更新のみ）」のどちらかが起きる
- FVG（Fair Value Gap、3本足ギャップ）ゾーンの生成・無効化を追跡し、直近ゾーンが
  無効化済みかどうかを `is_weak` として返す

## 移植上の要点

- **境界を設けたフォールバック探索（`detector_bsl_last_fractal` / `detector_ssl_last_fractal`
  の効率化）**: Pine 原文は反転イベントの瞬間に「全履歴を遡って最初に見つかる逆側の
  3本足フラクタル」を探す（原文 L31-50）。素直に移植すると最悪 O(n²) になる
  （`doc/completed/zone_break_plan.md` §2.2 で非採用と確定済み）。
  数学的には `detector_bsl_last_fractal(i)` の `k` 番目の探索対象は
  `fractal_high(j, 0)`（`j = i - k`）と厳密に等価（中心バーの左右1本ずつだけで
  判定する条件のため、`i` 自体には依存しない）。したがって「確定済みの3本足
  フラクタルを、採用されたかどうかに関わらず全て前向きに積んでおき、
  フォールバックが必要になったら末尾を参照するだけ」で同じ結果が O(1) で得られる。
  この等価性は `tmp/zone_break_naive_port.py`（無制限バックスキャンの逐語移植・
  オラクル）との出力一致テスト（`test_zone_break.py::test_境界を設けた探索が無制限版と一致する`）
  で担保する。
- **確定遅延は無い**: ブレイク判定自体は当日の確定 close だけで決まる
  （3本足フラクタルの確定は1本遅れるが、先読みではない）。
- **FVG 配列の「詰めながら進める」癖**: Pine 原文は `array.remove` で配列を縮めながら
  同じ添字 `i` を進めるため、無効化が起きた回は次の1要素のチェックを事実上
  スキップする（原文自体のバグ疑い、原文 L325-343 / L363-381）。本実装もこの癖を
  忠実に再現する（`while idx < len(...)` ループで `pop(idx)` 後に `idx` を進めない）。
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


def _zone_break_scan(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[Dict], List[Dict]]:
    """状態機械の唯一の実装。`zone_break_series` と `find_zone_break_zones` が共用する。

    Returns
    -------
    is_bull, zb_ssl, zb_bsl, is_weak : 各バーの状態（`zone_break_series` が返すもの）
    fvg_bull_records, fvg_bear_records : 完結（無効化 or 末尾到達）した FVG ボックスの
        辞書リスト。`find_zone_break_zones` がチャート描画用に整形する。
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = high.shape[0]

    out_is_bull = np.zeros(n, dtype=bool)
    out_ssl = np.zeros(n, dtype=np.float64)
    out_bsl = np.zeros(n, dtype=np.float64)
    out_is_weak = np.zeros(n, dtype=bool)

    if n == 0:
        return out_is_bull, out_ssl, out_bsl, out_is_weak, [], []
    if low.shape[0] != n or close.shape[0] != n:
        raise ValueError('high / low / close の長さが一致していません')

    ssl_bl = 0.0
    bsl_bl = 0.0
    int_ssl_bl = 0.0
    int_bsl_br = 0.0
    is_conf_bl = False
    is_break_bl = False
    is_bull = True
    is_bear = False
    is_weak_of = False
    prev_is_conf_bl = False  # isConf_bl[1] 相当（前バー確定後の値）

    # 確定済み3本足フラクタル（採用されたかどうかに関わらず全履歴）。
    # 反転時のフォールバック探索を O(1) にするため、末尾を見るだけで済むように積んでおく。
    high_fractals: List[Tuple[int, float]] = []
    low_fractals: List[Tuple[int, float]] = []

    fvg_bl: List[Dict] = []  # 強気FVG（生成中）
    fvg_br: List[Dict] = []  # 弱気FVG（生成中）
    fvg_bl_done: List[Dict] = []  # 完結（無効化 or 末尾到達）した強気FVG
    fvg_br_done: List[Dict] = []

    for i in range(n):
        # 3本足フラクタル確定判定（中心バーは1本前）。同じ値をこのバー内で使い回す。
        fh0 = i >= 2 and high[i - 1] > high[i - 2] and high[i - 1] > high[i]
        fl0 = i >= 2 and low[i - 1] < low[i - 2] and low[i - 1] < low[i]

        if fh0:
            high_fractals.append((i - 1, high[i - 1]))
        if fl0:
            low_fractals.append((i - 1, low[i - 1]))

        c0 = close[i]

        # ---- 初回 SSL/BSL 確定 + ブレイク前の追従更新（Bull側） ----
        if is_bull:
            if ssl_bl <= 0.0 and fl0:
                ssl_bl = low[i - 1]
            if ssl_bl > 0.0 and bsl_bl <= 0.0 and fl0 and low[i - 1] < ssl_bl:
                ssl_bl = low[i - 1]
            if bsl_bl <= 0.0 and ssl_bl > 0.0 and fh0:
                bsl_bl = high[i - 1]
            if bsl_bl > 0.0 and int_ssl_bl <= 0.0 and fh0 and high[i - 1] > bsl_bl:
                bsl_bl = high[i - 1]

        # ---- 同上（Bear側、対称） ----
        if is_bear:
            # 次行の `bsl_bl = 0.0` はBull側の `ssl_bl = low[i-1]`（L108）と非対称に見えるが、
            # Pine原文L92-93がそのまま `bsl_bl := 0.0`（実質no-op）であり翻訳ミスではない。
            # 原文に忠実に、あえて未修正のまま残す（2026-09-16 code-review Angle Aで確認済み。
            # 影響は high_fractals が空の状態でBearへフリップする極端なケースのみで、
            # 数年分の実データでは事実上発生しない。計画書 §8.1 参照）。
            if bsl_bl <= 0.0 and fh0:
                bsl_bl = 0.0
            if bsl_bl > 0.0 and ssl_bl <= 0.0 and fh0 and high[i - 1] > bsl_bl:
                bsl_bl = high[i - 1]
            if ssl_bl <= 0.0 and bsl_bl > 0.0 and fl0:
                ssl_bl = low[i - 1]
            if ssl_bl > 0.0 and int_bsl_br <= 0.0 and fl0 and low[i - 1] < ssl_bl:
                ssl_bl = low[i - 1]

        # ---- 内部フラクタル候補の追従更新 ----
        if is_bull:
            if bsl_bl > 0.0 and int_ssl_bl <= 0.0 and fl0:
                int_ssl_bl = low[i - 1]
            if int_ssl_bl > 0.0 and fl0 and low[i - 1] < int_ssl_bl:
                int_ssl_bl = low[i - 1]
        if is_bear:
            if ssl_bl > 0.0 and int_bsl_br <= 0.0 and fh0:
                int_bsl_br = high[i - 1]
            if int_bsl_br > 0.0 and fh0 and high[i - 1] > int_bsl_br:
                int_bsl_br = high[i - 1]

        # ---- ブレイク（反転）/ コンファーム（継続）判定: Bull側 ----
        if is_bull:
            if ssl_bl > 0.0 and int_ssl_bl > 0.0:
                if not is_break_bl and c0 < ssl_bl:
                    is_break_bl = True
                    is_weak_of = False
                if is_break_bl and fl0:
                    ssl_bl = low[i - 1]
                    int_ssl_bl = 0.0
                    bsl_bl = high_fractals[-1][1] if high_fractals else 0.0
                    is_break_bl = False
                    is_bull = False
                    is_bear = True

            if bsl_bl > 0.0 and int_ssl_bl > 0.0:
                if not is_conf_bl and c0 > bsl_bl:
                    is_conf_bl = True
                    is_weak_of = False
                if is_conf_bl and fh0:
                    bsl_bl = high[i - 1]
                    ssl_bl = int_ssl_bl
                    int_ssl_bl = 0.0
                    is_conf_bl = False

        # ---- ブレイク（反転）/ コンファーム（継続）判定: Bear側（対称） ----
        if is_bear:
            if bsl_bl > 0.0 and int_bsl_br > 0.0:
                if not is_break_bl and c0 > bsl_bl:
                    is_break_bl = True
                    is_weak_of = False
                if is_break_bl and fh0:
                    bsl_bl = high[i - 1]
                    int_bsl_br = 0.0
                    ssl_bl = low_fractals[-1][1] if low_fractals else 0.0
                    is_break_bl = False
                    is_bear = False
                    is_bull = True

            if ssl_bl > 0.0 and int_bsl_br > 0.0:
                if not is_conf_bl and c0 < ssl_bl:
                    is_conf_bl = True
                    is_weak_of = False
                if is_conf_bl and fl0:
                    ssl_bl = low[i - 1]
                    bsl_bl = int_bsl_br
                    int_bsl_br = 0.0
                    is_conf_bl = False

        # ---- FVG 検出・無効化判定: Bull側 ----
        if is_bull:
            if ssl_bl > 0.0:
                # 継続ブレイクの close 確定瞬間（フラクタル確定を待たない）でリセット
                if is_conf_bl and not prev_is_conf_bl:
                    for rec in fvg_bl:
                        rec['end_index'] = i - 1
                        rec['invalidated'] = False
                    fvg_bl_done.extend(fvg_bl)
                    fvg_bl = []
                if i >= 2 and low[i] > high[i - 2]:
                    fvg_bl.append({
                        'bottom': high[i - 2], 'top': low[i],
                        'left_index': i - 2, 'right_index': i,
                    })
                idx = 0
                while idx < len(fvg_bl):
                    rec = fvg_bl[idx]
                    if c0 > rec['bottom']:
                        rec['right_index'] = i
                        idx += 1
                    elif c0 < rec['bottom']:
                        rec['end_index'] = i
                        rec['invalidated'] = True
                        fvg_bl_done.append(rec)
                        # Pine の array.remove と同じく詰める(次要素は同idxへ) → idxは進めない
                        # （無効化が起きた回は次の1要素のチェックを事実上スキップする原文の癖）
                        fvg_bl.pop(idx)
                        if not is_weak_of:
                            is_weak_of = True
                    else:
                        idx += 1

        # ---- FVG 検出・無効化判定: Bear側（対称） ----
        if is_bear:
            if bsl_bl > 0.0:
                if is_conf_bl and not prev_is_conf_bl:
                    for rec in fvg_br:
                        rec['end_index'] = i - 1
                        rec['invalidated'] = False
                    fvg_br_done.extend(fvg_br)
                    fvg_br = []
                if i >= 2 and high[i] < low[i - 2]:
                    fvg_br.append({
                        'top': low[i - 2], 'bottom': high[i],
                        'left_index': i - 2, 'right_index': i,
                    })
                idx = 0
                while idx < len(fvg_br):
                    rec = fvg_br[idx]
                    if c0 < rec['top']:
                        rec['right_index'] = i
                        idx += 1
                    elif c0 > rec['top']:
                        rec['end_index'] = i
                        rec['invalidated'] = True
                        fvg_br_done.append(rec)
                        fvg_br.pop(idx)
                        if not is_weak_of:
                            is_weak_of = True
                    else:
                        idx += 1

        prev_is_conf_bl = is_conf_bl

        out_is_bull[i] = is_bull
        out_ssl[i] = ssl_bl
        out_bsl[i] = bsl_bl
        out_is_weak[i] = is_weak_of

    # 末尾まで生存した FVG を完結リストへ（無効化されていない＝現存中）
    for rec in fvg_bl:
        rec.setdefault('end_index', n - 1)
        rec.setdefault('invalidated', False)
    for rec in fvg_br:
        rec.setdefault('end_index', n - 1)
        rec.setdefault('invalidated', False)
    fvg_bl_done.extend(fvg_bl)
    fvg_br_done.extend(fvg_br)

    return out_is_bull, out_ssl, out_bsl, out_is_weak, fvg_bl_done, fvg_br_done


def zone_break_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """各バーの `(is_bull, zb_ssl, zb_bsl, is_weak)` を返す。T3 インジケータ用。

    - `is_bull`: 現在のトレンド方向（True=Bull, False=Bear）。初期値 True
    - `zb_ssl` / `zb_bsl`: 現在の SSL/BSL 価格。未確定の間は 0.0
    - `is_weak`: 直近のトレンド内で最も近い FVG ゾーンが無効化済みか
    """
    is_bull, zb_ssl, zb_bsl, is_weak, _fvg_bl, _fvg_br = _zone_break_scan(high, low, close)
    return is_bull, zb_ssl, zb_bsl, is_weak


@dataclass(frozen=True)
class ZoneBreakLevel:
    """SSL または BSL の水平線区間ひとつ分（チャート描画用）。

    Pine 原文はデフォルト設定（`isShowPrevSSLBSL=false`）では過去の線を消して
    「現在の値」だけを描き続ける。したがって区間は `zb_ssl`/`zb_bsl` の値が
    一定に保たれる連続区間としてそのまま導出できる
    （線オブジェクトの生成/削除タイミングを個別追跡するより単純で、見た目は同じ）。
    """

    kind: str
    """'ssl' または 'bsl'。"""

    price: float
    start_index: int
    end_index: int
    is_current: bool
    """最終バー時点で生きているか。"""


@dataclass(frozen=True)
class ZoneBreakFvg:
    """FVG（Fair Value Gap）ボックスひとつ分（チャート描画用）。"""

    kind: str
    """'bull' または 'bear'。"""

    left_index: int
    """固定境界（生成時の `high[i-2]` / `low[i-2]`）側のバー。"""

    right_index: int
    """最後に延長された（または生成された）バー。"""

    top: float
    bottom: float

    invalidated: bool
    """終値がゾーンを割り込んで無効化されたか。"""

    is_current: bool
    """最終バーまで無効化されずに残っているか。"""


@dataclass(frozen=True)
class ZoneBreakState:
    """`find_zone_break_zones` の返り値。SSL/BSL の区間列 + FVG ボックスの区間列。"""

    ssl_levels: List[ZoneBreakLevel]
    bsl_levels: List[ZoneBreakLevel]
    fvg_boxes: List[ZoneBreakFvg]


def _segments_from_series(values: np.ndarray, kind: str) -> List[ZoneBreakLevel]:
    """`zb_ssl`/`zb_bsl` の1次元配列から、値が一定に保たれる連続区間を切り出す。

    値が 0.0 以下（未確定）の区間は含めない。
    """
    n = values.shape[0]
    segments: List[ZoneBreakLevel] = []
    start = -1
    for i in range(n + 1):
        active = i < n and values[i] > 0.0
        same = active and start >= 0 and values[i] == values[start]
        if same:
            continue
        if start >= 0:
            segments.append(ZoneBreakLevel(
                kind=kind, price=float(values[start]),
                start_index=start, end_index=i - 1, is_current=(i == n),
            ))
        start = i if active else -1
    return segments


def find_zone_break_zones(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> ZoneBreakState:
    """SSL/BSL ラインの区間列 + FVG ボックスの区間列を返す（チャート描画用）。

    `zone_break_series` と同じ `_zone_break_scan` を使うので、検出ロジックを
    二重に持たない（`structure_pivot._scan_for_length` と同じ共用の考え方）。
    """
    is_bull, zb_ssl, zb_bsl, _is_weak, fvg_bl_done, fvg_br_done = _zone_break_scan(high, low, close)
    n = zb_ssl.shape[0]

    ssl_levels = _segments_from_series(zb_ssl, 'ssl')
    bsl_levels = _segments_from_series(zb_bsl, 'bsl')

    fvg_boxes: List[ZoneBreakFvg] = []
    for rec in fvg_bl_done:
        invalidated = bool(rec.get('invalidated', False))
        end_index = rec.get('end_index', n - 1)
        fvg_boxes.append(ZoneBreakFvg(
            kind='bull', left_index=rec['left_index'], right_index=end_index,
            top=float(rec['top']), bottom=float(rec['bottom']),
            invalidated=invalidated, is_current=(end_index == n - 1 and not invalidated),
        ))
    for rec in fvg_br_done:
        invalidated = bool(rec.get('invalidated', False))
        end_index = rec.get('end_index', n - 1)
        fvg_boxes.append(ZoneBreakFvg(
            kind='bear', left_index=rec['left_index'], right_index=end_index,
            top=float(rec['top']), bottom=float(rec['bottom']),
            invalidated=invalidated, is_current=(end_index == n - 1 and not invalidated),
        ))
    fvg_boxes.sort(key=lambda b: b.left_index)

    _ = is_bull  # チャート側は現在の方向を最終要素から得られるため、ここでは未使用
    return ZoneBreakState(ssl_levels=ssl_levels, bsl_levels=bsl_levels, fvg_boxes=fvg_boxes)
