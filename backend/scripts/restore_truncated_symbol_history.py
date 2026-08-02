"""上流（Yahoo）が系列を切り落とした銘柄の履歴を、旧 Parquet 世代から復元する。

## 背景（2026-08-02）

Yahoo が特定銘柄の内部レコードを作り直し（re-key）、`firstTradeDate` が最近の日付に
打ち直された結果、chart API が返す時系列がその日以降に切り詰められた。集計レイヤーには
履歴が残っている（TopBuild は「初回売買日 2026-06-30」なのに「52週高値 559.47」を返す）
ため、上場廃止でも改称でもない。**取り直しても直らない。**

こちらの履歴まで消えたのは、完全再構築で Parquet をクリーンしてから取り直したため。
上流が切断済みの状態で取り直すと、手元にあった正しいデータが「取得できなかった」で
上書きされる。旧 Parquet 世代（MVCC）に残っている履歴を継ぎ直すのが本スクリプト。

## やること

  1. 旧世代 Parquet から対象銘柄の履歴を取り出す
  2. **ticker で突合して symbol_id を振り直す**（世代間で id は一致しない）
  3. 現行 prices にマージし、T3 を再計算して新世代を書き出す

## やらないこと

- **T4 (relative_ranks) / T5 (market_signals) は再計算しない。** 横断的な
  パーセンタイル順位なので、17銘柄の履歴が増えると過去日の順位が全銘柄で変わる。
  完了後に `update_pipeline.py --rebuild-from T4` を別途実行すること。
- **旧世代の prune はしない**（`agent_execution_rules.md` §10.1: health check 合格まで
  MVCC 旧世代がバックアップを兼ねる）。

Usage:
    $env:PYTHONPATH="backend"
    .\\venv\\Scripts\\python.exe backend\\scripts\\restore_truncated_symbol_history.py --dry-run
    .\\venv\\Scripts\\python.exe backend\\scripts\\restore_truncated_symbol_history.py --apply
    # 対象を明示する場合
    ... --tickers BLD,LC,SCVL --apply
"""

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd

