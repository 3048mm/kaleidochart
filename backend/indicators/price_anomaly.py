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

from datetime import date as _date

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

# --- 分割メタデータとの照合 ------------------------------------------------
# 段差が `1/factor` からどれだけ外れてよいか。分割日当日の値動きが乗るので
# ぴったりにはならない（`BYND` は 30.10 / factor 30 = +0.3%）。
# `scan_split_consistency.py` の検査Aと同じ値を使う（**同じ判定を2箇所に
# 書かない**）。
SPLIT_JUMP_TOLERANCE = 0.08

# 「未適用」と「適用済み」を段差で識別するには、`1/factor` が 1.0 から十分
# 離れている必要がある。**株式配当（factor ≈ 1.0x）は識別できない。**
# 2026-09-09 の全ユニバース実測で、検出18件のうち16件がこの誤検出だった
# （`SCCO` 7件 / `TR` 2件 等、いずれも 1.006〜1.061 の株式配当）。
MIN_DISCRIMINABLE_GAP = 0.15

# 段差の日付と、上流が記録している分割日のずれをどこまで許すか（暦日）。
#
# > [!IMPORTANT]
# > **この数値に確たる根拠は無い**（`MARKET_WIDE_MIN_SYMBOLS` と同じ扱い＝
# > 運用しながら調整する前提の暫定値。2026-09-10 設定）。
# >
# > 手元で確認できた実測のずれは:
# >   `IESC` 2026-08-24 の段差 / 記録 2026-08-24 → 0日
# >   `WLFC` 2026-07-20 の段差 / 記録 2026-07-21 → **1日**
# > 起票時の根拠だった `AVB`（7日ずれ）は `active=0` で退役済みのため
# > 当プロジェクトのユニバースに無く、**検証できていない**。
# > 0 では取り逃すこと（`WLFC`）だけが確実に言える。
SPLIT_DATE_WINDOW_DAYS = 10


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


def is_below_liquidity_floor(prev_close: float | None, ratio: float | None,
                             adv21: float | None, *,
                             min_price: float = MIN_PRICE,
                             min_adv: float = MIN_AVG_DOLLAR_VOLUME) -> bool:
    """実売買しうる水準を下回るか（`low_liquidity` の判定条件そのもの）。

    分類器とレポートの両方が使う。レポート側は「要対応 N件（**うち低流動 M件**）」の
    内訳を出すのに使い、**分類を変えるためには使わない**（分割記録と一致した段差は
    流動性に関わらず要対応。`classify_price_jump` の Note を参照）。

    価格は**段差の前後で高い方**を見る。併合（reverse split）は前が低く後が高いため、
    前日終値だけで足切りすると本物の併合を取り逃す（`WBX` は 1:20 併合で $1 → $19.4）。
    """
    peak_price = None
    if prev_close is not None:
        peak_price = (max(prev_close, prev_close * ratio) if ratio is not None
                      else prev_close)
    if peak_price is None or peak_price <= min_price:
        return True
    return adv21 is None or adv21 <= min_adv


