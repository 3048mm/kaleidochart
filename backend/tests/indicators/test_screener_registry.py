"""
test_screener_registry.py — Phase 1 TDD-red: FILTER_SPECS 単体テスト ＋ fail-loud テスト

screener_registry.py（新規予定モジュール、backend/indicators/screener_registry.py）が
フィルタキーを唯一の場所で解決し、未知キー・必要カラム欠落を ValueError 系例外で
検知することを検証する。仕様は doc/completed/screener_filter_unification_plan.md
§3.1（Phase 1 詳細設計）そのもの。

この時点では backend/indicators/screener_registry.py が未実装のため、このファイル全体が
import エラー（ModuleNotFoundError）で RED になることを意図している。
"""
from pathlib import Path

import pytest
import tomli

from db.models import Indicator, RelativeRank
from indicators.screener_filters import SPECIAL_FILTER_KEYS

from indicators import screener_registry
from indicators.screener_registry import (
    resolve_filter_spec,
    resolve_required_columns,
    UnknownFilterKeyError,
    MissingFilterColumnError,
    METADATA_KEYS,
    EXPLICIT_SPECS,
    OUTPUT_EXCLUDED_CATEGORIES,
)


# ============================================================
# テスト用ヘルパー: 実カラム集合の構築（§3.1.2 「known_columns の供給元」に準拠）
# ============================================================

# 仮想カラム（データベース実カラムではなく計算列）。
# **レジストリを唯一の定義場所として参照する。** ここに一覧を複製すると、
# 仮想カラムを追加したときにテストだけが古い集合を見て偽陰性/偽陽性になる
# （実際 sp_dist_pivot_pct 追加時にこのテストだけが落ちた）。
_VIRTUAL_COLUMNS = set(screener_registry.VIRTUAL_COLUMNS)


def _real_known_columns() -> set:
    """db.models.Indicator の全カラム（id/symbol_id/date を除く）
    ＋ DailyPrice.market_cap ＋ 仮想カラム5種。
    """
    cols = {c for c in Indicator.__table__.columns.keys() if c not in ("id", "symbol_id", "date")}
    cols.add("market_cap")
    cols |= _VIRTUAL_COLUMNS
    return cols


def _real_rank_columns() -> set:
    """db.models.RelativeRank の全カラム（id/symbol_id/date/group_name を除く）。"""
    return {
        c for c in RelativeRank.__table__.columns.keys()
        if c not in ("id", "symbol_id", "date", "group_name")
    }


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_screener_presets_filters():
    """data/screener_presets*.toml の全プリセットの filters dict を
    [(ファイル名::preset_id, filters_dict), ...] で返す（expression のみのプリセットは除く）。

    `screener_presets.toml` 以外（`_A_only` / `_B_only` / `_E_only` 等）は git 管理外の
    実験用ファイルで、ワークツリーには存在しない。**glob で「在るものを全部」対象にする**のが
    仕様（本体チェックアウトでは4ファイル、ワークツリーでは1ファイルが対象になる）。
    手書きの実験用プリセットで使ったキーもレジストリで解決できなければならないため
    （そこを素通しにすると F1 が復活する）。
    """
    result = []
    for path in sorted((_PROJECT_ROOT / "data").glob("screener_presets*.toml")):
        with open(path, "rb") as f:
            data = tomli.load(f)
        for section in ("rise", "fall"):
            for item in data.get(section, []):
                filters = item.get("filters")
                if isinstance(filters, dict) and filters:
                    preset_id = item.get("id", item.get("name", "?"))
                    result.append((f"{path.name}::{preset_id}", filters))
    return result


def _load_backtest_strategies():
    """backend/backtest/backtest_config.toml の全 [[strategy]] を
    [(name, strategy_dict), ...] で返す。
    """
    path = _PROJECT_ROOT / "backend" / "backtest" / "backtest_config.toml"
    with open(path, "rb") as f:
        data = tomli.load(f)
    return [(s.get("name", "?"), s) for s in data.get("strategy", [])]


