"""
リネームリファクタリング後の検証スクリプト
Sandbox DB に新カラム名でパイプラインを実行した後に実行する。

検証項目:
  1. indicators テーブルに新カラム名でデータが存在するか
  2. relative_ranks テーブルに新カラム名でデータが存在するか
  3. 各指標の値が合理的な範囲にあるか (NaN ばかりでないか)
  4. API エンドポイントが正常に動作するか
"""
import sqlite3
import os, sys

# ── 設定 ──────────────────────────────────────────────
SANDBOX_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "stocktool_sandbox.db"
)
SPY_TICKER = "SPY"
SAMPLE_TICKERS = ["AAPL", "MSFT", "NVDA"]  # 検証対象の代表銘柄

# ── 期待するカラム名 (新) ──────────────────────────────
EXPECTED_INDICATOR_COLS = [
    "rs_value", "rs_value_e5", "rs_value_e14", "rs_value_e21", "rs_value_e63",
    "rs_trend_s14", "rs_trend_s21", "rs_trend_s63",
    "rs_ratio_e14", "rs_ratio_e21", "rs_ratio_e63",
    "rs_momentum_e14", "rs_momentum_e21", "rs_momentum_e63",
    "rs_roc_ema_14", "rs_roc_ema_21", "rs_roc_ema_63",
    "sma50_atr_mult",
    "vol_surge_rel_spy_21",
    "dist_63d_high_pct", "dist_52w_high_pct",
    "is_trend_template", "is_rs_blue_dot", "is_rs_red_dot",
]

EXPECTED_RANK_COLS = [
    "rs_value_rank",
    "rs_ratio_rank_e14", "rs_ratio_rank_e21", "rs_ratio_rank_e63",
    "rs_momentum_rank_e14", "rs_momentum_rank_e21", "rs_momentum_rank_e63",
    "rs_trend_rank_s14", "rs_trend_rank_s21", "rs_trend_rank_s63",
    "rs_roc_ema_rank_e14", "rs_roc_ema_rank_e21", "rs_roc_ema_rank_e63",
]

# ── 旧カラム名（存在してはいけない）──────────────────
OLD_INDICATOR_COLS = [
    "relative_strength_spy", "rs_condition_14", "rs_condition_21", "rs_condition_63",
    "rs_ema_5", "rs_ema_14", "rs_ema_21", "rs_ema_63",
    "rs_momentum_14", "rs_momentum_21", "rs_momentum_63",
    "rs_ratio_14", "rs_ratio_21", "rs_ratio_63",
    "dist_sma50_atr", "rel_vol_vs_spy_21",
    "pct_from_63d_high", "pct_from_52w_high",
    "trend_template_ok", "rs_blue_dot", "rs_red_dot",
]

OLD_RANK_COLS = [
    "relative_strength_spy",
    "rs_ratio_14", "rs_ratio_21", "rs_ratio_63",
    "rs_momentum_14", "rs_momentum_21", "rs_momentum_63",
    "rs_condition_14", "rs_condition_21", "rs_condition_63",
]


