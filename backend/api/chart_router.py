"""Chart API router: /chart/{symbol_id}, /earnings/{symbol_id} (audit D-1 で routers.py から分割)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session, aliased
from sqlalchemy import desc, func, or_, and_, select
from typing import List, Optional, Dict, Any
from datetime import date as dt_date, timedelta
import os, re, logging
from db.models import Symbol, DailyPrice, Indicator, RelativeRank, MarketSignal, ThemeConstituent, Earning
from api import schemas
from api.deps import get_api_db, get_api_user_db
from indicators.structure_pivot import (DEFAULT_MAX_LEN, DEFAULT_MIN_LEN,
                                        find_counter_trends, find_structures)
from indicators.zone_break import find_zone_break_zones, zone_break_series

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_active_db_path() -> str:
    """Parquet マスターの場所を解決するための DB パスを返す。"""
    from db.database import get_active_db_path
    db_path = get_active_db_path()
    if db_path:
        return db_path
    try:
        import tomllib
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.toml")
        with open(config_path, "rb") as f:
            return tomllib.load(f).get("system", {}).get("db_path", "data/stocktool.db")
    except Exception:
        return "data/stocktool.db"


@router.get("/chart/{symbol_id}")
def get_chart_data(symbol_id: int, db: Session = Depends(get_api_db), full_range: bool = Query(False)):
    """T2+T3: Get combined daily prices and indicators for rendering charts (High speed)"""
    if symbol_id in (99998, 99999):
        import pandas as pd
        import numpy as np
        import json
        from fastapi import Response
        
        raw_points = []
        
        if full_range is True:
            # Try loading/calculating from Parquet
            from pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files
            db_path = _resolve_active_db_path()
            parquet_dir = get_parquet_master_dir(db_path)
            pointer_file = get_pointer_file_path(parquet_dir)
            latest_files = get_latest_master_files(pointer_file)
            
            if latest_files:
                try:
                    df_sym = pd.read_parquet(latest_files['symbols'])
                    vix_id = int(df_sym[df_sym['ticker'] == '^VIX']['id'].iloc[0])
                    vxv_id = int(df_sym[df_sym['ticker'] == '^VIX3M']['id'].iloc[0])
                    spy_id = int(df_sym[df_sym['ticker'] == 'SPY']['id'].iloc[0])
                    
                    if symbol_id == 99999:
                        # VXV Ratio: compute from VXV and VIX closes
                        df_p_vix = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', vix_id)])
                        df_p_vxv = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', vxv_id)])
                        
                        df_p_vix = df_p_vix.sort_values("date").reset_index(drop=True)
                        df_p_vxv = df_p_vxv.sort_values("date").reset_index(drop=True)
                        
                        vix_ref = df_p_vix[['date', 'close']].rename(columns={'close': 'vix_close'})
                        vxv_ref = df_p_vxv[['date', 'close']].rename(columns={'close': 'vxv_close'})
                        merged_vix = pd.merge(vxv_ref, vix_ref, on='date', how='inner')
                        merged_vix['val'] = merged_vix['vxv_close'] / merged_vix['vix_close']
                        merged_vix = merged_vix.sort_values("date").reset_index(drop=True)
                        
                        for _, row in merged_vix.iterrows():
                            val = row['val']
                            if val is not None and not pd.isna(val):
                                raw_points.append({
                                    "date": pd.to_datetime(row["date"]).date(),
                                    "close": float(val)
                                })
                    else:
                        # MTS: compute using MarketTrendScorer
                        from backend.backtest.scenario_market_score import MarketTrendScorer
                        df_p_subset = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', 'in', [spy_id, vix_id, vxv_id])])
                        scorer = MarketTrendScorer(df_p_subset, df_sym, daily_metrics={}, use_vxv_vix=True, scaling_ratio=None)
                        df_p_spy = df_p_subset[df_p_subset['symbol_id'] == spy_id].sort_values("date")
                        
                        for d in df_p_spy['date'].tolist():
                            try:
                                score, _ = scorer.evaluate_market_phase(d)
                            except Exception:
                                score = 70.0
                            raw_points.append({
                                "date": pd.to_datetime(d).date(),
                                "close": float(score)
                            })
                            
                    # SQLite から Parquet データの最新日より新しい差分データを取得してマージ
                    max_p_date = None
                    if raw_points:
                        max_p_date = max(pt['date'] for pt in raw_points)
 
                    if max_p_date:
                        signals_newer = db.query(MarketSignal).filter(
                            MarketSignal.date > max_p_date
                        ).order_by(MarketSignal.date.asc()).all()
 
                        for s in signals_newer:
                            if symbol_id == 99999:
                                val = float(s.vxv_vix_ratio) if s.vxv_vix_ratio is not None else None
                            else:
                                val = float(s.market_trend_score) if s.market_trend_score is not None else None
 
                            if val is not None:
                                raw_points.append({
                                    "date": s.date,
                                    "close": val
                                })
                except Exception as ex:
                    logger.error(f"Error dynamically computing virtual symbols: {ex}")
                    
        # Fallback to SQLite if full_range is False or Parquet loading failed
        if not raw_points:
            signals = db.query(MarketSignal).order_by(MarketSignal.date.asc()).all()
            for s in signals:
                if symbol_id == 99999:
                    val = float(s.vxv_vix_ratio) if s.vxv_vix_ratio is not None else None
                else:
                    val = float(s.market_trend_score) if s.market_trend_score is not None else None
                    
                if val is not None:
                    raw_points.append({
                        "date": s.date,
                        "close": val
                    })
                
        chart_data = []
        ticker = "^VXV_VIX" if symbol_id == 99999 else "^MKT_TREND"
        name = "VXV/VIX Ratio" if symbol_id == 99999 else "Market Trend Score"
        
        if raw_points:
            df = pd.DataFrame(raw_points)
            df = df.sort_values("date").reset_index(drop=True)
            
            # 動的インジケーター計算
            for p in [5, 21, 50, 63, 150, 200]:
                df[f"sma_{p}"] = df["close"].rolling(window=p, min_periods=p).mean()
                df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False, min_periods=p).mean()

            # ボリンジャーバンド
            std_21 = df["close"].rolling(window=21, min_periods=21).std()
            df["bb_upper"] = df["sma_21"] + 2 * std_21
            df["bb_lower"] = df["sma_21"] - 2 * std_21
            
            # NaN の処理
            df = df.replace({np.nan: None})
            
            for _, row in df.iterrows():
                d_str = row["date"].strftime('%Y-%m-%d')
                val = row["close"]
                point = {
                    "time": d_str,
                    "open": val,
                    "high": val,
                    "low": val,
                    "close": val,
                    "volume": 0,
                    "market_cap": None,
                    "sma_5": row["sma_5"],
                    "sma_21": row["sma_21"],
                    "sma_50": row["sma_50"],
                    "sma_63": row["sma_63"],
                    "sma_150": row["sma_150"],
                    "sma_200": row["sma_200"],
                    "ema_5": row["ema_5"],
                    "ema_21": row["ema_21"],
                    "ema_50": row["ema_50"],
                    "ema_63": row["ema_63"],
                    "ema_200": row["ema_200"],
                    "bb_upper": row["bb_upper"],
                    "bb_lower": row["bb_lower"],
                    "change_1d_pct": None,
                    "change_1w_pct": None,
                    "change_1m_pct": None,
                    "adr_pct_21": None,
                    "dist_sma50_atr": None,
                    "sma50_atr_mult": None,
                    "relative_strength_spy": None,
                    "rs_value": None
                }
                chart_data.append(point)
                
        # Custom encoder/cleaner to handle NaN/Inf
        def clean_data(obj):
            if isinstance(obj, float):
                if obj != obj or obj == float('inf') or obj == float('-inf'):
                    return None
            return obj
            
        cleaned_data = {
            "metadata": {"id": symbol_id, "ticker": ticker, "name": name, "category": "市場指標"},
            "themes": [],
            "data": [
                {k: clean_data(v) for k, v in point.items()}
                for point in chart_data
            ]
        }
        return Response(
            content=json.dumps(cleaned_data, allow_nan=False),
            media_type="application/json"
        )

    symbol = db.query(Symbol).filter(Symbol.id == symbol_id, Symbol.active == 1).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    if full_range is True:
        # Load data from Parquet Master cache
        import pandas as pd
        import numpy as np
        from pipeline.parquet_cache_manager import get_parquet_master_dir, get_pointer_file_path, get_latest_master_files
        from fastapi.responses import JSONResponse

        db_path = _resolve_active_db_path()
        parquet_dir = get_parquet_master_dir(db_path)
        pointer_file = get_pointer_file_path(parquet_dir)
        latest_files = get_latest_master_files(pointer_file)

        if latest_files:
            try:
                df_p = pd.read_parquet(latest_files['prices'], filters=[('symbol_id', '==', symbol_id)])
                if df_p.empty:
                    return JSONResponse(content={"data": [], "themes": []})

                df_p = df_p.sort_values("date").reset_index(drop=True)
                df_p['date_str'] = pd.to_datetime(df_p['date']).dt.strftime('%Y-%m-%d')

                # SQLite から Parquet データの最新日より新しい差分データを取得してマージ
                max_p_date = None
                if not df_p.empty:
                    max_p_date_raw = df_p['date'].max()
                    if isinstance(max_p_date_raw, str):
                        max_p_date = pd.to_datetime(max_p_date_raw).date()
                    elif hasattr(max_p_date_raw, 'date'):
                        max_p_date = max_p_date_raw.date()
                    else:
                        max_p_date = pd.to_datetime(max_p_date_raw).date()

                if max_p_date:
                    prices_newer = db.query(DailyPrice).filter(
                        DailyPrice.symbol_id == symbol_id,
                        DailyPrice.date > max_p_date
                    ).order_by(DailyPrice.date.asc()).all()

                    if prices_newer:
                        newer_rows = []
                        for p in prices_newer:
                            newer_rows.append({
                                'symbol_id': symbol_id,
                                'date': p.date,
                                'open': p.open,
                                'high': p.high,
                                'low': p.low,
                                'close': p.close,
                                'volume': p.volume,
                                'market_cap': p.market_cap,
                                'date_str': p.date.strftime('%Y-%m-%d')
                            })
                        df_newer = pd.DataFrame(newer_rows)
                        df_p = pd.concat([df_p, df_newer], ignore_index=True)
                        df_p = df_p.sort_values("date").reset_index(drop=True)

                if full_range is True:
                    # Calculate basic indicators dynamically to cover the full historical range
                    close = df_p['close']
                    high = df_p['high']
                    low = df_p['low']

                    for p_val in [5, 21, 50, 63, 150, 200]:
                        df_p[f"calc_sma_{p_val}"] = close.rolling(window=p_val, min_periods=p_val).mean()
                        df_p[f"calc_ema_{p_val}"] = close.ewm(span=p_val, adjust=False, min_periods=p_val).mean()

                    df_p["calc_change_1d_pct"] = close.pct_change(1) * 100.0
                    df_p["calc_change_1w_pct"] = close.pct_change(5) * 100.0
                    df_p["calc_change_1m_pct"] = close.pct_change(21) * 100.0

                    close_prev = close.shift(1)
                    tr = pd.concat([
                        high - low,
                        (high - close_prev).abs(),
                        (low - close_prev).abs()
                    ], axis=1).max(axis=1)
                    df_p["calc_atr_14"] = tr.rolling(window=14, min_periods=1).mean().ffill().fillna(1.0)
                    df_p["calc_atr_pct_14"] = np.where(close == 0, 0.0, (df_p["calc_atr_14"] / close) * 100.0)
                    df_p["calc_sma50_atr_mult"] = np.where(
                        df_p["calc_atr_pct_14"].isna() | (df_p["calc_atr_pct_14"] == 0) | df_p["calc_sma_50"].isna() | (df_p["calc_sma_50"] == 0),
                        None,
                        ((close / df_p["calc_sma_50"] * 100) - 100) / df_p["calc_atr_pct_14"]
                    )

                try:
                    df_i = pd.read_parquet(latest_files['indicators'], filters=[('symbol_id', '==', symbol_id)])
                    if not df_i.empty:
                        df_i['date_str'] = pd.to_datetime(df_i['date']).dt.strftime('%Y-%m-%d')
                        ind_cols = [c for c in df_i.columns if c not in ('id', 'symbol_id', 'date', 'date_str')]
                        df_i_indexed = df_i.set_index('date_str')
                        ind_map = df_i_indexed[ind_cols].to_dict(orient='index')
                    else:
                        ind_map = {}
                except Exception as e:
                    logger.error(f"Error reading parquet indicators: {e}")
                    ind_map = {}

                # SQLite から最新の Indicators を取得してマージ
                max_i_date = None
                if not df_i.empty:
                    max_i_date_raw = df_i['date'].max()
                    if isinstance(max_i_date_raw, str):
                        max_i_date = pd.to_datetime(max_i_date_raw).date()
                    elif hasattr(max_i_date_raw, 'date'):
                        max_i_date = max_i_date_raw.date()
                    else:
                        max_i_date = pd.to_datetime(max_i_date_raw).date()

                try:
                    if max_i_date:
                        indicators_newer = db.query(Indicator).filter(
                            Indicator.symbol_id == symbol_id,
                            Indicator.date > max_i_date
                        ).all()
                    else:
                        indicators_newer = db.query(Indicator).filter(
                            Indicator.symbol_id == symbol_id
                        ).all()
                    for ind in indicators_newer:
                        ds = ind.date.strftime('%Y-%m-%d')
                        ind_dict = {}
                        for col in ind.__table__.columns.keys():
                            if col not in ('id', 'symbol_id', 'date'):
                                val = getattr(ind, col)
                                if val is not None:
                                    ind_dict[col] = float(val) if isinstance(val, (int, float)) else val
                                else:
                                    ind_dict[col] = None
                        ind_map[ds] = ind_dict
                except Exception as e:
                    logger.error(f"Error merging newer SQLite indicators: {e}")

                try:
                    df_r = pd.read_parquet(latest_files['ranks'], filters=[('symbol_id', '==', symbol_id)])
                    if not df_r.empty:
                        df_r['date_str'] = pd.to_datetime(df_r['date']).dt.strftime('%Y-%m-%d')
                        rank_indicators = [
                            'rs_ratio_rank_e5', 'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63', 'rs_ratio_rank_e200',
                            'rs_momentum_rank_e5', 'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63', 'rs_momentum_rank_e200',
                            'rs_trend_rank_s5', 'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63', 'rs_trend_rank_s200',
                            'rs_roc_ema_rank_e5', 'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63', 'rs_roc_ema_rank_e200',
                            'rs_macd_hist_rank_21', 'rs_value_rank'
                        ]
                        df_r['is_kobetsu'] = df_r['group_name'] == '個別'
                        df_r = df_r.sort_values('is_kobetsu', ascending=True)
                        
                        rank_map_nested = {}
                        for _, row in df_r.iterrows():
                            ds = row['date_str']
                            if ds not in rank_map_nested:
                                rank_map_nested[ds] = {}
                            for ind_name in rank_indicators:
                                val = row.get(ind_name)
                                if val is not None and not pd.isna(val):
                                    rank_map_nested[ds][ind_name] = float(val)
                    else:
                        rank_map_nested = {}
                except Exception as e:
                    logger.error(f"Error reading parquet ranks: {e}")
                    rank_map_nested = {}

                # SQLite から最新の Ranks を取得してマージ
                max_r_date = None
                if not df_r.empty:
                    max_r_date_raw = df_r['date'].max()
                    if isinstance(max_r_date_raw, str):
                        max_r_date = pd.to_datetime(max_r_date_raw).date()
                    elif hasattr(max_r_date_raw, 'date'):
                        max_r_date = max_r_date_raw.date()
                    else:
                        max_r_date = pd.to_datetime(max_r_date_raw).date()

                try:
                    if max_r_date:
                        ranks_newer = db.query(RelativeRank).filter(
                            RelativeRank.symbol_id == symbol_id,
                            RelativeRank.date > max_r_date
                        ).all()
                    else:
                        ranks_newer = db.query(RelativeRank).filter(
                            RelativeRank.symbol_id == symbol_id
                        ).all()
                    for r in ranks_newer:
                        ds = r.date.strftime('%Y-%m-%d')
                        if ds not in rank_map_nested:
                            rank_map_nested[ds] = {}
                        is_kobetsu = (r.group_name == '個別')
                        for ind_name in rank_indicators:
                            val = getattr(r, ind_name, None)
                            if val is not None:
                                if is_kobetsu or ind_name not in rank_map_nested[ds]:
                                    rank_map_nested[ds][ind_name] = float(val)
                except Exception as e:
                    logger.error(f"Error merging newer SQLite ranks: {e}")

                theme_meta = []
                if symbol.tags:
                    tag_list = [t.strip() for t in symbol.tags.split(',') if t.strip()]
                    tag_themes = db.query(Symbol).filter(Symbol.ticker.in_(tag_list)).all()
                    for t in tag_themes:
                        theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})
                
                tc_themes = db.query(Symbol).join(ThemeConstituent, Symbol.id == ThemeConstituent.theme_id).filter(ThemeConstituent.symbol_id == symbol_id).all()
                for t in tc_themes:
                    if not any(tm['ticker'] == t.ticker for tm in theme_meta):
                        theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})

                chart_data = []
                for _, p in df_p.iterrows():
                    d_str = p['date_str']
                    ind = ind_map.get(d_str, {})
                    
                    point = {
                        "time": d_str, 
                        "open": float(p['open']) if not pd.isna(p['open']) else None, 
                        "high": float(p['high']) if not pd.isna(p['high']) else None, 
                        "low": float(p['low']) if not pd.isna(p['low']) else None, 
                        "close": float(p['close']) if not pd.isna(p['close']) else None, 
                        "volume": float(p['volume']) if not pd.isna(p['volume']) else 0,
                        "market_cap": float(p['market_cap']) if not pd.isna(p['market_cap']) else None
                    }
                    
                    def val_or_none(k, calc_fallback_key=None, force_calc=False):
                        if force_calc and full_range is True and calc_fallback_key and calc_fallback_key in p:
                            fv = p[calc_fallback_key]
                            if fv is not None and not pd.isna(fv):
                                return float(fv)
                        v = ind.get(k) if ind else None
                        if v is not None and not pd.isna(v):
                            return float(v)
                        if full_range is True and calc_fallback_key and calc_fallback_key in p:
                            fv = p[calc_fallback_key]
                            if fv is not None and not pd.isna(fv):
                                return float(fv)
                        return None
                    
                    def bool_or_none(k):
                        v = ind.get(k) if ind else None
                        if v is None or pd.isna(v):
                            return None
                        return bool(v)

                    sma_21_val = val_or_none("sma_21", "calc_sma_21", force_calc=True)
                    atr_14_val = val_or_none("atr_14", "calc_atr_14", force_calc=True)
                    
                    point.update({
                        "sma_5": val_or_none("sma_5", "calc_sma_5", force_calc=True),
                        "sma_21": sma_21_val, 
                        "sma_50": val_or_none("sma_50", "calc_sma_50", force_calc=True), 
                        "sma_63": val_or_none("sma_63", "calc_sma_63", force_calc=True),
                        "sma_150": val_or_none("sma_150", "calc_sma_150", force_calc=True), 
                        "sma_200": val_or_none("sma_200", "calc_sma_200", force_calc=True),
                        "ema_5": val_or_none("ema_5", "calc_ema_5", force_calc=True), 
                        "ema_21": val_or_none("ema_21", "calc_ema_21", force_calc=True), 
                        "ema_50": val_or_none("ema_50", "calc_ema_50", force_calc=True),
                        "ema_63": val_or_none("ema_63", "calc_ema_63", force_calc=True), 
                        "ema_150": val_or_none("ema_150", "calc_ema_150", force_calc=True), 
                        "ema_200": val_or_none("ema_200", "calc_ema_200", force_calc=True),
                        "td9": val_or_none("td9"), 
                        "atr_14": atr_14_val, 
                        "atr_pct_14": val_or_none("atr_pct_14", "calc_atr_pct_14", force_calc=True),
                        "adr_pct_21": val_or_none("adr_pct_21"), 
                        "sma50_atr_mult": val_or_none("sma50_atr_mult", "calc_sma50_atr_mult", force_calc=True),
                        "dist_sma50_atr": val_or_none("sma50_atr_mult", "calc_sma50_atr_mult", force_calc=True), # Legacy compat
                        "relative_strength_spy": val_or_none("rs_value"), # Legacy compat
                        "rs_condition_14": val_or_none("rs_trend_s14"), # Legacy compat
                        "rs_condition_21": val_or_none("rs_trend_s21"), # Legacy compat
                        "rs_condition_63": val_or_none("rs_trend_s63"), # Legacy compat
                        "rs_ema_14": val_or_none("rs_value_e14"), # Legacy compat
                        "rs_ema_21": val_or_none("rs_value_e21"), # Legacy compat
                        "rs_ema_63": val_or_none("rs_value_e63"), # Legacy compat
                        "rs_momentum_14": val_or_none("rs_momentum_e14"), # Legacy compat
                        "rs_momentum_21": val_or_none("rs_momentum_e21"), # Legacy compat
                        "rs_momentum_63": val_or_none("rs_momentum_e63"), # Legacy compat
                        "rs_ratio_14": val_or_none("rs_ratio_e14"), # Legacy compat
                        "rs_ratio_21": val_or_none("rs_ratio_e21"), # Legacy compat
                        "rs_ratio_63": val_or_none("rs_ratio_e63"), # Legacy compat
                        "rel_vol_vs_spy_21": val_or_none("vol_surge_rel_spy_21"), # Legacy compat
                        "pct_from_63d_high": val_or_none("dist_63d_high_pct"), # Legacy compat
                        "pct_from_52w_high": val_or_none("dist_52w_high_pct"), # Legacy compat
                        "trend_template_ok": val_or_none("is_trend_template"), # Legacy compat
                        "change_1d_pct": val_or_none("change_1d_pct", "calc_change_1d_pct", force_calc=True),
                        "change_1w_pct": val_or_none("change_1w_pct", "calc_change_1w_pct", force_calc=True),
                        "change_1m_pct": val_or_none("change_1m_pct", "calc_change_1m_pct", force_calc=True),
                        "rs_value": val_or_none("rs_value"),
                        "rs_trend_s5": val_or_none("rs_trend_s5"),
                        "rs_trend_s14": val_or_none("rs_trend_s14"),
                        "rs_trend_s21": val_or_none("rs_trend_s21"),
                        "rs_trend_s63": val_or_none("rs_trend_s63"),
                        "rs_trend_s200": val_or_none("rs_trend_s200"),
                        "rs_macd_line_21": val_or_none("rs_macd_line_21"),
                        "rs_macd_signal_21": val_or_none("rs_macd_signal_21"),
                        "rs_macd_hist_21": val_or_none("rs_macd_hist_21"),
                        "rs_value_e5": val_or_none("rs_value_e5"),
                        "rs_value_e14": val_or_none("rs_value_e14"),
                        "rs_value_e21": val_or_none("rs_value_e21"),
                        "rs_value_e63": val_or_none("rs_value_e63"),
                        "rs_value_e200": val_or_none("rs_value_e200"),
                        "rs_momentum_e5": val_or_none("rs_momentum_e5"),
                        "rs_momentum_e14": val_or_none("rs_momentum_e14"),
                        "rs_momentum_e21": val_or_none("rs_momentum_e21"),
                        "rs_momentum_e63": val_or_none("rs_momentum_e63"),
                        "rs_momentum_e200": val_or_none("rs_momentum_e200"),
                        "rs_ratio_e5": val_or_none("rs_ratio_e5"),
                        "rs_ratio_e14": val_or_none("rs_ratio_e14"),
                        "rs_ratio_e21": val_or_none("rs_ratio_e21"),
                        "rs_ratio_e63": val_or_none("rs_ratio_e63"),
                        "rs_ratio_e200": val_or_none("rs_ratio_e200"),
                        "rs_roc_ema_5": val_or_none("rs_roc_ema_5"),
                        "rs_roc_ema_14": val_or_none("rs_roc_ema_14"),
                        "rs_roc_ema_21": val_or_none("rs_roc_ema_21"),
                        "rs_roc_ema_63": val_or_none("rs_roc_ema_63"),
                        "rs_roc_ema_200": val_or_none("rs_roc_ema_200"),
                        "vol_surge_21": val_or_none("vol_surge_21"),
                        "vol_surge_rel_spy_21": val_or_none("vol_surge_rel_spy_21"),
                        "up_down_vol_ratio_50": val_or_none("up_down_vol_ratio_50"),
                        "dist_63d_high_pct": val_or_none("dist_63d_high_pct"),
                        "dist_52w_high_pct": val_or_none("dist_52w_high_pct"),
                        "rs_blue_dot_age": val_or_none("rs_blue_dot_age"),
                        "rs_red_dot_age": val_or_none("rs_red_dot_age"),
                        "vcr": val_or_none("vcr"), 
                        # 構造ピボット (LL-HL)。DATA VIEW の Pivot / Pivot HL 列が読む
                        "sp_pivot": val_or_none("sp_pivot"),
                        "sp_counter": val_or_none("sp_counter"),
                        "sp_hl": val_or_none("sp_hl"),
                        "is_trend_template": bool_or_none("is_trend_template"),
                        "vol_accum_days_5": val_or_none("vol_accum_days_5"),
                        "bb_upper": (sma_21_val + 2 * atr_14_val) if sma_21_val and atr_14_val else None,
                        "bb_lower": (sma_21_val - 2 * atr_14_val) if sma_21_val and atr_14_val else None,
                    })

                    r_data = rank_map_nested.get(d_str, {})
                    for r_name in rank_indicators:
                        point[r_name] = r_data.get(r_name)
                    
                    # Include legacy rank keys for compatibility
                    point["rank_rs_ratio_14"] = r_data.get("rs_ratio_rank_e14")
                    point["rank_rs_ratio_21"] = r_data.get("rs_ratio_rank_e21")
                    point["rank_rs_ratio_63"] = r_data.get("rs_ratio_rank_e63")
                    point["rank_rs_momentum_14"] = r_data.get("rs_momentum_rank_e14")
                    point["rank_rs_momentum_21"] = r_data.get("rs_momentum_rank_e21")
                    point["rank_rs_momentum_63"] = r_data.get("rs_momentum_rank_e63")
                    point["rank_rs_condition_14"] = r_data.get("rs_trend_rank_s14")
                    point["rank_rs_condition_21"] = r_data.get("rs_trend_rank_s21")
                    point["rank_rs_condition_63"] = r_data.get("rs_trend_rank_s63")
                    
                    point["rs_ratio"] = r_data.get("rs_ratio_rank_e21")

                    chart_data.append(point)

                import json
                from fastapi import Response

                def clean_data(obj):
                    if isinstance(obj, float):
                        if obj != obj or obj == float('inf') or obj == float('-inf'):
                            return None
                    return obj

                cleaned_data = {
                    "metadata": {"id": symbol.id, "ticker": symbol.ticker, "name": symbol.name, "category": symbol.category},
                    "themes": theme_meta,
                    "data": [
                        {k: clean_data(v) for k, v in point.items()}
                        for point in chart_data
                    ]
                }

                return Response(
                    content=json.dumps(cleaned_data, allow_nan=False),
                    media_type="application/json"
                )

            except Exception as ex:
                logger.error(f"Error building chart from Parquet: {ex}")
                # Fallback to standard SQLite DB query
                pass

    # Fetch all prices
    prices = db.query(DailyPrice).filter(DailyPrice.symbol_id == symbol_id).order_by(DailyPrice.date.asc()).all()
    if not prices:
        return JSONResponse(content={"data": [], "themes": []})

    dates = [p.date for p in prices]
    date_strs = [d.strftime('%Y-%m-%d') for d in dates]

    # Indicators
    # Fetch related indicators (More efficient query without large IN clause)
    start_date = prices[0].date
    end_date = prices[-1].date
    indicators = db.query(Indicator).filter(
        Indicator.symbol_id == symbol_id,
        Indicator.date >= start_date,
        Indicator.date <= end_date
    ).all()
    ind_map = {i.date.strftime('%Y-%m-%d'): i for i in indicators}
    
    # RS Ranks (Optimized range query for all timeframes and types)
    ranks = db.query(RelativeRank).filter(
        RelativeRank.symbol_id == symbol_id,
        RelativeRank.date >= start_date,
        RelativeRank.date <= end_date
    ).all()
    
    # Organize ranks by date and indicator. 
    # If multiple groups exist for a symbol, prioritize '個別' or just take the latest found.
    rank_indicators = [
        'rs_ratio_rank_e5', 'rs_ratio_rank_e14', 'rs_ratio_rank_e21', 'rs_ratio_rank_e63', 'rs_ratio_rank_e200',
        'rs_momentum_rank_e5', 'rs_momentum_rank_e14', 'rs_momentum_rank_e21', 'rs_momentum_rank_e63', 'rs_momentum_rank_e200',
        'rs_trend_rank_s5', 'rs_trend_rank_s14', 'rs_trend_rank_s21', 'rs_trend_rank_s63', 'rs_trend_rank_s200',
        'rs_roc_ema_rank_e5', 'rs_roc_ema_rank_e14', 'rs_roc_ema_rank_e21', 'rs_roc_ema_rank_e63', 'rs_roc_ema_rank_e200',
        'rs_macd_hist_rank_21', 'rs_value_rank'
    ]
    rank_map_nested = {}
    for r in ranks:
        ds = r.date.strftime('%Y-%m-%d')
        if ds not in rank_map_nested:
            rank_map_nested[ds] = {}
        
        is_kobetsu = (r.group_name == '個別')
        for ind_name in rank_indicators:
            val = getattr(r, ind_name, None)
            if val is not None:
                if is_kobetsu or ind_name not in rank_map_nested[ds]:
                    rank_map_nested[ds][ind_name] = val

    # Themes
    theme_meta = []
    if symbol.tags:
        tag_list = [t.strip() for t in symbol.tags.split(',') if t.strip()]
        tag_themes = db.query(Symbol).filter(Symbol.ticker.in_(tag_list)).all()
        for t in tag_themes:
            theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})
    
    tc_themes = db.query(Symbol).join(ThemeConstituent, Symbol.id == ThemeConstituent.theme_id).filter(ThemeConstituent.symbol_id == symbol_id).all()
    for t in tc_themes:
        if not any(tm['ticker'] == t.ticker for tm in theme_meta):
            theme_meta.append({"id": t.id, "ticker": t.ticker, "name": t.name})

    # Optimized mapping to dict (bypasses Pydantic validation for speed)
    chart_data = []
    for p in prices:
        d_str = str(p.date)
        ind = ind_map.get(d_str)
        point = {
            "time": d_str, "open": p.open, "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
            "market_cap": p.market_cap
        }
        if ind:
            point.update({
                "sma_5": ind.sma_5,
                "sma_21": ind.sma_21, "sma_50": ind.sma_50, "sma_63": ind.sma_63,
                "sma_150": ind.sma_150, "sma_200": ind.sma_200,
                "ema_5": ind.ema_5, "ema_21": ind.ema_21, "ema_50": ind.ema_50,
                "ema_63": ind.ema_63, "ema_150": ind.ema_150, "ema_200": ind.ema_200,
                "td9": ind.td9, "atr_14": ind.atr_14, "atr_pct_14": ind.atr_pct_14,
                "adr_pct_21": ind.adr_pct_21, "sma50_atr_mult": ind.sma50_atr_mult,
                "dist_sma50_atr": ind.sma50_atr_mult, # Legacy compat
                "change_1d_pct": ind.change_1d_pct,
                "change_1w_pct": ind.change_1w_pct,
                "change_1m_pct": ind.change_1m_pct,
                "rs_value": ind.rs_value,
                "relative_strength_spy": ind.rs_value, # Legacy compat
                "rs_trend_s5": ind.rs_trend_s5,
                "rs_trend_s14": ind.rs_trend_s14,
                "rs_condition_14": ind.rs_trend_s14, # Legacy compat
                "rs_trend_s21": ind.rs_trend_s21,
                "rs_condition_21": ind.rs_trend_s21, # Legacy compat
                "rs_trend_s63": ind.rs_trend_s63,
                "rs_condition_63": ind.rs_trend_s63, # Legacy compat
                "rs_trend_s200": ind.rs_trend_s200,
                "rs_macd_line_21": ind.rs_macd_line_21,
                "rs_macd_signal_21": ind.rs_macd_signal_21,
                "rs_macd_hist_21": ind.rs_macd_hist_21,
                "rs_value_e5": ind.rs_value_e5,
                "rs_value_e14": ind.rs_value_e14,
                "rs_ema_14": ind.rs_value_e14, # Legacy compat
                "rs_value_e21": ind.rs_value_e21,
                "rs_ema_21": ind.rs_value_e21, # Legacy compat
                "rs_value_e63": ind.rs_value_e63,
                "rs_ema_63": ind.rs_value_e63, # Legacy compat
                "rs_value_e200": ind.rs_value_e200,
                "rs_momentum_e5": ind.rs_momentum_e5,
                "rs_momentum_e14": ind.rs_momentum_e14,
                "rs_momentum_14": ind.rs_momentum_e14, # Legacy compat
                "rs_momentum_e21": ind.rs_momentum_e21,
                "rs_momentum_21": ind.rs_momentum_e21, # Legacy compat
                "rs_momentum_e63": ind.rs_momentum_e63,
                "rs_momentum_63": ind.rs_momentum_e63, # Legacy compat
                "rs_momentum_e200": ind.rs_momentum_e200,
                "rs_ratio_e5": ind.rs_ratio_e5,
                "rs_ratio_e14": ind.rs_ratio_e14,
                "rs_ratio_14": ind.rs_ratio_e14, # Legacy compat
                "rs_ratio_e21": ind.rs_ratio_e21,
                "rs_ratio_21": ind.rs_ratio_e21, # Legacy compat
                "rs_ratio_e63": ind.rs_ratio_e63,
                "rs_ratio_e200": ind.rs_ratio_e200,
                "rs_ratio_63": ind.rs_ratio_e63, # Legacy compat
                "rs_roc_ema_5": ind.rs_roc_ema_5,
                "rs_roc_ema_14": ind.rs_roc_ema_14,
                "rs_roc_ema_21": ind.rs_roc_ema_21,
                "rs_roc_ema_63": ind.rs_roc_ema_63,
                "rs_roc_ema_200": ind.rs_roc_ema_200,
                "vol_surge_21": ind.vol_surge_21,
                "vol_surge_rel_spy_21": ind.vol_surge_rel_spy_21,
                "rel_vol_vs_spy_21": ind.vol_surge_rel_spy_21, # Legacy compat
                "up_down_vol_ratio_50": ind.up_down_vol_ratio_50,
                "dist_63d_high_pct": ind.dist_63d_high_pct,
                "pct_from_63d_high": ind.dist_63d_high_pct, # Legacy compat
                "dist_52w_high_pct": ind.dist_52w_high_pct,
                "pct_from_52w_high": ind.dist_52w_high_pct, # Legacy compat
                "rs_blue_dot_age": ind.rs_blue_dot_age, "rs_red_dot_age": ind.rs_red_dot_age,
                "vcr": ind.vcr, "is_trend_template": ind.is_trend_template,
                # 構造ピボット (LL-HL)。DATA VIEW の Pivot / Pivot HL 列が読む
                "sp_pivot": ind.sp_pivot, "sp_hl": ind.sp_hl,
                "sp_counter": ind.sp_counter,
                "trend_template_ok": ind.is_trend_template, # Legacy compat
                "vol_accum_days_5": ind.vol_accum_days_5,
                "bb_upper": (ind.sma_21 + 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
                "bb_lower": (ind.sma_21 - 2*ind.atr_14) if ind.sma_21 and ind.atr_14 else None,
            })
            # Include all ranks for RRG minimaps
            r_data = rank_map_nested.get(d_str, {})
            for r_name in rank_indicators:
                point[r_name] = r_data.get(r_name)
            
            # Include legacy rank keys for compatibility
            point["rank_rs_ratio_14"] = r_data.get("rs_ratio_rank_e14")
            point["rank_rs_ratio_21"] = r_data.get("rs_ratio_rank_e21")
            point["rank_rs_ratio_63"] = r_data.get("rs_ratio_rank_e63")
            point["rank_rs_momentum_14"] = r_data.get("rs_momentum_rank_e14")
            point["rank_rs_momentum_21"] = r_data.get("rs_momentum_rank_e21")
            point["rank_rs_momentum_63"] = r_data.get("rs_momentum_rank_e63")
            point["rank_rs_condition_14"] = r_data.get("rs_trend_rank_s14")
            point["rank_rs_condition_21"] = r_data.get("rs_trend_rank_s21")
            point["rank_rs_condition_63"] = r_data.get("rs_trend_rank_s63")
            
            # Include legacy key for RsLineChart
            point["rs_ratio"] = r_data.get("rs_ratio_rank_e21")
        
        chart_data.append(point)

    import json
    from fastapi import Response

    # Custom encoder/cleaner to handle NaN/Inf which are invalid in standard JSON
    def clean_data(obj):
        if isinstance(obj, float):
            if obj != obj or obj == float('inf') or obj == float('-inf'):
                return None
        return obj

    # Apply cleaning to the entire data structure
    cleaned_data = {
        "metadata": {"id": symbol.id, "ticker": symbol.ticker, "name": symbol.name, "category": symbol.category},
        "themes": theme_meta,
        "data": [
            {k: clean_data(v) for k, v in point.items()}
            for point in chart_data
        ]
    }

    return Response(
        content=json.dumps(cleaned_data, allow_nan=False),
        media_type="application/json"
    )

@router.get("/earnings/{symbol_id}", response_model=List[schemas.EarningResponse])
def get_earnings_data(symbol_id: int, db: Session = Depends(get_api_db)):
    """
    T6: Get quarterly earnings fundamental data for a symbol.
    """
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
        
    earnings = db.query(Earning).filter(Earning.symbol_id == symbol_id).order_by(Earning.period_date.desc()).all()
    
    resp = []
    for e in earnings:
        resp.append(schemas.EarningResponse(
            period_date=str(e.period_date),
            eps_basic=e.eps_basic,
            eps_diluted=e.eps_diluted,
            revenue=e.revenue,
            net_income=e.net_income
        ))
    return resp




# ============================================================
# 構造ピボット (LL-HL) — チャート描画用オーバーレイ
# ============================================================




def _load_ohlc_rows_from_parquet(symbol_id: int, db: Session):
    """Parquet マスターから全期間の (date, high, low, close) を読む。失敗したら None。"""
    import pandas as pd
    from pipeline.parquet_cache_manager import (
        get_parquet_master_dir, get_pointer_file_path, get_latest_master_files)

    try:
        parquet_dir = get_parquet_master_dir(_resolve_active_db_path())
        latest_files = get_latest_master_files(get_pointer_file_path(parquet_dir))
        if not latest_files:
            return None
        df = pd.read_parquet(latest_files['prices'],
                             columns=['symbol_id', 'date', 'high', 'low', 'close'],
                             filters=[('symbol_id', '==', symbol_id)])
        if df.empty:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        rows = [(d.strftime('%Y-%m-%d'), h, l, c)
                for d, h, l, c in zip(df['date'], df['high'], df['low'], df['close'])]

        # Parquet より新しい分は SQLite 側にしかないので継ぎ足す（/chart と同じ扱い）
        max_date = df['date'].max().date()
        newer = db.query(DailyPrice).filter(
            DailyPrice.symbol_id == symbol_id,
            DailyPrice.date > max_date,
        ).order_by(DailyPrice.date.asc()).all()
        rows.extend((p.date.strftime('%Y-%m-%d'), p.high, p.low, p.close) for p in newer)
        return rows
    except Exception as e:
        logger.warning("structure_pivot: Parquet 読み込みに失敗したため SQLite へフォールバック: %s", e)
        return None


def _load_ohlc_for_structure(symbol_id: int, db: Session, full_range: bool):
    """構造検出用に (日付文字列, high, low, close) を返す。

    既定は SQLite（直近730日のホットキャッシュ）。`full_range=True` のときだけ
    Parquet マスターから全期間を読む。チャート本体の期間と揃えるための引数。
    """
    import numpy as np

    rows = _load_ohlc_rows_from_parquet(symbol_id, db) if full_range else None
    if rows is None:
        prices = db.query(DailyPrice).filter(
            DailyPrice.symbol_id == symbol_id).order_by(DailyPrice.date.asc()).all()
        rows = [(p.date.strftime('%Y-%m-%d'), p.high, p.low, p.close) for p in prices]

    rows = [r for r in rows if r[1] is not None and r[2] is not None and r[3] is not None]
    dates = [r[0] for r in rows]
    high = np.array([float(r[1]) for r in rows], dtype=np.float64)
    low = np.array([float(r[2]) for r in rows], dtype=np.float64)
    close = np.array([float(r[3]) for r in rows], dtype=np.float64)
    return dates, high, low, close


#: fib 比率の既定値（改良版 Advanced Structure Pivot と同じ）
#: 1st = 早いエントリー候補 / TP1・TP2 = 利確目標。いずれも HL とピボットから導かれる
DEFAULT_FIB_1ST = 0.618
DEFAULT_FIB_TP1 = 1.764
DEFAULT_FIB_TP2 = 2.618


def _structure_to_dict(s, dates: List[str], fib_1st: float = DEFAULT_FIB_1ST,
                       tp1: float = DEFAULT_FIB_TP1, tp2: float = DEFAULT_FIB_TP2) -> Dict[str, Any]:
    """インデックスを日付文字列へ置き換えてフロントへ返す形にする。

    fib 水準は HL を起点、`pivot - hl` をレンジとした拡張。
    構造そのものは変わらないので、比率はレスポンス生成時に掛けるだけでよい。
    """
    fib_range = s.pivot_price - s.hl_price
    return {
        "length": s.length,
        "ll_date": dates[s.ll_index],
        "ll_price": s.ll_price,
        "hl_date": dates[s.hl_index],
        "hl_price": s.hl_price,
        "pivot_date": dates[s.pivot_index],
        "pivot_price": s.pivot_price,
        # 改良版 Advanced Structure Pivot と同じ呼び方: 本ピボット = 2nd
        "fib_1st_price": s.hl_price + fib_range * fib_1st,
        "tp1_price": s.hl_price + fib_range * tp1,
        "tp2_price": s.hl_price + fib_range * tp2,
        "confirmed_date": dates[s.confirmed_index],
        "end_date": dates[s.end_index],
        "invalidated": s.invalidated,
        "is_current": s.is_current,
        "broken_at_confirmation": s.broken_at_confirmation,
    }


#: レスポンスに載せるカウンター線の履歴上限。構造ピボットの履歴（フロント側 30本）と
#: 揃える。`sp_counter` の非NULL率は 28.6% と高く、全部返すとペイロードが膨らむ
COUNTER_HISTORY_LIMIT = 30


def _counter_to_dict(c, dates: List[str]) -> Dict[str, Any]:
    """カウンタートレンド線を、そのまま線分として描ける形にする。

    `start_value` / `end_value` を**サーバ側で計算して返す**のが要点。
    日付↔インデックスの対応はここにしか無いため、フロントで傾きから座標を
    復元させると「営業日でない日を跨ぐとずれる」種類のバグを呼び込む。
    """
    return {
        "a1_date": dates[c.a1_index],
        "a1_price": c.a1_price,
        "a2_date": dates[c.a2_index],
        "a2_price": c.a2_price,
        "start_date": dates[c.start_index],
        "end_date": dates[c.end_index],
        # 線分の両端の値。a1 から end まで引けば線全体が描ける
        "a1_value": c.a1_price,
        "start_value": c.value_at(c.start_index),
        "end_value": c.value_at(c.end_index),
        "slope": c.slope,
        "is_current": c.is_current,
    }


def build_structure_pivot_response(
    symbol_id: int,
    db: Session,
    full_range: bool = False,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
    fib_1st: float = DEFAULT_FIB_1ST,
    fib_tp1: float = DEFAULT_FIB_TP1,
    fib_tp2: float = DEFAULT_FIB_TP2,
) -> Dict[str, Any]:
    """LL-HL 構造ピボットをオンザフライで計算して返す（チャート描画用）。

    T3 インジケータには持たない。1銘柄あたり最大2,000本程度で計算は 1ms 未満のため、
    事前計算する理由が無い（採否の実測根拠は
    `doc/in_progress/structure_pivot_chart_plan.md` §1）。

    `structures` は過去に一度でも Tightest 勝者になった構造の履歴、
    `current` は最終バー時点で生きている構造（無ければ null）。
    データ不足のときはエラーにせず空で返す — チャートの一部なので、
    構造が出ないことで画面全体を落とさない。
    """
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id, Symbol.active == 1).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")

    dates, high, low, close = _load_ohlc_for_structure(symbol_id, db, full_range)
    structures = find_structures(high, low, close, min_len=min_len, max_len=max_len)
    items = [_structure_to_dict(s, dates, fib_1st, fib_tp1, fib_tp2) for s in structures]

    # カウンタートレンド線。構造が成立していない期間にだけ引かれる（sp_pivot と排他）。
    # 値は T3 の sp_counter と一致する（tmp/verify_counter_parity.py で 60銘柄・
    # 45,708バー・6,733線分の不一致 0 を確認済み）
    counters = find_counter_trends(high, low, close, min_len=min_len, max_len=max_len)
    counter_items = [_counter_to_dict(c, dates) for c in counters[-COUNTER_HISTORY_LIMIT:]]

    return {
        "metadata": {
            "ticker": symbol.ticker,
            "min_len": min_len,
            "max_len": max_len,
            "fib_1st": fib_1st,
            "fib_tp1": fib_tp1,
            "fib_tp2": fib_tp2,
            "bars": len(dates),
            "counter_history_limit": COUNTER_HISTORY_LIMIT,
        },
        "structures": items,
        "current": next((it for it in items if it["is_current"]), None),
        "counters": counter_items,
        "current_counter": next((it for it in counter_items if it["is_current"]), None),
    }


@router.get("/chart/{symbol_id}/structure_pivot")
def get_structure_pivot_data(
    symbol_id: int,
    db: Session = Depends(get_api_db),
    full_range: bool = Query(False),
    min_len: int = Query(DEFAULT_MIN_LEN, ge=1, le=50),
    max_len: int = Query(DEFAULT_MAX_LEN, ge=1, le=50),
    fib_1st: float = Query(DEFAULT_FIB_1ST, gt=0.0, le=1.0),
    fib_tp1: float = Query(DEFAULT_FIB_TP1, gt=1.0, le=5.0),
    fib_tp2: float = Query(DEFAULT_FIB_TP2, gt=1.0, le=5.0),
):
    """`build_structure_pivot_response` の薄いラッパ。

    ロジックを持たせない。FastAPI の `Query(...)` 既定値は関数を直接呼んだときに
    Query オブジェクトのまま渡り（かつ truthy）、条件分岐を静かに壊すため、
    実処理は素の引数を取る純関数側に置く。
    """
    return build_structure_pivot_response(symbol_id, db, full_range, min_len, max_len,
                                         fib_1st, fib_tp1, fib_tp2)


# ============================================================
# Direction via Zone Break — チャート描画用オーバーレイ
# ============================================================

def _level_to_dict(level, dates: List[str]) -> Dict[str, Any]:
    """`ZoneBreakLevel`（インデックス基準）を日付基準の dict に変換する。"""
    return {
        "kind": level.kind,
        "price": level.price,
        "start_date": dates[level.start_index],
        "end_date": dates[level.end_index],
        "is_current": level.is_current,
    }


def _fvg_to_dict(box, dates: List[str]) -> Dict[str, Any]:
    """`ZoneBreakFvg`（インデックス基準）を日付基準の dict に変換する。"""
    return {
        "kind": box.kind,
        "left_date": dates[box.left_index],
        "right_date": dates[box.right_index],
        "top": box.top,
        "bottom": box.bottom,
        "invalidated": box.invalidated,
        "is_current": box.is_current,
    }


def build_zone_break_response(
    symbol_id: int,
    db: Session,
    full_range: bool = False,
) -> Dict[str, Any]:
    """Direction via Zone Break をオンザフライで計算して返す（チャート描画用）。

    T3 インジケータには SSL/BSL/is_weak の4カラムのみ持つ（`is_zone_break_bull` /
    `zb_ssl` / `zb_bsl` / `is_zone_break_weak`）。SSL/BSL ラインの区間・FVG ボックスは
    チャート描画専用でありオンザフライ計算とする（`build_structure_pivot_response` と
    同じ設計判断。doc/completed/zone_break_plan.md §2.2 参照）。

    データ不足のときはエラーにせず空で返す — チャートの一部なので、
    区間が出ないことで画面全体を落とさない。
    """
    symbol = db.query(Symbol).filter(Symbol.id == symbol_id, Symbol.active == 1).first()
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")

    dates, high, low, close = _load_ohlc_for_structure(symbol_id, db, full_range)
    if len(dates) == 0:
        return {
            "metadata": {"ticker": symbol.ticker, "bars": 0},
            "ssl_levels": [], "bsl_levels": [], "fvg_boxes": [],
            "current_direction": None,
        }

    state = find_zone_break_zones(high, low, close)
    is_bull, _ssl, _bsl, _is_weak = zone_break_series(high, low, close)

    return {
        "metadata": {"ticker": symbol.ticker, "bars": len(dates)},
        "ssl_levels": [_level_to_dict(lv, dates) for lv in state.ssl_levels],
        "bsl_levels": [_level_to_dict(lv, dates) for lv in state.bsl_levels],
        "fvg_boxes": [_fvg_to_dict(b, dates) for b in state.fvg_boxes],
        "current_direction": "bull" if bool(is_bull[-1]) else "bear",
    }


@router.get("/chart/{symbol_id}/zone_break")
def get_zone_break_data(
    symbol_id: int,
    db: Session = Depends(get_api_db),
    full_range: bool = Query(False),
):
    """`build_zone_break_response` の薄いラッパ（`get_structure_pivot_data` と同じ構成）。"""
    return build_zone_break_response(symbol_id, db, full_range)
