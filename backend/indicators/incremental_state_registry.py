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
6. `warmup_bars` が設定されている列は0以上の整数（5-6c）

`calculate_indicators` の実装（`backend/indicators/calculate.py` 等）は本レジストリ作成時点では
**変更していない**。分類の正しさ自体（増分1歩 == 全期間再計算の最終行）は、計画書 5-5 の
等価性テストで別途検証する（誤分類があればそちらで検出される設計）。

## warmup_bars（5-6c・ユーザー提案）

`lookback`（増分計算が inputs から必要とする行数）とは**別の軸**。ユーザー提案の新しい
健全性チェック向けの属性で、意味は「**演算上必要な日数がある銘柄なら、その列は NULL に
ならない**」という一般則を検査可能にすること。

`rs_roc_ema_200`/`rs_momentum_e200` が全銘柄で NULL になった今回の不具合（§1.1）は、
この検査があれば初日に検出できた。`--check-recursive-state`（5-6b）は「増分計算の**状態**が
壊れていないか」を見るのに対し、本属性を使う `--check-warmup-nulls`
（`tools/db_health_check.py`）は「**指標そのものが出るべき値を出しているか**」を見る、
目的の異なる検査。

- **意味**: 銘柄の価格履歴の**先頭から数えて何本目（0始まり）でこの列が非NULLになるか**。
  T3 の保存行数（`t3_count`）がこの値を**超えていれば**、最新行は非NULLであるべき
  （`t3_count > warmup_bars` ⟹ 最新行が非NULL）。
- **実測（2026-09-22・初版）**: 本番 Parquet・`category='個別'`・active・2,400本以上の履歴を
  持つ120銘柄で、前方から1本ずつ価格を足しながら「この列が初めて非NULLになった本数」を測定。
  67列中64列は**中央値＝最小値＝最頻値**の構造的な定数だった（銘柄によらず一定）。
- **再実測（2026-09-24・5-11b。`min_periods_warmup_plan.md`）**: ①（`min_periods=1`→
  `min_periods=window`）とA-full（RS系4流儀目の統一）の適用後、sandboxで全期間再計算した
  実データに対し、**個別・active銘柄を履歴本数で層化抽出**（12ビン×最大30銘柄=355銘柄。
  初版が長期履歴銘柄（2,400本以上）だけに限っていたため短期履歴（IPO）銘柄特有の挙動を
  見落としていた反省を踏まえ、10本台の極短履歴も含めた）して再測定した。
  フル履歴を1回計算した結果から「各列が最初に非NULLになった0-indexed位置」を直接読む方式
  （前方切り詰め＋逐次再計算と数学的に等価。全指標はcausalなため）。①・A-fullが影響する
  列はいずれも中央値＝最小値＝最頻値のまま（変わらず構造的定数）だったが、値そのものは
  多くの列で大きく変化した（詳細は各エントリの note を参照）。
- **`vol_surge_21`/`vol_surge_rel_spy_21`/`up_down_vol_ratio_50` は中央値ではなく最大値を採用**
  ——中央値=最小値=最頻値が構造的定数になる他列と異なり、この3列は分位によりばらつくため、
  安全側（過検出しない側）に寄せた（`up_down_vol_ratio_50`が変動することは初版の
  サンプル（長期履歴銘柄限定）では検出できず、2026-09-24の層化サンプルで新たに判明した）。
- **例外（`warmup_bars=None`）**: `sp_pivot`/`sp_hl`/`sp_counter` の3列。これらは
  **イベント駆動**（構造がいつ最初に現れるかが銘柄の値動き次第）で、実測でも
  ばらついたため、「履歴が何本あれば非NULLになる」という閾値を置けない。
  `--check-warmup-nulls` の対象から機械的に除外される
  （`columns_with_warmup_threshold()` が `warmup_bars is not None` の列だけを返す）。
  `sp_pivot`/`sp_counter` は仕様上排他（同じ行でどちらか一方が必ずNULL）だが、
  「少なくとも一方が非NULL」という対の規則は実データでの偽陽性ゼロを確認できて
  いないため導入していない（単純に両方とも例外扱い）。
- **2026-09-23時点で「未収束」としていた3項目の解消状況**（2026-09-24再実測時点）:
  1. **構造的にNULL（SPYのrs_*・^VIX/^VIX3M/SPYの出来高由来列）** — 今回の再測定は
     `category='個別'` のみを対象にしており、これらは元々サンプル対象外（解消済み）。
     `is_structurally_null_column()` による除外は`--check-recursive-state`/
     `--check-warmup-nulls`/`_calculate_t3_worker`の3箇所で引き続き共有される。
  2. **atr_14/atr_pct_14の閾値誤り（0のままだと新規上場7銘柄でNULLだった件）** —
     5-4b（`min_periods_warmup_plan.md`）で `_atr_wilder_kernel` の非増分ブランチの
     先頭window-1本を明示的にNaN化する修正が入ったことで解消。今回の再測定でも
     `atr_14`/`atr_pct_14`はどちらも一貫して13（構造的定数）だった。
  3. **rs_roc_ema_200がNULLの40銘柄（本物の異常）** — 本計画のスコープ外。
     `doc/issue_list.md`に起票済みの別issueとして残る（sandbox全期間再計算後に
     再発しているか要再調査。本計画の5-11の`--check-nulls`/`--check-warmup-nulls`
     実行結果を参照）。
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

# ホットキャッシュ（stocktool.db）が保持する営業日数（実測）。730暦日に含まれる
# 営業日数は祝日配置で前後する（実測504、年により500〜505程度）。
HOT_WINDOW_BARS = 504

