"""スクリーナー経路間パリティテスト（レジストリ駆動。計画書 §3.2.1 / §5 Phase 2）。

`doc/in_progress/screener_filter_unification_plan.md` の Phase 2 に対応する。
①フロントのスクリーナー API（SQLite・SQLAlchemy）と ②③バックテスト経路（Parquet・pandas）が、
`indicators/screener_registry.py` の全フィルタキーについて同じ symbol_id 集合を返すことを検証する。

設計方針（§3.2.1 (a)）:
    「1つの真実」からのデータセット（_PARITY_DATASET 系のモジュール変数）を1回だけ定義し、
    そこから seed_sqlite()（API経路用）と to_dataframes()（バックテスト経路用）の
    2つの供給形式へ**機械的に**書き出す。ランクの long/wide 変換もこの場所で行い、
    フィクスチャを手書きで2つ並べない（食い違うと検証の意味が消えるため）。

このテストが実際に不一致を検出できることの実証（計画書の完了条件3）は、
`backend/backtest/backtest_screener.py::apply_filters_to_df` の bool_column 解決を
一時的に破壊して確認済み（実施ログはワーカーの完了報告を参照。プロダクションコードは
検証後に必ず元へ戻している）。
"""
from __future__ import annotations

import glob
import os
from datetime import date

import pandas as pd
import pytest
import tomli
from sqlalchemy import Integer, SmallInteger, create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, DailyPrice, Indicator, RelativeRank, Symbol, ThemeConstituent
from indicators.screener_registry import EXPLICIT_SPECS, is_non_filter_key
from backend.backtest.backtest_screener import apply_filters_to_df
from backend.backtest.common_constraints import load_min_avg_dollar_volume_21

# ============================================================
# 日付・シンボル定義
# ============================================================
PREV = date(2026, 6, 30)
LATEST = date(2026, 7, 1)

STOCK_IDS = [1, 2, 3, 4, 5, 6]         # 個別銘柄（STK1〜STK6）
THEME_A_ID = 901                        # 強気テーマ（e21>e63 等、常にリーディング側）
THEME_B_ID = 902                        # 弱気テーマ（常にラギング側）

# ============================================================
# Indicator / RelativeRank の「全カラムに値を入れる」ためのデフォルト行
# ============================================================
# P1-2（計画書 §7）: 列が丸ごと NULL だと pandas が object dtype に推論し、
# 特殊フィルタの数値比較が TypeError になる。最小フィクスチャではこれが実際に起きるため、
# 全カラムへ明示的にデフォルト値を入れる（本番は T3/T4 が一括で埋めるため未発生）。
_IND_COL_NAMES = [c.name for c in Indicator.__table__.columns if c.name not in ("id", "symbol_id", "date")]
_RANK_COL_NAMES = [
    c.name for c in RelativeRank.__table__.columns
    if c.name not in ("id", "symbol_id", "date", "group_name")
]


def _default_indicator_row() -> dict:
    row = {}
    for c in Indicator.__table__.columns:
        if c.name in ("id", "symbol_id", "date"):
            continue
        if isinstance(c.type, (Integer, SmallInteger)):
            row[c.name] = 0
        else:
            row[c.name] = 1.0
    return row


def _default_rank_row() -> dict:
    return {name: 0.5 for name in _RANK_COL_NAMES}


