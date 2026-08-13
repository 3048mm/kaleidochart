"""
screener_registry.py — スクリーンフィルタキーの宣言的レジストリ。

フィルタキー文字列（"min_vol_surge_21" 等）を FilterSpec に解決する唯一の場所。
`doc/in_progress/screener_filter_unification_plan.md` §3.1（Phase 1 詳細設計）が仕様。

設計上の制約（§3.1.2）:
- `backend/indicators/` の純粋性を壊さないため、このモジュールは pandas も SQLAlchemy も
  import しない。「どのカラムが実在するか」は呼び出し側が渡す known_columns / rank_columns
  （文字列集合）に委ねる。「宣言されている」だけでなく「実際に供給されている」ことを
  検査できるのが要点（フィルタ層の F1/F4 対策）。
- 特殊フィルタ（kind="special"）の関数本体は `screener_filters.py` のまま。このモジュールは
  関数参照を持たず、キーと必要カラムの宣言だけを持つ（Phase 1 では dispatch 構造を動かさない）。
"""
from dataclasses import dataclass
from typing import AbstractSet, Mapping


class UnknownFilterKeyError(ValueError):
    """フィルタキーがどの kind にも解決できなかったことを表す例外。"""


class MissingFilterColumnError(ValueError):
    """解決はできたが、必要なカラムが実際には供給されていないことを表す例外。"""


@dataclass(frozen=True)
class FilterSpec:
    """フィルタキー1つ分の宣言的仕様。"""

    key: str
    kind: str          # numeric|rank|theme_numeric|theme_rank|bool_column|close_gt|special
    column: str | None        # 比較対象の正準カラム名（special は None）
    op: str | None             # ">=" | "<=" | "=="
    requires: tuple[str, ...] = ()        # 当日に必要な正準カラム名
    prev_requires: tuple[str, ...] = ()   # 前日に必要なカラム名（'prev_' 接頭辞は付けない）
    params: tuple[str, ...] = ()          # 随伴する数値パラメータキー（VCP の閾値群など）


@dataclass(frozen=True)
class RequiredColumns:
    """戦略dict全体から集約した、必要カラムの集合。"""

    today: frozenset  # 基準日に必要なカラム（正準名）
    prev: frozenset    # 前日に必要なカラム（'prev_' を付けない素の名前）
    ranks: frozenset   # RelativeRank から必要なカラム


# ============================================================
# METADATA_KEYS — フィルタではない制御キー
# ============================================================
# 現在3箇所に分散している除外集合の和集合（doc/in_progress/screener_filter_unification_plan.md
# §3.1.3 の [!WARNING] 参照）:
#   1. backend/backtest/backtest_runner.py::validate_strategies_config の METADATA_KEYS
#   2. backend/backtest/backtest_screener.py::apply_filters_to_df の skip リスト
#   3. backend/api/screener_router.py::_build_preset_query の個別 elif
#      （'_use_hysteresis' / 'rrg_intensity_threshold'）
# 'max_avg_hits_per_day' は 'min_avg_hits_per_day' の対になる制御キーとして backtest_config.toml
# で実際に使われているが、旧 METADATA_KEYS には非対称に欠落していたため、この集約で補う。
METADATA_KEYS: frozenset = frozenset({
    'id', 'name', 'subtitle', 'subname', 'description', 'group', 'filters', 'use_hysteresis',
    'max_hits_per_day', 'sort_column', 'sort_ascending', 'expression', '_use_hysteresis',
    'min_avg_hits_per_day', 'max_avg_hits_per_day', 'min_hit_rate_pct', 'max_allowed_dd',
    'use_vxv_vix_hysteresis', 'vxv_vix_hysteresis_type',
    'optimization',
    'rrg_intensity_threshold',
})