# zb_ssl / zb_bsl / is_zone_break_bull / is_zone_break_weak の lookback（5-4e）。
#
# 5-4c では暫定的に `HOT_WINDOW_BARS`（504）をそのまま lookback に設定していたが、
# これは SQLite の保持行数そのものであり、`max_lookback() + 1 = 505` が保持行数を
# 1行超える＝**マージンがゼロ**だった（保持行数が実測で500〜505の間で揺れる年には
# 壊れる）。5-4e でユーザー判断により見直した。
#
# **400 を選んだ根拠**（300銘柄実測・2026-09-22。測定した lookback は離散点
# 120/250/400/600/900/1300/1800/2400 のみ）:
#   - `is_zone_break_bull`: 最大 250
#   - `is_zone_break_weak`: 最大 400
#   - `zb_ssl`: p99=250、2/300 銘柄が 2400（400〜2400 の間の測定点では収束せず）
#   - `zb_bsl`: p99=251、3/300 銘柄が 2400（同上）
# **400 と 600〜2400 の間で精度は変わらない**——400 で収束しなかった銘柄
# （2〜3/300）は 2400 まで拡張しても収束しなかった（測定点の間に該当銘柄が無い）。
# よって 400 より大きくしても正しさは向上せず、読み出し量が増えるだけ。
#
# **マージンの確保**: `ZONE_BREAK_LOOKBACK + 1`（=401）は `HOT_WINDOW_BARS`（504）に
# 対して `_ZONE_BREAK_MARGIN_BARS` 行の余裕を持つ。730暦日に含まれる営業日数が
# 年によって500〜505程度で揺れることへの安全マージンとして必要。
_ZONE_BREAK_MARGIN_BARS = 104
ZONE_BREAK_LOOKBACK = HOT_WINDOW_BARS - _ZONE_BREAK_MARGIN_BARS  # = 400


@dataclass(frozen=True)
class ColumnSpec:
    """T3列1つ分の増分計算特性の宣言。"""

    name: str
    kind: ColumnKind
    prev_self: bool = False                   # 自列の前日保存値を継ぐか（RECURSIVE は True 必須）
    inputs: Tuple[str, ...] = field(default_factory=tuple)  # 参照する列（生価格 or 保存済みT3列）
    lookback: Optional[int] = None             # inputs から何行必要か（当日のみなら1。未確定なら None）
    warmup_bars: Optional[int] = None          # 銘柄の先頭から何本目(0始まり)で非NULLになるか（5-6c。lookbackとは別軸）
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
        if self.warmup_bars is not None and self.warmup_bars < 0:
            raise ValueError(f'{self.name}: warmup_bars は0以上である必要があります')


def _recursive(name: str, inputs: Tuple[str, ...], lookback: int, warmup_bars: Optional[int] = None,
                note: str = '') -> ColumnSpec:
    """RECURSIVE 型のショートハンド。前日の自列値（prev_self）に加え、当日分の inputs/lookback を持つ。"""
    return ColumnSpec(
        name=name, kind=ColumnKind.RECURSIVE, prev_self=True,
        inputs=inputs, lookback=lookback, warmup_bars=warmup_bars, note=note,
    )


def _window(name: str, inputs: Tuple[str, ...] = (), lookback: Optional[int] = None,
            warmup_bars: Optional[int] = None, note: str = '') -> ColumnSpec:
    """WINDOW 型のショートハンド。"""
    return ColumnSpec(name=name, kind=ColumnKind.WINDOW, inputs=inputs, lookback=lookback,
                       warmup_bars=warmup_bars, note=note)


