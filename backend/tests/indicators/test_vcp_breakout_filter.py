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


def _pivot_rows():
    """ピボットクロス／ドライアップ用シナリオ（e4 計画書）。

    クロス条件: (1 + change_1d/100) × (1 + prev_dist_{W}/100) >= 1 - pivot_tol/100
    （今日の終値が「昨日までの」N日最大高値を上抜けたことと等価）
    """
    base = dict(
        is_trend_template=1,
        vol_surge_21=2.0,
        change_1d_pct=5.0,
        prev_vcr=0.6,
        prev_dist_52w_high_pct=-8.0,
        dist_63d_high_pct=-1.0,
        dist_52w_high_pct=-1.0,
        prev_dist_63d_high_pct=-3.0,   # 1.05×0.97=1.0185 → 63日クロス成立
        prev_vol_surge_21=0.7,         # 前日の出来高枯れ
    )
    rows = []
    # id=1: クロス成立＋枯れ → PASS（63日）。52週は prev_dist_52w=-8 で 1.05×0.92=0.966 → クロス不成立
    rows.append({**base, "symbol_id": 1})
    # id=8: 土台内リバウンド陽線（昨日の63日高値に届かない）: 1.05×0.91=0.9555 → クロス不成立
    rows.append({**base, "symbol_id": 8, "prev_dist_63d_high_pct": -9.0})
    # id=9: 前日の出来高が枯れていない → ドライアップ有効時のみ FAIL
    rows.append({**base, "symbol_id": 9, "prev_vol_surge_21": 1.8})
    # id=10: 惜しいクロス: 1.05×0.945=0.99225 → tol=0 で FAIL / tol=1.0(閾値0.99) で PASS
    rows.append({**base, "symbol_id": 10, "prev_dist_63d_high_pct": -5.5})
    # id=11: 52週高値もクロス: 1.05×0.98=1.029（63日も -2.0 でクロス）
    rows.append({**base, "symbol_id": 11,
                 "prev_dist_63d_high_pct": -2.0, "prev_dist_52w_high_pct": -2.0})
    return pd.DataFrame(rows)


def test_pivot_cross_rejects_rebound_inside_base():
    """クロス有効時、土台内リバウンド陽線（id=8）は不通過。真のクロスのみ通過。"""
    df = _pivot_rows()
    mask = filter_vcp_breakout(df, high_window=63, pivot_tol=0.0, base_vol_dry_max=1.0)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {1, 11}, f"クロス+枯れで通過すべきは {{1, 11}}。実際: {passed}"


def test_pivot_tol_allows_slightly_below_pivot():
    """pivot_tol=1.0 でピボットの1%下までの終値を許容（id=10 が追加で通過）。"""
    df = _pivot_rows()
    mask = filter_vcp_breakout(df, high_window=63, pivot_tol=1.0, base_vol_dry_max=1.0)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {1, 10, 11}, f"実際: {passed}"


def test_dry_up_gate_only():
    """base_vol_dry_max のみ有効時、前日出来高が枯れていない id=9 だけ落ちる。"""
    df = _pivot_rows()
    mask = filter_vcp_breakout(df, high_window=63, base_vol_dry_max=1.0)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {1, 8, 10, 11}, f"実際: {passed}"


def test_new_params_none_keeps_legacy_behavior():
    """新パラメータ未指定（None）なら従来挙動（回帰なし）: 全行が従来条件を満たすので全通過。"""
    df = _pivot_rows()
    mask = filter_vcp_breakout(df, high_window=63)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {1, 8, 9, 10, 11}, f"実際: {passed}"


def test_pivot_cross_52w_uses_prev_dist_52w():
    """high_window=252 のクロスは prev_dist_52w_high_pct を参照（id=1 は 52週では不成立）。"""
    df = _pivot_rows()
    mask = filter_vcp_breakout(df, high_window=252, pivot_tol=0.0)
    passed = set(df.loc[mask, "symbol_id"])
    assert passed == {11}, f"実際: {passed}"


def test_deny_when_pivot_prev_col_missing():
    """クロス有効なのに prev_dist_63d_high_pct が無い → 全 False（deny-by-default）。"""
    df = _pivot_rows().drop(columns=["prev_dist_63d_high_pct"])
    mask = filter_vcp_breakout(df, high_window=63, pivot_tol=0.0)
    assert not mask.any()


def test_deny_when_prev_vol_surge_missing():
    """ドライアップ有効なのに prev_vol_surge_21 が無い → 全 False（deny-by-default）。"""
    df = _pivot_rows().drop(columns=["prev_vol_surge_21"])
    mask = filter_vcp_breakout(df, high_window=63, base_vol_dry_max=1.0)
    assert not mask.any()


def test_api_dispatch_matches_pure_function_with_new_params():
    """新パラメータも API 側 evaluate_special_filters 経由で純関数と同値（D-2 同値性）。"""
    from api.screener_cross_section import evaluate_special_filters

    df = _pivot_rows()
    df["category"] = "個別"
    df_tc = pd.DataFrame(columns=["theme_id", "symbol_id"])

    ids = evaluate_special_filters(
        df, df_tc, {"is_vcp_breakout": True},
        params={"breakout_high_window": 63, "pivot_tol": 0.0, "base_vol_dry_max": 1.0})
    assert ids == {1, 11}


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