# ============================================================
# A. resolve_filter_spec の基本解決
# ============================================================

class TestResolveNumeric:
    """min_/max_ 接頭辞 + known_columns 一致 -> numeric"""

    def test_min_prefix_resolves_to_numeric_ge(self):
        spec = resolve_filter_spec(
            "min_vol_surge_21", known_columns={"vol_surge_21"}, rank_columns=set()
        )
        assert spec.kind == "numeric"
        assert spec.column == "vol_surge_21"
        assert spec.op == ">="
        assert spec.requires == ("vol_surge_21",)

    def test_max_prefix_resolves_to_numeric_le(self):
        spec = resolve_filter_spec(
            "max_sma50_atr_mult", known_columns={"sma50_atr_mult"}, rank_columns=set()
        )
        assert spec.kind == "numeric"
        assert spec.column == "sma50_atr_mult"
        assert spec.op == "<="


class TestResolveRank:
    """min_/max_ 接頭辞 + rank_columns 一致 -> rank（T4 RelativeRank の percent_rank）"""

    def test_min_prefix_with_rank_column_resolves_to_rank(self):
        spec = resolve_filter_spec(
            "min_rs_ratio_rank_e21", known_columns=set(), rank_columns={"rs_ratio_rank_e21"}
        )
        assert spec.kind == "rank"
        assert spec.column == "rs_ratio_rank_e21"


class TestResolveThemeRank:
    """min_theme_/max_theme_ 接頭辞 + rank_columns 一致 -> theme_rank"""

    def test_theme_prefix_with_rank_column_resolves_to_theme_rank(self):
        spec = resolve_filter_spec(
            "min_theme_rs_ratio_rank_e21", known_columns=set(), rank_columns={"rs_ratio_rank_e21"}
        )
        assert spec.kind == "theme_rank"


class TestResolveThemeNumeric:
    """min_theme_/max_theme_ 接頭辞 + known_columns 一致 -> theme_numeric"""

    def test_theme_prefix_with_known_column_resolves_to_theme_numeric(self):
        spec = resolve_filter_spec(
            "min_theme_rs_trend_s21", known_columns={"rs_trend_s21"}, rank_columns=set()
        )
        assert spec.kind == "theme_numeric"


class TestResolveBoolColumn:
    """is_/bool_/has_ 接頭辞 + known_columns 一致（SMALLINT の実在フラグ列） -> bool_column"""

    def test_is_prefix_with_bool_column_resolves(self):
        spec = resolve_filter_spec(
            "is_trend_template", known_columns={"is_trend_template"}, rank_columns=set()
        )
        assert spec.kind == "bool_column"
        assert spec.op == "=="


class TestResolveCloseGt:
    """is_close_gt_* は EXPLICIT_SPECS からの明示解決。短縮名（ema63/sma50）は
    正準カラム名（ema_63/sma_50）へ正規化されて requires に入る。
    """

    def test_close_gt_ema63_normalizes_short_name(self):
        spec = resolve_filter_spec("is_close_gt_ema63", known_columns=set(), rank_columns=set())
        assert spec.kind == "close_gt"
        assert "close" in spec.requires
        assert "ema_63" in spec.requires

    def test_close_gt_sma50_normalizes_short_name(self):
        spec = resolve_filter_spec("is_close_gt_sma50", known_columns=set(), rank_columns=set())
        assert spec.kind == "close_gt"
        assert "close" in spec.requires
        assert "sma_50" in spec.requires


class TestResolveSpecialRrg:
    """rrg_leading_in は kind='special'。当日・前日ともに rs_ratio_e21/rs_momentum_e21 が必要。
    prev_requires は 'prev_' を付けない素のカラム名で持つ（§3.1.2）。
    """

    def test_rrg_leading_in_requires_ratio_and_momentum_today_and_prev(self):
        spec = resolve_filter_spec("rrg_leading_in", known_columns=set(), rank_columns=set())
        assert spec.kind == "special"
        assert "rs_ratio_e21" in spec.requires
        assert "rs_momentum_e21" in spec.requires
        assert "rs_ratio_e21" in spec.prev_requires
        assert "rs_momentum_e21" in spec.prev_requires
        # prev_requires は 'prev_' 接頭辞を付けない素のカラム名で持つ契約
        assert not any(c.startswith("prev_") for c in spec.prev_requires)


