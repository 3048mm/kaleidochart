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
  - 分割・併合の価格補正（`doc/completed/split_anomaly_noise_reduction_plan.md`）
  - `restore_truncated_symbol_history.py`（従来は T4 を外部に投げていた）

詳細: `doc/completed/parquet_recompute_plan.md`
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


def recompute_indicators(
    symbol_ids: list[int],
    prices: pd.DataFrame,
    indicator_columns: list[str],
    spy_id: int,
) -> pd.DataFrame:
    """指定銘柄の T3（indicators）を全期間再計算する。

    価格を修正した銘柄・履歴を継ぎ足した銘柄は、**T3 を作り直さないと
    指標が古い価格に基づいたまま残る**。

    ## 旧世代の T3 を流用してはいけない

    指標列は増え続けている（実測: 50 → 63 列。`avg_dollar_volume_21` / `rs_macd_*` 等）。
    旧世代からコピーすると**新しい列が欠損したまま**スクリーナーとバックテストに入る。
    T4（順位）も同様に 17 → 26 列に増えている。

    Args:
        symbol_ids: 再計算する銘柄の symbol_id
        prices:     `symbol_id` / `date` / OHLCV を持つ DataFrame（**全銘柄分**。
                    SPY の系列を RS 計算に使うため対象銘柄だけでは足りない）
        indicator_columns: 出力する指標列（既存 indicators の列順に合わせる）
        spy_id:     SPY の symbol_id

    Returns:
        `symbol_id` / `date` + 指標列 の DataFrame
    """
    # 遅延 import: percent_rank だけ使う呼び出し元に indicators 依存を持ち込まない
    from indicators.calculate import calculate_indicators

    spy_df = (
        prices[prices["symbol_id"] == spy_id][["date", "close", "volume"]]
        .sort_values("date")
        .reset_index(drop=True)
    )

    out = []
    for sid in symbol_ids:
        px = (
            prices[prices["symbol_id"] == sid][
                ["date", "open", "high", "low", "close", "volume"]
            ]
            .sort_values("date")
            .reset_index(drop=True)
        )
        if px.empty:
            continue
        # SPY 自身は自分との相対強度を計算しない（NULL が正常）
        df = calculate_indicators(px, None if sid == spy_id else spy_df)
        if df.empty:
            continue
        df = df.reindex(columns=["date"] + list(indicator_columns))
        df.insert(0, "symbol_id", sid)
        out.append(df)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


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


# 仮想テーマ指数の基準値。`orchestrator.build_virtual_index_prices` と同じ。
VIRTUAL_INDEX_BASE = 1000.0

# 合成 volume のスケール。指数に実出来高は無いので「売買代金の急増倍率 × これ」を入れる。
VIRTUAL_INDEX_VOLUME_SCALE = 1_000_000.0

# 売買代金の移動平均窓。`orchestrator` 側と一致させること。
VIRTUAL_INDEX_SURGE_WINDOW = 21


def rebuild_virtual_index_prices(
    theme_ids: list[int],
    prices: pd.DataFrame,
    theme_constituents: pd.DataFrame,
) -> pd.DataFrame:
    """仮想テーマ指数を **Parquet 上で** 全期間再合成する。

    ## なぜ必要か

    合成ロジックは `pipeline/orchestrator.build_virtual_index_prices` にしか無く、
    SQLAlchemy セッションを要求する。ところが SQLite は直近730日しか持たないため、
    **構成銘柄の価格を過去まで補正しても SQLite 経由では再合成が過去に届かない**。
    Parquet を直接読み書きする経路には、同じ式の DB 非依存版が要る。

    実例（2026-08-25）: `BYND` の 1:30 併合が未調整だったため、所属する仮想テーマ3本
    （`_CNSM0A_` `_GRCL29_` `_NTRTFC_`）の指数が 2026-08-13 に ×3.44〜×5.87 で飛んだ。

    > [!IMPORTANT]
    > **アルゴリズムは `orchestrator` 版と一字一句同じにすること。** ここが食い違うと
    > 「再合成した過去」と「翌日以降に日次が積む未来」で式が変わり、継ぎ目に段差が出る。
    > 同値性は `test_parquet_recompute.py` の
    > `test_rebuild_virtual_index_matches_the_orm_implementation` で固定してある。

    Args:
        theme_ids: 再合成する仮想テーマの symbol_id
        prices: `symbol_id` / `date` / `close` / `volume` を持つ DataFrame（全銘柄分）
        theme_constituents: `theme_id` / `symbol_id` を持つ DataFrame

    Returns:
        `symbol_id` / `date` / `open` / `high` / `low` / `close` / `volume` の DataFrame。
        構成銘柄が無いテーマは黙って飛ばす。

    Note:
        **初日は指数に載らない**（リターンが計算できないため）。`orchestrator` 版と同じ挙動。
    """
    out = []
    for theme_id in theme_ids:
        c_ids = theme_constituents.loc[
            theme_constituents["theme_id"] == theme_id, "symbol_id"
        ].tolist()
        if not c_ids:
            continue

        # close が 0 / 負 / NULL の行は使わない（`orchestrator` 側の `p.close > 0` と同じ）
        df = prices[prices["symbol_id"].isin(c_ids)][
            ["symbol_id", "date", "close", "volume"]
        ].copy()
        df = df[df["close"].notna() & (df["close"] > 0)]
        if df.empty:
            continue
        df = df.sort_values("date")

        # --- 指数値: 構成銘柄の日次平均リターンを基準値から連鎖させる ---
        df["ret"] = df.groupby("symbol_id")["close"].pct_change()
        daily_avg_ret = (
            df.dropna(subset=["ret"]).groupby("date")["ret"].mean()
            .reset_index().sort_values("date")
        )
        if daily_avg_ret.empty:
            continue

        # --- 合成 volume: 売買代金の21日平均に対する当日比（surge）の日次平均 ---
        df["dollar_volume"] = df["close"] * df["volume"].fillna(0)
        df["dollar_volume_ma21"] = df.groupby("symbol_id")["dollar_volume"].transform(
            lambda x: x.rolling(window=VIRTUAL_INDEX_SURGE_WINDOW, min_periods=1).mean()
        )
        df["surge"] = np.where(
            df["dollar_volume_ma21"] == 0,
            1.0,
            df["dollar_volume"] / df["dollar_volume_ma21"],
        )
        df["surge"] = df["surge"].fillna(1.0)
        daily_avg_surge = df.groupby("date")["surge"].mean().reset_index()

        # cumprod ではなく逐次乗算にしてある（`orchestrator` 版と浮動小数の丸めまで揃える）
        current_val = VIRTUAL_INDEX_BASE
        synth_closes = []
        for r in daily_avg_ret["ret"]:
            current_val *= (1 + r)
            synth_closes.append(current_val)

        idx = daily_avg_ret[["date"]].copy()
        idx["close"] = synth_closes
        idx["open"] = idx["high"] = idx["low"] = idx["close"]
        idx = idx.merge(daily_avg_surge, on="date", how="left")
        idx["volume"] = (
            idx["surge"].fillna(1.0) * VIRTUAL_INDEX_VOLUME_SCALE
        ).astype(float)
        idx = idx.drop(columns=["surge"])
        idx.insert(0, "symbol_id", theme_id)
        out.append(idx[["symbol_id", "date", "open", "high", "low", "close", "volume"]])

    if not out:
        return pd.DataFrame(
            columns=["symbol_id", "date", "open", "high", "low", "close", "volume"]
        )
    return pd.concat(out, ignore_index=True)
