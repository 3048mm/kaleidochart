"""為替（USD/JPY）の営業日判定とレート解決。

## なぜ専用モジュールが要るか

株価の T2 取り込みは `row['date'] <= spy_latest_date` という **SPY の最新日を上限**に
しているため、Yahoo が返す「当日・部分バー」（取引していない日のスナップショット）は
自動的に落ちる。**`fx_rates` にはこの上限が無い**ため、土曜に日次パイプラインを実行すると
土曜バーがそのまま入る。2026-08-01(土) に `157.40` が入り、金曜終値 `160.18` と
1.7% 乖離していた（実際に本番へ混入）。

判定を1箇所に集約するのは、`theme_type` の判定が3実装に分裂して
IBIT / CPER の値が食い違った前例があるため。書き込み側（`sync_fx_rates` /
`backfill_fx_rates.py`）と読み取り側（`portfolio_service`）が同じ定義を使う。
"""

from datetime import date as _date
from typing import Optional

# 直近のレートを探すときに遡る最大行数。
# 土日2日＋連休を見込んでも数行で足りるが、余裕を持たせる。
# 「土日行が10連続で並ぶ」ことは構造上ありえないため、これで十分。
_LOOKBACK_ROWS = 10

DEFAULT_CURRENCY_PAIR = "USD/JPY"


def is_fx_trading_day(d: Optional[_date]) -> bool:
    """USD/JPY の日足バーが存在しうる日かどうか。

    為替は日曜 17:00 ET 〜 金曜 17:00 ET に連続取引されるため、
    Yahoo の日足バーは月〜金にしか出ない。土日のバーは
    「取引していない日のスナップショット」であり無効とみなす。

    米国の祝日は為替が動くため除外しない（株式の取引日と一致させてはいけない）。
    """
    if d is None:
        return False
    return d.weekday() < 5


def resolve_fx_rate(
    db,
    target_date: Optional[_date] = None,
    pair: str = DEFAULT_CURRENCY_PAIR,
    default: float = 150.0,
) -> float:
    """営業日のレートだけを使って為替レートを解決する。

    土日行が DB に混入していても影響を受けない。単なる防御ではなく
    **意味的にも正しい**: 土曜の取引に適用すべきレートは金曜終値であり、
    土曜行があってもそれを使ってはいけない。

    Args:
        target_date: 指定するとその日以前で最も新しい営業日のレート。
                     省略すると全期間で最も新しい営業日のレート。
        default: レートが1件も無い場合の戻り値。

    Returns:
        レート（1ドルあたりの円）。
    """
    from sqlalchemy import desc

    from db.models import FxRate

    query = db.query(FxRate).filter(FxRate.currency_pair == pair)
    if target_date is not None:
        query = query.filter(FxRate.date <= target_date)

    for row in query.order_by(desc(FxRate.date)).limit(_LOOKBACK_ROWS):
        if is_fx_trading_day(row.date):
            return row.rate

    # 対象日より前に営業日のレートが無い場合は、最も古い営業日のレートで代替する
    # （取引日がデータ開始日より前のケース）。
    for row in (
        db.query(FxRate)
        .filter(FxRate.currency_pair == pair)
        .order_by(FxRate.date)
        .limit(_LOOKBACK_ROWS)
    ):
        if is_fx_trading_day(row.date):
            return row.rate

    return default