class TestResolveSpecialVcp:
    """is_vcp_breakout は kind='special'。8種の閾値パラメータキーを params に持つ
    （filter_vcp_breakout の呼び出し側 screener_cross_section.py / backtest_screener.py が
    実際に params.get() で参照しているキー名と一致すること）。
    """

    def test_is_vcp_breakout_carries_all_eight_params(self):
        spec = resolve_filter_spec("is_vcp_breakout", known_columns=set(), rank_columns=set())
        assert spec.kind == "special"
        expected_params = {
            "breakout_high_window", "vcr_contraction_max", "base_high_tol",
            "near_high_tol", "breakout_change", "breakout_vol_mult",
            "pivot_tol", "base_vol_dry_max",
        }
        assert set(spec.params) == expected_params


# ============================================================
# B. 解決順序（rank/theme_rank が numeric/theme_numeric より優先）
# ============================================================

class TestResolutionOrder:
    """§3.1.2 の解決順序: 1.EXPLICIT_SPECS 2.theme_rank 3.theme_numeric 4.rank 5.numeric 6.bool_column"""

    def test_rank_wins_over_numeric_when_both_known(self):
        """known_columns と rank_columns の両方に同名列があっても rank が優先される"""
        spec = resolve_filter_spec(
            "min_rs_ratio_rank_e21",
            known_columns={"rs_ratio_rank_e21"},
            rank_columns={"rs_ratio_rank_e21"},
        )
        assert spec.kind == "rank"

    def test_theme_rank_wins_over_theme_numeric_when_both_known(self):
        """テーマ系も同様に theme_rank が theme_numeric より優先される"""
        spec = resolve_filter_spec(
            "min_theme_rs_ratio_rank_e21",
            known_columns={"rs_ratio_rank_e21"},
            rank_columns={"rs_ratio_rank_e21"},
        )
        assert spec.kind == "theme_rank"


# ============================================================
# C. fail-loud（未知キー -> UnknownFilterKeyError）
# ============================================================

class TestUnknownFilterKey:
    def test_typo_key_raises(self):
        """タイポキー（min_vol_surge_2、末尾1文字欠け）は既知カラムに一致しない"""
        with pytest.raises(UnknownFilterKeyError):
            resolve_filter_spec(
                "min_vol_surge_2", known_columns={"vol_surge_21"}, rank_columns=set()
            )

    def test_key_without_recognized_prefix_raises(self):
        """接頭辞の無い謎キーは UnknownFilterKeyError"""
        with pytest.raises(UnknownFilterKeyError):
            resolve_filter_spec("foobar", known_columns=set(), rank_columns=set())

    def test_min_prefix_with_nonexistent_column_raises(self):
        """min_ は付くが known にも rank にも無い列名は UnknownFilterKeyError"""
        with pytest.raises(UnknownFilterKeyError):
            resolve_filter_spec("min_nonexistent_col", known_columns=set(), rank_columns=set())

    def test_exceptions_are_value_error_subclasses(self):
        """§1.3 の既存 fail-loud 実装（report_strategy_scan_coverage 等）と同じ
        ValueError で停止する契約に揃える。
        """
        assert issubclass(UnknownFilterKeyError, ValueError)
        assert issubclass(MissingFilterColumnError, ValueError)


# ============================================================
# D. resolve_required_columns(strategy, known_columns, rank_columns)
# ============================================================