# ============================================================
# 個別銘柄（STK1〜STK6）の値生成
# ============================================================
# t = symbol_id (1..6) を「階層」として使う。t>=4 を「良い」側、t<=3 を「悪い」側とする
# 2値パターンと、t をそのまま連続値として使う線形パターンを併用し、各フィルタキーについて
# 「通過する銘柄と落ちる銘柄が両方出る」閾値を選ぶ（§3.2.1 (c)）。
def _stock_today_overrides(t: int) -> dict:
    hi = t >= 4
    ma_shared = 90.0 if hi else 110.0  # close(=100) との大小関係で close_gt_* 48種を一括制御
    return {
        # --- close_gt_* / is_close_gt_* 群（12列とも同一値。close=100 との比較のみが意味を持つ） ---
        "sma_5": ma_shared, "sma_21": ma_shared, "sma_50": ma_shared,
        "sma_63": ma_shared, "sma_150": ma_shared, "sma_200": ma_shared,
        "ema_5": ma_shared, "ema_21": ma_shared, "ema_50": ma_shared,
        "ema_63": ma_shared, "ema_150": ma_shared, "ema_200": ma_shared,
        # --- 連続値（t=1..6, 閾値は t=3/4 の中間） ---
        "adr_pct_21": float(t),
        "sma50_atr_mult": float(t),
        "td9": {1: -3, 2: -2, 3: -1, 4: 1, 5: 2, 6: 3}[t],
        "vcr": t * 0.2,
        "change_1m_pct": t * 2 - 7,
        "change_1w_pct": t * 1 - 3.5,
        "up_down_vol_ratio_50": t * 0.5,
        "vol_surge_rel_spy_21": t * 0.3,
        "rs_trend_s21": float(t),
        # --- 2値パターン（hi 側が「良い」） ---
        "change_1d_pct": 5.0 if hi else -1.0,
        "vol_surge_21": 2.0 if hi else 0.5,
        "dist_52w_high_pct": -1.0 if hi else -20.0,
        "dist_63d_high_pct": -1.0 if hi else -20.0,
        "is_trend_template": 1 if hi else 0,
        "rs_macd_hist_21": 0.5 if hi else -0.2,
        "vol_accum_days_5": 4 if hi else 1,
        # --- 個別パターン ---
        "rs_trend_s14": (float(t) - 0.5) if t <= 3 else (float(t) + 0.5),
        "rs_trend_s63": 3.5,
        "is_rs_blue_dot": 1 if t % 2 == 1 else 0,
        # RRG（rs_ratio_e21 / rs_momentum_e21）。STK1=leading_in, STK2=lagging_in,
        # STK3=improving_in をそれぞれ単独で成立させ、STK4-6 は無転換（安全値）にする。
        "rs_ratio_e21": {1: 0.5, 2: -0.5, 3: -0.3}.get(t, 0.1),
        "rs_momentum_e21": {1: 0.5, 2: -0.5, 3: 0.3}.get(t, 0.1),
        # 全戦略共通の流動性ハード制約を邪魔しないよう、十分大きい値にしておく
        # （床自体は test_liquidity_floor_excludes_illiquid_symbol_on_both_routes で別途検証）
        "avg_dollar_volume_21": 5e6,
    }


def _stock_prev_overrides(t: int) -> dict:
    return {
        "rs_ratio_e21": {1: -0.2, 2: 0.5, 3: -0.5}.get(t, 0.1),
        "rs_momentum_e21": {1: -0.2, 2: 0.5, 3: -0.1}.get(t, 0.1),
        "rs_macd_hist_21": 0.1,
        # VCP breakout の前日条件（収縮・高値圏の土台）は全銘柄で満たしておき、
        # 当日側の条件（is_trend_template 等の2値パターン）だけで合否を分ける。
        "vcr": 0.5,
        "dist_52w_high_pct": -5.0,
        "dist_63d_high_pct": -5.0,   # fail-loud のカラム充足確認用（pivot_tol 未指定時は未使用）
        "vol_surge_21": 1.0,          # 同上
        "avg_dollar_volume_21": 5e6,
    }


def _stock_rank_overrides(t: int) -> dict:
    e21 = 0.1 * t
    e14 = e21 + (0.05 if t % 2 == 1 else -0.05)
    return {
        "rs_ratio_rank_e14": e14,
        "rs_ratio_rank_e21": e21,
        "rs_ratio_rank_e63": 0.35,
        "rs_trend_rank_s21": 0.1 * t,
    }


def _stock_price(t: int) -> dict:
    hi = t >= 4
    return {
        "open": 95.0 if hi else 105.0,   # change_intraday_pct 用（close=100 固定）
        "high": 106.0,
        "low": 94.0,
        "close": 100.0,
        "volume": 1_000_000,
        "market_cap": t * 2e8,
    }