_this_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.dirname(_this_dir)
_project_root = os.path.dirname(_backend_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import tomli  # noqa: E402
from indicators.calculate import calculate_indicators  # noqa: E402
from pipeline.parquet_cache_manager import (  # noqa: E402
    get_latest_master_files,
    get_parquet_master_dir,
    get_pointer_file_path,
    update_pointer_with_retry,
)

DEFAULT_BACKUP_DIR = os.path.join(_project_root, "data", "_bk", "parquet_master")

# 復元候補の判定しきい値。
#   現行の保有行数がこれ以下 かつ 旧世代がその MIN_GAIN 倍以上を持つ銘柄を候補とする。
#   「上流が切った」以外の理由（新規上場・新規追加）で行数が少ない銘柄を巻き込まないため、
#   単なる行数の少なさではなく「旧世代との差」を条件にする。
CURRENT_ROWS_MAX = 50
MIN_GAIN = 5

# 接合部の価格比がこの範囲を外れたら分割・併合の疑いとして報告する
# （weekly_maintenance.py のアノマリー検出と同じしきい値）。
SEAM_RATIO_LO, SEAM_RATIO_HI = 0.61, 1.79


def _norm_date(s: pd.Series) -> pd.Series:
    """date 列を 'YYYY-MM-DD' 文字列に正規化する。

    世代によって str / datetime.date / Timestamp が混在するため、
    突合とマージの前に必ず揃える（型が違うと重複排除がすり抜ける）。
    """
    return pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")


def load_generation(paths: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    sym = pd.read_parquet(paths["symbols"])
    px = pd.read_parquet(paths["prices"])
    px["date"] = _norm_date(px["date"])
    return sym, px


def find_candidates(cur_sym, cur_px, bk_sym, bk_px, explicit: list[str] | None):
    """復元対象と、その復元行数を決める。"""
    cur_counts = cur_px.groupby("symbol_id").size()
    bk_counts = bk_px.groupby("symbol_id").size()

    cur_id = dict(zip(cur_sym["ticker"], cur_sym["id"]))
    bk_id = dict(zip(bk_sym["ticker"], bk_sym["id"]))

    tickers = explicit if explicit else sorted(set(cur_id) & set(bk_id))
    out = []
    for t in tickers:
        nid, oid = cur_id.get(t), bk_id.get(t)
        if nid is None or oid is None:
            continue
        n_cur = int(cur_counts.get(nid, 0))
        n_bk = int(bk_counts.get(oid, 0))
        if not explicit:
            if n_cur > CURRENT_ROWS_MAX or n_bk < max(n_cur * MIN_GAIN, 1):
                continue
        out.append({"ticker": t, "new_id": nid, "old_id": oid,
                    "cur_rows": n_cur, "bk_rows": n_bk})
    return out


def build_restore_rows(cand, cur_px, bk_px):
    """各銘柄について「現行に無い過去分」だけを旧世代から取り出す。

    現行側の行は残す（上流の最新値が正）。旧世代で埋めるのは現行の最古日より前だけ。
    """
    frames, report = [], []
    for c in cand:
        cur_rows = cur_px[cur_px["symbol_id"] == c["new_id"]]
        bk_rows = bk_px[bk_px["symbol_id"] == c["old_id"]].copy()
        if bk_rows.empty:
            continue

        if cur_rows.empty:
            add = bk_rows
            seam_ratio, cur_first = None, None
        else:
            cur_first = cur_rows["date"].min()
            add = bk_rows[bk_rows["date"] < cur_first]
            seam_ratio = None
            if not add.empty:
                last_old = add.sort_values("date").iloc[-1]["close"]
                first_new = cur_rows.sort_values("date").iloc[0]["close"]
                if last_old:
                    seam_ratio = first_new / last_old

        if add.empty:
            continue
        add = add.copy()
        add["symbol_id"] = c["new_id"]
        frames.append(add)
        report.append({**c, "restored": len(add),
                       "from": add["date"].min(), "to": add["date"].max(),
                       "seam_gap_to": cur_first, "seam_ratio": seam_ratio})
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), report


def recompute_indicators(sym_ids, merged_px, cur_ind, spy_id):
    """復元した銘柄の T3 を全期間再計算する。

    旧世代の T3 をそのまま流用してはいけない。指標列は 50 → 63 に増えており
    （`avg_dollar_volume_21` / `rs_macd_*` 等）、流用すると新しい列が欠損したまま
    バックテストとスクリーナーに入る。
    """
    spy_df = merged_px[merged_px["symbol_id"] == spy_id][
        ["date", "close", "volume"]
    ].sort_values("date").reset_index(drop=True)

    ind_cols = [c for c in cur_ind.columns if c not in ("id", "symbol_id", "date")]
    out = []
    for sid in sym_ids:
        px = merged_px[merged_px["symbol_id"] == sid][
            ["date", "open", "high", "low", "close", "volume"]
        ].sort_values("date").reset_index(drop=True)
        if px.empty:
            continue
        df = calculate_indicators(px, spy_df)
        if df.empty:
            continue
        df = df.reindex(columns=["date"] + ind_cols)
        df.insert(0, "symbol_id", sid)
        out.append(df)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def run(dry_run: bool, backup_dir: str, explicit: list[str] | None):
    with open(os.path.join(_project_root, "config.toml"), "rb") as f:
        config = tomli.load(f)
    db_path = config["system"]["db_path"]
    parquet_dir = get_parquet_master_dir(db_path)
    pointer_file = get_pointer_file_path(parquet_dir)

    cur_paths = get_latest_master_files(pointer_file)
    if not cur_paths:
        print("[ERROR] latest_master.json を解決できません。"); sys.exit(1)

    bk_pointer = os.path.join(backup_dir, "latest_master.json")
    if not os.path.exists(bk_pointer):
        print(f"[ERROR] バックアップ世代が見つかりません: {bk_pointer}"); sys.exit(1)
    bk_paths = json.load(open(bk_pointer, encoding="utf-8"))
    # バックアップの pointer は当時の絶対パスなので、実ファイル名だけ使う
    bk_paths = {k: os.path.join(backup_dir, os.path.basename(v)) for k, v in bk_paths.items()}

    print("=" * 74)
    print(f"上流切断銘柄の履歴復元  (dry-run={dry_run})")
    print("=" * 74)
    print(f"現行世代 : {os.path.basename(cur_paths['prices'])}")
    print(f"復元元   : {os.path.basename(bk_paths['prices'])}")

    print("\n[1] 世代の読み込み...")
    cur_sym, cur_px = load_generation(cur_paths)
    bk_sym, bk_px = load_generation(bk_paths)
    print(f"    現行 prices {len(cur_px):,}行 / 旧世代 prices {len(bk_px):,}行")

    print("\n[2] 復元対象の判定...")
    cand = find_candidates(cur_sym, cur_px, bk_sym, bk_px, explicit)
    if not cand:
        print("    対象なし。終了します。"); return
    add_px, report = build_restore_rows(cand, cur_px, bk_px)
    if add_px.empty:
        print("    復元すべき過去分がありません。終了します。"); return

    print(f"\n{'ticker':<7} {'現行':>5} {'復元':>7} {'復元期間':<26} {'接合比':>7}  判定")
    print("-" * 74)
    suspicious = []
    for r in sorted(report, key=lambda x: -x["restored"]):
        ratio = r["seam_ratio"]
        if ratio is None:
            judge, rs = "現行行なし", "    -"
        elif ratio <= SEAM_RATIO_LO or ratio >= SEAM_RATIO_HI:
            judge, rs = "★分割疑い", f"{ratio:7.3f}"
            suspicious.append(r["ticker"])
        else:
            judge, rs = "連続", f"{ratio:7.3f}"
        print(f"{r['ticker']:<7} {r['cur_rows']:>5} {r['restored']:>7} "
              f"{r['from']}〜{r['to']:<12} {rs}  {judge}")

    print(f"\n合計 {len(report)} 銘柄 / {len(add_px):,} 行を復元")
    if suspicious:
        print(f"接合部に分割疑い: {','.join(suspicious)}")
        print("  → 価格比だけでは実際の急変と区別できない。売買代金の連続性で確認すること")

    if dry_run:
        print("\n[DRY-RUN] 何も書き込んでいません。")
        return

    print("\n[3] prices のマージ...")
    add_px = add_px.copy()
    next_id = int(pd.to_numeric(cur_px["id"], errors="coerce").max()) + 1
    add_px["id"] = range(next_id, next_id + len(add_px))
    add_px["symbol_id"] = add_px["symbol_id"].astype(cur_px["symbol_id"].dtype)
    merged_px = pd.concat([cur_px, add_px[cur_px.columns]], ignore_index=True)
    before = len(merged_px)
    merged_px = merged_px.drop_duplicates(subset=["symbol_id", "date"], keep="last")
    merged_px = merged_px.sort_values(["symbol_id", "date"]).reset_index(drop=True)
    print(f"    {len(cur_px):,} → {len(merged_px):,} 行（重複除去 {before - len(merged_px)} 行）")

    print("\n[4] T3 の再計算（対象銘柄のみ・全期間）...")
    cur_ind = pd.read_parquet(cur_paths["indicators"])
    cur_ind["date"] = _norm_date(cur_ind["date"])
    spy_id = int(cur_sym[cur_sym["ticker"] == "SPY"]["id"].iloc[0])
    sym_ids = [r["new_id"] for r in report]
    new_ind = recompute_indicators(sym_ids, merged_px, cur_ind, spy_id)
    print(f"    再計算 {len(new_ind):,} 行 ({len(sym_ids)} 銘柄)")

    # 対象銘柄の既存 T3 を差し替える（断片行が残ると T2/T3 の件数が合わなくなる）
    keep = cur_ind[~cur_ind["symbol_id"].isin(sym_ids)]
    new_ind["symbol_id"] = new_ind["symbol_id"].astype(cur_ind["symbol_id"].dtype)
    next_ind_id = int(pd.to_numeric(cur_ind["id"], errors="coerce").max()) + 1
    new_ind["id"] = range(next_ind_id, next_ind_id + len(new_ind))
    merged_ind = pd.concat([keep, new_ind[cur_ind.columns]], ignore_index=True)
    merged_ind = merged_ind.sort_values(["symbol_id", "date"]).reset_index(drop=True)
    print(f"    indicators {len(cur_ind):,} → {len(merged_ind):,} 行")

    print("\n[5] 新世代の書き出し...")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    files = {}
    # 変更したテーブルは書き直し、その他は同世代へコピーして pointer の整合を保つ
    # （pointer が複数世代のファイルを混ぜて参照すると、prune で参照中のファイルが消える）
    import shutil
    name_map = {"symbols": "symbols", "prices": "prices", "indicators": "indicators",
                "ranks": "ranks", "tc": "theme_constituents",
                "signals": "market_signals", "fx": "fx_rates"}
    for key, base in name_map.items():
        dst = os.path.join(parquet_dir, f"{base}_{ts}.parquet")
        if key == "prices":
            merged_px.to_parquet(dst, index=False)
        elif key == "indicators":
            merged_ind.to_parquet(dst, index=False)
        else:
            shutil.copy2(cur_paths[key], dst)
        files[key] = dst
        print(f"    {base:<20} {os.path.getsize(dst) / 1e6:>9,.1f} MB")

    with open(os.path.join(parquet_dir, f"data_version_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)

    # pointer の更新は専用ヘルパーを使う。API サーバーやバックテストが同時に
    # 読んでいる可能性があり、Windows のファイルロックでリトライが要るため。
    import logging
    _lg = logging.getLogger("restore")
    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    if not update_pointer_with_retry(pointer_file, files, _lg):
        print("[ERROR] pointer の更新に失敗しました。新世代は書けているので、"
              f"latest_master.json を data_version_{ts}.json の内容に手動で差し替えてください。")
        sys.exit(1)
    print(f"\n    pointer を data_version_{ts} に更新しました")
    print("    ※ 旧世代は prune していません（health check 合格までバックアップを兼ねる）")

    print("\n=== 次にやること ===")
    print("  1. T4/T5 の再計算: python backend/scripts/update_pipeline.py --rebuild-from T4")
    print("     （横断的な順位なので、履歴が増えた過去日の順位が全銘柄で変わる）")
    print("  2. SQLite ホットキャッシュの再構築: restore_sqlite_cache_from_parquet")
    print("  3. tools/db_health_check.py で T2/T3 件数一致を確認")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="上流が切り落とした銘柄履歴を旧 Parquet 世代から復元")
    p.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    p.add_argument("--apply", action="store_true", help="実際に書き込む")
    p.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="復元元の Parquet 世代ディレクトリ")
    p.add_argument("--tickers", type=str, help="カンマ区切りで対象を明示（省略時は自動検出）")
    a = p.parse_args()
    if not a.apply and not a.dry_run:
        p.error("--dry-run か --apply のどちらかを指定してください")
    run(dry_run=not a.apply,
        backup_dir=a.backup_dir,
        explicit=[t.strip() for t in a.tickers.split(",")] if a.tickers else None)