# ============================================================
# close_gt の短縮名正規化（既存3箇所と完全に同じアルゴリズム）
# ============================================================
# screener_router.py（L313-324）/ backtest_screener.py（L395-408）/ backtest_runner.py（L590-599）
# にコピペされている正規化ロジックと同じ結果になるようにする（Phase 1 では既存側は削除しない）。
_CLOSE_GT_SHORT_NAMES = (
    'sma5', 'sma21', 'sma50', 'sma63', 'sma150', 'sma200',
    'ema5', 'ema21', 'ema50', 'ema63', 'ema150', 'ema200',
)


def _normalize_close_gt_target(ind_name: str) -> str:
    """close_gt 系フィルタの短縮名（sma50/ema63 等）を正準カラム名（sma_50/ema_63）へ正規化する。"""
    if ind_name in _CLOSE_GT_SHORT_NAMES:
        for num in ('200', '150', '63', '50', '21', '5'):
            if ind_name.endswith(num) and not ind_name.endswith('_' + num):
                ind_name = ind_name.replace(num, '_' + num)
                break
    return ind_name


def _build_close_gt_specs() -> dict:
    """close_gt_* / is_close_gt_* の FilterSpec を生成する。

    短縮名（`ema63`）と正準名（`ema_63`）の**両方**を登録する。既存の
    `screener_router._apply_filter`（L313-324）は正規化を無条件に行うため
    `is_close_gt_ema_63` のような正準形も受け付けており、短縮名だけを登録すると
    呼び出し側をレジストリ参照へ切り替えた時点で**今まで通っていたキーが
    UnknownFilterKeyError になる**（現行の TOML に正準形の使用は無いが、
    塞いでおかないと Phase 1 の「差分ゼロ」目標を崩す潜在的な後退になる）。
    """
    specs = {}
    for short_name in _CLOSE_GT_SHORT_NAMES:
        canonical = _normalize_close_gt_target(short_name)
        for target in {short_name, canonical}:
            for prefix in ('close_gt_', 'is_close_gt_'):
                key = f'{prefix}{target}'
                specs[key] = FilterSpec(
                    key=key, kind='close_gt', column=None, op=None,
                    requires=('close', canonical),
                )
    return specs