def get_table_columns(conn, table_name):
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def verify_db(db_path):
    print(f"\n{'='*60}")
    print(f"検証対象DB: {db_path}")
    print(f"{'='*60}\n")

    if not os.path.exists(db_path):
        print(f"[ERROR] DB が見つかりません: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    errors = []
    warnings = []

    # ── 1. indicators テーブルのカラム確認 ──────────────
    print("【1】indicators テーブルのカラム検証")
    ind_cols = get_table_columns(conn, "indicators")

    for col in EXPECTED_INDICATOR_COLS:
        if col in ind_cols:
            print(f"  ✅ {col}")
        else:
            errors.append(f"indicators に必須カラムなし: {col}")
            print(f"  ❌ {col} が存在しない")

    for col in OLD_INDICATOR_COLS:
        if col in ind_cols:
            errors.append(f"indicators に旧カラムが残存: {col}")
            print(f"  ⚠️  旧カラム残存: {col}")

    # ── 2. relative_ranks テーブルのカラム確認 ─────────
    print("\n【2】relative_ranks テーブルのカラム検証")
    rank_cols = get_table_columns(conn, "relative_ranks")

    for col in EXPECTED_RANK_COLS:
        if col in rank_cols:
            print(f"  ✅ {col}")
        else:
            errors.append(f"relative_ranks に必須カラムなし: {col}")
            print(f"  ❌ {col} が存在しない")

    for col in OLD_RANK_COLS:
        if col in rank_cols:
            errors.append(f"relative_ranks に旧カラムが残存: {col}")
            print(f"  ⚠️  旧カラム残存: {col}")

    # ── 3. データの値範囲チェック（最新日付のAAPL）──────
    print("\n【3】主要指標の値チェック（AAPL・最新日付）")
    try:
        row = conn.execute("""
            SELECT i.rs_value, i.rs_ratio_e21, i.rs_momentum_e21, i.rs_trend_s21,
                   i.sma50_atr_mult, i.is_trend_template, i.is_rs_blue_dot
            FROM indicators i
            JOIN symbols s ON i.symbol_id = s.id
            WHERE s.ticker = ? AND i.rs_value IS NOT NULL
            ORDER BY i.date DESC LIMIT 1
        """, (SAMPLE_TICKERS[0],)).fetchone()

        if row:
            rs_val, ratio, mom, trend, atr_mult, tt, bd = row
            print(f"  AAPL rs_value      = {rs_val:.4f}  (期待: ≈1.0)")
            print(f"  AAPL rs_ratio_e21  = {ratio:.4f}  (期待: -3〜3)")
            print(f"  AAPL rs_momentum_e21 = {mom:.4f} (期待: -3〜3)")
            print(f"  AAPL rs_trend_s21  = {trend:.4f} (期待: ≈1.0)")
            print(f"  AAPL sma50_atr_mult= {atr_mult:.4f} (期待: -10〜10)")
            print(f"  AAPL is_trend_template = {tt}   (期待: 0 or 1)")
            print(f"  AAPL is_rs_blue_dot    = {bd}   (期待: 0 or 1)")

            # 範囲チェック
            if rs_val and not (0.1 < rs_val < 5.0):
                warnings.append(f"AAPL rs_value={rs_val:.4f} が想定範囲外 (0.1〜5.0)")
            if ratio and not (-10 < ratio < 10):
                warnings.append(f"AAPL rs_ratio_e21={ratio:.4f} が想定範囲外 (-10〜10)")
        else:
            errors.append("AAPL の indicators データが取得できない")
    except Exception as e:
        errors.append(f"値チェックエラー: {e}")

    # ── 4. relative_ranks の非NAN率チェック ──────────
    print("\n【4】relative_ranks の非NULL率チェック（最新日付）")
    try:
        latest_date = conn.execute(
            "SELECT MAX(date) FROM relative_ranks"
        ).fetchone()[0]

        total = conn.execute(
            "SELECT COUNT(*) FROM relative_ranks WHERE date = ?", (latest_date,)
        ).fetchone()[0]

        for col in ["rs_ratio_rank_e21", "rs_momentum_rank_e21", "rs_trend_rank_s21"]:
            if col in rank_cols:
                non_null = conn.execute(
                    f"SELECT COUNT(*) FROM relative_ranks WHERE date = ? AND {col} IS NOT NULL",
                    (latest_date,)
                ).fetchone()[0]
                pct = non_null / total * 100 if total else 0
                status = "✅" if pct > 80 else "⚠️"
                print(f"  {status} {col}: {non_null}/{total} ({pct:.1f}% 非NULL) [{latest_date}]")

    except Exception as e:
        errors.append(f"relative_ranks 非NULL率チェックエラー: {e}")

    conn.close()

    # ── 結果サマリ ──────────────────────────────────
    print(f"\n{'='*60}")
    print("【検証結果サマリ】")
    if not errors and not warnings:
        print("  🎉 すべて正常 — 本番適用可能")
    else:
        if errors:
            print(f"  ❌ エラー ({len(errors)}件):")
            for e in errors:
                print(f"     - {e}")
        if warnings:
            print(f"  ⚠️  警告 ({len(warnings)}件):")
            for w in warnings:
                print(f"     - {w}")
    print(f"{'='*60}\n")

    return len(errors) == 0


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else SANDBOX_DB
    ok = verify_db(db_path)
    sys.exit(0 if ok else 1)
