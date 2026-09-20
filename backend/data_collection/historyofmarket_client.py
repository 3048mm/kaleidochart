"""historyofmarket.com breadth JSON APIアダプタ(tvDatafeed失敗時のフォールバック)。

出典: History of Market · 美股编年史 (historyofmarket.com)
ライセンス: CC BY 4.0(再配布はしないが出典は残す)

`GET https://historyofmarket.com/api/sp500/breadth.json` を取得し、
`pct50`→S5FIの`close`、`pct200`→S5THの`close`にマッピングする。
単一JSONに全期間が入る形式なので、取得後に指定範囲でフィルタする。
2023-07-13より前の日付は返せない(その場合は空DataFrameを返す)。
"""

import logging

import pandas as pd

from data_collection._series_utils import filter_date_range, normalize_columns

logger = logging.getLogger(__name__)

_BREADTH_URL = "https://historyofmarket.com/api/sp500/breadth.json"

# ticker -> レスポンスJSON内の使用フィールド名
_FIELD_MAP = {
    "S5FI": "pct50",
    "S5TH": "pct200",
}


def fetch_from_historyofmarket(
    ticker: str,
    start_date: str,
    end_date: str | None = None,
    http_get=None,
) -> pd.DataFrame:
    """historyofmarket.comのbreadth JSON APIから指定ティッカーの値を取得する。

    S5FI/S5TH以外のティッカーは対象外として、HTTP取得自体を行わず空DataFrameを返す。
    HTTP例外・非200レスポンス・JSON parse失敗はいずれも空DataFrameで返す。
    """
    field = _FIELD_MAP.get(ticker)
    if field is None:
        return pd.DataFrame()

    if http_get is None:
        import requests

        http_get = requests.get

    try:
        response = http_get(_BREADTH_URL)
    except Exception as e:
        logger.warning(f"[{ticker}] historyofmarket.com取得に失敗しました: {e}")
        return pd.DataFrame()

    if response is None or getattr(response, "status_code", None) != 200:
        logger.warning(f"[{ticker}] historyofmarket.comが非200を返しました。")
        return pd.DataFrame()

    # この関数はtvDatafeed失敗時の安全網(フォールバック)なので、JSON取得後の
    # パース処理全体もtry/exceptで囲む(それ自体が例外で落ちると安全網にならない)。
    try:
        payload = response.json()

        series = (payload or {}).get("series") or []
        if not series:
            return pd.DataFrame()

        rows = []
        for item in series:
            if field not in item or "date" not in item:
                continue
            rows.append({"date": item["date"], "close": item[field]})

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["close"] = df["close"].astype(float)
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df["volume"] = 0
        df["volume"] = df["volume"].astype("int64")

        df = normalize_columns(df)
        df = filter_date_range(df, start_date, end_date)

        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.warning(f"[{ticker}] historyofmarket.comのJSON parseに失敗しました: {e}")
        return pd.DataFrame()
