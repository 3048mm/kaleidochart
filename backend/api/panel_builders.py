"""ダッシュボード・テーマ・グループ詳細画面の共通アイテムビルダー（audit D-1 で routers.py から分割）。

None 安全な数値変換を行い、フロントエンドのレンダリングエラーを防止する。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from datetime import date as dt_date, timedelta
import logging
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, ThemeConstituent
from api import schemas

logger = logging.getLogger(__name__)


# --- バルクプリロード（Plan B: per-symbol N+1 の定数クエリ化） ---
# 1 銘柄あたり 3 クエリ（sparkline / 価格履歴 / indicator）を発行する代わりに、
# 対象銘柄全件ぶんを 3 クエリで一括取得してビルダーへ渡す。
# ROW_NUMBER() の PARTITION BY により per-symbol 版の
# 「日付降順・LIMIT n」と厳密に等価な結果を得る（test_panel_builders.py で検証）。

@dataclass
class PanelPreload:
    """パネルアイテム生成用の一括ロード済みデータ。"""
    sparklines: Dict[int, List[float]] = field(default_factory=dict)       # 日付昇順
    close_histories: Dict[int, List[float]] = field(default_factory=dict)  # 日付降順
    indicators: Dict[int, Indicator] = field(default_factory=dict)         # 対象日の1行


# 一括ロード時に遡る日数。limit（最大30営業日）を確実に覆う余裕を持たせる。
# 日付下限を付けないと窓関数が各銘柄の全履歴（数百万行規模のテーブル）を読み、
# コールドキャッシュの HDD ではランダム I/O で数分かかる（2026-07-04 実測 360秒）。
PRELOAD_DATE_MARGIN_DAYS = 90


def _to_date(value) -> dt_date:
    if isinstance(value, dt_date):
        return value
    return dt_date.fromisoformat(str(value)[:10])


def preload_sparklines(db: Session, symbol_ids, target_date: Optional[str],
                       period: int = 21, limit: int = 30) -> Dict[int, List[float]]:
    """_get_sparkline_data と厳密等価な結果（非NULL・最新 limit 件・日付昇順）を一括取得する。

    直近 PRELOAD_DATE_MARGIN_DAYS 日の窓に対して窓関数で一括取得し、
    窓内で limit 件に満たなかった銘柄のみ per-symbol クエリ（元実装と同一）で
    取り直す。窓内に limit 件ある銘柄は「最新 limit 件」が窓に完全に収まるため
    無制限版と一致し、等価性が保証される。

    target_date=None は日付上限なし（各銘柄の全期間から最新 limit 件）。
    """
    col_attr = getattr(RelativeRank, f"rs_ratio_rank_e{period}", None)
    if col_attr is None or not symbol_ids:
        return {}
    symbol_ids = list(symbol_ids)

    if target_date is not None:
        upper = _to_date(target_date)
    else:
        upper = db.query(func.max(RelativeRank.date)).scalar()
        if upper is None:
            return {}
    cutoff = upper - timedelta(days=PRELOAD_DATE_MARGIN_DAYS)

    rn = func.row_number().over(
        partition_by=RelativeRank.symbol_id,
        order_by=RelativeRank.date.desc()
    ).label("rn")
    q = db.query(RelativeRank.symbol_id.label("sid"), col_attr.label("val"), rn).filter(
        RelativeRank.symbol_id.in_(symbol_ids),
        col_attr.isnot(None),
        RelativeRank.date >= cutoff,
    )
    if target_date is not None:
        q = q.filter(RelativeRank.date <= target_date)
    subq = q.subquery()
    rows = db.query(subq.c.sid, subq.c.val).filter(subq.c.rn <= limit).order_by(
        subq.c.sid, subq.c.rn.desc()).all()

    result: Dict[int, List[float]] = {}
    for sid, val in rows:
        result.setdefault(sid, []).append(val)

    # フォールバック: 窓内で limit 件に満たない銘柄（新規上場・休止銘柄など少数）は
    # 窓の外にデータを持つ可能性があるため、per-symbol の元クエリで取り直す
    for sid in symbol_ids:
        if len(result.get(sid, ())) >= limit:
            continue
        q2 = db.query(col_attr).filter(
            RelativeRank.symbol_id == sid,
            col_attr.isnot(None),
        )
        if target_date is not None:
            q2 = q2.filter(RelativeRank.date <= target_date)
        rows2 = q2.order_by(desc(RelativeRank.date)).limit(limit).all()
        vals = [r[0] for r in reversed(rows2)]
        if vals:
            result[sid] = vals
        else:
            result.pop(sid, None)
    return result


def preload_close_histories(db: Session, symbol_ids, target_date: str,
                            limit: int = 22) -> Dict[int, List[float]]:
    """ビルダー内の価格履歴クエリ（close のみ・日付降順・LIMIT limit）を一括取得する。

    等価性の考え方は preload_sparklines と同じ（日付窓 + 不足銘柄のみフォールバック）。
    """
    if not symbol_ids:
        return {}
    symbol_ids = list(symbol_ids)
    cutoff = _to_date(target_date) - timedelta(days=PRELOAD_DATE_MARGIN_DAYS)

    rn = func.row_number().over(
        partition_by=DailyPrice.symbol_id,
        order_by=DailyPrice.date.desc()
    ).label("rn")
    subq = db.query(
        DailyPrice.symbol_id.label("sid"), DailyPrice.close.label("close"), rn
    ).filter(
        DailyPrice.symbol_id.in_(symbol_ids),
        DailyPrice.date >= cutoff,
        DailyPrice.date <= target_date
    ).subquery()
    rows = db.query(subq.c.sid, subq.c.close).filter(subq.c.rn <= limit).order_by(
        subq.c.sid, subq.c.rn.asc()).all()

    result: Dict[int, List[float]] = {}
    for sid, close in rows:
        result.setdefault(sid, []).append(close)

    for sid in symbol_ids:
        if len(result.get(sid, ())) >= limit:
            continue
        rows2 = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sid,
            DailyPrice.date <= target_date
        ).order_by(desc(DailyPrice.date)).limit(limit).all()
        vals = [r[0] for r in rows2]
        if vals:
            result[sid] = vals
        else:
            result.pop(sid, None)
    return result


def preload_indicators(db: Session, symbol_ids, target_date: str) -> Dict[int, Indicator]:
    """対象日の indicator 行を一括取得する。"""
    if not symbol_ids:
        return {}
    rows = db.query(Indicator).filter(
        Indicator.symbol_id.in_(symbol_ids),
        Indicator.date == target_date
    ).all()
    return {r.symbol_id: r for r in rows}


def build_panel_preload(db: Session, symbol_ids, target_date: str) -> PanelPreload:
    """_build_panel_item / _build_leading_item が必要とする全データを 3 クエリで取得する。"""
    return PanelPreload(
        sparklines=preload_sparklines(db, symbol_ids, target_date),
        close_histories=preload_close_histories(db, symbol_ids, target_date),
        indicators=preload_indicators(db, symbol_ids, target_date),
    )


def _get_sparkline_data(db: Session, sym_id: int, target_date: str, period: int = 21,
                        preloaded: Optional[Dict[int, List[float]]] = None):
    if preloaded is not None:
        vals = preloaded.get(sym_id, [])
        return vals if vals else [0.5] * 5

    col_attr = getattr(RelativeRank, f"rs_ratio_rank_e{period}", None)
    if col_attr is None:
        return [0.5] * 5
    ranks = db.query(col_attr).filter(
        RelativeRank.symbol_id == sym_id,
        col_attr.isnot(None),
        RelativeRank.date <= target_date
    ).order_by(desc(RelativeRank.date)).limit(30).all()

    vals = [r[0] for r in reversed(ranks)]
    if not vals:
        return [0.5] * 5
    return vals

def _build_panel_item(db: Session, sym: Symbol, dp: DailyPrice, rank_val_21: float, rank_val_63: float, target_date: str,
                     rank_val_14: float = 0.0, rank_val_mom: float = 0.0, rank_val_mom63: float = 0.0,
                     rank_val_trend_14: float = 0.0, rank_val_trend_21: float = 0.0, rank_val_trend_63: float = 0.0,
                     preload: Optional[PanelPreload] = None):
    # preload あり: build_panel_preload 済みの辞書から取得（クエリ発行なし）
    # preload なし: 従来通り per-symbol クエリ（呼び出し側の互換維持）
    if preload is not None:
        sparkline = _get_sparkline_data(db, sym.id, target_date, preloaded=preload.sparklines)
        hist_closes = preload.close_histories.get(sym.id, [])
    else:
        sparkline = _get_sparkline_data(db, sym.id, target_date)

        history = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sym.id,
            DailyPrice.date <= target_date
        ).order_by(desc(DailyPrice.date)).limit(22).all()

        hist_closes = [h[0] for h in history]

    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
        
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
        
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100
    elif len(hist_closes) > 1 and hist_closes[-1] > 0:
        change_1m_pct = ((dp.close - hist_closes[-1]) / hist_closes[-1]) * 100

    if preload is not None:
        ind = preload.indicators.get(sym.id)
    else:
        ind = db.query(Indicator).filter(
            Indicator.symbol_id == sym.id,
            Indicator.date == dp.date
        ).first()

    if ind:
        if ind.change_1d_pct is not None: change_1d_pct = ind.change_1d_pct
        if ind.change_1w_pct is not None: change_1w_pct = ind.change_1w_pct
        if ind.change_1m_pct is not None: change_1m_pct = ind.change_1m_pct

    dist_21ema_pct = 0.0
    if ind and ind.ema_21 and ind.ema_21 > 0:
        dist_21ema_pct = ((dp.close - ind.ema_21) / ind.ema_21) * 100

    return schemas.DashboardPanelItem(
        id=sym.id,
        ticker=sym.ticker,
        name=sym.name,
        category=sym.category,
        close=dp.close,
        change_pct=change_1d_pct,
        change_1w_pct=change_1w_pct,
        change_1m_pct=change_1m_pct,
        dist_21ema_pct=dist_21ema_pct,
        sparkline=sparkline if sparkline else [],
        intensity_score=float(rank_val_21 or 0.0),
        rs_ratio_rank_e21=float(rank_val_21 or 0.0),
        rs_ratio_rank_e63=float(rank_val_63 or 0.0),
        rs_ratio_rank_e14=float(rank_val_14 or 0.0),
        rs_momentum_rank_e21=float(rank_val_mom or 0.0),
        rs_momentum_rank_e63=float(rank_val_mom63 or 0.0),
        rs_trend_rank_s14=float(rank_val_trend_14 or 0.0),
        rs_trend_rank_s21=float(rank_val_trend_21 or 0.0),
        rs_trend_rank_s63=float(rank_val_trend_63 or 0.0),
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs_momentum_e21=ind.rs_momentum_e21 if ind else None,
        
        # Legacy fields for frontend compatibility
        rs_ratio_21_rank=float(rank_val_21 or 0.0),
        rs_ratio_63_rank=float(rank_val_63 or 0.0),
        rs_ratio_14_rank=float(rank_val_14 or 0.0),
        rs_momentum_21_rank=float(rank_val_mom or 0.0),
        rs_momentum_63_rank=float(rank_val_mom63 or 0.0),
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rs_momentum_21=ind.rs_momentum_e21 if ind else None
    )

def _build_leading_item(db: Session, sym: Symbol, dp: DailyPrice, target_date: str,
                        preload: Optional[PanelPreload] = None):
    if preload is not None:
        hist_closes = preload.close_histories.get(sym.id, [])
    else:
        history = db.query(DailyPrice.close).filter(
            DailyPrice.symbol_id == sym.id,
            DailyPrice.date <= target_date
        ).order_by(desc(DailyPrice.date)).limit(22).all()

        hist_closes = [h[0] for h in history]
    sparkline_raw = list(reversed(hist_closes))
    
    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100

    if preload is not None:
        ind = preload.indicators.get(sym.id)
    else:
        ind = db.query(Indicator).filter(
            Indicator.symbol_id == sym.id,
            Indicator.date == target_date
        ).first()
    dist_21ema_pct = 0.0
    if ind and ind.ema_21 and ind.ema_21 > 0:
        dist_21ema_pct = ((dp.close - ind.ema_21) / ind.ema_21) * 100
        
    return schemas.LeadingIndicatorItem(
        id=sym.id,
        ticker=sym.ticker,
        name=sym.name,
        category=sym.category,
        close=float(dp.close or 0.0),
        change_1d_pct=float(change_1d_pct or 0.0),
        change_1w_pct=float(change_1w_pct or 0.0),
        change_1m_pct=float(change_1m_pct or 0.0),
        dist_21ema_pct=float(dist_21ema_pct or 0.0),
        sparkline=sparkline_raw if sparkline_raw else []
    )

def _build_etf_feature(db: Session, sym: Symbol, dp: DailyPrice, target_date: str):
    history = db.query(DailyPrice).filter(
        DailyPrice.symbol_id == sym.id,
        DailyPrice.date <= target_date
    ).order_by(desc(DailyPrice.date)).limit(252).all()
    
    hist_closes = [h.close for h in history]
    
    change_1d_pct = 0.0
    change_1w_pct = 0.0
    change_1m_pct = 0.0
    change_1y_pct = 0.0
    
    if len(hist_closes) > 1 and hist_closes[1] > 0:
        change_1d_pct = ((dp.close - hist_closes[1]) / hist_closes[1]) * 100
    if len(hist_closes) >= 6 and hist_closes[5] > 0:
        change_1w_pct = ((dp.close - hist_closes[5]) / hist_closes[5]) * 100
    if len(hist_closes) >= 22 and hist_closes[21] > 0:
        change_1m_pct = ((dp.close - hist_closes[21]) / hist_closes[21]) * 100
    if len(hist_closes) >= 252 and hist_closes[251] > 0:
        change_1y_pct = ((dp.close - hist_closes[251]) / hist_closes[251]) * 100
        
    ind = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date == target_date
    ).first()
    
    dist_sma5_pct = 0.0
    dist_sma21_pct = 0.0
    dist_sma63_pct = 0.0
    sma21_sma63_pct = 0.0
    dist_sma200_pct = 0.0
    
    if ind:
        if ind.sma_5 and ind.sma_5 > 0:
            dist_sma5_pct = ((dp.close - ind.sma_5) / ind.sma_5) * 100
        if ind.sma_21 and ind.sma_21 > 0:
            dist_sma21_pct = ((dp.close - ind.sma_21) / ind.sma_21) * 100
        if ind.sma_63 and ind.sma_63 > 0:
            dist_sma63_pct = ((dp.close - ind.sma_63) / ind.sma_63) * 100
        if ind.sma_21 and ind.sma_63 and ind.sma_63 > 0:
            sma21_sma63_pct = ((ind.sma_21 - ind.sma_63) / ind.sma_63) * 100
        if ind.sma_200 and ind.sma_200 > 0:
            dist_sma200_pct = ((dp.close - ind.sma_200) / ind.sma_200) * 100
            
    rs14_spark = _get_sparkline_data(db, sym.id, target_date, 14)
    rs21_spark = _get_sparkline_data(db, sym.id, target_date, 21)
    rs63_spark = _get_sparkline_data(db, sym.id, target_date, 63)

    # Latest Ranks（3カラムを1クエリで取得し、本体/レガシー両フィールドで再利用）
    rank_row = db.query(
        RelativeRank.rs_ratio_rank_e14,
        RelativeRank.rs_ratio_rank_e21,
        RelativeRank.rs_ratio_rank_e63
    ).filter(
        RelativeRank.symbol_id == sym.id,
        RelativeRank.date == target_date
    ).first()
    rank_e14 = rank_row.rs_ratio_rank_e14 if rank_row else None
    rank_e21 = rank_row.rs_ratio_rank_e21 if rank_row else None
    rank_e63 = rank_row.rs_ratio_rank_e63 if rank_row else None

    # Mini chart (6 months = 126 days)
    six_m_hist = list(reversed(history[:126]))
    start_date = six_m_hist[0].date
    end_date = six_m_hist[-1].date
    
    theme_inds = db.query(Indicator).filter(
        Indicator.symbol_id == sym.id,
        Indicator.date >= start_date,
        Indicator.date <= end_date
    ).all()
    theme_ind_dict = {i.date.strftime('%Y-%m-%d'): i for i in theme_inds}
    
    chart_data = []
    for h in six_m_hist:
        ds = h.date.strftime('%Y-%m-%d')
        i = theme_ind_dict.get(ds)
        chart_data.append(schemas.ChartDataPoint(
            time=ds,
            open=h.open or 0.0,
            high=h.high or 0.0,
            low=h.low or 0.0,
            close=h.close,
            volume=int(round(h.volume)) if h.volume else 0,
            sma_63=i.sma_63 if i else None,
            sma_200=i.sma_200 if i else None,
            rs_value=i.rs_value if i else None,
            rs_value_e5=i.rs_value_e5 if i else None,
            rs_value_e14=i.rs_value_e14 if i else None,
            rs_value_e21=i.rs_value_e21 if i else None,
            rs_value_e63=i.rs_value_e63 if i else None,
            rs_value_e200=i.rs_value_e200 if i else None,
            rs_ratio_e5=i.rs_ratio_e5 if i else None,
            rs_ratio_e14=i.rs_ratio_e14 if i else None,
            rs_ratio_e21=i.rs_ratio_e21 if i else None,
            rs_ratio_e63=i.rs_ratio_e63 if i else None,
            rs_ratio_e200=i.rs_ratio_e200 if i else None,
            rs_momentum_e5=i.rs_momentum_e5 if i else None,
            rs_momentum_e14=i.rs_momentum_e14 if i else None,
            rs_momentum_e21=i.rs_momentum_e21 if i else None,
            rs_momentum_e63=i.rs_momentum_e63 if i else None,
            rs_momentum_e200=i.rs_momentum_e200 if i else None,
            rs_trend_s5=i.rs_trend_s5 if i else None,
            rs_trend_s14=i.rs_trend_s14 if i else None,
            rs_trend_s21=i.rs_trend_s21 if i else None,
            rs_trend_s63=i.rs_trend_s63 if i else None,
            rs_trend_s200=i.rs_trend_s200 if i else None,
            vol_surge_rel_spy_21=i.vol_surge_rel_spy_21 if i else None,
            rel_vol_vs_spy_21=i.vol_surge_rel_spy_21 if i else None,
        ))
    
    return schemas.EtfFeatureItem(
        id=sym.id, ticker=sym.ticker, name=sym.name, close=float(dp.close or 0.0),
        change_1d_pct=float(change_1d_pct or 0.0), change_1w_pct=float(change_1w_pct or 0.0),
        change_1m_pct=float(change_1m_pct or 0.0), change_1y_pct=float(change_1y_pct or 0.0),
        dist_sma5_pct=float(dist_sma5_pct or 0.0), dist_sma21_pct=float(dist_sma21_pct or 0.0),
        dist_sma63_pct=float(dist_sma63_pct or 0.0), sma21_sma63_pct=float(sma21_sma63_pct or 0.0),
        dist_sma200_pct=float(dist_sma200_pct or 0.0),
        rs_ratio_e14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_e21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_e63=ind.rs_ratio_e63 if ind else None,
        rs14_sparkline=rs14_spark,
        rs21_sparkline=rs21_spark,
        rs63_sparkline=rs63_spark,
        rs_ratio_rank_e14=rank_e14,
        rs_ratio_rank_e21=rank_e21,
        rs_ratio_rank_e63=rank_e63,
        chart_data=chart_data,

        # Legacy fields for frontend compatibility
        rs_ratio_14=ind.rs_ratio_e14 if ind else None,
        rs_ratio_21=ind.rs_ratio_e21 if ind else None,
        rs_ratio_63=ind.rs_ratio_e63 if ind else None,
        rank_rs_ratio_14=rank_e14,
        rank_rs_ratio_21=rank_e21,
        rank_rs_ratio_63=rank_e63
    )

