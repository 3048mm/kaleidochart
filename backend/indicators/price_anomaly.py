"""価格の段差（アノマリー）を分類する。

## 何を解決するか

従来の検出は「前日比が 0.61 以下 または 1.79 以上」だけを見ており、
**大きな値動きを全部拾っているだけ**だった。モメンタム系スクリーナーの母集団
（小型株・バイオを含む）では ±50〜70% の単日変動は日常的に起きる。

実測（2026-08-04 / Parquet 全期間・active 銘柄）:

    アノマリー総数 1,081件 / 504銘柄
      実際の値動き（バイオのイベント・暴落・決算）  大多数
      低位株のティック振動（ATLX は 0.75↔1.50 を何年も往復、27件）
      市場全体の急落日（2020-03-09 に34銘柄）        108件
      仮想テーマ指数の合成値                        43件
      本物の未調整分割                             ごく少数

`doc/issue_list.md` の「未調整の株式分割が299件」は誤りだった。

## 判定の考え方

「これは分割か？」を当てにいくのではなく、**明らかにノイズであるものを除く**。
本物の分割は少数なので、偽陽性を出さない側に倒す方が報告として使える。

詳細: `doc/completed/split_anomaly_noise_reduction_plan.md`
"""

from __future__ import annotations

# 段差とみなす前日比のしきい値（`weekly_maintenance.py` の従来値と同じ）
ANOMALY_RATIO_LO = 0.61
ANOMALY_RATIO_HI = 1.79

# 売買代金比が効くのは極端な比率のときだけ。
# **1:2 前後の帯では機能しない**（2026-08-04 実測）。暴落は出来高が1.5〜3倍に
# 跳ねるため代金比が 0.5〜2.0 に収まり、分割と区別できない
# （`DIN` `ADAM` `BBAI` の COVID 暴落を「分割」と誤判定した）。
SPLIT_RATIO_LO = 0.22   # 1:5 以上の併合
SPLIT_RATIO_HI = 4.0    # 4:1 以上の分割

# 分割なら株数が変わるだけなので売買代金は連続する。
SPLIT_DV_RATIO_LO = 0.5
SPLIT_DV_RATIO_HI = 2.0

# 実売買しうる水準。これを下回る銘柄は戦略が触らないため報告から外す。
# 価格は**段差の前後で高い方**を見る（併合は前が低く後が高いため）。
MIN_PRICE = 5.0
MIN_AVG_DOLLAR_VOLUME = 1_000_000.0

# 同日にこの数以上の銘柄が同時に段差を起こしたら市場イベントとみなす。
# 2020-03-09 は34銘柄、03-18 は16銘柄、03-16 は12銘柄（COVID）。
# 2〜3銘柄の偶然の一致は除外しない。
MARKET_WIDE_MIN_SYMBOLS = 4


def is_anomalous_ratio(ratio: float | None) -> bool:
    """前日比が段差の検出対象か。"""
    if ratio is None:
        return False
    return ratio <= ANOMALY_RATIO_LO or ratio >= ANOMALY_RATIO_HI


def is_virtual_ticker(ticker: str) -> bool:
    """仮想テーマ指数（`_..._`）か。"""
    return bool(ticker) and ticker.startswith("_") and ticker.endswith("_")


def count_real_symbols_per_day(rows):
    """同日に段差を起こした**実在銘柄**の数を、行ごとに返す。

    `classify_price_jump` の `same_day_count`（市場イベント判定）にはこの値を使う。

    > [!IMPORTANT]
    > **仮想テーマ指数を数に入れてはいけない。** 仮想指数は構成銘柄から合成されるので、
    > **1銘柄が壊れると所属テーマの数だけ同日件数が水増しされる**（1銘柄が最大5テーマに
    > 所属する）。結果として**犯人が自分の作った波及に隠れる**。
    >
    > 2026-08-25 に発覚。`BYND` の 1:30 併合（比率 30.10）がこれで `market_wide` に
    > 誤分類され、週次監査の報告から漏れていた:
    >
    > ```
    > 2026-08-13 の同日アノマリー 4件（MARKET_WIDE_MIN_SYMBOLS = 4）
    >   _GRCL29_   5.83  virtual      ← BYND が汚染したテーマ
    >   _CNSM0A_   3.44  virtual      ← 同上
    >   _NTRTFC_   5.87  virtual      ← 同上
    >   BYND      30.10  market_wide  ← 犯人が「市場イベント」に化けた
    > ```

    Args:
        rows: `ticker` / `date` 列を持つ DataFrame

    Returns:
        `rows` と同じ index の Series（各行の日付における実在銘柄の件数）。
    """
    real = rows[~rows["ticker"].map(is_virtual_ticker)]
    per_day = real.groupby("date").size()
    return rows["date"].map(per_day).fillna(0).astype(int)