# ============================================================
# INDICATOR_COLUMN_REGISTRY — indicators テーブルの全列（id/symbol_id/date を除く）
# ============================================================
_ENTRIES: Tuple[ColumnSpec, ...] = (
    # --- Simple Moving Averages ---
    # close.rolling(window=N, min_periods=1).mean()。min_periods=1 は「NaN を返すかどうか」の
    # 閾値にすぎず、集計対象の窓自体は常に直近N本（履歴がN本以上ある本番銘柄では常にこれ）。
    _window('sma_5', inputs=('close',), lookback=5, warmup_bars=4),
    _window('sma_21', inputs=('close',), lookback=21, warmup_bars=20),
    _window('sma_50', inputs=('close',), lookback=50, warmup_bars=49),
    _window('sma_63', inputs=('close',), lookback=63, warmup_bars=62),
    _window('sma_150', inputs=('close',), lookback=150, warmup_bars=149),
    _window('sma_200', inputs=('close',), lookback=200, warmup_bars=199),

    # --- Exponential Moving Averages ---
    # calculate_ema_tv は前日の EMA 値から _ema_kernel で継ぐ再帰計算（moving_averages.py）。
    # 当日の close だけあれば1ステップ進められる。
    _recursive('ema_5', inputs=('close',), lookback=1, warmup_bars=4, note='_ema_kernel の1ステップ'),
    _recursive('ema_21', inputs=('close',), lookback=1, warmup_bars=20, note='_ema_kernel の1ステップ'),
    _recursive('ema_50', inputs=('close',), lookback=1, warmup_bars=49, note='_ema_kernel の1ステップ'),
    _recursive('ema_63', inputs=('close',), lookback=1, warmup_bars=62, note='_ema_kernel の1ステップ'),
    _recursive('ema_150', inputs=('close',), lookback=1, warmup_bars=149, note='_ema_kernel の1ステップ'),
    _recursive('ema_200', inputs=('close',), lookback=1, warmup_bars=199, note='_ema_kernel の1ステップ'),

    # --- Volatility ---
    # _td9_kernel（volatility.py）は res[i-1]（前日の td9 自身）と close[i] vs close[i-4] の
    # 比較で決まる。close は当日〜4日前の5行が要る。
    _recursive('td9', inputs=('close',), lookback=5, warmup_bars=0,
               note='close[i] と close[i-4] の比較（_td9_kernel）'),
    # ta.volatility.AverageTrueRange は Wilder の再帰平滑化
    # （atr[i] = (atr[i-1]*(window-1) + true_range[i]) / window）。TR[i] の算出に
    # 前日 close が要るため、当日〜前日の high/low/close で2行必要。
    _recursive('atr_14', inputs=('high', 'low', 'close'), lookback=2, warmup_bars=13,
               note='Wilder再帰平滑化。TR[i]の算出にclose[i-1]が要るため2行（5-3bで再分類）。'
                    'warmup_barsは5-4bで先頭window-1本を明示NaN化したことに伴い0→13へ改訂'),
    # 同日の atr_14/close から決まる派生値（履歴不要）。
    _window('atr_pct_14', inputs=('atr_14', 'close'), lookback=1, warmup_bars=13,
            note='atr_14/close*100 の当日値のみ。warmup_barsはatr_14に連動し0→13へ改訂'),
    # (high-low)/low を rolling(21) するだけの単純窓。
    _window('adr_pct_21', inputs=('high', 'low'), lookback=21, warmup_bars=20, note='(high-low)/low の21日単純窓'),
    # close.pct_change() = close[i]/close[i-1]-1。2行必要。
    _window('change_1d_pct', inputs=('close',), lookback=2, warmup_bars=1,
            note='close.pct_change()。close[i-1]まで2行'),
    # close.pct_change(5) = close[i]/close[i-5]-1。6行必要。
    _window('change_1w_pct', inputs=('close',), lookback=6, warmup_bars=5,
            note='close.pct_change(5)。close[i-5]まで6行'),
    # close.pct_change(20) = close[i]/close[i-20]-1。21行必要。
    _window('change_1m_pct', inputs=('close',), lookback=21, warmup_bars=20,
            note='close.pct_change(20)。close[i-20]まで21行'),
    # ((close/sma_50*100-100)/atr_pct_14) の当日値のみ。sma_50/atr_pct_14 は別途維持される。
    _window('sma50_atr_mult', inputs=('close', 'sma_50', 'atr_pct_14'), lookback=1, warmup_bars=49,
            note='sma_50・atr_pct_14 は別途維持されるため当日値のみで決まる。'
                 'warmup_barsはsma_50側(49)が支配的で13→49へ改訂'),

    # --- Relative Strength (vs SPY) ---
    # close/spy_close の当日値のみで決まる（履歴不要）。
    _window('rs_value', inputs=('close', 'spy_close'), lookback=1, warmup_bars=0,
            note='close/spy_close の当日値のみ'),

    # rs_trend_sN = rs_value_e5（RECURSIVE、当日値のみ要） / rolling(rs_value, N).mean()。
    # rolling窓の分だけ rs_value がN行要る。
    _window('rs_trend_s5', inputs=('rs_value_e5', 'rs_value'), lookback=5, warmup_bars=4,
            note='rs_value_e5(当日値) / rolling(rs_value, 5).mean()'),
    _window('rs_trend_s14', inputs=('rs_value_e5', 'rs_value'), lookback=14, warmup_bars=13,
            note='rs_value_e5(当日値) / rolling(rs_value, 14).mean()。'
                 'A-full(min_periods=n統一・5-9c)でrs_smaの窓がn//2からnへ延びたため6→13へ改訂'),
    _window('rs_trend_s21', inputs=('rs_value_e5', 'rs_value'), lookback=21, warmup_bars=20,
            note='rs_value_e5(当日値) / rolling(rs_value, 21).mean()。A-fullで9→20へ改訂'),
    _window('rs_trend_s63', inputs=('rs_value_e5', 'rs_value'), lookback=63, warmup_bars=62,
            note='rs_value_e5(当日値) / rolling(rs_value, 63).mean()。A-fullで30→62へ改訂'),
    _window('rs_trend_s200', inputs=('rs_value_e5', 'rs_value'), lookback=200, warmup_bars=199,
            note='rs_value_e5(当日値) / rolling(rs_value, 200).mean()。'
                 '旧版の実測値99は「rs_value_e5をゼロから再構築する場合」かつA-full前(min_periods=n//2)の値。'
                 'A-full(5-9c)でmin_periods=nに統一したことで199へ改訂（本番sandbox実測・2026-09-24）'),

    # rs_value_eN: calculate_ema_tv(rs_value, N) によるEMA。RECURSIVE、当日の rs_value のみ要る。
    _recursive('rs_value_e5', inputs=('rs_value',), lookback=1, warmup_bars=4, note='EMA(rs_value, 5) の1ステップ'),
    _recursive('rs_value_e14', inputs=('rs_value',), lookback=1, warmup_bars=13, note='EMA(rs_value, 14) の1ステップ'),
    _recursive('rs_value_e21', inputs=('rs_value',), lookback=1, warmup_bars=20, note='EMA(rs_value, 21) の1ステップ'),
    _recursive('rs_value_e63', inputs=('rs_value',), lookback=1, warmup_bars=62, note='EMA(rs_value, 63) の1ステップ'),
    _recursive('rs_value_e200', inputs=('rs_value',), lookback=1, warmup_bars=199, note='EMA(rs_value, 200) の1ステップ'),

    # rs_ratio_eN: rs_value_eN（RECURSIVE） の rolling(N) Z-score（独立計算のrolling std）。
    # rs_value_eN 自体はN行分の生値を要さず、rolling窓の分だけ rs_value_eN がN行要る。
    _window('rs_ratio_e5', inputs=('rs_value_e5',), lookback=5, warmup_bars=8,
            note='rolling(rs_value_e5, 5) のZ-score。A-fullでrs_value_e5warmup(4)+5-1=8へ改訂'),
    _window('rs_ratio_e14', inputs=('rs_value_e14',), lookback=14, warmup_bars=26,
            note='rolling(rs_value_e14, 14) のZ-score。A-fullで19→26へ改訂'),
    _window('rs_ratio_e21', inputs=('rs_value_e21',), lookback=21, warmup_bars=40,
            note='rolling(rs_value_e21, 21) のZ-score。A-fullで29→40へ改訂'),
    _window('rs_ratio_e63', inputs=('rs_value_e63',), lookback=63, warmup_bars=124,
            note='rolling(rs_value_e63, 63) のZ-score。A-fullで92→124へ改訂'),
    _window('rs_ratio_e200', inputs=('rs_value_e200',), lookback=200, warmup_bars=398,
            note='rolling(rs_value_e200, 200) のZ-score。旧版の実測値298はA-full前'
                 '（min_periods=n//2）の値。A-full(5-9c)で398へ改訂（本番sandbox実測・2026-09-24）'),

    # rs_roc_ema_eN: rs_ratio_eN（WINDOW） の14日ROCをEMA平滑化。RECURSIVE。
    # roc[i] = ratio_offset[i]/ratio_offset[i-14]*100 なので rs_ratio_eN は当日〜14日前の15行要る。
    _recursive('rs_roc_ema_5', inputs=('rs_ratio_e5',), lookback=15, warmup_bars=26,
               note='roc = rs_ratio_e5[i]/rs_ratio_e5[i-14] を14日ROCしてEMA。15行。A-fullで23→26へ改訂'),
    _recursive('rs_roc_ema_14', inputs=('rs_ratio_e14',), lookback=15, warmup_bars=53,
               note='roc = rs_ratio_e14[i]/rs_ratio_e14[i-14] を14日ROCしてEMA。15行。A-fullで46→53へ改訂'),
    _recursive('rs_roc_ema_21', inputs=('rs_ratio_e21',), lookback=15, warmup_bars=74,
               note='roc = rs_ratio_e21[i]/rs_ratio_e21[i-14] を14日ROCしてEMA。15行。A-fullで63→74へ改訂'),
    _recursive('rs_roc_ema_63', inputs=('rs_ratio_e63',), lookback=15, warmup_bars=200,
               note='roc = rs_ratio_e63[i]/rs_ratio_e63[i-14] を14日ROCしてEMA。15行。A-fullで168→200へ改訂'),
    _recursive('rs_roc_ema_200', inputs=('rs_ratio_e200',), lookback=15, warmup_bars=611,
               note='roc = rs_ratio_e200[i]/rs_ratio_e200[i-14] を14日ROCしてEMA。15行。'
                    '旧版の実測値511はA-full前（min_periods=n//2）の値。'
                    'A-full(5-9c)で611へ改訂（本番sandbox実測・2026-09-24）。'
                    'max_lookback()=400を超えるため引き続きcolumns_with_undeterminable_warmup()の対象'),

    # rs_momentum_eN: rs_roc_ema_eN（RECURSIVE） の rolling(N) Z-score。WINDOW。
    _window('rs_momentum_e5', inputs=('rs_roc_ema_5',), lookback=5, warmup_bars=30,
            note='rolling(rs_roc_ema_5, 5) のZ-score。A-fullで24→30へ改訂'),
    _window('rs_momentum_e14', inputs=('rs_roc_ema_14',), lookback=14, warmup_bars=66,
            note='rolling(rs_roc_ema_14, 14) のZ-score。A-fullで52→66へ改訂'),
    _window('rs_momentum_e21', inputs=('rs_roc_ema_21',), lookback=21, warmup_bars=94,
            note='rolling(rs_roc_ema_21, 21) のZ-score。A-fullで72→94へ改訂'),
    _window('rs_momentum_e63', inputs=('rs_roc_ema_63',), lookback=63, warmup_bars=262,
            note='rolling(rs_roc_ema_63, 63) のZ-score。A-fullで198→262へ改訂'),
    _window('rs_momentum_e200', inputs=('rs_roc_ema_200',), lookback=200, warmup_bars=810,
            note='rolling(rs_roc_ema_200, 200) のZ-score。旧版の実測値610はA-full前'
                 '（min_periods=n//2）の値。A-full(5-9c)で810へ改訂（本番sandbox実測・2026-09-24）。'
                 'WINDOW型のためcolumns_with_undeterminable_warmup()の対象外'
                 '（同関数はRECURSIVE型のみを対象とするため、この値がmax_lookback()を'
                 '超えてもワーカー側の判別不能分類には影響しない）'),

    # --- RS-MACD(5, 21, 5) ---
    # rs_macd_line_21 = rs_value_e5 - rs_value_e21（どちらもRECURSIVEとして別途継続されている
    # 列の当日値どうしの差分）。追加の生値遡りは不要。
    _window('rs_macd_line_21', inputs=('rs_value_e5', 'rs_value_e21'), lookback=1, warmup_bars=20,
            note='rs_value_e5 - rs_value_e21 の当日値差分（両者は別途RECURSIVEで継続）'),
    # rs_macd_signal_21 = EMA(rs_macd_line_21, 5)。RECURSIVE。
    _recursive('rs_macd_signal_21', inputs=('rs_macd_line_21',), lookback=1, warmup_bars=24,
               note='EMA(rs_macd_line_21, 5) の1ステップ'),
    # rs_macd_hist_21 = rs_macd_line_21 - rs_macd_signal_21（同上、追加の生値遡り不要）。
    _window('rs_macd_hist_21', inputs=('rs_macd_line_21', 'rs_macd_signal_21'), lookback=1, warmup_bars=24,
            note='rs_macd_line_21 - rs_macd_signal_21 の当日値差分'),

    # --- Volume ---
    # vol_surge_21/vol_surge_rel_spy_21 の warmup_bars は実測の中央値ではなく最大値を採用。
    # 中央値=最小値=最頻値が構造的定数になる他列と異なり、この2列は分位によりばらつくため、
    # 安全側（過検出しない側）に寄せて最大値を採用している（5-6c以来の方針）。
    # 2026-09-24（5-11b・本番sandbox実測）: 5-5でvol_sma_21がmin_periods=1→21へ改訂された
    # ことに伴い、中央値20・最大73へ変化（旧13は改訂前の実測値）。
    _window('vol_surge_21', inputs=('volume',), lookback=21, warmup_bars=73,
            note='volume.rolling(21) の単純窓。5-5のmin_periods=window化に伴い13→73へ改訂（最大値採用）'),
    _window('vol_surge_rel_spy_21', inputs=('vol_surge_21', 'spy_volume'), lookback=21, warmup_bars=73,
            note='vol_surge_21(当日値) / SPY側vol_surge。spy_vol_sma_21がspy_volumeの21日窓を要る。'
                 '13→73へ改訂（最大値採用）'),
    # close.diff() を経由するため、up_vol の rolling(50) には close が51行（i-50まで）要る。
    # 2026-09-24: 本番sandbox実測で中央値49・最大76とばらつくことが判明（旧版は120本
    # 以上の長期履歴銘柄のみのサンプルで偶然49に固定されていた。短期履歴銘柄を含む
    # 層化サンプルで初めて検出）。vol_surge_21と同じ安全側（最大値）の方針に揃える。
    _window('up_down_vol_ratio_50', inputs=('close', 'volume'), lookback=51, warmup_bars=76,
            note='close.diff()経由のrolling(50)。close.diff()[i-49]がclose[i-50]を要るため51行。'
                 '49→76へ改訂（最大値採用。5-11bで短期履歴銘柄を含めた再測定により判明）'),
    # is_accum の rolling(5) の各日が vol_sma_21（21行窓）を要るため、最古で i-24 まで25行。
    _window('vol_accum_days_5', inputs=('close', 'volume'), lookback=25, warmup_bars=24,
            note='rolling(5)の各日がvol_sma_21(21日窓)を要るため i-24 まで25行。'
                 '5-5の再設計(is_accumのNaN伝播化)に伴い0→24へ改訂'),
    _window('avg_dollar_volume_21', inputs=('close', 'volume'), lookback=21, warmup_bars=20,
            note='(close*volume).rolling(21) の単純窓。5-5のmin_periods=window化で0→20へ改訂'),

    # --- Price Range from Highs ---
    _window('dist_63d_high_pct', inputs=('high', 'close'), lookback=63, warmup_bars=62,
            note='high.rolling(63).max() の単純窓。5-5のmin_periods=window化で0→62へ改訂'),
    _window('dist_52w_high_pct', inputs=('high', 'close'), lookback=252, warmup_bars=251,
            note='high.rolling(252).max() の単純窓。5-5のmin_periods=window化で0→251へ改訂'),

    # --- RS Leading Signals ---
    # cur_b/cur_r は prev_b/prev_r（自分自身の前日値）を継ぐ（_rs_dot_age_kernel）。
    # ただし当日の blue_lit/red_lit フラグ自体は rs_252_high/low・close_252_high/low
    # （どちらも rolling(252, min_periods=1)）で決まるため、rs_value・close を252行要る
    # （実装確認済み: relative_strength.py の rs_252_high/rs_252_low/blue_lit/red_lit）。
    _recursive('rs_blue_dot_age', inputs=('rs_value', 'close'), lookback=252, warmup_bars=0,
               note='当日フラグ(blue_lit)の算出にrolling(252)のrs_value・closeが要る（実装確認済み）'),
    _recursive('rs_red_dot_age', inputs=('rs_value', 'close'), lookback=252, warmup_bars=0,
               note='当日フラグ(red_lit)の算出にrolling(252)のrs_value・closeが要る（実装確認済み）'),

    # --- Volatility Contraction ---
    # Simple ATR(10)/Simple ATR(50)。close.shift(1) を経由するため close は51行
    # （atr_50の最古項が close[i-50] を要る）、high/lowは50行で足りる。
    _window('vcr', inputs=('high', 'low', 'close'), lookback=51, warmup_bars=49,
            note='atr_50=tr_vals.rolling(50).mean()。tr_valsがclose.shift(1)を要るため51行'),

    # --- Trend Quality ---
    # sma_50/150/200（当日値のみ、別途維持） + sma_200.shift(20)（21行） +
    # max_252d=high.rolling(252).max()（252行）の合成。
    _window('is_trend_template', inputs=('close', 'sma_50', 'sma_150', 'sma_200', 'high'), lookback=252,
            warmup_bars=251, note='sma_200.shift(20)は21行、max_252dは252行。最大の252行が支配的。'
                 '2026-09-24: 5-5bでcond1〜5全入力のNULLガードに拡張したことにより、'
                 'max_252d(warmup_bars=251)が実効的な支配要因になり20→251へ改訂'
                 '（本番sandbox実測）'),

    # --- Structure Pivot (LL-HL) ---
    # structure_pivot.py の _scan_for_length は curr_price/curr_idx/prev_idx/is_setup/
    # break_val/break_idx を全履歴にわたって引き継ぐ状態機械。5-2 の実測で「未来のバーで
    # 値が変わる」(c)ではないことは確認済み。さらに5-3bの前方切り詰め実測（8銘柄・
    # rtol=1e-9）で、直近60本の生価格（high/low/close）があれば全履歴と一致することを
    # 確認したため、状態機械であっても短い生価格窓から再構築できる＝WINDOW に分類する。
    # 5-4c で 300銘柄に実測を拡大しても中央値/p90/p99/最大が全て120本で揃い、
    # 250本超は0/300（sp_* は zb_* と異なり実際に有界）。lookback は実測120本に
    # 余裕を持たせて250のまま維持する（下げる積極的な理由が無いため）。
    # warmup_bars は意図的に None（5-6c）。sp_pivot/sp_hl/sp_counter は構造が最初に
    # 現れる時期が銘柄の値動き次第の**イベント駆動**（LL-HLパターンが形成されて初めて
    # 値が付く）であり、「履歴が何本あれば必ず非NULLになる」という閾値を置けない
    # （実測で最小9〜最大337本とばらついた。5-6c検証: tmp/verify_warmup_thresholds.py）。
    # なお sp_pivot と sp_counter は仕様上排他（同じ行でどちらか一方が必ずNULL）だが、
    # 「少なくとも一方が非NULL」という対の規則は実データでの偽陽性ゼロを確認できて
    # いないため導入せず、単純に両方とも例外扱いにしている。
    _window('sp_pivot', inputs=('high', 'low', 'close'), lookback=250, warmup_bars=None,
            note='5-4c実測(300銘柄): 中央値/p90/p99/最大とも120本で全履歴と一致（前方切り詰め）。'
                 '250本超0/300で有界と確認済み。余裕を持たせ250。'
                 'warmup_bars はイベント駆動のため None（5-6c）'),
    _window('sp_hl', inputs=('high', 'low', 'close'), lookback=250, warmup_bars=None,
            note='5-4c実測(300銘柄): 中央値/p90/p99/最大とも120本で全履歴と一致（前方切り詰め）。'
                 '250本超0/300で有界と確認済み。余裕を持たせ250。'
                 'warmup_bars はイベント駆動のため None（5-6c）'),
    _window('sp_counter', inputs=('high', 'low', 'close'), lookback=250, warmup_bars=None,
            note='5-4c実測(300銘柄): 中央値/p90/p99/最大とも120本で全履歴と一致（前方切り詰め）。'
                 '250本超0/300で有界と確認済み。余裕を持たせ250。'
                 'warmup_bars はイベント駆動のため None（5-6c）'),

    # --- Direction via Zone Break ---
    # zone_break.py の _zone_break_scan は sp_pivot 以上に多くの内部状態
    # （ssl_bl/bsl_bl/int_ssl_bl/int_bsl_br/is_conf_bl/is_break_bl/is_weak_of に加え、
    # 全履歴のフラクタル・FVGリスト）を持つ状態機械。5-2 の実測で「未来のバーで値が変わる」
    # (c)ではないと確認済みだが、必要履歴本数自体は sp_* と違い**原理的に非有界**である。
    # zone_break の内部状態は確定した反転（BOS）ごとにリセットされるが、トレンドレッグの
    # 長さに上限が無い（移植元Pine Scriptの性質）ため、リセットの間隔が銘柄・期間によって
    # 数千本に及ぶことがある。5-4c で300銘柄実測した結果:
    #   列                    中央値 p90  p99  最大    >250本の銘柄数
    #   is_zone_break_bull    120   120  250  250     0/300
    #   zb_ssl                120   120  250  2400    2/300
    #   zb_bsl                120   120  251  2400    3/300
    #   is_zone_break_weak    120   120  250  400     1/300
    # このためこの4列だけは WINDOW のまま lookback=ZONE_BREAK_LOOKBACK(400) とする
    # （5-4e。5-4c版は HOT_WINDOW_BARS(504) そのものを使っていたが、それだと
    # `max_lookback()+1=505` が SQLite の保持行数を1行超えマージンがゼロだった。
    # ZONE_BREAK_LOOKBACK の根拠と HOT_WINDOW_BARS に対するマージンは同定数の
    # docstring コメント参照）。
    # **これは厳密解ではない**: 400と600〜2400の間で精度が変わらないと実測で
    # 確認済みのため、これより大きくしても正しさは向上しない。全履歴（Parquet）
    # から計算した場合と異なる値になりうる銘柄が実測で300銘柄中2〜3銘柄（約1%）
    # 存在し、これは増分化以前から本番が抱えていた既存の不正確さ
    # （本計画が新たに作る問題ではない）。
    # 詳細: doc/backend_specification.md、doc/in_progress/t3_incremental_plan.md §8。
    # zb_ssl/zb_bsl/is_zone_break_bull/is_zone_break_weak の warmup_bars は5-6c実測で0
    # （先頭行から非NULL）。これは「必要履歴が非有界」（lookback列を参照。5-4c/5-4e）と
    # 矛盾しない——zone_break_series はフラットな初期状態から常に何らかの値を返す実装で、
    # NULLになること自体が無い（値が全履歴計算と一致するかは別問題。5-6cは「NULLかどうか」
    # だけを見る）。
    _window('is_zone_break_bull', inputs=('high', 'low', 'close'), lookback=ZONE_BREAK_LOOKBACK,
            warmup_bars=0,
            note='5-4c実測(300銘柄): 中央値120/p90 120/p99 250/最大250、250本超0/300。'
                 'zone_break系は原理的に非有界（トレンドレッグ長に上限なし）なため、'
                 'lookback=400(ZONE_BREAK_LOOKBACK)はHOT_WINDOW_BARS(504)にマージンを'
                 '確保した近似解であり厳密解ではない（5-4e）'),
    _window('zb_ssl', inputs=('high', 'low', 'close'), lookback=ZONE_BREAK_LOOKBACK,
            warmup_bars=0,
            note='5-4c実測(300銘柄): 中央値120/p90 120/p99 250/最大2400、250本超2/300。'
                 'zone_break系は原理的に非有界（トレンドレッグ長に上限なし）なため、'
                 'lookback=400(ZONE_BREAK_LOOKBACK)はHOT_WINDOW_BARS(504)にマージンを'
                 '確保した近似解であり厳密解ではない（約1%の銘柄で全履歴計算と値が'
                 '異なりうる。5-4e）'),
    _window('zb_bsl', inputs=('high', 'low', 'close'), lookback=ZONE_BREAK_LOOKBACK,
            warmup_bars=0,
            note='5-4c実測(300銘柄): 中央値120/p90 120/p99 251/最大2400、250本超3/300。'
                 'zone_break系は原理的に非有界（トレンドレッグ長に上限なし）なため、'
                 'lookback=400(ZONE_BREAK_LOOKBACK)はHOT_WINDOW_BARS(504)にマージンを'
                 '確保した近似解であり厳密解ではない（約1%の銘柄で全履歴計算と値が'
                 '異なりうる。5-4e）'),
    _window('is_zone_break_weak', inputs=('high', 'low', 'close'), lookback=ZONE_BREAK_LOOKBACK,
            warmup_bars=0,
            note='5-4c実測(300銘柄): 中央値120/p90 120/p99 250/最大400、250本超1/300。'
                 'zone_break系は原理的に非有界（トレンドレッグ長に上限なし）なため、'
                 'lookback=400(ZONE_BREAK_LOOKBACK)はHOT_WINDOW_BARS(504)にマージンを'
                 '確保した近似解であり厳密解ではない（5-4e）'),
)

