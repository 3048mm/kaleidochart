"""Screener API router.

Screener engine (preset loading, dynamic filters, boolean special filters,
expression parser) and the /screener* endpoints, extracted from routers.py
(audit D-1). The special-filter logic is being unified with
indicators/screener_filters.py (audit D-2).
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, Column as SAColumn
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, logging, tomli
import pandas as pd
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent, Earning
from api import schemas
from api.deps import get_api_db
from api.screener_cross_section import load_cross_section
from indicators.screener_filters import SPECIAL_FILTER_KEYS

import re

#: 頭字語など、単純な capitalize では正しくならない語。
_LABEL_TOKENS = {"rs": "RS", "rrg": "RRG", "macd": "MACD", "vcp": "VCP", "td9": "TD9",
                 "ema": "EMA", "sma": "SMA", "atr": "ATR", "adr": "ADR", "vcr": "VCR"}


def _label_words(body: str) -> str:
    """`rs_ratio_rank_e21` -> `RS Ratio Rank e21`。

    `e21` / `s63` のような期間サフィックスと `1st` のような序数は、
    大文字化すると読めなくなる（`E21` / `1St`）ので原形のまま残す。
    """
    words = []
    for token in body.split("_"):
        if token in _LABEL_TOKENS:
            words.append(_LABEL_TOKENS[token])
        elif re.fullmatch(r"[es]\d+", token) or re.fullmatch(r"\d+\w*", token):
            words.append(token)          # e21 / s63 / 21 / 1st
        else:
            words.append(token.capitalize())
    return " ".join(words)


def _humanize_special_filter(key: str) -> str:
    """特殊フィルタのキーから表示ラベルを作る。

    表記ルール（ユーザー合意 2026-08-27）:
      - `_gt_` / `_lt_` を含む -> 比較記号で表し、`Is` は付けない
            is_rs_ratio_rank_e21_gt_e63 -> "RS Ratio Rank e21 > e63"
            is_rs_trend_s14_lt_s21      -> "RS Trend s14 < s21"
      - 含まない場合 -> `is_` 接頭辞をそのまま `Is` として残す
            is_vcp_breakout             -> "Is VCP Breakout"
            rrg_leading_in              -> "RRG Leading In"

    ハードコードの辞書を持たないので、特殊フィルタを追加しても表記が揃う。
    """
    has_is = key.startswith("is_")
    body = key[3:] if has_is else key
    for op, symbol in (("_gt_", " > "), ("_lt_", " < ")):
        if op in body:
            left, right = body.split(op, 1)
            return f"{_label_words(left)}{symbol}{_label_words(right)}"
    label = _label_words(body)
    return f"Is {label}" if has_is else label
from indicators import screener_registry

logger = logging.getLogger(__name__)
router = APIRouter()

# ============================================================
# Screener Engine: External Config & Dynamic Filter
# ============================================================

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PRESETS_PATH = os.path.join(_PROJECT_ROOT, "data", "screener_presets.toml")

def _load_presets() -> dict:
    """Load screener presets from TOML (re-read on every call for hot-reload)."""
    try:
        from backend.backtest.strategy_normalizer import normalize_strategy_keys
        with open(_PRESETS_PATH, "rb") as f:
            data = tomli.load(f)
            for section in ("rise", "fall"):
                if section in data and isinstance(data[section], list):
                    for item in data[section]:
                        if "filters" in item and isinstance(item["filters"], dict):
                            item["filters"] = normalize_strategy_keys(item["filters"])
            return data
    except Exception as e:
        logger.error(f"Failed to load screener presets: {e}")
        return {"rise": [], "fall": []}

# --- Indicator column whitelist (built from SQLAlchemy model) ---
_INDICATOR_COLUMNS: Dict[str, SAColumn] = {}
_INDICATOR_COLUMN_TYPES: Dict[str, str] = {}
for _col_name, _col_obj in Indicator.__table__.columns.items():
    if _col_name in ("id", "symbol_id", "date"):
        continue
    _INDICATOR_COLUMNS[_col_name] = getattr(Indicator, _col_name)
    _python_type = "int" if "Integer" in str(type(_col_obj.type)) or "SmallInteger" in str(type(_col_obj.type)) else "float"
    _INDICATOR_COLUMN_TYPES[_col_name] = _python_type

# Add DailyPrice columns that or functionally like indicators (like market_cap)
_INDICATOR_COLUMNS["market_cap"] = DailyPrice.market_cap
_INDICATOR_COLUMN_TYPES["market_cap"] = "float"

# --- 全戦略共通の流動性ハード制約 (min_avg_dollar_volume_21) ---
# 最適化バックテスト・個別銘柄シナリオテストと同じ基準値を backtest_config.toml の
# [general] から読む（値のソースを1箇所に保つ）。UIには出さず常時適用する
# （2026-07-27、doc/issue_list.md P0 参照）。値の解決は common_constraints.py に一本化
# （§5 Phase 1。旧実装はこのファイルで TOML を直接読んでいた）。
def _load_min_avg_dollar_volume_21() -> Optional[float]:
    from backend.backtest.common_constraints import load_min_avg_dollar_volume_21
    return load_min_avg_dollar_volume_21()

_MIN_AVG_DOLLAR_VOLUME_21 = _load_min_avg_dollar_volume_21()

# --- Virtual (computed) columns ---
_VIRTUAL_COLUMNS = {
    "change_oc_pct":  lambda: (DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100,
    "change_intraday_pct":  lambda: (DailyPrice.close - DailyPrice.open) / DailyPrice.open * 100,
    "dist_ema21_pct": lambda: (DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100,
    "dist_21ema_pct": lambda: (DailyPrice.close - Indicator.ema_21) / Indicator.ema_21 * 100,
    "dist_sma50_pct": lambda: (DailyPrice.close - Indicator.sma_50) / Indicator.sma_50 * 100,
    # 構造ピボット (LL-HL)。sp_pivot / sp_hl が NULL の行は式全体が NULL になり、
    # 比較が偽になるため「構造が無い銘柄は自然に落ちる」
    "sp_dist_pivot_pct": lambda: (Indicator.sp_pivot - DailyPrice.close) / DailyPrice.close * 100,
    "sp_range_pct":      lambda: (Indicator.sp_pivot - Indicator.sp_hl) / DailyPrice.close * 100,
    "sp_risk_pct":       lambda: (DailyPrice.close - Indicator.sp_hl) / DailyPrice.close * 100,
}

# --- レジストリ fail-loud 判定用: 既知カラム集合 / ランクカラム集合 ---
# _build_preset_query の is_known 判定を screener_registry.resolve_filter_spec() へ
# 委譲するために使う（doc/completed/screener_filter_unification_plan.md §3.1.2）。
_KNOWN_FILTER_COLUMNS = frozenset(_INDICATOR_COLUMNS.keys()) | frozenset(_VIRTUAL_COLUMNS.keys())
_RANK_FILTER_COLUMNS = frozenset(
    col for col in RelativeRank.__table__.columns.keys()
    if col not in ("id", "symbol_id", "date", "group_name")
)

# --- Column category mapping for frontend ---
_COLUMN_CATEGORIES = {
    "Price & Trend": ["sma_5", "sma_21", "sma_50", "sma_63", "sma_150", "sma_200",
                      "ema_5", "ema_21", "ema_50", "ema_63", "ema_150", "ema_200",
                      "is_trend_template", "change_1d_pct", "change_1w_pct", "change_1m_pct",
                      "change_oc_pct", "change_intraday_pct", "dist_ema21_pct", "dist_21ema_pct", "dist_sma50_pct",
                      "dist_63d_high_pct", "dist_52w_high_pct"],
    "Volume & Volatility": ["atr_14", "atr_pct_14", "adr_pct_21", "sma50_atr_mult",
                            "td9", "vol_surge_21", "vol_surge_rel_spy_21", "up_down_vol_ratio_50", "vcr", "vol_accum_days_5"],
    "Momentum & RS": ["rs_value",
                      "rs_trend_s14", "rs_trend_s21", "rs_trend_s63",
                      "rs_value_e14", "rs_value_e21", "rs_value_e63",
                      "rs_momentum_e14", "rs_momentum_e21", "rs_momentum_e63",
                      "rs_ratio_e14", "rs_ratio_e21", "rs_ratio_e63",
                      "is_rs_blue_dot", "is_rs_red_dot"],
    "Fundamentals": ["market_cap"],
}
_COL_TO_CATEGORY = {}
for _cat, _cols in _COLUMN_CATEGORIES.items():
    for _c in _cols:
        _COL_TO_CATEGORY[_c] = _cat

_COLUMN_LABELS = {
    # Price & Trend
    "sma_5": "5 SMA",
    "sma_21": "21 SMA",
    "sma_50": "50 SMA",
    "sma_63": "63 SMA",
    "sma_150": "150 SMA",
    "sma_200": "200 SMA",
    "ema_5": "5 EMA",
    "ema_21": "21 EMA",
    "ema_50": "50 EMA",
    "ema_63": "63 EMA",
    "ema_150": "150 EMA",
    "ema_200": "200 EMA",
    "is_trend_template": "Trend Template",
    "change_1d_pct": "1D Change %",
    "change_1w_pct": "1W Change %",
    "change_1m_pct": "1M Change %",
    "change_oc_pct": "Open-to-Close %",
    "change_intraday_pct": "Intraday Change %",
    "dist_ema21_pct": "Dist 21EMA %",
    "dist_21ema_pct": "Dist 21EMA %",
    "dist_sma50_pct": "Dist 50SMA %",
    "dist_63d_high_pct": "Dist 63D High %",
    "dist_52w_high_pct": "Dist 52W High %",
    # Volume & Volatility
    "atr_14": "14 ATR",
    "atr_pct_14": "14 ATR %",
    "adr_pct_21": "21 ADR %",
    "sma50_atr_mult": "50/ATR",
    "td9": "TD9",
    "vol_surge_21": "21 Vol Surge",
    "vol_surge_rel_spy_21": "Vol Surge vs SPY",
    "up_down_vol_ratio_50": "U/D Vol Ratio",
    "vcr": "VCR",
    "vol_accum_days_5": "Accum Days (5D)",
    # Momentum & RS
    "rs_value": "RS Value",
    "rs_trend_s14": "RS Trend 14",
    "rs_trend_s21": "RS Trend 21",
    "rs_trend_s63": "RS Trend 63",
    "rs_value_e14": "RS Value e14",
    "rs_value_e21": "RS Value e21",
    "rs_value_e63": "RS Value e63",
    "rs_momentum_e14": "RS Momentum e14",
    "rs_momentum_e21": "RS Momentum e21",
    "rs_momentum_e63": "RS Momentum e63",
    "rs_ratio_e14": "RS Ratio e14",
    "rs_ratio_e21": "RS Ratio e21",
    "rs_ratio_e63": "RS Ratio e63",
    "is_rs_blue_dot": "RS Blue Dot",
    "is_rs_red_dot": "RS Red Dot",
    # Fundamentals
    "market_cap": "Market Cap",
    # Ranks (Relative Rank indicator columns labels)
    "rs_value_rank": "RS Value Rank",
    "rs_ratio_rank_e14": "RSR14% Rank",
    "rs_ratio_rank_e21": "RSR21% Rank",
    "rs_ratio_rank_e63": "RSR63% Rank",
    "rs_momentum_rank_e14": "RSM14% Rank",
    "rs_momentum_rank_e21": "RSM21% Rank",
    "rs_momentum_rank_e63": "RSM63% Rank",
    "rs_trend_rank_s14": "RS Trend Rank 14",
    "rs_trend_rank_s21": "RS Trend Rank 21",
    "rs_trend_rank_s63": "RS Trend Rank 63",
    "rs_roc_ema_rank_e14": "RS ROC Rank 14",
    "rs_roc_ema_rank_e21": "RS ROC Rank 21",
    "rs_roc_ema_rank_e63": "RS ROC Rank 63",
}

# --- Special boolean filters ---
# 実体は indicators/screener_filters.py の純関数群に一本化（audit D-2）。
# フィルタ適用の実装は backend/backtest/backtest_screener.py::apply_filters_to_df に一本化
# （Phase 3 ステップ 3c。doc/completed/screener_filter_unification_plan.md §3.3.1 (c)）。
# 旧 _apply_filter（SQLAlchemy）・_parse_expression_to_filter・_resolve_column は撤去済み。


# ============================================================
# Screener Preset / Meta Endpoints
# ============================================================

@router.get("/screener/presets", response_model=schemas.ScreenerPresetsResponse)
def get_screener_presets():
    """Return all screener presets from the external TOML config."""
    raw = _load_presets()
    def _to_items(raw_list):
        items = []
        for p in raw_list:
            items.append(schemas.ScreenerPresetItem(
                id=p.get("id", ""),
                name=p.get("name", ""),
                subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", ""),
                filters=p.get("filters", {}),
                expression=p.get("expression"),
            ))
        return items
    return schemas.ScreenerPresetsResponse(
        rise=_to_items(raw.get("rise", [])),
        fall=_to_items(raw.get("fall", [])),
    )

@router.get("/screener/meta", response_model=schemas.ScreenerMetaResponse)
def get_screener_meta(db: Session = Depends(get_api_db)):
    """Return all available filter columns (T3 Indicator + virtual) and T4 rank indicators."""
    columns = []
    for col_name, col_type in _INDICATOR_COLUMN_TYPES.items():
        cat = _COL_TO_CATEGORY.get(col_name, "Other")
        label = _COLUMN_LABELS.get(col_name, col_name.replace("_", " ").title())
        step = 1.0 if col_type == "int" else 0.1
        columns.append(schemas.ScreenerColumnMeta(name=col_name, label=label, category=cat, type=col_type, step=step))

    # Virtual columns
    virtual = []
    for vc_name in _VIRTUAL_COLUMNS.keys():
        cat = _COL_TO_CATEGORY.get(vc_name, "Price & Trend")
        label = _COLUMN_LABELS.get(vc_name, vc_name.replace("_", " ").title())
        virtual.append(schemas.ScreenerColumnMeta(name=vc_name, label=label, category=cat, type="float", step=0.1))

    # 特殊ブールフィルタ。唯一の定義場所（SPECIAL_FILTER_KEYS）から導出する。
    # フロントでハードコードすると、フィルタを足すたびに入れ忘れる（実際に起きた）。
    special = [
        schemas.ScreenerColumnMeta(
            name=key,
            label=_humanize_special_filter(key),
            category="Special",
            type="bool",
            step=1.0,
        )
        for key in sorted(SPECIAL_FILTER_KEYS)
    ]

    # close_gt 系（Close > 移動平均）も同じトグルとして出す。
    # SPECIAL_FILTER_KEYS は kind='special' だけなので、これまで漏れていた。
    # エイリアスが多い（close_gt_ema21 / close_gt_ema_21 / is_close_gt_ema21 …）ので
    # **対象カラムごとに1つ**へ畳む。代表キーは is_ 付きを優先し、無ければ最短のものを採る。
    _by_column: Dict[str, List[str]] = {}
    for key, spec in screener_registry.EXPLICIT_SPECS.items():
        # close_gt は column ではなく requires=("close", "<MA列>") に対象を持つ
        if spec.kind == "close_gt":
            target = next((c for c in spec.requires if c != "close"), None)
            if target:
                _by_column.setdefault(target, []).append(key)
    for column in sorted(_by_column):
        aliases = _by_column[column]
        canonical = min(
            aliases, key=lambda k: (not k.startswith("is_"), len(k), k))
        special.append(schemas.ScreenerColumnMeta(
            name=canonical,
            label=f"Close > {_label_words(column).replace(' ', '')}",
            category="Special",
            type="bool",
            step=1.0,
        ))

    # T4 rank indicator names (relative_ranks テーブル of ランクカラム名)
    rank_names = [
        'rs_value_rank',
        'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63',
        'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63',
        'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63',
        'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63',
    ]

    return schemas.ScreenerMetaResponse(
        columns=columns, 
        rank_indicators=rank_names, 
        virtual_columns=virtual,
        special_filters=special,
        labels=_COLUMN_LABELS
    )


@router.get("/screener/dashboard", response_model=schemas.ScreenerDashboardResponse)
def get_screener_dashboard(
    db: Session = Depends(get_api_db),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD")
):
    """Screener dashboard: builds each preset card from screener_presets.toml.

    Phase 3 ステップ 3c（doc/completed/screener_filter_unification_plan.md §3.3.1 (c)）:
    基準日1営業日分の ScreenerFrame（load_cross_section）を1回だけ構築し、全プリセットで
    使い回した上で apply_filters_to_df（唯一のフィルタエンジン）に通す。SQL は取得のみ。
    """
    from backend.backtest.backtest_screener import apply_filters_to_df
    from backend.backtest.common_constraints import inject_liquidity_floor

    # Determine the date to use
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()

    if not latest_date_result:
        return schemas.ScreenerDashboardResponse(rise=[], fall=[])

    # Get previous date for RRG transition check
    previous_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date < latest_date_result).scalar()

    # 基準日1営業日分の ScreenerFrame（全銘柄・全指標・ランク・前日列込み）を1回だけ構築し、
    # 全プリセットで使い回す（旧実装はプリセットごとに SQL クエリを発行していた）。
    frame, df_tc = load_cross_section(db, latest_date_result, previous_date_result)
    # DailyPrice は LEFT JOIN で取り込まれるため（load_cross_section は Indicator 側を母集合と
    # する）、旧実装の INNER JOIN（Symbol×Indicator×DailyPrice）と挙動を揃えるため、
    # 価格が無い行（本来あり得ないが念のため）は除外する。
    frame = frame[frame['close'].notna()].copy()

    # apply_filters_to_df の theme 生値フィルタ（is_theme_rs_ratio_e21_gt_e63 等）が直接参照する
    # df_symbols（id/category のみ。evaluate_special_filters の df_symbols_proxy と同じ形）。
    df_symbols = frame[["symbol_id", "category"]].rename(columns={"symbol_id": "id"})
    # ランクは frame に既にフレーム内名でマージ済み（has_all_premerged により再取得は
    # 自動的にスキップされる）ため、df_ranks は空でよい。
    df_ranks_empty = pd.DataFrame(columns=["symbol_id", "date"])

    def _fetch_top_8(filtered: pd.DataFrame, is_rise: bool = True):
        if filtered.empty:
            return []

        # Default sort: by 1Day% (Prev Close base) descending → top 8（旧 SQL の ORDER BY と同じ）
        sort_key = filtered['change_1d_pct'].fillna(float('-inf'))
        top = filtered.assign(_sort_key=sort_key).sort_values('_sort_key', ascending=False).head(8)

        stock_ids = [int(s) for s in top['symbol_id'].tolist()]
        if not stock_ids:
            return []

        # Bulk-preload rs_trend_s21 history for the last 30 trading days
        date_rows = db.query(Indicator.date).filter(
            Indicator.date <= latest_date_result
        ).distinct().order_by(desc(Indicator.date)).limit(30).all()
        history_dates = [dr[0] for dr in date_rows]

        trend_rows = db.query(
            Indicator.symbol_id,
            Indicator.date,
            Indicator.rs_trend_s21
        ).filter(
            Indicator.symbol_id.in_(stock_ids),
            Indicator.date.in_(history_dates)
        ).order_by(Indicator.symbol_id, Indicator.date).all()

        from collections import defaultdict
        stock_trends = defaultdict(list)
        for tr in trend_rows:
            stock_trends[tr.symbol_id].append(tr.rs_trend_s21 if tr.rs_trend_s21 is not None else 0.0)

        # We query the mapping and theme rank in one query
        theme_ranks = db.query(
            ThemeConstituent.symbol_id.label("stock_id"),
            Symbol.ticker.label("theme_ticker"),
            Symbol.name.label("theme_name"),
            RelativeRank.rs_ratio_rank_e21.label("rs_ratio_21")
        ).join(
            Symbol, ThemeConstituent.theme_id == Symbol.id
        ).join(
            RelativeRank, (Symbol.id == RelativeRank.symbol_id) & (RelativeRank.date == latest_date_result)
        ).filter(
            ThemeConstituent.symbol_id.in_(stock_ids)
        ).all()

        # Group theme ranks by stock_id
        stock_themes = defaultdict(list)
        for tr in theme_ranks:
            stock_themes[tr.stock_id].append({
                "ticker": tr.theme_ticker,
                "name": tr.theme_name.split("::")[1] if "::" in tr.theme_name else tr.theme_name,
                "rs_ratio": tr.rs_ratio_21 if tr.rs_ratio_21 is not None else 0.0
            })

        items = []
        for row in top.itertuples():
            sid = int(row.symbol_id)
            chg = row.change_1d_pct if not pd.isna(row.change_1d_pct) else 0.0

            # Find the strongest or weakest theme
            themes_for_stock = stock_themes.get(sid, [])
            best_weakest_theme = None
            if themes_for_stock:
                if is_rise:
                    # Rise: pick the one with max RSRatio
                    best_weakest_theme = max(themes_for_stock, key=lambda x: x["rs_ratio"])
                else:
                    # Fall: pick the one with min RSRatio
                    best_weakest_theme = min(themes_for_stock, key=lambda x: x["rs_ratio"])

            theme_ticker = best_weakest_theme["ticker"] if best_weakest_theme else None
            theme_name = best_weakest_theme["name"] if best_weakest_theme else None
            theme_rs = best_weakest_theme["rs_ratio"] if best_weakest_theme else None

            items.append(schemas.ScreenerDashboardItem(
                id=sid,
                ticker=row.ticker,
                name=row.name,
                change_pct=chg,
                theme_ticker=theme_ticker,
                theme_name=theme_name,
                theme_rs_ratio=theme_rs,
                rs_trend_history=stock_trends.get(sid, [])
            ))
        return items

    def _run_preset(preset_def: dict):
        """1プリセット分のフィルタを apply_filters_to_df へ委譲する。

        Returns:
            (filtered_df, applied_filters)
        未知キー・必要カラム欠落は apply_filters_to_df 内で UnknownFilterKeyError /
        MissingFilterColumnError として送出される（呼び出し元 try/except が捕捉し、
        他のプリセットは正常表示を続ける。U-1 (b)）。
        """
        preset_filters = preset_def.get("filters", {})
        filters: Dict[str, Any] = dict(preset_filters)
        applied_filters = set()

        # Apply expression-based filters (OR conditions etc.)
        expression = preset_def.get("expression")
        if expression:
            filters["expression"] = expression
            applied_filters.add("expression")

        # rrg_intensity_threshold は preset 直下 / [preset.filters] のどちらに書いてもよい旧仕様を維持
        if "rrg_intensity_threshold" not in filters:
            thr = preset_def.get("rrg_intensity_threshold")
            if thr:
                filters["rrg_intensity_threshold"] = thr

        for key, value in preset_filters.items():
            if key in SPECIAL_FILTER_KEYS:
                if value is True:
                    applied_filters.add(key)
                continue
            if screener_registry.is_non_filter_key(key):
                # 制御キー（_use_hysteresis 等）と特殊フィルタの随伴パラメータ
                # （is_vcp_breakout の pivot_tol 等）は applied_filters に含めない
                continue
            applied_filters.add(key)

        # ---- 全戦略共通の流動性ハード制約（最適化対象外・常時適用。UIには出さない） ----
        # P1-8 是正: 従来 /screener にのみ適用され /screener/dashboard には未適用だった
        # （doc/completed/screener_filter_unification_plan.md §7 P1-8）。
        filters = inject_liquidity_floor(filters, _MIN_AVG_DOLLAR_VOLUME_21)
        if _MIN_AVG_DOLLAR_VOLUME_21 is not None:
            applied_filters.add("min_avg_dollar_volume_21")

        # frame は全プリセットで使い回すため、apply_filters_to_df が書き換える対象は必ず
        # コピーを渡す。df_ind には frame をそのまま渡す（テーマ生値フィルタの当日参照に使われる）。
        # prev_date は実際の前日を渡してよい。frame は load_cross_section が prev_ 列を
        # マージ済みだが、apply_filters_to_df 側に premerged ガード（ランクと対称）を
        # 入れたため二重マージは起きない。
        filtered = apply_filters_to_df(
            merged=frame.copy(),
            target_date=latest_date_result,
            df_ind=frame,
            df_ranks=df_ranks_empty,
            df_symbols=df_symbols,
            df_theme_constituents=df_tc,
            strategy=filters,
            prev_date=previous_date_result,
        )

        # テーマ・仮想指数の最終出力除外は apply_filters_to_df が行う（screener_registry の
        # OUTPUT_EXCLUDED_CATEGORIES に一本化済み）。
        applied_filters.add("exclude_theme_category")

        return filtered, sorted(applied_filters)

    # Load presets from TOML
    presets = _load_presets()

    rise_categories = []
    fall_categories = []

    for p in presets.get("rise", []):
        try:
            filtered_df, applied_filters = _run_preset(p)
            rise_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Check"), items=_fetch_top_8(filtered_df, is_rise=True),
                applied_filters=applied_filters
            ))
        except Exception as e:
            # U-1 (b): 当該プリセットだけをエラーとして返し、他のプリセットは正常表示を続ける
            # （旧実装はここでカテゴリ自体を黙って落としており、それ自体がサイレント失敗だった）
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")
            rise_categories.append(schemas.ScreenerDashboardCategory(
                id=p.get("id", ""), name=p.get("name", ""), subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Check"), items=[],
                error=f"{type(e).__name__}: {e}"
            ))

    for p in presets.get("fall", []):
        try:
            filtered_df, applied_filters = _run_preset(p)
            fall_categories.append(schemas.ScreenerDashboardCategory(
                id=p["id"], name=p["name"], subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Warning"), items=_fetch_top_8(filtered_df, is_rise=False),
                applied_filters=applied_filters
            ))
        except Exception as e:
            logger.error(f"Screener preset '{p.get('id')}' failed: {e}")
            fall_categories.append(schemas.ScreenerDashboardCategory(
                id=p.get("id", ""), name=p.get("name", ""), subname=p.get("subname"),
                subtitle=p.get("subtitle"),
                group=p.get("group", "Warning"), items=[],
                error=f"{type(e).__name__}: {e}"
            ))

    return schemas.ScreenerDashboardResponse(rise=rise_categories, fall=fall_categories)


@router.get("/screener", response_model=List[schemas.ScreenerResultItem])
def get_screener(
    request: Request,
    db: Session = Depends(get_api_db),
    target_date: Optional[str] = Query(None, description="Optional target date YYYY-MM-DD"),
    rrg_leading_in: bool = Query(False),
    rrg_lagging_in: bool = Query(False),
    rrg_improving_in: bool = Query(False),
    rrg_intensity_threshold: float = Query(0.0),
    rs_rank_21_gt_63: bool = Query(False),
    theme_rs21_gt_63: bool = Query(False),
    theme_rs_rank_21_gt_63: bool = Query(False),
    rs_rank_14_gt_21: bool = Query(False),
    theme_rs14_gt_21: bool = Query(False),
    theme_rs_rank_14_gt_21: bool = Query(False),
    require_positive_eps: bool = Query(False),
    expression: Optional[str] = Query(None, description="Expression filter string"),
):
    """Dynamic screener: all min_*/max_*/exact filters via query params, routed through apply_filters_to_df.

    Phase 3 ステップ 3c: SQL のフィルタ組み立てをやめ、基準日1営業日分の ScreenerFrame を
    構築して apply_filters_to_df（唯一のフィルタエンジン）に通す。
    """
    from backend.backtest.backtest_screener import apply_filters_to_df
    from backend.backtest.common_constraints import inject_liquidity_floor
    from backend.backtest.strategy_normalizer import normalize_strategy_keys

    # Determine the date to use for indicators
    if target_date:
        latest_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date <= target_date).scalar()
    else:
        latest_date_result = db.query(func.max(Indicator.date)).scalar()

    if not latest_date_result:
        return []

    # Get previous date for transition check
    previous_date_result = db.query(func.max(Indicator.date)).filter(Indicator.date < latest_date_result).scalar()

    frame, df_tc = load_cross_section(db, latest_date_result, previous_date_result)
    # 旧実装の INNER JOIN（Symbol×Indicator×DailyPrice）と挙動を揃える（get_screener_dashboard と同じ理由）。
    frame = frame[frame['close'].notna()].copy()
    df_symbols = frame[["symbol_id", "category"]].rename(columns={"symbol_id": "id"})
    df_ranks_empty = pd.DataFrame(columns=["symbol_id", "date"])

    # 実際に適用されたフィルタキー一覧（ログ出力用。§5 Phase 1。/screener はエンベロープ化
    # しない方針のため、レスポンスには含めず logger.info のみで記録する）
    _applied_filters = set()
    filters: Dict[str, Any] = {}

    # ---- Dynamic filters from query params ----
    # Collect all query params except reserved ones
    _RESERVED_PARAMS = {"target_date", "rrg_leading_in", "rrg_lagging_in", "rrg_improving_in",
                        "rrg_intensity_threshold",
                        "rs_rank_21_gt_63", "theme_rs21_gt_63", "theme_rs_rank_21_gt_63",
                        "rs_rank_14_gt_21", "theme_rs14_gt_21", "theme_rs_rank_14_gt_21",
                        "require_positive_eps", "preset", "expression"}
    normalized_params = normalize_strategy_keys(dict(request.query_params))

    for key, value in normalized_params.items():
        if key in _RESERVED_PARAMS:
            continue
        if not value:
            continue
        # 未知キーはこれまで通り黙ってスキップする（/screener はプリセット単位のエラー分離が
        # 無いため、U-1 (b) の fail-loud 対象外。旧 _apply_filter も except Exception で
        # キー単位に黙って無視していた挙動を維持する）。
        try:
            spec = screener_registry.resolve_filter_spec(key, _KNOWN_FILTER_COLUMNS, _RANK_FILTER_COLUMNS)
        except screener_registry.UnknownFilterKeyError:
            logger.warning(f"Screener filter '{key}={value}' skipped: unknown key")
            continue
        if spec.kind == 'special':
            # 特殊フィルタは専用のクエリ引数（9種、下記）でのみ有効化する（旧実装と同じ挙動。
            # 生の動的パラメータとしては常に _resolve_column が None を返し無視されていた）。
            continue
        if spec.kind == 'close_gt':
            filters[key] = True
        else:
            try:
                filters[key] = float(value)
            except (TypeError, ValueError):
                logger.warning(f"Screener filter '{key}={value}' skipped: invalid numeric value")
                continue
        _applied_filters.add(key)

    # ---- Expression filter ----
    if expression:
        filters["expression"] = expression
        _applied_filters.add("expression")

    # ---- Special boolean filters ----
    if rs_rank_21_gt_63: filters["is_rs_ratio_rank_e21_gt_e63"] = True
    if theme_rs21_gt_63: filters["is_theme_rs_ratio_e21_gt_e63"] = True
    if theme_rs_rank_21_gt_63: filters["is_theme_rs_ratio_rank_e21_gt_e63"] = True
    if rs_rank_14_gt_21: filters["is_rs_ratio_rank_e14_gt_e21"] = True
    if theme_rs14_gt_21: filters["is_theme_rs_ratio_e14_gt_e21"] = True
    if theme_rs_rank_14_gt_21: filters["is_theme_rs_ratio_rank_e14_gt_e21"] = True
    if rrg_leading_in: filters["rrg_leading_in"] = True
    if rrg_lagging_in: filters["rrg_lagging_in"] = True
    if rrg_improving_in: filters["rrg_improving_in"] = True
    if rrg_intensity_threshold:
        filters["rrg_intensity_threshold"] = rrg_intensity_threshold
    _applied_filters |= {k for k in (
        "is_rs_ratio_rank_e21_gt_e63", "is_theme_rs_ratio_e21_gt_e63",
        "is_theme_rs_ratio_rank_e21_gt_e63", "is_rs_ratio_rank_e14_gt_e21",
        "is_theme_rs_ratio_e14_gt_e21", "is_theme_rs_ratio_rank_e14_gt_e21",
        "rrg_leading_in", "rrg_lagging_in", "rrg_improving_in",
    ) if k in filters}

    # ---- 全戦略共通の流動性ハード制約（最適化対象外・常時適用。UIには出さない） ----
    filters = inject_liquidity_floor(filters, _MIN_AVG_DOLLAR_VOLUME_21)
    if _MIN_AVG_DOLLAR_VOLUME_21 is not None:
        _applied_filters.add("min_avg_dollar_volume_21")

    # applied_filters: /screener はレスポンスをリストで返す既存契約（エンベロープ化しない）
    # ため、実際に適用されたフィルタキー一覧はログにのみ記録する（U-1 決定に基づく制約。
    # doc/completed/screener_filter_unification_plan.md §5 Phase 1）
    logger.info(f"GET /screener applied_filters: {sorted(_applied_filters)}")

    # prev_date は実際の前日を渡す（apply_filters_to_df 側の premerged ガードにより
    # 二重マージは起きない）。
    filtered = apply_filters_to_df(
        merged=frame.copy(),
        target_date=latest_date_result,
        df_ind=frame,
        df_ranks=df_ranks_empty,
        df_symbols=df_symbols,
        df_theme_constituents=df_tc,
        strategy=filters,
        prev_date=previous_date_result,
    )

    if require_positive_eps:
        target_eval_date = target_date if target_date else dt_date.today().strftime('%Y-%m-%d')
        latest_earnings_subq = db.query(
            Earning.symbol_id,
            func.max(Earning.period_date).label('max_date')
        ).filter(Earning.period_date <= target_eval_date).group_by(Earning.symbol_id).subquery()
        eps_filter_subq = db.query(Earning.symbol_id).join(
            latest_earnings_subq,
            (Earning.symbol_id == latest_earnings_subq.c.symbol_id) &
            (Earning.period_date == latest_earnings_subq.c.max_date)
        ).filter(Earning.eps_basic > 0).subquery()
        valid_ids = {r[0] for r in db.query(eps_filter_subq.c.symbol_id).all()}
        filtered = filtered[filtered['symbol_id'].isin(valid_ids)]

    if filtered.empty:
        return []

    # Slice to top 200 items by rs_ratio_21_rank (frame 内名: rs21_rank) before querying
    # price history (huge optimization!). 欠損は 0.0 として扱う（旧 rank_map.get(id, 0.0) と同じ）。
    sort_key = filtered['rs21_rank'].fillna(0.0) if 'rs21_rank' in filtered.columns else 0.0
    filtered = filtered.assign(_sort_key=sort_key).sort_values(
        '_sort_key', ascending=False
    ).drop(columns='_sort_key').head(200)

    symbol_ids = [int(s) for s in filtered['symbol_id'].tolist()]

    # Sparkline data: Need the past 21 days only for the top 200 sliced symbols
    start_date_sparkline = latest_date_result - timedelta(days=40)
    history = db.query(DailyPrice.symbol_id, DailyPrice.close, DailyPrice.date).filter(
        DailyPrice.symbol_id.in_(symbol_ids),
        DailyPrice.date >= start_date_sparkline,
        DailyPrice.date <= latest_date_result
    ).order_by(DailyPrice.date.asc()).all()

    history_map = {}
    for h in history:
        if h.symbol_id not in history_map:
            history_map[h.symbol_id] = []
        history_map[h.symbol_id].append(h.close)

    def _int_or_none(v):
        return None if pd.isna(v) else int(v)

    def _float_or(v, default=0.0):
        return default if pd.isna(v) else float(v)

    def _float_or_none(v):
        return None if pd.isna(v) else float(v)

    # Build response
    out = []
    for row in filtered.itertuples():
        sid = int(row.symbol_id)
        hist_prices = history_map.get(sid, [])
        hist_prices = hist_prices[-21:] if len(hist_prices) > 21 else hist_prices

        c_1d = _float_or(row.change_1d_pct)
        c_1w = _float_or(row.change_1w_pct)
        c_1m = _float_or(row.change_1m_pct)

        d_21ema = _float_or(getattr(row, 'dist_21ema_pct', float('nan')))

        rank_21 = _float_or(getattr(row, 'rs21_rank', float('nan')))
        rank_63 = _float_or(getattr(row, 'rs63_rank', float('nan')))

        sparkline_data = []
        if hist_prices:
            m_min, m_max = min(hist_prices), max(hist_prices)
            rng = m_max - m_min
            if rng > 0:
                sparkline_data = [(p - m_min) / rng for p in hist_prices]
            else:
                sparkline_data = [0.5 for _ in hist_prices]

        out.append(schemas.ScreenerResultItem(
            id=sid,
            ticker=row.ticker,
            name=row.name,
            category=row.category,
            close=float(row.close),
            change_pct=c_1d,
            change_1w_pct=c_1w,
            change_1m_pct=c_1m,
            dist_21ema_pct=d_21ema,
            rs_ratio_21_rank=rank_21,
            rs_ratio_63_rank=rank_63,
            rs_ratio_rank_e21=rank_21,
            rs_ratio_rank_e63=rank_63,
            rs_ratio_21=_float_or_none(row.rs_ratio_e21),
            rs_ratio_63=_float_or_none(row.rs_ratio_e63),
            rs_ratio_e21=_float_or_none(row.rs_ratio_e21),
            rs_ratio_e63=_float_or_none(row.rs_ratio_e63),
            rs_momentum_21=_float_or_none(row.rs_momentum_e21),
            rs_momentum_e21=_float_or_none(row.rs_momentum_e21),
            sparkline=sparkline_data,
            vol_surge_21=_float_or_none(row.vol_surge_21),
            adr_pct_21=_float_or_none(row.adr_pct_21),
            sma50_atr_mult=_float_or_none(row.sma50_atr_mult),
            dist_sma50_atr=_float_or_none(row.sma50_atr_mult),
            is_trend_template=_int_or_none(row.is_trend_template),
            trend_template_ok=_int_or_none(row.is_trend_template),
            market_cap=_float_or_none(row.market_cap),
            up_down_vol_ratio_50=_float_or_none(row.up_down_vol_ratio_50),
            is_rs_blue_dot=_int_or_none(row.is_rs_blue_dot),
            rs_blue_dot=_int_or_none(row.is_rs_blue_dot),
            is_rs_red_dot=_int_or_none(row.is_rs_red_dot),
            rs_red_dot=_int_or_none(row.is_rs_red_dot),
            vcr=_float_or_none(row.vcr),
            vol_accum_days_5=_int_or_none(row.vol_accum_days_5)
        ))

    out.sort(key=lambda x: x.rs_ratio_21_rank, reverse=True)
    return out