# ============================================================
# テーマ（901=強気, 902=弱気）の値生成
# ============================================================
def _theme_today_overrides(leading: bool) -> dict:
    return {
        "avg_dollar_volume_21": 5e6,
        "rs_ratio_e21": 0.5 if leading else 0.1,
        "rs_ratio_e63": 0.1 if leading else 0.5,
        "rs_ratio_e14": 0.6 if leading else 0.05,
        "rs_trend_s21": 1.5 if leading else 0.5,
    }


def _theme_prev_overrides() -> dict:
    return {
        "rs_ratio_e21": 0.2, "rs_momentum_e21": 0.1, "rs_macd_hist_21": 0.1,
        "vcr": 0.5, "dist_52w_high_pct": -5.0, "dist_63d_high_pct": -5.0,
        "vol_surge_21": 1.0, "avg_dollar_volume_21": 5e6,
    }


def _theme_rank_overrides(leading: bool) -> dict:
    if leading:
        return {
            "rs_ratio_rank_e14": 0.95, "rs_ratio_rank_e21": 0.9, "rs_ratio_rank_e63": 0.3,
            "rs_trend_rank_s14": 0.9, "rs_trend_rank_s21": 0.7, "rs_trend_rank_s63": 0.3,
        }
    return {
        "rs_ratio_rank_e14": 0.1, "rs_ratio_rank_e21": 0.3, "rs_ratio_rank_e63": 0.9,
        "rs_trend_rank_s14": 0.2, "rs_trend_rank_s21": 0.5, "rs_trend_rank_s63": 0.8,
    }


def _theme_price() -> dict:
    return {"open": 100.0, "high": 105.0, "low": 95.0, "close": 100.0,
            "volume": 100_000, "market_cap": None}


# ============================================================
# 「1つの真実」の組み立て（モジュール変数として1回だけ構築）
# ============================================================
SYMBOLS = (
    [{"id": t, "ticker": f"STK{t}", "name": f"Stock {t}", "category": "個別"} for t in STOCK_IDS]
    + [
        {"id": THEME_A_ID, "ticker": "_THA_", "name": "強気テーマ", "category": "テーマ"},
        {"id": THEME_B_ID, "ticker": "_THB_", "name": "弱気テーマ", "category": "テーマ"},
    ]
)

# STK1, STK2 は強気テーマの構成銘柄。STK3, STK4 は弱気テーマの構成銘柄。STK5, STK6 は無所属。
THEME_CONSTITUENTS = [
    (THEME_A_ID, 1), (THEME_A_ID, 2),
    (THEME_B_ID, 3), (THEME_B_ID, 4),
]

_INDICATOR_BY_DATE: dict = {LATEST: {}, PREV: {}}
_RANK_BY_ID: dict = {}
_PRICE_BY_ID: dict = {}

for _t in STOCK_IDS:
    _INDICATOR_BY_DATE[LATEST][_t] = {**_default_indicator_row(), **_stock_today_overrides(_t)}
    _INDICATOR_BY_DATE[PREV][_t] = {**_default_indicator_row(), **_stock_prev_overrides(_t)}
    _RANK_BY_ID[_t] = {**_default_rank_row(), **_stock_rank_overrides(_t)}
    _PRICE_BY_ID[_t] = _stock_price(_t)

_INDICATOR_BY_DATE[LATEST][THEME_A_ID] = {**_default_indicator_row(), **_theme_today_overrides(leading=True)}
_INDICATOR_BY_DATE[LATEST][THEME_B_ID] = {**_default_indicator_row(), **_theme_today_overrides(leading=False)}
_INDICATOR_BY_DATE[PREV][THEME_A_ID] = {**_default_indicator_row(), **_theme_prev_overrides()}
_INDICATOR_BY_DATE[PREV][THEME_B_ID] = {**_default_indicator_row(), **_theme_prev_overrides()}
_RANK_BY_ID[THEME_A_ID] = {**_default_rank_row(), **_theme_rank_overrides(leading=True)}
_RANK_BY_ID[THEME_B_ID] = {**_default_rank_row(), **_theme_rank_overrides(leading=False)}
_PRICE_BY_ID[THEME_A_ID] = _theme_price()
_PRICE_BY_ID[THEME_B_ID] = _theme_price()


