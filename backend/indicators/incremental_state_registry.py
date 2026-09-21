"""incremental_state_registry.py — T3（`indicators`テーブル）の各列を、増分計算の観点から
分類する宣言的レジストリ。

計画書: doc/in_progress/t3_incremental_plan.md §3.3（5-3b で再設計。旧版の要点は下記参照）

## 5-3b での再設計（実測ベース）

旧版（5-3）は「RECURSIVE 型に lookback は使わない（state_columns だけで継ぐ）」という
制約を置いていたが、以下3点の実測・確認により誤りと判明したため撤回した。

1. **`atr_14` は RECURSIVE で正しい**（`ta` ライブラリの Wilder 再帰平滑化。旧版の
   「単純窓ではないので要確認」というメモは誤りだった）
2. **`sp_*` / `zb_*` は追加の状態列が不要**。状態機械ではあるが、前方切り詰め実測
   （60〜120本の生価格があれば全履歴と一致）の結果、短い生価格窓から再構築できることが
   分かったため **WINDOW に再分類**した（RECURSIVE ではない）
3. **RECURSIVE 列にも非再帰の入力が要る**（例: `atr_14` は前日の自分自身に加えて当日の
   high/low/close が要る。`rs_roc_ema_N` は前日の自分自身に加えて `rs_ratio_e{N}` の
   14日前の値が要る）。そのため `state_columns`（継ぐ列名のみ）を廃止し、
   `prev_self`（前日の自列値を継ぐか）＋`inputs`（当日〜数日分、参照する列）＋
   `lookback`（inputs から何行必要か）の3つに分解した

## 分類（3種類）

- **RECURSIVE（(a) 再帰）**: 前日の**自列の出力値**を継ぐ（`prev_self=True`）。
  加えて、当日分（〜数日分）の非再帰入力を `inputs`/`lookback` に持つ
  （例: `ema_*` は当日の `close` だけ、`atr_14` は当日の `high`/`low`/`close` に加えて
  前日の `close` も要る＝`lookback=2`）。
- **WINDOW（(b) 窓）**: 直近N本の**入力**が必要・状態を持たない（`prev_self=False`）。
  `inputs`（参照する列。生の価格列 or 保存済み T3 列）と `lookback`（必要遡り本数）を持つ。
- **TWO_SIDED（(c) 両側参照）**: 未来のバーで値が変わる。**現時点で該当列は無い**
  （5-2 で `sp_pivot` / `sp_hl` / `sp_counter` / `zb_*` を実測した結果、確定遅延はあるが
  遡及的な値変化（未来依存）は無いことを確認済み）。このテストは将来 (c) が
  必要になったら意図的に更新する運用（`test_incremental_state_registry.py` 参照）。

## lookback の単位

**「inputs から何行必要か」＝行数**（0始まりのオフセットではない）。
**当日のみで決まるなら `lookback=1`**（例: `rs_value = close/spy_close` は当日値のみで
決まるので `lookback=1`。旧版は「履歴不要」を `lookback=0` としていたが、5-3b で
「行数」に統一したため `1` に変わっている）。

例:
- `sma_200`: `close` を200行（当日〜199日前）→ `lookback=200`
- `td9`: `close[i]` と `close[i-4]` の比較 → `lookback=5`
- `rs_roc_ema_N`: `rs_ratio_e{N}[i]` と14日前の `[i-14]` → `lookback=15`
- `atr_14`（RECURSIVE）: 前日の自分自身 ＋ 当日の high/low/close ＋ TR算出に前日の close
  → `lookback=2`

## lookback を埋める基準（5-3b）

本番実測（8銘柄・前方切り詰め・rtol=1e-9。計画書に転記済み）は「ゼロから再計算する場合」の
値であり、**RECURSIVE 列を保存済み前日値から継ぐ設計では直接使わない**
（継ぐ設計では長い窓が不要になるのが5-3bの要点）。そのため lookback は実測値をそのまま
転記するのではなく、**各列の計算式を読んで**「inputs から何行必要か」を導出している。
唯一の例外は `sp_*`/`zb_*`（WINDOW 250。実測60〜120本＋余裕）— これらは状態機械の
内部状態が列に表れない情報を含むため、実測に余裕を持たせた値を採用している。

**実測・計算式のどちらからも確定できない列は `lookback=None`（未確定）のままにし、
note に理由を書く。** 5-3b 時点では全列で確定できている（下記 `_ENTRIES` 参照）。

## テストで固定する点（`test_incremental_state_registry.py`）

1. T3 の全列（`id`/`symbol_id`/`date` を除く）が本レジストリに登録済み（逆に本レジストリに
   あって T3 に無い列があっても検出する）
2. TWO_SIDED に分類された列が存在しない（5-2 の実測結果の固定）
3. WINDOW 型の lookback 最大値が **252 以下**（SQLite の504行に収まることの担保）
4. RECURSIVE 型は `prev_self=True`
5. `inputs` の列名がすべて有効（生の価格列 or Indicator 実列）

`calculate_indicators` の実装（`backend/indicators/calculate.py` 等）は本レジストリ作成時点では
**変更していない**。分類の正しさ自体（増分1歩 == 全期間再計算の最終行）は、計画書 5-5 の
等価性テストで別途検証する（誤分類があればそちらで検出される設計）。
"""
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import FrozenSet, Mapping, Optional, Tuple