def classify_price_jump(
    row: dict,
    *,
    min_price: float = MIN_PRICE,
    min_adv: float = MIN_AVG_DOLLAR_VOLUME,
    market_wide_n: int = MARKET_WIDE_MIN_SYMBOLS,
) -> str:
    """段差を分類する。

    Args:
        row: 以下のキーを持つ dict
            ticker          … ティッカー
            ratio           … 当日終値 / 前日終値
            prev_close      … 前日終値
            adv21           … ジャンプ直前21日の平均売買代金
            dv_ratio        … 売買代金比（当日 / 前日）。前日が取引停止等なら None
            dv_vs_adv       … 当日代金 / 直前21日平均代金。`dv_ratio` が取れないときの代替
            same_day_count  … 同日にアノマリーを起こした銘柄数

    Returns:
        ``"virtual"``       … 仮想テーマ指数。合成値なのでテーマ自体は分割しない
        ``"market_wide"``   … 同日に多数が同時に動いた。市場イベント
        ``"low_liquidity"`` … 低位株・薄商い。戦略が触らない水準
        ``"split_suspect"`` … 未調整の分割の疑い（極端な比率＋売買代金の連続）
        ``"undecided"``     … 出来高0等で判定できない
        ``"real_move"``     … 実際の値動き（大多数。**対応不要**）

    優先順位は virtual > market_wide > low_liquidity > split/undecided/real。
    構造的にノイズと分かるものから先に落とす。
    """
    if is_virtual_ticker(row.get("ticker", "")):
        return "virtual"

    if (row.get("same_day_count") or 0) >= market_wide_n:
        return "market_wide"

    ratio = row.get("ratio")
    prev_close = row.get("prev_close")
    adv21 = row.get("adv21")

    # 価格の下限は「前後の高い方」で見る。
    # 併合（reverse split）は**前が低く後が高い**ため、前日終値だけで足切りすると
    # 本物の併合を取り逃す（`WBX` は 1:20 併合で $1 → $19.4）。
    peak_price = None
    if prev_close is not None:
        peak_price = max(prev_close, prev_close * ratio) if ratio is not None else prev_close

    if peak_price is None or peak_price <= min_price:
        return "low_liquidity"
    if adv21 is None or adv21 <= min_adv:
        return "low_liquidity"

    if ratio is None:
        return "undecided"

    dv = row.get("dv_ratio")
    if dv is None:
        # 前日が**取引停止**だと前日代金が0になり比が取れない。
        # その場合は直前21日平均との比で代替する。
        #   MESO 2023-08-04: 停止2日 → 再開して -59%。当日代金1,199万 / 21日平均166万 = 7.2倍
        #                    → 実際の値動き（分割ではない）
        #   CORZZ 2026-07-17: そもそも取引が無い。当日代金0 / 21日平均2.1万 = 0
        #                    → 判定材料が無いので保留
        dv = row.get("dv_vs_adv")
        if dv is None or dv == 0:
            return "undecided"

    # 売買代金の連続性が効くのは極端な比率のときだけ。
    # 中庸な帯（1:2 前後）では暴落と区別できないため split_suspect にしない。
    if ratio <= SPLIT_RATIO_LO or ratio >= SPLIT_RATIO_HI:
        if SPLIT_DV_RATIO_LO <= dv <= SPLIT_DV_RATIO_HI:
            return "split_suspect"

    return "real_move"