# ============================================================
# special（純関数15種）の requires / prev_requires / params
# ============================================================
# backend/indicators/screener_filters.py の各 filter_* 関数が実際に参照しているカラム名を
# そのまま写す（推測しない）。prev_requires は 'prev_' 接頭辞を付けない素のカラム名で持つ
# （接頭辞の付与は供給側＝マージ処理の責務。§3.1.2）。
_SPECIAL_SPECS = {
    # --- RRG 象限転換（filter_rrg_leading_in / _improving_in / _lagging_in） ---
    # 3種とも rrg_intensity_threshold を随伴パラメータとして共有する
    # （filter_rrg_lagging_in 自体は intensity を使わないが、TOML/UI 上は3種で1つの
    #   閾値を共有する設計のため params に含める）。
    'rrg_leading_in': FilterSpec(
        key='rrg_leading_in', kind='special', column=None, op=None,
        requires=('rs_ratio_e21', 'rs_momentum_e21'),
        prev_requires=('rs_ratio_e21', 'rs_momentum_e21'),
        params=('rrg_intensity_threshold',),
    ),
    'rrg_improving_in': FilterSpec(
        key='rrg_improving_in', kind='special', column=None, op=None,
        requires=('rs_ratio_e21', 'rs_momentum_e21'),
        prev_requires=('rs_ratio_e21', 'rs_momentum_e21'),
        params=('rrg_intensity_threshold',),
    ),
    'rrg_lagging_in': FilterSpec(
        key='rrg_lagging_in', kind='special', column=None, op=None,
        requires=('rs_ratio_e21', 'rs_momentum_e21'),
        prev_requires=('rs_ratio_e21', 'rs_momentum_e21'),
        params=('rrg_intensity_threshold',),
    ),

    # --- RS-MACD 加速（filter_rs_macd_hist_rising_21） ---
    'is_rs_macd_hist_rising_21': FilterSpec(
        key='is_rs_macd_hist_rising_21', kind='special', column=None, op=None,
        requires=('rs_macd_hist_21',),
        prev_requires=('rs_macd_hist_21',),
    ),

    # --- 個別 RS Ratio %rank 比較（filter_rs_rank_21_gt_63 / filter_rs_rank_14_gt_21） ---
    'is_rs_ratio_rank_e21_gt_e63': FilterSpec(
        key='is_rs_ratio_rank_e21_gt_e63', kind='special', column=None, op=None,
        requires=('rs_ratio_rank_e21', 'rs_ratio_rank_e63'),
    ),
    'is_rs_ratio_rank_e14_gt_e21': FilterSpec(
        key='is_rs_ratio_rank_e14_gt_e21', kind='special', column=None, op=None,
        requires=('rs_ratio_rank_e14', 'rs_ratio_rank_e21'),
    ),

    # --- テーマ RS Ratio 生値比較（filter_theme_rs21_gt_63 / filter_theme_rs14_gt_21） ---
    'is_theme_rs_ratio_e21_gt_e63': FilterSpec(
        key='is_theme_rs_ratio_e21_gt_e63', kind='special', column=None, op=None,
        requires=('rs_ratio_e21', 'rs_ratio_e63'),
    ),
    'is_theme_rs_ratio_e14_gt_e21': FilterSpec(
        key='is_theme_rs_ratio_e14_gt_e21', kind='special', column=None, op=None,
        requires=('rs_ratio_e14', 'rs_ratio_e21'),
    ),

    # --- テーマ RS Ratio %rank 比較（filter_theme_rs_rank_21_gt_63 / _14_gt_21） ---
    'is_theme_rs_ratio_rank_e21_gt_e63': FilterSpec(
        key='is_theme_rs_ratio_rank_e21_gt_e63', kind='special', column=None, op=None,
        requires=('rs_ratio_rank_e21', 'rs_ratio_rank_e63'),
    ),
    'is_theme_rs_ratio_rank_e14_gt_e21': FilterSpec(
        key='is_theme_rs_ratio_rank_e14_gt_e21', kind='special', column=None, op=None,
        requires=('rs_ratio_rank_e14', 'rs_ratio_rank_e21'),
    ),

    # --- RS Trend 生値比較（filter_rs_trend_s21_lt_s63 / filter_rs_trend_s14_lt_s21） ---
    'is_rs_trend_s21_lt_s63': FilterSpec(
        key='is_rs_trend_s21_lt_s63', kind='special', column=None, op=None,
        requires=('rs_trend_s21', 'rs_trend_s63'),
    ),
    'is_rs_trend_s14_lt_s21': FilterSpec(
        key='is_rs_trend_s14_lt_s21', kind='special', column=None, op=None,
        requires=('rs_trend_s14', 'rs_trend_s21'),
    ),

    # --- テーマ RS Trend %rank 比較（filter_theme_rs_trend_rank_s14_gt_s21 / _s21_gt_s63） ---
    'is_theme_rs_trend_rank_s14_gt_s21': FilterSpec(
        key='is_theme_rs_trend_rank_s14_gt_s21', kind='special', column=None, op=None,
        requires=('rs_trend_rank_s14', 'rs_trend_rank_s21'),
    ),
    'is_theme_rs_trend_rank_s21_gt_s63': FilterSpec(
        key='is_theme_rs_trend_rank_s21_gt_s63', kind='special', column=None, op=None,
        requires=('rs_trend_rank_s21', 'rs_trend_rank_s63'),
    ),

    # --- VCP ブレイクアウト（filter_vcp_breakout） ---
    # requires/prev_requires は high_window=63（既定）の経路に加え、pivot_tol /
    # base_vol_dry_max 指定時に追加で必要になる列も含めた和集合（実装 L412-424 参照）。
    'is_vcp_breakout': FilterSpec(
        key='is_vcp_breakout', kind='special', column=None, op=None,
        requires=('dist_63d_high_pct', 'dist_52w_high_pct', 'change_1d_pct',
                  'vol_surge_21', 'is_trend_template'),
        prev_requires=('vcr', 'dist_52w_high_pct', 'dist_63d_high_pct', 'vol_surge_21'),
        params=(
            'breakout_high_window', 'vcr_contraction_max', 'base_high_tol',
            'near_high_tol', 'breakout_change', 'breakout_vol_mult',
            'pivot_tol', 'base_vol_dry_max',
        ),
    ),
}