class ColumnKind(Enum):
    """T3列1つの増分計算特性。"""

    RECURSIVE = auto()   # (a) 再帰
    WINDOW = auto()       # (b) 窓
    TWO_SIDED = auto()    # (c) 両側参照


# 生の価格列（daily_prices 由来。T3 の実列ではないが inputs に直接使ってよい）
RAW_PRICE_COLUMNS: FrozenSet[str] = frozenset({
    'open', 'high', 'low', 'close', 'volume', 'spy_close', 'spy_volume',
})


@dataclass(frozen=True)
class ColumnSpec:
    """T3列1つ分の増分計算特性の宣言。"""

    name: str
    kind: ColumnKind
    prev_self: bool = False                   # 自列の前日保存値を継ぐか（RECURSIVE は True 必須）
    inputs: Tuple[str, ...] = field(default_factory=tuple)  # 参照する列（生価格 or 保存済みT3列）
    lookback: Optional[int] = None             # inputs から何行必要か（当日のみなら1。未確定なら None）
    note: str = ''                             # 補足

    def __post_init__(self):
        if self.kind is ColumnKind.RECURSIVE:
            if not self.prev_self:
                raise ValueError(f'{self.name}: RECURSIVE 型は prev_self=True が必須です')
            if not self.inputs:
                raise ValueError(f'{self.name}: RECURSIVE 型にも当日分の inputs が必要です（継続だけでは足りない）')
        else:
            if self.prev_self:
                raise ValueError(f'{self.name}: {self.kind.name} 型に prev_self=True は使いません')
        if self.lookback is not None and self.lookback < 1:
            raise ValueError(f'{self.name}: lookback は1以上である必要があります（当日のみなら1）')


def _recursive(name: str, inputs: Tuple[str, ...], lookback: int, note: str = '') -> ColumnSpec:
    """RECURSIVE 型のショートハンド。前日の自列値（prev_self）に加え、当日分の inputs/lookback を持つ。"""
    return ColumnSpec(
        name=name, kind=ColumnKind.RECURSIVE, prev_self=True,
        inputs=inputs, lookback=lookback, note=note,
    )


def _window(name: str, inputs: Tuple[str, ...] = (), lookback: Optional[int] = None, note: str = '') -> ColumnSpec:
    """WINDOW 型のショートハンド。"""
    return ColumnSpec(name=name, kind=ColumnKind.WINDOW, inputs=inputs, lookback=lookback, note=note)