def seed_sqlite(session) -> None:
    """①API経路（SQLite）へ「1つの真実」を投入する。"""
    for sym in SYMBOLS:
        session.add(Symbol(id=sym["id"], ticker=sym["ticker"], name=sym["name"],
                            category=sym["category"], active=1))
    for theme_id, symbol_id in THEME_CONSTITUENTS:
        session.add(ThemeConstituent(theme_id=theme_id, symbol_id=symbol_id, weight=1.0))
    for sym in SYMBOLS:
        price = _PRICE_BY_ID[sym["id"]]
        session.add(DailyPrice(symbol_id=sym["id"], date=LATEST, **price))
    for d, ind_map in _INDICATOR_BY_DATE.items():
        for sid, row in ind_map.items():
            session.add(Indicator(symbol_id=sid, date=d, **row))
    for sid, row in _RANK_BY_ID.items():
        group_name = "テーマ" if sid in (THEME_A_ID, THEME_B_ID) else "個別"
        session.add(RelativeRank(symbol_id=sid, date=LATEST, group_name=group_name, **row))
    session.commit()


def to_dataframes():
    """②③バックテスト経路（pandas）へ「1つの真実」を書き出す。

    ランクは SQLite・バックテストとも wide 形式（P0-1/P0-2 解消済み。melt を廃止したため）。
    _RANK_BY_ID（symbol_id -> {canonical: value}）をそのまま wide の行に変換するだけでよい。
    """
    df_symbols = pd.DataFrame([
        {"id": s["id"], "ticker": s["ticker"], "name": s["name"], "category": s["category"], "active": 1}
        for s in SYMBOLS
    ])
    df_tc = pd.DataFrame(THEME_CONSTITUENTS, columns=["theme_id", "symbol_id"])

    price_rows = [{"symbol_id": sym["id"], "date": LATEST, **_PRICE_BY_ID[sym["id"]]} for sym in SYMBOLS]
    df_prices = pd.DataFrame(price_rows)

    ind_rows = []
    for d, ind_map in _INDICATOR_BY_DATE.items():
        for sid, row in ind_map.items():
            ind_rows.append({"symbol_id": sid, "date": d, **row})
    df_ind = pd.DataFrame(ind_rows)

    rank_rows = [{"symbol_id": sid, "date": LATEST, **row} for sid, row in _RANK_BY_ID.items()]
    df_ranks = pd.DataFrame(rank_rows)

    return df_symbols, df_prices, df_ind, df_ranks, df_tc


_FRAMES = to_dataframes()  # 読み取り専用。全テストで共有する。

N_INDIVIDUAL = sum(1 for s in SYMBOLS if s["category"] == "個別")  # = 6


# ============================================================
# 両経路の実行ヘルパー
# ============================================================
def _run_api_path(session, filters: dict) -> set:
    """①API経路（get_screener_dashboard）でフィルタを適用し、通過ticker集合を返す。

    P0-5 対策（計画書 §7）: target_date は必ず明示的に渡す。省略すると FastAPI の
    Query() デフォルトオブジェクトがそのまま使われ、比較演算で壊れる。
    """
    import api.screener_router as router_module

    fake_presets = {
        "rise": [{"id": "parity", "name": "parity", "group": "Check", "filters": dict(filters)}],
        "fall": [],
    }
    original_load_presets = router_module._load_presets
    router_module._load_presets = lambda: fake_presets
    try:
        resp = router_module.get_screener_dashboard(db=session, target_date=str(LATEST))
    finally:
        router_module._load_presets = original_load_presets

    cat = resp.rise[0]
    # error が None であることを必ず確認する。例外がエラーとして返っていると
    # 「0件同士で一致」してしまい、パリティ検証にならない。
    assert cat.error is None, f"API経路がエラーを返した: filters={filters} error={cat.error}"
    # top-8 打ち切りが起きていないことの確認（母集団は個別6件なので必ず8未満のはず）
    assert len(cat.items) < 8, f"top-8打ち切りの疑い: filters={filters} items={len(cat.items)}"
    return {item.ticker for item in cat.items}