INDICATOR_COLUMN_REGISTRY: Mapping[str, ColumnSpec] = {spec.name: spec for spec in _ENTRIES}

if len(INDICATOR_COLUMN_REGISTRY) != len(_ENTRIES):
    # 同名の重複登録（コピペミス）を確実に検出する。
    _names = [spec.name for spec in _ENTRIES]
    _dupes = sorted({n for n in _names if _names.count(n) > 1})
    raise AssertionError(f'INDICATOR_COLUMN_REGISTRY に重複登録があります: {_dupes}')


def recursive_column_names() -> Tuple[str, ...]:
    """RECURSIVE 型（`prev_self=True`）の列名一覧（ソート済み）。

    5-4b で `calculate_indicators(df, spy_df, state=True)` の増分呼び出しは、
    RECURSIVE 列の前日シードを外部のスカラー辞書ではなく df 自身の供給済み
    履歴（最終行の1つ前の行）から直接取り出す設計に変わった
    （`backend/indicators/incremental_merge.py` の `prev_self_seed` 参照）。
    そのため呼び出し側の実装は本関数を必須では使わないが、
    「df に供給すべき列（＝RECURSIVE型の全列）」を機械的に列挙する用途
    （5-6 の T3 ワーカー実装や検証）のために残す。
    """
    return tuple(sorted(
        name for name, spec in INDICATOR_COLUMN_REGISTRY.items() if spec.prev_self
    ))