class TestResolveRequiredColumns:
    def test_aggregates_today_prev_and_rank_columns_across_keys(self):
        """複数キーを持つ戦略 dict から today/prev/ranks の必要カラムが集約される"""
        strategy = {
            "min_vol_surge_21": 1.5,
            "rrg_leading_in": True,
            "min_rs_ratio_rank_e21": 0.8,
        }
        known = {"vol_surge_21", "rs_ratio_e21", "rs_momentum_e21"}
        ranks = {"rs_ratio_rank_e21"}

        req = resolve_required_columns(strategy, known_columns=known, rank_columns=ranks)

        assert "vol_surge_21" in req.today
        assert "rs_ratio_e21" in req.today
        assert "rs_momentum_e21" in req.today
        assert "rs_ratio_e21" in req.prev
        assert "rs_momentum_e21" in req.prev
        assert "rs_ratio_rank_e21" in req.ranks

    def test_metadata_keys_are_ignored_and_do_not_appear_in_required_columns(self):
        """METADATA_KEYS が混ざっていても例外にならず、必要カラムにも現れない"""
        strategy = {
            "name": "test_strategy",
            "description": "テスト用戦略",
            "max_hits_per_day": 10,
            "sort_column": "rs_ratio_rank_e21",
            "sort_ascending": False,
            "min_avg_hits_per_day": 0.1,
            "max_avg_hits_per_day": 5.0,
            "max_allowed_dd": 30.0,
            "optimization": {"min_vol_surge_21": {"type": "float", "min": 1.0, "max": 3.0}},
            "_use_hysteresis": False,
            "rrg_intensity_threshold": 0.5,
            "min_vol_surge_21": 1.5,
        }
        known = {"vol_surge_21"}

        req = resolve_required_columns(strategy, known_columns=known, rank_columns=set())

        assert "vol_surge_21" in req.today
        metadata_probe_keys = (
            "name", "description", "max_hits_per_day", "sort_column", "sort_ascending",
            "min_avg_hits_per_day", "max_avg_hits_per_day", "max_allowed_dd", "optimization",
            "_use_hysteresis", "rrg_intensity_threshold",
        )
        for meta_key in metadata_probe_keys:
            assert meta_key not in req.today
            assert meta_key not in req.prev
            assert meta_key not in req.ranks
            # METADATA_KEYS 自体が単一の集約場所として、これらを保持していること
            assert meta_key in METADATA_KEYS

    def test_unknown_key_mixed_in_raises(self):
        """未知キーが混ざっていれば UnknownFilterKeyError が上がる"""
        strategy = {"min_vol_surge_21": 1.5, "min_totally_unknown_thing": 1.0}
        with pytest.raises(UnknownFilterKeyError):
            resolve_required_columns(strategy, known_columns={"vol_surge_21"}, rank_columns=set())


# ============================================================
# E. SPECIAL_FILTER_KEYS との整合
# ============================================================

class TestSpecialFilterKeysParity:
    def test_special_filter_keys_equals_registry_special_kind_keys(self):
        """screener_filters.SPECIAL_FILTER_KEYS（後方互換の導出値）は、レジストリの
        kind='special' キー集合と完全一致すること。ズレたら即検知したい（§3.1.2）。
        """
        registry_special_keys = {
            key for key, spec in EXPLICIT_SPECS.items() if spec.kind == "special"
        }
        assert registry_special_keys == SPECIAL_FILTER_KEYS


# ============================================================
# F. 実データ整合（実装漏れ検知の本体）
# ============================================================

class TestScreenerPresetsResolveAgainstRealSchema:
    """data/screener_presets.toml の全プリセットの filters キーが、
    実際の DB スキーマ（known_columns/rank_columns）に対して解決できること。
    解決できないキーがあれば、そのキー名を含めて fail する。
    """

    def test_all_preset_filter_keys_resolve(self):
        known = _real_known_columns()
        ranks = _real_rank_columns()
        unresolved = []
        for preset_id, filters in _load_screener_presets_filters():
            for key in filters:
                try:
                    resolve_filter_spec(key, known_columns=known, rank_columns=ranks)
                except UnknownFilterKeyError:
                    unresolved.append(f"{preset_id}::{key}")
        assert not unresolved, f"screener_presets.toml に未解決のフィルタキーがある: {unresolved}"