def _run_backtest_path(filters: dict, frames=None, prev_date=PREV) -> set:
    """②③バックテスト経路（apply_filters_to_df）でフィルタを適用し、通過ticker集合を返す。"""
    df_symbols, df_prices, df_ind, df_ranks, df_tc = frames if frames is not None else _FRAMES
    ind_day = df_ind[df_ind["date"] == LATEST].copy()
    price_day = df_prices[df_prices["date"] == LATEST].copy()
    price_cols = ["symbol_id", "date", "open", "high", "low", "close", "volume", "market_cap"]
    merged = ind_day.merge(price_day[price_cols], on=["symbol_id", "date"], how="inner")
    merged = merged.merge(
        df_symbols[["id", "ticker", "name", "category", "active"]],
        left_on="symbol_id", right_on="id", how="inner",
    )
    filtered = apply_filters_to_df(
        merged=merged,
        target_date=LATEST,
        df_ind=df_ind,
        df_ranks=df_ranks,
        df_symbols=df_symbols,
        df_theme_constituents=df_tc,
        strategy=dict(filters),
        prev_date=prev_date,
    )
    return set(filtered["ticker"].tolist())


# ============================================================
# レジストリ駆動の PARITY_CASES（§3.2.1 (c)）
# ============================================================
PARITY_CASES: dict = {
    # --- numeric (min_/max_) ---
    "max_adr_pct_21": 3.5,
    "max_change_1d_pct": 0.0,
    "max_dist_21ema_pct": 0.0,
    "max_sma50_atr_mult": 3.5,
    "max_td9": 0,
    "max_vcr": 0.7,
    "max_vol_surge_21": 1.0,
    "min_adr_pct_21": 3.5,
    "min_change_1d_pct": 0.0,
    "min_change_1m_pct": 0.0,
    "min_change_1w_pct": 0.0,
    "min_change_intraday_pct": 0.0,
    "min_dist_21ema_pct": 0.0,
    "min_dist_52w_high_pct": -10.0,
    "min_dist_63d_high_pct": -10.0,
    "min_market_cap": 7e8,
    "min_rs_macd_hist_21": 0.0,
    "min_rs_trend_s21": 3.5,
    "min_sma50_atr_mult": 3.5,
    "min_td9": 0,
    "min_up_down_vol_ratio_50": 1.75,
    "min_vol_accum_days_5": 2,
    "min_vol_surge_21": 1.0,
    "min_vol_surge_rel_spy_21": 1.05,
    # --- rank ---
    "min_rs_ratio_rank_e14": 0.35,
    "min_rs_ratio_rank_e21": 0.35,
    "min_rs_trend_rank_s21": 0.35,
    # --- theme_rank / theme_numeric ---
    "min_theme_rs_ratio_rank_e21": 0.5,
    "min_theme_rs_trend_s21": 1.0,
    # --- bool_column ---
    "is_rs_blue_dot": True,
    "is_trend_template": True,
}
# EXPLICIT_SPECS 由来のキー（special・close_gt）は機械的に True を割り当てる。
# どちらも「フィルタが有効かどうか」を示す boolean フラグとしてのみ使われ、個別の閾値を
# 持たないため、手書きで63行複製するとレジストリとの食い違いが起きうる箇所を機械導出にする。
for _key, _spec in EXPLICIT_SPECS.items():
    if _spec.kind in ("special", "close_gt"):
        PARITY_CASES[_key] = True

# 意図した差異（S-1/S-2等）を理由付きで登録する場所。D-2 で両側が統一済みのため初期値は空。
# ここに無いキーの不一致は全て失敗として扱う。
EXPECTED_DIVERGENCE: dict = {}


# ============================================================
# 対象キーの棚卸し（EXPLICIT_SPECS ＋ 実データ TOML の和集合）
# ============================================================
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_TEST_DIR)))
_PRESET_GLOB = os.path.join(_PROJECT_ROOT, "data", "screener_presets*.toml")
_BACKTEST_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "backend", "backtest", "backtest_config.toml")


