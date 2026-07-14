"""
strategy_normalizer.py — Single Source of Truth for filter key alias normalization.

Converts legacy TOML parameter names to canonical names so that downstream code
(backtest_screener, screener_router, validate_strategies_config) only needs to
handle the canonical naming convention.

Usage:
    from backend.backtest.strategy_normalizer import normalize_strategy_keys
    strategy = normalize_strategy_keys(raw_strategy_dict)
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Legacy parameter name → Canonical parameter name
# This is the ONLY place in the codebase that knows about old names.
# ─────────────────────────────────────────────────────────────────────
_PARAM_ALIASES: Dict[str, str] = {
    # --- Virtual column / derived column naming ---
    "min_change_oc_pct":      "min_change_intraday_pct",
    "max_change_oc_pct":      "max_change_intraday_pct",
    "min_dist_ema21_pct":     "min_dist_21ema_pct",
    "max_dist_ema21_pct":     "max_dist_21ema_pct",

    # --- RS Ratio Rank: old suffix-style → new infix-style ---
    "min_rs_ratio_21_rank":   "min_rs_ratio_rank_e21",
    "min_rs_ratio_63_rank":   "min_rs_ratio_rank_e63",
    "max_rs_ratio_21_rank":   "max_rs_ratio_rank_e21",
    "max_rs_ratio_63_rank":   "max_rs_ratio_rank_e63",

    # --- Theme RS Ratio Rank ---
    "min_theme_rs_ratio_14_rank":  "min_theme_rs_ratio_rank_e14",
    "min_theme_rs_ratio_21_rank":  "min_theme_rs_ratio_rank_e21",
    "min_theme_rs_ratio_63_rank":  "min_theme_rs_ratio_rank_e63",
    "max_theme_rs_ratio_14_rank":  "max_theme_rs_ratio_rank_e14",
    "max_theme_rs_ratio_21_rank":  "max_theme_rs_ratio_rank_e21",
    "max_theme_rs_ratio_63_rank":  "max_theme_rs_ratio_rank_e63",

    # --- RS Condition Rank → RS Trend Rank ---
    "min_rs_condition_14_rank":    "min_rs_trend_rank_s14",
    "min_rs_condition_21_rank":    "min_rs_trend_rank_s21",
    "min_rs_condition_63_rank":    "min_rs_trend_rank_s63",
    "max_rs_condition_14_rank":    "max_rs_trend_rank_s14",
    "max_rs_condition_21_rank":    "max_rs_trend_rank_s21",
    "max_rs_condition_63_rank":    "max_rs_trend_rank_s63",

    # --- Theme RS Condition Rank ---
    "min_theme_rs_condition_14_rank":   "min_theme_rs_trend_rank_s14",
    "min_theme_rs_condition_21_rank":   "min_theme_rs_trend_rank_s21",
    "min_theme_rs_condition_63_rank":   "min_theme_rs_trend_rank_s63",
    "max_theme_rs_condition_14_rank":   "max_theme_rs_trend_rank_s14",
    "max_theme_rs_condition_21_rank":   "max_theme_rs_trend_rank_s21",
    "max_theme_rs_condition_63_rank":   "max_theme_rs_trend_rank_s63",

    # --- Boolean comparison filters (old shorthand → canonical is_ prefix) ---
    "rs_rank_21_gt_63":                "is_rs_ratio_rank_e21_gt_e63",
    "rs_rank_14_gt_21":                "is_rs_ratio_rank_e14_gt_e21",
    "theme_rs21_gt_63":                "is_theme_rs_ratio_e21_gt_e63",
    "theme_rs_rank_21_gt_63":          "is_theme_rs_ratio_rank_e21_gt_e63",
    "theme_rs14_gt_21":                "is_theme_rs_ratio_e14_gt_e21",
    "theme_rs_rank_14_gt_21":          "is_theme_rs_ratio_rank_e14_gt_e21",
    "rs_condition_14_gt_21":           "is_rs_trend_rank_s14_gt_s21",
    "rs_condition_21_gt_63":           "is_rs_trend_rank_s21_gt_s63",
    "theme_rs_condition_14_gt_21":     "is_theme_rs_trend_rank_s14_gt_s21",
    "theme_rs_condition_21_gt_63":     "is_theme_rs_trend_rank_s21_gt_s63",
}


def normalize_strategy_keys(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy parameter names in a strategy dict to canonical names.

    - Converts all keys found in _PARAM_ALIASES to their canonical counterparts.
    - If both old and new name exist, the canonical (new) name's value wins.
    - Recursively normalizes the 'optimization' sub-dict if present.
    - Does NOT mutate the input dict; returns a new dict.
    - Logs warnings for each conversion (to encourage TOML migration).

    Args:
        strategy: Raw strategy dict loaded from TOML.

    Returns:
        A new dict with all keys normalized to canonical names.
    """
    result = _normalize_dict(strategy, context=strategy.get("name", "unknown"))

    # Recursively normalize the optimization section
    if "optimization" in result and isinstance(result["optimization"], dict):
        result["optimization"] = _normalize_dict(
            result["optimization"],
            context=f"{strategy.get('name', 'unknown')}.optimization",
        )

    return result


def _normalize_dict(d: Dict[str, Any], context: str = "") -> Dict[str, Any]:
    """Normalize keys in a single dict level."""
    result = {}
    converted = []

    for key, value in d.items():
        canonical = _PARAM_ALIASES.get(key)
        if canonical is not None:
            # Old name found — check for conflict
            if canonical in d:
                # Both old and new exist: skip old, new will be picked up naturally
                logger.warning(
                    "Strategy '%s': Both legacy '%s' and canonical '%s' exist. "
                    "Using canonical value.",
                    context, key, canonical,
                )
                continue
            result[canonical] = value
            converted.append((key, canonical))
        else:
            result[key] = value

    if converted:
        pairs = ", ".join(f"'{old}'->'{new}'" for old, new in converted)
        logger.warning(
            "Strategy '%s': Normalized legacy keys: %s. "
            "Consider updating your TOML to use canonical names.",
            context, pairs,
        )

    return result