class TestBacktestConfigResolvesAgainstRealSchema:
    """backend/backtest/backtest_config.toml の全 [[strategy]]（METADATA_KEYS を除く）と、
    その [strategy.optimization] サブテーブルのキーが実スキーマに対して解決できること。
    """

    def test_all_strategy_and_optimization_keys_resolve(self):
        from backtest.strategy_normalizer import normalize_strategy_keys

        known = _real_known_columns()
        ranks = _real_rank_columns()
        unresolved = []
        for name, raw_strategy in _load_backtest_strategies():
            strategy = normalize_strategy_keys(raw_strategy)
            opt = strategy.get("optimization", {})
            filter_keys = set(strategy.keys()) - METADATA_KEYS
            if isinstance(opt, dict):
                filter_keys |= set(opt.keys())
            for key in filter_keys:
                try:
                    resolve_filter_spec(key, known_columns=known, rank_columns=ranks)
                except UnknownFilterKeyError:
                    unresolved.append(f"{name}::{key}")
        assert not unresolved, f"backtest_config.toml に未解決のフィルタキーがある: {unresolved}"


class TestRuntimeInjectedLiquidityFloor:
    def test_min_avg_dollar_volume_21_resolves_as_numeric(self):
        """実行時注入される min_avg_dollar_volume_21（TOML には書かれない全戦略共通の
        流動性ハード制約）が avg_dollar_volume_21 実カラムへ numeric 解決できること。
        """
        spec = resolve_filter_spec(
            "min_avg_dollar_volume_21",
            known_columns=_real_known_columns(),
            rank_columns=set(),
        )
        assert spec.kind == "numeric"
        assert spec.column == "avg_dollar_volume_21"


class TestP0_2_DroppedRankColumnsRegression:
    """P0-2 の回帰テスト: バックテスト側の melt が rs_value_rank と
    rs_roc_ema_rank_e5/e14/e21/e63/e200 の6列を落としている（§7 P0-2）。
    レジストリはこれらを正しく rank として解決できなければならない
    （Phase 1 でレジストリ導出に置き換えれば自動的に供給されるようになる想定）。
    """

    @pytest.mark.parametrize("rank_col", [
        "rs_value_rank",
        "rs_roc_ema_rank_e5", "rs_roc_ema_rank_e14", "rs_roc_ema_rank_e21",
        "rs_roc_ema_rank_e63", "rs_roc_ema_rank_e200",
    ])
    def test_dropped_rank_columns_resolve_as_rank(self, rank_col):
        ranks = {rank_col}
        min_spec = resolve_filter_spec(f"min_{rank_col}", known_columns=set(), rank_columns=ranks)
        assert min_spec.kind == "rank"
        assert min_spec.column == rank_col

        max_spec = resolve_filter_spec(f"max_{rank_col}", known_columns=set(), rank_columns=ranks)
        assert max_spec.kind == "rank"
        assert max_spec.column == rank_col

    def test_dropped_rank_columns_are_present_in_real_rank_columns(self):
        """RelativeRank モデルには実際にこの6列が存在すること（melt が落としているのは
        バックテスト側の中間処理であって、DB スキーマの欠落ではない）。
        """
        ranks = _real_rank_columns()
        for rank_col in (
            "rs_value_rank",
            "rs_roc_ema_rank_e5", "rs_roc_ema_rank_e14", "rs_roc_ema_rank_e21",
            "rs_roc_ema_rank_e63", "rs_roc_ema_rank_e200",
        ):
            assert rank_col in ranks


class TestOutputExcludedCategories:
    """OUTPUT_EXCLUDED_CATEGORIES: 最終出力から常に除外するカテゴリの単一定義（§5 Phase 1）。

    テーマ（実在ETF・仮想合成指数）は実売買不可能なため買い候補に出さない。この定義が
    screener_router.py / backtest_screener.py の3箇所で個別にコピペされていたことが
    2026-07-29〜08-05 のテーマ混入バグ（片側だけ修正）の一因だった。
    """

    def test_contains_theme(self):
        assert 'テーマ' in OUTPUT_EXCLUDED_CATEGORIES