def _collect_required_keys() -> set:
    keys = set(EXPLICIT_SPECS.keys())
    for fn in glob.glob(_PRESET_GLOB):
        with open(fn, "rb") as f:
            data = tomli.load(f)
        for section in ("rise", "fall"):
            for item in data.get(section, []):
                keys |= set(item.get("filters", {}).keys())
    if os.path.exists(_BACKTEST_CONFIG_PATH):
        with open(_BACKTEST_CONFIG_PATH, "rb") as f:
            bt_data = tomli.load(f)
        for strat in bt_data.get("strategy", []):
            keys |= set(strat.keys())
            keys |= set(strat.get("optimization", {}).keys())
    return {k for k in keys if not is_non_filter_key(k)}


def test_parity_cases_cover_all_required_keys():
    """レジストリで解決可能な全キー（EXPLICIT_SPECS）＋実データで使われている全キーが
    PARITY_CASES に登録されていること。ここに無いキーは実装漏れ（fail-loud で検知すべき
    対象が未検証のまま）としてこのテストを失敗させる（計画書 §3.2.1 (c)）。
    """
    required = _collect_required_keys()
    missing = required - set(PARITY_CASES.keys())
    assert not missing, f"PARITY_CASES に無いキー（実装漏れの疑い）: {sorted(missing)}"


# ============================================================
# DB フィクスチャ（メインデータセット）
# ============================================================
_engine = create_engine(
    "sqlite:///file:screener_parity_mem?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
_TestSession = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


@pytest.fixture()
def db_session():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    s = _TestSession()
    seed_sqlite(s)
    yield s
    s.close()


# ============================================================
# レジストリ駆動のパリティテスト本体
# ============================================================
@pytest.mark.parametrize("key", sorted(PARITY_CASES.keys()))
def test_parity_per_key(db_session, key):
    """PARITY_CASES の各キーについて、API経路とバックテスト経路が同じ symbol 集合を返すこと。"""
    value = PARITY_CASES[key]
    filters = {key: value}

    api_tickers = _run_api_path(db_session, filters)
    bt_tickers = _run_backtest_path(filters)

    if key in EXPECTED_DIVERGENCE:
        pytest.skip(f"意図した差異として登録済み: {EXPECTED_DIVERGENCE[key]}")

    assert api_tickers == bt_tickers, (
        f"経路間パリティ不一致: key={key!r} value={value!r} "
        f"API={sorted(api_tickers)} backtest={sorted(bt_tickers)}"
    )
    # 全通過でも全落ちでもないこと。両側が壊れていても「0件同士」「全件同士」で
    # 一致してしまうと検証にならない（計画書 §3.2.1 (c)）。
    assert 0 < len(api_tickers) < N_INDIVIDUAL, (
        f"key={key!r}: 通過件数が境界的でない（全通過/全落ちの疑い）: {sorted(api_tickers)}"
    )


# ============================================================
# 全経路共通ルールの検証（§3.2.1 (d)。キー単位のパリティでは捕まらない）
# ============================================================
def test_theme_rows_excluded_but_theme_filters_still_work(db_session):
    """テーマ混入バグ（計画書 §1.2）の再発防止テスト。

    - どんなフィルタでも category=='テーマ' の銘柄は両経路の最終出力に1件も出ない。
    - にもかかわらず is_theme_* フィルタ自体は正しく機能する
      （＝テーマ行はフィルタ処理の中間データとしては保持されている）。
    この2つを同時に満たすのが正しい状態。
    """
    wide_filters = {"min_change_1d_pct": -1000.0}  # ほぼ無条件（個別6件は全通過するはず）
    api_tickers = _run_api_path(db_session, wide_filters)
    bt_tickers = _run_backtest_path(wide_filters)
    for theme_ticker in ("_THA_", "_THB_"):
        assert theme_ticker not in api_tickers, f"{theme_ticker} がAPI出力に混入している"
        assert theme_ticker not in bt_tickers, f"{theme_ticker} がbacktest出力に混入している"
    assert api_tickers == bt_tickers == {f"STK{t}" for t in STOCK_IDS}

    # is_theme_* フィルタ自体が正しく機能すること（テーマ行が中間データとして生きている証拠）
    theme_filters = {"is_theme_rs_ratio_e21_gt_e63": True}
    api_theme_tickers = _run_api_path(db_session, theme_filters)
    bt_theme_tickers = _run_backtest_path(theme_filters)
    assert api_theme_tickers == bt_theme_tickers == {"STK1", "STK2"}


# --- 流動性床（独立データセット。PARITY_CASES の閾値と混ざらないよう分離する） ---
_LIQ_LIQUID_ID = 1
_LIQ_ILLIQUID_ID = 2


def _liquidity_frames():
    df_symbols = pd.DataFrame([
        {"id": _LIQ_LIQUID_ID, "ticker": "LIQOK", "name": "Liquid", "category": "個別", "active": 1},
        {"id": _LIQ_ILLIQUID_ID, "ticker": "LIQNG", "name": "Illiquid", "category": "個別", "active": 1},
    ])
    df_prices = pd.DataFrame([
        {"symbol_id": _LIQ_LIQUID_ID, "date": LATEST, "open": 100.0, "high": 105.0, "low": 95.0,
         "close": 100.0, "volume": 100_000, "market_cap": 1e9},
        {"symbol_id": _LIQ_ILLIQUID_ID, "date": LATEST, "open": 100.0, "high": 105.0, "low": 95.0,
         "close": 100.0, "volume": 100_000, "market_cap": 1e9},
    ])
    rows = []
    for sid, adv in ((_LIQ_LIQUID_ID, 5e6), (_LIQ_ILLIQUID_ID, 500_000.0)):
        rows.append({**_default_indicator_row(), "symbol_id": sid, "date": LATEST,
                     "avg_dollar_volume_21": adv, "change_1d_pct": 1.0})
    df_ind = pd.DataFrame(rows)
    df_ranks = pd.DataFrame(columns=["symbol_id", "date"])
    df_tc = pd.DataFrame(columns=["theme_id", "symbol_id"])
    return df_symbols, df_prices, df_ind, df_ranks, df_tc


def _seed_liquidity_dataset(session) -> None:
    session.add_all([
        Symbol(id=_LIQ_LIQUID_ID, ticker="LIQOK", name="Liquid", category="個別", active=1),
        Symbol(id=_LIQ_ILLIQUID_ID, ticker="LIQNG", name="Illiquid", category="個別", active=1),
    ])
    for sid, adv in ((_LIQ_LIQUID_ID, 5e6), (_LIQ_ILLIQUID_ID, 500_000.0)):
        session.add(DailyPrice(symbol_id=sid, date=LATEST, open=100.0, high=105.0, low=95.0,
                                close=100.0, volume=100_000, market_cap=1e9))
        row = {**_default_indicator_row(), "avg_dollar_volume_21": adv, "change_1d_pct": 1.0}
        session.add(Indicator(symbol_id=sid, date=LATEST, **row))
    session.commit()


@pytest.fixture()
def db_session_liquidity():
    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)
    s = _TestSession()
    _seed_liquidity_dataset(s)
    yield s
    s.close()


