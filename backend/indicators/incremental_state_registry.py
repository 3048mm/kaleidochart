"""incremental_state_registry.py — T3（`indicators`テーブル）の各列を、増分計算の観点から
分類する宣言的レジストリ。

計画書: doc/in_progress/t3_incremental_plan.md §3.3

## 分類（3種類）

- **RECURSIVE（(a) 再帰）**: 前日の**出力値**が状態。増分計算では前日の保存値を継ぐ。
  `state_columns` に継ぐべき列名を持つ（多くは自分自身）。
- **WINDOW（(b) 窓）**: 直近N本の**入力**が必要・状態を持たない。増分計算では直近N本を読む。
  `lookback` に必要遡り本数を持つ。**推測で埋めない**（下記「lookback を埋める基準」参照）。
- **TWO_SIDED（(c) 両側参照）**: 未来のバーで値が変わる。**現時点で該当列は無い**
  （5-2 で `sp_pivot` / `sp_hl` / `sp_counter` / `zb_*` を実測した結果、確定遅延はあるが
  遡及的な値変化（未来依存）は無いことを確認済み）。このテストは将来 (c) が
  必要になったら意図的に更新する運用（`test_incremental_state_registry.py` 参照）。

## lookback（WINDOW 型）を埋める基準

本番実測で判明している4値（`rs_trend_s200` / `rs_ratio_e200` / `rs_ratio_e200` は WINDOW 型、
`rs_roc_ema_200` は RECURSIVE 型なので note に記載）以外は、**単一の `.rolling(window=N)` /
`.pct_change(N)` を、履歴を必要としない同日内の値（生の価格・出来高、またはその四則演算）に
直接適用しているだけの列**（＝ `sma_200` と同じ構造の列）に限り `lookback=N` を埋める。

これを埋めない理由: `rs_trend_s200` は定義上 `rolling(window=200, min_periods=100)` に見えるが、
実測された必要遡り本数は **99** であり、素朴な窓長読み取り（200 や 100）と一致しない
（EMA 再シードや `rolling_std_independent` 等、複合した計算の収束特性が絡むため）。
複合的な列（`shift()`/`diff()` を経由する、複数の窓を合成する、EMA など再帰列に依存する）は
**同じ落とし穴を持ちうる**ため、素朴な読み取りでは埋めず `None`（未確定）のままにする。
確定作業は後続項目（5-3 の後続）で実測ベースに行う。

## テストで固定する2点

1. T3 の全列（`id`/`symbol_id`/`date` を除く）が本レジストリに登録済み（逆に本レジストリに
   あって T3 に無い列があっても検出する）
2. TWO_SIDED に分類された列が存在しない（5-2 の実測結果の固定）

`calculate_indicators` の実装（`backend/indicators/calculate.py` 等）は本レジストリ作成時点では
**変更していない**。分類の正しさ自体（増分1歩 == 全期間再計算の最終行）は、計画書 5-5 の
等価性テストで別途検証する（誤分類があればそちらで検出される設計）。
"""
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Mapping, Optional, Tuple


class ColumnKind(Enum):
    """T3列1つの増分計算特性。"""

    RECURSIVE = auto()   # (a) 再帰
    WINDOW = auto()       # (b) 窓
    TWO_SIDED = auto()    # (c) 両側参照


@dataclass(frozen=True)
class ColumnSpec:
    """T3列1つ分の増分計算特性の宣言。"""

    name: str
    kind: ColumnKind
    lookback: Optional[int] = None          # WINDOW のみ。必要遡り本数（未確定なら None）
    state_columns: Tuple[str, ...] = field(default_factory=tuple)  # RECURSIVE のみ。継ぐ列名
    note: str = ''                            # 補足（未確定の理由・追加の生値窓など）

    def __post_init__(self):
        if self.kind is ColumnKind.RECURSIVE:
            if not self.state_columns:
                raise ValueError(f'{self.name}: RECURSIVE 型には state_columns が必須です')
            if self.lookback is not None:
                raise ValueError(f'{self.name}: RECURSIVE 型に lookback は使いません（state_columns を使う）')
        else:
            if self.state_columns:
                raise ValueError(f'{self.name}: {self.kind.name} 型に state_columns は使いません')
            if self.lookback is not None and self.lookback < 0:
                raise ValueError(f'{self.name}: lookback は0以上である必要があります')