def supplied_column_names() -> Tuple[str, ...]:
    """T3 ワーカーが `indicators` テーブルから実際に読むべき列名一覧（ソート済み）（5-6d・5-15c）。

    計画書: doc/in_progress/t3_incremental_plan.md §2.3・5-6d・5-15c（読み出しコストの削減）

    5-6 時点の実装は増分呼び出しの都度、レジストリの全67列
    （`sorted(INDICATOR_COLUMN_REGISTRY.keys())`）を `indicators` から読んでいたが、
    実測（3,360銘柄・sandbox SQLite）で読み出しコストを支配しているのは**行数ではなく
    列数**と判明した（252行×67列=18.8秒 に対し 252行×6列=3.2秒）。

    5-6d では「RECURSIVE型列自身 ＋ 他列の `inputs` として参照される T3 列」の
    33列（RECURSIVE21列＋WINDOW型で参照される12列）を保守的に供給していたが、
    2回目の `/code-review` 指摘（5-15c）で、このうち WINDOW型の12列
    （`sma_50`/`sma_150`/`sma_200`/`atr_pct_14`/`rs_value`/`rs_ratio_e5〜200`/
    `rs_macd_line_21`/`vol_surge_21`）は**読まれる前に必ず上書きされる**ことが
    判明した。`calc_moving_averages`/`calc_relative_strength`/`calc_volatility` 等、
    WINDOW型列を計算する箇所はいずれも `state` を受け取らず、常に生価格
    （または同一呼び出し内で先に計算し直された他のRECURSIVE/WINDOW型列）から
    **無条件に上書き計算**する（`calculate_indicators` 内で毎回全行再計算）ため、
    df に供給した WINDOW型列の値そのものは一切読まれない。

    そのため実際に増分計算の入力として参照される T3 列は
    **RECURSIVE型列（`recursive_column_names()`）21列のみ**（自列の前日値を
    `prev_self_seed` が `df[col].iloc[-2]` として直接参照する。`ema_*` /
    `rs_value_eN` / `rs_roc_ema_N` / `td9` / `atr_14` / `rs_macd_signal_21` /
    `rs_blue_dot_age` / `rs_red_dot_age`）。実データ（sandbox SQLite・25銘柄）で
    33列供給時と21列供給時の等価性を突き合わせ、不一致ゼロを確認済み
    （`tmp/verify_worker_incremental_5_15c.py`）。

    lookback/inputs の変更で件数が増減したら気づけるよう、テストで21件を固定している
    （`test_incremental_state_registry.py`）。
    """
    return recursive_column_names()