def test_liquidity_floor_excludes_illiquid_symbol_on_both_routes(db_session_liquidity):
    """流動性床（min_avg_dollar_volume_21）未満の銘柄が両経路の出力に出ないこと。

    キー単位のパリティでは検証できない「全経路共通ルール」のため独立して検証する
    （計画書 §3.2.1 (d)。P1-8: 過去に /screener/dashboard だけ床が未適用だった実障害の再発防止）。
    """
    floor = load_min_avg_dollar_volume_21()
    assert floor is not None, "backtest_config.toml の [general].min_avg_dollar_volume_21 が読めない"

    # API 側は _build_preset_query 内で常時適用されるため、フィルタに書かなくても効く
    api_tickers = _run_api_path(db_session_liquidity, {"min_change_1d_pct": -1000.0})
    assert "LIQOK" in api_tickers
    assert "LIQNG" not in api_tickers

    # バックテスト側（apply_filters_to_df 単体）には床の自動注入は無い
    # （common_constraints.inject_liquidity_floor 経由で戦略へ注入されて初めて効く設計）。
    # そのため明示的に min_avg_dollar_volume_21 をフィルタとして渡す。
    bt_tickers = _run_backtest_path(
        {"min_change_1d_pct": -1000.0, "min_avg_dollar_volume_21": floor},
        frames=_liquidity_frames(),
        prev_date=None,
    )
    assert "LIQOK" in bt_tickers
    assert "LIQNG" not in bt_tickers