def _recursive(name: str, state_columns: Tuple[str, ...] = None, note: str = '') -> ColumnSpec:
    """RECURSIVE 型のショートハンド。state_columns 省略時は自分自身を継ぐ。"""
    return ColumnSpec(
        name=name, kind=ColumnKind.RECURSIVE,
        state_columns=state_columns if state_columns is not None else (name,),
        note=note,
    )


def _window(name: str, lookback: Optional[int] = None, note: str = '') -> ColumnSpec:
    """WINDOW 型のショートハンド。"""
    return ColumnSpec(name=name, kind=ColumnKind.WINDOW, lookback=lookback, note=note)


# ============================================================
# INDICATOR_COLUMN_REGISTRY — indicators テーブルの全列（id/symbol_id/date を除く）
# ============================================================
_ENTRIES: Tuple[ColumnSpec, ...] = (
    # --- Simple Moving Averages ---
    # close.rolling(window=N, min_periods=1).mean() のみで決まる純粋な窓。
    # min_periods=1 だが、履歴が窓長以上あるバー（本番の対象銘柄は常にこれ）では
    # min_periods は「NaN を返すかどうか」の閾値にすぎず、集計対象は常に直近N本になる。
    _window('sma_5', 5),
    _window('sma_21', 21),
    _window('sma_50', 50),
    _window('sma_63', 63),
    _window('sma_150', 150),
    _window('sma_200', 200),

    # --- Exponential Moving Averages ---
    # calculate_ema_tv は前日の EMA 値から _ema_kernel で継ぐ再帰計算（moving_averages.py）。
    _recursive('ema_5'),
    _recursive('ema_21'),
    _recursive('ema_50'),
    _recursive('ema_63'),
    _recursive('ema_150'),
    _recursive('ema_200'),

    # --- Volatility ---
    # _td9_kernel は res[i-1]（前日の td9 自身）しか参照しない（volatility.py:11-18）。
    _recursive('td9'),
    # ta.volatility.AverageTrueRange は Wilder の再帰平滑化
    # （atr[i] = (atr[i-1]*(window-1) + true_range[i]) / window）を使っており、
    # window=14 は「窓長」ではなく減衰率パラメータ。sma_* のような単純窓ではないため
    # lookback は埋めない。RECURSIVE への再分類の要否を含め、後続項目で要確認
    # （このレジストリ作成時点で判明した論点。5-3 完了報告で報告済み）。
    _window('atr_14', note='ta ライブラリの Wilder 再帰平滑化。window=14 は単純窓ではない。要確認'),
    _window('atr_pct_14', note='atr_14 と同じ入力窓に依存（atr_14/close*100）。要確認'),
    _window('adr_pct_21', note='(high-low)/low を rolling(21) するだけの単純窓だが、5-3 では数値確認は行わない'),
    _window('change_1d_pct', note='close.pct_change() のみ。5-3 では数値確認は行わない'),
    _window('change_1w_pct', note='close.pct_change(5) のみ。5-3 では数値確認は行わない'),
    _window('change_1m_pct', note='close.pct_change(20) のみ。5-3 では数値確認は行わない'),
    # atr_pct_14（未確定）と sma_50（50）の合成。合成列のため lookback は埋めない。
    _window('sma50_atr_mult', note='atr_pct_14 と sma_50 の合成。atr_pct_14 の未確定を引き継ぐ'),

    # --- Relative Strength (vs SPY) ---
    # close/spy_close の当日値のみで決まる（履歴不要）。rolling も shift も無い。
    _window('rs_value', lookback=0, note='当日の close/spy_close のみで決まる（履歴不要）'),

    # rs_trend_sN = rs_value_e5（RECURSIVE） / rolling(rs_value, N) の合成。
    # rs_trend_s200 のみ本番実測値（99）が判明している。素朴な窓長（200 や min_periods の100）
    # と一致しないため、他の N はここから類推せず None のままにする。
    _window('rs_trend_s5', note='rs_trend_s200(=99) が素朴な窓長と一致しないため類推せず未確定'),
    _window('rs_trend_s14', note='同上。未確定'),
    _window('rs_trend_s21', note='同上。未確定'),
    _window('rs_trend_s63', note='同上。未確定'),
    _window('rs_trend_s200', lookback=99, note='本番 Parquet 実測値（doc/in_progress/t3_incremental_plan.md §1.2）'),

    # rs_value_eN: calculate_ema_tv によるEMA。RECURSIVE。
    _recursive('rs_value_e5'),
    _recursive('rs_value_e14'),
    _recursive('rs_value_e21'),
    _recursive('rs_value_e63'),
    _recursive('rs_value_e200'),

    # rs_ratio_eN: rs_value_eN（RECURSIVE） の rolling(N) Z-score。WINDOW。
    # rs_ratio_e200 のみ実測値（298）が判明している。
    _window('rs_ratio_e5', note='rs_ratio_e200(=298) から類推せず未確定'),
    _window('rs_ratio_e14', note='同上。未確定'),
    _window('rs_ratio_e21', note='同上。未確定'),
    _window('rs_ratio_e63', note='同上。未確定'),
    _window('rs_ratio_e200', lookback=298, note='本番 Parquet 実測値（doc/in_progress/t3_incremental_plan.md §1.2）'),

    # rs_roc_ema_eN: rs_ratio_eN（WINDOW） の14日ROCをEMA平滑化。RECURSIVE。
    # rs_roc_ema_200 は状態列としては RECURSIVE だが、保存状態が無い場合の全期間再計算
    # （フォールバック／移行シード）には本番実測で 511 本必要と判明している（§1.2）。
    _recursive('rs_roc_ema_5'),
    _recursive('rs_roc_ema_14'),
    _recursive('rs_roc_ema_21'),
    _recursive('rs_roc_ema_63'),
    _recursive('rs_roc_ema_200',
               note='状態が無い場合の全期間再計算に本番実測で511本必要（doc/in_progress/t3_incremental_plan.md §1.2）'),

    # rs_momentum_eN: rs_roc_ema_eN（RECURSIVE） の rolling(N) Z-score。WINDOW。
    # rs_momentum_e200 のみ実測値（610）が判明している。
    _window('rs_momentum_e5', note='rs_momentum_e200(=610) から類推せず未確定'),
    _window('rs_momentum_e14', note='同上。未確定'),
    _window('rs_momentum_e21', note='同上。未確定'),
    _window('rs_momentum_e63', note='同上。未確定'),
    _window('rs_momentum_e200', lookback=610, note='本番 Parquet 実測値（doc/in_progress/t3_incremental_plan.md §1.2）'),

    # --- RS-MACD(5, 21, 5) ---
    # rs_macd_line_21 = rs_value_e5 - rs_value_e21（どちらも既に RECURSIVE として
    # 個別に状態継続されている列の当日値どうしの差分）。追加の生値遡りは不要（=0）。
    _window('rs_macd_line_21', lookback=0, note='rs_value_e5 - rs_value_e21 の当日値差分（両者は別途 RECURSIVE で継続）'),
    # rs_macd_signal_21 = EMA(rs_macd_line_21, 5)。RECURSIVE。
    _recursive('rs_macd_signal_21'),
    # rs_macd_hist_21 = rs_macd_line_21 - rs_macd_signal_21（同上、追加の生値遡り不要）。
    _window('rs_macd_hist_21', lookback=0, note='rs_macd_line_21 - rs_macd_signal_21 の当日値差分'),

    # --- Volume ---
    _window('vol_surge_21', note='volume.rolling(21) の単純窓だが、5-3 では数値確認は行わない'),
    _window('vol_surge_rel_spy_21', note='vol_surge_21 と SPY 側 vol_surge の合成（ともに21日窓）。未確定'),
    _window('up_down_vol_ratio_50', note='close.diff() を経由してから rolling(50) するため合成列。未確定'),
    _window('vol_accum_days_5', note='rolling(21) の判定結果を rolling(5) で集計する二段構成。未確定'),
    _window('avg_dollar_volume_21', note='(close*volume).rolling(21) の単純窓だが、5-3 では数値確認は行わない'),

    # --- Price Range from Highs ---
    _window('dist_63d_high_pct', note='high.rolling(63).max() の単純窓だが、5-3 では数値確認は行わない'),
    _window('dist_52w_high_pct', note='high.rolling(252).max() の単純窓だが、5-3 では数値確認は行わない'),

    # --- RS Leading Signals ---
    # cur_b は prev_b（自分自身の前日値）のみに依存する（volatility.py と同型のカーネル）。
    # ただし当日の blue_lit/red_lit フラグ自体は rolling(252, min_periods=1) 由来
    # （RS_DOT_WARMUP_BARS=252）なので、生値の追加窓が別途必要（state_columns には表せない）。
    _recursive('rs_blue_dot_age',
               note='当日フラグの算出に rolling(252) の生値窓が別途必要（RS_DOT_WARMUP_BARS）'),
    _recursive('rs_red_dot_age',
               note='当日フラグの算出に rolling(252) の生値窓が別途必要（RS_DOT_WARMUP_BARS）'),

    # --- Volatility Contraction ---
    # Simple ATR(10)/Simple ATR(50)。atr_14（ta ライブラリ、Wilder）とは異なり非再帰の
    # 単純平均だが、close.shift(1) と2つの異なる窓（10, 50）を合成しているため未確定。
    _window('vcr', note='非再帰の単純移動平均だが shift(1)+2窓の合成のため未確定'),

    # --- Trend Quality ---
    # sma_50/150/200（WINDOW） + sma_200.shift(20)（保存済み中間列の20日前参照） +
    # dist_52w_high_pct 由来の max_252d（WINDOW 252）の合成。単一の数値に落とせないため未確定。
    _window('is_trend_template',
            note='sma_50/150/200 + sma_200 の20日前参照 + 252日高値の合成。未確定'),

    # --- Structure Pivot (LL-HL) ---
    # structure_pivot.py の _scan_for_length は curr_price/curr_idx/prev_idx/is_setup/
    # break_val/break_idx を全履歴にわたって引き継ぐ状態機械。5-2 の実測で「未来のバーで
    # 値が変わる」(c)ではないことは確認済みだが、出力列（sp_pivot/sp_hl）の前日値だけで
    # 継続に十分かは未検証（内部状態には break_idx 等、列に表れない情報も含まれる）。
    # 5-3 では自分自身を状態とみなして登録し、正しさの検証は 5-5 の等価性テストに委ねる。
    _recursive('sp_pivot', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
    _recursive('sp_hl', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
    _recursive('sp_counter', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),

    # --- Direction via Zone Break ---
    # zone_break.py の _zone_break_scan は sp_pivot 以上に多くの内部状態
    # （ssl_bl/bsl_bl/int_ssl_bl/int_bsl_br/is_conf_bl/is_break_bl/is_weak_of に加え、
    # 全履歴のフラクタル・FVGリスト）を持つ状態機械。5-2 の実測で(c)ではないと確認済みだが、
    # T3 に保存されている4列だけで正しく継続できるかは未検証（sp_* と同じ留保）。
    _recursive('is_zone_break_bull', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
    _recursive('zb_ssl', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
    _recursive('zb_bsl', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
    _recursive('is_zone_break_weak', note='5-2実測で(c)ではないと確認済み。単一列状態で十分かは5-5で検証'),
)

INDICATOR_COLUMN_REGISTRY: Mapping[str, ColumnSpec] = {spec.name: spec for spec in _ENTRIES}

if len(INDICATOR_COLUMN_REGISTRY) != len(_ENTRIES):
    # 同名の重複登録（コピペミス）を確実に検出する。
    _names = [spec.name for spec in _ENTRIES]
    _dupes = sorted({n for n in _names if _names.count(n) > 1})
    raise AssertionError(f'INDICATOR_COLUMN_REGISTRY に重複登録があります: {_dupes}')