def max_lookback() -> int:
    """全列（RECURSIVE の inputs 由来の遡りも含む）の lookback 最大値。

    `calculate_indicators` を増分呼び出しする際に、df に供給すべき履歴の
    行数 K の算出根拠（T3 増分化計画 5-4b の等価性テストで使う）。
    5-4b の設計では、RECURSIVE 型列は「供給された行K-1の値をシードに
    最終行だけを計算」、WINDOW 型列は「マージ済みの実値だけに依存する
    通常の計算」になるため、K はレジストリの宣言する lookback（収束を
    待つ必要がない、各列が直接必要とする行数）だけで足りる。
    lookback が未確定（None）の列は対象外とする（5-3b 時点では全列確定済み）。
    """
    values = [spec.lookback for spec in INDICATOR_COLUMN_REGISTRY.values() if spec.lookback is not None]
    return max(values) if values else 0


def is_structurally_null_column(ticker: str, column: str) -> bool:
    """(ticker, column) の組み合わせが「構造的にNULLが正常」かどうかを判定する（5-15b）。

    `calc_relative_strength`（relative_strength.py）は `df_spy is None`（SPY自身を
    計算する場合）で早期returnし、`rs_*` で始まる列（`rs_value` / `rs_value_eN` /
    `rs_ratio_eN` / `rs_roc_ema_N` / `rs_momentum_eN` / `rs_trend_sN` /
    `rs_macd_*` / `rs_blue_dot_age` / `rs_red_dot_age`）は一切計算されず常にNULLに
    なる（自分自身に対する相対強度は定義されないため）。これは増分計算の状態が
    壊れているわけではなく、`--check-recursive-state`（5-6b）・
    `--check-warmup-nulls`（5-6c。`tools/db_health_check.py`）・
    `_calculate_t3_worker`（`pipeline/phases/t3_indicators.py`。5-15b で追加）の
    3箇所すべてで同じ除外が必要なため、重複実装を避けて本関数に集約する
    （code-review指摘2: 除外がワーカー側に無いと、SPYが毎日必ず全期間計算に
    フォールバックし続け、かつ健全な状態でも `NULL_RECURSIVE_COLUMN` の
    WARNING が消えない）。

    2026-09-24（5-11b・sandbox実データ検証で発見）: 同じ「df_spy=None早期return」の
    副作用で、`volume_and_trends.py` の `calc_volume_and_trends` は
    `'spy_volume' in df.columns` をゲートに `vol_surge_21`/`vol_surge_rel_spy_21`
    を計算しており、SPY自身の `df` には `spy_close`/`spy_volume` 列自体が
    マージされない（早期returnのため）。そのためこの2列もSPYでは常にNULLになる
    （`rs_*`とは別の理由だが、同じ「df_spy=None早期return」が根本原因）。
    `up_down_vol_ratio_50`/`vcr` はSPY自身の価格・出来高のみで計算されるため
    この早期returnの影響を受けず対象外（実データで非NULLを確認済み）。
    """
    if ticker != 'SPY':
        return False
    return column.startswith('rs_') or column in ('vol_surge_21', 'vol_surge_rel_spy_21')