# ============================================================
# INDICATOR_COLUMN_REGISTRY — indicators テーブルの全列（id/symbol_id/date を除く）
# ============================================================
_ENTRIES: Tuple[ColumnSpec, ...] = (
    # --- Simple Moving Averages ---
    # close.rolling(window=N, min_periods=1).mean()。min_periods=1 は「NaN を返すかどうか」の
    # 閾値にすぎず、集計対象の窓自体は常に直近N本（履歴がN本以上ある本番銘柄では常にこれ）。
    _window('sma_5', inputs=('close',), lookback=5),
    _window('sma_21', inputs=('close',), lookback=21),
    _window('sma_50', inputs=('close',), lookback=50),
    _window('sma_63', inputs=('close',), lookback=63),
    _window('sma_150', inputs=('close',), lookback=150),
    _window('sma_200', inputs=('close',), lookback=200),

    # --- Exponential Moving Averages ---
    # calculate_ema_tv は前日の EMA 値から _ema_kernel で継ぐ再帰計算（moving_averages.py）。
    # 当日の close だけあれば1ステップ進められる。
    _recursive('ema_5', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),
    _recursive('ema_21', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),
    _recursive('ema_50', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),
    _recursive('ema_63', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),
    _recursive('ema_150', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),
    _recursive('ema_200', inputs=('close',), lookback=1, note='_ema_kernel の1ステップ'),

    # --- Volatility ---
    # _td9_kernel（volatility.py）は res[i-1]（前日の td9 自身）と close[i] vs close[i-4] の
    # 比較で決まる。close は当日〜4日前の5行が要る。
    _recursive('td9', inputs=('close',), lookback=5, note='close[i] と close[i-4] の比較（_td9_kernel）'),
    # ta.volatility.AverageTrueRange は Wilder の再帰平滑化
    # （atr[i] = (atr[i-1]*(window-1) + true_range[i]) / window）。TR[i] の算出に
    # 前日 close が要るため、当日〜前日の high/low/close で2行必要。
    _recursive('atr_14', inputs=('high', 'low', 'close'), lookback=2,
               note='Wilder再帰平滑化。TR[i]の算出にclose[i-1]が要るため2行（5-3bで再分類）'),
    # 同日の atr_14/close から決まる派生値（履歴不要）。
    _window('atr_pct_14', inputs=('atr_14', 'close'), lookback=1, note='atr_14/close*100 の当日値のみ'),
    # (high-low)/low を rolling(21) するだけの単純窓。
    _window('adr_pct_21', inputs=('high', 'low'), lookback=21, note='(high-low)/low の21日単純窓'),
    # close.pct_change() = close[i]/close[i-1]-1。2行必要。
    _window('change_1d_pct', inputs=('close',), lookback=2, note='close.pct_change()。close[i-1]まで2行'),
    # close.pct_change(5) = close[i]/close[i-5]-1。6行必要。
    _window('change_1w_pct', inputs=('close',), lookback=6, note='close.pct_change(5)。close[i-5]まで6行'),
    # close.pct_change(20) = close[i]/close[i-20]-1。21行必要。
    _window('change_1m_pct', inputs=('close',), lookback=21, note='close.pct_change(20)。close[i-20]まで21行'),
    # ((close/sma_50*100-100)/atr_pct_14) の当日値のみ。sma_50/atr_pct_14 は別途維持される。
    _window('sma50_atr_mult', inputs=('close', 'sma_50', 'atr_pct_14'), lookback=1,
            note='sma_50・atr_pct_14 は別途維持されるため当日値のみで決まる'),

    # --- Relative Strength (vs SPY) ---
    # close/spy_close の当日値のみで決まる（履歴不要）。
    _window('rs_value', inputs=('close', 'spy_close'), lookback=1, note='close/spy_close の当日値のみ'),

    # rs_trend_sN = rs_value_e5（RECURSIVE、当日値のみ要） / rolling(rs_value, N).mean()。
    # rolling窓の分だけ rs_value がN行要る。
    _window('rs_trend_s5', inputs=('rs_value_e5', 'rs_value'), lookback=5,
            note='rs_value_e5(当日値) / rolling(rs_value, 5).mean()'),
    _window('rs_trend_s14', inputs=('rs_value_e5', 'rs_value'), lookback=14,
            note='rs_value_e5(当日値) / rolling(rs_value, 14).mean()'),
    _window('rs_trend_s21', inputs=('rs_value_e5', 'rs_value'), lookback=21,
            note='rs_value_e5(当日値) / rolling(rs_value, 21).mean()'),
    _window('rs_trend_s63', inputs=('rs_value_e5', 'rs_value'), lookback=63,
            note='rs_value_e5(当日値) / rolling(rs_value, 63).mean()'),
    _window('rs_trend_s200', inputs=('rs_value_e5', 'rs_value'), lookback=200,
            note='rs_value_e5(当日値) / rolling(rs_value, 200).mean()。'
                 '旧版の実測値99は「rs_value_e5をゼロから再構築する場合」の値で、'
                 'rs_value_e5をRECURSIVE継続する5-3b設計では使わない'),

    # rs_value_eN: calculate_ema_tv(rs_value, N) によるEMA。RECURSIVE、当日の rs_value のみ要る。
    _recursive('rs_value_e5', inputs=('rs_value',), lookback=1, note='EMA(rs_value, 5) の1ステップ'),
    _recursive('rs_value_e14', inputs=('rs_value',), lookback=1, note='EMA(rs_value, 14) の1ステップ'),
    _recursive('rs_value_e21', inputs=('rs_value',), lookback=1, note='EMA(rs_value, 21) の1ステップ'),
    _recursive('rs_value_e63', inputs=('rs_value',), lookback=1, note='EMA(rs_value, 63) の1ステップ'),
    _recursive('rs_value_e200', inputs=('rs_value',), lookback=1, note='EMA(rs_value, 200) の1ステップ'),

    # rs_ratio_eN: rs_value_eN（RECURSIVE） の rolling(N) Z-score（独立計算のrolling std）。
    # rs_value_eN 自体はN行分の生値を要さず、rolling窓の分だけ rs_value_eN がN行要る。
    _window('rs_ratio_e5', inputs=('rs_value_e5',), lookback=5, note='rolling(rs_value_e5, 5) のZ-score'),
    _window('rs_ratio_e14', inputs=('rs_value_e14',), lookback=14, note='rolling(rs_value_e14, 14) のZ-score'),
    _window('rs_ratio_e21', inputs=('rs_value_e21',), lookback=21, note='rolling(rs_value_e21, 21) のZ-score'),
    _window('rs_ratio_e63', inputs=('rs_value_e63',), lookback=63, note='rolling(rs_value_e63, 63) のZ-score'),
    _window('rs_ratio_e200', inputs=('rs_value_e200',), lookback=200,
            note='rolling(rs_value_e200, 200) のZ-score。旧版の実測値298は'
                 'rs_value_e200をゼロから再構築する場合の値で5-3b設計では使わない'),

    # rs_roc_ema_eN: rs_ratio_eN（WINDOW） の14日ROCをEMA平滑化。RECURSIVE。
    # roc[i] = ratio_offset[i]/ratio_offset[i-14]*100 なので rs_ratio_eN は当日〜14日前の15行要る。
    _recursive('rs_roc_ema_5', inputs=('rs_ratio_e5',), lookback=15,
               note='roc = rs_ratio_e5[i]/rs_ratio_e5[i-14] を14日ROCしてEMA。15行'),
    _recursive('rs_roc_ema_14', inputs=('rs_ratio_e14',), lookback=15,
               note='roc = rs_ratio_e14[i]/rs_ratio_e14[i-14] を14日ROCしてEMA。15行'),
    _recursive('rs_roc_ema_21', inputs=('rs_ratio_e21',), lookback=15,
               note='roc = rs_ratio_e21[i]/rs_ratio_e21[i-14] を14日ROCしてEMA。15行'),
    _recursive('rs_roc_ema_63', inputs=('rs_ratio_e63',), lookback=15,
               note='roc = rs_ratio_e63[i]/rs_ratio_e63[i-14] を14日ROCしてEMA。15行'),
    _recursive('rs_roc_ema_200', inputs=('rs_ratio_e200',), lookback=15,
               note='roc = rs_ratio_e200[i]/rs_ratio_e200[i-14] を14日ROCしてEMA。15行。'
                    '旧版の実測値511は「rs_ratio_e200をゼロから再構築する場合」の値で、'
                    'rs_ratio_e200を継続する5-3b設計では使わない'),

    # rs_momentum_eN: rs_roc_ema_eN（RECURSIVE） の rolling(N) Z-score。WINDOW。
    _window('rs_momentum_e5', inputs=('rs_roc_ema_5',), lookback=5, note='rolling(rs_roc_ema_5, 5) のZ-score'),
    _window('rs_momentum_e14', inputs=('rs_roc_ema_14',), lookback=14, note='rolling(rs_roc_ema_14, 14) のZ-score'),
    _window('rs_momentum_e21', inputs=('rs_roc_ema_21',), lookback=21, note='rolling(rs_roc_ema_21, 21) のZ-score'),
    _window('rs_momentum_e63', inputs=('rs_roc_ema_63',), lookback=63, note='rolling(rs_roc_ema_63, 63) のZ-score'),
    _window('rs_momentum_e200', inputs=('rs_roc_ema_200',), lookback=200,
            note='rolling(rs_roc_ema_200, 200) のZ-score。旧版の実測値610は'
                 'rs_roc_ema_200をゼロから再構築する場合の値で5-3b設計では使わない'),

    # --- RS-MACD(5, 21, 5) ---
    # rs_macd_line_21 = rs_value_e5 - rs_value_e21（どちらもRECURSIVEとして別途継続されている
    # 列の当日値どうしの差分）。追加の生値遡りは不要。
    _window('rs_macd_line_21', inputs=('rs_value_e5', 'rs_value_e21'), lookback=1,
            note='rs_value_e5 - rs_value_e21 の当日値差分（両者は別途RECURSIVEで継続）'),
    # rs_macd_signal_21 = EMA(rs_macd_line_21, 5)。RECURSIVE。
    _recursive('rs_macd_signal_21', inputs=('rs_macd_line_21',), lookback=1,
               note='EMA(rs_macd_line_21, 5) の1ステップ'),
    # rs_macd_hist_21 = rs_macd_line_21 - rs_macd_signal_21（同上、追加の生値遡り不要）。
    _window('rs_macd_hist_21', inputs=('rs_macd_line_21', 'rs_macd_signal_21'), lookback=1,
            note='rs_macd_line_21 - rs_macd_signal_21 の当日値差分'),

    # --- Volume ---
    _window('vol_surge_21', inputs=('volume',), lookback=21, note='volume.rolling(21) の単純窓'),
    _window('vol_surge_rel_spy_21', inputs=('vol_surge_21', 'spy_volume'), lookback=21,
            note='vol_surge_21(当日値) / SPY側vol_surge。spy_vol_sma_21がspy_volumeの21日窓を要る'),
    # close.diff() を経由するため、up_vol の rolling(50) には close が51行（i-50まで）要る。
    _window('up_down_vol_ratio_50', inputs=('close', 'volume'), lookback=51,
            note='close.diff()経由のrolling(50)。close.diff()[i-49]がclose[i-50]を要るため51行'),
    # is_accum の rolling(5) の各日が vol_sma_21（21行窓）を要るため、最古で i-24 まで25行。
    _window('vol_accum_days_5', inputs=('close', 'volume'), lookback=25,
            note='rolling(5)の各日がvol_sma_21(21日窓)を要るため i-24 まで25行'),
    _window('avg_dollar_volume_21', inputs=('close', 'volume'), lookback=21,
            note='(close*volume).rolling(21) の単純窓'),

    # --- Price Range from Highs ---
    _window('dist_63d_high_pct', inputs=('high', 'close'), lookback=63, note='high.rolling(63).max() の単純窓'),
    _window('dist_52w_high_pct', inputs=('high', 'close'), lookback=252, note='high.rolling(252).max() の単純窓'),

    # --- RS Leading Signals ---
    # cur_b/cur_r は prev_b/prev_r（自分自身の前日値）を継ぐ（_rs_dot_age_kernel）。
    # ただし当日の blue_lit/red_lit フラグ自体は rs_252_high/low・close_252_high/low
    # （どちらも rolling(252, min_periods=1)）で決まるため、rs_value・close を252行要る
    # （実装確認済み: relative_strength.py の rs_252_high/rs_252_low/blue_lit/red_lit）。
    _recursive('rs_blue_dot_age', inputs=('rs_value', 'close'), lookback=252,
               note='当日フラグ(blue_lit)の算出にrolling(252)のrs_value・closeが要る（実装確認済み）'),
    _recursive('rs_red_dot_age', inputs=('rs_value', 'close'), lookback=252,
               note='当日フラグ(red_lit)の算出にrolling(252)のrs_value・closeが要る（実装確認済み）'),

    # --- Volatility Contraction ---
    # Simple ATR(10)/Simple ATR(50)。close.shift(1) を経由するため close は51行
    # （atr_50の最古項が close[i-50] を要る）、high/lowは50行で足りる。
    _window('vcr', inputs=('high', 'low', 'close'), lookback=51,
            note='atr_50=tr_vals.rolling(50).mean()。tr_valsがclose.shift(1)を要るため51行'),

    # --- Trend Quality ---
    # sma_50/150/200（当日値のみ、別途維持） + sma_200.shift(20)（21行） +
    # max_252d=high.rolling(252).max()（252行）の合成。
    _window('is_trend_template', inputs=('close', 'sma_50', 'sma_150', 'sma_200', 'high'), lookback=252,
            note='sma_200.shift(20)は21行、max_252dは252行。最大の252行が支配的'),

    # --- Structure Pivot (LL-HL) ---
    # structure_pivot.py の _scan_for_length は curr_price/curr_idx/prev_idx/is_setup/
    # break_val/break_idx を全履歴にわたって引き継ぐ状態機械。5-2 の実測で「未来のバーで
    # 値が変わる」(c)ではないことは確認済み。さらに5-3bの前方切り詰め実測（8銘柄・
    # rtol=1e-9）で、直近60本の生価格（high/low/close）があれば全履歴と一致することを
    # 確認したため、状態機械であっても短い生価格窓から再構築できる＝WINDOW に分類する。
    # lookback は実測60本に余裕を持たせて250とする。
    _window('sp_pivot', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
    _window('sp_hl', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
    _window('sp_counter', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),

    # --- Direction via Zone Break ---
    # zone_break.py の _zone_break_scan は sp_pivot 以上に多くの内部状態
    # （ssl_bl/bsl_bl/int_ssl_bl/int_bsl_br/is_conf_bl/is_break_bl/is_weak_of に加え、
    # 全履歴のフラクタル・FVGリスト）を持つ状態機械。5-2 の実測で(c)ではないと確認済み。
    # 5-3bの前方切り詰め実測で60〜120本の生価格で全履歴と一致することを確認したため、
    # sp_* と同じくWINDOWに分類する（余裕を持たせ250）。
    _window('is_zone_break_bull', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
    _window('zb_ssl', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
    _window('zb_bsl', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 60本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
    _window('is_zone_break_weak', inputs=('high', 'low', 'close'), lookback=250,
            note='5-3b実測: 120本の生価格で全履歴と一致（前方切り詰め）。余裕を持たせ250'),
)

INDICATOR_COLUMN_REGISTRY: Mapping[str, ColumnSpec] = {spec.name: spec for spec in _ENTRIES}

if len(INDICATOR_COLUMN_REGISTRY) != len(_ENTRIES):
    # 同名の重複登録（コピペミス）を確実に検出する。
    _names = [spec.name for spec in _ENTRIES]
    _dupes = sorted({n for n in _names if _names.count(n) > 1})
    raise AssertionError(f'INDICATOR_COLUMN_REGISTRY に重複登録があります: {_dupes}')
