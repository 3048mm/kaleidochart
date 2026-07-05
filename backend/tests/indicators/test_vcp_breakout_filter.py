"""filter_vcp_breakout（VCP ブレイクアウトの特殊フィルタ）の純関数テスト。

E ファミリー再設計（doc/in_progress/e3_vcp_breakout_plan.md）:
「昨日まで収縮した高値圏の土台にあった銘柄が、今日ピボットを出来高膨張で
終値上抜けた日」だけを通過させるイベントフィルタ。high_window で
ピボットの高値ウィンドウ（63日 or 252日=52週）を切り替える。
"""
import pandas as pd
import pytest

from indicators.screener_filters import filter_vcp_breakout, SPECIAL_FILTER_KEYS


def _rows():
    """1行=1シナリオ。row1 が PASS の基準、他は1条件ずつ崩して FAIL させる。"""
    base = dict(
        is_trend_template=1,
        vol_surge_21=2.0,            # >= breakout_vol_mult(1.5) 出来高膨張
        change_1d_pct=5.0,           # >= breakout_change(4.0) ブレイクの大陽線
        prev_vcr=0.6,                # <= vcr_contraction_max(0.8) 前日収縮
        prev_dist_52w_high_pct=-8.0,  # >= -base_high_tol(15) 高値圏の土台
        dist_63d_high_pct=-1.0,       # >= -near_high_tol(4) 63日高値近傍
        dist_52w_high_pct=-1.0,       # 52週高値近傍
    )
    rows = []
    # id=1: 完全なブレイク → PASS
    rows.append({**base, "symbol_id": 1})
    # id=2: 高値から遠い（近傍ゲート未達） → FAIL
    rows.append({**base, "symbol_id": 2, "dist_63d_high_pct": -10.0, "dist_52w_high_pct": -10.0})
    # id=3: 前日が収縮していない → FAIL
    rows.append({**base, "symbol_id": 3, "prev_vcr": 1.2})
    # id=4: 出来高膨張なし → FAIL
    rows.append({**base, "symbol_id": 4, "vol_surge_21": 1.0})
    # id=5: トレンドテンプレート非適合 → FAIL
    rows.append({**base, "symbol_id": 5, "is_trend_template": 0})
    # id=6: 大陽線でない（ブレイクバーなし） → FAIL
    rows.append({**base, "symbol_id": 6, "change_1d_pct": 1.0})
    # id=7: 土台が深すぎる（高値から遠い） → FAIL
    rows.append({**base, "symbol_id": 7, "prev_dist_52w_high_pct": -30.0})
    return pd.DataFrame(rows)


def test_registered_in_special_filter_keys():
    assert "is_vcp_breakout" in SPECIAL_FILTER_KEYS


def test_breakout_63_passes_only_the_true_breakout():
    df = _rows()
    mask = filter_vcp_breakout(df, high_window=63)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {1}, f"63日ブレイクで通過すべきは id=1 のみ。実際: {passed}"


def test_breakout_52w_uses_252_window_columns():
    """high_window=252 は dist_52w_high_pct / prev_dist_252_high_pct を参照すること。"""
    df = _rows()
    # 52週版: id=1 は今日 dist_52w_high_pct=-0.2 到達・昨日 prev_dist_252_high_pct=-3.0 下 → PASS
    mask = filter_vcp_breakout(df, high_window=252)
    passed = set(df.loc[mask, "symbol_id"])
    assert 1 in passed
    # id=2 は 52週でも今日 -2.0 で未達 → 除外
    assert 2 not in passed


def test_missing_prev_columns_denies_all():
    """prev カラムが無い場合、イベントは検出不能なので全 False（deny-by-default）。

    他の prev 依存フィルタ（RRG 等）は「全通過（no-op）」フォールバックだが、
    ブレイクは前日比較が本質なので、前日情報が無いのに全通過させると
    「全銘柄がブレイク」という危険な過剰包含になる。イベントフィルタは deny 側に倒す。
    """
    df = _rows().drop(columns=["prev_vcr", "prev_dist_52w_high_pct"])
    mask = filter_vcp_breakout(df, high_window=63)
    assert not mask.any(), "prev 欠損時は全 False であるべき"


def test_params_are_tunable():
    """閾値パラメータが効くこと（出来高倍率を上げると id=1 も落ちる）。"""
    df = _rows()
    mask = filter_vcp_breakout(df, high_window=63, breakout_vol_mult=3.0)
    assert not mask.any(), "vol 倍率 3.0 では vol_surge_21=2.0 の id=1 も不通過"


def test_api_dispatch_matches_pure_function():
    """API 側 evaluate_special_filters 経由でも純関数と同じ通過集合になること（D-2 同値性）。"""
    from api.screener_cross_section import evaluate_special_filters
    import pandas as pd

    df = _rows()
    df["category"] = "個別"
    df_tc = pd.DataFrame(columns=["theme_id", "symbol_id"])

    ids_63 = evaluate_special_filters(df, df_tc, {"is_vcp_breakout": True},
                                      params={"breakout_high_window": 63})
    assert ids_63 == {1}

    # params 省略時はデフォルト（high_window=63）で動くこと（サイレント素通しでない）
    ids_default = evaluate_special_filters(df, df_tc, {"is_vcp_breakout": True})
    assert ids_default == {1}
