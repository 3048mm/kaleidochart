"""Parquet 上で T3 / T4 を再計算するための共通部品。

## なぜ必要か

T4（`relative_ranks`）は **SQLite の `indicators` に存在する日付しか計算できない**。
SQLite はホットキャッシュとして直近730日しか持たないため、
`update_pipeline.py --rebuild-from T4` を回しても**直近730日の順位しか作られない**。

2026-08-05 に改称6件を適用した際、この経路で5時間かけて再構築したが
`T3=2096 / T4=500` にしかならなかった。全期間を埋めるには
`run_production_restore.py`（SQLite を全期間復元）→ `--rebuild-from T4` の
2段階が必要で、約3.5時間かかる。

**Parquet 上で直接再計算すれば全期間を約4分で作れる**（SQLite への接続はゼロ）。

同じ能力が独立に3箇所で必要になるため部品化する。

  - 改称・新規銘柄追加後の T4 補完
  - 分割・併合の価格補正（`doc/in_progress/split_anomaly_noise_reduction_plan.md`）
  - `restore_truncated_symbol_history.py`（従来は T4 を外部に投げていた）

詳細: `doc/in_progress/parquet_recompute_plan.md`
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# T4 の対象外カテゴリ。`pipeline/phases/t4_ranks.py` の WHERE 句と一致させること。
#   レバレッジ: 原資産の増幅であり単独比較に意味がない
#   指標      : 資産クラスがばらばらでパーセンタイルに解釈可能な意味がない
RANK_EXCLUDED_CATEGORIES = ("レバレッジ", "指標")

# T4 が計算を開始する下限日。`t4_ranks.py` の min_allowed_date と同じ。
# T2 は index_start_date(2010-04-01) から入るが、T4 は 2018-04-01 から。
DEFAULT_MIN_ALLOWED_DATE = "2018-04-01"

# (indicators の列, relative_ranks の列)。`t4_ranks.indicators_to_rank` と同一に保つ。
# ここがズレると SQLite 経由と Parquet 経由で違う値になる。
INDICATORS_TO_RANK: list[tuple[str, str]] = [
    ("rs_value", "rs_value_rank"),
    ("rs_ratio_e5", "rs_ratio_rank_e5"),
    ("rs_ratio_e14", "rs_ratio_rank_e14"),
    ("rs_ratio_e21", "rs_ratio_rank_e21"),
    ("rs_ratio_e63", "rs_ratio_rank_e63"),
    ("rs_ratio_e200", "rs_ratio_rank_e200"),
    ("rs_momentum_e5", "rs_momentum_rank_e5"),
    ("rs_momentum_e14", "rs_momentum_rank_e14"),
    ("rs_momentum_e21", "rs_momentum_rank_e21"),
    ("rs_momentum_e63", "rs_momentum_rank_e63"),
    ("rs_momentum_e200", "rs_momentum_rank_e200"),
    ("rs_trend_s5", "rs_trend_rank_s5"),
    ("rs_trend_s14", "rs_trend_rank_s14"),
    ("rs_trend_s21", "rs_trend_rank_s21"),
    ("rs_trend_s63", "rs_trend_rank_s63"),
    ("rs_trend_s200", "rs_trend_rank_s200"),
    ("rs_roc_ema_5", "rs_roc_ema_rank_e5"),
    ("rs_roc_ema_14", "rs_roc_ema_rank_e14"),
    ("rs_roc_ema_21", "rs_roc_ema_rank_e21"),
    ("rs_roc_ema_63", "rs_roc_ema_rank_e63"),
    ("rs_roc_ema_200", "rs_roc_ema_rank_e200"),
    ("rs_macd_hist_21", "rs_macd_hist_rank_21"),
]


def percent_rank(s: pd.Series) -> pd.Series:
    """SQL の ``PERCENT_RANK() OVER(ORDER BY col ASC)`` を再現する。

        PERCENT_RANK = (rank - 1) / (n - 1)

    実装上の必須事項が2つある。どちらも外すと SQLite の結果と一致しない。

    1. **同値は最小順位を共有する** → ``method="min"``。
       pandas の既定 ``method="average"`` では一致しない。
    2. **NULL は最小値として扱う** → NaN を ``-inf`` に置換してから順位付けする。
       SQLite は NULL を最小とみなすため、NaN を除外すると
       その銘柄の順位が欠落し、母集団サイズも変わって全体がずれる。

    ``s.rank(pct=True)`` は ``rank / n`` であり **別物**。使ってはいけない。

    実データ検証: 2026-07-31 の3,190行で SQLite の値と最大誤差 0.000e+00。
    """
    n = len(s)
    if n <= 1:
        # n=1 では (n-1) がゼロ除算になる。SQL の PERCENT_RANK は 0 を返す。
        return pd.Series(0.0, index=s.index)
    ranked = s.fillna(-np.inf).rank(method="min", ascending=True)
    return (ranked - 1.0) / (n - 1.0)


def recompute_ranks(
    indicators: pd.DataFrame,
    symbols: pd.DataFrame,
    cols: list[tuple[str, str]] | None = None,
    min_allowed_date: str = DEFAULT_MIN_ALLOWED_DATE,
) -> pd.DataFrame:
    """T4（relative_ranks）を Parquet の indicators から再計算する。

    `t4_ranks.py` の SQL と等価:
        PERCENT_RANK() OVER(PARTITION BY s.category ORDER BY i.{col} ASC)
    を日付ごとに適用する（SQL 側は日付単位のクエリなので、実質 (date, category) 分割）。

    Args:
        indicators: `symbol_id` / `date` と対象の指標列を持つ DataFrame
        symbols:    `id` / `category` / `active` を持つ DataFrame
        cols:       (指標列, ランク列) のリスト。省略時は INDICATORS_TO_RANK
        min_allowed_date: この日付より前は計算しない

    Returns:
        `symbol_id` / `date` / `group_name` + ランク列 の DataFrame

    Note:
        **現在の `active` を使う。** 過去の active 状態は記録していないため
        歴史的な母集団は再現できない。SQLite の T4 も同じ慣習で計算している。
    """
    cols = cols or INDICATORS_TO_RANK

    meta = symbols.loc[symbols["active"] == 1, ["id", "category"]]
    meta = meta[~meta["category"].isin(RANK_EXCLUDED_CATEGORIES)]

    df = indicators.merge(meta, left_on="symbol_id", right_on="id", how="inner")
    df = df[df["date"] >= min_allowed_date]
    if df.empty:
        return pd.DataFrame(columns=["symbol_id", "date", "group_name"] + [c for _, c in cols])

    grouped = df.groupby(["date", "category"], sort=False)
    out = df[["symbol_id", "date", "category"]].copy()
    for src, dst in cols:
        if src not in df.columns:
            # 指標列が無い場合は NULL 列を作る（SQL 側も NULL になる）
            out[dst] = np.nan
            continue
        out[dst] = grouped[src].transform(percent_rank)

    out = out.rename(columns={"category": "group_name"})
    return out.reset_index(drop=True)


def find_affected_virtual_themes(
    symbol_ids: list[int],
    theme_constituents: pd.DataFrame,
    symbols: pd.DataFrame,
) -> list[int]:
    """価格を補正した銘柄が所属する**仮想テーマ**の id を返す。

    ## なぜ必要か

    仮想テーマ指数は構成銘柄のリターンを連鎖させて合成している。
    構成銘柄の価格を補正したら、**所属する仮想テーマも全期間を再合成しないと
    誤った値が残る**。

    厄介なのは、再合成するかどうかの判定が **構成銘柄の集合のハッシュ**
    （`data/virtual_theme_hashes.json`）で決まること。
    **構成銘柄の「価格」が変わってもハッシュは変わらない**ため、
    差分モードのまま過去が再計算されない。

    実測（2026-08-05）: 仮想テーマ170件・構成ペア1,462件（平均8.6銘柄/テーマ）、
    1銘柄が最大5テーマに所属（`RKLB` `NET` `HPE` `CIEN` `CGNX`）。

    Returns:
        影響を受ける仮想テーマの symbol id（昇順）。実在 ETF のテーマは含まない。
    """
    ids = set(symbol_ids)
    virtual = symbols[
        symbols["ticker"].str.startswith("_") & symbols["ticker"].str.endswith("_")
    ]
    virtual_ids = set(virtual["id"])

    hit = theme_constituents[
        theme_constituents["symbol_id"].isin(ids)
        & theme_constituents["theme_id"].isin(virtual_ids)
    ]
    return sorted(set(hit["theme_id"]))