EXPLICIT_SPECS: dict = {**_SPECIAL_SPECS, **_build_close_gt_specs()}


def resolve_filter_spec(key: str, known_columns: AbstractSet, rank_columns: AbstractSet) -> FilterSpec:
    """キー1つを FilterSpec に解決する。解決できなければ UnknownFilterKeyError。

    解決順序（先に一致したものを採用）:
      1. EXPLICIT_SPECS（special / close_gt）
      2. min_theme_ / max_theme_ + rank_columns  -> theme_rank
      3. min_theme_ / max_theme_ + known_columns -> theme_numeric
      4. min_ / max_ + rank_columns              -> rank
      5. min_ / max_ + known_columns             -> numeric
      6. is_ / bool_ / has_ + known_columns      -> bool_column
      -> いずれにも当たらなければ raise
    """
    # 1. EXPLICIT_SPECS
    if key in EXPLICIT_SPECS:
        return EXPLICIT_SPECS[key]

    # 2 / 3. テーマ系（min_theme_ / max_theme_）— rank が numeric より優先
    for prefix, op in (('min_theme_', '>='), ('max_theme_', '<=')):
        if key.startswith(prefix):
            col_name = key[len(prefix):]
            if col_name in rank_columns:
                return FilterSpec(key=key, kind='theme_rank', column=col_name, op=op)
            if col_name in known_columns:
                return FilterSpec(key=key, kind='theme_numeric', column=col_name, op=op,
                                   requires=(col_name,))
            raise UnknownFilterKeyError(f"未知のフィルタキー（テーマ系）: {key}")

    # 4 / 5. min_ / max_ — rank が numeric より優先
    for prefix, op in (('min_', '>='), ('max_', '<=')):
        if key.startswith(prefix):
            col_name = key[len(prefix):]
            if col_name in rank_columns:
                return FilterSpec(key=key, kind='rank', column=col_name, op=op)
            if col_name in known_columns:
                return FilterSpec(key=key, kind='numeric', column=col_name, op=op,
                                   requires=(col_name,))
            raise UnknownFilterKeyError(f"未知のフィルタキー: {key}")

    # 6. is_ / bool_ / has_ — 実カラム名は接頭辞を含んだまま定義されている
    # （is_trend_template / is_rs_blue_dot / is_rs_red_dot 等）ため、素の key で照合する。
    if key.startswith('is_') or key.startswith('bool_') or key.startswith('has_'):
        if key in known_columns:
            return FilterSpec(key=key, kind='bool_column', column=key, op='==',
                               requires=(key,))

    raise UnknownFilterKeyError(f"未知のフィルタキー: {key}")


def resolve_required_columns(strategy: Mapping, known_columns: AbstractSet,
                              rank_columns: AbstractSet) -> RequiredColumns:
    """戦略dict全体から必要カラム集合を導出する（METADATA_KEYS は無視）。

    データ供給側（クロスセクション構築・日次マージ）はこの結果だけを見ればよい。
    """
    today: set = set()
    prev: set = set()
    ranks: set = set()

    for key in strategy:
        if key in METADATA_KEYS:
            continue

        spec = resolve_filter_spec(key, known_columns, rank_columns)

        if spec.kind in ('rank', 'theme_rank'):
            if spec.column is not None:
                ranks.add(spec.column)
        else:
            for col in spec.requires:
                if col in rank_columns:
                    ranks.add(col)
                else:
                    today.add(col)

        for col in spec.prev_requires:
            if col in rank_columns:
                ranks.add(col)
            else:
                prev.add(col)

    return RequiredColumns(today=frozenset(today), prev=frozenset(prev), ranks=frozenset(ranks))