def columns_with_warmup_threshold() -> Mapping[str, int]:
    """`warmup_bars` が確定している列名 → 閾値の対応（5-6c）。

    T3 の保存行数（`t3_count`）がこの閾値を**超えている**銘柄は、対応する列の
    最新行が非NULLであるべき。`tools/db_health_check.py --check-warmup-nulls`
    が使う。イベント駆動で閾値を置けない列（`sp_pivot`/`sp_hl`/`sp_counter`。
    `warmup_bars=None`）は戻り値に含まれない＝機械的にチェック対象から除外される。
    """
    return {
        name: spec.warmup_bars
        for name, spec in INDICATOR_COLUMN_REGISTRY.items()
        if spec.warmup_bars is not None
    }


def columns_with_undeterminable_warmup() -> Tuple[str, ...]:
    """`_calculate_t3_worker` が増分ウィンドウ（K=`max_lookback()`行）内だけでは
    「欠陥」か「正当なウォームアップ中」かを判別できない列名一覧（5-15c・ソート済み）。

    2回目の `/code-review` 指摘1（5-15b の回帰）: RECURSIVE型列の供給履歴が
    増分ウィンドウ全体でNULLだった場合、その列が「まだウォームアップが済んで
    いないだけ」なのか「本来値が出るはずなのに欠陥で消えた」のかは、
    `warmup_bars`（演算上必要な本数）と増分ウィンドウ長 K の大小関係で決まる:

    - `warmup_bars < K` なら、ウィンドウがどこから始まっていても（＝銘柄の
      真の先頭が不明でも）ウィンドウ最終行の絶対位置は必ず `warmup_bars` を
      超えるため、それでも全行NULLなら確実に欠陥と判定できる。
    - `warmup_bars >= K` なら、ウィンドウが銘柄の先頭から始まっている
      （＝真の履歴がウィンドウちょうどの長さしかない）場合は正当な
      ウォームアップ中でも全行NULLになりうるため、判別不能
      （`_calculate_t3_worker` は `FALLBACK_REASON_WARMUP_UNDETERMINED` に分類する）。

    本関数はこの「判別不能になりうる列」を機械的に列挙する。**対象は RECURSIVE
    型列のみ**（`_calculate_t3_worker` がこの分類を行うのは、増分計算の供給
    履歴として実際にNULLチェックする対象＝RECURSIVE型列に限られるため。
    WINDOW型列は毎回生価格から無条件に再計算されるためこの分岐を通らず、
    `warmup_bars` がどれだけ大きくても本関数の対象にする意味が無い）。
    A-full後（2026-09-24時点）では `rs_roc_ema_200`（warmup_bars=611 >
    K=`max_lookback()`=400）の1列のみだが、将来レジストリに列が追加/変更
    されて増えても気づけるよう、この事実自体を `test_incremental_state_registry.py`
    で固定する。
    """
    k = max_lookback()
    return tuple(sorted(
        name for name, spec in INDICATOR_COLUMN_REGISTRY.items()
        if spec.prev_self and spec.warmup_bars is not None and spec.warmup_bars >= k
    ))
