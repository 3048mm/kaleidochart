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
from typing import AbstractSet, Iterable, Mapping


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
    """戦略dict全体から集約した、必要カラムの集合。

    `today` / `prev` / `ranks` は**ハード要求**（欠けていればフィルタが正しく評価できないので
    `MissingFilterColumnError` で停止する）。`optional` は**ソフト要求**で、
    「あれば引くが、無くても失敗させない」列（典型は並べ替えキー `sort_column`）。

    両者を混ぜてはいけない。ソート列をハード要求として扱うと、ランクデータが無い環境で
    deny-by-default が誤発火し、**フィルタと無関係にシグナルが0件になる**
    （2026-08-10 に RRG のテスト15件が落ちる形で顕在化。計画書 §7 P1-6）。
    """

    today: frozenset      # 基準日に必要なカラム（正準名）
    prev: frozenset       # 前日に必要なカラム（'prev_' を付けない素の名前）
    ranks: frozenset      # RelativeRank から必要なカラム
    optional: frozenset = frozenset()   # あれば引く（欠けても失敗させない）


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
    # 'vcr'（当日）は filter_vcp_breakout 自体は prev_vcr しか参照しないが、
    # screener_cross_section の旧ハードコード _IND_COLS が当日分も取得していたため、
    # 導出後の _IND_COLS が旧実装を包含するようにここへ明示登録している
    # （§3.1.4 (e) の「不足があれば宣言漏れとしてレジストリ側を直す」方針）。
    'is_vcp_breakout': FilterSpec(
        key='is_vcp_breakout', kind='special', column=None, op=None,
        # 当日側で filter_vcp_breakout が実際に読むのはこの5列だけ。
        # 旧 screener_cross_section._IND_COLS は当日の 'vcr' も含んでいたが、
        # 実装は prev_vcr しか参照しない（当日 vcr は誰も読まない）ため宣言しない。
        # requires は「実際に読む列」の宣言であり、旧リストへの追従ではない
        # （false declaration を入れるとレジストリが真実でなくなる。2026-08-10 検収で是正）。
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


# ============================================================
# VIRTUAL_COLUMNS — 仮想（計算）カラム名
# ============================================================
# SQL 式は screener_router._VIRTUAL_COLUMNS、pandas の導出は apply_filters_to_df にあり、
# このレジストリは「名前」だけを持つ（式は持たない）。値は screener_router._VIRTUAL_COLUMNS
# のキーと完全一致させること。
VIRTUAL_COLUMNS: frozenset = frozenset({
    'change_oc_pct', 'change_intraday_pct',
    'dist_ema21_pct', 'dist_21ema_pct', 'dist_sma50_pct',
})


# ============================================================
# ATTACHED_PARAM_KEYS — 特殊フィルタに随伴する数値パラメータ
# ============================================================
# `is_vcp_breakout` の閾値8種や `rrg_intensity_threshold` のように、それ自体はフィルタでは
# なく「特殊フィルタが有効なときに参照される値」であるキーの全体集合。
# 旧 `backtest_runner.validate_strategies_config` のローカル定数
# `FILTER_ATTACHED_PARAM_KEYS` に相当する（レジストリへ集約）。
#
# これを除外し忘れると、`is_vcp_breakout` を使う戦略で `pivot_tol` 等が
# 「未知のキー」と判定され、fail-loud によりバックテスト全体が停止する
# （2026-08-10 の検収で実際に8件の誤検知として再現。計画書 §7 P1-1）。
ATTACHED_PARAM_KEYS: frozenset = frozenset(
    p for spec in EXPLICIT_SPECS.values() for p in spec.params
)


def is_non_filter_key(key: str) -> bool:
    """フィルタキーとして解決を試みるべきでないキー（制御キー・随伴パラメータ）か。

    全ての呼び出し側はキーを走査する前にこの述語で除外すること。
    除外集合が呼び出し側ごとに分かれると、また3箇所に分散する（それが本計画の出発点）。
    """
    return key in METADATA_KEYS or key in ATTACHED_PARAM_KEYS


# ============================================================
# RANK_FRAME_ALIASES — 正準名（RelativeRank の実カラム名）→ フレーム内で使われている列名
# ============================================================
# 未登録の正準名は恒等（そのままの名前でフレームに入る）。現在 screener_cross_section の
# _RANK_COL_MAP と backtest_screener の alias_map に同じ6件が重複しているため、
# 両者をここへ寄せる（doc/in_progress/screener_filter_unification_plan.md §3.1.4 (a)）。
RANK_FRAME_ALIASES: dict = {
    'rs_ratio_rank_e14': 'rs14_rank',
    'rs_ratio_rank_e21': 'rs21_rank',
    'rs_ratio_rank_e63': 'rs63_rank',
    'rs_trend_rank_s14': 'rs_condition_14_rank',
    'rs_trend_rank_s21': 'rs_condition_21_rank',
    'rs_trend_rank_s63': 'rs_condition_63_rank',
}


def to_frame_column(canonical: str) -> str:
    """正準カラム名をフレーム内の実列名へ変換する（未登録は恒等）。"""
    return RANK_FRAME_ALIASES.get(canonical, canonical)


# ============================================================
# OUTPUT_EXCLUDED_CATEGORIES — 最終出力から常に除外するカテゴリ
# ============================================================
# テーマ（実在ETF・仮想合成指数）は実売買の対象にしないため、買いシグナル／表示候補には出さない。
# ただし**リーディングテーマ判定と構成銘柄への波及には category=='テーマ' の行が必要**なので、
# フィルタ処理の途中では保持し、最終出力の直前でだけ落とす（2段構え）。
# 2026-07-29 にバックテスト側、2026-08-05 に API 側と別々に修正され、その間の1週間
# 本番画面に仮想合成指数が出ていた（doc/in_progress/screener_filter_unification_plan.md §1.2）。
# 定義を1箇所に集約して再発を防ぐ。適用位置（フィルタ後・出力直前）は変えないこと
# （前に出すとテーマ行が消えてリーディングテーマ判定が壊れる）。
OUTPUT_EXCLUDED_CATEGORIES: frozenset = frozenset({'テーマ'})


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
                              rank_columns: AbstractSet,
                              extra_columns: Iterable = ()) -> RequiredColumns:
    """戦略dict全体から必要カラム集合を導出する（METADATA_KEYS は無視）。

    データ供給側（クロスセクション構築・日次マージ）はこの結果だけを見ればよい。

    Args:
        extra_columns: **フィルタ以外の理由で必要な列**（正準名）。
            典型は `sort_column`（Top-N 抽出の並べ替えキー）。`sort_column` は
            METADATA_KEYS に含まれるためフィルタキーとしては解決されないが、
            **列としては供給されていないと Top-N の結果が変わる**。
            旧 `needs_rs*` の or 連鎖は `sort_col in (...)` を明示的に含んでいたので、
            ここを落とすと「フィルタでランクを使わない戦略」だけ並べ替えが壊れる
            （2026-08-10 の差分実測で C1/C2/G2/G3 の上位10件が入れ替わる形で顕在化。
            計画書 §7 P1-6）。呼び出し側は既定値を適用したうえで渡すこと。
    """
    today: set = set()
    prev: set = set()
    ranks: set = set()
    optional: set = set()

    for col in extra_columns:
        # ソフト要求として積む。ハード側（ranks/today）に入れてはいけない
        # （deny-by-default と MissingFilterColumnError が誤発火する）
        if col and (col in rank_columns or col in known_columns):
            optional.add(col)

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

    # optional からハード要求と重複するものを除く（重複しても害はないが意味を明確にする）
    optional -= (ranks | today)
    return RequiredColumns(today=frozenset(today), prev=frozenset(prev),
                           ranks=frozenset(ranks), optional=frozenset(optional))