def find_matching_split(ticker: str, date: str, ratio: float | None,
                        split_records: dict | None, *,
                        window_days: int = SPLIT_DATE_WINDOW_DAYS,
                        tolerance: float = SPLIT_JUMP_TOLERANCE) -> dict | None:
    """段差が、上流の記録している分割で説明できるかを判定する。

    ## なぜ要るか

    分類器は長らく**分割メタデータを一切見ていなかった**ため、比の大きさと
    同日件数だけで判定していた。その結果、本物のデータ破損が
    `market_wide` / `real_move` として捨てられていた（`doc/issue_list.md` P1）:

        IESC  2026-08-24  ratio=0.4731  same_day_count=4  → market_wide
        WLFC  2026-07-20  ratio=0.3294  same_day_count=1  → real_move

    ## 判定は2条件の AND

    1. 記録された分割日が段差の日付から ``±window_days`` 以内
    2. 比が ``1/factor`` に ``tolerance`` の相対許容で一致

    **日付だけでは足りない**（分割日に本物の急落が重なりうる）。
    **比だけでも足りない**（1:2 前後の比は暴落と区別できない。
    `SPLIT_RATIO_LO/HI` がその帯を避けているのと同じ理由）。

    Args:
        ticker: 銘柄。
        date: 段差の日付（``YYYY-MM-DD``）。
        ratio: 当日終値 / 前日終値。
        split_records: ``{ticker: [(split_date, factor), ...]}``。
            `data_collection.split_records.split_map()` の戻り値をそのまま渡す。

    Returns:
        一致した分割の ``{"split_date", "factor", "expected_ratio", "days_off"}``。
        **一致しなければ ``None``**（記録が無い・期間外・比が合わない、を
        区別せず ``None`` にする。呼び出し側はどれでも「分割とは言えない」で同じ）。

    Note:
        **識別できない領域では判定しない。** 株式配当（``factor ≈ 1.0``）は
        「未適用（``jump≈1/factor``）」と「適用済み（``jump≈1.0``）」が
        許容幅の中で重なるため、常に一致してしまう。
    """
    if not split_records or not ticker or not date or ratio is None or ratio <= 0:
        return None

    pairs = split_records.get(ticker)
    if not pairs:
        return None

    try:
        target = _to_ordinal(date)
    except ValueError:
        return None

    best = None
    for split_date, factor in pairs:
        if not factor or factor <= 0:
            continue
        expected = 1.0 / factor
        # 未適用と適用済みが識別できない帯（株式配当）は判定を放棄する
        if abs(expected - 1.0) < MIN_DISCRIMINABLE_GAP:
            continue
        try:
            days_off = _to_ordinal(str(split_date)) - target
        except ValueError:
            continue
        if abs(days_off) > window_days:
            continue
        if abs(ratio - expected) > expected * tolerance:
            continue
        # 同じ窓に複数あるなら、段差の日に最も近いものを採る
        if best is None or abs(days_off) < abs(best["days_off"]):
            best = {"split_date": str(split_date), "factor": float(factor),
                    "expected_ratio": expected, "days_off": days_off}
    return best


def _to_ordinal(date_str: str) -> int:
    """``YYYY-MM-DD`` を日数に直す（差分を取るためだけの内部関数）。"""
    return _date(int(date_str[0:4]), int(date_str[5:7]),
                 int(date_str[8:10])).toordinal()


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
            split_match     … `find_matching_split()` の戻り値（任意）。
                              **渡さなければ従来と完全に同じ挙動**（後方互換）

    Returns:
        ``"virtual"``       … 仮想テーマ指数。合成値なのでテーマ自体は分割しない
        ``"market_wide"``   … 同日に多数が同時に動いた。市場イベント
        ``"low_liquidity"`` … 低位株・薄商い。戦略が触らない水準
        ``"split_suspect"`` … 未調整の分割の疑い（極端な比率＋売買代金の連続）
        ``"undecided"``     … 出来高0等で判定できない
        ``"real_move"``     … 実際の値動き（大多数。**対応不要**）

    優先順位は virtual > **split_match** > market_wide > low_liquidity >
    split/undecided/real。構造的にノイズと分かるものから先に落とし、
    **分割記録との一致だけがそれを上書きする**。

    > [!IMPORTANT]
    > **分割一致を `low_liquidity` より下に置いてはいけない。**
    > `low_liquidity`（$5 / $1M日）は「破損かどうか」ではなく
    > 「**対応する価値があるか**」の足切り。未調整分割は取引しなくても
    > Parquet マスタが壊れたまま残り、T4 の横断ランクにも乗る。
    > 読む量の調整はレポート側の並び順で行う（分類で握り潰さない）。
    """
    if is_virtual_ticker(row.get("ticker", "")):
        return "virtual"

    # 上流が記録している分割と一致するなら、それが最も強い証拠。
    # `MARKET_WIDE_MIN_SYMBOLS = 4` は「同日に4件の分割が重なると全部を
    # 市場全体の動きとして消す」ため、ここより下に置くと `IESC` を取り逃す。
    if row.get("split_match"):
        return "split_suspect"

    if (row.get("same_day_count") or 0) >= market_wide_n:
        return "market_wide"

    ratio = row.get("ratio")
    prev_close = row.get("prev_close")
    adv21 = row.get("adv21")

    if is_below_liquidity_floor(prev_close, ratio, adv21,
                                min_price=min_price, min_adv=min_adv):
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
