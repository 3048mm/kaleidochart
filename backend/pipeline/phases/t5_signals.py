import logging
import pandas as pd
from typing import Optional
from sqlalchemy import func

from db.models import Symbol, DailyPrice, Indicator, MarketSignal
from pipeline.utils import sanitize_numeric
from indicators.calculate import calculate_market_signals
from indicators.market_signals import compute_breadth_momentum, SPY_LOOKBACK_MIN_BARS


def _get_parquet_spy_min_date() -> Optional["pd.Timestamp"]:
    """Parquet マスタの SPY 起点（最古日付）を取得する（§7-6(1)・5-7b）。

    遡り不足ガードが「SQLite の窓が Parquet に対して切り詰められているか」を
    判定するために使う。**読めない場合は None を返す**——黙って通さず、
    呼び出し側で従来の本数判定（`SPY_LOOKBACK_MIN_BARS`）にフォールバックさせる
    （安全側に倒す）。
    """
    try:
        import paths
        from pipeline.parquet_cache_manager import (
            get_latest_master_files, get_parquet_master_dir, get_pointer_file_path,
        )
        db_path = paths.resolve_db_path_for_init("stocktool", None)
        parquet_dir = get_parquet_master_dir(db_path)
        pointer_file = get_pointer_file_path(parquet_dir)
        cur = get_latest_master_files(pointer_file)
        if not cur:
            return None
        sym = pd.read_parquet(cur["symbols"], columns=["id", "ticker"])
        spy_rows = sym.loc[sym["ticker"] == "SPY", "id"]
        if spy_rows.empty:
            return None
        spy_id = int(spy_rows.iloc[0])
        prices = pd.read_parquet(
            cur["prices"], columns=["symbol_id", "date"],
            filters=[("symbol_id", "==", spy_id)],
        )
        if prices.empty:
            return None
        return pd.to_datetime(prices["date"]).min().date()
    except Exception:  # noqa: BLE001 — 読めない場合は None（フォールバックの合図）
        return None


def sync_phase_t5_signals(db, logger: logging.Logger):
    """Phase 5: Market Signals (T5) - Idempotent catch-up."""
    logger.info("--- Phase 5: Market Signal calculation START ---")
    spy_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "SPY").scalar()
    vix_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "^VIX").scalar()
    vxv_sym_id = db.query(Symbol.id).filter(Symbol.ticker == "^VIX3M").scalar()
    
    t3_dates = {r[0] for r in db.query(Indicator.date).distinct().filter(Indicator.symbol_id == spy_sym_id).all()}
    t5_completed_dates = {r[0] for r in db.query(MarketSignal.date).filter(MarketSignal.market_trend_score.is_not(None)).all()}
    gap_dates = sorted(list(t3_dates - t5_completed_dates))
    
    if not gap_dates:
        logger.info("No gaps or missing scores detected in Phase 5.")
        return

    spy_df = pd.DataFrame([{"date": r.date, "close": r.close, "high": r.high, "low": r.low, "volume": r.volume} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == spy_sym_id).order_by(DailyPrice.date).all()])
    if spy_df.empty:
        logger.error("SPY price data missing.")
        return
    spy_df['date'] = pd.to_datetime(spy_df['date'])

    # 遡り不足ガード（§4-1・§7-6(1)・5-7b）: gap_dates のうち最古の日付は、SPY の
    # 遡りが最も浅い（＝最も条件が厳しい）ため、そこだけ確認すれば足りる。
    #
    # 本数（220本未満）だけを見て即座に止めると、正当な全期間再構築（SQLite の
    # SPY が Parquet と同じ起点から入っている）まで必ず止まってしまう
    # （最古日付の遡りは定義上1本しかない）。また、ホット期間の古い日付に
    # NULL スコアが1件あるだけの正常な修復ケースも、その日付の遡りが220本に
    # 満たないだけで毎晩落ちる。5-9 で「遡り不足の日付は None（NaN）になる」
    # ようにした今、このガードの役割は「偽値の防止」ではなく「SQLite の窓が
    # Parquet に対して切り詰められている（＝取れるはずの履歴を使っていない）
    # ことの通知」である。そこで本数不足を検知した場合に限り、Parquet マスタの
    # SPY 起点と比較し、**Parquet にも同等以上の遡りが無い（=切り詰めではなく
    # 単なる履歴不足）なら通す**。デイリー（本数十分）はこの比較に到達しない。
    earliest_gap_date = min(gap_dates)
    spy_bars_before_gap = int((spy_df['date'] <= pd.Timestamp(earliest_gap_date)).sum())
    if spy_bars_before_gap < SPY_LOOKBACK_MIN_BARS:
        sqlite_spy_min_date = spy_df['date'].min().date()
        parquet_spy_min_date = _get_parquet_spy_min_date()

        if parquet_spy_min_date is None:
            # Parquet を読めない場合は黙って通さず、従来の本数判定に
            # フォールバックする（安全側に倒す）。
            logger.warning(
                "Parquet マスタの SPY 起点を取得できなかったため、遡り不足ガードは"
                " 従来の本数判定（SPY_LOOKBACK_MIN_BARS）にフォールバックします。"
            )
            raise RuntimeError(
                f"T5 の遡りが不足しています: {earliest_gap_date} 時点で SQLite の SPY は "
                f"{spy_bars_before_gap} 本しかありません（sma_200 の遡りに必要な "
                f"{SPY_LOOKBACK_MIN_BARS} 本に未達）。SQLite はホット期間（直近730日程度）"
                "しか保持していないため、この状態で全日付を再計算すると sma_200 の窓の"
                "先頭が壊れ、MTS の SPY 由来列（market_phase 等）に誤った値が保存されます。"
                " `--rebuild-from T5` など Parquet 基点の再構築手順を使ってください。"
            )
        elif sqlite_spy_min_date > parquet_spy_min_date:
            # SQLite の窓が Parquet に対して切り詰められている
            # （Parquet には使えるはずの履歴があるのに SQLite に無い）。
            raise RuntimeError(
                f"T5 の SQLite 窓が Parquet マスタに対して切り詰められています: "
                f"SQLite 起点={sqlite_spy_min_date}, Parquet 起点={parquet_spy_min_date}"
                f"（{earliest_gap_date} 時点で SQLite の SPY は {spy_bars_before_gap} 本、"
                f"sma_200 の遡りに必要な {SPY_LOOKBACK_MIN_BARS} 本に未達）。この状態で"
                "全日付を再計算すると sma_200 の窓の先頭が壊れ、MTS の SPY 由来列"
                "（market_phase 等）に誤った値が保存されます。"
                " `--rebuild-from T5` など Parquet 基点の再構築手順を使ってください。"
            )
        else:
            # SQLite 起点と Parquet 起点が一致（=切り詰めではなく、そもそも
            # Parquet にもこれ以上の履歴が無い）。遡り不足の先頭日は
            # calculate_market_signals() 側で None になる（5-9）ため、
            # 偽の値が書かれることは無い。通す。
            logger.info(
                f"T5: {earliest_gap_date} 時点の SPY 遡りは {spy_bars_before_gap} 本"
                f"（{SPY_LOOKBACK_MIN_BARS} 本未満）ですが、Parquet マスタの SPY 起点"
                f"（{parquet_spy_min_date}）も SQLite と同じため切り詰めではないと判断し、"
                "計算を続行します（遡り不足の先頭日は NaN として書き込まれます）。"
            )

    vix_df = pd.DataFrame()
    if vix_sym_id:
        vix_df = pd.DataFrame([{"date": r.date, "close": r.close} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == vix_sym_id).order_by(DailyPrice.date).all()])
        if not vix_df.empty:
            vix_df['date'] = pd.to_datetime(vix_df['date'])

    vxv_df = pd.DataFrame()
    if vxv_sym_id:
        vxv_df = pd.DataFrame([{"date": r.date, "close": r.close} for r in db.query(DailyPrice).filter(DailyPrice.symbol_id == vxv_sym_id).order_by(DailyPrice.date).all()])
        if not vxv_df.empty:
            vxv_df['date'] = pd.to_datetime(vxv_df['date'])
    
    active_stock_ids = [r[0] for r in db.query(Symbol.id).filter(Symbol.active == 1, Symbol.category == '個別').all()]
    
    if not active_stock_ids:
        logger.warning("No active '個別' stocks found for metrics calculation.")
        metrics_df = pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])
    else:
        logger.info(f"Phase 5: Vectorizing breadth and momentum calculations for {len(gap_dates)} dates...")
        
        # Optimize query by filtering on the earliest gap date (with 10-day buffer for shift(1))
        # to prevent loading the entire 5.8+ million rows history.
        from datetime import timedelta
        min_gap_date = min(gap_dates)
        start_filter_date = min_gap_date - timedelta(days=10)
        start_date_str = start_filter_date.strftime('%Y-%m-%d')
        
        query_metrics = f"""
            SELECT dp.date, dp.symbol_id, dp.close, i.sma_50
            FROM daily_prices dp
            JOIN symbols s ON s.id = dp.symbol_id
            JOIN indicators i ON i.symbol_id = dp.symbol_id AND i.date = dp.date
            WHERE s.active = 1 AND s.category = '個別' AND dp.date >= '{start_date_str}'
            ORDER BY dp.symbol_id, dp.date
        """
        # 自己デッドロック防止: commit してロック解放後、読み取りエンジンで読む
        db.commit()
        from db.database import get_read_engine_for
        raw_df = pd.read_sql(query_metrics, get_read_engine_for(db))

        if not raw_df.empty:
            raw_df['date'] = pd.to_datetime(raw_df['date'])
            metrics_df = compute_breadth_momentum(raw_df)
        else:
            metrics_df = pd.DataFrame(columns=['date', 'breadth_sma50', 'momentum_ratio'])

    ms_df = calculate_market_signals(spy_df, vix_df, vxv_df, metrics_df)
    
    gap_dt = [pd.to_datetime(d) for d in gap_dates]
    ms_df = ms_df[ms_df['date'].isin(gap_dt)]
    
    if ms_df.empty:
        logger.info("No signals found for the target gap dates.")
        return

    # Build a date-indexed lookup for breadth_sma50
    breadth_by_date = {}
    if not metrics_df.empty and 'breadth_sma50' in metrics_df.columns:
        for _, r in metrics_df.iterrows():
            d = r['date']
            d_key = d.date() if hasattr(d, 'date') else d
            breadth_by_date[d_key] = float(r['breadth_sma50'])

    t5_recs = []
    for _, row in ms_df.iterrows():
        row_date = row['date'].date()
        t5_recs.append(MarketSignal(
            date=row_date,
            # spy_above_sma200 / distribution_days は SPY の遡りが足りない先頭区間で
            # None（判定不能）になりうる（indicators/market_signals.py 参照）。
            # spy_sma200_rising と同じ sanitize_numeric によるガードを掛ける。
            spy_above_sma200=int(row['spy_above_sma200']) if sanitize_numeric(row, 'spy_above_sma200') is not None else None,
            spy_sma200_rising=int(row['spy_sma200_rising']) if sanitize_numeric(row, 'spy_sma200_rising') is not None else None,
            distribution_days=int(row['distribution_days']) if sanitize_numeric(row, 'distribution_days') is not None else None,
            is_distribution_day=int(row['is_distribution_day']),
            follow_through_day=int(row['follow_through_day']),
            market_phase=row['market_phase'],
            market_trend_score=float(row['market_trend_score']) if sanitize_numeric(row, 'market_trend_score') is not None else None,
            vxv_vix_ratio=float(row['vxv_vix_ratio']) if sanitize_numeric(row, 'vxv_vix_ratio') is not None else None,
            breadth_sma50=breadth_by_date.get(row_date)
        ))
    
    db.query(MarketSignal).filter(MarketSignal.date.in_([r.date for r in t5_recs])).delete(synchronize_session=False)
    db.bulk_save_objects(t5_recs)
    db.commit()
    logger.info(f"Phase 5 COMPLETE: Saved {len(t5_recs)} signal records.")
